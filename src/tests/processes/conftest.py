import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from app.config import DatabaseSettings, config
from tests.conftest import SRC_DIR
from tests.rabbit_utils import RabbitManagement

WEBHOOK_SECRET = "whsec_MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="
START_TIMEOUT_SEC = 30
STOP_TIMEOUT_SEC = 30


def wait_until(condition: Callable[[], Any], timeout: float = 30, message: str = "condition is not met") -> Any:
    deadline = time.monotonic() + timeout
    while True:
        if result := condition():
            return result
        assert time.monotonic() < deadline, message
        time.sleep(0.1)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class WebhookReceiver:
    """HTTP-сервер в потоке, который принимает вебхуки от настоящего процесса консьюмера."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.status_code = 200
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                receiver.requests.append({"headers": dict(self.headers), "raw": body, "body": json.loads(body)})
                self.send_response(receiver.status_code)
                self.end_headers()

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}/hook"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


class Worker:
    """Настоящий процесс сервиса, запущенный теми же командами, что и в docker-compose."""

    def __init__(self, command: list[str], env: dict[str, str], heartbeat_path: Path, metrics_port: int) -> None:
        self._command = command
        self._env = env
        self.heartbeat_path = heartbeat_path
        self.metrics_port = metrics_port
        self.process: subprocess.Popen[str] | None = None

    def start(self, wait_ready: bool = True) -> "Worker":
        self.heartbeat_path.unlink(missing_ok=True)
        self.process = subprocess.Popen(
            self._command,
            cwd=SRC_DIR,
            env=self._env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        if wait_ready:
            # Признак жизни появляется, когда процесс поднялся, увидел брокер и прошёл первый удачный круг.
            wait_until(self._is_ready, timeout=START_TIMEOUT_SEC, message=f"{self._command} is not started")
        return self

    def _is_ready(self) -> bool:
        assert self.process is not None
        assert self.process.poll() is None, f"process exited on start:\n{self.output()}"
        return self.heartbeat_path.exists()

    def terminate(self) -> int:
        """Штатная остановка, как делает docker stop."""
        assert self.process is not None
        self.process.send_signal(signal.SIGTERM)
        return self.process.wait(timeout=STOP_TIMEOUT_SEC)

    def kill(self) -> None:
        """Аварийное завершение без шанса что-либо доделать."""
        assert self.process is not None
        self.process.kill()
        self.process.wait(timeout=STOP_TIMEOUT_SEC)

    def output(self) -> str:
        assert self.process is not None
        if self.process.poll() is None:
            self.process.kill()
        return self.process.communicate(timeout=STOP_TIMEOUT_SEC)[0]

    def cleanup(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.kill()
            self.process.wait(timeout=STOP_TIMEOUT_SEC)


@pytest.fixture(scope="package")
def rabbit() -> RabbitManagement:
    """Свой vhost для процессов: в нём нет подписчика, которого поднимают тесты из tests/integration."""
    management = RabbitManagement(f"{config.RABBIT.URL}_processes")
    management.recreate_vhost()
    return management


@pytest.fixture(autouse=True)
def _clean_queues(rabbit: RabbitManagement) -> Iterator[None]:
    yield
    rabbit.purge("payments.new", "payments.new.dlq", "payments.new.retry.1", "payments.new.retry.2")


@pytest.fixture()
def webhook_receiver() -> Iterator[WebhookReceiver]:
    receiver = WebhookReceiver()
    receiver.start()
    yield receiver
    receiver.stop()


@pytest.fixture()
def worker_env(db_settings: DatabaseSettings, rabbit: RabbitManagement) -> dict[str, str]:
    return os.environ | {
        "PYTHONPATH": str(SRC_DIR),
        "DB__URL": db_settings.URL,
        "RABBIT__URL": f"{config.RABBIT.URL}_processes",
        "GATEWAY__MIN_DELAY_SEC": "0",
        "GATEWAY__MAX_DELAY_SEC": "0",
        "GATEWAY__SUCCESS_RATE": "1",
        "OUTBOX__POLL_INTERVAL_SEC": "0.1",
        "OUTBOX__MAX_ERROR_DELAY_SEC": "1",
        "HEARTBEAT__INTERVAL_SEC": "0.2",
        "METRICS__COLLECT_INTERVAL_SEC": "0.5",
        "WEBHOOK__SECRET": WEBHOOK_SECRET,
        "LOG_JSON": "false",
    }


@pytest.fixture()
def start_worker(worker_env: dict[str, str], tmp_path: Path) -> Iterator[Callable[..., Worker]]:
    workers: list[Worker] = []
    commands = {
        "consumer": [sys.executable, "-m", "faststream", "run", "app.infra.rabbit.workers.payments_new:app"],
        "relay": [sys.executable, "manage.py", "run-outbox-relay"],
    }

    def start(kind: str, wait_ready: bool = True, **env: str) -> Worker:
        heartbeat_path = tmp_path / f"heartbeat-{kind}-{len(workers)}"
        metrics_port = free_port()
        worker_environment = (
            worker_env | env | {"HEARTBEAT__PATH": str(heartbeat_path), "METRICS__PORT": str(metrics_port)}
        )
        worker = Worker(commands[kind], worker_environment, heartbeat_path, metrics_port)
        workers.append(worker)
        return worker.start(wait_ready=wait_ready)

    yield start
    for worker in workers:
        worker.cleanup()


@pytest.fixture()
def run_command(worker_env: dict[str, str]) -> Callable[..., subprocess.CompletedProcess[str]]:
    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "manage.py", *args],
            cwd=SRC_DIR,
            env=worker_env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    return run

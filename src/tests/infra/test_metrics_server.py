import socket
import urllib.request

from app.config import MetricsSettings
from app.infra.metrics_server import start_metrics_server


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_metrics_server_serves_metrics() -> None:
    port = free_port()

    start_metrics_server(MetricsSettings(PORT=port))

    with urllib.request.urlopen(f"http://127.0.0.1:{port}", timeout=5) as response:
        assert b"outbox_pending_records" in response.read()


def test_busy_port_does_not_stop_worker() -> None:
    """Relay и консьюмер на одной машине без контейнеров делят порт: второй должен стартовать без метрик."""
    with socket.socket() as busy:
        busy.bind(("0.0.0.0", 0))
        busy.listen()

        start_metrics_server(MetricsSettings(PORT=busy.getsockname()[1]))


def test_disabled_metrics_server_is_not_started() -> None:
    port = free_port()

    start_metrics_server(MetricsSettings(ENABLED=False, PORT=port))

    with socket.socket() as sock:
        assert sock.connect_ex(("127.0.0.1", port)) != 0

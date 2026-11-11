"""Приёмник вебхуков для сквозных тестов.

POST /hooks/<режим>  принимает вебхук и отвечает по режиму:
    ok      всегда 200
    fail    всегда 500
    flaky   первые две попытки одного уведомления 503, дальше 200
    flaky5  первые четыре попытки 503, дальше 200
GET /requests        всё, что пришло, в JSON
DELETE /requests     забыть всё
"""

import base64
import json
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

PORT = 8099
FAILURES_BEFORE_SUCCESS = {"flaky": 2, "flaky5": 4}

lock = threading.Lock()
requests: list[dict[str, Any]] = []
attempts: Counter[tuple[str, str]] = Counter()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        pass

    def _send_json(self, code: int, data: Any = None) -> None:
        body = json.dumps(data).encode() if data is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        with lock:
            self._send_json(200, requests if self.path == "/requests" else {"status": "ok"})

    def do_DELETE(self) -> None:
        with lock:
            requests.clear()
            attempts.clear()
        self._send_json(204)

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        mode = self.path.split("?")[0].rsplit("/", 1)[-1]
        webhook_id = self.headers.get("webhook-id", "")
        with lock:
            attempts[(mode, webhook_id)] += 1
            attempt = attempts[(mode, webhook_id)]
            if mode == "fail":
                code = 500
            elif attempt <= FAILURES_BEFORE_SUCCESS.get(mode, 0):
                code = 503
            else:
                code = 200
            requests.append(
                {
                    "time": time.time(),
                    "mode": mode,
                    "attempt": attempt,
                    "code": code,
                    "headers": {key.lower(): value for key, value in self.headers.items()},
                    # Сырые байты нужны для проверки подписи: она считается по телу как оно пришло.
                    "raw": base64.b64encode(body).decode(),
                },
            )
        self._send_json(code)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

"""Работа с RabbitMQ через management API для тестов: vhost'ы, сообщения, принудительный разрыв соединений."""

import json
import os
import time
from typing import Any
from urllib.parse import quote

import httpx
from sqlalchemy.engine import make_url

MANAGEMENT_URL = os.environ.get("RABBIT_MANAGEMENT_URL", "http://localhost:15672")


class RabbitManagement:
    def __init__(self, amqp_url: str) -> None:
        url = make_url(amqp_url.replace("amqp://", "http://"))
        self.vhost_name = url.database or "/"
        self.vhost = quote(self.vhost_name, safe="")
        self.user = url.username or "guest"
        # trust_env=False: запросы к localhost не должны уходить в системный прокси.
        self._client = httpx.Client(
            base_url=MANAGEMENT_URL,
            auth=(self.user, url.password or "guest"),
            timeout=10,
            trust_env=False,
        )

    def recreate_vhost(self) -> None:
        self._client.delete(f"/api/vhosts/{self.vhost}")
        self._client.put(f"/api/vhosts/{self.vhost}").raise_for_status()
        permissions = {"configure": ".*", "write": ".*", "read": ".*"}
        self._client.put(f"/api/permissions/{self.vhost}/{self.user}", json=permissions).raise_for_status()

    def publish(
        self, exchange: str, routing_key: str, body: dict[str, Any], headers: dict[str, Any] | None = None
    ) -> bool:
        response = self._client.post(
            f"/api/exchanges/{self.vhost}/{exchange}/publish",
            json={
                "properties": {"content_type": "application/json", "delivery_mode": 2, "headers": headers or {}},
                "routing_key": routing_key,
                "payload": json.dumps(body),
                "payload_encoding": "string",
            },
        )
        response.raise_for_status()
        return bool(response.json()["routed"])

    def peek(self, queue: str, count: int = 100) -> list[dict[str, Any]]:
        """Читает сообщения очереди и возвращает их обратно. Точнее счётчиков API: те обновляются с задержкой."""
        response = self._client.post(
            f"/api/queues/{self.vhost}/{queue}/get",
            json={"count": count, "ackmode": "ack_requeue_true", "encoding": "auto"},
        )
        if response.status_code == httpx.codes.NOT_FOUND:
            return []
        response.raise_for_status()
        return [
            {"body": json.loads(m["payload"]), "headers": m["properties"].get("headers", {})} for m in response.json()
        ]

    def purge(self, *queues: str) -> None:
        for queue in queues:
            self._client.delete(f"/api/queues/{self.vhost}/{queue}/contents")

    def close_connections(self, wait_sec: float = 20) -> int:
        """Рвёт все соединения клиентов с vhost'ом, как это сделал бы сбой сети."""
        deadline = time.monotonic() + wait_sec
        while True:
            connections = [c for c in self._client.get("/api/connections").json() if c.get("vhost") == self.vhost_name]
            # Список соединений в API обновляется раз в несколько секунд, свежие появляются не сразу.
            if connections or time.monotonic() > deadline:
                break
            time.sleep(0.5)
        for connection in connections:
            self._client.delete(f"/api/connections/{quote(connection['name'], safe='')}")
        return len(connections)

"""Признак жизни фонового процесса для healthcheck контейнера.

Процесс периодически обновляет файл, а проверка смотрит на его возраст:
    python -m app.infra.heartbeat
"""

import sys
import time
from pathlib import Path

from app.config import HeartbeatSettings, config


class Heartbeat:
    def __init__(self, settings: HeartbeatSettings) -> None:
        self._path = Path(settings.PATH)
        self._max_age = settings.MAX_AGE_SEC

    def beat(self) -> None:
        self._path.touch()

    def is_alive(self) -> bool:
        try:
            age = time.time() - self._path.stat().st_mtime
        except FileNotFoundError:
            return False
        return age <= self._max_age


if __name__ == "__main__":
    sys.exit(0 if Heartbeat(config.HEARTBEAT).is_alive() else 1)

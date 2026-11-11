import os
import time
from pathlib import Path

from app.config import HeartbeatSettings
from app.infra.heartbeat import Heartbeat


def test_process_without_heartbeat_is_not_alive(tmp_path: Path) -> None:
    assert Heartbeat(HeartbeatSettings(PATH=str(tmp_path / "heartbeat"))).is_alive() is False


def test_fresh_heartbeat_is_alive(tmp_path: Path) -> None:
    heartbeat = Heartbeat(HeartbeatSettings(PATH=str(tmp_path / "heartbeat"), MAX_AGE_SEC=30))

    heartbeat.beat()

    assert heartbeat.is_alive() is True


def test_stale_heartbeat_is_not_alive(tmp_path: Path) -> None:
    path = tmp_path / "heartbeat"
    heartbeat = Heartbeat(HeartbeatSettings(PATH=str(path), MAX_AGE_SEC=30))
    heartbeat.beat()
    stale = time.time() - 31
    os.utime(path, (stale, stale))

    assert heartbeat.is_alive() is False

from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


def mask_url(url: str) -> str:
    """Оставляет от адреса схему, хост и путь. Логин, пароль и query в логи не попадают."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, "", ""))

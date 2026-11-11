import secrets
from typing import Annotated

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

from app.config import config
from app.errors import UNAUTHORIZED_ERROR

api_key_header = APIKeyHeader(name=config.AUTH.HEADER_NAME, auto_error=False)


async def require_api_key(api_key: Annotated[str | None, Security(api_key_header)] = None) -> None:
    # Ключ не задан в окружении: эндпоинты закрыты, иначе пустой заголовок совпал бы с пустым ключом.
    expected = config.AUTH.API_KEY
    if not expected or not api_key or not secrets.compare_digest(api_key.encode(), expected.encode()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=UNAUTHORIZED_ERROR)

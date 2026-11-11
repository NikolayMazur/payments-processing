FROM python:3.13-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

COPY --from=ghcr.io/astral-sh/uv:0.11.19 /uv /bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./

# Образ с dev-зависимостями: линтеры и тесты.
FROM base AS checks
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked
COPY src /app/code
WORKDIR /app/code

FROM base AS runtime
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev
RUN useradd --create-home --uid 1000 appuser
COPY src /app/code
WORKDIR /app/code
USER 1000
EXPOSE 8000 9000
CMD ["python", "manage.py", "run-server"]

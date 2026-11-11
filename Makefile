.PHONY: install format lint ruff-check mypy test check migrations create_migration up down infra

target ?= tests

install:
	uv sync

ruff-format:
	cd src && uv run ruff format $(arg1) .

ruff-isort:
	cd src && uv run ruff check --select I $(if $(arg1),,--fix) .

format: ruff-isort ruff-format

ruff-check:
	cd src && uv run ruff check .

mypy:
	cd src && uv run mypy .

lint: ruff-check mypy

test:
	cd src && uv run pytest -vv $(target) $(pytest_args) --cov=app --cov-config=../pyproject.toml --cov-report=term-missing

check: format lint test

# Postgres и RabbitMQ для локальной разработки и тестов
infra:
	docker compose up -d --wait db rabbitmq

migrations:
	cd src && uv run alembic upgrade head

clean_outbox:
	cd src && uv run python manage.py clean-outbox

requeue_dlq:
	cd src && uv run python manage.py requeue-dlq

create_migration:
	cd src && uv run alembic revision --autogenerate --message $(name)

up:
	docker compose --profile demo up --build -d --wait

down:
	docker compose --profile demo down

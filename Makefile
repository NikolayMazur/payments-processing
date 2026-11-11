.PHONY: install format lint ruff-format ruff-isort ruff-check mypy bandit squawk hadolint \
	test test_fast check infra migrations create_migration clean_outbox requeue_dlq \
	up down e2e_up e2e_down e2e fuzz loadtest mutation

target ?= tests
SOURCES = src e2e loadtest
E2E_COMPOSE = docker compose -f docker-compose.yaml -f docker-compose.e2e.yaml
SCHEMATHESIS = uvx schemathesis@4.33.2

install:
	uv sync

# ---------- форматирование и статические проверки ----------

ruff-format:
	uv run ruff format $(arg1) $(SOURCES)

ruff-isort:
	uv run ruff check --select I $(if $(arg1),,--fix) $(SOURCES)

format: ruff-isort ruff-format

ruff-check:
	uv run ruff check $(SOURCES)

mypy:
	cd src && uv run mypy .

bandit:
	uv run bandit -q -r src/app src/manage.py

# Линтер SQL миграций: ищет операции, которые надолго блокируют таблицы
squawk:
	cd src && uv run alembic upgrade head --sql 2>/dev/null | uv run squawk -c ../.squawk.toml

hadolint:
	docker run --rm -i hadolint/hadolint < Dockerfile

lint: ruff-check mypy bandit squawk

# ---------- тесты ----------

# Все тесты из src/tests. Нужны PostgreSQL и RabbitMQ: make infra
test:
	cd src && uv run pytest -vv $(target) $(pytest_args) --cov=app --cov-config=../pyproject.toml --cov-report=term-missing

# Без тестов, которые ходят в настоящий RabbitMQ и запускают процессы. Нужен только PostgreSQL
test_fast:
	cd src && uv run pytest -q $(target) -m "not integration" $(pytest_args)

check: format lint test

e2e_up:
	$(E2E_COMPOSE) up --build -d --wait

e2e_down:
	$(E2E_COMPOSE) down -v

# Сквозные тесты на поднятом docker-compose окружении, со сбоями контейнеров
e2e: e2e_up
	uv run pytest e2e -v; status=$$?; $(E2E_COMPOSE) down -v; exit $$status

# Фаззинг API по схеме OpenAPI. Воркеры остановлены, чтобы сервис не слал вебхуки на случайные адреса.
# negative_data_rejection отключена: схема адреса вебхука намеренно строже, чем принимает сервис.
fuzz: e2e_up
	$(E2E_COMPOSE) stop consumer outbox-relay
	$(SCHEMATHESIS) run http://localhost:8000/openapi.json --header 'X-API-Key: local-api-key' \
		--checks all --exclude-checks negative_data_rejection --generation-allow-x00 false --max-examples 200; \
		status=$$?; $(E2E_COMPOSE) down -v; exit $$status

# Нагрузочный тест с порогами, параметры в loadtest/locustfile.py
loadtest: e2e_up
	uv run --with locust locust -f loadtest/locustfile.py --headless --only-summary \
		-u $(or $(users),30) -r 10 -t $(or $(duration),60s) --host http://localhost:8000; \
		status=$$?; $(E2E_COMPOSE) down -v; exit $$status

# Мутационное тестирование логики без ввода-вывода. Нужен PostgreSQL: make infra
mutation:
	rm -rf mutants
	uv run mutmut run
	uv run mutmut results

# ---------- окружение ----------

# PostgreSQL и RabbitMQ для локальной разработки и тестов
infra:
	docker compose up -d --wait db rabbitmq

migrations:
	cd src && uv run alembic upgrade head

create_migration:
	cd src && uv run alembic revision --autogenerate --message $(name)

clean_outbox:
	cd src && uv run python manage.py clean-outbox

requeue_dlq:
	cd src && uv run python manage.py requeue-dlq

up:
	docker compose --profile demo up --build -d --wait

down:
	docker compose --profile demo down

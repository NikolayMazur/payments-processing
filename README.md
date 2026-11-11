# Payments processing

Асинхронный сервис процессинга платежей. Принимает запрос на оплату, проводит его через платёжный шлюз (эмуляция) в фоне и сообщает результат клиенту вебхуком.

Стек: Python 3.13, FastAPI, Pydantic v2, SQLAlchemy 2 (asyncio), PostgreSQL, RabbitMQ (FastStream), Alembic, dependency-injector, uv, Docker.

## Как это работает

```mermaid
flowchart LR
    client([Клиент]) -->|POST /api/v1/payments| api[api]
    api -->|одна транзакция| db[(payments + outbox)]
    relay[outbox-relay] -->|FOR UPDATE SKIP LOCKED| db
    relay -->|publish + confirm| q[[payments.new]]
    q --> consumer[consumer]
    consumer -->|шлюз, статус| db
    consumer -->|вебхук| client
    consumer -.->|ошибка, попытки 1 и 2| retry[[payments.new.retry.N]]
    retry -.->|TTL 2 и 4 с| q
    consumer -.->|ошибка на попытке 3| dlq[[payments.new.dlq]]
```

1. `api` сохраняет платёж в статусе `pending` и событие в таблицу `outbox` одной транзакцией, отвечает `202 Accepted`. В RabbitMQ сам не ходит.
2. `outbox-relay` забирает неотправленные записи, публикует их в обменник `payments` и помечает отправленными.
3. `consumer` читает очередь `payments.new`, вызывает шлюз (ответ через 2–5 секунд, 90% успеха), пишет итог в БД и шлёт вебхук на `webhook_url`.
4. Если обработка упала, сообщение уходит в очередь задержки и возвращается через 2, затем через 4 секунды. После третьей неудачной попытки оно попадает в `payments.new.dlq`.

## Запуск

Нужен Docker с compose.

```bash
docker compose --profile demo up --build -d --wait
```

Поднимутся `db`, `rabbitmq`, `migrations` (накатывает схему и завершается), `api`, `outbox-relay`, `consumer`. Профиль `demo` добавляет `webhook-sink`, простой приёмник, который печатает входящие вебхуки в лог.

| Что | Адрес |
|---|---|
| API | http://localhost:8000 |
| Swagger | http://localhost:8000/docs |
| RabbitMQ management | http://localhost:15672 (guest / guest) |
| PostgreSQL | localhost:5442 (postgres / pass, база payments) |

Остановить и удалить данные: `docker compose --profile demo down -v`.

Настройки лежат в `.env-defaults`. Чтобы поменять их локально, создайте рядом `.env` с нужными переменными, он в git не попадает.

## Примеры

API-ключ для локального запуска `local-api-key` (переменная `AUTH__API_KEY`).

Создать платёж:

```bash
curl -i -X POST http://localhost:8000/api/v1/payments \
  -H 'X-API-Key: local-api-key' \
  -H 'Idempotency-Key: order-42-attempt-1' \
  -H 'Content-Type: application/json' \
  -d '{
    "amount": "1500.00",
    "currency": "RUB",
    "description": "Заказ 42",
    "metadata": {"order_id": 42},
    "webhook_url": "http://webhook-sink:8080/hooks/payments"
  }'
```

```json
{"payment_id": "51f14258-95af-43fa-a24c-4d70b9ca2c3b", "status": "pending", "created_at": "2026-11-11T16:33:02.772944Z"}
```

Получить платёж:

```bash
curl http://localhost:8000/api/v1/payments/51f14258-95af-43fa-a24c-4d70b9ca2c3b -H 'X-API-Key: local-api-key'
```

```json
{
  "payment_id": "51f14258-95af-43fa-a24c-4d70b9ca2c3b",
  "amount": "1500.00",
  "currency": "RUB",
  "description": "Заказ 42",
  "metadata": {"order_id": 42},
  "status": "succeeded",
  "idempotency_key": "order-42-attempt-1",
  "webhook_url": "http://webhook-sink:8080/hooks/payments",
  "failure_reason": null,
  "created_at": "2026-11-11T16:33:02.772944Z",
  "processed_at": "2026-11-11T16:33:07.744907Z"
}
```

Посмотреть пришедший вебхук:

```bash
docker compose logs webhook-sink
```

Тело вебхука:

```json
{
  "payment_id": "51f14258-95af-43fa-a24c-4d70b9ca2c3b",
  "status": "succeeded",
  "amount": "1500.00",
  "currency": "RUB",
  "description": "Заказ 42",
  "metadata": {"order_id": 42},
  "failure_reason": null,
  "created_at": "2026-11-11T16:33:02.772944Z",
  "processed_at": "2026-11-11T16:33:07.744907Z"
}
```

Увидеть ретраи и DLQ: создайте платёж с недоступным адресом, например `"webhook_url": "http://unreachable-host:9/hook"`. Примерно через 10 секунд сообщение появится в очереди `payments.new.dlq`, это видно в RabbitMQ management или командой

```bash
docker compose exec rabbitmq rabbitmqctl list_queues name messages
```

### Ответы API

| Код | Когда |
|---|---|
| 202 | Платёж принят. Тот же ответ приходит на повтор запроса с тем же `Idempotency-Key` |
| 401 | Нет заголовка `X-API-Key` или ключ неверный |
| 404 | Платёж не найден |
| 409 | `Idempotency-Key` уже использован с другими параметрами платежа |
| 422 | Нет `Idempotency-Key` или тело не прошло валидацию |

Ошибки, кроме 422, приходят в виде `{"detail": {"message_code": "PAYMENT_NOT_FOUND", "message": "Платёж не найден"}}`.

`GET /healthcheck` работает без ключа и проверяет соединение с БД.

## Гарантии

### Идемпотентность создания

На `payments.idempotency_key` стоит уникальный индекс. Платёж вставляется через `INSERT ... ON CONFLICT DO NOTHING RETURNING id`, поэтому два одновременных запроса с одним ключом создадут одну строку и одно событие. Вместе с платежом хранится sha256 от параметров запроса. Повтор с теми же параметрами получает исходный ответ, с другими получает 409.

### Outbox

Событие пишется в `outbox` в той же транзакции, что и платёж, так что либо есть оба, либо нет ни одного. Relay выбирает записи запросом с `FOR UPDATE SKIP LOCKED`, поэтому его можно запускать в несколько реплик: каждая берёт свои строки. Публикация идёт с подтверждением от брокера (publisher confirms), сообщения persistent, запись помечается `SUCCESS` только после подтверждения. Пока RabbitMQ недоступен, записи остаются `PENDING` и уходят после его возврата.

Доставка at-least-once. Если relay упадёт между публикацией и коммитом, событие уйдёт второй раз.

### Обработка в консьюмере

Повторную доставку консьюмер переносит безопасно. Итог шлюза записывается запросом `UPDATE ... WHERE status = 'pending'`, так что платёж проводится один раз, а при повторе только заново отправляется вебхук.

Подтверждение сообщений ручное. При ошибке консьюмер сначала публикует копию в очередь задержки и только потом подтверждает исходное сообщение.

### Retry и DLQ

| Объект | Назначение |
|---|---|
| exchange `payments`, очередь `payments.new` | новые платежи. У очереди `x-dead-letter-exchange = payments.dlx` |
| exchange `payments.retry`, очереди `payments.new.retry.1` и `.2` | задержка 2 и 4 секунды через `x-message-ttl`, по истечении сообщение возвращается в `payments.new` |
| exchange `payments.dlx`, очередь `payments.new.dlq` | сообщения, не обработанные за 3 попытки |

Номер повтора едет в заголовке `x-retry-attempt`. На каждую задержку своя очередь, иначе сообщение с длинным TTL задерживало бы стоящие за ним. После третьей неудачи консьюмер делает `reject` без requeue, и брокер сам переносит сообщение в DLQ. Сообщение с неразбираемым телом или с неизвестным платежом уходит в DLQ сразу.

Ошибкой отправки вебхука считается сетевой сбой, таймаут и любой ответ не 2xx.

### Что стоит знать получателю вебхука

- Вебхук может прийти больше одного раза, дедуплицировать нужно по `payment_id`.
- Если задан `WEBHOOK__SECRET`, в заголовке `X-Signature` приходит `sha256=<HMAC-SHA256 тела>`.
- Если вебхук не доставлен за три попытки, статус платежа всё равно доступен через `GET /api/v1/payments/{id}`.

## Структура

```
src/
├── manage.py                 # CLI: run-server, run-outbox-relay
├── alembic/                  # миграции
├── app/
│   ├── config.py             # настройки из переменных окружения
│   ├── containers.py         # DI-контейнер
│   ├── api/                  # вход: REST, обработчики событий, CLI
│   ├── app_layer/            # интерфейсы и сервисы (сценарии)
│   ├── domain/               # сущности и события
│   └── infra/                # БД, RabbitMQ, шлюз, HTTP-клиент вебхуков
└── tests/
```

Зависимости направлены внутрь: `api` и `infra` знают про `app_layer`, а `app_layer` работает только с интерфейсами (`AbcUnitOfWork`, `AbstractPaymentGateway`, `AbstractWebhookClient`, `AbstractEventPublisher`). Каждый сценарий это класс с методом `process`: `CreatePaymentService`, `GetPaymentService`, `ProcessPaymentService`, `SendOutboxService`.

Процессы:

| Сервис compose | Команда |
|---|---|
| api | `python manage.py run-server` |
| outbox-relay | `python manage.py run-outbox-relay` |
| consumer | `faststream run app.infra.rabbit.workers.payments_new:app` |

## Переменные окружения

Вложенные настройки задаются через двойное подчёркивание.

| Переменная | По умолчанию | Смысл |
|---|---|---|
| `AUTH__API_KEY` | пусто | Ключ для `X-API-Key`. Пока не задан, API отвечает 401 |
| `DB__URL` | `postgresql+asyncpg://postgres:pass@localhost:5442/payments` | Строка подключения к PostgreSQL |
| `RABBIT__URL` | `amqp://guest:guest@localhost:5672/` | Адрес RabbitMQ |
| `RABBIT__MAX_ATTEMPTS` | `3` | Сколько раз обрабатывать сообщение до DLQ |
| `RABBIT__RETRY_BASE_DELAY_SEC` | `2` | База экспоненциальной задержки |
| `RABBIT__PREFETCH_COUNT` | `10` | Сколько сообщений консьюмер обрабатывает одновременно |
| `OUTBOX__BATCH_SIZE` | `100` | Размер пачки relay |
| `OUTBOX__POLL_INTERVAL_SEC` | `0.5` | Пауза relay, когда отправлять нечего |
| `GATEWAY__MIN_DELAY_SEC`, `GATEWAY__MAX_DELAY_SEC` | `2`, `5` | Время ответа шлюза |
| `GATEWAY__SUCCESS_RATE` | `0.9` | Доля успешных платежей |
| `WEBHOOK__TIMEOUT_SEC` | `5` | Таймаут запроса вебхука |
| `WEBHOOK__SECRET` | пусто | Секрет для подписи вебхуков |
| `LOG_LEVEL`, `LOG_JSON` | `INFO`, `true` | Логирование |

Число попыток и задержки зашиты в аргументы очередей. Если поменять `RABBIT__MAX_ATTEMPTS` или `RABBIT__RETRY_BASE_DELAY_SEC` на работающем брокере, очереди `payments.new.retry.*` нужно удалить, иначе RabbitMQ откажет в объявлении с другими аргументами.

## Разработка

Нужны [uv](https://docs.astral.sh/uv/) и Docker.

```bash
make install   # зависимости в .venv
make infra     # PostgreSQL и RabbitMQ в Docker
make test      # тесты с покрытием
make lint      # ruff и mypy
make format    # автоформатирование
make check     # всё вместе
```

Тестам нужен только PostgreSQL. Они создают отдельную базу `payments_test`, накатывают её настоящими миграциями и чистят таблицы после каждого теста. Брокер в тестах заменён на `TestRabbitBroker` из FastStream.

Миграции:

```bash
make create_migration name="add_some_field"
make migrations
```

## Ограничения

- Шлюз эмулируется, результат случайный.
- Таблица `outbox` не чистится. На боевой нагрузке нужна периодическая очистка записей `SUCCESS` или партиционирование по дате.
- Сообщения из DLQ разбираются вручную, автоматического возврата в обработку нет.
- `webhook_url` проверяется только на корректность адреса. Для публичного сервиса понадобится запрет внутренних адресов.

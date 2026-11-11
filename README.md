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
3. `consumer` читает очередь `payments.new`, вызывает шлюз (ответ через 2–5 секунд, 90% успеха), пишет итог в БД и шлёт вебхук на `webhook_url`. Попытки доставки и момент подтверждения сохраняются в платеже.
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
  "processed_at": "2026-11-11T16:33:07.744907Z",
  "webhook_attempts": 1,
  "webhook_delivered_at": "2026-11-11T16:33:07.766312Z"
}
```

`webhook_attempts` и `webhook_delivered_at` показывают, сколько раз сервис пытался доставить вебхук и когда получатель его принял.

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
| 400 | Тело запроса не разбирается как JSON |
| 422 | Нет `Idempotency-Key` или тело не прошло валидацию |
| 503 | Нет связи с базой данных. Запрос можно повторить с тем же `Idempotency-Key`, в заголовке `Retry-After` пауза в секундах |

Ошибки, кроме 400 и 422, приходят в виде `{"detail": {"message_code": "PAYMENT_NOT_FOUND", "message": "Платёж не найден"}}`.

`GET /healthcheck` работает без ключа и проверяет соединение с БД.

## Наблюдаемость

### Идентификатор запроса

API принимает заголовок `X-Request-ID`, а если его нет, генерирует сам и возвращает в ответе. Значение сохраняется в событии outbox, едет заголовком `x-request-id` в сообщении RabbitMQ и попадает в поле `request_id` каждой строки лога API и консьюмера, включая повторы. По нему собирается вся история одного платежа:

```bash
docker compose logs api consumer | grep '"request_id": "<значение>"'
```

### Метрики

Формат Prometheus. API отдаёт их на `GET /metrics` своего порта, `consumer` и `outbox-relay` на порту `9000` внутри сети compose.

| Метрика | Что показывает |
|---|---|
| `outbox_oldest_pending_age_seconds`, `outbox_pending_records` | Отставание relay. Рост означает, что события не доходят до брокера |
| `dead_letter_queue_messages` | Сколько сообщений лежит в DLQ |
| `payment_processing_seconds` | Время от создания платежа до записи итога |
| `payments_created_total`, `payments_processed_total` | Принятые платежи и итоги по статусам |
| `webhook_deliveries_total` | Попытки доставки вебхука по результату |
| `payment_messages_retried_total`, `payment_messages_dead_lettered_total` | Повторы и уходы в DLQ по причинам |
| `outbox_published_total`, `outbox_publish_errors_total` | Работа relay |
| `http_requests_total`, `http_request_duration_seconds` | Запросы к API по маршруту и коду ответа |

Посмотреть метрики воркера:

```bash
docker compose exec consumer python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:9000').read().decode())"
```

### Состояние воркеров

У `consumer` и `outbox-relay` нет HTTP-интерфейса, поэтому их healthcheck устроен через признак жизни: процесс обновляет файл, пока работает и видит брокер, а проверка `python -m app.infra.heartbeat` смотрит на возраст файла. Консьюмер при остановке дожидается обработчиков до `RABBIT__GRACEFUL_TIMEOUT_SEC`, в compose для этого задан `stop_grace_period: 30s`.

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

Сообщения из DLQ возвращаются в обработку командой `python manage.py requeue-dlq`, счётчик попыток при этом обнуляется. Платёж повторно не проводится, а уже принятый получателем вебхук не отправляется заново.

### Вебхуки

Уведомления отправляются по соглашению [Standard Webhooks](https://github.com/standard-webhooks/standard-webhooks/blob/main/spec/standard-webhooks.md), для проверки подходят готовые библиотеки.

| Заголовок | Значение |
|---|---|
| `webhook-id` | Идентификатор уведомления, совпадает с `payment_id`. Между повторами не меняется |
| `webhook-timestamp` | Время отправки, Unix-время в секундах |
| `webhook-signature` | `v1,<base64>`, HMAC-SHA256 от строки `{webhook-id}.{webhook-timestamp}.{тело}`. Есть, если задан `WEBHOOK__SECRET` |

Что стоит знать получателю:

- Вебхук может прийти больше одного раза, дедуплицировать нужно по `webhook-id`. Сервис запоминает подтверждённую доставку и после неё уведомление не повторяет, но при сбое между ответом получателя и записью в БД повтор возможен.
- Подпись считается по сырым байтам тела. Чтобы перехваченный запрос нельзя было переслать позже, сверяйте `webhook-timestamp` с текущим временем.
- Секрет задаётся в виде `whsec_<base64>`, ключом служат раскодированные байты. Сгенерировать: `echo "whsec_$(openssl rand -base64 32)"`.
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

Служебные команды:

| Команда | Что делает |
|---|---|
| `python manage.py requeue-dlq [--limit N]` | Возвращает сообщения из `payments.new.dlq` в обработку |
| `python manage.py clean-outbox [--retention-days N]` | Удаляет опубликованные записи outbox старше срока хранения |

В Docker они запускаются так: `docker compose run --rm api python manage.py clean-outbox`. Очистку стоит повесить на расписание, например раз в сутки.

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
| `RABBIT__GRACEFUL_TIMEOUT_SEC` | `15` | Сколько консьюмер ждёт обработчики при остановке |
| `OUTBOX__BATCH_SIZE` | `100` | Размер пачки relay |
| `OUTBOX__POLL_INTERVAL_SEC` | `0.5` | Пауза relay, когда отправлять нечего |
| `OUTBOX__MAX_ERROR_DELAY_SEC` | `10` | Потолок паузы relay при сбоях. После каждой ошибки подряд пауза удваивается |
| `GATEWAY__MIN_DELAY_SEC`, `GATEWAY__MAX_DELAY_SEC` | `2`, `5` | Время ответа шлюза |
| `GATEWAY__SUCCESS_RATE` | `0.9` | Доля успешных платежей |
| `WEBHOOK__TIMEOUT_SEC` | `5` | Таймаут запроса вебхука |
| `OUTBOX__RETENTION_DAYS` | `7` | Срок хранения опубликованных записей для `clean-outbox` |
| `WEBHOOK__SECRET` | пусто | Секрет для подписи вебхуков, `whsec_<base64>` |
| `API__MAX_METADATA_BYTES` | `16384` | Предел размера `metadata` в запросе |
| `METRICS__ENABLED`, `METRICS__PORT` | `true`, `9000` | Метрики и порт, на котором их отдают воркеры |
| `LOG_LEVEL`, `LOG_JSON` | `INFO`, `true` | Логирование |

Число попыток и задержки зашиты в аргументы очередей. Если поменять `RABBIT__MAX_ATTEMPTS` или `RABBIT__RETRY_BASE_DELAY_SEC` на работающем брокере, очереди `payments.new.retry.*` нужно удалить, иначе RabbitMQ откажет в объявлении с другими аргументами.

## Разработка

Нужны [uv](https://docs.astral.sh/uv/) и Docker.

```bash
make install   # зависимости в .venv
make infra     # PostgreSQL и RabbitMQ в Docker
make test      # тесты с покрытием
make lint      # ruff, mypy, bandit, squawk
make format    # автоформатирование
make check     # всё вместе
```

Миграции:

```bash
make create_migration name="add_some_field"
make migrations
```

На время миграции выставляются `lock_timeout` и `statement_timeout` (`DB__MIGRATION_LOCK_TIMEOUT_MS`, `DB__MIGRATION_STATEMENT_TIMEOUT_MS`), чтобы она не вставала надолго в очередь за блокировкой.

## Тесты

| Команда | Что проверяет | Что нужно |
|---|---|---|
| `make test_fast` | Логика, сервисы на настоящей БД, API, консьюмер на заглушке брокера, миграции | PostgreSQL |
| `make test` | То же плюс тесты с настоящим RabbitMQ и тесты процессов | PostgreSQL, RabbitMQ |
| `make e2e` | Сквозные сценарии на docker-compose, со сбоями контейнеров | Docker |
| `make fuzz` | Фаззинг API по схеме OpenAPI (schemathesis) | Docker |
| `make loadtest` | Нагрузка с порогами (locust) | Docker |
| `make mutation` | Мутационное тестирование логики (mutmut) | PostgreSQL |

В CI на каждый push идут `make test`, линтеры, сквозные тесты и фаззинг.

### Что лежит в `src/tests`

- **Сервисы и API на настоящей БД.** База `payments_test` накатывается настоящими миграциями, таблицы чистятся после каждого теста. Здесь идемпотентность под параллельными запросами, атомарность платежа и события, гонка двух консьюмеров на одном сообщении, `SKIP LOCKED` в relay.
- **Проверки на свойствах** (`test_properties.py`, hypothesis). Инварианты на случайных данных: одна и та же сумма в разной записи даёт один хеш запроса, порядок ключей в metadata не важен, сообщение обрабатывается ровно `MAX_ATTEMPTS` раз.
- **Контракты.** Снимок схемы OpenAPI в `tests/api/rest/openapi.json`: случайная правка эндпоинта роняет тест, намеренная обновляется через `UPDATE_SNAPSHOTS=1`. Подпись вебхука проверяется официальной библиотекой `standardwebhooks`, включая отказ на изменённом теле и на устаревшей отметке времени.
- **Миграции.** Каждая ревизия накатывается, откатывается и накатывается снова, а полная цепочка проходит на базе с данными первой версии схемы.
- **`tests/integration`.** Настоящий RabbitMQ в отдельном vhost: возврат из очередей задержки по TTL, уход в DLQ и возврат из неё.
- **`tests/processes`.** Консьюмер и relay запускаются отдельными процессами теми же командами, что в compose. Проверяются остановка по SIGTERM с завершением текущего платежа, повторная доставка после SIGKILL, восстановление после обрыва соединений с брокером и с БД, служебные команды.

### Сквозные тесты

`make e2e` поднимает окружение с дополнением `docker-compose.e2e.yaml` (приёмник вебхуков `e2e/receiver.py`, тестовый секрет подписи) и ходит в сервис снаружи, как клиент. Контейнеры по ходу останавливаются, убиваются и перезапускаются: консьюмер посреди обработки, relay, RabbitMQ, PostgreSQL.

### Ожидаемое поведение, закреплённое тестами

- Вебхук может прийти дважды, если сервис упал между ответом получателя и записью об этом в БД (`test_webhook_is_sent_again_if_delivery_was_not_recorded`). Это следствие гарантии at-least-once, получатель отсекает дубль по `webhook-id`.
- Два консьюмера, получившие одно сообщение одновременно, оба обращаются к шлюзу, но итог в БД сохраняется один (`test_two_consumers_on_same_message_save_single_result`). Настоящий шлюз должен быть идемпотентным по идентификатору платежа.
- Relay с событием, которое нельзя опубликовать, не считает себя здоровым (`test_relay_exposes_outbox_lag`).

## Ограничения

- Шлюз эмулируется, результат случайный.
- Событие, которое брокер не принимает (например, указан несуществующий обменник), останавливает relay на себе: записи публикуются по порядку. Это видно по метрике `outbox_oldest_pending_age_seconds` и по healthcheck.
- `description` и `metadata` не могут содержать нулевой символ, PostgreSQL его не хранит. Такой запрос получает 422.
- Очистка `outbox` и возврат сообщений из DLQ запускаются командами вручную или по расписанию, внутри сервиса планировщика нет.
- Метрики API считаются в памяти процесса. При `UVICORN__WORKERS` больше одного каждый процесс отдаёт свои значения.
- `webhook_url` проверяется только на корректность адреса. Для публичного сервиса понадобится запрет внутренних адресов.

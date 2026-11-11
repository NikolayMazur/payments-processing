from prometheus_client import Counter, Gauge, Histogram

http_requests = Counter("http_requests_total", "Запросы к API", ["method", "path", "status"])
http_request_duration = Histogram("http_request_duration_seconds", "Время ответа API", ["method", "path"])

payments_created = Counter("payments_created_total", "Принятые платежи", ["currency"])
payments_processed = Counter("payments_processed_total", "Платежи с итогом от шлюза", ["status"])
payment_processing_duration = Histogram(
    "payment_processing_seconds",
    "Время от создания платежа до записи итога",
    buckets=(1, 2, 3, 5, 8, 13, 21, 34, 60, 120, 300),
)
webhook_deliveries = Counter("webhook_deliveries_total", "Попытки доставки вебхука", ["result"])

messages_retried = Counter("payment_messages_retried_total", "Сообщения, отправленные на повтор")
messages_duplicated = Counter(
    "payment_messages_duplicated_total",
    "Копии сообщения, отброшенные, пока платёж обрабатывался по другой копии",
)
messages_dead_lettered = Counter("payment_messages_dead_lettered_total", "Сообщения, ушедшие в DLQ", ["reason"])

outbox_published = Counter("outbox_published_total", "Записи outbox, принятые брокером")
outbox_publish_errors = Counter("outbox_publish_errors_total", "Неудачные итерации relay")
outbox_pending = Gauge("outbox_pending_records", "Записи outbox, ожидающие публикации")
outbox_oldest_pending_age = Gauge(
    "outbox_oldest_pending_age_seconds",
    "Возраст самой старой неопубликованной записи outbox",
)
payments_pending = Gauge("payments_pending", "Платежи без итога")
payments_oldest_pending_age = Gauge(
    "payments_oldest_pending_age_seconds",
    "Возраст самого старого платежа без итога. Долгий рост означает, что его сообщение потеряно или лежит в DLQ",
)
dead_letter_queue_messages = Gauge("dead_letter_queue_messages", "Сообщения в DLQ")

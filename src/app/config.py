from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseModel):
    URL: str = "postgresql+asyncpg://postgres:pass@localhost:5442/payments"
    POOL_SIZE: int = Field(default=10, gt=0)
    MAX_OVERFLOW: int = Field(default=10, ge=0)
    ECHO: bool = False
    # Миграция не должна надолго вставать в очередь за блокировкой и держать за собой запросы сервиса.
    MIGRATION_LOCK_TIMEOUT_MS: int = Field(default=2000, gt=0)
    MIGRATION_STATEMENT_TIMEOUT_MS: int = Field(default=60000, gt=0)


class APISettings(BaseModel):
    ROOT_PREFIX: str = "/api"
    DOCS_ENABLED: bool = True
    MAX_METADATA_BYTES: int = Field(default=16 * 1024, gt=0)
    REQUEST_ID_HEADER: str = "X-Request-ID"


class AuthSettings(BaseModel):
    HEADER_NAME: str = "X-API-Key"
    API_KEY: str = ""


class UvicornSettings(BaseModel):
    HOST: str = "0.0.0.0"  # nosec B104  сервис работает в контейнере и слушает все его интерфейсы
    PORT: int = 8000
    WORKERS: int = Field(default=1, gt=0)


class RabbitSettings(BaseModel):
    URL: str = "amqp://guest:guest@localhost:5672/"
    EXCHANGE: str = "payments"
    QUEUE: str = "payments.new"
    RETRY_EXCHANGE: str = "payments.retry"
    DLX: str = "payments.dlx"
    DLQ: str = "payments.new.dlq"
    PREFETCH_COUNT: int = Field(default=10, gt=0)
    MAX_ATTEMPTS: int = Field(default=3, gt=0)
    RETRY_BASE_DELAY_SEC: int = Field(default=2, gt=0)
    RETRY_HEADER: str = "x-retry-attempt"
    # Сколько ждать обработчики при остановке. Должно покрывать ответ шлюза и таймаут вебхука.
    GRACEFUL_TIMEOUT_SEC: float = Field(default=15.0, gt=0)


class OutboxSettings(BaseModel):
    BATCH_SIZE: int = Field(default=100, gt=0)
    POLL_INTERVAL_SEC: float = Field(default=0.5, gt=0)
    MAX_ERROR_DELAY_SEC: float = Field(default=10.0, gt=0)
    RETENTION_DAYS: int = Field(default=7, ge=0)
    CLEAN_BATCH_SIZE: int = Field(default=1000, gt=0)


class GatewaySettings(BaseModel):
    MIN_DELAY_SEC: float = Field(default=2.0, ge=0)
    MAX_DELAY_SEC: float = Field(default=5.0, ge=0)
    SUCCESS_RATE: float = Field(default=0.9, ge=0, le=1)


class WebhookSettings(BaseModel):
    TIMEOUT_SEC: float = Field(default=5.0, gt=0)
    # Секрет в формате Standard Webhooks: whsec_<base64>. Строка без префикса берётся как есть.
    SECRET: str = ""


class MetricsSettings(BaseModel):
    ENABLED: bool = True
    # Порт, на котором relay и консьюмер отдают метрики. API отдаёт их на своём порту по /metrics.
    PORT: int = 9000
    COLLECT_INTERVAL_SEC: float = Field(default=10.0, gt=0)


class HeartbeatSettings(BaseModel):
    # В файле нет данных, важно только время его изменения.
    PATH: str = "/tmp/payments-heartbeat"  # noqa: S108  # nosec B108
    INTERVAL_SEC: float = Field(default=5.0, gt=0)
    MAX_AGE_SEC: float = Field(default=30.0, gt=0)


class Config(BaseSettings):
    SERVICE_NAME: str = "payments-processing"
    LOG_LEVEL: str = "INFO"
    LOG_JSON: bool = True

    DB: DatabaseSettings = DatabaseSettings()
    API: APISettings = APISettings()
    AUTH: AuthSettings = AuthSettings()
    UVICORN: UvicornSettings = UvicornSettings()
    RABBIT: RabbitSettings = RabbitSettings()
    OUTBOX: OutboxSettings = OutboxSettings()
    GATEWAY: GatewaySettings = GatewaySettings()
    WEBHOOK: WebhookSettings = WebhookSettings()
    METRICS: MetricsSettings = MetricsSettings()
    HEARTBEAT: HeartbeatSettings = HeartbeatSettings()

    model_config = SettingsConfigDict(env_file=".env", env_nested_delimiter="__", extra="ignore")


config = Config()

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseModel):
    URL: str = "postgresql+asyncpg://postgres:pass@localhost:5442/payments"
    POOL_SIZE: int = Field(default=10, gt=0)
    MAX_OVERFLOW: int = Field(default=10, ge=0)
    ECHO: bool = False


class APISettings(BaseModel):
    ROOT_PREFIX: str = "/api"
    DOCS_ENABLED: bool = True


class AuthSettings(BaseModel):
    HEADER_NAME: str = "X-API-Key"
    API_KEY: str = ""


class UvicornSettings(BaseModel):
    HOST: str = "0.0.0.0"
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


class OutboxSettings(BaseModel):
    BATCH_SIZE: int = Field(default=100, gt=0)
    POLL_INTERVAL_SEC: float = Field(default=0.5, gt=0)


class GatewaySettings(BaseModel):
    MIN_DELAY_SEC: float = Field(default=2.0, ge=0)
    MAX_DELAY_SEC: float = Field(default=5.0, ge=0)
    SUCCESS_RATE: float = Field(default=0.9, ge=0, le=1)


class WebhookSettings(BaseModel):
    TIMEOUT_SEC: float = Field(default=5.0, gt=0)
    SECRET: str = ""
    SIGNATURE_HEADER: str = "X-Signature"


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

    model_config = SettingsConfigDict(env_file=".env", env_nested_delimiter="__", extra="ignore")


config = Config()

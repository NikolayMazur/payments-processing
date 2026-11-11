from pydantic import BaseModel


class ErrorSchema(BaseModel):
    message_code: str
    message: str


class DetailErrorSchema(BaseModel):
    detail: ErrorSchema


class HealthCheckSchema(BaseModel):
    status: str = "ok"

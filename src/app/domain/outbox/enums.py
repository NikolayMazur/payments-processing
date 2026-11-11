from enum import StrEnum


class OutboxStatusEnum(StrEnum):
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"

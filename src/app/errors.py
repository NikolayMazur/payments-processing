from enum import StrEnum


class ErrorCodeEnum(StrEnum):
    UNKNOWN_ERROR_CODE = "UNKNOWN_ERROR"
    UNAUTHORIZED_ERROR_CODE = "UNAUTHORIZED"
    PAYMENT_NOT_FOUND_ERROR_CODE = "PAYMENT_NOT_FOUND"
    IDEMPOTENCY_KEY_CONFLICT_ERROR_CODE = "IDEMPOTENCY_KEY_CONFLICT"
    SERVICE_UNAVAILABLE_ERROR_CODE = "SERVICE_UNAVAILABLE"


UNKNOWN_ERROR = {
    "message_code": ErrorCodeEnum.UNKNOWN_ERROR_CODE.value,
    "message": "Что-то пошло не так",
}
UNAUTHORIZED_ERROR = {
    "message_code": ErrorCodeEnum.UNAUTHORIZED_ERROR_CODE.value,
    "message": "Неверный или отсутствующий API-ключ",
}
PAYMENT_NOT_FOUND_ERROR = {
    "message_code": ErrorCodeEnum.PAYMENT_NOT_FOUND_ERROR_CODE.value,
    "message": "Платёж не найден",
}
IDEMPOTENCY_KEY_CONFLICT_ERROR = {
    "message_code": ErrorCodeEnum.IDEMPOTENCY_KEY_CONFLICT_ERROR_CODE.value,
    "message": "Ключ идемпотентности уже использован с другими параметрами платежа",
}
SERVICE_UNAVAILABLE_ERROR = {
    "message_code": ErrorCodeEnum.SERVICE_UNAVAILABLE_ERROR_CODE.value,
    "message": "Сервис временно недоступен",
}

class BasePaymentException(Exception):
    pass


class PaymentNotFoundError(BasePaymentException):
    """Платёж не найден"""


class IdempotencyKeyConflictError(BasePaymentException):
    """Ключ идемпотентности уже использован с другими параметрами платежа"""

from contextvars import ContextVar

# Идентификатор запроса клиента. Проходит через API, outbox и сообщение брокера,
# чтобы по одному значению собрать все логи обработки платежа.
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

REQUEST_ID_MESSAGE_HEADER = "x-request-id"


def get_request_id() -> str | None:
    return request_id_var.get()

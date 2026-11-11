class OutboxPublishError(Exception):
    """Часть записей outbox не удалось опубликовать, они остались в статусе PENDING"""

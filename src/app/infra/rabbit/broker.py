from logging import getLogger

from faststream.rabbit import Channel, RabbitBroker

from app.config import config

broker = RabbitBroker(
    config.RABBIT.URL,
    default_channel=Channel(prefetch_count=config.RABBIT.PREFETCH_COUNT, on_return_raises=True),
    logger=getLogger("app.rabbit"),
)

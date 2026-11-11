import httpx
from dependency_injector import containers, providers

from app.app_layer.services.health.health_service import HealthService
from app.app_layer.services.outbox.clean_service import CleanOutboxService
from app.app_layer.services.outbox.collect_metrics_service import CollectQueueMetricsService
from app.app_layer.services.outbox.send_service import SendOutboxService
from app.app_layer.services.payments.create_payment_service import CreatePaymentService
from app.app_layer.services.payments.get_payment_service import GetPaymentService
from app.app_layer.services.payments.process_payment_service import ProcessPaymentService
from app.config import Config, config
from app.infra.db.database import Database
from app.infra.gateways.emulated import EmulatedPaymentGateway
from app.infra.heartbeat import Heartbeat
from app.infra.http.webhook.client import WebhookClient
from app.infra.locks.payment import PgAdvisoryPaymentLock
from app.infra.rabbit.broker import broker
from app.infra.rabbit.dead_letters import RabbitDeadLetterQueue
from app.infra.rabbit.publisher import RabbitEventPublisher
from app.infra.unit_of_work.sqla import Uow


class DBContainer(containers.DeclarativeContainer):
    config = providers.Dependency(instance_of=Config)

    db = providers.Singleton(Database, settings=config.provided.DB)
    uow = providers.Factory(Uow, session_factory=db.provided.session_factory)
    payment_lock = providers.Factory(PgAdvisoryPaymentLock, session_factory=db.provided.session_factory)


class ClientsContainer(containers.DeclarativeContainer):
    config = providers.Dependency(instance_of=Config)

    http_client = providers.Singleton(httpx.AsyncClient)
    webhook_client = providers.Factory(WebhookClient, client=http_client, settings=config.provided.WEBHOOK)
    payment_gateway = providers.Factory(EmulatedPaymentGateway, settings=config.provided.GATEWAY)


class Container(containers.DeclarativeContainer):
    config = providers.Object(config)

    db = providers.Container(DBContainer, config=config)
    clients = providers.Container(ClientsContainer, config=config)

    rabbit_broker = providers.Object(broker)
    event_publisher = providers.Factory(RabbitEventPublisher, broker=rabbit_broker, settings=config.provided.RABBIT)

    health_service = providers.Factory(HealthService, uow=db.container.uow)
    create_payment_service = providers.Factory(
        CreatePaymentService,
        uow=db.container.uow,
        rabbit_settings=config.provided.RABBIT,
    )
    get_payment_service = providers.Factory(GetPaymentService, uow=db.container.uow)
    process_payment_service = providers.Factory(
        ProcessPaymentService,
        uow=db.container.uow,
        payment_gateway=clients.container.payment_gateway,
        webhook_client=clients.container.webhook_client,
        payment_lock=db.container.payment_lock,
    )
    dead_letter_queue = providers.Factory(RabbitDeadLetterQueue, broker=rabbit_broker, settings=config.provided.RABBIT)
    heartbeat = providers.Singleton(Heartbeat, settings=config.provided.HEARTBEAT)

    clean_outbox_service = providers.Factory(CleanOutboxService, uow=db.container.uow, settings=config.provided.OUTBOX)
    collect_queue_metrics_service = providers.Factory(
        CollectQueueMetricsService,
        uow=db.container.uow,
        dead_letter_queue=dead_letter_queue,
    )
    send_outbox_service = providers.Factory(
        SendOutboxService,
        uow=db.container.uow,
        publisher=event_publisher,
        settings=config.provided.OUTBOX,
    )


async def shutdown_container(container: Container) -> None:
    """Закрывает соединения, которые контейнер успел открыть."""
    await container.clients.http_client().aclose()
    await container.db.db().shutdown()

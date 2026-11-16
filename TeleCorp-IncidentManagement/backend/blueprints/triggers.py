"""Triggers: change feed de Cosmos, colas de Service Bus y Durable Functions."""
import json
import logging
import azure.durable_functions as df
import azure.functions as func

from pulsored import lifecycle
from pulsored.messaging import get_sender
from pulsored.queueing import get_queue
from pulsored.services import correlator, export, infra, notify
from pulsored.store import get_store

bp = df.Blueprint()
log = logging.getLogger("pulsored.triggers")
DB = "pulsored"


# ---------- change feed: reportes -> clasificación IA + correlador ----------
@bp.retry(strategy="exponential_backoff", max_retry_count="5", minimum_interval="00:00:05", maximum_interval="00:01:00")
@bp.cosmos_db_trigger(arg_name="docs", connection="COSMOS", database_name=DB, container_name="reports",
                      lease_container_name="leases", lease_container_prefix="corr-",
                      create_lease_container_if_not_exists=True)
@bp.durable_client_input(client_name="client")
async def correlator_fn(docs: func.DocumentList, client):
    store, queue = get_store(), get_queue()
    for d in docs:
        new_incident = correlator.process_report(store, dict(d), queue)
        if new_incident:
            try:
                await client.start_new("incident_lifecycle", instance_id=new_incident["id"],
                                       client_input=lifecycle.start_input(new_incident))
            except Exception as e:  # noqa: BLE001 - ya existe esa instancia: no duplicar
                log.warning("start_new %s: %s", new_incident["id"], e)


# ---------- change feed: eventos de infraestructura -> infra-linker ----------
@bp.retry(strategy="exponential_backoff", max_retry_count="5", minimum_interval="00:00:05", maximum_interval="00:01:00")
@bp.cosmos_db_trigger(arg_name="docs", connection="COSMOS", database_name=DB, container_name="infra_events",
                      lease_container_name="leases", lease_container_prefix="infra-",
                      create_lease_container_if_not_exists=True)
@bp.durable_client_input(client_name="client")
async def infra_linker(docs: func.DocumentList, client):
    store, queue = get_store(), get_queue()
    for d in docs:
        for iid in infra.link_event(store, dict(d), queue):
            try:
                await client.raise_event(iid, "operator_decision", "CONFIRMAR")
            except Exception as e:  # noqa: BLE001 - nadie esperando: no pasa nada
                log.info("sin orquestación esperando %s: %s", iid, e)


# ---------- Durable: orquestador + actividades ----------
@bp.orchestration_trigger(context_name="context")
def incident_lifecycle(context: df.DurableOrchestrationContext):
    retry = df.RetryOptions(first_retry_interval_in_milliseconds=5000, max_number_of_attempts=3)
    return (yield from lifecycle.run(context, retry))


def _register_activity(name):
    fn = lifecycle.ACTIVITIES[name]

    def activity(arg):
        return fn(arg)

    activity.__name__ = name
    return bp.activity_trigger(input_name="arg", activity=name)(activity)


for _name in lifecycle.ACTIVITIES:
    _register_activity(_name)


# ---------- Service Bus ----------
@bp.service_bus_queue_trigger(arg_name="msg", queue_name="notifications", connection="SB")
def notifier(msg: func.ServiceBusMessage):
    m = json.loads(msg.get_body().decode())
    result = notify.deliver(get_store(), get_sender(), m)
    log.info("notificación %s/%s -> %s", m["incident_id"], m["stage"], result)


@bp.service_bus_queue_trigger(arg_name="msg", queue_name="analytics-export", connection="SB")
def exporter(msg: func.ServiceBusMessage):
    message = json.loads(msg.get_body().decode())   # sobre común incident.upsert / report.upsert
    result = export.send_to_gcp(message)            # excepción -> reintento de Service Bus -> dead-letter
    log.info("export %s %s -> %s", message.get("type"), message.get("event_id"), result)

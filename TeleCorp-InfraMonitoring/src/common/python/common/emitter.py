"""Publicación de eventos `antenna.event` hacia Azure y GCP (RF-AWS-10, 11).

El evento se "reclama" con una escritura condicional (`published_at`): quien lo reclama primero lo publica;
los demás (router, Step Functions, reintentos) ven que ya está publicado. Si el envío a las colas falla, se
libera el reclamo para que el reintento lo vuelva a publicar. El `event_id` es determinista.
"""
import json

from . import aws, ddb, envelope, log, timeutil, validators
from . import events as ev

ITEM_FIELDS = ("antenna_id", "zone_id", "ts", "event_id", "episode_id", "event_type", "severity", "previous_state",
               "new_state", "gb_per_min", "first_anomaly_at", "starts_episode", "detected_at", "prediction",
               "recurrence", "elapsed_minutes")


def complete(e: dict) -> dict:
    """Rellena lo que falte (zona, severidad, id) y devuelve un evento listo para publicar."""
    e = ddb.native(dict(e))
    for k in ("antenna_id", "event_type", "new_state", "ts", "episode_id"):
        if not e.get(k):
            raise validators.ValidationError(f"falta {k}")
    e["ts"] = timeutil.norm(e["ts"])
    if not e.get("zone_id"):
        a = ddb.get(aws.table("ANTENNAS"), {"antenna_id": e["antenna_id"]})
        if not a:
            raise validators.ValidationError("antena desconocida: " + e["antenna_id"])
        e["zone_id"] = a["zone_id"]
    prob = (e.get("prediction") or {}).get("probability")
    if not e.get("severity"):
        e["severity"] = ev.severity_for(e["event_type"], e["new_state"], prob, e.get("base_severity"))
    e["event_id"] = e.get("event_id") or ev.event_id(e["antenna_id"], e["event_type"], e["new_state"], e["ts"])
    e.setdefault("detected_at", timeutil.fmt(timeutil.now()))
    e.setdefault("starts_episode", False)
    return e


def _queue_urls():
    return [aws.env("OUTBOX_AZURE_URL"), aws.env("OUTBOX_GCP_EVENTS_URL")]


def publish(event: dict, correlation_id: str = None) -> dict:
    e = complete(event)
    payload = ev.payload_from(e)
    validators.antenna_event_payload(payload)
    table, key = aws.table("ANTENNAEVENTS"), {"antenna_id": e["antenna_id"], "sk": ev.sort_key(e["ts"], e["event_id"])}
    item = {k: e.get(k) for k in ITEM_FIELDS if e.get(k) is not None}
    item["published_at"] = timeutil.fmt(timeutil.now())
    sets = {k: v for k, v in item.items() if k not in ("antenna_id",)}
    try:
        ddb.update(table, key, sets=sets, condition="attribute_not_exists(published_at)")
    except Exception as exc:  # noqa: BLE001
        if ddb.error_code(exc) == "ConditionalCheckFailedException":
            log.info("evento ya publicado", event_id=e["event_id"], event_type=e["event_type"])
            return {"published": False, "reason": "ya publicado", "event_id": e["event_id"]}
        raise
    env = envelope.make("antenna.event", payload, event_id=e["event_id"], occurred_at=e["detected_at"],
                        correlation_id=correlation_id or e["episode_id"])
    try:
        body = json.dumps(env)
        for url in _queue_urls():
            aws.client("sqs").send_message(QueueUrl=url, MessageBody=body)
    except Exception:
        ddb.update(table, key, removes=["published_at"])  # liberar: el reintento lo publicará
        raise
    log.metric("EventsPublished", 1, EventType=e["event_type"])
    log.info("evento publicado", antenna_id=e["antenna_id"], episode_id=e["episode_id"], event_id=e["event_id"],
             event_type=e["event_type"])
    return {"published": True, "event_id": e["event_id"]}

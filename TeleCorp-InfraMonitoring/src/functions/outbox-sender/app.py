"""Envía los mensajes de las colas outbox a Azure o GCP (RF-AWS-11, 12, 19). El destino se deduce de la cola.

2xx -> borrar. 5xx / 429 / timeout -> fallo parcial con espera creciente (acaba en la DLQ tras 5 recepciones).
4xx distinto de 429 -> error permanente: va directo a la DLQ y se registra, no se reintenta.
Telemetría a GCP: los mensajes de una ventana de 300 s se combinan en pocos POST (máx. 500 muestras).
"""
import json
import urllib.error
import urllib.request

from common import aws, config, envelope, log, secrets
from common import events as ev

TIMEOUT_S = 10
ROUTES = {  # sufijo de la cola -> (destino, ruta, variable de la cola, variable de su DLQ)
    "outbox-azure": ("azure", "/events/infra", "OUTBOX_AZURE_URL", "DLQ_AZURE_URL"),
    "outbox-gcp-events": ("gcp", "/ingest/events", "OUTBOX_GCP_EVENTS_URL", "DLQ_GCP_EVENTS_URL"),
    "outbox-gcp-telemetry": ("gcp", "/ingest/telemetry", "OUTBOX_GCP_TELEMETRY_URL", "DLQ_GCP_TELEMETRY_URL"),
}


def post(url: str, api_key: str, body: dict):
    """POST JSON. Devuelve el código HTTP, o 0 si no hubo respuesta (red, DNS, timeout)."""
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={
        "Content-Type": "application/json", "X-API-Key": api_key, "X-Correlation-Id": body.get("correlation_id", ""),
        "Idempotency-Key": body.get("event_id", "")})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:  # noqa: S310  la URL viene de SSM
            return r.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception as exc:  # noqa: BLE001
        log.warn("sin respuesta del destino", url=url, error=str(exc))
        return 0


def verdict(status: int) -> str:
    if 200 <= status < 300:
        return "ok"
    if status in (0, 429) or status >= 500:
        return "retry"
    return "permanent"


def _backoff(sqs, queue_url, record):
    n = int(record.get("attributes", {}).get("ApproximateReceiveCount", 1))
    try:
        sqs.change_message_visibility(QueueUrl=queue_url, ReceiptHandle=record["receiptHandle"],
                                      VisibilityTimeout=min(900, 30 * 2 ** (n - 1)))
    except Exception as exc:  # noqa: BLE001
        log.warn("no se pudo cambiar la visibilidad", error=str(exc))


def _to_dlq(sqs, dlq_url, record, reason):
    sqs.send_message(QueueUrl=dlq_url, MessageBody=record["body"], MessageAttributes={
        "reason": {"DataType": "String", "StringValue": reason[:200]}})
    log.error("error permanente: enviado a la DLQ", reason=reason, message_id=record.get("messageId"))
    log.metric("OutboxPermanentFailures", 1)


def _combine_telemetry(records):
    """Une las muestras de varios mensajes sin duplicados y las parte en lotes de <= 500."""
    seen, samples = set(), []
    for r in records:
        for s in json.loads(r["body"]).get("payload", {}).get("samples", []):
            k = (s["antenna_id"], s["ts"])
            if k not in seen:
                seen.add(k)
                samples.append(s)
    samples.sort(key=lambda s: (s["ts"], s["antenna_id"]))
    out = []
    for i in range(0, len(samples), 500):
        chunk = samples[i:i + 500]
        out.append(envelope.make("telemetry.batch", {"samples": chunk}, event_id=ev.batch_event_id(
            "telemetry", [f"{s['antenna_id']}|{s['ts']}" for s in chunk])))
    return out


def handler(event, context):
    records = event.get("Records", [])
    if not records:
        return {"batchItemFailures": []}
    suffix = next(s for s in ROUTES if records[0]["eventSourceARN"].endswith(s))
    dest, path, queue_var, dlq_var = ROUTES[suffix]
    sqs = aws.client("sqs")
    try:
        base, key = config.get_endpoint(dest), secrets.outbound_key(dest)
    except Exception as exc:  # noqa: BLE001
        log.error("no se pudo leer el endpoint o la clave", error=str(exc))
        base, key = None, ""
    url = f"{base}{path}" if base else None

    def deliver(body: dict) -> str:
        if not url or not key:
            log.warn("destino sin configurar (endpoint o clave vacíos)", dest=dest)
            return "retry"
        status = post(url, key, body)
        log.info("entrega", dest=dest, status=status, event_id=body.get("event_id"))
        return verdict(status)

    failures = []
    if suffix == "outbox-gcp-telemetry":
        try:
            results = [deliver(b) for b in _combine_telemetry(records)]
        except (KeyError, ValueError, TypeError) as exc:
            for r in records:
                _to_dlq(sqs, aws.env(dlq_var), r, "mensaje ilegible: " + str(exc))
            return {"batchItemFailures": []}
        if "retry" in results:
            for r in records:
                failures.append({"itemIdentifier": r["messageId"]})
                _backoff(sqs, aws.env(queue_var), r)
        elif "permanent" in results:
            for r in records:
                _to_dlq(sqs, aws.env(dlq_var), r, "rechazo permanente (4xx) del destino")
        return {"batchItemFailures": failures}

    for r in records:
        try:
            body = json.loads(r["body"])
        except ValueError as exc:
            _to_dlq(sqs, aws.env(dlq_var), r, "JSON inválido: " + str(exc))
            continue
        result = deliver(body)
        if result == "retry":
            failures.append({"itemIdentifier": r["messageId"]})
            _backoff(sqs, aws.env(queue_var), r)
        elif result == "permanent":
            _to_dlq(sqs, aws.env(dlq_var), r, "rechazo permanente (4xx) del destino")
    return {"batchItemFailures": failures}

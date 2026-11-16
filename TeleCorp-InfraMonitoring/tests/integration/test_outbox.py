"""outbox-sender: entrega, reintentos con espera creciente, errores permanentes y combinación de telemetría."""
import json

import boto3
import pytest
from support import load_function
from helpers import Receiver, sample, ts

from common import config, emitter, ingest


@pytest.fixture()
def dest(cloud):
    r = Receiver()
    ssm = boto3.client("ssm")
    for name in ("azure", "gcp"):
        ssm.put_parameter(Name=f"/pulsored/endpoints/{name}", Value=r.url, Type="String", Overwrite=True)
    config.reset_cache()
    yield r
    r.stop()


def record(cloud, key, body, n=1, arn_suffix=None):
    q, _ = cloud.urls[key]
    cloud.sqs.send_message(QueueUrl=q, MessageBody=json.dumps(body) if not isinstance(body, str) else body)
    m = cloud.sqs.receive_message(QueueUrl=q, MaxNumberOfMessages=1, AttributeNames=["All"])["Messages"][0]
    names = {"AZURE": "outbox-azure", "GCP_EVENTS": "outbox-gcp-events", "GCP_TELEMETRY": "outbox-gcp-telemetry"}
    return {"messageId": m["MessageId"], "receiptHandle": m["ReceiptHandle"], "body": m["Body"],
            "attributes": {"ApproximateReceiveCount": str(n)},
            "eventSourceARN": f"arn:aws:sqs:us-east-1:123456789012:stack-{names[key]}"}


def an_event(cloud):
    ingest.process([sample("ANT-LIM-007", 0, 95), sample("ANT-LIM-007", 1, 30), sample("ANT-LIM-007", 2, 30)])
    emitter.publish(cloud.events("ANT-LIM-007")[0])
    return cloud.messages("AZURE")[0]


def test_entrega_a_azure_con_clave_y_ruta(cloud, dest):
    sender = load_function("outbox-sender")
    env = an_event(cloud)
    r = sender.handler({"Records": [record(cloud, "AZURE", env)]}, None)
    assert r == {"batchItemFailures": []}
    got = dest.received[0]
    assert got["path"] == "/events/infra" and got["key"] == "out-azure" and got["body"]["event_id"] == env["event_id"]


def test_entrega_de_eventos_a_gcp(cloud, dest):
    env = an_event(cloud)
    load_function("outbox-sender").handler({"Records": [record(cloud, "GCP_EVENTS", env)]}, None)
    assert dest.received[0]["path"] == "/ingest/events" and dest.received[0]["key"] == "out-gcp"


def test_503_reintenta_con_espera_creciente(cloud, dest):
    dest.status = 503
    env = an_event(cloud)
    rec = record(cloud, "AZURE", env, n=3)
    r = load_function("outbox-sender").handler({"Records": [rec]}, None)
    assert r["batchItemFailures"] == [{"itemIdentifier": rec["messageId"]}]
    # tercer intento -> 30 * 2^2 = 120 s de invisibilidad: el mensaje no se ve de inmediato
    assert cloud.sqs.receive_message(QueueUrl=cloud.urls["AZURE"][0]).get("Messages") is None


@pytest.mark.parametrize("status", [429, 500, 502])
def test_codigos_reintentables(cloud, dest, status):
    dest.status = status
    rec = record(cloud, "AZURE", an_event(cloud))
    assert load_function("outbox-sender").handler({"Records": [rec]}, None)["batchItemFailures"]


@pytest.mark.parametrize("status", [400, 401, 404, 422])
def test_4xx_va_directo_a_la_dlq(cloud, dest, status):
    dest.status = status
    rec = record(cloud, "AZURE", an_event(cloud))
    r = load_function("outbox-sender").handler({"Records": [rec]}, None)
    assert r == {"batchItemFailures": []}
    dlq = cloud.sqs.receive_message(QueueUrl=cloud.urls["AZURE"][1], MessageAttributeNames=["All"])["Messages"]
    assert len(dlq) == 1 and "4xx" in dlq[0]["MessageAttributes"]["reason"]["StringValue"]


def test_sin_respuesta_del_receptor_reintenta(cloud, dest):
    dest.stop()
    rec = record(cloud, "AZURE", an_event(cloud))
    assert load_function("outbox-sender").handler({"Records": [rec]}, None)["batchItemFailures"]


def test_mensaje_ilegible_va_a_la_dlq(cloud, dest):
    rec = record(cloud, "AZURE", "esto no es json")
    assert load_function("outbox-sender").handler({"Records": [rec]}, None)["batchItemFailures"] == []
    assert len(cloud.sqs.receive_message(QueueUrl=cloud.urls["AZURE"][1]).get("Messages", [])) == 1


def test_endpoint_sin_configurar_reintenta(cloud):
    rec = record(cloud, "AZURE", {"event_id": "x"})
    assert load_function("outbox-sender").handler({"Records": [rec]}, None)["batchItemFailures"]


def test_fallo_parcial_solo_reporta_los_que_fallaron(cloud, dest):
    sender = load_function("outbox-sender")
    ok = record(cloud, "AZURE", an_event(cloud))
    bad = record(cloud, "AZURE", "no json")
    r = sender.handler({"Records": [ok, bad]}, None)
    assert r["batchItemFailures"] == [] and len(dest.received) == 1


def test_telemetria_combina_mensajes_y_deduplica(cloud, dest):
    ingest.process([sample(i=i) for i in range(3)])
    ingest.process([sample(i=i) for i in range(3, 6)])
    msgs = cloud.messages("GCP_TELEMETRY")
    recs = [record(cloud, "GCP_TELEMETRY", m) for m in msgs] + [record(cloud, "GCP_TELEMETRY", msgs[0])]
    r = load_function("outbox-sender").handler({"Records": recs}, None)
    assert r == {"batchItemFailures": []}
    assert len(dest.received) == 1 and dest.received[0]["path"] == "/ingest/telemetry"
    assert len(dest.received[0]["body"]["payload"]["samples"]) == 6
    first_id = dest.received[0]["body"]["event_id"]
    load_function("outbox-sender").handler({"Records": [record(cloud, "GCP_TELEMETRY", m) for m in msgs]}, None)
    assert dest.received[1]["body"]["event_id"] == first_id  # mismo lote -> mismo id


def test_telemetria_se_parte_en_lotes_de_500(cloud, dest):
    samples = [{"antenna_id": "ANT-LIM-001", "zone_id": "LIM-SURCO", "ts": f"2026-11-15T{h:02d}:{m:02d}:00Z",
                "gb_per_min": 90.0, "state": "NORMAL"} for h in range(10) for m in range(60)][:550]
    from common import envelope
    body = envelope.make("telemetry.batch", {"samples": samples[:300]})
    body2 = envelope.make("telemetry.batch", {"samples": samples[250:]})
    recs = [record(cloud, "GCP_TELEMETRY", body), record(cloud, "GCP_TELEMETRY", body2)]
    load_function("outbox-sender").handler({"Records": recs}, None)
    assert [len(r["body"]["payload"]["samples"]) for r in dest.received] == [500, 50]


def test_telemetria_con_503_reintenta_todos(cloud, dest):
    dest.status = 503
    ingest.process([sample(i=i) for i in range(2)])
    recs = [record(cloud, "GCP_TELEMETRY", m) for m in cloud.messages("GCP_TELEMETRY")]
    r = load_function("outbox-sender").handler({"Records": recs}, None)
    assert len(r["batchItemFailures"]) == len(recs)

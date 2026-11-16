"""Evento -> router/emit-event -> colas outbox, y sus Lambdas auxiliares de Step Functions."""
import json
import os

import boto3
import pytest
from support import api_event, load_function
from helpers import sample, ts

from common import aws, ddb, emitter, ingest, validators


def fail_episode(antenna="ANT-LIM-007", at=0):
    return ingest.process([sample(antenna, at, 95), sample(antenna, at + 1, 30), sample(antenna, at + 2, 29)])["events"]


def stream_record(item):
    """Registro de DynamoDB Streams (INSERT) a partir de un item ya guardado."""
    return {"eventName": "INSERT", "dynamodb": {"SequenceNumber": "1", "NewImage": ddb.to_item(item)}}


class FakeSF:
    def __init__(self):
        self.started = []

    def start_execution(self, **kw):
        if any(s["name"] == kw["name"] for s in self.started):
            raise type("ExecutionAlreadyExists", (Exception,), {})()
        self.started.append(kw)
        return {"executionArn": "arn:fake:" + kw["name"]}


@pytest.fixture()
def sf(cloud, monkeypatch):
    fake = FakeSF()
    real = aws.client
    monkeypatch.setattr(aws, "client", lambda s: fake if s == "stepfunctions" else real(s))
    monkeypatch.setenv("SF_LIFECYCLE_ARN", "arn:lifecycle")
    monkeypatch.setenv("SF_PREVENTIVE_ARN", "arn:preventive")
    return fake


def test_emit_publica_en_azure_y_gcp_con_sobre_valido(cloud):
    ev = fail_episode()[0]
    out = emitter.publish(ev)
    assert out["published"] is True
    az, gcp = cloud.messages("AZURE"), cloud.messages("GCP_EVENTS")
    assert len(az) == len(gcp) == 1 and az[0] == gcp[0]
    env = az[0]
    validators.validate_envelope(env, "antenna.event")
    validators.antenna_event_payload(env["payload"])
    assert env["event_id"] == ev["event_id"] and env["payload"]["event_type"] == "FALLA"


def test_emit_es_idempotente_una_sola_publicacion(cloud):
    ev = fail_episode()[0]
    outs = [emitter.publish(ev) for _ in range(5)]
    assert [o["published"] for o in outs] == [True, False, False, False, False]
    assert len(cloud.messages("AZURE")) == 1 and len(cloud.messages("GCP_EVENTS")) == 1


def test_emit_libera_el_reclamo_si_falla_el_envio(cloud, monkeypatch):
    ev = fail_episode()[0]
    real = aws.client

    class Broken:
        def send_message(self, **kw):
            raise RuntimeError("SQS caído")
    monkeypatch.setattr(aws, "client", lambda s: Broken() if s == "sqs" else real(s))
    with pytest.raises(RuntimeError):
        emitter.publish(ev)
    monkeypatch.setattr(aws, "client", real)
    assert emitter.publish(ev)["published"] is True  # el reintento sí publica
    assert len(cloud.messages("AZURE")) == 1


def test_emit_completa_zona_severidad_e_id(cloud):
    out = emitter.publish({"event_type": "PROLONGADA", "antenna_id": "ANT-LIM-007", "episode_id": "EP-X",
                           "new_state": "CAIDA", "base_severity": "ALTA", "ts": ts(5), "elapsed_minutes": 31,
                           "first_anomaly_at": ts(1), "gb_per_min": 30})
    p = cloud.messages("AZURE")[0]["payload"]
    assert out["published"] and p["zone_id"] == "LIM-MIRAFLORES" and p["severity"] == "CRITICA"
    assert p["elapsed_minutes"] == 31


def test_router_primer_evento_arranca_la_sf_y_no_publica(cloud, sf):
    router = load_function("event-router")
    item = cloud.events("ANT-LIM-007") or None
    fail_episode()
    item = cloud.events("ANT-LIM-007")[0]
    r = router.handler({"Records": [stream_record(item)]}, None)
    assert r == {"batchItemFailures": []}
    assert [s["name"] for s in sf.started] == [item["episode_id"]]
    assert json.loads(sf.started[0]["input"])["event_id"] == item["event_id"]
    assert cloud.messages("AZURE") == []
    router.handler({"Records": [stream_record(item)]}, None)  # repetido: ExecutionAlreadyExists se ignora
    assert len(sf.started) == 1


def test_router_evento_posterior_se_publica_directo(cloud, sf):
    router = load_function("event-router")
    ingest.process([sample("ANT-LIM-007", i, 30) for i in range(2)] + [sample("ANT-LIM-007", i, 95) for i in range(2, 5)])
    evs = {e["event_type"]: e for e in cloud.events("ANT-LIM-007")}
    router.handler({"Records": [stream_record(evs["RECUPERACION"])]}, None)
    assert sf.started == []
    assert [m["payload"]["event_type"] for m in cloud.messages("AZURE")] == ["RECUPERACION"]


def test_router_ignora_eventos_ya_publicados_y_modificaciones(cloud, sf):
    router = load_function("event-router")
    fail_episode()
    item = cloud.events("ANT-LIM-007")[0]
    router.handler({"Records": [stream_record({**item, "published_at": ts(9)}),
                                {**stream_record(item), "eventName": "MODIFY"}]}, None)
    assert sf.started == [] and cloud.messages("AZURE") == []


def test_router_reporta_fallo_de_un_registro(cloud, sf):
    router = load_function("event-router")
    r = router.handler({"Records": [{"eventName": "INSERT", "dynamodb": {"SequenceNumber": "7", "NewImage": {}}}]}, None)
    assert r["batchItemFailures"] == [{"itemIdentifier": "7"}]


def test_check_recovery_y_recuperacion_publicada_una_sola_vez(cloud):
    chk = load_function("check-recovery")
    ev = fail_episode()[0]
    inp = {"antenna_id": ev["antenna_id"], "episode_id": ev["episode_id"], "first_anomaly_at": ev["first_anomaly_at"]}
    out = chk.handler(inp, None)
    assert out["recovered"] is False and out["state"] == "CAIDA" and out["recovery_event"] is None
    ingest.process([sample("ANT-LIM-007", i, 95) for i in range(3, 6)])
    out = chk.handler(inp, None)
    assert out["recovered"] is True and out["recovery_event"]["event_type"] == "RECUPERACION"
    # el router y la SF intentan publicar la misma recuperación: sale una vez
    rec = out["recovery_event"]
    assert emitter.publish(rec)["published"] is True and emitter.publish(rec)["published"] is False
    assert [m["payload"]["event_type"] for m in cloud.messages("AZURE")] == ["RECUPERACION"]


def test_check_recovery_otro_episodio_cuenta_como_recuperada(cloud):
    chk = load_function("check-recovery")
    ev = fail_episode()[0]
    out = chk.handler({"antenna_id": ev["antenna_id"], "episode_id": "EP-OTRO", "first_anomaly_at": ev["first_anomaly_at"]}, None)
    assert out["recovered"] is True


def test_enrich_context(cloud):
    ev = fail_episode()[0]
    out = load_function("enrich-context").handler(ev, None)
    assert out["zone"]["zone_id"] == "LIM-MIRAFLORES" and len(out["neighbors"]) == 4
    assert out["config"] == {"check_interval_s": 60, "prolonged_after_min": 30, "max_episode_min": 720}


def test_tres_episodios_producen_estructural(cloud):
    for k in range(3):  # tres episodios distintos de la misma antena
        base = k * 8
        ingest.process([sample("ANT-LIM-010", base + i, 30 if i < 3 else 95) for i in range(7)])
    count = load_function("count-episodes")
    ts_now = ts(30)
    out = count.handler({"antenna": {"antenna_id": "ANT-LIM-010", "zone_id": "LIM-MIRAFLORES"}, "ts": ts_now}, None)
    assert out["episodes_7d"] == 3 and out["structural"] is True
    ev = out["event"]
    assert ev["event_type"] == "ESTRUCTURAL" and ev["recurrence"] == {"episodes_7d": 3, "episodes_30d": 3}
    emitter.publish(ev)
    payload = cloud.messages("AZURE")[0]["payload"]
    assert payload["severity"] == "ALTA" and payload["recurrence"]["episodes_7d"] == 3
    ddb.update(aws.table("ANTENNAS"), {"antenna_id": "ANT-LIM-010"}, sets={"structural_until": out["structural_until"]})
    again = count.handler({"antenna": {"antenna_id": "ANT-LIM-010", "zone_id": "LIM-MIRAFLORES"}, "ts": ts_now}, None)
    assert again["structural"] is False and again["reason"] == "ya marcada como estructural"


def test_dos_episodios_no_son_estructural_y_umbral_manual(cloud):
    for k in range(2):
        ingest.process([sample("ANT-LIM-011", k * 8 + i, 30 if i < 3 else 95) for i in range(7)])
    count = load_function("count-episodes")
    a = {"antenna": {"antenna_id": "ANT-LIM-011", "zone_id": "LIM-MIRAFLORES"}, "ts": ts(30)}
    assert count.handler(a, None)["structural"] is False
    assert count.handler({**a, "overrides": {"episodes_7d_min": 2}}, None)["structural"] is True


def test_list_antennas(cloud):
    out = load_function("list-antennas").handler({"episodes_7d_min": 2}, None)
    assert len(out["antennas"]) == 60 and out["overrides"] == {"episodes_7d_min": 2}


def test_dlq_redrive_mueve_los_mensajes(cloud):
    sqs = cloud.sqs
    q, dlq = cloud.urls["AZURE"]
    for i in range(23):
        sqs.send_message(QueueUrl=dlq, MessageBody=json.dumps({"n": i}))
    out = load_function("dlq-redrive").handler({"dlq_url": dlq, "source_url": q}, None)
    assert out["moved"] == 23 and len(cloud.messages("AZURE", dlq=True)) == 0 and len(cloud.messages("AZURE")) == 23

"""Ingesta end-to-end contra DynamoDB/SQS simulados."""
import json

from support import api_event, load_function
from helpers import sample, ts

from common import aws, ddb, ingest


def body(samples, type_="telemetry.batch"):
    return {"event_id": "11111111-1111-4111-8111-111111111111", "type": type_, "schema_version": "1.0", "source": "aws",
            "occurred_at": ts(0), "correlation_id": "c-1", "payload": {"samples": samples}}


def call(samples, caller="telemetry", env=None):
    fn = load_function("telemetry-ingest")
    r = fn.handler(api_event("POST /telemetry", caller, env or body(samples)), None)
    return r["statusCode"], json.loads(r["body"]), r


def rows(cloud, a="ANT-LIM-001"):
    return ddb.query(aws.table("TELEMETRY"), "antenna_id = :a", {":a": a})


def test_lote_valido_acepta_y_clasifica(cloud):
    code, out, _ = call([sample(i=0, gb=95), sample(i=1, gb=0), sample("ANT-LIM-002", 0, 140)])
    assert code == 202 and out == {"accepted": 3, "duplicates": 0, "rejected": []}
    assert [r["state"] for r in rows(cloud)] == ["NORMAL", "INHABILITADA"]
    assert cloud.antenna("ANT-LIM-001")["last_gb_per_min"] == 0


def test_aceptacion_parcial_con_rechazos_registrados(cloud):
    bad = [sample("ANT-LIM-001", 0, -5), {"antenna_id": "ANT-LIM-001", "gb_per_min": 5},
           sample("ANT-LIM-999", 0, 90), sample("ANT-LIM-001", 0, "x"), sample("ANT-LIM-001", 1, 90, zone_id="LIM-ATE"),
           sample("ANT-LIM-001", 2, 90, extra_field=1), sample(i=3, gb=80)]
    code, out, _ = call(bad)
    assert code == 202 and out["accepted"] == 1 and len(out["rejected"]) == 6
    assert {r["index"] for r in out["rejected"]} == {0, 1, 2, 3, 4, 5}
    assert len(ddb.scan(aws.table("REJECTS"))) == 6


def test_sobre_invalido_es_400(cloud):
    for env in ({"nada": 1}, body([sample()], "prediction.batch"), body([]), body([sample()] * 501), [1, 2]):
        assert call(None, env=env)[0] == 400


def test_sin_permiso_es_403(cloud):
    assert call([sample()], caller="azure")[0] == 403


def test_idempotencia_mismo_lote_cinco_veces(cloud):
    batch = [sample(i=i, gb=90 + i) for i in range(5)]
    outs = [call(batch)[1] for _ in range(5)]
    assert outs[0]["accepted"] == 5 and all(o["accepted"] == 0 and o["duplicates"] == 5 for o in outs[1:])
    assert len(rows(cloud)) == 5
    assert len(cloud.messages("GCP_TELEMETRY")) == 1  # solo el primer lote va a GCP


def test_duplicados_dentro_del_lote(cloud):
    _, out, _ = call([sample(i=0), sample(i=0), sample(i=1)])
    assert out["accepted"] == 2 and out["duplicates"] == 1 and len(rows(cloud)) == 2


def test_muestras_desordenadas_se_procesan_en_orden(cloud):
    call([sample(i=2, gb=30), sample(i=0, gb=95), sample(i=1, gb=30)])
    a = cloud.antenna("ANT-LIM-001")
    assert a["state"] == "CAIDA" and a["last_ts"] == ts(2)


def test_telemetria_a_gcp_con_zona_y_estado(cloud):
    call([sample(i=0, gb=30)])
    msgs = cloud.messages("GCP_TELEMETRY")
    assert len(msgs) == 1 and msgs[0]["type"] == "telemetry.batch" and msgs[0]["source"] == "aws"
    s = msgs[0]["payload"]["samples"][0]
    assert s["zone_id"] == "LIM-SURCO" and s["state"] == "CAIDA"


def test_falla_confirmada_escribe_evento_y_actualiza_estado(cloud):
    call([sample(i=0, gb=95), sample(i=1, gb=30), sample(i=2, gb=29)])
    evs = cloud.events("ANT-LIM-001")
    assert len(evs) == 1
    e = evs[0]
    assert e["event_type"] == "FALLA" and e["severity"] == "ALTA" and e["starts_episode"] is True
    assert e["first_anomaly_at"] == ts(1) and e["zone_id"] == "LIM-SURCO"
    a = cloud.antenna("ANT-LIM-001")
    assert a["state"] == "CAIDA" and a["episode_id"] == e["episode_id"]


def test_el_estado_se_conserva_entre_lotes(cloud):
    call([sample(i=0, gb=30)])
    assert cloud.events("ANT-LIM-001") == []
    call([sample(i=1, gb=30)])
    assert len(cloud.events("ANT-LIM-001")) == 1


def test_conflicto_optimista_reintenta_una_vez(cloud, monkeypatch):
    real, n = ingest._write_state, {"n": 0}

    def flaky(*a, **kw):
        n["n"] += 1
        if n["n"] == 1:
            err = type("E", (Exception,), {"response": {"Error": {"Code": "ConditionalCheckFailedException"}}})
            raise err()
        return real(*a, **kw)
    monkeypatch.setattr(ingest, "_write_state", flaky)
    code, out, _ = call([sample(i=0), sample(i=1)])
    assert code == 202 and out["accepted"] == 2 and n["n"] == 2


def test_throttling_responde_429_con_retry_after(cloud, monkeypatch):
    def boom(*a, **kw):
        err = type("E", (Exception,), {"response": {"Error": {"Code": "ProvisionedThroughputExceededException"}}})
        raise err()
    monkeypatch.setattr(ingest, "_write_state", boom)
    code, out, r = call([sample()])
    assert code == 429 and r["headers"]["Retry-After"] == "2"


def test_lote_de_500_muestras_de_60_antenas(cloud):
    samples = [sample(f"ANT-LIM-{a:03d}", i, 90 + (a % 7)) for a in range(1, 61) for i in range(8)][:500]
    code, out, _ = call(samples)
    assert code == 202 and out["accepted"] == 480 and out["rejected"] == []


def test_ttl_de_telemetria(cloud):
    call([sample(i=0)])
    r = rows(cloud)[0]
    assert r["expire_at"] > 0 and "state" in r

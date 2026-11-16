"""Lambdas de la API: autenticación, antenas, config, predicciones, simulador, mock."""
import json
from datetime import datetime, timedelta, timezone

import boto3
import pytest
from support import api_event, load_function
from helpers import sample, ts

from common import aws, config, ddb, ingest, timeutil

from test_events_flow import FakeSF, sf  # noqa: F401  (fixture)


def now_plus(**kw):
    return (datetime.now(timezone.utc) + timedelta(**kw)).strftime("%Y-%m-%dT%H:%M:%SZ")


def call(fn, route, caller="operator", **kw):
    r = load_function(fn).handler(api_event(route, caller, **kw), None)
    return r["statusCode"], json.loads(r["body"]), r


# ---------------- authorizer ----------------
def test_authorizer(cloud):
    fn = load_function("authorizer")
    ok = fn.handler({"headers": {"X-API-Key": "k-gcp"}}, None)
    assert ok == {"isAuthorized": True, "context": {"caller": "gcp"}}
    assert fn.handler({"headers": {"x-api-key": "k-operator"}}, None)["context"]["caller"] == "operator"
    for h in ({}, {"x-api-key": ""}, {"x-api-key": "mala"}, {"x-api-key": "out-azure"}):
        assert fn.handler({"headers": h}, None) == {"isAuthorized": False}


def test_health_sin_clave(cloud):
    r = load_function("health").handler({}, None)
    assert r["statusCode"] == 200 and json.loads(r["body"])["status"] == "ok"


# ---------------- config ----------------
def test_config_versionada_sin_secretos(cloud):
    code, out, _ = call("config-api", "GET /config", "azure")
    assert code == 200 and len(out["config_version"]) == 8 and out["zones_version"] == "1.0.0"
    assert out["items"]["THRESHOLD_DOWN"] == 60 and out["items"]["PRED_ALERT_MIN"] == 0.7
    assert out["USERS_THRESHOLD"] == 10 and out["WINDOW_HOURS"] == 24 and out["CORRELATION_MODE"] == "ZONA"
    blob = json.dumps(out)
    assert "k-gcp" not in blob and "out-azure" not in blob and "secret" not in blob.lower()


def test_config_lee_ssm_y_cambia_la_version(cloud):
    _, before, _ = call("config-api", "GET /config")
    boto3.client("ssm").put_parameter(Name="/pulsored/config/PRED_ALERT_MIN", Value="0.8", Type="String")
    boto3.client("ssm").put_parameter(Name="/pulsored/config/DEMO_MODE", Value="true", Type="String")
    config.reset_cache()
    _, after, _ = call("config-api", "GET /config")
    assert after["items"]["PRED_ALERT_MIN"] == 0.8 and after["items"]["DEMO_MODE"] is True
    assert before["config_version"] != after["config_version"]


def test_config_exige_clave_permitida(cloud):
    assert call("config-api", "GET /config", "telemetry")[0] == 403
    assert call("config-api", "GET /config", None)[0] == 403


# ---------------- antennas ----------------
def test_antennas_lista_y_filtros(cloud):
    code, out, _ = call("antennas-api", "GET /antennas", "azure")
    assert code == 200 and len(out["antennas"]) == 60 and out["antennas"][0]["antenna_id"] == "ANT-LIM-001"
    assert len(call("antennas-api", "GET /antennas", query={"zone_id": "LIM-ATE"})[1]["antennas"]) == 5
    ingest.process([sample("ANT-LIM-001", i, 30) for i in range(2)])
    down = call("antennas-api", "GET /antennas", query={"state": "CAIDA"})[1]["antennas"]
    assert [a["antenna_id"] for a in down] == ["ANT-LIM-001"] and down[0]["episode_id"]


def test_antenna_detalle_con_prediccion_vigente(cloud, sf):  # noqa: F811
    code, out, _ = call("antennas-api", "GET /antennas/{antenna_id}", "gcp", path={"antenna_id": "ANT-LIM-003"})
    assert code == 200 and out["prediction"] is None and out["state"] == "NORMAL" and out["zone_id"] == "LIM-SURCO"
    send_prediction([pred("ANT-LIM-003", 0.55)])
    out = call("antennas-api", "GET /antennas/{antenna_id}", path={"antenna_id": "ANT-LIM-003"})[1]
    assert out["prediction"]["probability"] == 0.55
    assert call("antennas-api", "GET /antennas/{antenna_id}", path={"antenna_id": "ANT-LIM-999"})[0] == 404


def test_antenna_metricas(cloud):
    ingest.process([sample("ANT-LIM-002", i, 70 + i) for i in range(20, 30)])
    code, out, _ = call("antennas-api", "GET /antennas/{antenna_id}/metrics", path={"antenna_id": "ANT-LIM-002"},
                        query={"minutes": "20"})
    assert code == 200 and len(out["samples"]) == 10 and out["samples"][0]["state"] == "NORMAL"
    out = call("antennas-api", "GET /antennas/{antenna_id}/metrics", path={"antenna_id": "ANT-LIM-002"},
               query={"minutes": "5"})[1]
    assert 4 <= len(out["samples"]) <= 6
    for bad in ("0", "4321", "abc"):
        assert call("antennas-api", "GET /antennas/{antenna_id}/metrics", path={"antenna_id": "ANT-LIM-002"},
                    query={"minutes": bad})[0] == 400


# ---------------- predictions ----------------
def pred(antenna, p, **kw):
    return {"antenna_id": antenna, "probability": p, "predicted_state": "CAIDA", "horizon_minutes": 120,
            "valid_until": now_plus(hours=3), **kw}


def send_prediction(preds, caller="gcp", generated_at=None, model="m-1"):
    env = {"event_id": "22222222-2222-4222-8222-222222222222", "type": "prediction.batch", "schema_version": "1.0",
           "source": "gcp", "occurred_at": ts(0), "correlation_id": "c",
           "payload": {"model_version": model, "generated_at": generated_at or timeutil.fmt(timeutil.now()),
                       "predictions": preds}}
    return call("predictions-ingest", "POST /predictions", caller, body=env)


def test_prediccion_0_70_arranca_alerta_y_0_69_no(cloud, sf):  # noqa: F811
    code, out, _ = send_prediction([pred("ANT-LIM-001", 0.83), pred("ANT-LIM-002", 0.70), pred("ANT-LIM-003", 0.69),
                                    pred("ANT-LIM-004", 0.50)])
    assert code == 202 and out == {"accepted": 4, "duplicates": 0, "rejected": []}
    assert sorted(s["name"].rsplit("-", 1)[0] for s in sf.started) == ["ANT-LIM-001", "ANT-LIM-002"]
    started = json.loads(sf.started[0]["input"])
    assert started["probability"] in (0.83, 0.7) and started["model_version"] == "m-1"


def test_prediccion_horizonte_de_25_horas_no_alerta(cloud, sf):  # noqa: F811
    send_prediction([pred("ANT-LIM-001", 0.95, horizon_minutes=1500)])
    assert sf.started == []


def test_prediccion_rechazos(cloud, sf):  # noqa: F811
    bad = [pred("ANT-LIM-999", 0.9), pred("ANT-LIM-001", 1.2), pred("ANT-LIM-001", 0.9, predicted_state="NORMAL"),
           pred("ANT-LIM-001", 0.9, horizon_minutes=0), pred("ANT-LIM-001", 0.9, valid_until="2020-01-01T00:00:00Z"),
           pred("ANT-LIM-001", 0.9, zone_id="LIM-ATE"), {"antenna_id": "ANT-LIM-001"}, "x"]
    code, out, _ = send_prediction(bad)
    assert code == 202 and out["accepted"] == 0 and [r["index"] for r in out["rejected"]] == list(range(8))
    assert sf.started == []


def test_prediccion_idempotente_y_solo_reemplaza_si_es_mas_nueva(cloud, sf):  # noqa: F811
    t1, t2 = now_plus(minutes=-10), now_plus(minutes=-5)
    send_prediction([pred("ANT-LIM-001", 0.55)], generated_at=t1)
    assert send_prediction([pred("ANT-LIM-001", 0.99)], generated_at=t1)[1]["duplicates"] == 1  # misma generación
    assert send_prediction([pred("ANT-LIM-001", 0.99)], generated_at=now_plus(minutes=-20))[1]["duplicates"] == 1
    assert ddb.get(aws.table("PREDICTIONS"), {"antenna_id": "ANT-LIM-001"})["probability"] == ddb.Decimal("0.55")
    assert send_prediction([pred("ANT-LIM-001", 0.60)], generated_at=t2)[1]["accepted"] == 1
    assert ddb.get(aws.table("PREDICTIONS"), {"antenna_id": "ANT-LIM-001"})["probability"] == ddb.Decimal("0.6")


def test_prediccion_con_alerta_vigente_no_arranca_otra_sf(cloud, sf):  # noqa: F811
    ddb.update(aws.table("ANTENNAS"), {"antenna_id": "ANT-LIM-001"}, sets={"preventive_until": now_plus(hours=2)})
    send_prediction([pred("ANT-LIM-001", 0.9)])
    assert sf.started == []


def test_prediccion_solo_acepta_la_clave_de_gcp(cloud, sf):  # noqa: F811
    assert send_prediction([pred("ANT-LIM-001", 0.9)], caller="azure")[0] == 403
    assert call("predictions-ingest", "POST /predictions", "gcp", body={"x": 1})[0] == 400


def test_get_predictions_vigentes_ordenadas(cloud, sf):  # noqa: F811
    send_prediction([pred("ANT-LIM-001", 0.55), pred("ANT-LIM-002", 0.91), pred("ANT-LIM-006", 0.75),
                     pred("ANT-LIM-003", 0.30)])
    code, out, _ = call("predictions-api", "GET /predictions", "azure")
    assert code == 200 and [p["antenna_id"] for p in out["predictions"]] == ["ANT-LIM-002", "ANT-LIM-006", "ANT-LIM-001"]
    assert out["model_version"] == "m-1"
    assert len(call("predictions-api", "GET /predictions", query={"min_probability": "0.8"})[1]["predictions"]) == 1
    assert len(call("predictions-api", "GET /predictions", query={"zone_id": "LIM-SURCO"})[1]["predictions"]) == 2
    assert len(call("predictions-api", "GET /predictions", query={"antenna_id": "ANT-LIM-006"})[1]["predictions"]) == 1
    assert call("predictions-api", "GET /predictions", query={"min_probability": "x"})[0] == 400


# ---------------- check-preventive ----------------
def test_check_preventive_devuelve_evento_preventiva(cloud):
    chk = load_function("check-preventive")
    p = {**pred("ANT-LIM-001", 0.92), "generated_at": ts(0), "model_version": "m-9"}
    out = chk.handler(p, None)
    assert out["eligible"] and out["event"]["event_type"] == "PREVENTIVA" and out["event"]["severity"] == "ALTA"
    assert out["event"]["prediction"]["model_version"] == "m-9"
    from common import emitter
    emitter.publish(out["event"])
    ddb.update(aws.table("ANTENNAS"), {"antenna_id": "ANT-LIM-001"}, sets={"preventive_until": out["valid_until"]})
    assert chk.handler(p, None)["eligible"] is False
    assert chk.handler({**p, "antenna_id": "ANT-LIM-999"}, None)["reason"] == "antena desconocida"


# ---------------- simulador ----------------
def test_simulador_apagado_no_hace_nada(cloud):
    assert load_function("simulator").handler({}, None) == {"active": False}
    assert ddb.scan(aws.table("TELEMETRY")) == []


def test_simulador_start_escenario_y_falla(cloud):
    code, out, _ = call("simulator-control", "POST /simulator/start", body={})
    assert code == 200 and out["active"] is True
    code, out, _ = call("simulator-control", "POST /simulator/scenario",
                        body={"type": "FALLA_ABRUPTA", "antenna_id": "ANT-LIM-005", "to": "CAIDA"})
    assert code == 200 and out["antennas"] == ["ANT-LIM-005"]
    sim = load_function("simulator")
    for _ in range(3):  # el escenario nace "ahora": dos ticks consecutivos bastan si avanza el reloj
        r = sim.handler({}, None)
    assert r["samples"] == 60 and ddb.scan(aws.table("TELEMETRY"))
    assert call("simulator-control", "GET /simulator/status")[1]["scenarios"]


def test_simulador_control_validaciones_y_permisos(cloud):
    assert call("simulator-control", "POST /simulator/start", "azure", body={})[0] == 403
    for body in ({"type": "RARO"}, {"type": "FALLA_ABRUPTA"}, {"type": "FALLA_ABRUPTA", "antenna_id": "ANT-LIM-999"},
                 {"type": "APAGON_ZONA", "antenna_id": "ANT-LIM-001"}, {"type": "APAGON_ZONA", "zone_id": "LIM-X"},
                 {"type": "DEGRADACION", "antenna_id": "ANT-LIM-001", "to": "NORMAL"},
                 {"type": "DEGRADACION", "antenna_id": "ANT-LIM-001", "ramp_minutes": -1},
                 {"type": "DEGRADACION", "antenna_id": "ANT-LIM-001", "recovery": "X"}):
        assert call("simulator-control", "POST /simulator/scenario", body=body)[0] == 400, body
    code, out, _ = call("simulator-control", "POST /simulator/scenario", body={"type": "APAGON_ZONA", "zone_id": "LIM-ATE"})
    assert code == 200 and len(out["antennas"]) == 5 and "apagado" in out["note"]
    assert call("simulator-control", "POST /simulator/scenario", body={"type": "NORMAL", "zone_id": "LIM-ATE"})[1]["cancelled"] == 1
    assert call("simulator-control", "POST /simulator/stop")[1]["active"] is False


def test_modo_demo_acorta_la_rampa(cloud):
    boto3.client("ssm").put_parameter(Name="/pulsored/config/DEMO_MODE", Value="true", Type="String")
    config.reset_cache()
    call("simulator-control", "POST /simulator/scenario", body={"type": "DEGRADACION", "antenna_id": "ANT-LIM-001"})
    sc = call("simulator-control", "GET /simulator/status")[1]["scenarios"][0]
    assert sc["ramp_minutes"] == 4


# ---------------- mock receiver ----------------
def mock(method, path, key="out-azure", body=None):
    ev = api_event(f"{method} /mock/{{proxy+}}", None, body=body, raw_path=path)
    ev["headers"] = {"x-api-key": key}
    r = load_function("mock-receiver").handler(ev, None)
    return r["statusCode"], json.loads(r["body"])


def test_mock_receiver_idempotente_con_clave_y_modo_caido(cloud):
    env = {"event_id": "33333333-3333-4333-8333-333333333333", "type": "antenna.event",
           "payload": {"event_type": "FALLA", "antenna_id": "ANT-LIM-001", "new_state": "CAIDA"}}
    assert mock("POST", "/mock/azure/events/infra", "mala", env)[0] == 401
    assert mock("POST", "/mock/azure/events/infra", "out-gcp", env)[0] == 401
    assert mock("POST", "/mock/azure/events/infra", body=env) == (202, {"status": "accepted", "event_id": env["event_id"]})
    assert mock("POST", "/mock/azure/events/infra", body=env)[1]["status"] == "duplicate"
    assert mock("POST", "/mock/gcp/ingest/events", "out-gcp", env)[0] == 202
    assert mock("POST", "/mock/azure/otra", body=env)[0] == 404
    rec = mock("GET", "/mock/azure/received", "k-operator")
    assert rec[0] == 200 and rec[1]["count"] == 1 and rec[1]["items"][0]["summary"] == "FALLA ANT-LIM-001 CAIDA"
    assert mock("GET", "/mock/azure/received", "k-gcp")[0] == 401
    assert mock("POST", "/mock/mode", "k-operator", {"fail": True})[0] == 200
    assert mock("POST", "/mock/azure/events/infra", body={**env, "event_id": "44444444-4444-4444-8444-444444444444"})[0] == 503
    assert mock("POST", "/mock/mode", "k-operator", {"fail": False})[0] == 200

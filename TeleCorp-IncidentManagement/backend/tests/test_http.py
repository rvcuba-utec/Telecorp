"""Importa los blueprints (valida decoradores de Azure Functions/Durable) y prueba la capa HTTP."""
import json

import azure.functions as func
from conftest import body, event


def _req(method, url, payload=None, headers=None, params=None, route=None):
    return func.HttpRequest(method, url, headers=headers or {}, params=params or {}, route_params=route or {},
                            body=json.dumps(payload).encode() if payload is not None else b"")


def _fn(f):
    return f.build().get_user_function() if hasattr(f, "build") else f


OP = {"x-operator-token": "tok-test"}


def test_function_app_registers_everything():
    import function_app
    names = {f.get_function_name() for f in function_app.app.get_functions()}
    expected = {"health", "zones", "catalog", "intake_report", "reports_api", "incidents_list", "incidents_detail",
                "infra_events_list", "antennas", "predictions", "settings", "redrive_dlq", "ingest_infra_event",
                "correlator_fn", "infra_linker", "incident_lifecycle", "notifier", "exporter"}
    assert expected <= names, expected - names
    assert {"load_context", "check_infra", "ai_enrich", "compute_priority", "set_status", "enqueue_notifications",
            "enqueue_export", "alert_operator", "check_recovery", "check_new_reports"} <= names


def test_http_intake_and_public_get_with_verification(env):
    from blueprints import http_api as h
    r = _fn(h.intake_report)(_req("POST", "/api/reports", body(1)))
    assert r.status_code == 202
    rid = json.loads(r.get_body())["report_id"]
    g = _fn(h.reports_api)
    assert g(_req("GET", f"/api/reports/{rid}", route={"report_id": rid})).status_code == 404
    ok = g(_req("GET", f"/api/reports/{rid}", route={"report_id": rid}, params={"verify": body(1)["phone"][-4:]}))
    assert ok.status_code == 200 and json.loads(ok.get_body())["status"] == "REGISTRADA"
    assert g(_req("GET", f"/api/reports/{rid}", route={"report_id": rid}, headers=OP)).status_code == 200


def test_http_rate_limit_sets_retry_after(env, monkeypatch):
    from blueprints import http_api as h
    monkeypatch.setenv("RATE_LIMIT_PER_HOUR", "1")
    f = _fn(h.intake_report)
    assert f(_req("POST", "/api/reports", body(1))).status_code == 202
    r = f(_req("POST", "/api/reports", body(1, idempotency_key="otra-clave-123")))
    assert r.status_code == 429 and r.headers.get("Retry-After")


def test_http_operator_endpoints_require_token(env):
    from blueprints import http_api as h
    for fn in (h.incidents_list, h.antennas, h.predictions, h.settings, h.infra_events_list):
        f = _fn(fn)
        assert f(_req("GET", "/api/x")).status_code == 401
        assert f(_req("GET", "/api/x", headers={"x-operator-token": "mal"})).status_code == 401
        assert f(_req("GET", "/api/x", headers=OP)).status_code == 200
    s = json.loads(_fn(h.settings)(_req("GET", "/api/settings", headers=OP)).get_body())
    assert s["USERS_THRESHOLD"] == 10 and s["CORRELATION_MODE"] == "ZONA" and s["source"] == "local"
    a = json.loads(_fn(h.antennas)(_req("GET", "/api/antennas", headers=OP, params={"zone_id": "LIM-SURCO"})).get_body())
    assert a["source"] == "local" and len(a["items"]) == 5


def test_http_bad_json_and_oversize(env):
    from blueprints import http_api as h
    f = _fn(h.intake_report)
    bad = func.HttpRequest("POST", "/api/reports", headers={}, params={}, route_params={}, body=b"{no json")
    assert f(bad).status_code == 400
    big = func.HttpRequest("POST", "/api/reports", headers={}, params={}, route_params={}, body=b"x" * 9000)
    assert f(big).status_code == 413


def test_http_aws_event_requires_x_api_key(env):
    from blueprints import http_api as h
    f = _fn(h.ingest_infra_event)
    e = event()
    assert f(_req("POST", "/api/events/infra", e)).status_code == 401
    assert f(_req("POST", "/api/events/infra", e, headers={"X-API-Key": "mala"})).status_code == 401
    key = {"X-API-Key": "aws-key-test"}
    assert f(_req("POST", "/api/events/infra", e, headers=key)).status_code == 202
    dup = f(_req("POST", "/api/events/infra", e, headers=key))
    assert dup.status_code == 200 and json.loads(dup.get_body())["status"] == "duplicate"
    assert f(_req("POST", "/api/events/infra", {"x": 1}, headers=key)).status_code == 400


def test_catalog_and_zones(env):
    from blueprints import http_api as h
    z = json.loads(_fn(h.zones)(_req("GET", "/api/zones")).get_body())
    assert z["zones_version"] and {x["zone_id"] for x in z["items"]} >= {"LIM-SURCO", "LIM-JESUSMARIA"}
    c = json.loads(_fn(h.catalog)(_req("GET", "/api/catalog")).get_body())
    assert "SIN_INTERNET" in c["category"] and c["status_labels"]["EN_ATENCION"] == "Siendo atendida"

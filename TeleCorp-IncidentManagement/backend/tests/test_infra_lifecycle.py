import json

import pytest
from conftest import ANT, ZONE, ZONE_SALUD, event, send_reports
from pulsored import clock, lifecycle
from pulsored.localrun import run_lifecycle
from pulsored.services import ai, export, incidents, infra, notify


def ingest(env, ev):
    code, out = infra.ingest_event(env.store, ev)
    doc = env.store.get("infra_events", ev["event_id"], ev["payload"]["zone_id"])
    return code, out, doc


def incident(env, n=10, zone=ZONE):
    return send_reports(env.store, n, zone=zone)[0]


# ------------------------------------------------------------ eventos de AWS (contrato)
def test_infra_event_idempotent_5_times(env):
    e = event()
    assert [infra.ingest_event(env.store, e)[0] for _ in range(5)] == [202, 200, 200, 200, 200]
    assert len(env.store.data["infra_events"]) == 1


def test_all_seven_event_types_accepted(env):
    extra = {"PREVENTIVA": {"prediction": {"probability": 0.82, "predicted_state": "CAIDA", "horizon_minutes": 60,
                                           "valid_until": "2026-11-15T18:00:00Z", "model_version": "bqml-1"}},
             "ESTRUCTURAL": {"recurrence": {"episodes_7d": 3, "episodes_30d": 5}},
             "PROLONGADA": {"elapsed_minutes": 95}}
    for t in ("FALLA", "SATURACION", "DEGRADACION", "RECUPERACION", "PREVENTIVA", "ESTRUCTURAL", "PROLONGADA"):
        assert infra.ingest_event(env.store, event(t, **extra.get(t, {})))[0] == 202, t


def test_infra_event_validation(env):
    assert infra.ingest_event(env.store, event("ANTENNA_DOWN"))[0] == 400                     # tipo viejo
    assert infra.ingest_event(env.store, event(zone="Z07"))[0] == 400                           # zona fuera del catálogo
    assert infra.ingest_event(env.store, event(ant="A-0123"))[0] == 400                         # patrón ANT-LIM-###
    assert infra.ingest_event(env.store, {**event(), "type": "telemetry.batch"})[0] == 400
    flat = {"event_id": "x", "event_type": "FALLA", "antenna_id": ANT, "zone_id": ZONE}         # sin sobre común
    assert infra.ingest_event(env.store, flat)[0] == 400


def test_infra_event_raises_priority_and_signals(env):
    inc = incident(env)
    before = env.store.get("incidents", inc["id"], ZONE)["priority_score"]
    _, _, doc = ingest(env, event())
    signals = infra.link_event(env.store, doc, env.queue)
    after = env.store.get("incidents", inc["id"], ZONE)
    assert after["priority_score"] > before
    assert after["detected_by"] == "AMBOS" and after["antenna_ids"] == [ANT]
    assert signals == [inc["id"]]
    hist = [h["action"] for h in env.store.list_history(inc["id"])]
    assert "INFRA_EVENT" in hist and "PRIORITY_CHANGED" in hist
    assert any(m["type"] == "incident.upsert" for m in env.queue.drain("analytics-export"))


def test_preventive_creates_own_incident_without_notifications(env):
    pr = {"probability": 0.91, "predicted_state": "CAIDA", "horizon_minutes": 60,
          "valid_until": "2026-11-15T18:00:00Z", "model_version": "bqml-1"}
    _, _, doc = ingest(env, event("PREVENTIVA", new_state="NORMAL", prediction=pr))
    assert infra.link_event(env.store, doc, env.queue) == []
    incs = list(env.store.data["incidents"].values())
    assert len(incs) == 1 and incs[0]["incident_type"] == "PREVENTIVA" and incs[0]["antenna_ids"] == [ANT]
    assert notify.enqueue_notifications(env.store, env.queue, incs[0]["id"], ZONE, "EVALUANDO") == 0
    _, _, doc2 = ingest(env, event("PREVENTIVA", new_state="NORMAL", prediction=pr))     # segunda alerta: misma incidencia
    infra.link_event(env.store, doc2, env.queue)
    assert len(env.store.data["incidents"]) == 1
    assert infra.predictions_view(env.store, ZONE)[0]["probability"] == 0.91


def test_structural_creates_incident_and_feeds_recurrence(env):
    _, _, doc = ingest(env, event("ESTRUCTURAL", recurrence={"episodes_7d": 3, "episodes_30d": 5}))
    infra.link_event(env.store, doc, env.queue)
    inc = next(iter(env.store.data["incidents"].values()))
    assert inc["incident_type"] == "ESTRUCTURAL" and inc["priority_factors"]["R"] == 1.0


def test_collective_incident_not_merged_with_preventive(env):
    _, _, doc = ingest(env, event("PREVENTIVA", new_state="NORMAL"))
    infra.link_event(env.store, doc)
    created = send_reports(env.store, 10)
    assert len(created) == 1 and created[0]["incident_type"] == "COLECTIVA"


def test_detection_lag_positive_when_antenna_first(env):
    _, _, doc = ingest(env, event())
    clock.advance(minutes=4)
    inc = incident(env)
    assert env.store.get("incidents", inc["id"], ZONE)["detection_lag_s"] > 0


def test_antenna_projection(env):
    ingest(env, event(new_state="INHABILITADA"))
    view = {a["antenna_id"]: a for a in infra.antenna_view(env.store, ZONE)}
    assert view[ANT]["state"] == "INHABILITADA" and view["ANT-LIM-002"]["state"] == "NORMAL"
    clock.advance(minutes=1)
    ingest(env, event("RECUPERACION"))
    assert {a["antenna_id"]: a for a in infra.antenna_view(env.store, ZONE)}[ANT]["state"] == "NORMAL"


# ------------------------------------------------------------ ciclo de vida
def test_lifecycle_happy_path_with_infra_confirmation(env):
    ingest(env, event())
    inc = incident(env)
    result, steps = run_lifecycle(inc)
    assert result["outcome"] == "CERRADA"
    final = env.store.get("incidents", inc["id"], ZONE)
    assert final["status"] == "CERRADA" and final["closed_at"] and final["resolved_at"]
    assert final["ai_source"] == "reglas" and final["ai"]["summary"] and final["ai"]["probable_cause"]
    assert "check_infra" in steps and "enqueue_notifications" in steps


def test_lifecycle_recovers_when_aws_reports_recovered(env):
    ingest(env, event())
    inc = incident(env)
    polls = {"n": 0}

    def on_step(name, inp, res):
        if name == "check_recovery":
            polls["n"] += 1
            if polls["n"] == 2:
                ingest(env, event("RECUPERACION"))
            if polls["n"] <= 2:
                assert res is False
    start = clock.now()
    result, _ = run_lifecycle(inc, on_step=on_step)
    assert result["outcome"] == "CERRADA"
    assert (clock.now() - start).total_seconds() < 6 * 3600


def test_lifecycle_reopens_on_new_reports_during_cooldown(env):
    ingest(env, event())
    inc = incident(env)
    state = {"reopened": False, "recovered": False}

    def on_step(name, inp, res):
        if name == "check_recovery" and not state["recovered"]:
            state["recovered"] = True
            ingest(env, event("RECUPERACION"))
        if name == "enqueue_notifications" and inp["stage"] == "RESTABLECIDO" and not state["reopened"]:
            state["reopened"] = True
            clock.advance(minutes=1)
            send_reports(env.store, 1, start=500)

    result, steps = run_lifecycle(inc, events=["CONFIRMAR"], on_step=on_step)
    assert result["outcome"] == "CERRADA"
    assert steps.count("enqueue_notifications") == 6          # 3 etapas x 2 ciclos
    final = env.store.get("incidents", inc["id"], ZONE)
    assert final["users_affected"] == 11
    assert [h["to"] for h in env.store.list_history(inc["id"]) if h["action"] == "STATUS_CHANGED"].count("EN_REVISION") == 2


def test_lifecycle_waits_for_operator_and_discard(env):
    inc = incident(env)
    result, _ = run_lifecycle(inc, events=["DESCARTAR"])
    assert result["outcome"] == "FALSO_POSITIVO"
    final = env.store.get("incidents", inc["id"], ZONE)
    assert final["status"] == "CERRADA" and final["close_reason"] == "FALSO_POSITIVO"


def test_lifecycle_operator_confirm_then_recovery(env):
    inc = incident(env)
    result, _ = run_lifecycle(inc, events=["CONFIRMAR"])
    assert result["outcome"] == "CERRADA"
    assert [x["action"] for x in env.store.list_history(inc["id"])].count("STATUS_CHANGED") >= 4


def test_lifecycle_operator_timeout_escalates(env):
    inc = incident(env)
    seen = []
    with pytest.raises(RuntimeError):
        run_lifecycle(inc, events=[], on_step=lambda n, i, r: seen.append(n))
    assert "alert_operator" in seen


# ------------------------------------------------------------ notificaciones y exportación
def test_notifications_three_stages_one_per_user(env):
    ingest(env, event())
    inc = incident(env)
    run_lifecycle(inc)
    msgs = env.queue.messages["notifications"]
    assert {m["stage"] for m in msgs} == {"EVALUANDO", "IDENTIFICADA", "RESTABLECIDO"}
    assert len(msgs) == 30
    assert all("@" not in json.dumps(m) and "phone" not in m and "email" not in m for m in msgs)


def test_notifier_is_idempotent_and_visible_in_history(env):
    inc = incident(env)
    notify.enqueue_notifications(env.store, env.queue, inc["id"], ZONE, "EVALUANDO")
    msgs = env.queue.drain("notifications")
    for m in msgs:
        assert notify.deliver(env.store, env.sender, m) == "sent"
    for m in msgs:
        assert notify.deliver(env.store, env.sender, m) == "duplicate"
    assert len([s for s in env.sender.sent if s[0] == "EMAIL_SIMULATED"]) == 10
    h = env.store.list_history(msgs[0]["report_id"])
    assert [x for x in h if x["action"] == "NOTIFICATION_SENT"][0]["note"].startswith("Evaluando")


def test_export_envelope_has_no_pii(env):
    inc = incident(env)
    export.enqueue_export(env.store, env.queue, inc["id"], ZONE)
    msg = env.queue.drain("analytics-export")[-1]
    assert msg["type"] == "incident.upsert" and msg["source"] == "azure" and msg["schema_version"] == "1.0"
    blob = json.dumps(msg)
    for pii in ("DOC0", "+51999", "@test.pe", "user_key", "description"):
        assert pii not in blob
    assert msg["payload"]["users_affected"] == 10 and msg["payload"]["incident_id"] == inc["id"]
    again = export.incident_message(env.store.get("incidents", inc["id"], ZONE))
    assert again["event_id"] == msg["event_id"]                 # determinista: reintento sin duplicar
    assert export.send_to_gcp(msg) == "mock"


def test_export_auth_error_is_retried_not_dropped(env, monkeypatch):
    """Sin API key válida el Gateway responde 401: el mensaje debe ir a reintento/DLQ, no descartarse."""
    import pytest

    class Resp:
        def __init__(self, code):
            self.status_code, self.text = code, "x"

        def raise_for_status(self):
            pass

    monkeypatch.setenv("GCP_API_BASE", "https://gcp.test/v1")
    msg = export.incident_message(incident(env))
    for code in (401, 403):
        monkeypatch.setattr(export.requests, "post", lambda *a, c=code, **k: Resp(c))
        with pytest.raises(RuntimeError):
            export.send_to_gcp(msg)
    monkeypatch.setattr(export.requests, "post", lambda *a, **k: Resp(400))
    assert export.send_to_gcp(msg) == "rejected"
    monkeypatch.setattr(export.requests, "post", lambda *a, **k: Resp(200))
    assert export.send_to_gcp(msg) == "sent"


def test_gcp_down_falls_back_to_rules(env, monkeypatch):
    monkeypatch.setenv("GCP_API_BASE", "http://127.0.0.1:9")
    monkeypatch.setenv("GCP_API_KEY", "k")
    inc = incident(env)
    out = lifecycle.ai_enrich({"incident_id": inc["id"], "zone_id": ZONE})
    assert out["ai_source"] == "reglas"
    r = next(iter(env.store.data["reports"].values()))
    assert r["ai"]["source"] == "reglas"


def test_gcp_ai_contract(env, monkeypatch):
    """Simula GCP: verifica las rutas, el header y que no viajan datos personales."""
    calls = []

    class Resp:
        def __init__(self, data):
            self.status_code, self._d = 200, data

        def json(self):
            return self._d

        def raise_for_status(self):
            pass

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append((url, json, headers, timeout))
        if url.endswith("/ai/classify-report"):
            return Resp({"report_id": json["report_id"], "category": "SIN_SENAL", "urgency": "ALTA",
                         "critical_site": {"mentioned": True, "kind": "COLEGIO"}, "confidence": 0.9,
                         "model_version": "gemini-x", "source": "gemini"})
        return Resp({"summary": "S", "probable_cause": "C", "recommended_action": "A", "model_version": "gemini-x"})

    monkeypatch.setenv("GCP_API_BASE", "https://gcp.test")
    monkeypatch.setenv("GCP_API_KEY", "gcp-key")
    monkeypatch.setattr(ai.requests, "post", fake_post)
    inc = send_reports(env.store, 10, description="sin señal, llamen al 987654321 o a yo@mail.com")[0]
    lifecycle.ai_enrich({"incident_id": inc["id"], "zone_id": ZONE})
    final = env.store.get("incidents", inc["id"], ZONE)
    assert final["ai_source"] == "gemini" and final["ai"]["probable_cause"] == "C"
    r = next(iter(env.store.data["reports"].values()))
    assert r["ai"]["category"] == "SIN_SENAL" and r["ai"]["source"] == "gemini"
    urls = {c[0] for c in calls}
    assert urls == {"https://gcp.test/ai/classify-report", "https://gcp.test/ai/summarize-incident"}
    assert all(c[2]["X-API-Key"] == "gcp-key" for c in calls)
    assert {c[3] for c in calls if c[0].endswith("classify-report")} == {4.0}
    blob = json.dumps([c[1] for c in calls])
    for pii in ("DOC0", "+5199900", "987654321", "yo@mail.com", "@test.pe"):
        assert pii not in blob


# ------------------------------------------------------------ consola
def test_operator_actions_and_invalid_transition(env):
    inc = incident(env)
    iid = inc["id"]
    assert incidents.apply_action(env.store, iid, {"action": "CAMBIAR_ESTADO", "to": "SOLUCIONADA"})[0] == 409
    assert any(h["action"] == "TRANSITION_REJECTED" for h in env.store.list_history(iid))
    code, _, sig = incidents.apply_action(env.store, iid, {"action": "CONFIRMAR"})
    assert (code, sig) == (202, "CONFIRMAR")
    assert incidents.apply_action(env.store, iid, {"action": "???"})[0] == 400
    assert incidents.get_incident(env.store, f"INC-{ZONE}-1")[0] == 404
    code, detail = incidents.get_incident(env.store, iid)
    assert code == 200 and "user_document" not in json.dumps(detail) and "phone" not in json.dumps(detail)


def test_operator_priority_override(env):
    inc = incident(env)
    code, _, _ = incidents.apply_action(env.store, inc["id"], {"action": "CAMBIAR_PRIORIDAD", "level": "CRITICA"},
                                        queue=env.queue)
    assert code == 200
    assert env.store.get("incidents", inc["id"], ZONE)["priority_level"] == "CRITICA"
    incidents.apply_action(env.store, inc["id"], {"action": "CAMBIAR_PRIORIDAD", "level": ""})
    assert env.store.get("incidents", inc["id"], ZONE)["priority_override"] is None


def test_incident_list_sorted_and_filtered(env):
    send_reports(env.store, 10, zone=ZONE)                     # residencial
    send_reports(env.store, 10, zone=ZONE_SALUD, start=100)    # salud
    items = incidents.list_incidents(env.store)[1]["items"]
    assert [i["zone_id"] for i in items] == [ZONE_SALUD, ZONE]
    assert incidents.list_incidents(env.store, incident_type="PREVENTIVA")[1]["items"] == []
    lvl = items[0]["priority_level"]
    assert all(i["priority_level"] == lvl for i in incidents.list_incidents(env.store, priority_level=lvl)[1]["items"])

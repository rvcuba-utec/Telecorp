import json

from conftest import ZONE, body, send_reports
from pulsored import clock, domain
from pulsored.services import correlator, intake, reports


def test_validation_rejects_bad_input(env):
    for bad in (
        body(1, category="SIN_DATOS"),          # categoría fuera de enums.json
        body(1, zone_id="Z07"),                 # zona fuera del catálogo
        body(1, phone="abc"),
        body(1, description="x" * 1001),
        {**body(1), "extra": 1},
    ):
        assert intake.register_report(env.store, bad)[0] == 400, bad
    b = body(1)
    del b["description"]
    assert intake.register_report(env.store, b)[0] == 400
    assert intake.register_report(env.store, "no-objeto")[0] == 400
    b = body(1)
    del b["zone_id"]                            # sin zona ni coordenadas
    assert intake.register_report(env.store, b)[0] == 400


def test_coordinates_resolve_zone(env):
    c = domain.ZONES["LIM-JESUSMARIA"]["centroid"]
    b = body(1)
    del b["zone_id"]
    b["location"] = {"lat": c["lat"], "lng": c["lng"]}
    code, out = intake.register_report(env.store, b)
    assert code == 202 and out["zone_id"] == "LIM-JESUSMARIA" and out["report_id"].startswith("REP-LIM-JESUSMARIA-")


def test_idempotent_intake_same_key_5_times(env):
    ids, codes = set(), []
    for _ in range(5):
        code, out = intake.register_report(env.store, body(1))
        codes.append(code)
        ids.add(out["report_id"])
    assert codes == [202, 200, 200, 200, 200]
    assert len(ids) == 1 and len(env.store.data["reports"]) == 1
    assert next(iter(ids)).startswith(f"REP-{ZONE}-")


def test_rate_limit_per_user(env, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_HOUR", "3")
    codes = [intake.register_report(env.store, body(1, idempotency_key=f"rate-key-{i:04d}"))[0] for i in range(4)]
    assert codes == [202, 202, 202, 429]
    clock.advance(hours=1, minutes=1)
    assert intake.register_report(env.store, body(1, idempotency_key="rate-key-9999"))[0] == 202


def test_nine_users_no_incident(env):
    assert send_reports(env.store, 9) == []
    assert env.store.data["incidents"] == {}


def test_ten_reports_same_user_no_incident(env):
    for i in range(10):
        code, out = intake.register_report(env.store, body(0, idempotency_key=f"idem-same-user-{i:04d}"))
        assert code == 202
        assert correlator.process_report(env.store, env.store.get("reports", out["report_id"], ZONE)) is None


def test_ten_users_two_zones_no_incident(env):
    send_reports(env.store, 5, zone=ZONE)
    assert send_reports(env.store, 5, zone="LIM-MIRAFLORES", start=100) == []


def test_ten_users_open_exactly_one_incident(env):
    created = send_reports(env.store, 10, queue=env.queue)
    assert len(created) == 1
    inc = created[0]
    assert inc["users_affected"] == 10 and inc["status"] == "REGISTRADA" and inc["incident_type"] == "COLECTIVA"
    assert inc["id"].startswith(f"INC-{ZONE}-") and inc["report_count"] == 10
    assert all(r["incident_id"] == inc["id"] for r in env.store.data["reports"].values())


def test_any_category_counts_in_zone_mode(env):
    """Decisión del equipo: misma zona + tiempo, cualquier categoría."""
    send_reports(env.store, 5, category="SIN_INTERNET")
    assert len(send_reports(env.store, 5, start=50, category="LLAMADAS")) == 1


def test_category_mode_does_not_mix(env, monkeypatch):
    monkeypatch.setenv("CORRELATION_MODE", "ZONA_Y_CATEGORIA")
    send_reports(env.store, 5, category="SIN_INTERNET")
    assert send_reports(env.store, 5, start=50, category="LLAMADAS") == []


def test_eleventh_user_links_to_open_incident(env):
    send_reports(env.store, 10)
    assert send_reports(env.store, 1, start=10) == []
    assert len(env.store.data["incidents"]) == 1
    assert next(iter(env.store.data["incidents"].values()))["users_affected"] == 11


def test_reprocessing_same_report_is_idempotent(env):
    send_reports(env.store, 10)
    inc = next(iter(env.store.data["incidents"].values()))
    for r in list(env.store.data["reports"].values()):
        assert correlator.process_report(env.store, r) is None
    assert len(env.store.data["incidents"]) == 1
    assert env.store.count_incident_users(inc["id"]) == 10


def test_window_24h_sliding(env):
    send_reports(env.store, 9)
    clock.advance(hours=24, minutes=1)
    assert send_reports(env.store, 1, start=50) == []
    assert env.store.data["incidents"] == {}


def test_ten_users_within_23h59_make_incident(env):
    send_reports(env.store, 5)
    clock.advance(hours=23, minutes=50)
    assert len(send_reports(env.store, 5, start=50)) == 1


def test_classification_rules_and_report_export(env):
    send_reports(env.store, 1, description="sin señal al lado del hospital", queue=env.queue)
    r = next(iter(env.store.data["reports"].values()))
    assert r["ai"]["source"] == "reglas" and r["ai"]["critical_site"] == {"mentioned": True, "kind": "HOSPITAL"}
    assert r["ai"]["urgency"] == "ALTA"
    msgs = env.queue.drain("analytics-export")
    assert msgs and msgs[0]["type"] == "report.upsert" and msgs[0]["source"] == "azure"
    blob = json.dumps(msgs)
    for pii in ("DOC0", "+51999", "@test.pe", "hospital"):
        assert pii not in blob
    assert msgs[0]["payload"]["user_key"] == r["user_key"]


def test_public_report_requires_verification_and_hides_pii(env):
    send_reports(env.store, 10)
    r = next(iter(env.store.data["reports"].values()))
    assert reports.get_public_report(env.store, r["id"])[0] == 404               # sin dato de verificación
    assert reports.get_public_report(env.store, r["id"], "0000x")[0] == 404
    code, out = reports.get_public_report(env.store, r["id"], r["phone"][-4:])
    assert code == 200 and out["incident"]["status"] == "REGISTRADA" and out["status_label"] == "Registrada"
    blob = str(out)
    for pii in ("DOC0", "+51999", "@test.pe", "user_key"):
        assert pii not in blob
    assert reports.get_public_report(env.store, f"REP-{ZONE}-NOEXISTE", "1234")[0] == 404


def test_report_status_transitions(env):
    _, out = intake.register_report(env.store, body(1))
    rid = out["report_id"]
    assert reports.change_report_status(env.store, rid, "SOLUCIONADA", "x")[0] == 409
    assert any(h["action"] == "TRANSITION_REJECTED" for h in env.store.list_history(rid))
    assert reports.change_report_status(env.store, rid, "EN_REVISION", "ok")[0] == 200


def test_orphan_incident_is_resumed(env):
    """Si el correlador falló tras crear la incidencia (sin pedir la orquestación), el siguiente reporte la retoma."""
    inc = send_reports(env.store, 10)[0]
    doc = env.store.get("incidents", inc["id"], ZONE)
    assert doc["lifecycle_requested"] is True
    doc["lifecycle_requested"] = False                        # simula el fallo antes de arrancar la orquestación
    env.store.upsert("incidents", doc)
    resumed = send_reports(env.store, 1, start=10)
    assert len(resumed) == 1 and resumed[0]["id"] == inc["id"] and resumed[0]["users_affected"] == 11
    assert send_reports(env.store, 1, start=11) == []          # ya pedida: no se vuelve a arrancar

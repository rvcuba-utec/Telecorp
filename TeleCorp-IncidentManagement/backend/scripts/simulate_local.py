"""Demo end-to-end 100 % local (memoria, sin Azure):  python scripts/simulate_local.py
Escenario: AWS informa una caída en LIM-SURCO -> 10 usuarios reportan -> incidencia colectiva -> IA (reglas)
-> orquestación -> 3 notificaciones -> exportación a GCP (mock). Además: alerta PREVENTIVA de otra antena."""
import json
import os
import sys
import uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["PULSORED_NO_DOTENV"] = "1"
for k in ("COSMOS", "SB", "ACS_CONNECTION_STRING", "GCP_API_BASE", "AWS_API_BASE"):
    os.environ.pop(k, None)

from pulsored import clock, messaging, queueing, store as store_mod  # noqa: E402
from pulsored.localrun import run_lifecycle  # noqa: E402
from pulsored.services import correlator, export, infra, intake, notify  # noqa: E402

ZONE, ANT = "LIM-SURCO", "ANT-LIM-001"
clock.set_fixed(datetime(2026, 11, 15, 14, 0, tzinfo=timezone.utc))
st, q, snd = store_mod.MemoryStore(), queueing.MemoryQueue(), messaging.SimulatedSender()
store_mod.set_store(st), queueing.set_queue(q), messaging.set_sender(snd)


def step(t):
    print(f"\n== {t}")


def aws_event(event_type, new_state, ant=ANT, **extra):
    now = clock.iso()
    p = {"antenna_id": ant, "zone_id": ZONE, "episode_id": f"EP-{ant}-1", "event_type": event_type,
         "severity": "INFO" if event_type == "RECUPERACION" else "ALTA", "previous_state": "NORMAL",
         "new_state": new_state, "gb_per_min": 41.2, "first_anomaly_at": now, "detected_at": now, **extra}
    ev = {"event_id": str(uuid.uuid4()), "type": "antenna.event", "schema_version": "1.0", "source": "aws",
          "occurred_at": now, "correlation_id": "demo", "payload": p}
    code, out = infra.ingest_event(st, ev)
    infra.link_event(st, st.get("infra_events", ev["event_id"], ZONE), q)
    return code, out


step(f"AWS informa: {ANT} CAIDA en {ZONE} (POST /api/events/infra)")
print(aws_event("FALLA", "CAIDA"))
clock.advance(minutes=4)

step(f"10 usuarios distintos reportan en {ZONE} (POST /api/reports)")
incident = None
for i in range(10):
    _, out = intake.register_report(st, {
        "idempotency_key": f"demo-{i:04d}-key", "user_document": f"DOC{i:05d}", "phone": f"+5199900{i:04d}",
        "email": f"usuario{i}@correo.pe", "zone_id": ZONE, "occurred_at": clock.iso(),
        "category": "SIN_INTERNET" if i % 2 else "LLAMADAS",
        "description": "No tengo internet desde hace una hora" + (" y el colegio de al lado tampoco" if i == 3 else "")})
    incident = correlator.process_report(st, st.get("reports", out["report_id"], ZONE), q) or incident
    clock.advance(seconds=5)
print("incidencia:", incident["id"], "| usuarios:", incident["users_affected"])

step("Orquestación (Durable) ejecutada localmente")
polls = {"n": 0}


def on_step(name, inp, res):
    if name == "check_recovery":
        polls["n"] += 1
        if polls["n"] == 3:
            print("  ... AWS informa RECUPERACION")
            aws_event("RECUPERACION", "NORMAL", previous_state="CAIDA")
        if polls["n"] > 1:
            return
    print(f"  {name:<22} -> {json.dumps(res, default=str)[:90]}")


result, steps = run_lifecycle(incident, on_step=on_step)
print("resultado:", result)

inc = st.get("incidents", incident["id"], ZONE)
step("Estado final")
print(f'estado={inc["status"]} prioridad={inc["priority_score"]} ({inc["priority_level"]}) detectado_por={inc["detected_by"]} '
      f'ia={inc["ai_source"]} antenas={inc["antenna_ids"]} detection_lag_s={inc["detection_lag_s"]}')
print("factores:", inc["priority_factors"])
print("resumen IA:", inc["ai"]["summary"], "| causa:", inc["ai"]["probable_cause"])

step("Notificador (cola -> email/SMS simulados)")
for m in q.drain("notifications"):
    notify.deliver(st, snd, m)
print("envíos simulados:", len(snd.sent), "| ejemplo:", snd.sent[0][:3])

step("AWS informa: alerta PREVENTIVA en ANT-LIM-002 -> incidencia PREVENTIVA (sin avisos a usuarios)")
aws_event("PREVENTIVA", "NORMAL", ant="ANT-LIM-002",
          prediction={"probability": 0.86, "predicted_state": "CAIDA", "horizon_minutes": 60,
                      "valid_until": clock.iso(clock.now().replace(hour=23)), "model_version": "bqml-demo"})
for i in st.list_incidents(include_closed=True):
    print(f'  {i["id"]:<55} {i["incident_type"]:<11} {i["status"]:<11} {i["priority_level"]}')

step("Exportador a GCP (sobre común, sin datos personales)")
msgs = q.drain("analytics-export")
types = {}
for m in msgs:
    types[m["type"]] = types.get(m["type"], 0) + 1
print("mensajes:", types)
print(json.dumps([m for m in msgs if m["type"] == "incident.upsert"][-1], indent=1, ensure_ascii=False)[:900])
print("entrega:", export.send_to_gcp(msgs[-1]))

step("Historial de la incidencia")
for h in st.list_history(incident["id"]):
    print(f'  {h["ts"][11:19]} {h["actor"]:<8} {h["action"]:<20} {h["from"]}->{h["to"]} {h["note"]}')

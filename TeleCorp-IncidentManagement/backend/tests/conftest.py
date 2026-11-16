import os
import sys
import uuid
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ["PULSORED_NO_DOTENV"] = "1"   # las pruebas no leen el .env del desarrollador
for k in ("COSMOS", "SB", "ACS_CONNECTION_STRING", "ACS_SENDER", "GCP_API_BASE", "GCP_API_KEY", "AWS_API_BASE",
          "AWS_API_KEY", "CORRELATION_MODE", "USERS_THRESHOLD", "RATE_LIMIT_PER_HOUR"):
    os.environ.pop(k, None)
os.environ["OPERATOR_TOKEN"] = "tok-test"
os.environ["AZURE_INBOUND_API_KEY"] = "aws-key-test"

from pulsored import clock, config, messaging, queueing, store as store_mod  # noqa: E402
from pulsored.services import correlator, intake  # noqa: E402

ZONE = "LIM-SURCO"            # RESIDENCIAL
ZONE_SALUD = "LIM-JESUSMARIA"  # SALUD
ANT = "ANT-LIM-001"            # antena de LIM-SURCO


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_HOUR", "1000")
    config.reset_cache()
    clock.set_fixed(datetime(2026, 11, 15, 14, 0, tzinfo=timezone.utc))
    s, q, snd = store_mod.MemoryStore(), queueing.MemoryQueue(), messaging.SimulatedSender()
    store_mod.set_store(s)
    queueing.set_queue(q)
    messaging.set_sender(snd)
    yield type("Env", (), {"store": s, "queue": q, "sender": snd})
    clock.set_fixed(None)


def body(i, zone=ZONE, category="SIN_INTERNET", **kw):
    b = {
        "idempotency_key": f"idem-{zone}-{i:04d}-xx", "user_document": f"DOC{i:05d}", "phone": f"+5199900{i:04d}",
        "email": f"u{i}@test.pe", "zone_id": zone, "occurred_at": clock.iso(), "category": category,
        "description": f"Sin internet desde hace una hora ({i})",
    }
    b.update(kw)
    return b


def send_reports(st, n, zone=ZONE, start=0, category="SIN_INTERNET", queue=None, **kw):
    """Registra n reportes de n usuarios distintos y pasa cada uno por el correlador (como el change feed)."""
    created = []
    for i in range(start, start + n):
        code, out = intake.register_report(st, body(i, zone, category, **kw))
        assert code == 202, out
        r = st.get("reports", out["report_id"], zone)
        inc = correlator.process_report(st, r, queue)
        if inc:
            created.append(inc)
        clock.advance(seconds=1)
    return created


def event(event_type="FALLA", zone=ZONE, ant=ANT, new_state="CAIDA", event_id=None, **payload):
    """Sobre común antenna.event tal como lo envía AWS (contracts/events)."""
    now = clock.iso()
    p = {"antenna_id": ant, "zone_id": zone, "episode_id": f"EP-{ant}-1", "event_type": event_type,
         "severity": "ALTA", "previous_state": "NORMAL", "new_state": new_state, "gb_per_min": 41.2,
         "first_anomaly_at": now, "detected_at": now}
    if event_type == "RECUPERACION":
        p.update(previous_state=new_state if new_state != "NORMAL" else "CAIDA", new_state="NORMAL", severity="INFO")
    p.update(payload)
    return {"event_id": event_id or str(uuid.uuid4()), "type": "antenna.event", "schema_version": "1.0",
            "source": "aws", "occurred_at": now, "correlation_id": "corr-test", "payload": p}

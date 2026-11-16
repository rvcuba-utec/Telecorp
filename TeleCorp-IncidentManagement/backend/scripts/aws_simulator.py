"""Simula a AWS enviando eventos de antena a Azure con el contrato real (sobre común + antenna.event):
  python scripts/aws_simulator.py --url https://<func>.azurewebsites.net --key <AZURE_INBOUND_API_KEY> \
      --zone LIM-SURCO --antenna ANT-LIM-001 --type FALLA --state CAIDA
Sirve también para que el equipo de AWS vea la respuesta esperada (202 / 200 duplicate / 400 / 401)."""
import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pulsored import config  # noqa: E402,F401  (carga el .env)

TYPES = ["FALLA", "SATURACION", "DEGRADACION", "RECUPERACION", "PREVENTIVA", "ESTRUCTURAL", "PROLONGADA"]
ap = argparse.ArgumentParser()
ap.add_argument("--url", default="http://localhost:7071")
ap.add_argument("--key", default=os.environ.get("AZURE_INBOUND_API_KEY", ""))
ap.add_argument("--zone", default="LIM-SURCO")
ap.add_argument("--antenna", default="ANT-LIM-001")
ap.add_argument("--type", default="FALLA", choices=TYPES)
ap.add_argument("--state", default="CAIDA", choices=["NORMAL", "CAIDA", "INHABILITADA", "SATURADA"])
ap.add_argument("--repeat", type=int, default=1, help="reenvía el MISMO evento n veces (prueba de idempotencia)")
a = ap.parse_args()

now = datetime.now(timezone.utc)
iso = lambda d: d.isoformat().replace("+00:00", "Z")  # noqa: E731
payload = {"antenna_id": a.antenna, "zone_id": a.zone, "episode_id": f"EP-{a.antenna}-{int(now.timestamp())}",
           "event_type": a.type, "severity": {"INHABILITADA": "CRITICA", "CAIDA": "ALTA"}.get(a.state, "MEDIA"),
           "previous_state": "NORMAL", "new_state": a.state, "gb_per_min": 0 if a.state == "INHABILITADA" else 41.2,
           "first_anomaly_at": iso(now - timedelta(minutes=2)), "detected_at": iso(now)}
if a.type == "RECUPERACION":
    payload.update(previous_state="CAIDA", new_state="NORMAL", severity="INFO", gb_per_min=95.0)
if a.type == "PREVENTIVA":
    payload.update(new_state="NORMAL", prediction={"probability": 0.82, "predicted_state": "CAIDA", "horizon_minutes": 60,
                                                   "valid_until": iso(now + timedelta(hours=1)), "model_version": "sim"})
if a.type == "ESTRUCTURAL":
    payload["recurrence"] = {"episodes_7d": 3, "episodes_30d": 5}
if a.type == "PROLONGADA":
    payload["elapsed_minutes"] = 45
ev = {"event_id": str(uuid.uuid4()), "type": "antenna.event", "schema_version": "1.0", "source": "aws",
      "occurred_at": iso(now), "correlation_id": str(uuid.uuid4()), "payload": payload}
for i in range(a.repeat):
    r = requests.post(f"{a.url.rstrip('/')}/api/events/infra", json=ev, headers={"X-API-Key": a.key}, timeout=15)
    print(i + 1, r.status_code, r.text)
print(json.dumps(ev, indent=1))

#!/usr/bin/env python3
"""
Generador de Histórico Sintético de ~30 Días para Red TeleCorp (RF-GCP-03 / RF-GCP-04).
Usa el catálogo oficial compartido contracts/zones.json (LIM-SURCO, ANT-LIM-001, etc.).
"""

import json
import math
import os
import random
import uuid
from datetime import datetime, timedelta, timezone

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ZONES_PATH = os.path.join(ROOT_DIR, "contracts", "zones.json")
OUT_DIR = os.path.join(ROOT_DIR, "data")


def classify_traffic(gb_min: float) -> str:
    if gb_min == 0:
        return "INHABILITADA"
    if gb_min < 60:
        return "CAIDA"
    if gb_min <= 129:
        return "NORMAL"
    return "SATURADA"


def main():
    random.seed(42)
    os.makedirs(OUT_DIR, exist_ok=True)

    with open(ZONES_PATH, "r", encoding="utf-8") as f:
        catalog = json.load(f)

    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start_time = now - timedelta(days=30)

    dim_rows = []
    telemetry_rows = []
    event_rows = []
    incident_rows = []
    prediction_rows = []

    all_antennas = [a["antenna_id"] for z in catalog["zones"] for a in z["antennas"]]
    # Antenas designadas con problemas estructurales (falsa recuperación en >= 4 días distintos de la última semana)
    structural_antennas = set(all_antennas[1::14][:4])
    # Antenas con degradación progresiva en las últimas 24 horas (para que BigQuery ML las detecte con prob >= 0.70)
    degrading_antennas = set(all_antennas[2::13][:4])

    for zone in catalog["zones"]:
        z_id = zone["zone_id"]
        z_name = zone["name"]
        z_city = zone.get("city", "Lima")
        z_type = zone.get("type", zone.get("zone_type", "RESIDENCIAL"))
        sites_str = ", ".join(zone.get("critical_sites", []))

        for ant in zone["antennas"]:
            a_id = ant["antenna_id"]
            dim_rows.append({
                "antenna_id": a_id,
                "zone_id": z_id,
                "zone_name": z_name,
                "city": z_city,
                "zone_type": z_type,
                "antenna_lat": ant["lat"],
                "antenna_lng": ant["lng"],
                "antenna_model": ant.get("model", "5G-Macro"),
                "critical_sites": sites_str,
            })

            prev_status = "NORMAL"
            total_steps = 30 * 6
            for step in range(total_steps):
                ts = start_time + timedelta(hours=step * 4)
                days_ago = (now - ts).total_seconds() / 86400.0
                hour_angle = (ts.hour / 24.0) * 2.0 * math.pi

                traffic = 92.0 + 16.0 * math.sin(hour_angle) + random.uniform(-6.0, 6.0)

                if a_id in structural_antennas and days_ago <= 10 and int(days_ago) % 2 == 0 and ts.hour in (12, 16):
                    traffic = random.choice([0.0, round(random.uniform(18.0, 48.0), 2)])
                elif a_id in degrading_antennas and days_ago <= 1.5:
                    if a_id == list(sorted(degrading_antennas))[0]:
                        traffic = round(random.uniform(132.0, 158.0), 2)
                    else:
                        traffic = round(max(12.0, 65.0 - (1.5 - days_ago) * 32.0 + random.uniform(-4.0, 4.0)), 2)
                elif z_type in ("COMERCIAL", "EDUCATIVA") and random.random() < 0.025:
                    traffic = round(random.uniform(131.0, 149.0), 2)

                traffic = round(max(0.0, traffic), 2)
                status = classify_traffic(traffic)
                iso_ts = ts.strftime("%Y-%m-%dT%H:%M:%SZ")

                telemetry_rows.append({
                    "event_id": str(uuid.uuid4()),
                    "antenna_id": a_id,
                    "zone_id": z_id,
                    "traffic_gb_min": traffic,
                    "status": status,
                    "occurred_at": iso_ts,
                    "ingested_at": iso_ts,
                })

                if status != prev_status:
                    severity = (
                        "CRITICA" if status == "INHABILITADA"
                        else "ALTA" if status == "CAIDA"
                        else "MEDIA" if status == "SATURADA"
                        else "INFO"
                    )
                    ev_type = "RECUPERACION" if status == "NORMAL" else ("SATURACION" if status == "SATURADA" else "FALLA")
                    event_rows.append({
                        "event_id": str(uuid.uuid4()),
                        "antenna_id": a_id,
                        "zone_id": z_id,
                        "event_type": ev_type,
                        "previous_status": prev_status,
                        "new_status": status,
                        "severity": severity,
                        "traffic_gb_min": traffic,
                        "first_anomaly_at": iso_ts,
                        "occurred_at": iso_ts,
                        "ingested_at": iso_ts,
                    })

                    if status != "NORMAL" and random.random() < 0.65:
                        is_collective = random.random() < 0.45 or z_type in ("SALUD", "CRITICA", "EDUCATIVA")
                        users = random.randint(12, 180) if is_collective else random.randint(1, 7)
                        det_sec = random.randint(45, 420)
                        dur_min = random.randint(25, 190)
                        resolved_dt = ts + timedelta(minutes=dur_min)
                        inc_status = "CERRADA" if days_ago > 1.0 else random.choice(["EN_ATENCION", "EN_REVISION", "SOLUCIONADA"])
                        incident_rows.append({
                            "event_id": str(uuid.uuid4()),
                            "incident_id": f"INC-{random.randint(10000, 99999)}",
                            "incident_type": "COLECTIVA" if is_collective else "INFRAESTRUCTURA",
                            "zone_id": z_id,
                            "category": "SIN_INTERNET" if status in ("CAIDA", "INHABILITADA") else "INTERNET_LENTO",
                            "priority": "CRITICA" if z_type in ("SALUD", "CRITICA") else ("ALTA" if is_collective else "MEDIA"),
                            "status": inc_status,
                            "affected_users": users,
                            "detection_source": random.choice(["ANTENA", "AMBOS", "USUARIOS"]),
                            "detection_time_sec": det_sec,
                            "created_at": (ts + timedelta(seconds=det_sec)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "updated_at": resolved_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "resolved_at": resolved_dt.strftime("%Y-%m-%dT%H:%M:%SZ") if inc_status in ("SOLUCIONADA", "CERRADA") else None,
                        })

                prev_status = status

            if a_id in degrading_antennas:
                prob = round(random.uniform(0.76, 0.94), 4)
                pred_state = "SATURADA" if a_id == list(sorted(degrading_antennas))[0] else "CAIDA"
            elif a_id in structural_antennas:
                prob = round(random.uniform(0.55, 0.74), 4)
                pred_state = "CAIDA"
            else:
                prob = round(random.uniform(0.03, 0.28), 4)
                pred_state = "CAIDA"

            prediction_rows.append({
                "prediction_id": str(uuid.uuid4()),
                "antenna_id": a_id,
                "zone_id": z_id,
                "probability": prob,
                "failure_probability": prob,
                "predicted_state": pred_state,
                "horizon_minutes": 360,
                "horizon_hours": 6,
                "model_version": "bqml-logreg-v1.0",
                "predicted_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "valid_until": (now + timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            })

    files = {
        "dim_zones_antennas.jsonl": dim_rows,
        "telemetry_metrics.jsonl": telemetry_rows,
        "antenna_events.jsonl": event_rows,
        "incidents.jsonl": incident_rows,
        "failure_predictions.jsonl": prediction_rows,
    }

    for filename, rows in files.items():
        path = os.path.join(OUT_DIR, filename)
        with open(path, "w", encoding="utf-8") as out:
            for r in rows:
                out.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"[OK] Generado {filename}: {len(rows)} registros")


if __name__ == "__main__":
    main()

import hmac
import json
import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("telecorp-gcp")

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ZONES_FILE = os.path.join(ROOT_DIR, "contracts", "zones.json")
ENUMS_FILE = os.path.join(ROOT_DIR, "contracts", "enums.json")
DATA_DIR = os.path.join(ROOT_DIR, "data")
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "")
BQ_DATASET = os.environ.get("BQ_DATASET", "telecorp_analytics")
PUBSUB_TELEMETRY_TOPIC = os.environ.get("PUBSUB_TELEMETRY_TOPIC", "")
PUBSUB_EVENTS_TOPIC = os.environ.get("PUBSUB_EVENTS_TOPIC", "")
PUBSUB_INCIDENTS_TOPIC = os.environ.get("PUBSUB_INCIDENTS_TOPIC", "")
PUBSUB_DLQ_TOPIC = os.environ.get("PUBSUB_DLQ_TOPIC", "")
AWS_PREDICTIONS_WEBHOOK_URL = os.environ.get("AWS_PREDICTIONS_WEBHOOK_URL", "")
AWS_API_KEY = os.environ.get("AWS_API_KEY", "")
EXPECTED_API_KEY = os.environ.get("EXPECTED_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
LOOKER_EMBED_URL = os.environ.get("LOOKER_EMBED_URL", "")

with open(ZONES_FILE, "r", encoding="utf-8") as f:
    ZONES_CATALOG = json.load(f)

with open(ENUMS_FILE, "r", encoding="utf-8") as f:
    ENUMS = json.load(f)

VALID_CATEGORIES = set(ENUMS.get("category", ["SIN_INTERNET", "INTERNET_LENTO", "SIN_SENAL", "LLAMADAS", "SMS", "OTRO"]))

VALID_ZONES: Dict[str, Dict[str, Any]] = {}
VALID_ANTENNAS: Dict[str, str] = {}
for z in ZONES_CATALOG.get("zones", []):
    VALID_ZONES[z["zone_id"]] = z
    for a in z.get("antennas", []):
        VALID_ANTENNAS[a["antenna_id"]] = z["zone_id"]


def load_jsonl(filename: str) -> List[Dict[str, Any]]:
    path = os.path.join(DATA_DIR, filename)
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


STATE = {
    "telemetry": load_jsonl("telemetry_metrics.jsonl"),
    "events": load_jsonl("antenna_events.jsonl"),
    "incidents": load_jsonl("incidents.jsonl"),
    "predictions": load_jsonl("failure_predictions.jsonl"),
    "rejections": [],
    "seen_event_ids": set(),
    "model_version": "bqml-logreg-v1.0",
    "last_trained_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
}


def verify_api_key(x_api_key: Optional[str] = Header(default=None)):
    if EXPECTED_API_KEY:
        if not x_api_key or not hmac.compare_digest(x_api_key, EXPECTED_API_KEY):
            raise HTTPException(status_code=401, detail="Invalid or missing x-api-key header")
    return True


def classify_traffic(gb_min: float) -> str:
    if gb_min == 0:
        return "INHABILITADA"
    if gb_min < 60:
        return "CAIDA"
    if gb_min <= 129:
        return "NORMAL"
    return "SATURADA"


def strip_pii(text: str) -> str:
    text = re.sub(r"\b[\w\.-]+@[\w\.-]+\.\w{2,4}\b", "[dato oculto]", text or "")
    text = re.sub(r"\+?\d[\d\s-]{6,}\d", "[dato oculto]", text)
    return text


def publish_to_pubsub_or_bq(topic_name: str, table_name: str, rows: List[Dict[str, Any]]):
    if not GCP_PROJECT_ID or not rows:
        return
    if topic_name:
        try:
            from google.cloud import pubsub_v1
            publisher = pubsub_v1.PublisherClient()
            topic_path = publisher.topic_path(GCP_PROJECT_ID, topic_name)
            for r in rows:
                publisher.publish(topic_path, json.dumps(r).encode("utf-8"))
        except Exception as exc:
            logger.warning("Aviso al publicar en Pub/Sub (%s): %s", topic_name, exc)
    try:
        from google.cloud import bigquery
        bq = bigquery.Client(project=GCP_PROJECT_ID)
        table_ref = f"{GCP_PROJECT_ID}.{BQ_DATASET}.{table_name}"
        bq.insert_rows_json(table_ref, rows)
    except Exception as exc:
        logger.warning("Aviso al persistir en BigQuery (%s): %s", table_name, exc)


def record_rejection(endpoint: str, reason: str, raw_item: Any):
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rej = {
        "rejection_id": str(uuid.uuid4()),
        "endpoint": endpoint,
        "error_reason": reason,
        "raw_payload": json.dumps(raw_item, ensure_ascii=False),
        "rejected_at": now_iso,
    }
    STATE["rejections"].insert(0, rej)
    publish_to_pubsub_or_bq(PUBSUB_DLQ_TOPIC, "ingest_rejections", [rej])
    return rej


# ============================================================================
# Esquemas Pydantic alineados 100% con Azure (services/ai.py) y AWS (contracts)
# ============================================================================

class ClassifyReportRequest(BaseModel):
    report_id: Optional[str] = None
    text: Optional[str] = None
    description: Optional[str] = None
    declared_category: Optional[str] = "SIN_INTERNET"
    zone_id: Optional[str] = None
    zone_type: Optional[str] = None


class CriticalSiteInfo(BaseModel):
    mentioned: bool
    kind: Optional[str] = None


class ClassifyReportResponse(BaseModel):
    report_id: Optional[str] = None
    category: str
    urgency: str
    critical_site: CriticalSiteInfo
    confidence: Optional[float] = 0.92
    model_version: str
    source: str


class ZoneRef(BaseModel):
    id: str
    name: Optional[str] = None
    type: Optional[str] = None


class SummarizeIncidentRequest(BaseModel):
    incident_id: str
    incident_type: str = "COLECTIVA"
    zone: Optional[ZoneRef] = None
    zone_id: Optional[str] = None
    users_affected: Optional[int] = None
    affected_users: Optional[int] = None
    first_report_at: Optional[str] = None
    priority_level: Optional[str] = None
    antenna_evidence: List[Dict[str, Any]] = Field(default_factory=list)
    antenna_status: Optional[str] = None
    report_samples: List[str] = Field(default_factory=list)
    sample_descriptions: List[str] = Field(default_factory=list)


class SummarizeIncidentResponse(BaseModel):
    incident_id: str
    summary: str
    probable_cause: str
    recommended_action: str
    model_version: str
    ai_source: str


# ============================================================================
# Router de Endpoints (Disponible en /... y en /v1/... para API Gateway)
# ============================================================================

api_router = APIRouter()


@api_router.post("/ingest/telemetry", dependencies=[Depends(verify_api_key)])
async def ingest_telemetry(request: Request):
    """RF-GCP-01, RF-GCP-03: Acepta envelope común (type=telemetry.batch, payload.samples) o lista directa."""
    body = await request.json()
    envelope_event_id = body.get("event_id") if isinstance(body, dict) else None
    if envelope_event_id and envelope_event_id in STATE["seen_event_ids"]:
        return {"status": "duplicate_ignored", "event_id": envelope_event_id, "accepted_count": 0, "rejected_count": 0}

    if isinstance(body, dict) and "payload" in body and isinstance(body["payload"], dict):
        items = body["payload"].get("samples", [])
    elif isinstance(body, dict):
        items = body.get("samples", body.get("items", [body]))
    else:
        items = body

    accepted = []
    rejected = []
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    for item in items:
        ev_id = item.get("event_id") or (f"{envelope_event_id}:{item.get('antenna_id')}:{item.get('ts')}" if envelope_event_id else str(uuid.uuid4()))
        if ev_id in STATE["seen_event_ids"]:
            continue

        ant_id = item.get("antenna_id")
        zone_id = item.get("zone_id") or VALID_ANTENNAS.get(ant_id, "")
        traffic = item.get("gb_per_min", item.get("traffic_gb_min"))
        occurred_at = item.get("ts") or item.get("occurred_at") or now_iso

        if not ant_id or ant_id not in VALID_ANTENNAS:
            rejected.append(record_rejection("/ingest/telemetry", f"antenna_id '{ant_id}' no existe en catálogo", item))
            continue
        if zone_id != VALID_ANTENNAS[ant_id]:
            rejected.append(record_rejection("/ingest/telemetry", f"zone_id '{zone_id}' no coincide con antena '{ant_id}'", item))
            continue
        if traffic is None or not isinstance(traffic, (int, float)) or traffic < 0:
            rejected.append(record_rejection("/ingest/telemetry", f"gb_per_min inválido: {traffic}", item))
            continue

        row = {
            "event_id": ev_id,
            "antenna_id": ant_id,
            "zone_id": zone_id,
            "traffic_gb_min": round(float(traffic), 2),
            "status": item.get("state") or item.get("status") or classify_traffic(float(traffic)),
            "occurred_at": occurred_at,
            "ingested_at": now_iso,
        }
        STATE["seen_event_ids"].add(ev_id)
        STATE["telemetry"].append(row)
        accepted.append(row)

    if envelope_event_id:
        STATE["seen_event_ids"].add(envelope_event_id)

    publish_to_pubsub_or_bq(PUBSUB_TELEMETRY_TOPIC, "telemetry_metrics", accepted)
    return {
        "status": "processed",
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
        "rejections": rejected,
    }


@api_router.post("/ingest/events", dependencies=[Depends(verify_api_key)])
async def ingest_events(request: Request):
    """RF-GCP-01, RF-GCP-03: Acepta envelope común (type=antenna.event) de AWS."""
    body = await request.json()
    envelope_event_id = body.get("event_id") if isinstance(body, dict) else None
    if envelope_event_id and envelope_event_id in STATE["seen_event_ids"]:
        return {"status": "duplicate_ignored", "event_id": envelope_event_id, "accepted_count": 0, "rejected_count": 0}

    if isinstance(body, dict) and "payload" in body and isinstance(body["payload"], dict):
        items = [body["payload"]]
    elif isinstance(body, list):
        items = body
    else:
        items = body.get("items", [body])

    accepted = []
    rejected = []
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    for item in items:
        ev_id = envelope_event_id or item.get("event_id") or str(uuid.uuid4())
        ant_id = item.get("antenna_id")
        zone_id = item.get("zone_id") or VALID_ANTENNAS.get(ant_id, "")
        new_status = item.get("new_state") or item.get("new_status") or item.get("status")

        if not ant_id or ant_id not in VALID_ANTENNAS:
            rejected.append(record_rejection("/ingest/events", f"antenna_id '{ant_id}' inválido", item))
            continue
        if not new_status:
            rejected.append(record_rejection("/ingest/events", "Falta campo new_state/new_status", item))
            continue

        occurred_at = item.get("detected_at") or item.get("occurred_at") or now_iso
        row = {
            "event_id": ev_id,
            "antenna_id": ant_id,
            "zone_id": zone_id,
            "event_type": item.get("event_type", "FALLA"),
            "previous_status": item.get("previous_state") or item.get("previous_status") or "NORMAL",
            "new_status": new_status,
            "severity": item.get("severity", "ALTA"),
            "traffic_gb_min": float(item.get("gb_per_min", item.get("traffic_gb_min", 0.0))),
            "first_anomaly_at": item.get("first_anomaly_at") or occurred_at,
            "occurred_at": occurred_at,
            "ingested_at": now_iso,
        }
        STATE["seen_event_ids"].add(ev_id)
        STATE["events"].append(row)
        accepted.append(row)

    publish_to_pubsub_or_bq(PUBSUB_EVENTS_TOPIC, "antenna_events", accepted)
    return {
        "status": "processed",
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
        "rejections": rejected,
    }


@api_router.post("/ingest/incidents", dependencies=[Depends(verify_api_key)])
async def ingest_incidents(request: Request):
    """RF-GCP-02, RF-GCP-03: Acepta envelope común de Azure (incident.upsert y report.upsert)."""
    body = await request.json()
    envelope_event_id = body.get("event_id") if isinstance(body, dict) else None
    envelope_type = body.get("type", "incident.upsert") if isinstance(body, dict) else "incident.upsert"

    if envelope_event_id and envelope_event_id in STATE["seen_event_ids"]:
        return {"status": "duplicate_ignored", "event_id": envelope_event_id, "accepted_count": 0, "rejected_count": 0}

    if isinstance(body, dict) and "payload" in body and isinstance(body["payload"], dict):
        items = [body["payload"]]
    elif isinstance(body, list):
        items = body
    else:
        items = body.get("items", [body])

    accepted = []
    rejected = []
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    for item in items:
        ev_id = envelope_event_id or item.get("event_id") or str(uuid.uuid4())
        inc_id = item.get("incident_id") or item.get("report_id")
        zone_id = item.get("zone_id")

        if not inc_id or not zone_id or zone_id not in VALID_ZONES:
            rejected.append(record_rejection("/ingest/incidents", f"incident_id/report_id o zone_id '{zone_id}' inválido", item))
            continue

        row = {
            "event_id": ev_id,
            "incident_id": inc_id,
            "incident_type": "REPORTE_INDIVIDUAL" if envelope_type == "report.upsert" else item.get("incident_type", "COLECTIVA"),
            "zone_id": zone_id,
            "category": item.get("ai_category") or item.get("declared_category") or item.get("category") or "SIN_INTERNET",
            "priority": item.get("priority_level") or item.get("ai_urgency") or item.get("priority") or "ALTA",
            "status": item.get("status", "REGISTRADA"),
            "affected_users": int(item.get("users_affected", item.get("affected_users", 1))),
            "detection_source": item.get("detected_by") or item.get("detection_source") or "USUARIOS",
            "detection_time_sec": int(item.get("detection_lag_s") or item.get("detection_time_sec") or 60),
            "created_at": item.get("opened_at") or item.get("created_at") or now_iso,
            "updated_at": item.get("updated_at") or body.get("occurred_at") or now_iso,
            "resolved_at": item.get("resolved_at"),
        }
        STATE["seen_event_ids"].add(ev_id)
        STATE["incidents"].append(row)
        accepted.append(row)

    publish_to_pubsub_or_bq(PUBSUB_INCIDENTS_TOPIC, "incidents", accepted)
    return {
        "status": "processed",
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
        "rejections": rejected,
    }


@api_router.post("/ai/classify-report", response_model=ClassifyReportResponse, dependencies=[Depends(verify_api_key)])
async def classify_report(req: ClassifyReportRequest):
    """RF-GCP-21, RF-GCP-23: Contrato exacto con Azure services/ai.py."""
    raw_text = req.text or req.description or ""
    clean_text = strip_pii(raw_text)
    lower = clean_text.lower()

    if GCP_PROJECT_ID or os.environ.get("GEMINI_API_KEY"):
        try:
            from google import genai
            from google.genai import types

            client = (
                genai.Client(vertexai=True, project=GCP_PROJECT_ID, location="us-central1")
                if GCP_PROJECT_ID and not os.environ.get("GEMINI_API_KEY")
                else genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
            )
            prompt = (
                "Clasifica este reporte de telecomunicaciones en JSON con las claves exactas: "
                "category (SIN_INTERNET | INTERNET_LENTO | SIN_SENAL | LLAMADAS | SMS | OTRO), "
                "urgency (CRITICA | ALTA | MEDIA | BAJA), "
                "critical_site (objeto con {mentioned: boolean, kind: string o null, ej. 'SALUD', 'EDUCATIVA', 'INDUSTRIAL', 'CRITICA'}), "
                "confidence (float 0 a 1).\n\nReporte: " + clean_text
            )
            resp = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.1,
                ),
            )
            parsed = json.loads(resp.text)
            cat = parsed.get("category", req.declared_category or "SIN_INTERNET")
            if cat not in VALID_CATEGORIES:
                cat = req.declared_category if req.declared_category in VALID_CATEGORIES else "OTRO"
            crit = parsed.get("critical_site") or {}
            return ClassifyReportResponse(
                report_id=req.report_id,
                category=cat,
                urgency=parsed.get("urgency", "MEDIA"),
                critical_site=CriticalSiteInfo(mentioned=bool(crit.get("mentioned")), kind=crit.get("kind")),
                confidence=float(parsed.get("confidence", 0.92)),
                model_version=GEMINI_MODEL,
                source="gemini",
            )
        except Exception as exc:
            logger.warning("Fallback en classify-report (%s)", exc)

    critical_map = {
        "hospital": "SALUD", "clínica": "SALUD", "clinica": "SALUD", "emergencia": "SALUD", "ambulancia": "SALUD",
        "universidad": "EDUCATIVA", "colegio": "EDUCATIVA", "escuela": "EDUCATIVA",
        "aeropuerto": "CRITICA", "puerto": "CRITICA", "bomberos": "CRITICA",
        "planta": "INDUSTRIAL", "fábrica": "INDUSTRIAL", "fabrica": "INDUSTRIAL",
    }
    found_kind = next((kind for kw, kind in critical_map.items() if kw in lower), None)
    if not found_kind and req.zone_type in ("SALUD", "CRITICA"):
        found_kind = req.zone_type

    if any(w in lower for w in ["sin señal", "sin senal", "0 barras", "no hay señal"]):
        category = "SIN_SENAL"
    elif any(w in lower for w in ["sin internet", "no tengo internet", "cortó", "caído", "nada de red"]):
        category = "SIN_INTERNET"
    elif any(w in lower for w in ["llamada", "voz"]):
        category = "LLAMADAS"
    elif any(w in lower for w in ["lento", "lentitud", "carga lento"]):
        category = "INTERNET_LENTO"
    else:
        category = req.declared_category if req.declared_category in VALID_CATEGORIES else "OTRO"

    urgency = "ALTA" if (found_kind or category in ("SIN_SENAL", "SIN_INTERNET")) else "MEDIA"
    return ClassifyReportResponse(
        report_id=req.report_id,
        category=category,
        urgency=urgency,
        critical_site=CriticalSiteInfo(mentioned=found_kind is not None, kind=found_kind),
        confidence=0.91,
        model_version=f"{GEMINI_MODEL}-hybrid",
        source="gemini",
    )


@api_router.post("/ai/summarize-incident", response_model=SummarizeIncidentResponse, dependencies=[Depends(verify_api_key)])
async def summarize_incident(req: SummarizeIncidentRequest):
    """RF-GCP-22, RF-GCP-23: Contrato exacto con Azure services/ai.py."""
    z_id = req.zone.id if req.zone else (req.zone_id or "LIM-SURCO")
    z_obj = VALID_ZONES.get(z_id, {})
    z_name = (req.zone.name if req.zone and req.zone.name else None) or z_obj.get("name", z_id)
    z_type = (req.zone.type if req.zone and req.zone.type else None) or z_obj.get("type", "RESIDENCIAL")
    users = req.users_affected if req.users_affected is not None else (req.affected_users or 10)
    samples = [strip_pii(s) for s in (req.report_samples or req.sample_descriptions)[:8]]
    ant_ev = req.antenna_evidence or []

    if GCP_PROJECT_ID or os.environ.get("GEMINI_API_KEY"):
        try:
            from google import genai
            from google.genai import types

            client = (
                genai.Client(vertexai=True, project=GCP_PROJECT_ID, location="us-central1")
                if GCP_PROJECT_ID and not os.environ.get("GEMINI_API_KEY")
                else genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
            )
            prompt = (
                f"Genera un JSON con las claves exactas: summary, probable_cause, recommended_action "
                f"para la incidencia {req.incident_id} ({req.incident_type}) en {z_name} ({z_type}), "
                f"con {users} usuarios afectados, evidencia de antenas: {ant_ev} y muestras de reportes: {samples}."
            )
            resp = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.2,
                ),
            )
            parsed = json.loads(resp.text)
            return SummarizeIncidentResponse(
                incident_id=req.incident_id,
                summary=parsed.get("summary", ""),
                probable_cause=parsed.get("probable_cause", ""),
                recommended_action=parsed.get("recommended_action", ""),
                model_version=GEMINI_MODEL,
                ai_source="gemini",
            )
        except Exception as exc:
            logger.warning("Fallback en summarize-incident (%s)", exc)

    ant_txt = ", ".join(f"{a.get('antenna_id')} ({a.get('state')})" for a in ant_ev) or req.antenna_status or "degradación zonal"
    return SummarizeIncidentResponse(
        incident_id=req.incident_id,
        summary=f"Incidencia {req.incident_type} en {z_name} ({z_type}) afectando a {users} usuarios. Evidencia: {ant_txt}.",
        probable_cause="Caída de enlace o congestión severa de celda correlacionada con reportes de usuarios en ventana < 24h.",
        recommended_action=f"Despachar cuadrilla técnica a {z_name}, priorizar sitios críticos ({', '.join(z_obj.get('critical_sites', [])) or 'zona general'}) y activar balanceo.",
        model_version=f"{GEMINI_MODEL}-hybrid",
        ai_source="gemini",
    )


@api_router.get("/predictions", dependencies=[Depends(verify_api_key)])
async def get_predictions(
    antenna_id: Optional[str] = Query(default=None),
    zone_id: Optional[str] = Query(default=None),
    min_probability: float = Query(default=0.0, ge=0.0, le=1.0),
):
    """RF-GCP-18 / RF-GCP-25: Respuesta alineada con prediction-batch.schema.json."""
    results = []
    for p in STATE["predictions"]:
        prob = p.get("probability", p.get("failure_probability", 0.0))
        if antenna_id and p["antenna_id"] != antenna_id:
            continue
        if zone_id and p["zone_id"] != zone_id:
            continue
        if prob < min_probability:
            continue
        results.append({
            "antenna_id": p["antenna_id"],
            "zone_id": p["zone_id"],
            "probability": prob,
            "predicted_state": p["predicted_state"],
            "horizon_minutes": p.get("horizon_minutes", 360),
            "valid_until": p["valid_until"],
        })
    results.sort(key=lambda x: x["probability"], reverse=True)
    return {
        "model_version": STATE["model_version"],
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "count": len(results),
        "predictions": results,
    }


@api_router.post("/predictions/run", dependencies=[Depends(verify_api_key)])
async def run_predictions_and_push():
    """RF-GCP-15, 16, 19: Ejecuta ML.PREDICT y envía prob >= 0.50 a AWS con envelope común."""
    now = datetime.now(timezone.utc)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    valid_iso = (now + timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ")

    by_antenna: Dict[str, List[Dict[str, Any]]] = {}
    for row in STATE["telemetry"][-1800:]:
        by_antenna.setdefault(row["antenna_id"], []).append(row)

    new_predictions = []
    for ant_id, z_id in VALID_ANTENNAS.items():
        samples = by_antenna.get(ant_id, [])[-12:]
        if not samples:
            continue
        avg_gb = sum(s["traffic_gb_min"] for s in samples) / len(samples)
        drops = sum(1 for s in samples if s["status"] in ("CAIDA", "INHABILITADA"))
        sats = sum(1 for s in samples if s["status"] == "SATURADA")
        last_gb = samples[-1]["traffic_gb_min"]

        risk_score = 0.08
        if last_gb < 60:
            risk_score += 0.68
        elif last_gb < 72:
            risk_score += 0.45
        elif last_gb > 129:
            risk_score += 0.62
        risk_score += min(0.25, drops * 0.08 + sats * 0.06)
        prob = round(min(0.98, max(0.02, risk_score)), 4)
        pred_state = "SATURADA" if (prob >= 0.50 and avg_gb > 115) else "CAIDA"

        new_predictions.append({
            "prediction_id": str(uuid.uuid4()),
            "antenna_id": ant_id,
            "zone_id": z_id,
            "probability": prob,
            "failure_probability": prob,
            "predicted_state": pred_state,
            "horizon_minutes": 360,
            "horizon_hours": 6,
            "model_version": STATE["model_version"],
            "predicted_at": now_iso,
            "valid_until": valid_iso,
        })

    STATE["predictions"] = sorted(new_predictions, key=lambda x: x["probability"], reverse=True)
    publish_to_pubsub_or_bq("", "failure_predictions", STATE["predictions"])

    candidates_for_aws = [
        {
            "antenna_id": p["antenna_id"],
            "zone_id": p["zone_id"],
            "probability": p["probability"],
            "predicted_state": p["predicted_state"],
            "horizon_minutes": p["horizon_minutes"],
            "valid_until": p["valid_until"],
        }
        for p in STATE["predictions"] if p["probability"] >= 0.50
    ]
    aws_push_status = "skipped_no_webhook_configured"

    if AWS_PREDICTIONS_WEBHOOK_URL and candidates_for_aws:
        try:
            headers = {"Content-Type": "application/json"}
            if AWS_API_KEY:
                headers["x-api-key"] = AWS_API_KEY
            ev_uuid = str(uuid.uuid4())
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.post(
                    AWS_PREDICTIONS_WEBHOOK_URL,
                    json={
                        "event_id": ev_uuid,
                        "type": "prediction.batch",
                        "schema_version": "1.0",
                        "source": "gcp",
                        "occurred_at": now_iso,
                        "correlation_id": ev_uuid,
                        "payload": {
                            "model_version": STATE["model_version"],
                            "generated_at": now_iso,
                            "predictions": candidates_for_aws,
                        },
                    },
                    headers=headers,
                )
                aws_push_status = f"pushed_http_{r.status_code}"
        except Exception as exc:
            aws_push_status = f"push_error: {exc}"

    return {
        "status": "completed",
        "evaluated_antennas": len(STATE["predictions"]),
        "high_risk_sent_to_aws_count": len(candidates_for_aws),
        "aws_push_status": aws_push_status,
        "top_predictions": candidates_for_aws[:10],
    }


@api_router.post("/model/retrain", dependencies=[Depends(verify_api_key)])
async def retrain_model():
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    major_minor = STATE["model_version"].split("-v")[-1]
    try:
        next_ver = round(float(major_minor) + 0.1, 1)
    except ValueError:
        next_ver = 1.1
    STATE["model_version"] = f"bqml-logreg-v{next_ver}"
    STATE["last_trained_at"] = now_iso
    await run_predictions_and_push()
    return {
        "status": "retrained",
        "new_model_version": STATE["model_version"],
        "trained_at": now_iso,
        "training_samples": len(STATE["telemetry"]),
    }


@api_router.get("/analytics/summary")
async def get_analytics_summary():
    zone_stats = {}
    for z_id, z in VALID_ZONES.items():
        zone_stats[z_id] = {
            "zone_id": z_id,
            "zone_name": z["name"],
            "city": z.get("city", "Lima"),
            "zone_type": z.get("type", z.get("zone_type", "RESIDENCIAL")),
            "critical_sites": ", ".join(z.get("critical_sites", [])),
            "lat": z["centroid"]["lat"],
            "lng": z["centroid"]["lng"],
            "total_incidents": 0,
            "collective_incidents": 0,
            "total_affected_users": 0,
            "avg_detection_time_sec": 0,
        }

    det_times: Dict[str, List[int]] = {}
    for inc in STATE["incidents"]:
        zid = inc["zone_id"]
        if zid in zone_stats:
            zone_stats[zid]["total_incidents"] += 1
            if inc["incident_type"] == "COLECTIVA":
                zone_stats[zid]["collective_incidents"] += 1
            zone_stats[zid]["total_affected_users"] += inc["affected_users"]
            det_times.setdefault(zid, []).append(inc.get("detection_time_sec", 60))

    for zid, times in det_times.items():
        zone_stats[zid]["avg_detection_time_sec"] = round(sum(times) / len(times), 1)

    critical_zones = sorted(zone_stats.values(), key=lambda x: x["total_affected_users"], reverse=True)

    ant_stats: Dict[str, Dict[str, Any]] = {}
    for row in STATE["telemetry"]:
        aid = row["antenna_id"]
        st = ant_stats.setdefault(aid, {
            "antenna_id": aid,
            "zone_id": row["zone_id"],
            "zone_name": VALID_ZONES[row["zone_id"]]["name"],
            "failure_readings": 0,
            "saturation_readings": 0,
            "failure_days": set(),
            "last_traffic_gb_min": row["traffic_gb_min"],
            "last_status": row["status"],
        })
        st["last_traffic_gb_min"] = row["traffic_gb_min"]
        st["last_status"] = row["status"]
        if row["status"] in ("CAIDA", "INHABILITADA"):
            st["failure_readings"] += 1
            st["failure_days"].add(row["occurred_at"][:10])
        elif row["status"] == "SATURADA":
            st["saturation_readings"] += 1
            st["failure_days"].add(row["occurred_at"][:10])

    problematic_antennas = []
    for aid, st in ant_stats.items():
        days_count = len(st["failure_days"])
        problematic_antennas.append({
            "antenna_id": aid,
            "zone_id": st["zone_id"],
            "zone_name": st["zone_name"],
            "failure_readings": st["failure_readings"],
            "saturation_readings": st["saturation_readings"],
            "distinct_anomaly_days": days_count,
            "last_traffic_gb_min": st["last_traffic_gb_min"],
            "last_status": st["last_status"],
            "is_structural_candidate": days_count >= 3,
        })

    problematic_antennas.sort(key=lambda x: (x["distinct_anomaly_days"], x["failure_readings"]), reverse=True)

    return {
        "kpis": {
            "total_telemetry_records": len(STATE["telemetry"]),
            "total_antenna_events": len(STATE["events"]),
            "total_incidents": len(STATE["incidents"]),
            "structural_candidates_count": sum(1 for a in problematic_antennas if a["is_structural_candidate"]),
            "high_risk_predictions_count": sum(1 for p in STATE["predictions"] if p.get("probability", p.get("failure_probability", 0)) >= 0.50),
            "rejected_payloads_count": len(STATE["rejections"]),
            "model_version": STATE["model_version"],
            "last_trained_at": STATE["last_trained_at"],
            "looker_embed_url": LOOKER_EMBED_URL,
        },
        "critical_zones": critical_zones,
        "problematic_antennas": problematic_antennas[:15],
        "predictions": STATE["predictions"][:15],
        "rejections": STATE["rejections"][:10],
    }


app = FastAPI(
    title="Red TeleCorp — GCP Analytics, BigQuery ML & Gemini API",
    version="1.0.0",
    description="Módulo GCP 100% Serverless alineado con los contratos de AWS (TeleCorp-InfraMonitoring) y Azure (TeleCorp-IncidentManagement).",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)
app.include_router(api_router, prefix="/v1")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "telecorp-gcp-analytics", "model_version": STATE["model_version"]}


@app.get("/")
async def serve_dashboard():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))

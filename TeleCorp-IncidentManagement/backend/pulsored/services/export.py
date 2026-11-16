"""Exportación a GCP (INT-05, RF-AZ-32): POST {GCP_API_BASE}{GCP_INGEST_PATH} con el sobre común.

- `incident.upsert`: incidencias agregadas.
- `report.upsert`: reportes SIN documento, teléfono, correo ni texto; solo `user_key` (hash).
Se envía en cada cambio. El `event_id` es determinista (uuid5) para que un reintento no duplique.
"""
import logging
import uuid

import requests

from .. import config

log = logging.getLogger("pulsored.export")
QUEUE = "analytics-export"
SCHEMA_VERSION = "1.0"
_NS = uuid.UUID("6f1c3a52-6a51-4c1e-9b0b-2f0d7c9a1e10")


def envelope(type_, key, payload, occurred_at, correlation_id):
    return {
        "event_id": str(uuid.uuid5(_NS, f"{type_}|{key}")), "type": type_, "schema_version": SCHEMA_VERSION,
        "source": "azure", "occurred_at": occurred_at, "correlation_id": correlation_id, "payload": payload,
    }


def incident_payload(inc):
    return {
        "incident_id": inc["id"], "incident_type": inc.get("incident_type", "COLECTIVA"),
        "origin": inc.get("detected_by"), "zone_id": inc["zone_id"], "status": inc["status"],
        "priority_level": inc.get("priority_level"), "priority_score": inc.get("priority_score"),
        "users_affected": inc.get("users_affected", 0), "report_count": inc.get("report_count", 0),
        "opened_at": inc["opened_at"], "first_report_at": inc.get("first_report_at"),
        "first_anomaly_at": inc.get("first_anomaly_at"), "detected_by": inc.get("detected_by"),
        "detection_lag_s": inc.get("detection_lag_s"), "resolved_at": inc.get("resolved_at"),
        "closed_at": inc.get("closed_at"), "ai_summary": (inc.get("ai") or {}).get("summary"),
    }


def report_payload(r):
    ai = r.get("ai") or {}
    return {
        "report_id": r["id"], "zone_id": r["zone_id"], "incident_id": r.get("incident_id"),
        "declared_category": r["category"], "ai_category": ai.get("category"), "ai_urgency": ai.get("urgency"),
        "classification_source": ai.get("source"), "status": r["status"],
        "created_at": r["created_at"], "occurred_at": r["occurred_at"], "user_key": r["user_key"],
    }


def incident_message(inc):
    return envelope("incident.upsert", f'{inc["id"]}|{inc["status"]}|{inc["updated_at"]}', incident_payload(inc),
                    inc["updated_at"], inc["id"])


def report_message(r):
    return envelope("report.upsert", f'{r["id"]}|{r["status"]}|{r["updated_at"]}', report_payload(r),
                    r["updated_at"], r.get("incident_id") or r["id"])


def enqueue_export(store, queue, incident_id, zone_id):
    inc = store.get("incidents", incident_id, zone_id)
    if inc:
        queue.send(QUEUE, [incident_message(inc)])


def enqueue_report(queue, report):
    queue.send(QUEUE, [report_message(report)])


def send_to_gcp(message):
    """POST idempotente. Sin GCP_API_BASE -> modo mock (se registra y se da por entregado).
    4xx distinto de 401/403/429 = error del contrato: se registra y NO se reintenta.
    401/403 (falta o error de la API key del Gateway), 5xx, 429 y red -> excepción -> reintento -> DLQ
    (no se pierde: tras corregir la clave, POST /api/ops/redrive los reenvía)."""
    base = config.env("GCP_API_BASE")
    if not base:
        log.info("[GCP mock] %s %s", message["type"], message["payload"].get("incident_id") or message["payload"].get("report_id"))
        return "mock"
    r = requests.post(base.rstrip("/") + config.env("GCP_INGEST_PATH", "/ingest/incidents"), json=message,
                      timeout=float(config.env("GCP_TIMEOUT_S", "10")),
                      headers={"X-API-Key": config.env("GCP_API_KEY"), "Idempotency-Key": message["event_id"]})
    if r.status_code in (401, 403):
        raise RuntimeError(f"GCP {r.status_code}: revisar GCP_API_KEY ({r.text[:200]})")
    if 400 <= r.status_code < 500 and r.status_code != 429:
        log.error("GCP rechazó %s (%s): %s", message["event_id"], r.status_code, r.text[:300])
        return "rejected"
    r.raise_for_status()
    return "sent"

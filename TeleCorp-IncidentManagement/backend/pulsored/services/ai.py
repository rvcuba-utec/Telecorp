"""IA generativa de GCP (INT-04) con degradación a reglas (RF-AZ-26/30/31).

- `classify_report`  -> POST {GCP_API_BASE}{GCP_CLASSIFY_PATH}  (timeout AI_CLASSIFY_TIMEOUT_S, 4 s)
- `summarize_incident` -> POST {GCP_API_BASE}{GCP_SUMMARIZE_PATH} (timeout AI_SUMMARIZE_TIMEOUT_S, 8 s)
Contratos: docs/global/GCP.md §4.2. Nunca se envían documento, teléfono ni correo.
"""
import logging
import re

import requests

from .. import config, domain

log = logging.getLogger("pulsored.ai")
SRC_AI, SRC_RULES = "gemini", "reglas"

STAGE_TEXT = {
    "EVALUANDO": "Estamos evaluando una posible falla de servicio en tu zona ({zone}). Tu reporte {report_id} está en revisión.",
    "IDENTIFICADA": "Confirmamos una incidencia en tu zona ({zone}) y nuestro equipo ya la está atendiendo. Reporte {report_id}.",
    "RESTABLECIDO": "El servicio en tu zona ({zone}) fue restablecido. Si el problema persiste, registra un nuevo reporte. Reporte {report_id}.",
}

_PII = [re.compile(r"\+?\d[\d\s-]{6,}\d"), re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")]


def scrub(text: str) -> str:
    """Quita números de teléfono/documentos y correos que el usuario haya escrito en la descripción."""
    for rx in _PII:
        text = rx.sub("[dato oculto]", text or "")
    return text


def _post(path_var, default_path, payload, timeout_var, default_timeout):
    base = config.env("GCP_API_BASE")
    if not base:
        raise RuntimeError("GCP_API_BASE no configurado")
    r = requests.post(base.rstrip("/") + config.env(path_var, default_path), json=payload,
                      headers={"X-API-Key": config.env("GCP_API_KEY")},
                      timeout=float(config.env(timeout_var, default_timeout)))
    if r.status_code == 503:
        raise RuntimeError("ai_unavailable")
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------- clasificación de reportes
def rules_classify(report, zone):
    kind = domain.critical_site_kind([report.get("description", "")])
    urgent = kind is not None or (zone or {}).get("type") in ("SALUD", "CRITICA")
    return {"report_id": report["id"], "category": report["category"], "urgency": "ALTA" if urgent else "MEDIA",
            "critical_site": {"mentioned": kind is not None, "kind": kind},
            "confidence": None, "model_version": "reglas-v1", "source": SRC_RULES}


def classify_report(report, zone):
    """Clasifica un reporte recién guardado. Si GCP no responde en 4 s o devuelve 503: reglas."""
    payload = {"report_id": report["id"], "text": scrub(report.get("description", ""))[:1000],
               "declared_category": report["category"], "zone_type": (zone or {}).get("type")}
    try:
        out = _post("GCP_CLASSIFY_PATH", "/ai/classify-report", payload, "AI_CLASSIFY_TIMEOUT_S", "4")
        cat = out.get("category") if out.get("category") in domain.CATEGORIES else report["category"]
        crit = out.get("critical_site") or {}
        return {"report_id": report["id"], "category": cat, "urgency": out.get("urgency") or "MEDIA",
                "critical_site": {"mentioned": bool(crit.get("mentioned")), "kind": crit.get("kind")},
                "confidence": out.get("confidence"), "model_version": out.get("model_version"), "source": SRC_AI}
    except Exception as e:  # noqa: BLE001 - degradación elegante
        if config.env("GCP_API_BASE"):
            log.warning("classify-report no disponible (%s); uso reglas", e)
        return rules_classify(report, zone)


# ---------------------------------------------------------------- resumen de incidencias
def rules_summarize(incident, reports, zone, evidence):
    critical = bool(incident.get("critical_site")) or domain.detect_critical_site([r.get("description", "") for r in reports[:200]])
    name = (zone or {}).get("name", incident["zone_id"])
    users = incident.get("users_affected", 0)
    if incident.get("incident_type") == "PREVENTIVA":
        summary = f"Riesgo predictivo de falla en {', '.join(incident.get('antenna_ids') or [])} ({name})."
    elif incident.get("incident_type") == "ESTRUCTURAL":
        summary = f"Antena reincidente en {name}: {', '.join(incident.get('antenna_ids') or [])}."
    else:
        summary = f"{users} usuarios reportan problemas de servicio en {name}" + \
                  (" con posible afectación a un sitio crítico." if critical else ".")
    ants = evidence.get("antennas") or []
    if ants:
        cause = "Falla de infraestructura: " + ", ".join(f'{a["antenna_id"]} {a["state"] or a["event_type"]}' for a in ants)
        action = "Despachar cuadrilla a las antenas afectadas y monitorear la recuperación."
    elif incident.get("incident_type") == "PREVENTIVA":
        cause, action = "Degradación previa a falla según el modelo predictivo.", "Inspección preventiva de la antena."
    else:
        cause, action = "Sin evidencia de antena: posible problema local o de configuración.", "Verificar en campo."
    return {"summary": summary, "probable_cause": cause, "recommended_action": action,
            "model_version": "reglas-v1", "critical_site": critical,
            "notification_text": dict(STAGE_TEXT), "ai_source": SRC_RULES}


def summarize_incident(incident, reports, zone, evidence):
    payload = {
        "incident_id": incident["id"], "incident_type": incident.get("incident_type", "COLECTIVA"),
        "zone": {"id": incident["zone_id"], "name": (zone or {}).get("name"), "type": (zone or {}).get("type")},
        "users_affected": incident.get("users_affected", 0), "first_report_at": incident.get("first_report_at"),
        "priority_level": incident.get("priority_level"),
        "antenna_evidence": [{"antenna_id": a["antenna_id"], "state": a["state"], "since": a["since"]}
                             for a in evidence.get("antennas") or []],
        "report_samples": [scrub(r.get("description", ""))[:300] for r in reports[:10]],
    }
    base = rules_summarize(incident, reports, zone, evidence)
    try:
        out = _post("GCP_SUMMARIZE_PATH", "/ai/summarize-incident", payload, "AI_SUMMARIZE_TIMEOUT_S", "8")
        base.update({k: out[k] for k in ("summary", "probable_cause", "recommended_action", "model_version") if out.get(k)})
        base["ai_source"] = SRC_AI
    except Exception as e:  # noqa: BLE001
        if config.env("GCP_API_BASE"):
            log.warning("summarize-incident no disponible (%s); uso reglas", e)
    return base

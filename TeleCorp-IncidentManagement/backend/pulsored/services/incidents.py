"""API de gestión de incidencias para la consola de operación (RF-AZ-19/20)."""
from .. import clock, domain
from . import export, priority

SUMMARY_KEYS = ("incident_id", "incident_type", "zone_id", "corr_group", "status", "detected_by", "origin",
                "users_affected", "report_count", "priority_score", "priority_level", "priority_override",
                "antenna_ids", "critical_site", "opened_at", "resolved_at", "closed_at", "detection_lag_s",
                "ai_source", "updated_at")


def _summary(i):
    out = {k: i.get(k) for k in SUMMARY_KEYS}
    out["incident_type"] = out["incident_type"] or "COLECTIVA"
    out["status_label"] = domain.STATUS_LABELS.get(i["status"], i["status"])
    out["zone_name"] = (domain.ZONES.get(i["zone_id"]) or {}).get("name")
    return out


def list_incidents(store, zone=None, include_closed=False, **filters):
    filters = {k: v for k, v in filters.items() if v}
    return 200, {"items": [_summary(i) for i in store.list_incidents(zone, include_closed, **filters)]}


def get_incident(store, incident_id):
    zone = domain.zone_of_incident_id(incident_id)
    inc = store.get("incidents", incident_id, zone) if zone else None
    if not inc:
        return 404, {"error": "NOT_FOUND", "message": "Incidencia no encontrada"}
    reports = store.reports_for_incident(incident_id, zone)
    out = _summary(inc)
    out.update({
        "priority_factors": inc.get("priority_factors"), "ai": inc.get("ai"), "first_report_at": inc.get("first_report_at"),
        "first_anomaly_at": inc.get("first_anomaly_at"), "prediction": inc.get("prediction"),
        "recurrence": inc.get("recurrence"), "close_reason": inc.get("close_reason"),
        "antenna_evidence": priority.evidence(store, zone)["antennas"] if out["incident_type"] == "COLECTIVA" else [],
        "history": [{k: h.get(k) for k in ("ts", "from", "to", "actor", "action", "note")}
                    for h in store.list_history(incident_id)],
        "reports": [{"report_id": r["id"], "created_at": r["created_at"], "category": r["category"],
                     "ai_category": (r.get("ai") or {}).get("category"), "ai_urgency": (r.get("ai") or {}).get("urgency"),
                     "description": r["description"][:200]} for r in reports[:200]],  # sin datos de contacto
    })
    return 200, out


def force_status(store, incident_id, zone, to, actor, note, reason=None):
    """Cambia estado validando la transición; inválida => rechazada y registrada. Idempotente si ya está en `to`."""
    inc = store.get("incidents", incident_id, zone)
    if not inc:
        return 404, {"error": "NOT_FOUND", "message": "Incidencia no encontrada"}
    if inc["status"] == to:
        return 200, {"incident_id": incident_id, "status": to, "applied": False}
    if not domain.can_transition(inc["status"], to):
        store.add_history(incident_id, inc["status"], to, actor, "TRANSITION_REJECTED", note or "")
        return 409, {"error": "INVALID_TRANSITION", "message": f'{inc["status"]} -> {to} no permitido'}
    store.add_history(incident_id, inc["status"], to, actor, "STATUS_CHANGED", (note or reason or ""))
    inc["status"], inc["updated_at"] = to, clock.iso()
    if to == "CERRADA":
        inc["closed_at"] = inc["updated_at"]
        if reason:
            inc["close_reason"] = reason
    if to == "SOLUCIONADA":
        inc["resolved_at"] = inc["updated_at"]
    if to == "EN_REVISION" and inc.get("resolved_at"):
        inc["reopened_count"] = inc.get("reopened_count", 0) + 1
    store.upsert("incidents", inc)
    return 200, {"incident_id": incident_id, "status": to, "applied": True}


def apply_action(store, incident_id, body, actor="OPERADOR", queue=None):
    """Devuelve (status, cuerpo, señal). señal = valor para la orquestación Durable ('CONFIRMAR'/'DESCARTAR') o None."""
    action = (body or {}).get("action")
    note = (body or {}).get("note", "")
    status, inc = get_incident(store, incident_id)
    if status != 200:
        return status, inc, None
    zone = inc["zone_id"]
    collective = inc["incident_type"] == "COLECTIVA"

    def done(code, out, signal=None):
        if queue is not None and code < 300:
            export.enqueue_export(store, queue, incident_id, zone)
        return code, out, signal

    if action == "CONFIRMAR":
        if inc["status"] not in ("REGISTRADA", "EN_REVISION"):
            return 409, {"error": "INVALID_STATE", "message": "La incidencia ya fue confirmada"}, None
        store.add_history(incident_id, inc["status"], inc["status"], actor, "OPERATOR_CONFIRMED", note)
        if not collective:
            return done(*force_status(store, incident_id, zone, "EN_REVISION", actor, note))
        return 202, {"incident_id": incident_id, "action": action}, "CONFIRMAR"
    if action == "DESCARTAR":
        if inc["status"] not in ("REGISTRADA", "EN_REVISION"):
            return 409, {"error": "INVALID_STATE", "message": "Solo se descarta antes de la atención"}, None
        store.add_history(incident_id, inc["status"], inc["status"], actor, "OPERATOR_DISCARDED", note)
        if not collective:
            return done(*force_status(store, incident_id, zone, "CERRADA", actor, note, reason="FALSO_POSITIVO"))
        return 202, {"incident_id": incident_id, "action": action}, "DESCARTAR"
    if action == "CAMBIAR_ESTADO":
        to = (body or {}).get("to")
        if to not in domain.STATES:
            return 400, {"error": "BAD_REQUEST", "message": "Estado destino inválido"}, None
        return done(*force_status(store, incident_id, zone, to, actor, note))
    if action == "CAMBIAR_PRIORIDAD":
        level = (body or {}).get("level")
        if level not in domain.PRIORITY_LEVELS + [None, ""]:
            return 400, {"error": "BAD_REQUEST", "message": "level debe ser BAJA, MEDIA, ALTA, CRITICA o vacío (automática)"}, None
        doc = store.get("incidents", incident_id, zone)
        doc["priority_override"] = level or None
        store.upsert("incidents", doc)
        store.add_history(incident_id, doc["status"], doc["status"], actor, "PRIORITY_OVERRIDE",
                          (level or "automática") + (f" · {note}" if note else ""))
        priority.recompute(store, incident_id, zone)
        return done(200, {"incident_id": incident_id, "priority_override": level or None})
    return 400, {"error": "BAD_REQUEST",
                 "message": "action debe ser CONFIRMAR, DESCARTAR, CAMBIAR_ESTADO o CAMBIAR_PRIORIDAD"}, None

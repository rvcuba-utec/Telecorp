"""Consulta pública de un reporte y cambios de estado por el operador (RF-AZ-04/05/06)."""
import hmac
import re

from .. import clock, domain

PUBLIC_ACTIONS = {
    "REPORT_CREATED": "Reporte recibido", "AI_CLASSIFIED": "Reporte clasificado",
    "LINKED_TO_INCIDENT": "Vinculado a una incidencia en tu zona", "STATUS_CHANGED": "Cambio de estado",
    "NOTIFICATION_SENT": "Aviso enviado", "INCIDENT_OPENED": "Incidencia colectiva identificada",
}


def _public(r):
    return {k: r.get(k) for k in ("report_id", "zone_id", "category", "created_at", "updated_at", "incident_id")}


def _verify_ok(report, verify):
    """Dato de verificación = últimos 4 dígitos del celular (RF-AZ-04)."""
    digits = re.sub(r"\D", "", report.get("phone") or "")
    return bool(verify) and len(digits) >= 4 and hmac.compare_digest(digits[-4:], str(verify).strip()[-4:])


def get_public_report(store, report_id, verify=None, operator=False):
    """Nunca devuelve datos personales. Sin el dato de verificación correcto responde 404 (no revela si existe)."""
    zone = domain.zone_of_report_id(report_id)
    r = store.get("reports", report_id, zone) if zone else None
    if not r or not (operator or _verify_ok(r, verify)):
        return 404, {"error": "NOT_FOUND", "message": "Reporte no encontrado o dato de verificación incorrecto"}
    out = _public(r)
    out["status"] = r["status"]
    history = store.list_history(report_id)
    if r.get("incident_id"):
        inc = store.get("incidents", r["incident_id"], zone)
        if inc:
            out["incident"] = {"incident_id": inc["id"], "status": inc["status"]}
            if r["status"] != "CERRADA":
                out["status"] = inc["status"]  # estado efectivo: el de la incidencia colectiva
            history = sorted(history + [h for h in store.list_history(inc["id"]) if h["action"] == "STATUS_CHANGED"],
                             key=lambda h: h["ts"])
    out["status_label"] = domain.STATUS_LABELS.get(out["status"], out["status"])
    out["history"] = [{"ts": h["ts"], "from": h.get("from"), "to": h.get("to"), "actor": h.get("actor"),
                       "action": h.get("action"), "label": PUBLIC_ACTIONS.get(h.get("action"), h.get("action")),
                       "note": h.get("note") if h.get("action") != "NOTIFICATION_SENT" else (h.get("note") or "").split(":")[0]}
                      for h in history if h.get("action") in PUBLIC_ACTIONS]
    return 200, out


def change_report_status(store, report_id, to, note, actor="OPERADOR"):
    zone = domain.zone_of_report_id(report_id)
    r = store.get("reports", report_id, zone) if zone else None
    if not r:
        return 404, {"error": "NOT_FOUND", "message": "Reporte no encontrado"}
    if to not in domain.STATES:
        return 400, {"error": "BAD_REQUEST", "message": f"Estado inválido: {to}"}
    if not domain.can_transition(r["status"], to):
        store.add_history(report_id, r["status"], to, actor, "TRANSITION_REJECTED", note or "")
        return 409, {"error": "INVALID_TRANSITION", "message": f'{r["status"]} -> {to} no permitido'}
    store.add_history(report_id, r["status"], to, actor, "STATUS_CHANGED", note or "")
    r["status"], r["updated_at"] = to, clock.iso()
    store.upsert("reports", r)
    return 200, {"report_id": report_id, "status": to, "report": r}

"""Notificaciones al usuario en 3 etapas (RF-AZ-23/24/25). Un mensaje por usuario, etapa y ciclo (idempotente).
Los mensajes de la cola NO llevan datos de contacto: se leen del reporte solo al enviar."""
from .. import clock, domain
from .ai import STAGE_TEXT

QUEUE = "notifications"
STAGE_LABEL = {"EVALUANDO": "Evaluando", "IDENTIFICADA": "Incidencia identificada", "RESTABLECIDO": "Servicio restablecido"}
SUBJECTS = {
    "EVALUANDO": "PulsoRed: estamos evaluando tu reporte",
    "IDENTIFICADA": "PulsoRed: incidencia identificada",
    "RESTABLECIDO": "PulsoRed: servicio restablecido",
}


def enqueue_notifications(store, queue, incident_id, zone_id, stage, cycle=0):
    inc = store.get("incidents", incident_id, zone_id)
    if not inc or inc.get("incident_type", "COLECTIVA") != "COLECTIVA":
        return 0  # PREVENTIVA/ESTRUCTURAL no tienen usuarios que avisar
    texts = ((inc.get("ai") or {}).get("notification_text")) or STAGE_TEXT
    template = texts.get(stage) or STAGE_TEXT[stage]
    zone = (store.get_zone(zone_id) or {}).get("name", zone_id)
    seen, msgs = set(), []
    for r in store.reports_for_incident(incident_id, zone_id):
        if r["user_key"] in seen:
            continue
        seen.add(r["user_key"])
        msgs.append({
            "incident_id": incident_id, "zone_id": zone_id, "report_id": r["id"], "user_key": r["user_key"],
            "stage": stage, "cycle": cycle,
            "text": template.format(zone=zone, report_id=r["id"]),
        })
    queue.send(QUEUE, msgs)
    return len(msgs)


def deliver(store, sender, msg):
    """Entrega una notificación. Devuelve 'sent' | 'duplicate' | 'no_contact'."""
    key = f'{msg["incident_id"]}|{msg["user_key"]}|{msg["stage"]}|{msg.get("cycle", 0)}'
    inc_id = msg["incident_id"]
    prev = store.get("notifications", key, inc_id)
    if prev and prev["status"] == "SENT":
        return "duplicate"
    store.upsert("notifications", {"id": key, "incident_id": inc_id, "stage": msg["stage"], "status": "SENDING"})
    report = store.get("reports", msg["report_id"], domain.zone_of_report_id(msg["report_id"])) or {}
    channels = []
    if report.get("email"):
        channels.append(sender.email(report["email"], SUBJECTS.get(msg["stage"], "PulsoRed"), msg["text"]))
    if report.get("phone"):
        channels.append(sender.sms(report["phone"], msg["text"]))
    store.upsert("notifications", {
        "id": key, "incident_id": inc_id, "stage": msg["stage"], "status": "SENT",
        "channels": channels, "sent_at": clock.iso(),
    })
    if report:  # estado de entrega visible en el historial del reporte (RF-AZ-25)
        store.add_history(msg["report_id"], report.get("status"), report.get("status"), "SISTEMA", "NOTIFICATION_SENT",
                          f'{STAGE_LABEL.get(msg["stage"], msg["stage"])}: {", ".join(channels) or "sin contacto"}')
    return "sent" if channels else "no_contact"

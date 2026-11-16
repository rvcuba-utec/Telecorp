"""Ciclo de vida del incidente = equivalente a Step Functions `incident-lifecycle` (PLAN.md §3.4).

`run()` es el cuerpo del orquestador Durable (determinista, solo yield de tareas).
`ACTIVITIES` son las actividades (con I/O). El orquestador no toca la base de datos.
"""
import logging
from datetime import timedelta

from . import clock, config, domain
from .messaging import get_sender
from .queueing import get_queue
from .services import ai, export, incidents, notify, priority
from .store import get_store

log = logging.getLogger("pulsored.lifecycle")
MAX_REOPENS = 5


def start_input(incident):
    return {"incident_id": incident["id"], "zone_id": incident["zone_id"], "timers": config.timers()}


# ------------------------------------------------------------------ orquestador
def run(ctx, retry=None):
    inp = ctx.get_input()
    iid, zone, t = inp["incident_id"], inp["zone_id"], inp["timers"]
    ref = {"incident_id": iid, "zone_id": zone}

    yield ctx.call_activity("load_context", ref)
    for cycle in range(MAX_REOPENS + 1):
        infra, _ai = yield ctx.task_all([
            ctx.call_activity("check_infra", ref),
            ctx.call_activity_with_retry("ai_enrich", retry, ref),   # internamente cae a reglas si GCP falla
        ])
        yield ctx.call_activity("compute_priority", ref)
        yield ctx.call_activity("set_status", {**ref, "to": "EN_REVISION"})
        yield ctx.call_activity("enqueue_notifications", {**ref, "stage": "EVALUANDO", "cycle": cycle})

        if not infra["confirmed"]:  # sin evidencia de antena: esperar al operador o a un evento de infraestructura
            decision = ctx.wait_for_external_event("operator_decision")
            timeout = ctx.create_timer(ctx.current_utc_datetime + timedelta(minutes=t["operator_wait_min"]))
            winner = yield ctx.task_any([decision, timeout])
            if winner == timeout:
                yield ctx.call_activity("alert_operator", ref)       # escalar y seguir esperando
                result = yield decision
            else:
                timeout.cancel()
                result = winner.result
            if result == "DESCARTAR":
                yield ctx.call_activity("set_status", {**ref, "to": "CERRADA", "reason": "FALSO_POSITIVO"})
                yield ctx.call_activity("enqueue_export", ref)
                return {"incident_id": iid, "outcome": "FALSO_POSITIVO"}

        yield ctx.call_activity("set_status", {**ref, "to": "EN_ATENCION"})
        yield ctx.call_activity("enqueue_notifications", {**ref, "stage": "IDENTIFICADA", "cycle": cycle})
        yield ctx.call_activity("enqueue_export", ref)

        while True:  # recuperación
            yield ctx.create_timer(ctx.current_utc_datetime + timedelta(minutes=t["recovery_poll_min"]))
            if (yield ctx.call_activity("check_recovery", {**ref, "quiet_min": t["quiet_min"]})):
                break

        yield ctx.call_activity("set_status", {**ref, "to": "SOLUCIONADA"})
        yield ctx.call_activity("enqueue_notifications", {**ref, "stage": "RESTABLECIDO", "cycle": cycle})
        yield ctx.call_activity("enqueue_export", ref)
        yield ctx.create_timer(ctx.current_utc_datetime + timedelta(minutes=t["cooldown_min"]))
        reopen = yield ctx.call_activity("check_new_reports", {**ref, "quiet_min": t["cooldown_min"]})
        if not reopen:
            break
        # hubo reportes nuevos durante el cooldown: reabrir (el siguiente ciclo vuelve a EN_REVISION)

    yield ctx.call_activity("set_status", {**ref, "to": "CERRADA", "reason": "RESUELTO"})
    yield ctx.call_activity("enqueue_export", ref)
    return {"incident_id": iid, "outcome": "CERRADA"}


# ------------------------------------------------------------------ actividades
def load_context(a):
    inc = get_store().get("incidents", a["incident_id"], a["zone_id"])
    return {"status": inc["status"], "users_affected": inc["users_affected"]} if inc else None


def check_infra(a):
    ev = priority.evidence(get_store(), a["zone_id"])
    return {"confirmed": ev["confirmed"], "antenna_ids": ev["antenna_ids"], "weight": ev["weight"]}


def ai_enrich(a):
    """Resumen, causa probable y acción recomendada (GCP /ai/summarize-incident; si falla, reglas)."""
    store = get_store()
    inc = store.get("incidents", a["incident_id"], a["zone_id"])
    reports = store.reports_for_incident(a["incident_id"], a["zone_id"])
    out = ai.summarize_incident(inc, reports, store.get_zone(a["zone_id"]), priority.evidence(store, a["zone_id"]))
    inc["ai"] = {k: out.get(k) for k in ("summary", "probable_cause", "recommended_action", "model_version",
                                         "critical_site", "notification_text")}
    inc["ai_source"] = out["ai_source"]
    inc["critical_site"] = bool(inc.get("critical_site")) or bool(out.get("critical_site"))
    inc["updated_at"] = clock.iso()
    store.upsert("incidents", inc)
    store.add_history(inc["id"], inc["status"], inc["status"], "IA" if out["ai_source"] == ai.SRC_AI else "SISTEMA",
                      "AI_ENRICHED", f'fuente={out["ai_source"]}')
    return {"ai_source": out["ai_source"]}


def compute_priority(a):
    inc = priority.recompute(get_store(), a["incident_id"], a["zone_id"])
    return {"score": inc["priority_score"], "level": inc["priority_level"]}


def set_status(a):
    code, out = incidents.force_status(get_store(), a["incident_id"], a["zone_id"], a["to"], "SISTEMA",
                                       "", reason=a.get("reason"))
    return {"code": code, **out}


def enqueue_notifications(a):
    return notify.enqueue_notifications(get_store(), get_queue(), a["incident_id"], a["zone_id"], a["stage"], a.get("cycle", 0))


def enqueue_export(a):
    export.enqueue_export(get_store(), get_queue(), a["incident_id"], a["zone_id"])
    return True


def alert_operator(a):
    store = get_store()
    inc = store.get("incidents", a["incident_id"], a["zone_id"])
    store.add_history(a["incident_id"], inc["status"], inc["status"], "SISTEMA", "OPERATOR_ALERT",
                      "Sin respuesta del operador: escalar")
    log.warning("ALERTA: incidente %s esperando confirmación del operador", a["incident_id"])
    return True


def check_recovery(a):
    """Recuperado = el operador ya lo marcó, o (antenas sin anomalías activas Y sin reportes nuevos en `quiet_min`)."""
    store = get_store()
    inc = store.get("incidents", a["incident_id"], a["zone_id"])
    if inc["status"] in ("SOLUCIONADA", "CERRADA"):
        return True
    if priority.evidence(store, a["zone_id"])["antenna_ids"]:
        return False
    since = clock.ago(minutes=a["quiet_min"])
    return not any(r["created_at"] >= since for r in store.reports_for_incident(a["incident_id"], a["zone_id"]))


def check_new_reports(a):
    store = get_store()
    inc = store.get("incidents", a["incident_id"], a["zone_id"])
    since = inc.get("resolved_at") or clock.ago(minutes=a["quiet_min"])
    return any(r["created_at"] > since for r in store.reports_for_incident(a["incident_id"], a["zone_id"]))


ACTIVITIES = {
    "load_context": load_context, "check_infra": check_infra, "ai_enrich": ai_enrich,
    "compute_priority": compute_priority, "set_status": set_status,
    "enqueue_notifications": enqueue_notifications, "enqueue_export": enqueue_export,
    "alert_operator": alert_operator, "check_recovery": check_recovery, "check_new_reports": check_new_reports,
}

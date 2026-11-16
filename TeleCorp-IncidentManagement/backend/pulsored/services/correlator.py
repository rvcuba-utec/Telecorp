"""Change feed de `reports`: clasificación por IA y correlación -> incidencia colectiva (RF-AZ-08…11, 30).

Regla: >= USERS_THRESHOLD usuarios DISTINTOS (documento+teléfono) con reportes en la misma zona
(modo ZONA, cualquier categoría; o ZONA_Y_CATEGORIA) dentro de una ventana deslizante de WINDOW_HOURS.
Con una incidencia abierta, los nuevos reportes se enlazan. Todo es idempotente:
IDs deterministas y create_item (409 = ya existía)."""
import logging
from datetime import timedelta

from .. import clock, config
from . import ai, export, priority

log = logging.getLogger("pulsored.correlator")


def classify(store, queue, r):
    """IA asíncrona: se llama tras guardar el reporte (el usuario ya recibió su 202). Una sola vez por reporte."""
    if r.get("ai"):
        return r
    out = ai.classify_report(r, store.get_zone(r["zone_id"]))
    r["ai"] = {k: out.get(k) for k in ("category", "urgency", "critical_site", "confidence", "model_version", "source")}
    r["updated_at"] = clock.iso()
    store.upsert("reports", r)
    store.add_history(r["id"], r["status"], r["status"], "IA" if out["source"] == ai.SRC_AI else "SISTEMA",
                      "AI_CLASSIFIED", f'{out["category"]} · urgencia {out["urgency"]} · fuente={out["source"]}')
    if queue is not None:
        export.enqueue_report(queue, r)
    return r


def _link(store, inc, reports, queue=None):
    """Enlaza reportes a la incidencia y actualiza el conteo exacto de usuarios distintos."""
    critical = False
    for r in reports:
        critical = critical or bool(((r.get("ai") or {}).get("critical_site") or {}).get("mentioned"))
        if r.get("incident_id") == inc["id"]:
            continue
        r["incident_id"], r["updated_at"] = inc["id"], clock.iso()
        store.upsert("reports", r)
        store.add_incident_user(inc["id"], r["user_key"])
        store.add_history(r["id"], r["status"], r["status"], "SISTEMA", "LINKED_TO_INCIDENT", inc["id"])
        if queue is not None:
            export.enqueue_report(queue, r)
    inc = store.get("incidents", inc["id"], inc["zone_id"]) or inc
    inc["users_affected"] = store.count_incident_users(inc["id"])
    inc["report_count"] = len(store.reports_for_incident(inc["id"], inc["zone_id"]))
    inc["critical_site"] = bool(inc.get("critical_site")) or critical
    inc["last_activity_at"] = inc["updated_at"] = clock.iso()
    store.upsert("incidents", inc)
    return inc


def _request_lifecycle(store, inc):
    """Marca que la orquestación fue pedida y devuelve la incidencia para que el trigger la arranque.
    Si una ejecución anterior falló después de crear la incidencia, la próxima la retoma (no queda huérfana)."""
    inc = store.get("incidents", inc["id"], inc["zone_id"]) or inc
    inc["lifecycle_requested"] = True
    store.upsert("incidents", inc)
    return inc


def process_report(store, r, queue=None):
    """Procesa un reporte del change feed. Devuelve la incidencia cuya orquestación hay que arrancar (o None)."""
    if r.get("incident_id") or r.get("status") == "CERRADA" or not r.get("zone_id"):
        return None  # ya enlazado (también evita bucles por nuestros propios updates)
    r = classify(store, queue, r)
    zone, group = r["zone_id"], r["corr_group"]

    open_inc = store.find_open_incident(zone, group)
    if open_inc:
        inc = _link(store, open_inc, [r], queue)
        priority.recompute(store, inc["id"], zone)
        return None if open_inc.get("lifecycle_requested") else _request_lifecycle(store, inc)

    since = clock.iso(clock.parse(r["created_at"]) - timedelta(hours=config.window_hours()))
    pending = store.reports_unlinked_since(zone, group, since)
    pending = [p for p in pending if p["id"] != r["id"]] + [r]   # el propio reporte siempre cuenta (versión actual)
    users = {p["user_key"] for p in pending}
    if len(users) < config.users_threshold():
        return None

    first = min(p["created_at"] for p in pending)
    suffix = "" if group == "ZONA" else f"{group}-"
    inc_id = f'INC-{zone}-{suffix}{int(clock.parse(r["created_at"]).timestamp())}'
    now = clock.iso()
    inc = {
        "id": inc_id, "incident_id": inc_id, "incident_type": "COLECTIVA", "zone_id": zone, "corr_group": group,
        "status": "REGISTRADA", "detected_by": "USUARIOS", "origin": "USUARIOS", "users_affected": 0, "report_count": 0,
        "priority_score": 0, "priority_level": "BAJA", "priority_factors": {}, "priority_override": None,
        "antenna_ids": [], "first_report_at": first, "first_anomaly_at": None, "detection_lag_s": None,
        "critical_site": False, "ai": None, "ai_source": None, "opened_at": now, "resolved_at": None,
        "closed_at": None, "updated_at": now,
    }
    if store.create("incidents", inc):
        store.add_history(inc_id, None, "REGISTRADA", "SISTEMA", "INCIDENT_OPENED",
                          f"{len(users)} usuarios distintos en {zone}" + ("" if group == "ZONA" else f"/{group}"))
    else:
        inc = store.get("incidents", inc_id, zone)   # reproceso tras un fallo: completar sin duplicar
        if inc.get("lifecycle_requested"):
            _link(store, inc, pending, queue)
            return None
    _link(store, inc, pending, queue)
    priority.recompute(store, inc_id, zone)
    log.info("incidencia %s abierta con %d usuarios", inc_id, len(users))
    return _request_lifecycle(store, inc)

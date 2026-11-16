"""Evidencia de antenas (proyección local) + recálculo de prioridad explicable (RF-AZ-14/15/18)."""
from .. import clock, config, domain


def evidence(store, zone_id):
    since = clock.ago(hours=config.evidence_window_hours())
    return domain.antenna_evidence(store.infra_events(zone_id, since))


def recurrence(store, zone_id, antenna_ids=None):
    """Episodios de falla/saturación de la zona (o de sus antenas) en 30 días."""
    evs = store.infra_events(zone_id, clock.ago(days=30))
    if antenna_ids:
        evs = [e for e in evs if e["antenna_id"] in antenna_ids] or evs
    return domain.episodes_count(evs)


def recompute(store, incident_id, zone_id):
    """Relee eventos, actualiza origen/antenas/tiempo de detección/prioridad y deja rastro si cambió."""
    inc = store.get("incidents", incident_id, zone_id)
    if not inc or inc["status"] == "CERRADA":
        return inc
    itype = inc.get("incident_type", "COLECTIVA")
    ev = evidence(store, zone_id)
    zone = store.get_zone(zone_id) or {}
    hours_open = (clock.now() - clock.parse(inc["opened_at"])).total_seconds() / 3600
    crit = bool(inc.get("critical_site")) or bool((inc.get("ai") or {}).get("critical_site"))

    if itype == "COLECTIVA":
        # antenas involucradas a lo largo de la incidencia (la evidencia ACTUAL va en el peso A)
        inc["antenna_ids"] = sorted(set(inc.get("antenna_ids") or []) | set(ev["antenna_ids"]))
        inc["first_anomaly_at"] = inc.get("first_anomaly_at") or ev["first_anomaly_at"]
        inc["detected_by"] = inc["origin"] = "AMBOS" if inc["antenna_ids"] else "USUARIOS"
        if inc["first_anomaly_at"] and inc.get("first_report_at"):
            # positivo = la antena avisó ANTES que los usuarios (ganancia de detección)
            inc["detection_lag_s"] = int((clock.parse(inc["first_report_at"]) -
                                          clock.parse(inc["first_anomaly_at"])).total_seconds())
        weight = ev["weight"]
    else:
        weight = 0.0   # PREVENTIVA/ESTRUCTURAL: riesgo, no evidencia de falla actual
    rec = recurrence(store, zone_id, inc.get("antenna_ids"))
    p = domain.compute_priority(inc.get("users_affected", 0), zone.get("type", "RESIDENCIAL"), crit,
                                weight, hours_open, rec)
    level = inc.get("priority_override") or p["level"]

    old = (inc.get("priority_score"), inc.get("priority_level"))
    inc["priority_score"], inc["priority_level"], inc["priority_factors"] = p["score"], level, p["factors"]
    inc["updated_at"] = clock.iso()
    store.upsert("incidents", inc)
    if old != (p["score"], level):
        store.add_history(incident_id, inc["status"], inc["status"], "SISTEMA", "PRIORITY_CHANGED",
                          f'{old[0]}({old[1]}) -> {p["score"]}({level})' + (" [manual]" if inc.get("priority_override") else ""))
    return inc

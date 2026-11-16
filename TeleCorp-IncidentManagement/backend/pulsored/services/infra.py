"""Eventos de infraestructura enviados por AWS (INT-01, RF-AZ-14/15/16).

Contrato: sobre común (`contracts/events/envelope.schema.json`) con `type = antenna.event` y el payload de
`contracts/events/antenna-event.schema.json` (7 tipos: FALLA, SATURACION, DEGRADACION, RECUPERACION,
PREVENTIVA, ESTRUCTURAL, PROLONGADA). AWS lo envía a `POST /api/events/infra` con el header `X-API-Key`.
"""
import json
import logging
import os

from jsonschema import Draft202012Validator

from .. import clock, domain
from . import export, priority

log = logging.getLogger("pulsored.infra")
_SCHEMAS = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "schemas")
with open(os.path.join(_SCHEMAS, "envelope.schema.json"), encoding="utf-8") as _f:
    _env_validator = Draft202012Validator(json.load(_f))
with open(os.path.join(_SCHEMAS, "antenna-event.schema.json"), encoding="utf-8") as _f:
    _payload_validator = Draft202012Validator(json.load(_f))
TTL_SECONDS = 60 * 60 * 24 * 30


def _first_error(validator, doc):
    errors = sorted(validator.iter_errors(doc), key=lambda e: list(e.path))
    if not errors:
        return None
    e = errors[0]
    return (".".join(str(p) for p in e.path) or "body") + ": " + e.message


def ingest_event(store, body):
    """Devuelve (status_http, cuerpo): 202 aceptado · 200 duplicado · 400 inválido (AWS no reintenta)."""
    if not isinstance(body, dict):
        return 400, {"error": "BAD_REQUEST", "message": "Se esperaba el sobre común en JSON"}
    err = _first_error(_env_validator, body)
    if err:
        return 400, {"error": "BAD_REQUEST", "message": "sobre: " + err}
    if body["type"] != "antenna.event":
        return 400, {"error": "BAD_REQUEST", "message": "type debe ser antenna.event"}
    p = body["payload"]
    err = _first_error(_payload_validator, p)
    if err:
        return 400, {"error": "BAD_REQUEST", "message": "payload: " + err}
    if p["zone_id"] not in domain.ZONES:
        return 400, {"error": "BAD_REQUEST", "message": "payload.zone_id: zona desconocida"}
    if domain.ANTENNA_ZONE.get(p["antenna_id"], p["zone_id"]) != p["zone_id"]:
        log.warning("antena %s no pertenece a %s según el catálogo", p["antenna_id"], p["zone_id"])
    try:
        detected = clock.normalize(p["detected_at"])
        first = clock.normalize(p["first_anomaly_at"]) if p.get("first_anomaly_at") else None
        clock.parse(body["occurred_at"])
    except ValueError:
        return 400, {"error": "BAD_REQUEST", "message": "fecha ISO-8601 inválida"}
    doc = {
        **p, "id": body["event_id"], "event_id": body["event_id"], "occurred_at": detected,
        "first_anomaly_at": first, "envelope": {k: body[k] for k in ("type", "schema_version", "source",
                                                                     "occurred_at", "correlation_id")},
        "received_at": clock.iso(), "ttl": TTL_SECONDS,
    }
    if not store.create("infra_events", doc):
        return 200, {"status": "duplicate", "event_id": body["event_id"]}
    return 202, {"status": "accepted", "event_id": body["event_id"]}


def _typed_incident(store, ev, queue):
    """PREVENTIVA y ESTRUCTURAL crean (o actualizan) una incidencia propia por antena. No notifican a usuarios."""
    itype = ev["event_type"]
    zone = ev["zone_id"]
    inc = store.find_open_typed_incident(zone, itype, ev["antenna_id"])
    now = clock.iso()
    if inc is None:
        inc_id = f'INC-{zone}-{itype}-{ev["antenna_id"]}-{int(clock.parse(ev["occurred_at"]).timestamp())}'
        inc = {
            "id": inc_id, "incident_id": inc_id, "incident_type": itype, "zone_id": zone, "corr_group": None,
            "status": "REGISTRADA", "detected_by": "ANTENA", "origin": "ANTENA", "users_affected": 0, "report_count": 0,
            "priority_score": 0, "priority_level": "BAJA", "priority_factors": {}, "priority_override": None,
            "antenna_ids": [ev["antenna_id"]], "first_report_at": None, "first_anomaly_at": ev.get("first_anomaly_at"),
            "detection_lag_s": None, "critical_site": False, "ai": None, "ai_source": None, "opened_at": now,
            "resolved_at": None, "closed_at": None, "updated_at": now,
            "prediction": ev.get("prediction"), "recurrence": ev.get("recurrence"), "source_event_id": ev["id"],
        }
        if store.create("incidents", inc):
            store.add_history(inc_id, None, "REGISTRADA", "SISTEMA", "INCIDENT_OPENED",
                              f'{itype} {ev["antenna_id"]} (severidad {ev.get("severity")})')
    else:
        inc.update({"prediction": ev.get("prediction") or inc.get("prediction"),
                    "recurrence": ev.get("recurrence") or inc.get("recurrence"), "updated_at": now})
        store.upsert("incidents", inc)
        store.add_history(inc["id"], inc["status"], inc["status"], "SISTEMA", "INFRA_EVENT",
                          f'{itype} {ev["antenna_id"]} actualizado')
    inc = priority.recompute(store, inc["id"], zone)
    if queue is not None and inc:
        export.enqueue_export(store, queue, inc["id"], zone)
    return inc


def link_event(store, ev, queue=None):
    """Change feed de infra_events: relaciona el evento con incidencias abiertas de la zona (RF-AZ-15/16).
    Devuelve los incident_id cuya orquestación debe recibir la señal CONFIRMAR."""
    t = ev["event_type"]
    if t in ("PREVENTIVA", "ESTRUCTURAL"):
        _typed_incident(store, ev, queue)
    signals = []
    for inc in store.open_incidents_in_zone(ev["zone_id"]):
        note = f'{t} {ev["antenna_id"]} -> {ev.get("new_state")} (severidad {ev.get("severity")})'
        action = "RECOVERY_REPORTED" if t == "RECUPERACION" else "INFRA_EVENT"
        if t == "RECUPERACION":
            note += " · propone Solucionada"
        store.add_history(inc["id"], inc["status"], inc["status"], "SISTEMA", action, note)
        priority.recompute(store, inc["id"], ev["zone_id"])
        if queue is not None:
            export.enqueue_export(store, queue, inc["id"], ev["zone_id"])
        if t in domain.CONFIRMING_TYPES and inc["status"] in ("REGISTRADA", "EN_REVISION"):
            signals.append(inc["id"])
    return signals


def antenna_view(store, zone_id=None):
    """Proyección local del estado de antenas por zona (sin consultar a AWS en la ruta crítica)."""
    zones = [zone_id] if zone_id else list(domain.ZONES)
    since = clock.ago(hours=72)
    out = []
    for z in zones:
        evs = store.infra_events(z, since)
        last = {}
        for e in sorted(evs, key=lambda e: e["occurred_at"]):
            if e["event_type"] not in ("PREVENTIVA", "ESTRUCTURAL"):
                last[e["antenna_id"]] = e
        for a in domain.ZONES[z].get("antennas", []):
            e = last.get(a["antenna_id"])
            state = "NORMAL" if not e or e["event_type"] == "RECUPERACION" else e.get("new_state")
            out.append({"antenna_id": a["antenna_id"], "zone_id": z, "lat": a.get("lat"), "lng": a.get("lng"),
                        "state": state, "state_since": e["occurred_at"] if e else None,
                        "last_event": e["event_type"] if e else None})
    return out


def predictions_view(store, zone_id=None):
    """Riesgos predictivos vigentes conocidos por Azure (eventos PREVENTIVA no vencidos)."""
    zones = [zone_id] if zone_id else list(domain.ZONES)
    now = clock.iso()
    out = []
    for z in zones:
        for e in store.infra_events(z, clock.ago(hours=48)):
            pr = e.get("prediction") or {}
            if e["event_type"] == "PREVENTIVA" and (not pr.get("valid_until") or clock.normalize(pr["valid_until"]) > now):
                out.append({"antenna_id": e["antenna_id"], "zone_id": z, **pr})
    return sorted(out, key=lambda p: -(p.get("probability") or 0))

"""Tipos de evento, severidades, ids deterministas y construcción del payload `antenna.event`."""
import uuid

from . import timeutil
from .classify import ANOMALOUS, CAIDA, INHABILITADA, NORMAL, SATURADA

EVENT_TYPES = ("FALLA", "SATURACION", "DEGRADACION", "RECUPERACION", "PREVENTIVA", "ESTRUCTURAL", "PROLONGADA")
SEVERITIES = ("INFO", "BAJA", "MEDIA", "ALTA", "CRITICA")
# Espacio de nombres fijo del proyecto: cambiarlo cambia todos los event_id.
NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://pulsored.telecorp/aws/events")

SEVERITY_BY_STATE = {INHABILITADA: "CRITICA", CAIDA: "ALTA", SATURADA: "MEDIA", NORMAL: "INFO"}


def event_id(antenna_id: str, event_type: str, new_state: str, ts: str) -> str:
    """uuid5 determinista: reintentar nunca crea un id nuevo (los receptores deduplican)."""
    return str(uuid.uuid5(NAMESPACE, f"{antenna_id}|{event_type}|{new_state}|{ts}"))


def batch_event_id(kind: str, keys) -> str:
    """Id determinista de un lote (telemetría): depende solo de las muestras que contiene."""
    return str(uuid.uuid5(NAMESPACE, kind + "|" + "|".join(sorted(keys))))


def episode_id(antenna_id: str, first_anomaly_at: str) -> str:
    return f"EP-{antenna_id}-{timeutil.epoch(first_anomaly_at)}"


def type_for_state(new_state: str) -> str:
    return "SATURACION" if new_state == SATURADA else "FALLA"


def severity_for(event_type: str, new_state: str, probability: float = None, base: str = None) -> str:
    if event_type == "RECUPERACION":
        return "INFO"
    if event_type == "DEGRADACION":
        return "MEDIA"
    if event_type == "PREVENTIVA":
        return "ALTA" if (probability or 0) >= 0.90 else "MEDIA"
    if event_type == "ESTRUCTURAL":
        return "ALTA"
    if event_type == "PROLONGADA":
        return bump(base or SEVERITY_BY_STATE.get(new_state, "MEDIA"))
    return SEVERITY_BY_STATE.get(new_state, "MEDIA")


def bump(severity: str) -> str:
    """Sube un nivel (tope CRITICA)."""
    return SEVERITIES[min(SEVERITIES.index(severity) + 1, len(SEVERITIES) - 1)]


def from_transition(previous: str, new: str):
    """(event_type, starts_episode) de una transición confirmada de estado."""
    if new == NORMAL:
        return "RECUPERACION", False
    return type_for_state(new), previous == NORMAL


def sort_key(ts: str, eid: str) -> str:
    return f"{ts}#{eid}"


def item_for(ev: dict) -> dict:
    """Fila de la tabla AntennaEvents a partir del dict de un evento."""
    item = dict(ev)
    item["sk"] = sort_key(ev["ts"], ev["event_id"])
    return item


def payload_from(ev: dict) -> dict:
    """Payload `antenna.event` (contracts/events/antenna-event.schema.json) a partir de un evento."""
    p = {"antenna_id": ev["antenna_id"], "zone_id": ev["zone_id"], "episode_id": ev["episode_id"],
         "event_type": ev["event_type"], "severity": ev["severity"], "new_state": ev["new_state"],
         "detected_at": ev.get("detected_at") or ev["ts"]}
    for key in ("previous_state", "first_anomaly_at", "prediction", "recurrence", "elapsed_minutes"):
        if ev.get(key) is not None:
            p[key] = ev[key]
    if ev.get("gb_per_min") is not None:
        p["gb_per_min"] = float(ev["gb_per_min"])
    return p


def is_anomalous(state: str) -> bool:
    return state in ANOMALOUS

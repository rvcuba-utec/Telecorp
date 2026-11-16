"""Reglas de negocio puras (sin I/O). Catálogos tomados de `contracts/` (copiados en `pulsored/data/`)."""
import json
import math
import os

_DATA = os.path.join(os.path.dirname(__file__), "data")
with open(os.path.join(_DATA, "enums.json"), encoding="utf-8") as _f:
    ENUMS = json.load(_f)
with open(os.path.join(_DATA, "zones.json"), encoding="utf-8") as _f:
    _CATALOG = json.load(_f)

ZONES = {z["zone_id"]: z for z in _CATALOG["zones"]}
ZONES_VERSION = _CATALOG.get("zones_version")
ANTENNA_ZONE = {a["antenna_id"]: z["zone_id"] for z in _CATALOG["zones"] for a in z.get("antennas", [])}

CATEGORIES = ENUMS["category"]                      # SIN_INTERNET, INTERNET_LENTO, SIN_SENAL, LLAMADAS, SMS, OTRO
STATES = ENUMS["status"]                            # REGISTRADA … CERRADA
STATUS_LABELS = ENUMS["status_labels"]
INCIDENT_TYPES = ENUMS["incident_type"]             # COLECTIVA, INFRAESTRUCTURA, PREVENTIVA, ESTRUCTURAL
EVENT_TYPES = ENUMS["event_type"]                   # 7 tipos de AWS

# Grupo de categoría: solo se usa en el modo ZONA_Y_CATEGORIA
CATEGORY_GROUP = {
    "SIN_INTERNET": "DATOS", "INTERNET_LENTO": "DATOS",
    "SIN_SENAL": "VOZ", "LLAMADAS": "VOZ", "SMS": "VOZ",
    "OTRO": "OTRO",
}


def correlation_group(category: str, mode: str) -> str:
    """Clave con la que se agrupan reportes: 'ZONA' (cualquier categoría) o el grupo de la categoría."""
    return "ZONA" if mode == "ZONA" else CATEGORY_GROUP.get(category, "OTRO")


TRANSITIONS = {
    "REGISTRADA": {"EN_REVISION", "CERRADA"},
    "EN_REVISION": {"EN_ATENCION", "CERRADA"},
    "EN_ATENCION": {"SOLUCIONADA"},
    "SOLUCIONADA": {"CERRADA", "EN_REVISION"},  # EN_REVISION = reapertura
    "CERRADA": set(),
}


def can_transition(frm: str, to: str) -> bool:
    return to in TRANSITIONS.get(frm, set())


# ---------------------------------------------------------------- evidencia de antenas
STATE_WEIGHT = {"INHABILITADA": 1.0, "CAIDA": 0.7, "SATURADA": 0.5}
ANOMALY_TYPES = {"FALLA", "SATURACION", "DEGRADACION", "PROLONGADA"}   # abren/mantienen un episodio
CONFIRMING_TYPES = {"FALLA", "SATURACION", "PROLONGADA"}              # confirman la incidencia
DEGRADATION_WEIGHT = 0.3   # propuesta: intermitencia (no está fijado en los contratos)


def event_weight(e) -> float:
    """Factor A de la prioridad para un evento (PLAN §4.5). PREVENTIVA/ESTRUCTURAL/RECUPERACION = 0."""
    t = e.get("event_type")
    if t in ("FALLA", "PROLONGADA"):
        return STATE_WEIGHT.get(e.get("new_state"), 0.7)
    if t == "SATURACION":
        return 0.5
    if t == "DEGRADACION":
        return DEGRADATION_WEIGHT
    return 0.0


def antenna_evidence(events):
    """Proyección del estado de antenas a partir de sus eventos, ordenados por `occurred_at` (tolera desorden).
    Un episodio se abre con FALLA/SATURACION/DEGRADACION/PROLONGADA y se cierra con RECUPERACION."""
    by = {}
    for e in sorted(events, key=lambda e: e["occurred_at"]):
        by.setdefault(e["antenna_id"], []).append(e)
    active = {}
    for ant, evs in by.items():
        start, cur = None, None
        for e in evs:
            t = e["event_type"]
            if t in ANOMALY_TYPES:
                if cur is None:
                    start = e.get("first_anomaly_at") or e["occurred_at"]
                cur = e
            elif t == "RECUPERACION":
                start, cur = None, None
        if cur is not None:
            active[ant] = (cur, start)
    return {
        "weight": max((event_weight(c) for c, _ in active.values()), default=0.0),
        "confirmed": any(c["event_type"] in CONFIRMING_TYPES for c, _ in active.values()),
        "antenna_ids": sorted(active),
        "antennas": [{"antenna_id": a, "state": c.get("new_state"), "event_type": c["event_type"], "since": s}
                     for a, (c, s) in sorted(active.items())],
        "first_anomaly_at": min((s for _, s in active.values()), default=None),
    }


def episodes_count(events) -> int:
    """Episodios distintos de falla/saturación (base de la recurrencia R) + lo que diga un ESTRUCTURAL."""
    eps = {e.get("episode_id") or e["id"] for e in events if e["event_type"] in ("FALLA", "SATURACION")}
    structural = max(((e.get("recurrence") or {}).get("episodes_30d") or 0 for e in events
                      if e["event_type"] == "ESTRUCTURAL"), default=0)
    return max(len(eps), structural)


# ---------------------------------------------------------------- prioridad
ZONE_WEIGHT = {
    "SALUD": 1.0, "CRITICA": 1.0, "EDUCATIVA": 0.8,
    "INDUSTRIAL": 0.7, "COMERCIAL": 0.5, "RESIDENCIAL": 0.4,
}
PRIORITY_LEVELS = ["BAJA", "MEDIA", "ALTA", "CRITICA"]


def priority_level(score: int) -> str:
    if score >= 75:
        return "CRITICA"
    if score >= 50:
        return "ALTA"
    if score >= 25:
        return "MEDIA"
    return "BAJA"


def compute_priority(users, zone_type, critical_site, antenna_weight, hours_open, recurrence):
    """score = 40·U + 25·Z + 20·A + 10·D + 5·R  (PLAN.md §4.5). Devuelve score, nivel y factores explicables."""
    u = min(1.0, math.log10(max(users, 1)) / 4) if users else 0.0
    z = min(1.0, ZONE_WEIGHT.get(zone_type, 0.4) + (0.2 if critical_site else 0.0))
    a = float(antenna_weight)
    d = min(1.0, max(hours_open, 0) / 6)
    r = min(1.0, recurrence / 5)
    score = int(round(40 * u + 25 * z + 20 * a + 10 * d + 5 * r))
    return {
        "score": score,
        "level": priority_level(score),
        "factors": {"U": round(u, 3), "Z": round(z, 3), "A": round(a, 3), "D": round(d, 3), "R": round(r, 3)},
    }


# ---------------------------------------------------------------- sitios críticos (reglas de respaldo de la IA)
CRITICAL_KEYWORDS = {
    "HOSPITAL": ("hospital", "clínica", "clinica", "posta médica", "posta medica", "ambulancia", "emergencia"),
    "COLEGIO": ("colegio", "escuela", "universidad", "instituto"),
    "INDUSTRIA": ("planta", "fábrica", "fabrica", "industria", "almacén", "almacen", "puerto", "aeropuerto"),
    "SEGURIDAD": ("comisaría", "comisaria", "bomberos", "policía", "policia"),
}


def critical_site_kind(texts):
    blob = " ".join(t.lower() for t in texts if t)
    for kind, words in CRITICAL_KEYWORDS.items():
        if any(w in blob for w in words):
            return kind
    return None


def detect_critical_site(texts) -> bool:
    return critical_site_kind(texts) is not None


# ---------------------------------------------------------------- zonas e IDs
def nearest_zone(lat: float, lng: float):
    """Coordenadas -> zona más cercana por centroide (RF-AZ-02)."""
    best, dist = None, None
    for z in ZONES.values():
        c = z.get("centroid") or {}
        if "lat" not in c:
            continue
        d = (c["lat"] - lat) ** 2 + ((c["lng"] - lng) * math.cos(math.radians(lat))) ** 2
        if dist is None or d < dist:
            best, dist = z["zone_id"], d
    return best


def zone_from_id(entity_id: str) -> str:
    """REP-<zona>-<hash> / INC-<zona>-<...>: la zona (partición de Cosmos) viaja dentro del id.
    Las zonas tienen guiones (LIM-SURCO), así que se busca el prefijo más largo que sea una zona conocida."""
    if not entity_id or "-" not in entity_id:
        return ""
    rest = entity_id.split("-", 1)[1]
    match = ""
    for z in ZONES:
        if rest.startswith(z + "-") and len(z) > len(match):
            match = z
    return match


def zone_of_report_id(report_id: str) -> str:
    return zone_from_id(report_id) if report_id.startswith("REP-") else ""


def zone_of_incident_id(incident_id: str) -> str:
    return zone_from_id(incident_id) if incident_id.startswith("INC-") else ""

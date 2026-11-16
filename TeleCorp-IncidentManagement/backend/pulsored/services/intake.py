"""POST /api/reports: validación, idempotencia, límite de tasa y registro (RF-AZ-01/02/03/07)."""
import base64
import hashlib

from jsonschema import Draft202012Validator

from .. import clock, config, domain

REPORT_SCHEMA = {
    "type": "object",
    "required": ["idempotency_key", "user_document", "phone", "occurred_at", "category", "description"],
    "additionalProperties": False,
    "properties": {
        "idempotency_key": {"type": "string", "minLength": 8, "maxLength": 64},
        "user_document": {"type": "string", "pattern": r"^[A-Za-z0-9]{6,15}$"},
        "phone": {"type": "string", "pattern": r"^\+?[0-9]{7,15}$"},
        "email": {"type": "string", "maxLength": 120, "pattern": r"^[^@\s]+@[^@\s]+\.[^@\s]+$"},
        "zone_id": {"type": "string", "maxLength": 40},
        "location": {
            "type": "object",
            "required": ["lat", "lng"],
            "additionalProperties": False,
            "properties": {"lat": {"type": "number", "minimum": -90, "maximum": 90},
                           "lng": {"type": "number", "minimum": -180, "maximum": 180}},
        },
        "occurred_at": {"type": "string", "maxLength": 40},
        "category": {"enum": domain.CATEGORIES},
        "description": {"type": "string", "minLength": 3, "maxLength": 1000},
    },
}
_validator = Draft202012Validator(REPORT_SCHEMA)


def user_key(document: str, phone: str) -> str:
    """Identidad seudónima del usuario (se exporta a GCP en lugar del documento/teléfono)."""
    return hashlib.sha256(f"{document.strip().upper()}|{phone.strip()}".encode()).hexdigest()[:24]


def report_id_for(zone_id: str, idempotency_key: str) -> str:
    """Determinista: el mismo idempotency_key siempre produce el mismo id. Formato REP-<zona>-<16>."""
    digest = hashlib.sha256(idempotency_key.encode()).digest()
    return f"REP-{zone_id}-" + base64.b32encode(digest)[:16].decode()


def resolve_zone(body):
    """Zona explícita o, si no viene, la más cercana a las coordenadas (RF-AZ-02)."""
    if body.get("zone_id"):
        return body["zone_id"]
    loc = body.get("location")
    return domain.nearest_zone(loc["lat"], loc["lng"]) if loc else None


def validate(body):
    errors = sorted(_validator.iter_errors(body), key=lambda e: list(e.path))
    if errors:
        e = errors[0]
        loc = ".".join(str(p) for p in e.path) or "body"
        return f"{loc}: {e.message}"
    zone = resolve_zone(body)
    if not zone:
        return "zone_id: indica la zona o tu ubicación (location)"
    if zone not in domain.ZONES:
        return "zone_id: zona desconocida"
    try:
        occurred = clock.parse(body["occurred_at"])
    except ValueError:
        return "occurred_at: fecha ISO-8601 inválida"
    if occurred > clock.now() and (occurred - clock.now()).total_seconds() > 600:
        return "occurred_at: no puede estar en el futuro"
    return None


def register_report(store, body):
    """Devuelve (status_http, cuerpo)."""
    if not isinstance(body, dict):
        return 400, {"error": "BAD_REQUEST", "message": "Se esperaba un objeto JSON"}
    err = validate(body)
    if err:
        return 400, {"error": "BAD_REQUEST", "message": err}
    zone = resolve_zone(body)
    rid = report_id_for(zone, body["idempotency_key"])
    if store.get("reports", rid, zone):                       # reenvío del mismo reporte
        return 200, {"report_id": rid, "status": "REGISTRADA", "duplicate": True}

    ukey = user_key(body["user_document"], body["phone"])
    if store.count_reports_by_user_since(ukey, clock.ago(hours=1)) >= config.rate_limit_per_hour():
        return 429, {"error": "RATE_LIMITED", "message": "Demasiados reportes en la última hora. Intenta más tarde.",
                     "retry_after_s": 600}

    now = clock.iso()
    doc = {
        "id": rid, "report_id": rid, "zone_id": zone, "user_key": ukey,
        "user_document": body["user_document"], "phone": body["phone"], "email": body.get("email"),
        "location": body.get("location"), "zone_source": "declarada" if body.get("zone_id") else "coordenadas",
        "occurred_at": clock.normalize(body["occurred_at"]), "created_at": now, "updated_at": now,
        "category": body["category"], "corr_group": domain.correlation_group(body["category"], config.correlation_mode()),
        "description": body["description"], "status": "REGISTRADA", "incident_id": None,
        "ai": None, "idempotency_key": body["idempotency_key"],
    }
    if not store.create("reports", doc):
        return 200, {"report_id": rid, "status": "REGISTRADA", "duplicate": True}
    store.add_history(rid, None, "REGISTRADA", "USUARIO", "REPORT_CREATED", "Reporte recibido")
    return 202, {"report_id": rid, "status": "REGISTRADA", "zone_id": zone}

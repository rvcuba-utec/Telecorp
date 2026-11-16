"""Validación manual de los payloads (equivale a contracts/events/*.json; los tests lo comprueban)."""
import math
import re

from . import timeutil

ANTENNA_RE = re.compile(r"^ANT-LIM-\d{3}$")
STATES = ("NORMAL", "CAIDA", "INHABILITADA", "SATURADA")
PRED_STATES = ("CAIDA", "INHABILITADA", "SATURADA")
ENVELOPE_TYPES = ("telemetry.batch", "antenna.event", "prediction.batch", "incident.upsert", "report.upsert")
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
MAX_SAMPLES = 500


class ValidationError(Exception):
    """Sobre o payload inválido: el llamante recibe 400 y no debe reintentar."""


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def validate_envelope(env, expected_type: str) -> dict:
    if not isinstance(env, dict):
        raise ValidationError("el cuerpo debe ser un objeto JSON")
    required = ("event_id", "type", "schema_version", "source", "occurred_at", "correlation_id", "payload")
    missing = [k for k in required if k not in env]
    if missing:
        raise ValidationError("faltan campos del sobre: " + ", ".join(missing))
    extra = [k for k in env if k not in required]
    if extra:
        raise ValidationError("campos no permitidos en el sobre: " + ", ".join(extra))
    if not isinstance(env["event_id"], str) or not UUID_RE.match(env["event_id"]):
        raise ValidationError("event_id debe ser un uuid")
    if env["type"] not in ENVELOPE_TYPES:
        raise ValidationError("type desconocido")
    if env["type"] != expected_type:
        raise ValidationError(f"type debe ser {expected_type}")
    if not isinstance(env["schema_version"], str) or not re.match(r"^\d+\.\d+$", env["schema_version"]):
        raise ValidationError("schema_version inválido")
    if env["source"] not in ("aws", "azure", "gcp"):
        raise ValidationError("source inválido")
    try:
        timeutil.parse(env["occurred_at"])
    except (ValueError, TypeError):
        raise ValidationError("occurred_at inválido")
    if not isinstance(env["correlation_id"], str) or not env["correlation_id"]:
        raise ValidationError("correlation_id inválido")
    if not isinstance(env["payload"], dict):
        raise ValidationError("payload debe ser un objeto")
    return env["payload"]


def telemetry_payload(payload: dict) -> list:
    """Valida la forma del lote (1..500 muestras) y devuelve la lista. Las filas se validan una a una."""
    extra = [k for k in payload if k != "samples"]
    if extra:
        raise ValidationError("campos no permitidos en el payload: " + ", ".join(extra))
    samples = payload.get("samples")
    if not isinstance(samples, list) or not 1 <= len(samples) <= MAX_SAMPLES:
        raise ValidationError(f"samples debe ser una lista de 1 a {MAX_SAMPLES} elementos")
    return samples


def check_sample(s) -> str:
    """Devuelve None si la fila es válida, o el motivo de rechazo (aceptación parcial)."""
    if not isinstance(s, dict):
        return "la muestra debe ser un objeto"
    extra = [k for k in s if k not in ("antenna_id", "zone_id", "ts", "gb_per_min", "state")]
    if extra:
        return "campos no permitidos: " + ", ".join(extra)
    for k in ("antenna_id", "ts", "gb_per_min"):
        if k not in s:
            return f"falta {k}"
    if not isinstance(s["antenna_id"], str) or not ANTENNA_RE.match(s["antenna_id"]):
        return "antenna_id inválido"
    try:
        timeutil.parse(s["ts"])
    except (ValueError, TypeError):
        return "ts inválido (ISO-8601 con zona horaria)"
    if not _num(s["gb_per_min"]):
        return "gb_per_min debe ser un número"
    if s["gb_per_min"] < 0:
        return "gb_per_min no puede ser negativo"
    if "state" in s and s["state"] not in STATES:
        return "state inválido"
    if "zone_id" in s and not isinstance(s["zone_id"], str):
        return "zone_id inválido"
    return None


def prediction_payload(payload: dict):
    extra = [k for k in payload if k not in ("model_version", "generated_at", "predictions")]
    if extra:
        raise ValidationError("campos no permitidos en el payload: " + ", ".join(extra))
    if not isinstance(payload.get("model_version"), str) or not payload["model_version"]:
        raise ValidationError("model_version inválido")
    try:
        generated = timeutil.norm(payload.get("generated_at"))
    except (ValueError, TypeError):
        raise ValidationError("generated_at inválido")
    preds = payload.get("predictions")
    if not isinstance(preds, list) or len(preds) > MAX_SAMPLES:
        raise ValidationError(f"predictions debe ser una lista de hasta {MAX_SAMPLES}")
    return payload["model_version"], generated, preds


def check_prediction(p, now_iso: str) -> str:
    if not isinstance(p, dict):
        return "la predicción debe ser un objeto"
    extra = [k for k in p if k not in ("antenna_id", "zone_id", "probability", "predicted_state",
                                       "horizon_minutes", "valid_until")]
    if extra:
        return "campos no permitidos: " + ", ".join(extra)
    for k in ("antenna_id", "probability", "predicted_state", "horizon_minutes", "valid_until"):
        if k not in p:
            return f"falta {k}"
    if not isinstance(p["antenna_id"], str) or not ANTENNA_RE.match(p["antenna_id"]):
        return "antenna_id inválido"
    if not _num(p["probability"]) or not 0 <= p["probability"] <= 1:
        return "probability debe estar entre 0 y 1"
    if p["predicted_state"] not in PRED_STATES:
        return "predicted_state debe ser CAIDA, INHABILITADA o SATURADA"
    h = p["horizon_minutes"]
    if isinstance(h, bool) or not isinstance(h, int) or h < 1:
        return "horizon_minutes debe ser un entero > 0"
    try:
        if timeutil.norm(p["valid_until"]) <= now_iso:
            return "valid_until ya venció"
    except (ValueError, TypeError):
        return "valid_until inválido"
    return None


def antenna_event_payload(p: dict) -> None:
    """Comprueba un payload `antenna.event` saliente; lanza ValidationError si no cumple el contrato."""
    from .events import EVENT_TYPES, SEVERITIES
    required = ("antenna_id", "zone_id", "episode_id", "event_type", "severity", "new_state", "detected_at")
    for k in required:
        if p.get(k) in (None, ""):
            raise ValidationError(f"falta {k}")
    if not ANTENNA_RE.match(p["antenna_id"]):
        raise ValidationError("antenna_id inválido")
    if p["event_type"] not in EVENT_TYPES:
        raise ValidationError("event_type inválido")
    if p["severity"] not in SEVERITIES:
        raise ValidationError("severity inválida")
    for k in ("new_state", "previous_state"):
        if k in p and p[k] not in STATES:
            raise ValidationError(f"{k} inválido")
    for k in ("detected_at", "first_anomaly_at"):
        if k in p:
            timeutil.parse(p[k])
    allowed = set(required) | {"previous_state", "gb_per_min", "first_anomaly_at", "prediction", "recurrence",
                               "elapsed_minutes"}
    extra = set(p) - allowed
    if extra:
        raise ValidationError("campos no permitidos: " + ", ".join(sorted(extra)))

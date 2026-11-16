"""Reloj inyectable: permite pruebas deterministas y timestamps ISO uniformes (comparables como texto)."""
from datetime import datetime, timedelta, timezone

_fixed = None


def now() -> datetime:
    return _fixed or datetime.now(timezone.utc)


def iso(dt: datetime = None) -> str:
    return (dt or now()).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def normalize(s: str) -> str:
    """Cualquier ISO-8601 de entrada -> formato uniforme de almacenamiento."""
    return iso(parse(s))


def ago(**kw) -> str:
    return iso(now() - timedelta(**kw))


# --- solo para pruebas ---
def set_fixed(dt):
    global _fixed
    _fixed = dt


def advance(**kw):
    global _fixed
    _fixed = (_fixed or datetime.now(timezone.utc)) + timedelta(**kw)

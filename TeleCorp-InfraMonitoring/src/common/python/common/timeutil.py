"""Fechas: todo en UTC, formato ISO-8601 con 'Z' y precisión de segundos."""
from datetime import datetime, timedelta, timezone

FMT = "%Y-%m-%dT%H:%M:%SZ"


def now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def parse(value) -> datetime:
    """Interpreta un date-time ISO-8601 con zona horaria. Lanza ValueError si no es válido."""
    if not isinstance(value, str) or len(value) < 20 or len(value) > 40:
        raise ValueError("date-time inválido")
    text = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        raise ValueError("falta la zona horaria")
    return dt.astimezone(timezone.utc).replace(microsecond=0)


def fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(FMT)


def norm(value) -> str:
    return fmt(parse(value))


def epoch(value) -> int:
    dt = parse(value) if isinstance(value, str) else value
    return int(dt.timestamp())


def minutes_between(a: str, b: str) -> float:
    return (parse(b) - parse(a)).total_seconds() / 60.0


def plus(value: str, **kw) -> str:
    return fmt(parse(value) + timedelta(**kw))

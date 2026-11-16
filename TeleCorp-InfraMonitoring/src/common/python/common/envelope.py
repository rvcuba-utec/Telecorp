"""Sobre común de mensajes entre nubes (contracts/events/envelope.schema.json)."""
import uuid

from . import timeutil

SCHEMA_VERSION = "1.0"


def make(type_: str, payload: dict, event_id: str = None, correlation_id: str = None, occurred_at: str = None) -> dict:
    return {
        "event_id": event_id or str(uuid.uuid4()),
        "type": type_,
        "schema_version": SCHEMA_VERSION,
        "source": "aws",
        "occurred_at": occurred_at or timeutil.fmt(timeutil.now()),
        "correlation_id": correlation_id or str(uuid.uuid4()),
        "payload": payload,
    }

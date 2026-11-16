"""Secrets Manager: un único secreto JSON {inbound:{...}, outbound:{...}} con caché de 5 min."""
import hmac
import json
import time

from . import aws

TTL = 300
_cache = {"at": 0.0, "value": None}


def get_secrets(force: bool = False) -> dict:
    if not force and _cache["value"] is not None and time.time() - _cache["at"] < TTL:
        return _cache["value"]
    raw = aws.client("secretsmanager").get_secret_value(SecretId=aws.env("SECRETS_NAME"))["SecretString"]
    _cache.update(at=time.time(), value=json.loads(raw))
    return _cache["value"]


def caller_for_key(key: str):
    """Devuelve el nombre del llamante dueño de la clave (telemetry|gcp|azure|operator) o None."""
    if not key:
        return None
    found = None
    for name, candidate in get_secrets().get("inbound", {}).items():
        if isinstance(candidate, str) and candidate and hmac.compare_digest(candidate.encode(), key.encode()):
            found = name
    return found


def outbound_key(dest: str) -> str:
    return get_secrets().get("outbound", {}).get(f"{dest}_api_key", "")


def reset_cache():
    _cache.update(at=0.0, value=None)

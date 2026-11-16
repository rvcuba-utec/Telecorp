"""Utilidades HTTP independientes del framework (probables sin Azure)."""
import hmac
import json

from . import config


def _header(headers, name):
    if not headers:
        return ""
    return headers.get(name) or headers.get(name.lower()) or ""


def _match(got, expected):
    return bool(expected) and bool(got) and hmac.compare_digest(got, expected)


def operator_ok(headers) -> bool:
    return _match(_header(headers, "x-operator-token"), config.env("OPERATOR_TOKEN"))


def inbound_key_ok(headers) -> bool:
    """Clave con la que AWS llama a Azure (header X-API-Key = AZURE_INBOUND_API_KEY)."""
    return _match(_header(headers, "x-api-key"), config.env("AZURE_INBOUND_API_KEY"))


def unauthorized():
    return 401, {"error": "UNAUTHORIZED", "message": "Token de operador requerido"}


def dumps(body) -> str:
    return json.dumps(body, default=str, ensure_ascii=False)

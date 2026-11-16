"""Respuestas HTTP y permisos por llamante (el authorizer valida la clave; cada Lambda valida la ruta)."""
import base64
import json

from . import ddb

PERMISSIONS = {
    "telemetry": {"POST /telemetry"},
    "gcp": {"POST /predictions", "GET /predictions", "GET /config", "GET /antennas*"},
    "azure": {"GET /antennas*", "GET /predictions", "GET /config"},
    "operator": {"*"},
}


def allowed(caller: str, route: str) -> bool:
    """`route` = 'GET /antennas/{antenna_id}' (routeKey). Los prefijos con * admiten cualquier sufijo."""
    for perm in PERMISSIONS.get(caller or "", ()):
        if perm == "*" or perm == route:
            return True
        if perm.endswith("*"):
            method, _, prefix = perm.partition(" ")
            rmethod, _, rpath = route.partition(" ")
            if method == rmethod and rpath.startswith(prefix[:-1]):
                return True
    return False


def caller_of(event: dict):
    return ((event.get("requestContext") or {}).get("authorizer") or {}).get("lambda", {}).get("caller")


def response(status: int, body=None, headers: dict = None) -> dict:
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    return {"statusCode": status, "headers": h, "body": json.dumps(ddb.native(body if body is not None else {}))}


def forbidden(route):
    return response(403, {"error": "Forbidden", "message": f"la clave no permite {route}"})


def check(event: dict):
    """Devuelve None si la llamada está permitida, o la respuesta 403."""
    route = event.get("routeKey") or ""
    return None if allowed(caller_of(event), route) else forbidden(route)


def json_body(event: dict):
    raw = event.get("body")
    if raw is None:
        raise ValueError("falta el cuerpo")
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode()
    return json.loads(raw)

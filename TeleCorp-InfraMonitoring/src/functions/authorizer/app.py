"""Lambda authorizer (REQUEST, formato 2.0, respuesta simple). Solo identifica al llamante por su X-API-Key.
Los permisos por ruta los comprueba cada Lambda con `auth.check` (el resultado del authorizer se cachea por clave)."""
from common import log, secrets


def handler(event, context):
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    try:
        caller = secrets.caller_for_key(headers.get("x-api-key", ""))
    except Exception as exc:  # noqa: BLE001
        log.error("authorizer no pudo leer el secreto", error=str(exc))
        raise
    if not caller:
        return {"isAuthorized": False}
    return {"isAuthorized": True, "context": {"caller": caller}}

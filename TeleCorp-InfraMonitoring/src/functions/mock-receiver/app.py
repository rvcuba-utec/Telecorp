"""Receptor simulado de Azure y GCP (T4): permite probar todo AWS sin las otras nubes.

POST /mock/azure/events/infra · /mock/gcp/ingest/events · /mock/gcp/ingest/telemetry  (clave outbound del destino)
GET  /mock/{azure|gcp}/received?limit=N                                              (clave de operador)
POST /mock/mode  {"fail": true|false, "status": 503}  -> simula un receptor caído   (clave de operador)
Es idempotente por event_id, igual que debe ser el receptor real.
"""
import json
from datetime import timedelta

from common import auth, aws, ddb, secrets, timeutil

DESTS = {"azure": ("/events/infra",), "gcp": ("/ingest/events", "/ingest/telemetry")}
MODE = {"key": "mock"}


def _key(event):
    return {k.lower(): v for k, v in (event.get("headers") or {}).items()}.get("x-api-key", "")


def _is_operator(event):
    return secrets.caller_for_key(_key(event)) == "operator"


def handler(event, context):
    method = event["requestContext"]["http"]["method"]
    parts = event["rawPath"].strip("/").split("/")  # ['mock', dest, ...]
    if parts == ["mock", "mode"] and method == "POST":
        if not _is_operator(event):
            return auth.response(401, {"error": "Unauthorized"})
        body = auth.json_body(event)
        ddb.put(aws.table("CONTROL"), {"key": "mock", "fail": bool(body.get("fail")),
                                       "status": int(body.get("status", 503))})
        return auth.response(200, {"fail": bool(body.get("fail"))})
    if len(parts) < 3 or parts[1] not in DESTS:
        return auth.response(404, {"error": "NotFound"})
    dest, path = parts[1], "/" + "/".join(parts[2:])
    if method == "GET" and path == "/received":
        if not _is_operator(event):
            return auth.response(401, {"error": "Unauthorized"})
        limit = int((event.get("queryStringParameters") or {}).get("limit", 50))
        rows = [r for r in ddb.scan(aws.table("MOCKINBOX")) if r["dest"] == dest]
        rows.sort(key=lambda r: r["received_at"], reverse=True)
        return auth.response(200, {"count": len(rows), "items": [
            {k: r[k] for k in ("event_id", "type", "path", "received_at", "summary")} for r in rows[:limit]]})
    if method != "POST" or path not in DESTS[dest]:
        return auth.response(404, {"error": "NotFound"})
    if _key(event) != secrets.outbound_key(dest) or not _key(event):
        return auth.response(401, {"error": "Unauthorized"})
    mode = ddb.get(aws.table("CONTROL"), MODE) or {}
    if mode.get("fail"):
        return auth.response(int(mode.get("status", 503)), {"error": "ServiceUnavailable", "message": "receptor simulado caído"})
    try:
        body = auth.json_body(event)
        eid, typ = body["event_id"], body["type"]
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return auth.response(400, {"error": "BadRequest"})
    p = body.get("payload", {})
    summary = (f"{p.get('event_type')} {p.get('antenna_id')} {p.get('new_state')}" if typ == "antenna.event"
               else f"{len(p.get('samples', []))} muestras")
    now = timeutil.now()
    try:
        ddb.put(aws.table("MOCKINBOX"), {"pk": f"{dest}:{path}:{eid}", "dest": dest, "event_id": eid, "type": typ,
                                         "path": path, "received_at": timeutil.fmt(now), "summary": summary,
                                         "expire_at": timeutil.epoch(now + timedelta(days=3))},
                condition="attribute_not_exists(pk)")
    except Exception as exc:  # noqa: BLE001
        if ddb.error_code(exc) == "ConditionalCheckFailedException":
            return auth.response(200, {"status": "duplicate", "event_id": eid})
        raise
    return auth.response(202, {"status": "accepted", "event_id": eid})

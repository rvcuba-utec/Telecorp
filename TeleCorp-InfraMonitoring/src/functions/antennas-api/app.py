"""GET /antennas, /antennas/{id}, /antennas/{id}/metrics (RF-AWS-07)."""
from datetime import timedelta

from common import auth, aws, ddb, timeutil


def _shape(a: dict, pred: dict = None) -> dict:
    out = {"antenna_id": a["antenna_id"], "zone_id": a.get("zone_id"), "lat": a.get("lat"), "lng": a.get("lng"),
           "state": a.get("state", "NORMAL"), "state_since": a.get("state_since"), "last_seen": a.get("last_ts"),
           "last_gb_per_min": a.get("last_gb_per_min"), "episode_id": a.get("episode_id"), "prediction": pred}
    return out


def _valid_prediction(p: dict, now_iso: str):
    if not p or p.get("valid_until", "") <= now_iso:
        return None
    return {k: p.get(k) for k in ("probability", "predicted_state", "horizon_minutes", "valid_until",
                                  "generated_at", "model_version")}


def handler(event, context):
    denied = auth.check(event)
    if denied:
        return denied
    route = event["routeKey"]
    qs = event.get("queryStringParameters") or {}
    now_iso = timeutil.fmt(timeutil.now())
    if route == "GET /antennas":
        if qs.get("zone_id"):
            rows = ddb.query(aws.table("ANTENNAS"), "zone_id = :z", {":z": qs["zone_id"]}, index="zone-index")
        else:
            rows = ddb.scan(aws.table("ANTENNAS"))
        if qs.get("state"):
            rows = [r for r in rows if r.get("state", "NORMAL") == qs["state"]]
        rows.sort(key=lambda r: r["antenna_id"])
        return auth.response(200, {"generated_at": now_iso, "antennas": [_shape(r) for r in rows]})
    antenna_id = (event.get("pathParameters") or {}).get("antenna_id", "")
    a = ddb.get(aws.table("ANTENNAS"), {"antenna_id": antenna_id})
    if not a:
        return auth.response(404, {"error": "NotFound", "message": "antena inexistente"})
    if route == "GET /antennas/{antenna_id}":
        pred = _valid_prediction(ddb.get(aws.table("PREDICTIONS"), {"antenna_id": antenna_id}), now_iso)
        return auth.response(200, _shape(a, pred))
    try:
        minutes = int(qs.get("minutes", 60))
    except ValueError:
        return auth.response(400, {"error": "BadRequest", "message": "minutes debe ser un entero"})
    if not 1 <= minutes <= 4320:
        return auth.response(400, {"error": "BadRequest", "message": "minutes debe estar entre 1 y 4320"})
    since = timeutil.fmt(timeutil.now() - timedelta(minutes=minutes))
    rows = ddb.query(aws.table("TELEMETRY"), "antenna_id = :a AND ts >= :s", {":a": antenna_id, ":s": since})
    return auth.response(200, {"antenna_id": antenna_id, "samples": [
        {"ts": r["ts"], "gb_per_min": r["gb_per_min"], "state": r["state"]} for r in rows]})

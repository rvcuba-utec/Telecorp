"""GET /predictions — predicciones vigentes, por probabilidad descendente (RF-AWS-22)."""
from common import auth, aws, ddb, timeutil


def handler(event, context):
    denied = auth.check(event)
    if denied:
        return denied
    qs = event.get("queryStringParameters") or {}
    try:
        min_p = float(qs.get("min_probability", 0.5))
    except ValueError:
        return auth.response(400, {"error": "BadRequest", "message": "min_probability inválido"})
    now_iso = timeutil.fmt(timeutil.now())
    table = aws.table("PREDICTIONS")
    if qs.get("antenna_id"):
        one = ddb.get(table, {"antenna_id": qs["antenna_id"]})
        rows = [one] if one else []
    elif qs.get("zone_id"):
        rows = ddb.query(table, "zone_id = :z", {":z": qs["zone_id"]}, index="zone-index")
    else:
        rows = ddb.scan(table)
    rows = [r for r in rows if r["valid_until"] > now_iso and float(r["probability"]) >= min_p]
    rows.sort(key=lambda r: -float(r["probability"]))
    latest = max(rows, key=lambda r: r["generated_at"]) if rows else None
    preds = [{k: r.get(k) for k in ("antenna_id", "zone_id", "probability", "predicted_state", "horizon_minutes",
                                    "valid_until")} for r in rows]
    return auth.response(200, {"model_version": latest["model_version"] if latest else None,
                               "generated_at": latest["generated_at"] if latest else now_iso, "predictions": preds})

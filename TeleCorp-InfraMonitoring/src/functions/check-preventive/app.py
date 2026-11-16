"""SF preventive-alert: ¿es elegible? ¿ya hay una alerta vigente?"""
from common import alerts, aws, config, ddb, timeutil


def handler(event, context):
    cfg = config.get_config()
    now_iso = timeutil.fmt(timeutil.now())
    a = ddb.get(aws.table("ANTENNAS"), {"antenna_id": event["antenna_id"]}, consistent=True)
    if not a:
        return {"eligible": False, "reason": "antena desconocida", "now": now_iso}
    ok, reason = alerts.eligible(event, a, cfg, now_iso)
    out = {"eligible": ok, "reason": reason, "now": now_iso, "valid_until": timeutil.norm(event["valid_until"])}
    if ok:
        out["event"] = alerts.preventive_event(event, ddb.native(a))
    return ddb.native(out)

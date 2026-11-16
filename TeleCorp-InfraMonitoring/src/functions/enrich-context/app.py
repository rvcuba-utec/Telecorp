"""SF lifecycle, paso 1: zona, antenas vecinas, episodios en 30 días y parámetros de espera."""
from common import aws, config, ddb, recurrence, timeutil


def handler(event, context):
    cfg = config.get_config()
    zone = ddb.get(aws.table("ZONES"), {"zone_id": event["zone_id"]}) or {"zone_id": event["zone_id"]}
    neighbors = [{"antenna_id": a["antenna_id"], "state": a.get("state", "NORMAL")}
                 for a in ddb.query(aws.table("ANTENNAS"), "zone_id = :z", {":z": event["zone_id"]}, index="zone-index")
                 if a["antenna_id"] != event["antenna_id"]]
    _, e30 = recurrence.count_episodes(event["antenna_id"], timeutil.fmt(timeutil.now()))
    return ddb.native({
        "zone": {k: zone.get(k) for k in ("zone_id", "name", "type", "critical_sites")},
        "neighbors": neighbors, "episodes_30d": e30,
        "config": {"check_interval_s": cfg["CHECK_INTERVAL_S"], "prolonged_after_min": cfg["PROLONGED_AFTER_MIN"],
                   "max_episode_min": cfg["MAX_EPISODE_HOURS"] * 60}})

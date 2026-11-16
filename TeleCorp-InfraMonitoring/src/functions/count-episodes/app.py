"""SF recurrence-analysis: cuenta episodios 7 d / 30 d y decide si la antena es ESTRUCTURAL (§3.4)."""
from datetime import timedelta

from common import aws, config, ddb, recurrence, timeutil


def handler(event, context):
    ant = event["antenna"]
    cfg = config.get_config()
    ov = event.get("overrides") or {}
    ts = timeutil.norm(event["ts"])
    e7, e30 = recurrence.count_episodes(ant["antenna_id"], ts)
    a = ddb.get(aws.table("ANTENNAS"), {"antenna_id": ant["antenna_id"]}) or {}
    out = {"antenna_id": ant["antenna_id"], "episodes_7d": e7, "episodes_30d": e30, "structural": False}
    if a.get("structural_until") and a["structural_until"] > ts:
        out["reason"] = "ya marcada como estructural"
    elif e7 >= ov.get("episodes_7d_min", cfg["RECURRENCE_7D"]) or e30 >= ov.get("episodes_30d_min", cfg["RECURRENCE_30D"]):
        state = a.get("state", "NORMAL")
        out.update(structural=True, reason="recurrencia",
                   structural_until=timeutil.fmt(timeutil.parse(ts) + timedelta(days=cfg["STRUCTURAL_REEMIT_DAYS"])),
                   event={"event_type": "ESTRUCTURAL", "antenna_id": ant["antenna_id"],
                          "zone_id": ant["zone_id"], "episode_id": f"STR-{ant['antenna_id']}-{timeutil.epoch(ts)}",
                          "previous_state": state, "new_state": state, "gb_per_min": a.get("last_gb_per_min"),
                          "ts": ts, "recurrence": {"episodes_7d": e7, "episodes_30d": e30}})
    else:
        out["reason"] = "sin recurrencia"
    return ddb.native(out)

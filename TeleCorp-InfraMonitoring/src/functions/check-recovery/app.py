"""SF lifecycle: ¿la antena volvió a NORMAL? ¿cuánto lleva el episodio?"""
from common import aws, ddb, emitter, timeutil


def handler(event, context):
    now_iso = timeutil.fmt(timeutil.now())
    a = ddb.get(aws.table("ANTENNAS"), {"antenna_id": event["antenna_id"]}, consistent=True) or {}
    # recuperada = vuelve a NORMAL, o ya empezó otro episodio (el anterior necesariamente terminó)
    recovered = a.get("state", "NORMAL") == "NORMAL" or a.get("episode_id") != event["episode_id"]
    recovery = None
    if recovered:
        rows = ddb.query(aws.table("ANTENNAEVENTS"), "antenna_id = :a AND sk >= :s",
                         {":a": event["antenna_id"], ":s": event["first_anomaly_at"], ":e": event["episode_id"],
                          ":t": "RECUPERACION"}, filter_expr="episode_id = :e AND event_type = :t", forward=False)
        if rows:
            recovery = {k: rows[0].get(k) for k in emitter.ITEM_FIELDS if rows[0].get(k) is not None}
    return ddb.native({"recovered": recovered, "state": a.get("state", "NORMAL"), "now": now_iso,
                       "elapsed_min": int(timeutil.minutes_between(event["first_anomaly_at"], now_iso)),
                       "recovery_event": recovery})

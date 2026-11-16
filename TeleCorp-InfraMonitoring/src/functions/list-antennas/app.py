"""SF recurrence-analysis: lista las antenas y pasa los umbrales opcionales (bajarlos en la demo)."""
from common import aws, ddb


def handler(event, context):
    rows = ddb.scan(aws.table("ANTENNAS"), projection="antenna_id, zone_id")
    overrides = {k: event[k] for k in ("episodes_7d_min", "episodes_30d_min") if isinstance(event.get(k), int)}
    return {"antennas": sorted(({"antenna_id": r["antenna_id"], "zone_id": r["zone_id"]} for r in rows),
                               key=lambda r: r["antenna_id"]), "overrides": overrides}

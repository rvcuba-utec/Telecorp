"""Simulador (RF-AWS-08): se ejecuta cada minuto; si está apagado termina al instante."""
import random
from collections import Counter

from common import aws, config, ddb, ingest, log, simulation, timeutil


def handler(event, context, rng=None):
    ctrl = ddb.get(aws.table("CONTROL"), {"key": "simulator"}) or {}
    if not ctrl.get("active"):
        return {"active": False}
    cfg = config.get_config()
    rng = rng or random.Random()
    antennas = sorted(a["antenna_id"] for a in ddb.scan(aws.table("ANTENNAS"), projection="antenna_id"))
    if not antennas:
        log.warn("el catálogo de antenas está vacío: ejecuta tools/seed_catalog.py")
        return {"active": True, "samples": 0}
    ctrl = ddb.native(ctrl)
    samples, scenarios = simulation.tick(timeutil.now(), antennas, ctrl, bool(cfg["DEMO_MODE"]), rng)
    result = ingest.process(samples, "simulator")
    ddb.update(aws.table("CONTROL"), {"key": "simulator"}, sets={"scenarios": scenarios, "last_tick": samples[0]["ts"]})
    states = Counter(a.get("state", "NORMAL") for a in ddb.scan(aws.table("ANTENNAS"), projection="#s",
                                                                   names={"#s": "state"}))
    for st in ("NORMAL", "CAIDA", "INHABILITADA", "SATURADA"):
        log.metric("AntennasByState", states.get(st, 0), State=st)
    return {"active": True, "samples": len(samples), "accepted": result["accepted"],
            "events": [e["event_type"] for e in result["events"]]}

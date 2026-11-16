"""/simulator/start|stop|scenario|status — solo clave de operador."""
import json
import random

from common import auth, aws, config, ddb, simulation, timeutil

KEY = {"key": "simulator"}


def _bad(msg):
    return auth.response(400, {"error": "BadRequest", "message": msg})


def _int(body, name, default, lo=0, hi=1440):
    v = body.get(name, default)
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise ValueError(f"{name} debe ser un entero entre {lo} y {hi}")
    return v


def _status():
    c = ddb.get(aws.table("CONTROL"), KEY) or {}
    return {"active": bool(c.get("active")), "auto": bool(c.get("auto")),
            "episode_rate_per_antenna_per_day": c.get("episode_rate_per_antenna_per_day", 0.10),
            "last_tick": c.get("last_tick"), "scenarios": c.get("scenarios", [])}


def _scenario(body: dict, rng):
    kind = body.get("type")
    if kind not in simulation.KINDS:
        return _bad("type debe ser uno de " + ", ".join(simulation.KINDS))
    catalog = {a["antenna_id"]: a["zone_id"] for a in ddb.scan(aws.table("ANTENNAS"), projection="antenna_id, zone_id")}
    ant, zone = body.get("antenna_id"), body.get("zone_id")
    if ant and ant not in catalog:
        return _bad("antena inexistente")
    if zone and zone not in set(catalog.values()):
        return _bad("zona inexistente")
    targets = [ant] if ant else sorted(a for a, z in catalog.items() if z == zone) if zone else []
    if kind == "NORMAL":  # cancela escenarios de las antenas indicadas (o todos)
        ctrl = ddb.native(ddb.get(aws.table("CONTROL"), KEY) or {})
        keep = [s for s in ctrl.get("scenarios", []) if targets and not set(s["targets"]) & set(targets)]
        ddb.update(aws.table("CONTROL"), KEY, sets={"scenarios": keep})
        return auth.response(200, {"cancelled": len(ctrl.get("scenarios", [])) - len(keep)})
    if kind == "APAGON_ZONA" and not zone:
        return _bad("APAGON_ZONA requiere zone_id")
    if not targets:
        return _bad("indica antenna_id o zone_id")
    to = body.get("to", simulation.DEFAULT_TO[kind])
    if to not in simulation.TO_STATES:
        return _bad("to debe ser CAIDA, INHABILITADA o SATURADA")
    demo = bool(config.get_config()["DEMO_MODE"])
    try:
        ramp = _int(body, "ramp_minutes", (4 if demo else 45) if kind == "DEGRADACION" else 0, 0, 240)
        hold = _int(body, "hold_minutes", 20, 1, 720)
    except ValueError as exc:
        return _bad(str(exc))
    recovery = body.get("recovery", "ABRUPTA")
    if recovery not in ("ABRUPTA", "GRADUAL"):
        return _bad("recovery debe ser ABRUPTA o GRADUAL")
    if kind in ("FALLA_ABRUPTA", "SATURACION", "APAGON_ZONA", "INTERMITENTE"):
        ramp = 0
    sc = simulation.new_scenario(kind, targets, to, ramp, hold, recovery, timeutil.fmt(timeutil.now()), rng)
    ctrl = ddb.native(ddb.get(aws.table("CONTROL"), KEY) or {})
    ddb.update(aws.table("CONTROL"), KEY, sets={"scenarios": ctrl.get("scenarios", []) + [sc]})
    note = None if ctrl.get("active") else "el simulador está apagado: usa POST /simulator/start"
    return auth.response(200, {"scenario_id": sc["id"], "antennas": targets, "note": note})


def handler(event, context, rng=None):
    denied = auth.check(event)
    if denied:
        return denied
    route = event["routeKey"]
    if route == "GET /simulator/status":
        return auth.response(200, _status())
    if route == "POST /simulator/stop":
        ddb.update(aws.table("CONTROL"), KEY, sets={"active": False, "scenarios": []})
        return auth.response(200, _status())
    try:
        body = auth.json_body(event) if event.get("body") else {}
    except (ValueError, json.JSONDecodeError):
        return _bad("el cuerpo no es JSON válido")
    if not isinstance(body, dict):
        return _bad("el cuerpo debe ser un objeto")
    if route == "POST /simulator/start":
        rate = body.get("episode_rate_per_antenna_per_day", 0.10)
        if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not 0 <= rate <= 10:
            return _bad("episode_rate_per_antenna_per_day debe estar entre 0 y 10")
        ddb.update(aws.table("CONTROL"), KEY, sets={"active": True, "auto": bool(body.get("auto", False)),
                                                    "episode_rate_per_antenna_per_day": rate})
        return auth.response(200, _status())
    return _scenario(body, rng or random.Random())

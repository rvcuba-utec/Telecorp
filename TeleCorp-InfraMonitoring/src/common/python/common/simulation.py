"""Generador de muestras del simulador (§8). Todo es determinista si se pasa un `random.Random` con semilla."""
import random
import uuid
from datetime import timedelta

from . import timeutil

BASE_MEAN, BASE_SD, BASE_MIN, BASE_MAX = 95.0, 8.0, 65.0, 125.0
NOISE_SD = 3.0
KINDS = ("FALLA_ABRUPTA", "DEGRADACION", "SATURACION", "APAGON_ZONA", "INTERMITENTE", "NORMAL")
TO_STATES = ("CAIDA", "INHABILITADA", "SATURADA")
DEFAULT_TO = {"FALLA_ABRUPTA": "INHABILITADA", "DEGRADACION": "CAIDA", "SATURACION": "SATURADA",
              "APAGON_ZONA": "INHABILITADA", "INTERMITENTE": "CAIDA"}
GRADUAL_MIN = 10


def base_value(rng) -> float:
    return min(BASE_MAX, max(BASE_MIN, rng.gauss(BASE_MEAN, BASE_SD)))


def target_for(state: str, rng) -> float:
    return {"CAIDA": lambda: rng.uniform(25, 55), "INHABILITADA": lambda: 0.0,
            "SATURADA": lambda: rng.uniform(135, 170)}[state]()


def new_scenario(kind: str, antennas: list, to: str, ramp: int, hold: int, recovery: str, now_iso: str, rng) -> dict:
    return {"id": uuid.UUID(int=rng.getrandbits(128)).hex[:12], "type": kind, "antennas": list(antennas), "to": to,
            "ramp_minutes": int(ramp), "hold_minutes": int(hold), "recovery": recovery, "started_at": now_iso,
            "seed": rng.randrange(1, 10 ** 9), "targets": {a: round(target_for(to, rng), 2) for a in antennas}}


def _total_minutes(sc) -> float:
    if sc["type"] == "INTERMITENTE":
        return sc["hold_minutes"]
    return sc["ramp_minutes"] + sc["hold_minutes"] + (GRADUAL_MIN if sc["recovery"] == "GRADUAL" else 0)


def expired(sc: dict, now) -> bool:
    return (now - timeutil.parse(sc["started_at"])).total_seconds() / 60.0 >= _total_minutes(sc)


def _intermittent_bad(sc, age: float) -> bool:
    rng, t, bad = random.Random(sc["seed"]), 0, True
    while True:
        length = rng.choice((1, 2))
        if age < t + length:
            return bad
        t += length
        bad = not bad


def value_at(sc: dict, antenna_id: str, now, base: float, rng):
    """Valor del escenario para la antena en `now`, o None si no afecta en este instante."""
    if antenna_id not in sc["targets"]:
        return None
    age = (now - timeutil.parse(sc["started_at"])).total_seconds() / 60.0
    target = float(sc["targets"][antenna_id])
    if age < 0 or expired(sc, now):
        return None
    if sc["type"] == "INTERMITENTE":
        if not _intermittent_bad(sc, age):
            return None
        return 0.0 if target == 0 else round(min(target + rng.gauss(0, NOISE_SD), 59.0) if sc["to"] == "CAIDA"
                                             else max(target + rng.gauss(0, NOISE_SD), 131.0), 2)
    ramp, hold = sc["ramp_minutes"], sc["hold_minutes"]
    if age < ramp:
        v = base + (target - base) * (age / ramp) + rng.gauss(0, NOISE_SD)
        return round(max(0.1, v), 2)
    if age < ramp + hold:
        if target == 0:
            return 0.0
        v = target + rng.gauss(0, NOISE_SD)
        if sc["to"] == "CAIDA":
            v = min(v, 59.0)
        elif sc["to"] == "SATURADA":
            v = max(v, 131.0)
        return round(max(0.1, v), 2)
    frac = (age - ramp - hold) / GRADUAL_MIN  # recuperación GRADUAL
    return round(max(0.1, target + (base - target) * frac + rng.gauss(0, NOISE_SD)), 2)


def auto_episode(antenna_id: str, demo: bool, now_iso: str, rng) -> dict:
    ramped = rng.random() < 0.8
    ramp = (rng.randint(3, 5) if demo else rng.randint(30, 90)) if ramped else 0
    hold = rng.randint(8, 12) if demo else rng.randint(15, 60)
    to = rng.choices(TO_STATES, weights=(0.5, 0.2, 0.3))[0]
    return new_scenario("AUTO", [antenna_id], to, ramp, hold, rng.choice(("ABRUPTA", "GRADUAL")), now_iso, rng)


def tick(now, antennas: list, control: dict, demo: bool, rng):
    """Genera una muestra por antena para el minuto `now`. Devuelve (muestras, escenarios_vigentes)."""
    now = now.replace(second=0, microsecond=0)
    now_iso = timeutil.fmt(now)
    scenarios = [s for s in control.get("scenarios", []) if not expired(s, now)]
    if control.get("auto"):
        busy = {a for s in scenarios for a in s["targets"]}
        p = float(control.get("episode_rate_per_antenna_per_day", 0.10)) / 1440.0
        for a in antennas:
            if a not in busy and rng.random() < p:
                scenarios.append(auto_episode(a, demo, now_iso, rng))
    samples = []
    for a in antennas:
        base = base_value(rng)
        value = None
        for sc in reversed(scenarios):  # el escenario más reciente manda
            value = value_at(sc, a, now, base, rng)
            if value is not None:
                break
        samples.append({"antenna_id": a, "ts": now_iso, "gb_per_min": base if value is None else value})
    for s in samples:
        s["gb_per_min"] = round(s["gb_per_min"], 2)
    return samples, scenarios

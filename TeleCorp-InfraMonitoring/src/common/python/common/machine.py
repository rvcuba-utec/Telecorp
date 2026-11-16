"""Máquina de confirmación por antena (RF-AWS-09) y detección de intermitencia. Funciones puras.

Una muestra aislada nunca cambia el estado confirmado: hacen falta N consecutivas
(N=2 CAIDA/INHABILITADA, N=3 recuperación, N=5 saturación sostenida).
"""
from . import events as ev
from . import timeutil
from .classify import ANOMALOUS, NORMAL

# Atributos de `Antennas` que la máquina lee y escribe.
FIELDS = ("state", "state_since", "last_ts", "last_gb_per_min", "candidate_state", "candidate_count",
          "candidate_since", "transitions", "last_raw", "episode_id", "episode_started_at", "first_anomaly_at",
          "last_flap_at")


def required_samples(raw: str, cfg: dict) -> int:
    if raw == NORMAL:
        return int(cfg["RECOVERY_SAMPLES"])
    if raw == "SATURADA":
        return int(cfg["CONFIRM_SAMPLES_SATURATED"])
    return int(cfg["CONFIRM_SAMPLES"])


def initial_state(antenna: dict) -> dict:
    s = {k: antenna.get(k) for k in FIELDS}
    s["state"] = s["state"] or NORMAL
    s["transitions"] = list(s["transitions"] or [])
    s["candidate_count"] = int(s["candidate_count"] or 0)
    return s


def _event(s: dict, antenna: dict, kind: str, previous: str, new: str, ts: str, gb: float, **extra) -> dict:
    e = {"antenna_id": antenna["antenna_id"], "zone_id": antenna.get("zone_id"), "ts": ts,
         "event_id": ev.event_id(antenna["antenna_id"], kind, new, ts), "episode_id": s["episode_id"],
         "event_type": kind, "severity": ev.severity_for(kind, new), "previous_state": previous,
         "new_state": new, "gb_per_min": gb, "first_anomaly_at": s["first_anomaly_at"],
         "starts_episode": False}
    e.update(extra)
    return e


def step(s: dict, antenna: dict, ts: str, raw: str, gb: float, cfg: dict, detected_at: str = None):
    """Aplica UNA muestra (en orden de `ts`). Modifica `s` y devuelve la lista de eventos generados.

    Las muestras con ts <= last_ts se ignoran (lo decide el llamante; aquí se protege igualmente).
    """
    if s["last_ts"] and ts <= s["last_ts"]:
        return []
    out = []
    detected_at = detected_at or ts
    raw_anom = raw in ANOMALOUS

    # --- intermitencia: transiciones crudas NORMAL <-> anómalo ---
    if s["last_raw"] is not None and (s["last_raw"] in ANOMALOUS) != raw_anom:
        s["transitions"] = (s["transitions"] + [ts])[-10:]
    s["last_raw"] = raw
    window = [t for t in s["transitions"] if timeutil.minutes_between(t, ts) <= cfg["FLAP_WINDOW_MIN"]]
    if len(window) >= cfg["FLAP_TRANSITIONS"] and (
            not s["last_flap_at"] or timeutil.minutes_between(s["last_flap_at"], ts) >= cfg["FLAP_WINDOW_MIN"]):
        ep = s["episode_id"] or ev.episode_id(antenna["antenna_id"], window[0])
        out.append(_event({**s, "episode_id": ep, "first_anomaly_at": s["first_anomaly_at"] or window[0]},
                          antenna, "DEGRADACION", s["state"], s["state"], ts, gb, detected_at=detected_at))
        s["last_flap_at"] = ts

    # --- confirmación ---
    if raw == s["state"]:
        s["candidate_state"], s["candidate_count"], s["candidate_since"] = None, 0, None
    else:
        if raw == s["candidate_state"]:
            s["candidate_count"] += 1
        else:
            s["candidate_state"], s["candidate_count"], s["candidate_since"] = raw, 1, ts
        if s["candidate_count"] >= required_samples(raw, cfg):
            previous, since = s["state"], s["candidate_since"]
            kind, starts = ev.from_transition(previous, raw)
            s["state"], s["state_since"] = raw, ts
            s["candidate_state"], s["candidate_count"], s["candidate_since"] = None, 0, None
            if starts:  # NORMAL -> anómalo: empieza un episodio
                s["first_anomaly_at"] = since
                s["episode_id"] = ev.episode_id(antenna["antenna_id"], since)
                s["episode_started_at"] = ts
            out.append(_event(s, antenna, kind, previous, raw, ts, gb, starts_episode=starts, detected_at=detected_at))
    s["last_ts"], s["last_gb_per_min"] = ts, gb
    return out

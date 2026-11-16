"""Regla de alerta preventiva (RF-AWS-21, §3.5) y construcción del evento PREVENTIVA."""
from . import events as ev
from . import timeutil


def eligible(pred: dict, antenna: dict, cfg: dict, now_iso: str):
    """(ok, motivo). pred usa probability, horizon_minutes, valid_until."""
    if float(pred["probability"]) < cfg["PRED_ALERT_MIN"]:
        return False, "probabilidad bajo el umbral"
    if int(pred["horizon_minutes"]) > cfg["PRED_HORIZON_MAX_MIN"]:
        return False, "horizonte fuera de rango"
    if timeutil.norm(pred["valid_until"]) <= now_iso:
        return False, "predicción vencida"
    until = (antenna or {}).get("preventive_until")
    if until and until > now_iso:
        return False, "ya hay una alerta vigente"
    return True, "elegible"


def preventive_event(pred: dict, antenna: dict) -> dict:
    """Evento PREVENTIVA listo para `emit-event`. ts = generated_at: el id es el mismo ante reintentos."""
    ts = timeutil.norm(pred["generated_at"])
    return {
        "event_type": "PREVENTIVA", "antenna_id": pred["antenna_id"], "zone_id": antenna.get("zone_id"),
        "episode_id": f"PRE-{pred['antenna_id']}-{timeutil.epoch(ts)}",
        "previous_state": antenna.get("state", "NORMAL"), "new_state": pred["predicted_state"],
        "gb_per_min": antenna.get("last_gb_per_min"), "ts": ts,
        "prediction": {"probability": float(pred["probability"]), "predicted_state": pred["predicted_state"],
                       "horizon_minutes": int(pred["horizon_minutes"]),
                       "valid_until": timeutil.norm(pred["valid_until"]),
                       "model_version": pred.get("model_version", "desconocido")},
        "severity": ev.severity_for("PREVENTIVA", pred["predicted_state"], float(pred["probability"])),
    }

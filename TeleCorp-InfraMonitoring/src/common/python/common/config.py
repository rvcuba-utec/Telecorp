"""Configuración global (SSM Parameter Store, /pulsored/config/*) con caché de 60 s.

DEFAULTS es la única fuente de los valores iniciales: tools/seed_catalog.py los siembra en SSM.
Si SSM no responde o falta un parámetro se usa el valor por defecto.
"""
import hashlib
import json
import os
import time

from . import aws

PREFIX = os.environ.get("CONFIG_PREFIX", "/pulsored/config")
ENDPOINT_PREFIX = os.environ.get("ENDPOINT_PREFIX", "/pulsored/endpoints")
TTL = 60

DEFAULTS = {
    "THRESHOLD_DOWN": 60, "THRESHOLD_SATURATED": 129,
    "CONFIRM_SAMPLES": 2, "CONFIRM_SAMPLES_SATURATED": 5, "RECOVERY_SAMPLES": 3,
    "FLAP_TRANSITIONS": 4, "FLAP_WINDOW_MIN": 30,
    "PROLONGED_AFTER_MIN": 30, "CHECK_INTERVAL_S": 60, "MAX_EPISODE_HOURS": 12,
    "PRED_SEND_MIN": 0.50, "PRED_ALERT_MIN": 0.70, "PRED_HORIZON_MAX_MIN": 1440, "PRED_HORIZON_MIN": 60,
    "RECURRENCE_7D": 3, "RECURRENCE_30D": 5, "STRUCTURAL_REEMIT_DAYS": 7,
    "TELEMETRY_TTL_DAYS": 3, "DEMO_MODE": False, "ZONES_VERSION": "1.0.0",
    # Reglas que usa Azure (incidencias colectivas); AWS es la fuente única de configuración.
    "USERS_THRESHOLD": 10, "WINDOW_HOURS": 24, "CORRELATION_MODE": "ZONA",
}
_cache = {"at": 0.0, "cfg": None, "ep": {}}


def _coerce(name, raw: str):
    default = DEFAULTS[name]
    if isinstance(default, bool):
        return raw.strip().lower() in ("1", "true", "yes", "si")
    if isinstance(default, int):
        return int(float(raw))
    if isinstance(default, float):
        return float(raw)
    return raw


def stringify(value) -> str:
    return str(value).lower() if isinstance(value, bool) else str(value)


def _load_prefix(prefix: str) -> dict:
    out, token = {}, None
    while True:
        kw = {"Path": prefix, "Recursive": True, "MaxResults": 10}
        if token:
            kw["NextToken"] = token
        r = aws.client("ssm").get_parameters_by_path(**kw)
        for p in r["Parameters"]:
            out[p["Name"].rsplit("/", 1)[-1]] = p["Value"]
        token = r.get("NextToken")
        if not token:
            return out


def get_config(force: bool = False) -> dict:
    if not force and _cache["cfg"] is not None and time.time() - _cache["at"] < TTL:
        return _cache["cfg"]
    cfg = dict(DEFAULTS)
    try:
        for k, raw in _load_prefix(PREFIX).items():
            if k in DEFAULTS:
                try:
                    cfg[k] = _coerce(k, raw)
                except ValueError:
                    pass
    except Exception:  # SSM caído o sin permiso: se usan los valores por defecto
        if _cache["cfg"] is not None:
            return _cache["cfg"]
    _cache.update(at=time.time(), cfg=cfg)
    return cfg


def config_version(cfg: dict) -> str:
    blob = json.dumps({k: cfg[k] for k in sorted(cfg)}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:8]


def get_endpoint(name: str) -> str:
    """URL base de `azure` o `gcp` (parámetro SSM, no secreto)."""
    hit = _cache["ep"].get(name)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    value = aws.client("ssm").get_parameter(Name=f"{ENDPOINT_PREFIX}/{name}")["Parameter"]["Value"]
    _cache["ep"][name] = (time.time(), value.rstrip("/"))
    return value.rstrip("/")


def reset_cache():
    _cache.update(at=0.0, cfg=None, ep={})

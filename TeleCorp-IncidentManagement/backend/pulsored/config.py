"""Configuración.

Orden de precedencia de cada valor:
  1. Configuración global de AWS (`GET {AWS_API_BASE}/config`, con caché) si trae la clave.
  2. Variable de entorno (app settings en Azure, o `.env` en local).
  3. Valor por defecto del código.
Las URLs y claves de otras nubes solo vienen del entorno (`.env.example` las documenta).
"""
import logging
import os
import time

log = logging.getLogger("pulsored.config")

_ROOT_ENV = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".env")


def load_dotenv(path=_ROOT_ENV):
    """Carga `.env` (formato KEY=VALUE) sin pisar variables ya definidas. Sin dependencias."""
    if os.environ.get("PULSORED_NO_DOTENV") or not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v


load_dotenv()


def env(name: str, default: str = "") -> str:
    v = os.environ.get(name)
    return default if v is None or v == "" else v


# ---------------------------------------------------------------- AWS /config (RF-AZ-33)
_cache = {"at": 0.0, "data": None}


def aws_config() -> dict:
    """Configuración global de AWS con caché. Si AWS no está configurado o falla: último valor conocido o {}."""
    base = env("AWS_API_BASE")
    if not base:
        return {}
    ttl = int(env("CONFIG_CACHE_S", "300"))
    if _cache["data"] is not None and time.time() - _cache["at"] < ttl:
        return _cache["data"]
    try:
        import requests
        r = requests.get(base.rstrip("/") + env("AWS_CONFIG_PATH", "/config"),
                         headers={"X-API-Key": env("AWS_API_KEY")}, timeout=float(env("AWS_TIMEOUT_S", "5")))
        r.raise_for_status()
        _cache["data"], _cache["at"] = r.json(), time.time()
    except Exception as e:  # noqa: BLE001 - degradación: caché o valores por defecto
        log.warning("GET /config de AWS no disponible (%s); uso caché/valores por defecto", e)
        _cache["at"] = time.time()          # no reintentar en cada llamada
        if _cache["data"] is None:
            _cache["data"] = {}
    return _cache["data"]


def reset_cache():
    _cache["at"], _cache["data"] = 0.0, None


def setting(name: str, default: str) -> str:
    items = (aws_config() or {}).get("items") or {}
    if name in items and items[name] not in (None, ""):
        return str(items[name])
    return env(name, default)


def config_info() -> dict:
    c = aws_config() or {}
    return {"source": "aws" if c else "local", "config_version": c.get("config_version"),
            "zones_version": c.get("zones_version")}


# ---------------------------------------------------------------- reglas
def users_threshold() -> int:
    return int(setting("USERS_THRESHOLD", "10"))


def window_hours() -> int:
    return int(setting("WINDOW_HOURS", "24"))


def correlation_mode() -> str:
    m = setting("CORRELATION_MODE", "ZONA").upper()
    return m if m in ("ZONA", "ZONA_Y_CATEGORIA") else "ZONA"


def evidence_window_hours() -> int:
    return int(setting("EVIDENCE_WINDOW_HOURS", "48"))


def rate_limit_per_hour() -> int:
    return int(setting("RATE_LIMIT_PER_HOUR", "5"))


def timers() -> dict:
    return {
        "operator_wait_min": int(setting("OPERATOR_WAIT_MIN", "120")),
        "recovery_poll_min": int(setting("RECOVERY_POLL_MIN", "5")),
        "cooldown_min": int(setting("COOLDOWN_MIN", "30")),
        "quiet_min": int(setting("RECOVERY_QUIET_MIN", "15")),
    }


def effective() -> dict:
    """Configuración efectiva (sin secretos), para la consola."""
    return {
        **config_info(),
        "USERS_THRESHOLD": users_threshold(), "WINDOW_HOURS": window_hours(),
        "CORRELATION_MODE": correlation_mode(), "EVIDENCE_WINDOW_HOURS": evidence_window_hours(),
        "RATE_LIMIT_PER_HOUR": rate_limit_per_hour(), **{k.upper(): v for k, v in timers().items()},
        "integrations": {
            "aws": bool(env("AWS_API_BASE")), "gcp": bool(env("GCP_API_BASE")),
            "email": bool(env("ACS_CONNECTION_STRING")), "sms": False,
        },
    }


COSMOS_DB = "pulsored"


def cosmos_db() -> str:
    return env("COSMOS_DB", COSMOS_DB)

"""Consultas de Azure a AWS (INT-06/07): `GET /antennas` y `GET /predictions` con `X-API-Key`.
No es ruta crítica: si AWS no está configurado o falla, se usa la proyección local (eventos ya recibidos)."""
import logging

import requests

from .. import config
from . import infra

log = logging.getLogger("pulsored.aws")


def _get(path_var, default_path, params):
    base = config.env("AWS_API_BASE")
    if not base:
        raise RuntimeError("AWS_API_BASE no configurado")
    r = requests.get(base.rstrip("/") + config.env(path_var, default_path),
                     params={k: v for k, v in params.items() if v},
                     headers={"X-API-Key": config.env("AWS_API_KEY")}, timeout=float(config.env("AWS_TIMEOUT_S", "5")))
    r.raise_for_status()
    return r.json()


def antennas(store, zone_id=None):
    try:
        data = _get("AWS_ANTENNAS_PATH", "/antennas", {"zone_id": zone_id})
        return {"source": "aws", "generated_at": data.get("generated_at"), "items": data.get("antennas", [])}
    except Exception as e:  # noqa: BLE001 - degradación: último estado conocido
        if config.env("AWS_API_BASE"):
            log.warning("GET /antennas de AWS no disponible (%s); uso la proyección local", e)
        return {"source": "local", "items": infra.antenna_view(store, zone_id)}


def predictions(store, zone_id=None):
    try:
        data = _get("AWS_PREDICTIONS_PATH", "/predictions", {"zone_id": zone_id})
        items = data.get("predictions", data.get("items", data if isinstance(data, list) else []))
        return {"source": "aws", "items": items}
    except Exception as e:  # noqa: BLE001
        if config.env("AWS_API_BASE"):
            log.warning("GET /predictions de AWS no disponible (%s); uso los eventos PREVENTIVA recibidos", e)
        return {"source": "local", "items": infra.predictions_view(store, zone_id)}

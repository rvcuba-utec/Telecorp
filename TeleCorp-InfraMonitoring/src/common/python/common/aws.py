"""Clientes boto3 perezosos y nombres de recursos (vienen de variables de entorno)."""
import os

import boto3
from botocore.config import Config

_CFG = Config(retries={"max_attempts": 4, "mode": "standard"}, connect_timeout=3, read_timeout=10)
_clients = {}


def client(service: str):
    if service not in _clients:
        _clients[service] = boto3.client(service, config=_CFG)
    return _clients[service]


def reset_clients():
    _clients.clear()


def env(name: str, default=None):
    value = os.environ.get(name, default)
    if value is None:
        raise RuntimeError(f"falta la variable de entorno {name}")
    return value


def table(name: str) -> str:
    """Nombre real de la tabla lógica (Zones, Antennas, Telemetry, AntennaEvents, Predictions, Rejects, Control, MockInbox)."""
    return env("TABLE_" + name.upper())

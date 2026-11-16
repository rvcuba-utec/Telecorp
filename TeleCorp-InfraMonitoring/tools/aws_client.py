"""Utilidades de las herramientas: sesión boto3, salidas del stack, claves y llamadas a la API."""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src" / "common" / "python"))
STACK = os.environ.get("STACK_NAME", "pulsored-aws")
REGION = os.environ.get("REGION", "us-east-1")
SECRETS_NAME = os.environ.get("SECRETS_NAME", "pulsored/secrets")
CONFIG_PREFIX = "/pulsored/config"
ENDPOINT_PREFIX = "/pulsored/endpoints"
KEYS_FILE = ROOT / ".local-env" / "claves.json"


def client(service):
    return boto3.client(service, region_name=REGION)


def outputs(stack=None) -> dict:
    r = client("cloudformation").describe_stacks(StackName=stack or STACK)["Stacks"][0]
    return {o["OutputKey"]: o["OutputValue"] for o in r.get("Outputs", [])}


def secrets() -> dict:
    return json.loads(client("secretsmanager").get_secret_value(SecretId=SECRETS_NAME)["SecretString"])


class Api:
    """Cliente mínimo de la API desplegada (sin dependencias)."""

    def __init__(self, base_url: str, keys: dict):
        self.base, self.keys = base_url.rstrip("/"), keys

    def call(self, method, path, key=None, body=None, timeout=30):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"}
        if key:
            headers["X-API-Key"] = self.keys.get(key, key)
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else {}), dict(r.headers)
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw), dict(e.headers)
            except ValueError:
                return e.code, {"raw": raw.decode(errors="replace")}, dict(e.headers)


def api() -> Api:
    return Api(outputs()["ApiUrl"], secrets()["inbound"])

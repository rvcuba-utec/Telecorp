#!/usr/bin/env python3
"""Crea (o actualiza) el secreto único `pulsored/secrets` en Secrets Manager.

  inbound  = claves que OTROS usan para llamar a AWS (las genera este script y se les entregan)
  outbound = claves que AWS usa para llamar a Azure y GCP (te las dan ellos)

Uso:
  python tools/bootstrap_secrets.py                       crea el secreto si no existe (idempotente)
  python tools/bootstrap_secrets.py --set azure=KEY gcp=KEY   guarda las claves outbound reales
  python tools/bootstrap_secrets.py --rotate telemetry    rota una clave inbound
Las claves inbound también se guardan en .local-env/claves.json (fuera de Git, permisos 600).
"""
import argparse
import json
import os
import secrets as pysecrets

import botocore.exceptions

from aws_client import KEYS_FILE, SECRETS_NAME, client, outputs

INBOUND = ("telemetry", "gcp", "azure", "operator")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", nargs="+", metavar="DEST=KEY", help="clave outbound: azure=... gcp=...")
    ap.add_argument("--rotate", nargs="+", choices=INBOUND, help="rota claves inbound")
    a = ap.parse_args()
    sm = client("secretsmanager")
    mock = outputs().get("MockEnabled") == "true"
    try:
        value = json.loads(sm.get_secret_value(SecretId=SECRETS_NAME)["SecretString"])
        exists = True
    except sm.exceptions.ResourceNotFoundException:
        exists, value = False, {"inbound": {k: pysecrets.token_urlsafe(32) for k in INBOUND},
                                "outbound": {"azure_api_key": ("mock-azure-" + pysecrets.token_urlsafe(16)) if mock else "",
                                             "gcp_api_key": ("mock-gcp-" + pysecrets.token_urlsafe(16)) if mock else ""}}
    changed = not exists
    for k in a.rotate or []:
        value["inbound"][k] = pysecrets.token_urlsafe(32)
        changed = True
    for pair in a.set or []:
        dest, _, key = pair.partition("=")
        if dest not in ("azure", "gcp") or not key:
            raise SystemExit("usa --set azure=KEY gcp=KEY")
        value["outbound"][f"{dest}_api_key"] = key
        changed = True
    if not exists:
        sm.create_secret(Name=SECRETS_NAME, SecretString=json.dumps(value),
                         Description="Claves de PulsoRed (inbound: otros -> AWS; outbound: AWS -> otros)")
        print(f"Secreto {SECRETS_NAME} creado.")
    elif changed:
        sm.put_secret_value(SecretId=SECRETS_NAME, SecretString=json.dumps(value))
        print(f"Secreto {SECRETS_NAME} actualizado.")
    else:
        print(f"Secreto {SECRETS_NAME} ya existe: sin cambios.")
    KEYS_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(KEYS_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"inbound": value["inbound"], "api_url": outputs().get("ApiUrl")}, f, indent=2)
    missing = [k for k, v in value["outbound"].items() if not v]
    print(f"Claves inbound guardadas en {KEYS_FILE.relative_to(KEYS_FILE.parents[1])} (no se imprimen).")
    if missing:
        print("Faltan claves outbound:", ", ".join(missing), "-> python tools/bootstrap_secrets.py --set azure=KEY gcp=KEY")


if __name__ == "__main__":
    main()

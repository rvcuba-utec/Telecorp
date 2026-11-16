#!/usr/bin/env python3
"""Carga el catálogo (Zones y Antennas desde contracts/zones.json), la configuración en SSM y los endpoints.

Idempotente: no pisa el estado de las antenas ni la configuración que ya cambiaste a mano.
  --reset-config            vuelve a escribir todos los valores por defecto
  --azure-url URL --gcp-url URL   apunta los outbox a Azure / GCP reales (por defecto: el receptor simulado)
"""
import argparse
import json

from aws_client import CONFIG_PREFIX, ENDPOINT_PREFIX, ROOT, client, outputs
from common import config as cfgmod

PENDING = "https://pendiente.invalid"


def put_param(ssm, name, value, overwrite):
    try:
        ssm.put_parameter(Name=name, Value=str(value), Type="String", Overwrite=overwrite)
        return True
    except ssm.exceptions.ParameterAlreadyExists:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset-config", action="store_true")
    ap.add_argument("--azure-url")
    ap.add_argument("--gcp-url")
    a = ap.parse_args()
    out = outputs()
    ddb = client("dynamodb")
    catalog = json.loads((ROOT / "contracts" / "zones.json").read_text())

    n_ant = 0
    for z in catalog["zones"]:
        ddb.put_item(TableName=out["ZonesTable"], Item={
            "zone_id": {"S": z["zone_id"]}, "name": {"S": z["name"]}, "type": {"S": z["type"]},
            "centroid": {"M": {"lat": {"N": str(z["centroid"]["lat"])}, "lng": {"N": str(z["centroid"]["lng"])}}},
            "critical_sites": {"L": [{"S": s} for s in z["critical_sites"]]}})
        for ant in z["antennas"]:
            ddb.update_item(  # no pisa el estado si la antena ya existe
                TableName=out["AntennasTable"], Key={"antenna_id": {"S": ant["antenna_id"]}},
                UpdateExpression="SET zone_id = :z, lat = :la, lng = :ln, #s = if_not_exists(#s, :n)",
                ExpressionAttributeNames={"#s": "state"},
                ExpressionAttributeValues={":z": {"S": z["zone_id"]}, ":la": {"N": str(ant["lat"])},
                                           ":ln": {"N": str(ant["lng"])}, ":n": {"S": "NORMAL"}})
            n_ant += 1
    print(f"Catálogo: {len(catalog['zones'])} zonas y {n_ant} antenas (zones_version {catalog['zones_version']}).")

    ssm = client("ssm")
    values = dict(cfgmod.DEFAULTS, ZONES_VERSION=catalog["zones_version"])
    created = sum(put_param(ssm, f"{CONFIG_PREFIX}/{k}", cfgmod.stringify(v), a.reset_config) for k, v in values.items())
    print(f"Configuración: {created} parámetros escritos de {len(values)} ({CONFIG_PREFIX}/*).")

    mock_url = out["ApiUrl"] + "/mock"
    use_mock = out.get("MockEnabled") == "true"
    for dest, url in (("azure", a.azure_url), ("gcp", a.gcp_url)):
        default = f"{mock_url}/{dest}" if use_mock else PENDING
        wrote = put_param(ssm, f"{ENDPOINT_PREFIX}/{dest}", url or default, bool(url))
        cur = ssm.get_parameter(Name=f"{ENDPOINT_PREFIX}/{dest}")["Parameter"]["Value"]
        print(f"Endpoint {dest}: {cur}" + (" (nuevo)" if wrote else ""))
    if not use_mock and not (a.azure_url and a.gcp_url):
        print("AVISO: sin receptor simulado; pasa --azure-url y --gcp-url cuando existan (los mensajes esperan en SQS).")


if __name__ == "__main__":
    main()

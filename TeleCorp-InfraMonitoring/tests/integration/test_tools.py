"""Las herramientas de despliegue (seed_catalog, bootstrap_secrets) contra AWS simulado."""
import json
import os
import sys

import boto3
import pytest
from moto import mock_aws

from support import ROOT

sys.path.insert(0, str(ROOT / "tools"))


@pytest.fixture()
def stack():
    with mock_aws():
        for t, k in (("zones", "zone_id"), ("antennas", "antenna_id")):
            boto3.client("dynamodb").create_table(
                TableName=t, BillingMode="PAY_PER_REQUEST", AttributeDefinitions=[{"AttributeName": k, "AttributeType": "S"}],
                KeySchema=[{"AttributeName": k, "KeyType": "HASH"}])
        template = {"Resources": {"T": {"Type": "AWS::SNS::Topic"}}, "Outputs": {
            "ApiUrl": {"Value": "https://abc.execute-api.us-east-1.amazonaws.com"}, "ZonesTable": {"Value": "zones"},
            "AntennasTable": {"Value": "antennas"}, "MockEnabled": {"Value": "true"}}}
        boto3.client("cloudformation").create_stack(StackName="pulsored-aws", TemplateBody=json.dumps(template))
        import aws_client
        yield aws_client


def test_bootstrap_secrets_idempotente_y_set(stack, tmp_path, monkeypatch, capsys):
    import bootstrap_secrets as bs
    monkeypatch.setattr(stack, "KEYS_FILE", tmp_path / "claves.json")
    monkeypatch.setattr(bs, "KEYS_FILE", tmp_path / "claves.json")
    monkeypatch.setattr(sys, "argv", ["x"])
    bs.main()
    first = stack.secrets()
    assert set(first["inbound"]) == {"telemetry", "gcp", "azure", "operator"} and all(first["inbound"].values())
    assert first["outbound"]["azure_api_key"].startswith("mock-azure-")
    bs.main()  # idempotente
    assert stack.secrets() == first
    monkeypatch.setattr(sys, "argv", ["x", "--set", "azure=REAL", "--rotate", "telemetry"])
    bs.main()
    after = stack.secrets()
    assert after["outbound"]["azure_api_key"] == "REAL" and after["inbound"]["telemetry"] != first["inbound"]["telemetry"]
    assert after["inbound"]["gcp"] == first["inbound"]["gcp"]
    assert oct((tmp_path / "claves.json").stat().st_mode)[-3:] == "600"
    assert json.loads((tmp_path / "claves.json").read_text())["inbound"] == after["inbound"]


def test_seed_catalog_carga_sin_pisar_estado(stack, monkeypatch):
    import seed_catalog as sc
    monkeypatch.setattr(sys, "argv", ["x"])
    sc.main()
    ddb = boto3.client("dynamodb")
    assert ddb.scan(TableName="antennas", Select="COUNT")["Count"] == 60
    assert ddb.scan(TableName="zones", Select="COUNT")["Count"] == 12
    ddb.update_item(TableName="antennas", Key={"antenna_id": {"S": "ANT-LIM-001"}},
                    UpdateExpression="SET #s = :c", ExpressionAttributeNames={"#s": "state"},
                    ExpressionAttributeValues={":c": {"S": "CAIDA"}})
    ssm = boto3.client("ssm")
    ssm.put_parameter(Name="/pulsored/config/PRED_ALERT_MIN", Value="0.9", Type="String", Overwrite=True)
    sc.main()  # segunda corrida: no pisa el estado ni la configuración
    assert ddb.get_item(TableName="antennas", Key={"antenna_id": {"S": "ANT-LIM-001"}})["Item"]["state"]["S"] == "CAIDA"
    assert ssm.get_parameter(Name="/pulsored/config/PRED_ALERT_MIN")["Parameter"]["Value"] == "0.9"
    assert ssm.get_parameter(Name="/pulsored/endpoints/azure")["Parameter"]["Value"].endswith("/mock/azure")
    pages = ssm.get_paginator("get_parameters_by_path").paginate(Path="/pulsored/config")
    cfg = {p["Name"].rsplit("/", 1)[1]: p["Value"] for pg in pages for p in pg["Parameters"]}
    assert len(cfg) == 23 and cfg["DEMO_MODE"] == "false"
    from common import config
    config.reset_cache()
    loaded = config.get_config()  # lee las 20 en varias páginas de 10
    assert loaded["PRED_ALERT_MIN"] == 0.9 and loaded["ZONES_VERSION"] == "1.0.0" and loaded["DEMO_MODE"] is False
    monkeypatch.setattr(sys, "argv", ["x", "--reset-config", "--azure-url", "https://azure.example", "--gcp-url", "https://gcp.example"])
    sc.main()
    assert ssm.get_parameter(Name="/pulsored/config/PRED_ALERT_MIN")["Parameter"]["Value"] == "0.7"
    assert ssm.get_parameter(Name="/pulsored/endpoints/gcp")["Parameter"]["Value"] == "https://gcp.example"

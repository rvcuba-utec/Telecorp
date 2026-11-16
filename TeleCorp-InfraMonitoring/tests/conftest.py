"""Fixtures comunes: AWS simulado con moto (DynamoDB, SQS, SSM, Secrets Manager) y carga de las Lambdas."""
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src" / "common" / "python"))
for k, v in {"AWS_ACCESS_KEY_ID": "test", "AWS_SECRET_ACCESS_KEY": "test", "AWS_SESSION_TOKEN": "test",
             "AWS_DEFAULT_REGION": "us-east-1", "AWS_REGION": "us-east-1"}.items():
    os.environ.setdefault(k, v)

ZONES = json.loads((ROOT / "contracts" / "zones.json").read_text())
KEYS = {"telemetry": "k-telemetry", "gcp": "k-gcp", "azure": "k-azure", "operator": "k-operator"}
OUTBOUND = {"azure_api_key": "out-azure", "gcp_api_key": "out-gcp"}


def _tables(ddb):
    def mk(name, hash_, range_=None, gsi=None, stream=False):
        attrs = {hash_: "S"}
        schema = [{"AttributeName": hash_, "KeyType": "HASH"}]
        if range_:
            attrs[range_] = "S"
            schema.append({"AttributeName": range_, "KeyType": "RANGE"})
        kw = {}
        if gsi:
            attrs[gsi] = "S"
            kw["GlobalSecondaryIndexes"] = [{"IndexName": "zone-index", "Projection": {"ProjectionType": "ALL"},
                                             "KeySchema": [{"AttributeName": gsi, "KeyType": "HASH"}]}]
        if stream:
            kw["StreamSpecification"] = {"StreamEnabled": True, "StreamViewType": "NEW_IMAGE"}
        ddb.create_table(TableName=name, BillingMode="PAY_PER_REQUEST",
                         AttributeDefinitions=[{"AttributeName": k, "AttributeType": t} for k, t in attrs.items()],
                         KeySchema=schema, **kw)
        os.environ["TABLE_" + name.upper()] = name
    mk("Zones", "zone_id")
    mk("Antennas", "antenna_id", gsi="zone_id")
    mk("Telemetry", "antenna_id", "ts")
    mk("AntennaEvents", "antenna_id", "sk", stream=True)
    mk("Predictions", "antenna_id", gsi="zone_id")
    mk("Rejects", "day", "sk")
    mk("Control", "key")
    mk("MockInbox", "pk")


@pytest.fixture()
def cloud():
    """AWS simulado y catálogo cargado (12 zonas, 60 antenas). Devuelve un objeto con atajos."""
    from moto import mock_aws
    with mock_aws():
        import boto3
        from common import aws, config, secrets
        aws.reset_clients()
        config.reset_cache()
        secrets.reset_cache()
        ddb = boto3.client("dynamodb")
        _tables(ddb)
        sqs = boto3.client("sqs")
        urls = {}
        for key, name in (("AZURE", "outbox-azure"), ("GCP_EVENTS", "outbox-gcp-events"),
                          ("GCP_TELEMETRY", "outbox-gcp-telemetry")):
            dlq = sqs.create_queue(QueueName=name + "-dlq")["QueueUrl"]
            q = sqs.create_queue(QueueName=name)["QueueUrl"]
            urls[key] = (q, dlq)
            os.environ[f"OUTBOX_{key}_URL"] = q
            os.environ[f"DLQ_{key}_URL"] = dlq
        os.environ["TELEMETRY_QUEUE_URL"] = urls["GCP_TELEMETRY"][0]
        os.environ["SECRETS_NAME"] = "pulsored/secrets"
        boto3.client("secretsmanager").create_secret(
            Name="pulsored/secrets", SecretString=json.dumps({"inbound": KEYS, "outbound": OUTBOUND}))
        for z in ZONES["zones"]:
            ddb.put_item(TableName="Zones", Item={"zone_id": {"S": z["zone_id"]}, "name": {"S": z["name"]},
                                                  "type": {"S": z["type"]}})
            for a in z["antennas"]:
                ddb.put_item(TableName="Antennas", Item={
                    "antenna_id": {"S": a["antenna_id"]}, "zone_id": {"S": z["zone_id"]}, "state": {"S": "NORMAL"},
                    "lat": {"N": str(a["lat"])}, "lng": {"N": str(a["lng"])}})
        yield Cloud(urls)


class Cloud:
    def __init__(self, urls):
        self.urls = urls

    @property
    def sqs(self):
        import boto3
        return boto3.client("sqs")

    def messages(self, key, dlq=False):
        """Lee y BORRA los mensajes de una cola (cada prueba las consulta una vez)."""
        url = self.urls[key][1 if dlq else 0]
        out = []
        while True:
            r = self.sqs.receive_message(QueueUrl=url, MaxNumberOfMessages=10)
            if not r.get("Messages"):
                return out
            for m in r["Messages"]:
                out.append(json.loads(m["Body"]))
                self.sqs.delete_message(QueueUrl=url, ReceiptHandle=m["ReceiptHandle"])

    def antenna(self, antenna_id):
        from common import aws, ddb
        return ddb.get(aws.table("ANTENNAS"), {"antenna_id": antenna_id})

    def events(self, antenna_id):
        from common import aws, ddb
        return ddb.query(aws.table("ANTENNAEVENTS"), "antenna_id = :a", {":a": antenna_id})

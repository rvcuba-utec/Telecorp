"""Stream de AntennaEvents: el primer evento del episodio arranca la SF; los demás se publican directo."""
import json

from common import aws, ddb, emitter, log


def route(image: dict):
    if image.get("starts_episode"):
        try:
            aws.client("stepfunctions").start_execution(
                stateMachineArn=aws.env("SF_LIFECYCLE_ARN"), name=image["episode_id"],
                input=json.dumps({k: image.get(k) for k in emitter.ITEM_FIELDS if image.get(k) is not None}))
            log.info("episodio iniciado", episode_id=image["episode_id"], antenna_id=image["antenna_id"])
        except Exception as exc:  # noqa: BLE001
            if "ExecutionAlreadyExists" not in type(exc).__name__ and "ExecutionAlreadyExists" not in str(exc):
                raise
        return "started"
    emitter.publish(image)
    return "published"


def handler(event, context):
    failures = []
    for rec in event.get("Records", []):
        if rec.get("eventName") != "INSERT":
            continue
        try:
            image = ddb.native(ddb.from_item(rec["dynamodb"]["NewImage"]))
            if image.get("published_at"):
                continue
            route(image)
        except Exception as exc:  # noqa: BLE001
            log.error("falló el enrutado", error=str(exc), sequence=rec["dynamodb"].get("SequenceNumber"))
            failures.append({"itemIdentifier": rec["dynamodb"]["SequenceNumber"]})
    return {"batchItemFailures": failures}

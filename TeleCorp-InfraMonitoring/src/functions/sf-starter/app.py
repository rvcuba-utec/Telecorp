"""Arranca una máquina de estados desde EventBridge (reglas con destino Lambda: no necesitan asumir ningún rol)."""
import json
import uuid

from common import aws, log, timeutil

MACHINES = {"recurrence": "SF_RECURRENCE_ARN", "redelivery": "SF_REDELIVERY_ARN"}


def handler(event, context):
    machine = event.get("machine")
    if machine not in MACHINES:
        raise ValueError("machine debe ser " + " o ".join(MACHINES))
    name = f"{machine}-{timeutil.fmt(timeutil.now()).replace(':', '').replace('-', '')}-{uuid.uuid4().hex[:6]}"
    r = aws.client("stepfunctions").start_execution(stateMachineArn=aws.env(MACHINES[machine]), name=name,
                                                    input=json.dumps(event.get("input") or {}))
    log.info("máquina iniciada", machine=machine, execution=r["executionArn"])
    return {"executionArn": r["executionArn"]}

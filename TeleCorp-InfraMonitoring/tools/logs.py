#!/usr/bin/env python3
"""Últimos logs de una Lambda del stack. Uso: python tools/logs.py <LogicalId sin 'Function'> [minutos]"""
import sys
import time

from aws_client import STACK, client

name = sys.argv[1] + "Function"
minutes = int(sys.argv[2]) if len(sys.argv) > 2 else 15
fn = client("cloudformation").describe_stack_resource(StackName=STACK, LogicalResourceId=name)["StackResourceDetail"]["PhysicalResourceId"]
try:
    r = client("logs").filter_log_events(logGroupName=f"/aws/lambda/{fn}", startTime=int((time.time() - minutes * 60) * 1000), limit=200)
    for e in r["events"][-60:]:
        print(e["message"].rstrip())
except client("logs").exceptions.ResourceNotFoundException:
    print("sin logs todavía (la función no se ha ejecutado)")

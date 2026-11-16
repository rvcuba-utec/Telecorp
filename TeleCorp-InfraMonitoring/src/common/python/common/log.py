"""Logs JSON y métricas en formato EMF (CloudWatch las extrae sin llamadas a la API)."""
import json
import os
import sys
import time

NAMESPACE = "PulsoRed"
_correlation = {"id": None}


def set_correlation(cid):
    _correlation["id"] = cid


def log(level: str, message: str, **fields):
    rec = {"level": level, "message": message, "correlation_id": _correlation["id"],
           "function": os.environ.get("AWS_LAMBDA_FUNCTION_NAME")}
    rec.update({k: v for k, v in fields.items() if v is not None})
    sys.stdout.write(json.dumps(rec, default=str) + "\n")


def info(message, **f):
    log("INFO", message, **f)


def warn(message, **f):
    log("WARN", message, **f)


def error(message, **f):
    log("ERROR", message, **f)


def metric(name: str, value: float = 1, unit: str = "Count", **dimensions):
    """Emite una métrica EMF. `dimensions` son pares nombre=valor (pocos, de baja cardinalidad)."""
    rec = {
        "_aws": {"Timestamp": int(time.time() * 1000),
                 "CloudWatchMetrics": [{"Namespace": NAMESPACE, "Dimensions": [list(dimensions)] if dimensions else [[]],
                                        "Metrics": [{"Name": name, "Unit": unit}]}]},
        name: value,
    }
    rec.update(dimensions)
    sys.stdout.write(json.dumps(rec) + "\n")

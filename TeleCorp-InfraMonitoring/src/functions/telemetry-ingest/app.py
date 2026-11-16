"""POST /telemetry — recibe un lote `telemetry.batch` (RF-AWS-01, 03, 17)."""
import json

from common import auth, ingest, log, validators


def handler(event, context):
    denied = auth.check(event)
    if denied:
        return denied
    try:
        body = auth.json_body(event)
    except (ValueError, json.JSONDecodeError):
        return auth.response(400, {"error": "BadRequest", "message": "el cuerpo no es JSON válido"})
    try:
        payload = validators.validate_envelope(body, "telemetry.batch")
        samples = validators.telemetry_payload(payload)
    except validators.ValidationError as exc:
        return auth.response(400, {"error": "BadRequest", "message": str(exc)})
    log.set_correlation(body["correlation_id"])
    try:
        result = ingest.process(samples, "api", body["correlation_id"])
    except ingest.Throttled:
        return auth.response(429, {"error": "TooManyRequests", "message": "DynamoDB limitó la escritura"},
                             {"Retry-After": "2"})
    return auth.response(202, {"accepted": result["accepted"], "duplicates": result["duplicates"],
                               "rejected": result["rejected"]})

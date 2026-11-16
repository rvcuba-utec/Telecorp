"""POST /predictions — recibe `prediction.batch` de GCP, guarda y arranca `preventive-alert` (RF-AWS-20, 21)."""
import json
from datetime import timedelta

from common import alerts, auth, aws, config, ddb, log, timeutil, validators


def _start_alert(pred: dict):
    name = f"{pred['antenna_id']}-{timeutil.epoch(pred['generated_at'])}"
    try:
        aws.client("stepfunctions").start_execution(stateMachineArn=aws.env("SF_PREVENTIVE_ARN"), name=name,
                                                    input=json.dumps(ddb.native(pred)))
        return True
    except Exception as exc:  # noqa: BLE001
        if "ExecutionAlreadyExists" in type(exc).__name__ or "ExecutionAlreadyExists" in str(exc):
            return False
        raise


def handler(event, context):
    denied = auth.check(event)
    if denied:
        return denied
    try:
        body = auth.json_body(event)
        payload = validators.validate_envelope(body, "prediction.batch")
        model_version, generated_at, preds = validators.prediction_payload(payload)
    except (ValueError, json.JSONDecodeError):
        return auth.response(400, {"error": "BadRequest", "message": "el cuerpo no es JSON válido"})
    except validators.ValidationError as exc:
        return auth.response(400, {"error": "BadRequest", "message": str(exc)})
    log.set_correlation(body["correlation_id"])
    cfg = config.get_config()
    now_iso = timeutil.fmt(timeutil.now())
    ids = sorted({p["antenna_id"] for p in preds if isinstance(p, dict) and isinstance(p.get("antenna_id"), str)})
    antennas = ddb.batch_get(aws.table("ANTENNAS"), [{"antenna_id": a} for a in ids]) if ids else {}
    accepted, duplicates, rejected, started = 0, 0, [], 0
    expire = timeutil.epoch(timeutil.parse(now_iso) + timedelta(days=7))
    for i, p in enumerate(preds):
        reason = validators.check_prediction(p, now_iso)
        a = antennas.get(p.get("antenna_id")) if not reason else None
        if not reason and not a:
            reason = "antena desconocida"
        if not reason and p.get("zone_id") and p["zone_id"] != a.get("zone_id"):
            reason = "zone_id no coincide con el catálogo"
        if reason:
            rejected.append({"index": i, "reason": reason})
            continue
        item = {"antenna_id": p["antenna_id"], "zone_id": a.get("zone_id"), "probability": p["probability"],
                "predicted_state": p["predicted_state"], "horizon_minutes": p["horizon_minutes"],
                "valid_until": timeutil.norm(p["valid_until"]), "generated_at": generated_at,
                "model_version": model_version, "alert_status": "NINGUNA", "expire_at": expire}
        try:  # solo reemplaza si es más nueva (idempotencia)
            ddb.put(aws.table("PREDICTIONS"), item,
                    condition="attribute_not_exists(antenna_id) OR generated_at < :g", values={":g": generated_at})
        except Exception as exc:  # noqa: BLE001
            if ddb.error_code(exc) == "ConditionalCheckFailedException":
                duplicates += 1
                continue
            raise
        accepted += 1
        ok, _ = alerts.eligible(item, a, cfg, now_iso)  # pre-filtro: no gastar ejecuciones de SF
        if ok and _start_alert(item):
            started += 1
    log.metric("PredictionsAccepted", accepted)
    log.info("predicciones procesadas", accepted=accepted, duplicates=duplicates, rejected=len(rejected),
             alerts_started=started)
    return auth.response(202, {"accepted": accepted, "duplicates": duplicates, "rejected": rejected})

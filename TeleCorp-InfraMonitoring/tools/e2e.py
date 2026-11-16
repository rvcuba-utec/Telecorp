#!/usr/bin/env python3
"""Verificación contra el stack desplegado (usa el receptor simulado de Azure/GCP).

  smoke        comprobaciones rápidas de solo lectura (las usa ./check.sh)
  e2e          episodio completo, idempotencia, predicción -> PREVENTIVA y recurrencia -> ESTRUCTURAL (~4 min)
  resilience   receptor caído -> reintentos -> DLQ -> event-redelivery -> entrega sin duplicados (~20 min)
  status       estado de la cola, el simulador y lo recibido por el receptor simulado
  warmup       reactiva los disparadores SQS/Streams que Academy apaga al cerrar el laboratorio
  purge        borra el secreto y los parámetros SSM (tras ./destroy.sh)
"""
import sys
import time
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone

from aws_client import CONFIG_PREFIX, ENDPOINT_PREFIX, SECRETS_NAME, STACK, api, client, outputs, secrets

FAILS = []


def ok(msg):
    print(f"  OK     {msg}")


def bad(msg):
    FAILS.append(msg)
    print(f"  FALLO  {msg}")


def check(cond, msg):
    (ok if cond else bad)(msg)
    return cond


def minute(offset=0):
    return datetime.now(timezone.utc).replace(second=0, microsecond=0) + timedelta(minutes=offset)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def envelope(type_, payload, source="aws"):
    return {"event_id": str(uuid.uuid4()), "type": type_, "schema_version": "1.0", "source": source,
            "occurred_at": iso(datetime.now(timezone.utc)), "correlation_id": "e2e-" + uuid.uuid4().hex[:8],
            "payload": payload}


def push(a, antenna, series):
    """series = [(offset_minutos, gb_per_min)] -> POST /telemetry."""
    samples = [{"antenna_id": antenna, "ts": iso(minute(o)), "gb_per_min": g} for o, g in series]
    return a.call("POST", "/telemetry", "telemetry", envelope("telemetry.batch", {"samples": samples}))


def received(a, dest="azure"):
    code, body, _ = a.call("GET", f"/mock/{dest}/received?limit=500", "operator")
    return body.get("items", []) if code == 200 else []


def wait_for(desc, fn, timeout=90, every=3):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = fn()
        if v:
            ok(f"{desc} ({time.time() - t0:.0f} s)")
            return v
        time.sleep(every)
    bad(f"{desc}: no ocurrió en {timeout} s")
    return None


def resource_ids():
    res = client("cloudformation").list_stack_resources(StackName=STACK)["StackResourceSummaries"]
    return {r["LogicalResourceId"]: r["PhysicalResourceId"] for r in res}


def mappings():
    ids, lam = resource_ids(), client("lambda")
    out = []
    for logical in ("OutboxSenderFunction", "EventRouterFunction"):
        for m in lam.list_event_source_mappings(FunctionName=ids[logical])["EventSourceMappings"]:
            out.append((logical.replace("Function", ""), m["UUID"], m["State"]))
    return out


def execution(machine_arn, name):
    arn = machine_arn.replace(":stateMachine:", ":execution:") + ":" + name
    try:
        return client("stepfunctions").describe_execution(executionArn=arn)
    except Exception:  # noqa: BLE001
        return None


def pick_antennas(a, n, quiet_minutes=8, exclude=()):
    """Antenas en NORMAL, sin muestras recientes (así no chocan con otra corrida ni con el simulador)."""
    if a.call("GET", "/simulator/status", "operator")[1].get("active"):
        a.call("POST", "/simulator/stop", "operator", {})
        raise SystemExit("El simulador estaba activo y escribe en las 60 antenas cada minuto: lo detuve.\n"
                         "Vuelve a ejecutar esta prueba en ~8 minutos.")
    _, body, _ = a.call("GET", "/antennas", "operator")
    cutoff = iso(minute(-quiet_minutes))
    free = [x["antenna_id"] for x in body["antennas"] if x["state"] == "NORMAL" and not x["episode_id"]
            and (not x["last_seen"] or x["last_seen"] < cutoff) and x["antenna_id"] not in exclude]
    free.reverse()  # desde la 060 hacia abajo: lejos de lo que use el simulador al azar
    if len(free) < n:
        raise SystemExit("no hay suficientes antenas libres: espera unos minutos o detén el simulador")
    return free[:n]


# ------------------------------------------------------------------ smoke
def smoke():
    out = outputs()
    a = api()
    print("Comprobaciones de solo lectura:")
    check(a.call("GET", "/health")[0] == 200, "GET /health sin clave")
    check(a.call("GET", "/config")[0] == 401, "GET /config sin clave -> 401")
    check(a.call("GET", "/config", "clave-mala")[0] == 403, "clave inválida -> 403 (HTTP API responde 403 cuando el authorizer niega)")
    code, cfg, _ = a.call("GET", "/config", "gcp")
    check(code == 200 and cfg["items"]["THRESHOLD_DOWN"] == 60, f"GET /config con clave de GCP (version {cfg.get('config_version')})")
    check("secret" not in str(cfg).lower(), "/config no contiene secretos")
    code, body, _ = a.call("GET", "/antennas", "azure")
    check(code == 200 and len(body["antennas"]) == 60, f"GET /antennas devuelve {len(body.get('antennas', []))} antenas")
    check(a.call("GET", "/antennas", "telemetry")[0] == 403, "la clave de telemetría no puede leer antenas -> 403")
    check(a.call("POST", "/simulator/start", "azure", {})[0] == 403, "la clave de Azure no controla el simulador -> 403")
    st = a.call("GET", "/simulator/status", "operator")[1]
    print(f"  INFO   simulador: {'ACTIVO' if st.get('active') else 'apagado'}")
    bad_maps = [(n, s) for n, _, s in mappings() if s not in ("Enabled", "Enabling")]
    check(not bad_maps, "disparadores SQS y Streams activos" + (f" (apagados: {bad_maps}; ejecuta ./warmup.sh)" if bad_maps else ""))
    sqs = client("sqs")
    for k, label in (("OutboxAzureDlqUrl", "DLQ azure"), ("OutboxGcpEventsDlqUrl", "DLQ gcp-events"),
                     ("OutboxGcpTelemetryDlqUrl", "DLQ gcp-telemetry")):
        n = int(sqs.get_queue_attributes(QueueUrl=out[k], AttributeNames=["ApproximateNumberOfMessages"])["Attributes"]["ApproximateNumberOfMessages"])
        print(f"  {'OK    ' if n == 0 else 'AVISO '} {label}: {n} mensajes")
    ssm = client("ssm")
    for d in ("azure", "gcp"):
        check(bool(ssm.get_parameter(Name=f"{ENDPOINT_PREFIX}/{d}")["Parameter"]["Value"]), f"endpoint {d} configurado")
    s = secrets()
    check(all(s["inbound"].values()), "claves inbound presentes")
    check(all(s["outbound"].values()), "claves outbound presentes (si faltan: bootstrap_secrets.py --set azure=KEY gcp=KEY)")
    return finish("smoke")


# ------------------------------------------------------------------ e2e
def e2e():
    a = api()
    out = outputs()
    ant, pre = pick_antennas(a, 2)
    rec_ant, = pick_antennas(a, 1, quiet_minutes=20, exclude=(ant, pre))  # recibe 18 min de historia
    print(f"Antenas de prueba: falla={ant} preventiva={pre} recurrencia={rec_ant}\n")
    before = {d: {i["event_id"] for i in received(a, d)} for d in ("azure", "gcp")}
    fresh = lambda dest: [i for i in received(a, dest) if i["event_id"] not in before[dest]]  # noqa: E731

    print("1) Ingesta, validación e idempotencia")
    code, r, _ = push(a, ant, [(-2, 95), (-1, 30), (0, 30)])
    check(code == 202 and r["accepted"] == 3, f"lote de 3 muestras aceptado {r}")
    code, r, _ = push(a, ant, [(-2, 95), (-1, 30), (0, 30)])
    check(code == 202 and r["accepted"] == 0 and r["duplicates"] == 3, f"mismo lote reenviado: 0 nuevas, 3 duplicadas {r}")
    code, r, _ = a.call("POST", "/telemetry", "telemetry", envelope("telemetry.batch", {"samples": [
        {"antenna_id": "ANT-LIM-999", "ts": iso(minute()), "gb_per_min": 5}, {"antenna_id": ant, "gb_per_min": -3}]}))
    check(code == 202 and r["accepted"] == 0 and len(r["rejected"]) == 2, "muestras inválidas rechazadas con motivo")
    check(a.call("POST", "/telemetry", "telemetry", {"x": 1})[0] == 400, "sobre inválido -> 400")

    print("\n2) Caída confirmada -> evento a Azure y GCP, y máquina de estados")
    seen = lambda dest, text: any(i["summary"] == text for i in fresh(dest))  # noqa: E731
    wait_for("FALLA llega a Azure", lambda: seen("azure", f"FALLA {ant} CAIDA"), 120)
    wait_for("FALLA llega a GCP", lambda: seen("gcp", f"FALLA {ant} CAIDA"), 60)
    ep = f"EP-{ant}-{int(minute(-1).timestamp())}"
    ex = execution(out["IncidentLifecycleArn"], ep)
    check(ex is not None and ex["status"] in ("RUNNING", "SUCCEEDED"), f"antenna-incident-lifecycle ejecutándose ({ep})")
    code, d, _ = a.call("GET", f"/antennas/{ant}", "azure")
    check(d.get("state") == "CAIDA" and d.get("episode_id") == ep, f"GET /antennas/{ant}: CAIDA, episodio {d.get('episode_id')}")

    print("\n3) Recuperación (3 muestras normales) -> publicada una sola vez; la SF cierra el episodio")
    push(a, ant, [(1, 95), (2, 95), (3, 95)])
    wait_for("RECUPERACION llega a Azure", lambda: seen("azure", f"RECUPERACION {ant} NORMAL"), 120)
    done = wait_for("la máquina de estados termina", lambda: (execution(out["IncidentLifecycleArn"], ep) or {}).get("status") == "SUCCEEDED", 180, 5)
    time.sleep(3)
    counts = Counter(i["summary"] for i in fresh("azure"))
    check(counts[f"RECUPERACION {ant} NORMAL"] == 1, "RECUPERACION publicada exactamente una vez")
    check(counts[f"FALLA {ant} CAIDA"] == 1, "FALLA publicada exactamente una vez")
    if done:
        check(a.call("GET", f"/antennas/{ant}", "azure")[1].get("episode_id") is None, "episodio cerrado en Antennas")

    print("\n4) Predicción de GCP -> alerta preventiva")
    gen = iso(minute())
    preds = lambda p, antenna: envelope("prediction.batch", {"model_version": "e2e-1", "generated_at": gen, "predictions": [  # noqa: E731
        {"antenna_id": antenna, "probability": p, "predicted_state": "CAIDA", "horizon_minutes": 120,
         "valid_until": iso(minute(180))}]}, "gcp")
    code, r, _ = a.call("POST", "/predictions", "gcp", preds(0.83, pre))
    check(code == 202 and r["accepted"] == 1, f"POST /predictions p=0.83 aceptada {r}")
    wait_for("PREVENTIVA llega a Azure", lambda: seen("azure", f"PREVENTIVA {pre} CAIDA"), 90)
    code, r, _ = a.call("POST", "/predictions", "gcp", preds(0.83, pre))
    check(r.get("duplicates") == 1, "reenviar la misma predicción es idempotente")
    p_ex = execution(out["PreventiveAlertArn"], f"{pre}-{int(datetime.strptime(gen, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc).timestamp())}")
    check(p_ex is not None, "SF preventive-alert ejecutada")
    code, r, _ = a.call("GET", "/predictions?min_probability=0.8", "azure")
    check(code == 200 and any(p["antenna_id"] == pre for p in r["predictions"]), "GET /predictions la devuelve")
    check(a.call("POST", "/predictions", "azure", preds(0.9, pre))[0] == 403, "Azure no puede enviar predicciones -> 403")

    print("\n5) Tres episodios en una antena -> recurrence-analysis -> ESTRUCTURAL")
    series = [(-19 + i, g) for i, g in enumerate([95, 30, 30, 95, 95, 95] * 3)]  # 3 episodios en el pasado
    code, r, _ = push(a, rec_ant, series)
    check(code == 202 and r["accepted"] == 18, "18 muestras históricas (3 episodios) aceptadas")
    time.sleep(8)
    sfn = client("stepfunctions")
    name = "e2e-recurrence-" + uuid.uuid4().hex[:8]
    arn = sfn.start_execution(stateMachineArn=out["RecurrenceAnalysisArn"], name=name, input="{}")["executionArn"]
    wait_for("recurrence-analysis termina", lambda: sfn.describe_execution(executionArn=arn)["status"] == "SUCCEEDED", 150, 5)
    wait_for("ESTRUCTURAL llega a Azure", lambda: seen("azure", f"ESTRUCTURAL {rec_ant} NORMAL"), 60)
    return finish("e2e")


# ------------------------------------------------------------------ resilience
def resilience():
    a = api()
    out = outputs()
    ant, = pick_antennas(a, 1)
    sqs = client("sqs")
    depth = lambda k: int(sqs.get_queue_attributes(QueueUrl=out[k], AttributeNames=["ApproximateNumberOfMessages"])["Attributes"]["ApproximateNumberOfMessages"])  # noqa: E731
    before = {i["event_id"] for i in received(a, "azure")}
    new = lambda text: [i for i in received(a, "azure") if i["summary"] == text and i["event_id"] not in before]  # noqa: E731
    seen = lambda text: bool(new(text))  # noqa: E731
    print(f"Antena de prueba: {ant}\n1) Receptor caído (503)")
    check(a.call("POST", "/mock/mode", "operator", {"fail": True, "status": 503})[0] == 200, "receptor simulado en modo caído")
    try:
        push(a, ant, [(-2, 95), (-1, 30), (0, 30)])
        print("2) Reintentos con espera creciente (30, 60, 120, 240, 480 s) y paso a la DLQ (~16 min)...")
        wait_for("el mensaje llega a la DLQ de Azure", lambda: depth("OutboxAzureDlqUrl") > 0, 1200, 15)
        check(not seen(f"FALLA {ant} CAIDA"), "el receptor no recibió nada mientras estuvo caído")
    finally:
        a.call("POST", "/mock/mode", "operator", {"fail": False})
    print("3) Receptor recuperado -> event-redelivery")
    sfn = client("stepfunctions")
    arn = sfn.start_execution(stateMachineArn=out["EventRedeliveryArn"], name="e2e-redelivery-" + uuid.uuid4().hex[:8],
                              input='{"wait_s": 5, "settle_s": 30}')["executionArn"]
    wait_for("el evento se entrega tras el reenvío", lambda: seen(f"FALLA {ant} CAIDA"), 240, 5)
    wait_for("event-redelivery termina", lambda: sfn.describe_execution(executionArn=arn)["status"] == "SUCCEEDED", 240, 5)
    time.sleep(3)
    check(len(new(f"FALLA {ant} CAIDA")) == 1, "entregado exactamente una vez (sin duplicados)")
    check(depth("OutboxAzureDlqUrl") == 0, "la DLQ de Azure quedó vacía")
    push(a, ant, [(1, 95), (2, 95), (3, 95)])  # deja la antena en NORMAL: la SF del episodio termina sola
    return finish("resilience")


def status():
    out = outputs()
    a = api()
    print("Simulador:", a.call("GET", "/simulator/status", "operator")[1])
    _, body, _ = a.call("GET", "/antennas", "operator")
    print("Antenas por estado:", dict(Counter(x["state"] for x in body["antennas"])))
    sqs = client("sqs")
    for k in ("OutboxAzureUrl", "OutboxGcpEventsUrl", "OutboxGcpTelemetryUrl", "OutboxAzureDlqUrl",
              "OutboxGcpEventsDlqUrl", "OutboxGcpTelemetryDlqUrl"):
        at = sqs.get_queue_attributes(QueueUrl=out[k], AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"])["Attributes"]
        print(f"  {k:28s} visibles={at['ApproximateNumberOfMessages']} en vuelo={at['ApproximateNumberOfMessagesNotVisible']}")
    for d in ("azure", "gcp"):
        items = received(a, d)
        print(f"Receptor simulado {d}: {len(items)} recibidos; últimos:", [i["summary"] for i in items[:5]])


def warmup():
    lam = client("lambda")
    for name, uuid_, state in mappings():
        if state not in ("Enabled", "Enabling"):
            lam.update_event_source_mapping(UUID=uuid_, Enabled=True)
            print(f"Reactivado: {name} ({state} -> Enabled)")
        else:
            print(f"OK: {name} ya activo")
    ids = resource_ids()
    for logical in ("HealthFunction", "AuthorizerFunction", "TelemetryIngestFunction", "AntennasApiFunction"):
        client("lambda").invoke(FunctionName=ids[logical], InvocationType="Event", Payload=b"{}")
    print("Lambdas calentadas.")


def purge():
    if input(f"Escribe 'borrar' para eliminar el secreto {SECRETS_NAME} y los parámetros /pulsored/*: ") != "borrar":
        raise SystemExit("cancelado")
    client("secretsmanager").delete_secret(SecretId=SECRETS_NAME, ForceDeleteWithoutRecovery=True)
    ssm = client("ssm")
    names = [p["Name"] for pre in (CONFIG_PREFIX, ENDPOINT_PREFIX)
             for p in ssm.get_parameters_by_path(Path=pre, Recursive=True)["Parameters"]]
    for i in range(0, len(names), 10):
        ssm.delete_parameters(Names=names[i:i + 10])
    print("Secreto y parámetros eliminados.")


def finish(name):
    print()
    if FAILS:
        print(f"{name}: {len(FAILS)} comprobaciones fallaron:")
        for f in FAILS:
            print("  -", f)
        return 1
    print(f"{name}: todo correcto.")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "smoke"
    fn = {"smoke": smoke, "e2e": e2e, "resilience": resilience, "status": status, "warmup": warmup, "purge": purge}.get(cmd)
    if not fn:
        raise SystemExit(__doc__)
    raise SystemExit(fn() or 0)

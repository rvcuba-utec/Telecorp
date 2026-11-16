"""Núcleo de ingesta: valida, clasifica, guarda y ejecuta la máquina de confirmación (RF-AWS-01..06, 09, 17).

Lo usan `telemetry-ingest` (API) y `simulator` (en proceso).
"""
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from . import aws, config, ddb, envelope, log, machine, timeutil, validators
from . import events as ev
from .classify import classify

MAX_FUTURE_MIN = 10


class Throttled(Exception):
    """DynamoDB limitó la escritura: el llamante responde 429 con Retry-After."""


def _antenna_update(antenna_id, s, old_last_ts):
    sets = {k: s.get(k) for k in machine.FIELDS}
    if old_last_ts:
        cond, vals = "last_ts = :old", {":old": old_last_ts}
    else:
        cond, vals = "attribute_not_exists(last_ts)", {}
    return ddb.update_kwargs(aws.table("ANTENNAS"), {"antenna_id": antenna_id}, sets=sets,
                            condition=cond, cond_values=vals)


def _write_state(antenna_id, s, events, old_last_ts):
    if events:
        actions = [{"Update": _antenna_update(antenna_id, s, old_last_ts)}]
        actions += [ddb.put_action(aws.table("ANTENNAEVENTS"), ev.item_for(e)) for e in events]
        ddb.transact(actions)
    else:
        kw = _antenna_update(antenna_id, s, old_last_ts)
        ddb.db().update_item(**kw)


def _process_antenna(antenna_id, rows, antenna, cfg, now_iso, ttl_days):
    """Procesa las muestras de UNA antena. Devuelve (aceptadas, eventos). Reintenta una vez ante conflicto."""
    for attempt in (1, 2):
        state = machine.initial_state(antenna)
        old_last_ts = state["last_ts"]
        fresh = [r for r in rows if not old_last_ts or r["ts"] > old_last_ts]
        if not fresh:
            return [], []
        try:
            ddb.batch_write(aws.table("TELEMETRY"), [
                {"antenna_id": antenna_id, "ts": r["ts"], "gb_per_min": r["gb_per_min"], "state": r["state"],
                 "expire_at": timeutil.epoch(timeutil.parse(r["ts"]) + timedelta(days=ttl_days))} for r in fresh])
            events = []
            for r in fresh:
                events += machine.step(state, antenna, r["ts"], r["state"], r["gb_per_min"], cfg, detected_at=now_iso)
            _write_state(antenna_id, state, events, old_last_ts)
            return fresh, events
        except Exception as exc:  # noqa: BLE001
            if ddb.is_throttle(exc):
                raise Throttled() from exc
            if ddb.is_conflict(exc) and attempt == 1:
                antenna = ddb.get(aws.table("ANTENNAS"), {"antenna_id": antenna_id}, consistent=True) or antenna
                continue
            raise
    return [], []


def _enqueue_gcp(fresh_rows, correlation_id):
    url = aws.env("TELEMETRY_QUEUE_URL", "")
    if not url or not fresh_rows:
        return
    samples = [{"antenna_id": r["antenna_id"], "zone_id": r["zone_id"], "ts": r["ts"],
                "gb_per_min": r["gb_per_min"], "state": r["state"]} for r in fresh_rows]
    env = envelope.make("telemetry.batch", {"samples": samples}, correlation_id=correlation_id,
                        event_id=ev.batch_event_id("telemetry", [f"{s['antenna_id']}|{s['ts']}" for s in samples]))
    aws.client("sqs").send_message(QueueUrl=url, MessageBody=json.dumps(env))


def _record_rejects(rejected, samples, origin, now_iso):
    if not rejected:
        return
    try:
        day = now_iso[:10]
        exp = timeutil.epoch(timeutil.parse(now_iso) + timedelta(days=7))
        ddb.batch_write(aws.table("REJECTS"), [
            {"day": day, "sk": f"{now_iso}#{uuid.uuid4()}", "origin": origin, "index": r["index"],
             "reason": r["reason"], "original": json.dumps(samples[r["index"]], default=str)[:1000],
             "expire_at": exp} for r in rejected[:100]])
    except Exception as exc:  # noqa: BLE001  los rechazos son informativos: nunca tumban la ingesta
        log.warn("no se pudieron registrar los rechazos", error=str(exc))


def process(samples: list, origin: str = "api", correlation_id: str = None, now_iso: str = None) -> dict:
    """Procesa un lote ya con forma válida. Devuelve {accepted, duplicates, rejected[], events[]}."""
    cfg = config.get_config()
    now_iso = now_iso or timeutil.fmt(timeutil.now())
    rejected, rows, seen, duplicates = [], [], set(), 0
    latest_ok = timeutil.fmt(timeutil.parse(now_iso) + timedelta(minutes=MAX_FUTURE_MIN))

    for i, s in enumerate(samples):
        reason = validators.check_sample(s)
        if reason:
            rejected.append({"index": i, "reason": reason})
            continue
        ts = timeutil.norm(s["ts"])
        if ts > latest_ok:
            rejected.append({"index": i, "reason": "ts en el futuro"})
            continue
        key = (s["antenna_id"], ts)
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        gb = float(s["gb_per_min"])
        rows.append({"index": i, "antenna_id": s["antenna_id"], "ts": ts, "gb_per_min": gb,
                     "zone_hint": s.get("zone_id"),
                     "state": classify(gb, cfg["THRESHOLD_DOWN"], cfg["THRESHOLD_SATURATED"])})

    ids = sorted({r["antenna_id"] for r in rows})
    antennas = ddb.batch_get(aws.table("ANTENNAS"), [{"antenna_id": a} for a in ids]) if ids else {}
    by_antenna, ok_rows = {}, []
    for r in rows:
        a = antennas.get(r["antenna_id"])
        if not a:
            rejected.append({"index": r["index"], "reason": "antena desconocida"})
        elif r["zone_hint"] and r["zone_hint"] != a.get("zone_id"):
            rejected.append({"index": r["index"], "reason": "zone_id no coincide con el catálogo"})
        else:
            r["zone_id"] = a.get("zone_id")
            by_antenna.setdefault(r["antenna_id"], []).append(r)
            ok_rows.append(r)
    for rs in by_antenna.values():
        rs.sort(key=lambda r: r["ts"])

    # 1) cola hacia GCP primero: si falla la base, el cliente reintenta y el id determinista evita duplicados
    stale = {a: [r for r in rs if antennas[a].get("last_ts") and r["ts"] <= antennas[a]["last_ts"]]
             for a, rs in by_antenna.items()}
    to_gcp = [r for a, rs in by_antenna.items() for r in rs if r not in stale[a]]
    _enqueue_gcp(sorted(to_gcp, key=lambda r: (r["antenna_id"], r["ts"])), correlation_id)

    # 2) por antena, en paralelo
    accepted, events = 0, []
    if by_antenna:
        ttl = int(cfg["TELEMETRY_TTL_DAYS"])
        with ThreadPoolExecutor(max_workers=min(8, len(by_antenna))) as pool:
            futures = [pool.submit(_process_antenna, a, rs, antennas[a], cfg, now_iso, ttl)
                       for a, rs in by_antenna.items()]
            results = [f.result() for f in futures]
        for (fresh, evs), (a, rs) in zip(results, by_antenna.items()):
            accepted += len(fresh)
            duplicates += len(rs) - len(fresh)
            events += evs
    rejected.sort(key=lambda r: r["index"])
    _record_rejects(rejected, samples, origin, now_iso)
    log.metric("SamplesAccepted", accepted, Origin=origin)
    if rejected:
        log.metric("SamplesRejected", len(rejected), Origin=origin)
    for e in events:
        log.metric("EventsDetected", 1, EventType=e["event_type"])
        log.info("evento detectado", antenna_id=e["antenna_id"], episode_id=e["episode_id"], event_id=e["event_id"],
                 event_type=e["event_type"])
    return {"accepted": accepted, "duplicates": duplicates, "rejected": rejected, "events": events}

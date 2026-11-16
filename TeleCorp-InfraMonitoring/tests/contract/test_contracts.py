"""Lo que AWS envía y recibe cumple los contratos compartidos (contracts/)."""
import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker

from common import emitter, envelope, events as ev, ingest, validators

C = Path(__file__).resolve().parents[2] / "contracts"


def schema(name):
    return Draft202012Validator(json.loads((C / "events" / name).read_text()), format_checker=FormatChecker())


def assert_valid(validator, doc):
    errors = [e.message for e in validator.iter_errors(doc)]
    assert not errors, errors


def test_catalogo_coherente_con_los_contratos():
    zones = json.loads((C / "zones.json").read_text())
    enums = json.loads((C / "enums.json").read_text())
    ids = [a["antenna_id"] for z in zones["zones"] for a in z["antennas"]]
    assert len(ids) == 60 and len(set(ids)) == 60 and all(validators.ANTENNA_RE.match(i) for i in ids)
    assert tuple(enums["event_type"]) == ev.EVENT_TYPES and tuple(enums["severity"]) == ev.SEVERITIES
    assert tuple(enums["antenna_state"]) == validators.STATES
    assert tuple(enums["envelope_types"]) == validators.ENVELOPE_TYPES


def test_sobre_que_construye_aws_es_valido():
    e = envelope.make("antenna.event", {"x": 1})
    assert_valid(schema("envelope.schema.json"), e)
    assert validators.validate_envelope(e, "antenna.event") == {"x": 1}


@pytest.mark.parametrize("bad", [
    {}, {"event_id": "no-uuid"}, ])
def test_sobre_invalido_lo_rechazan_ambos(bad):
    base = envelope.make("telemetry.batch", {"samples": []})
    doc = {**base, **bad} if bad else {}
    assert list(schema("envelope.schema.json").iter_errors(doc))
    with pytest.raises(validators.ValidationError):
        validators.validate_envelope(doc, "telemetry.batch")


SAMPLES = [
    ({"antenna_id": "ANT-LIM-001", "ts": "2026-11-15T10:00:00Z", "gb_per_min": 95.5}, True),
    ({"antenna_id": "ANT-LIM-001", "ts": "2026-11-15T10:00:00Z", "gb_per_min": 0}, True),
    ({"antenna_id": "ANT-LIM-001", "ts": "2026-11-15T10:00:00Z", "gb_per_min": 5, "zone_id": "LIM-SURCO", "state": "CAIDA"}, True),
    ({"antenna_id": "ANT-1", "ts": "2026-11-15T10:00:00Z", "gb_per_min": 5}, False),
    ({"antenna_id": "ANT-LIM-001", "ts": "2026-11-15T10:00:00Z", "gb_per_min": -1}, False),
    ({"antenna_id": "ANT-LIM-001", "ts": "2026-11-15T10:00:00Z", "gb_per_min": "5"}, False),
    ({"antenna_id": "ANT-LIM-001", "gb_per_min": 5}, False),
    ({"antenna_id": "ANT-LIM-001", "ts": "2026-11-15T10:00:00Z", "gb_per_min": 5, "x": 1}, False),
    ({"antenna_id": "ANT-LIM-001", "ts": "2026-11-15T10:00:00Z", "gb_per_min": 5, "state": "ROTA"}, False),
    ({"antenna_id": "ANT-LIM-001", "ts": "ayer", "gb_per_min": 5}, False),
]


@pytest.mark.parametrize("sample,ok", SAMPLES)
def test_validador_de_muestras_equivale_al_esquema(sample, ok):
    sch = schema("telemetry-batch.schema.json")
    assert (not list(sch.iter_errors({"samples": [sample]}))) is ok
    assert (validators.check_sample(sample) is None) is ok


def test_lote_de_telemetria_limites():
    sch = schema("telemetry-batch.schema.json")
    for n, ok in ((0, False), (1, True), (500, True), (501, False)):
        doc = {"samples": [SAMPLES[0][0]] * n}
        assert (not list(sch.iter_errors(doc))) is ok
        try:
            validators.telemetry_payload(doc)
            assert ok
        except validators.ValidationError:
            assert not ok


PRED = {"antenna_id": "ANT-LIM-001", "probability": 0.8, "predicted_state": "CAIDA", "horizon_minutes": 60,
        "valid_until": "2099-01-01T00:00:00Z"}


@pytest.mark.parametrize("patch,ok", [
    ({}, True), ({"probability": 1.5}, False), ({"probability": -0.1}, False), ({"predicted_state": "NORMAL"}, False),
    ({"horizon_minutes": 0}, False), ({"horizon_minutes": 1.5}, False), ({"zone_id": "LIM-SURCO"}, True),
    ({"extra": 1}, False), ({"antenna_id": "X"}, False)])
def test_validador_de_predicciones_equivale_al_esquema(patch, ok):
    p = {**PRED, **patch}
    doc = {"model_version": "m", "generated_at": "2026-11-15T10:00:00Z", "predictions": [p]}
    assert (not list(schema("prediction-batch.schema.json").iter_errors(doc))) is ok
    assert (validators.check_prediction(p, "2026-11-15T10:00:00Z") is None) is ok


def test_todos_los_tipos_de_evento_cumplen_antenna_event(cloud):
    """Genera los 7 tipos con el código real y los valida contra el esquema compartido con Azure y GCP."""
    from helpers import sample, ts
    from common import alerts
    sch = schema("antenna-event.schema.json")
    produced = {}
    for i, v in enumerate([95, 30, 30, 95, 95, 95]):  # FALLA y RECUPERACION
        pass
    r = ingest.process([sample("ANT-LIM-001", i, v) for i, v in enumerate([95, 30, 30, 95, 95, 95])]
                       + [sample("ANT-LIM-002", i, v) for i, v in enumerate([95, 140, 140, 140, 140, 140])]
                       + [sample("ANT-LIM-003", i, v) for i, v in enumerate([95, 30, 95, 30, 95, 30, 95, 30, 95])])
    for e in r["events"]:
        produced[e["event_type"]] = e
    produced["PREVENTIVA"] = emitter.complete(alerts.preventive_event(
        {**PRED, "generated_at": ts(0), "model_version": "m-1"}, {"zone_id": "LIM-SURCO", "state": "NORMAL"}))
    produced["PROLONGADA"] = emitter.complete({"event_type": "PROLONGADA", "antenna_id": "ANT-LIM-001", "episode_id": "EP-1",
                                               "new_state": "CAIDA", "ts": ts(5), "elapsed_minutes": 40,
                                               "base_severity": "ALTA"})
    produced["ESTRUCTURAL"] = emitter.complete({"event_type": "ESTRUCTURAL", "antenna_id": "ANT-LIM-001", "episode_id": "STR-1",
                                                "new_state": "NORMAL", "ts": ts(5),
                                                "recurrence": {"episodes_7d": 3, "episodes_30d": 4}})
    assert set(produced) == set(ev.EVENT_TYPES)
    for kind, e in produced.items():
        payload = ev.payload_from(e)
        assert_valid(sch, payload)
        validators.antenna_event_payload(payload)
        assert_valid(schema("envelope.schema.json"), envelope.make("antenna.event", payload, event_id=e["event_id"]))


def test_telemetria_hacia_gcp_cumple_el_esquema(cloud):
    from helpers import sample
    ingest.process([sample(i=i, gb=90 + i) for i in range(4)])
    msg = cloud.messages("GCP_TELEMETRY")[0]
    assert_valid(schema("envelope.schema.json"), msg)
    assert_valid(schema("telemetry-batch.schema.json"), msg["payload"])
    assert all("zone_id" in s and "state" in s for s in msg["payload"]["samples"])


def test_ingest_result_cumple_openapi():
    spec = yaml.safe_load((C / "openapi-aws.yaml").read_text())
    comps = spec["components"]["schemas"]
    result = Draft202012Validator({**comps["IngestResult"], "components": {"schemas": comps}})
    assert_valid(result, {"accepted": 1, "duplicates": 0, "rejected": [{"index": 0, "reason": "x"}]})
    assert set(spec["paths"]) >= {"/health", "/telemetry", "/antennas", "/antennas/{antenna_id}",
                                  "/antennas/{antenna_id}/metrics", "/predictions", "/config", "/simulator/status",
                                  "/simulator/start", "/simulator/stop", "/simulator/scenario"}


def test_template_expone_todas_las_rutas_del_contrato():
    """Cada ruta de openapi-aws.yaml tiene su evento HttpApi en template.yaml (y viceversa)."""
    import re
    spec = yaml.safe_load((C / "openapi-aws.yaml").read_text())
    expected = {f"{m.upper()} {p}" for p, ops in spec["paths"].items() for m in ops}
    text = (C.parent / "template.yaml").read_text()
    found = {f"{m} {p}" for p, m in re.findall(r"Path: \"?([^,\"]+?)\"?, Method: (\w+)", text) if not p.startswith("/mock")}
    assert found == expected

"""Máquina de confirmación (RF-AWS-09): una muestra aislada nunca cambia el estado."""
from datetime import datetime, timedelta, timezone

from common import config, machine
from common.classify import classify

CFG = dict(config.DEFAULTS)
ANT = {"antenna_id": "ANT-LIM-001", "zone_id": "LIM-SURCO"}
T0 = datetime(2026, 11, 15, 10, 0, tzinfo=timezone.utc)


def ts(i):
    return (T0 + timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:%SZ")


def feed(values, state=None, start=0):
    s = state or machine.initial_state(ANT)
    events = []
    for i, v in enumerate(values, start):
        events += machine.step(s, ANT, ts(i), classify(v), v, CFG)
    return s, events


def kinds(events):
    return [e["event_type"] for e in events]


def test_una_muestra_no_cambia_el_estado():
    s, ev = feed([30])
    assert s["state"] == "NORMAL" and ev == [] and s["candidate_count"] == 1


def test_dos_muestras_confirman_caida():
    s, ev = feed([30, 31])
    assert s["state"] == "CAIDA"
    assert kinds(ev) == ["FALLA"] and ev[0]["severity"] == "ALTA" and ev[0]["starts_episode"] is True


def test_inhabilitada_es_critica():
    s, ev = feed([0, 0])
    assert s["state"] == "INHABILITADA" and ev[0]["severity"] == "CRITICA"


def test_first_anomaly_at_es_la_primera_muestra_anomala():
    s, ev = feed([95, 30, 31])
    assert ev[0]["first_anomaly_at"] == ts(1)
    assert ev[0]["episode_id"].endswith(str(int((T0 + timedelta(minutes=1)).timestamp())))


def test_ruido_aislado_no_confirma():
    s, ev = feed([95, 30, 95, 30, 95])
    assert s["state"] == "NORMAL"
    assert "FALLA" not in kinds(ev)


def test_recuperacion_necesita_tres_muestras():
    s, _ = feed([30, 30])
    s, ev = feed([95, 95], state=s, start=2)
    assert s["state"] == "CAIDA" and ev == []
    s, ev = feed([95], state=s, start=4)
    assert s["state"] == "NORMAL" and kinds(ev) == ["RECUPERACION"] and ev[0]["severity"] == "INFO"
    assert ev[0]["starts_episode"] is False


def test_recuperacion_interrumpida_se_reinicia():
    s, _ = feed([30, 30])
    s, ev = feed([95, 95, 30, 95, 95], state=s, start=2)
    assert s["state"] == "CAIDA"


def test_saturacion_sostenida_de_5_no_de_4():
    s, ev = feed([140] * 4)
    assert s["state"] == "NORMAL" and ev == []
    s, ev = feed([140], state=s, start=4)
    assert s["state"] == "SATURADA" and kinds(ev) == ["SATURACION"] and ev[0]["severity"] == "MEDIA"


def test_anomalo_a_otro_anomalo_mismo_episodio():
    s, ev1 = feed([30, 30])
    ep = s["episode_id"]
    s, ev2 = feed([0, 0], state=s, start=2)
    assert s["state"] == "INHABILITADA" and kinds(ev2) == ["FALLA"] and ev2[0]["severity"] == "CRITICA"
    assert ev2[0]["starts_episode"] is False and s["episode_id"] == ep and ev2[0]["previous_state"] == "CAIDA"


def test_muestras_viejas_se_ignoran():
    s, _ = feed([95, 95])
    before = dict(s)
    assert machine.step(s, ANT, ts(0), "CAIDA", 10, CFG) == []
    assert s == before


def test_intermitencia_genera_degradacion_una_vez():
    s, ev = feed([95, 30, 95, 30, 95, 30, 95, 30, 95])
    assert kinds(ev).count("DEGRADACION") == 1
    deg = [e for e in ev if e["event_type"] == "DEGRADACION"][0]
    assert deg["severity"] == "MEDIA" and deg["new_state"] == "NORMAL"
    s, ev = feed([30, 95, 30, 95], state=s, start=9)  # dentro de los 30 min: no repite
    assert "DEGRADACION" not in kinds(ev)


def test_event_id_determinista():
    _, a = feed([30, 30])
    _, b = feed([30, 30])
    assert a[0]["event_id"] == b[0]["event_id"]

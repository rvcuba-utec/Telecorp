"""Simulador (§8): distribuciones, escenarios y modo automático."""
import random
import statistics
from datetime import datetime, timedelta, timezone

from common import config, machine, simulation as sim
from common.classify import classify

T0 = datetime(2026, 11, 15, 10, 0, tzinfo=timezone.utc)
ISO = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731


def series(sc, antenna, minutes, seed=1):
    rng, out = random.Random(seed), []
    for m in range(minutes):
        now = T0 + timedelta(minutes=m)
        base = sim.base_value(rng)
        v = sim.value_at(sc, antenna, now, base, rng)
        out.append(base if v is None else v)
    return out


def test_base_normal_recortada():
    rng = random.Random(7)
    vals = [sim.base_value(rng) for _ in range(5000)]
    assert min(vals) >= 65 and max(vals) <= 125 and abs(statistics.mean(vals) - 95) < 1
    assert all(classify(v) == "NORMAL" for v in vals)


def test_estados_anomalos_caen_en_su_rango():
    rng = random.Random(3)
    assert all(25 <= sim.target_for("CAIDA", rng) <= 55 for _ in range(500))
    assert all(135 <= sim.target_for("SATURADA", rng) <= 170 for _ in range(500))
    assert sim.target_for("INHABILITADA", rng) == 0.0


def test_falla_abrupta_es_instantanea_y_se_recupera():
    sc = sim.new_scenario("FALLA_ABRUPTA", ["A"], "INHABILITADA", 0, 5, "ABRUPTA", ISO(T0), random.Random(1))
    s = series(sc, "A", 8)
    assert s[:5] == [0.0] * 5 and all(classify(v) == "NORMAL" for v in s[5:])


def test_degradacion_rampa_lineal_hacia_caida():
    sc = sim.new_scenario("DEGRADACION", ["A"], "CAIDA", 30, 10, "ABRUPTA", ISO(T0), random.Random(1))
    s = series(sc, "A", 45)
    assert s[0] > 60 and s[15] < s[0] and all(classify(v) == "CAIDA" for v in s[31:40]) and classify(s[41]) == "NORMAL"
    assert s[5] > s[25]  # desciende


def test_degradacion_hacia_inhabilitada_termina_en_cero_exacto():
    sc = sim.new_scenario("DEGRADACION", ["A"], "INHABILITADA", 20, 5, "ABRUPTA", ISO(T0), random.Random(1))
    s = series(sc, "A", 26)
    assert s[20:25] == [0.0] * 5 and all(v > 0 for v in s[:20])


def test_recuperacion_gradual_tarda_10_minutos():
    sc = sim.new_scenario("FALLA_ABRUPTA", ["A"], "CAIDA", 0, 3, "GRADUAL", ISO(T0), random.Random(1))
    s = series(sc, "A", 15)
    assert all(v < 60 for v in s[:3]) and s[8] > s[3] and not sim.expired(sc, T0 + timedelta(minutes=12))
    assert sim.expired(sc, T0 + timedelta(minutes=13))


def test_saturacion_supera_129():
    sc = sim.new_scenario("SATURACION", ["A"], "SATURADA", 0, 10, "ABRUPTA", ISO(T0), random.Random(2))
    assert all(classify(v) == "SATURADA" for v in series(sc, "A", 10))


def test_intermitente_alterna_en_bloques_de_1_a_2_minutos():
    sc = sim.new_scenario("INTERMITENTE", ["A"], "CAIDA", 0, 20, "ABRUPTA", ISO(T0), random.Random(5))
    states = [classify(v) for v in series(sc, "A", 20)]
    assert "CAIDA" in states and "NORMAL" in states
    runs = 1 + sum(1 for a, b in zip(states, states[1:]) if a != b)
    assert runs >= 8


def test_intermitente_dispara_degradacion_en_la_maquina():
    sc = sim.new_scenario("INTERMITENTE", ["A"], "CAIDA", 0, 20, "ABRUPTA", ISO(T0), random.Random(5))
    s = machine.initial_state({"antenna_id": "ANT-LIM-001", "zone_id": "Z"})
    cfg, evs = dict(config.DEFAULTS), []
    for m, v in enumerate(series(sc, "A", 20)):
        evs += machine.step(s, {"antenna_id": "ANT-LIM-001", "zone_id": "Z"}, ISO(T0 + timedelta(minutes=m)),
                            classify(v), v, cfg)
    assert any(e["event_type"] == "DEGRADACION" for e in evs)


def test_tick_apagon_de_zona_y_normal_para_el_resto():
    ants = [f"A{i}" for i in range(10)]
    sc = sim.new_scenario("APAGON_ZONA", ants[:5], "INHABILITADA", 0, 10, "ABRUPTA", ISO(T0), random.Random(1))
    samples, active = sim.tick(T0 + timedelta(minutes=1), ants, {"scenarios": [sc]}, False, random.Random(2))
    assert [s["gb_per_min"] for s in samples[:5]] == [0.0] * 5 and all(60 <= s["gb_per_min"] <= 125 for s in samples[5:])
    assert len(active) == 1 and all(s["ts"] == "2026-11-15T10:01:00Z" for s in samples)


def test_tick_descarta_escenarios_vencidos():
    sc = sim.new_scenario("FALLA_ABRUPTA", ["A"], "CAIDA", 0, 2, "ABRUPTA", ISO(T0), random.Random(1))
    _, active = sim.tick(T0 + timedelta(minutes=5), ["A"], {"scenarios": [sc]}, False, random.Random(2))
    assert active == []


def test_modo_automatico_proporciones_de_rampa():
    rng = random.Random(11)
    eps = [sim.auto_episode("A", False, ISO(T0), rng) for _ in range(2000)]
    ramped = [e for e in eps if e["ramp_minutes"] > 0]
    assert 0.76 < len(ramped) / len(eps) < 0.84
    assert all(30 <= e["ramp_minutes"] <= 90 for e in ramped)
    demo = [sim.auto_episode("A", True, ISO(T0), rng) for _ in range(300)]
    assert all(e["ramp_minutes"] == 0 or 3 <= e["ramp_minutes"] <= 5 for e in demo)


def test_modo_automatico_genera_episodios_a_la_tasa_configurada():
    ants = [f"A{i}" for i in range(60)]
    rng, started, scenarios = random.Random(4), 0, []
    for m in range(1440):  # un día, tasa 0.10 -> ~6 episodios en 60 antenas
        _, scenarios = sim.tick(T0 + timedelta(minutes=m), ants,
                                {"auto": True, "episode_rate_per_antenna_per_day": 0.10, "scenarios": scenarios},
                                False, rng)
        started = max(started, len(scenarios))
    assert started >= 1

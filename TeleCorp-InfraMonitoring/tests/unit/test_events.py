from common import alerts, config, events as ev


def test_event_id_determinista_y_distinto_por_campo():
    a = ev.event_id("ANT-LIM-001", "FALLA", "CAIDA", "2026-11-15T10:00:00Z")
    assert a == ev.event_id("ANT-LIM-001", "FALLA", "CAIDA", "2026-11-15T10:00:00Z")
    assert a != ev.event_id("ANT-LIM-001", "FALLA", "INHABILITADA", "2026-11-15T10:00:00Z")
    assert a != ev.event_id("ANT-LIM-002", "FALLA", "CAIDA", "2026-11-15T10:00:00Z")


def test_severidades():
    assert ev.severity_for("FALLA", "INHABILITADA") == "CRITICA"
    assert ev.severity_for("FALLA", "CAIDA") == "ALTA"
    assert ev.severity_for("SATURACION", "SATURADA") == "MEDIA"
    assert ev.severity_for("RECUPERACION", "NORMAL") == "INFO"
    assert ev.severity_for("DEGRADACION", "NORMAL") == "MEDIA"
    assert ev.severity_for("ESTRUCTURAL", "CAIDA") == "ALTA"
    assert ev.severity_for("PREVENTIVA", "CAIDA", 0.83) == "MEDIA"
    assert ev.severity_for("PREVENTIVA", "CAIDA", 0.90) == "ALTA"


def test_prolongada_sube_un_nivel_con_tope():
    assert ev.severity_for("PROLONGADA", "CAIDA", base="ALTA") == "CRITICA"
    assert ev.severity_for("PROLONGADA", "INHABILITADA", base="CRITICA") == "CRITICA"
    assert ev.severity_for("PROLONGADA", "SATURADA", base="MEDIA") == "ALTA"


def test_regla_de_alerta_preventiva():
    cfg = dict(config.DEFAULTS)
    now = "2026-11-15T10:00:00Z"
    base = {"probability": 0.70, "horizon_minutes": 120, "valid_until": "2026-11-15T12:00:00Z"}
    ant = {"antenna_id": "ANT-LIM-001"}
    assert alerts.eligible(base, ant, cfg, now)[0]
    assert not alerts.eligible({**base, "probability": 0.69}, ant, cfg, now)[0]
    assert alerts.eligible({**base, "horizon_minutes": 1440}, ant, cfg, now)[0]
    assert not alerts.eligible({**base, "horizon_minutes": 1500}, ant, cfg, now)[0]  # 25 h
    assert not alerts.eligible({**base, "valid_until": "2026-11-15T09:00:00Z"}, ant, cfg, now)[0]
    assert not alerts.eligible(base, {"preventive_until": "2026-11-15T11:00:00Z"}, cfg, now)[0]
    assert alerts.eligible(base, {"preventive_until": "2026-11-15T09:00:00Z"}, cfg, now)[0]


def test_config_version_cambia_con_el_contenido():
    a = dict(config.DEFAULTS)
    b = dict(config.DEFAULTS, PRED_ALERT_MIN=0.8)
    assert config.config_version(a) == config.config_version(dict(config.DEFAULTS))
    assert config.config_version(a) != config.config_version(b)
    assert len(config.config_version(a)) == 8


def test_permisos_por_ruta():
    from common import auth
    assert auth.allowed("telemetry", "POST /telemetry")
    assert not auth.allowed("telemetry", "GET /antennas")
    assert auth.allowed("gcp", "GET /antennas/{antenna_id}/metrics")
    assert auth.allowed("gcp", "POST /predictions") and not auth.allowed("gcp", "POST /telemetry")
    assert not auth.allowed("azure", "POST /predictions") and auth.allowed("azure", "GET /config")
    assert auth.allowed("operator", "POST /simulator/start") and not auth.allowed("azure", "POST /simulator/start")
    assert not auth.allowed(None, "GET /config")

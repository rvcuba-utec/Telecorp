from pulsored import domain


def test_catalog_comes_from_contracts():
    assert "LIM-SURCO" in domain.ZONES and len(domain.ZONES) == 12
    assert domain.ANTENNA_ZONE["ANT-LIM-001"] == "LIM-SURCO"
    assert domain.CATEGORIES == ["SIN_INTERNET", "INTERNET_LENTO", "SIN_SENAL", "LLAMADAS", "SMS", "OTRO"]
    assert len(domain.EVENT_TYPES) == 7
    assert domain.STATUS_LABELS["EN_ATENCION"] == "Siendo atendida"


def test_transitions():
    assert domain.can_transition("REGISTRADA", "EN_REVISION")
    assert domain.can_transition("SOLUCIONADA", "EN_REVISION")  # reapertura
    assert not domain.can_transition("REGISTRADA", "SOLUCIONADA")
    assert not domain.can_transition("CERRADA", "EN_REVISION")


def test_priority_health_beats_residential():
    salud = domain.compute_priority(5000, "SALUD", False, 0.7, 1, 0)
    resid = domain.compute_priority(12, "RESIDENCIAL", False, 0.0, 1, 0)
    assert salud["score"] > resid["score"]
    assert salud["level"] in ("ALTA", "CRITICA")
    assert resid["level"] in ("BAJA", "MEDIA")


def test_priority_levels_and_bounds():
    assert domain.priority_level(75) == "CRITICA"
    assert domain.priority_level(74) == "ALTA"
    assert domain.priority_level(25) == "MEDIA"
    assert domain.priority_level(24) == "BAJA"
    assert domain.compute_priority(10 ** 6, "SALUD", True, 1.0, 99, 99)["score"] == 100


def _ev(ant, typ, at, state=None, **kw):
    return {"id": f"{ant}-{at}", "antenna_id": ant, "event_type": typ, "occurred_at": at, "new_state": state, **kw}


def test_antenna_evidence_by_new_state_and_order():
    evs = [_ev("A2", "RECUPERACION", "2026-11-15T10:30:00.000000Z", "NORMAL"),     # llega desordenado
           _ev("A1", "FALLA", "2026-11-15T10:00:00.000000Z", "CAIDA"),
           _ev("A1", "FALLA", "2026-11-15T10:05:00.000000Z", "INHABILITADA"),
           _ev("A2", "FALLA", "2026-11-15T10:01:00.000000Z", "CAIDA")]
    e = domain.antenna_evidence(evs)
    assert e["antenna_ids"] == ["A1"] and e["weight"] == 1.0 and e["confirmed"]
    assert e["first_anomaly_at"] == "2026-11-15T10:00:00.000000Z"


def test_weights_per_contract():
    assert domain.event_weight({"event_type": "FALLA", "new_state": "CAIDA"}) == 0.7
    assert domain.event_weight({"event_type": "SATURACION", "new_state": "SATURADA"}) == 0.5
    assert domain.event_weight({"event_type": "PREVENTIVA"}) == 0.0
    deg = domain.antenna_evidence([_ev("A1", "DEGRADACION", "2026-11-15T10:00:00.000000Z", "CAIDA")])
    assert not deg["confirmed"] and deg["weight"] == domain.DEGRADATION_WEIGHT
    prev = domain.antenna_evidence([_ev("A1", "PREVENTIVA", "2026-11-15T10:00:00.000000Z", "NORMAL")])
    assert not prev["confirmed"] and prev["antenna_ids"] == []


def test_ids_with_hyphenated_zones():
    assert domain.zone_of_report_id("REP-LIM-SURCO-ABCDEFGH") == "LIM-SURCO"
    assert domain.zone_of_incident_id("INC-LIM-SANMIGUEL-1763215200") == "LIM-SANMIGUEL"
    assert domain.zone_of_incident_id("INC-LIM-SURCO-PREVENTIVA-ANT-LIM-001-1") == "LIM-SURCO"
    assert domain.zone_of_report_id("basura") == "" and domain.zone_of_report_id("REP-NADA-X") == ""


def test_nearest_zone_and_critical_sites():
    surco = domain.ZONES["LIM-SURCO"]["centroid"]
    assert domain.nearest_zone(surco["lat"] + 0.001, surco["lng"]) == "LIM-SURCO"
    assert domain.critical_site_kind(["No hay señal cerca del Hospital"]) == "HOSPITAL"
    assert domain.critical_site_kind(["no tengo internet"]) is None


def test_correlation_group_modes():
    assert domain.correlation_group("LLAMADAS", "ZONA") == "ZONA"
    assert domain.correlation_group("LLAMADAS", "ZONA_Y_CATEGORIA") == "VOZ"


def test_cosmos_clean_handles_scalar_query_results():
    """SELECT VALUE COUNT(1) en Cosmos devuelve enteros, no documentos (falló en el primer despliegue)."""
    from pulsored.store import CosmosStore, clean

    assert clean(7) == 7
    assert clean({"id": "x", "_rid": "r", "_etag": "e"}) == {"id": "x"}

    class FakeContainer:
        def query_items(self, sql, parameters=None, **kw):
            return [3] if "COUNT" in sql else [{"id": "a", "_ts": 1}]

    s = CosmosStore.__new__(CosmosStore)
    s._c = {"reports": FakeContainer(), "incident_users": FakeContainer(), "incidents": FakeContainer()}
    assert s.count_reports_by_user_since("u", "2026-01-01") == 3
    assert s.count_incident_users("INC-x") == 3
    assert s.count_incidents_since("LIM-SURCO", "2026-01-01") == 3

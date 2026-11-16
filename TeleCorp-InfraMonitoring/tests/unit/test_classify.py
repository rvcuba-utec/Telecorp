import pytest

from common.classify import classify


@pytest.mark.parametrize("x,expected", [
    (0, "INHABILITADA"), (0.1, "CAIDA"), (59.9, "CAIDA"), (60, "NORMAL"), (129, "NORMAL"), (129.1, "SATURADA"),
    (0.0, "INHABILITADA"), (95, "NORMAL"), (200, "SATURADA")])
def test_umbrales(x, expected):
    assert classify(x) == expected


def test_umbrales_configurables():
    assert classify(70, down=80) == "CAIDA"
    assert classify(100, saturated=99) == "SATURADA"

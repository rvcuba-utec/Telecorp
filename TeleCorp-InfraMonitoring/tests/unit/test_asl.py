"""Chequeo estático de las 4 máquinas de estado: sin referencias rotas, ni estados huérfanos."""
import json
import re
from pathlib import Path

import pytest

SM = Path(__file__).resolve().parents[2] / "statemachines"
TEMPLATE = (Path(__file__).resolve().parents[2] / "template.yaml").read_text()
FILES = sorted(SM.glob("*.asl.json"))


def targets(state):
    out = [state[k] for k in ("Next", "Default") if k in state]
    out += [c["Next"] for c in state.get("Choices", []) if "Next" in c]
    out += [c["Next"] for c in state.get("Catch", [])]
    return out


def walk(defn):
    """Itera (nombre, estado, definicion_contenedora) incluyendo los procesadores de Map."""
    yield from ((n, s, defn) for n, s in defn["States"].items())
    for s in defn["States"].values():
        if s["Type"] == "Map":
            yield from walk(s["ItemProcessor"])


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_referencias_y_alcanzabilidad(path):
    d = json.loads(path.read_text())
    for name, st, cont in walk(d):
        for t in targets(st):
            assert t in cont["States"], f"{path.stem}: {name} -> {t} no existe"
        assert st["Type"] in ("Choice", "Succeed", "Fail") or "Next" in st or st.get("End"), f"{name} sin salida"
        if st["Type"] == "Choice":
            assert "Default" in st or st["Choices"], name
    for cont in [d] + [s["ItemProcessor"] for _, s, _ in walk(d) if s["Type"] == "Map"]:
        seen, todo = set(), [cont["StartAt"]]
        while todo:
            n = todo.pop()
            if n in seen:
                continue
            seen.add(n)
            todo += targets(cont["States"][n])
        assert seen == set(cont["States"]), f"{path.stem}: huérfanos {set(cont['States']) - seen}"


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_sustituciones_definidas_en_el_template(path):
    used = set(re.findall(r"\$\{(\w+)\}", path.read_text()))
    block = TEMPLATE.split(f"statemachines/{path.name}")[1].split("Type: AWS::Serverless::StateMachine")[0]
    for name in used:
        assert f"        {name}:" in block, f"{path.name}: falta la sustitución {name}"


def test_lifecycle_cubre_el_flujo_documentado():
    d = json.loads((SM / "antenna-incident-lifecycle.asl.json").read_text())["States"]
    for needed in ("EnrichContext", "PublishFirstEvent", "Wait", "CheckRecovery", "Recovered?", "ProlongedOrTooLong?",
                   "EmitProlonged", "EnsureRecoveryPublished", "CloseEpisode", "NotifyOps"):
        assert needed in d
    assert d["PublishFirstEvent"]["Retry"][0]["MaxAttempts"] == 3
    assert d["CloseEpisode"]["Resource"].endswith("dynamodb:updateItem")
    assert json.loads((SM / "recurrence-analysis.asl.json").read_text())["States"]["Analyze"]["MaxConcurrency"] == 5

"""Every override is a limit the runner can check (GOALS.md, Every override
is a limit the runner holds a step to).

bringup/overrides.yaml is the one way a limit comes from a person, not the
design, and procgen merged whatever it held into the step's `expect`. A
misspelt key (`mx: 5.2`) left the generated max in force while the report
called the step overridden; `max: null` dropped the bound, so any reading
passed; `max: 5.2 V` (a string) aborted the run at that step on the bench;
`{}` was recorded as an override that changed nothing; and a min above the
max failed every reading. procedure_gen.py now refuses each, naming the step.
Every limit every fixture variant generates is still accepted as its own
override, so the check refuses only what a person got wrong.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NPIE = ROOT / "skill" / "scripts"
if str(NPIE) not in sys.path:
    sys.path.insert(0, str(NPIE))

from npielib import design, procgen  # noqa: E402
from test_npie_limit_fields import VARIANTS, _hall  # noqa: E402
from test_npie_limit_trace import FIX  # noqa: E402

# rails.02 is the +5V reading, generated as min 4.75 .. max 5.25
REFUSED = [
    ("rails.02: {mx: 5.2}", "mx is not a bound"),
    ("rails.02: {unit: mV}", "unit is not a bound"),
    ("rails.02: {edges_min: 2}", "edges_min is not a bound"),
    ("rails.02: {max: null}", "max: None is not a number"),
    ("rails.02: {max: 5.2 V}", "max: '5.2 V' is not a number"),
    ("rails.02: {max: true}", "max: True is not a number"),
    ("rails.02: {max: .nan}", "is not a number"),
    ("rails.02: {max: .inf}", "is not a number"),
    ("rails.02: {}", "empty"),
    ("rails.02: {min: 5.3}", "min 5.3 is above max 5.25"),
    ("rails.02: {min: 5.1, max: 4.9}", "min 5.1 is above max 4.9"),
    ("rails.02: {nominal: 6}", "nominal 6 is outside"),
]


def _ws(tmp_path: Path, variant=None) -> Path:
    ws = tmp_path / "mini-bldc"
    shutil.copytree(FIX, ws)
    if variant:
        variant(ws)
    (ws / "bringup").mkdir(exist_ok=True)
    return ws


def _overrides(ws: Path, text: str) -> None:
    (ws / "bringup" / "overrides.yaml").write_text(text + "\n")


def _steps(proc: dict) -> dict[str, dict]:
    return {s["id"]: s for st in proc["stages"] for s in st["steps"]}


@pytest.mark.parametrize("text,why", REFUSED, ids=[t for t, _ in REFUSED])
def test_an_override_the_runner_cannot_hold_is_refused(tmp_path, text, why):
    ws = _ws(tmp_path)
    _overrides(ws, text)
    with pytest.raises(design.DesignError) as e:
        procgen.generate(design.load(ws))
    assert "overrides.yaml: step rails.02: " in str(e.value) and why in str(e.value)


def test_a_logic_step_takes_only_edges_min(tmp_path):
    ws = _ws(tmp_path, _hall)
    logic = next(s for s in _steps(procgen.generate(design.load(ws))).values()
                 if s["type"] == "logic")
    _overrides(ws, f"{logic['id']}: {{min: 1}}")
    with pytest.raises(design.DesignError, match="min is not a bound of this limit"):
        procgen.generate(design.load(ws))
    _overrides(ws, f"{logic['id']}: {{edges_min: 4}}")
    assert _steps(procgen.generate(design.load(ws)))[logic["id"]]["expect"] == {"edges_min": 4}


def test_the_cli_refuses_it_with_exit_2_and_writes_nothing(tmp_path):
    ws = _ws(tmp_path)
    _overrides(ws, "rails.02: {mx: 5.2}")
    r = subprocess.run([sys.executable, str(NPIE / "procedure_gen.py"), "--workspace", str(ws)],
                       capture_output=True, text=True)
    assert r.returncode == 2, r.stdout
    assert "step rails.02: mx is not a bound" in json.loads(r.stdout)["error"]
    assert not (ws / "bringup" / "procedure.json").exists()


def test_an_integer_bound_is_a_number(tmp_path):
    ws = _ws(tmp_path)
    _overrides(ws, "rails.02: {max: 5}")
    assert _steps(procgen.generate(design.load(ws)))["rails.02"]["expect"]["max"] == 5


@pytest.mark.parametrize("variant", VARIANTS, ids=[v.__name__.strip("_") for v in VARIANTS])
def test_every_generated_limit_is_accepted_as_its_own_override(tmp_path, variant):
    ws = _ws(tmp_path, variant)
    base = _steps(procgen.generate(design.load(ws)))
    bounds = {"min", "max", "nominal", "edges_min"}
    ov = {sid: {k: v for k, v in s["expect"].items() if k in bounds and v is not None}
          for sid, s in base.items() if s.get("expect")}
    ov = {sid: f for sid, f in ov.items() if f}
    assert ov
    _overrides(ws, json.dumps(ov))          # JSON is YAML
    got = _steps(procgen.generate(design.load(ws)))
    assert {sid: got[sid]["expect"] for sid in ov} == {sid: base[sid]["expect"] for sid in ov}

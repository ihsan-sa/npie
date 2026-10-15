"""Nothing above the rated input (GOALS.md, No supply setpoint above the
input's rating; SKILL.md rule 4): no `supply` step a procedure carries sets
the supply above the input's rating in constraints voltages[], nor above
the requirements' VIN max.

procgen took the first power and the operating-range corners from
requirements.md alone, so a requirements range wider than the input's
rating (a typo, or a stale requirements.md) became a supply step above it,
and a board with no input net still spun its motor at a procgen constant.
A setpoint above the rating is now listed in `skipped` with both numbers.
Every fixture variant of test_npie_limit_fields is checked, plus the three
cases that reach the new skips.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NPIE = ROOT / "skill" / "scripts"
if str(NPIE) not in sys.path:
    sys.path.insert(0, str(NPIE))

from npielib import design, procgen  # noqa: E402
from test_npie_limit_fields import VARIANTS  # noqa: E402
from test_npie_limit_trace import FIX, _spin  # noqa: E402


def _range_over_rating(ws: Path) -> None:
    """requirements VIN max 40 V on an input constraints rates at 30 V."""
    (ws / "requirements.md").write_text("# mini\n\nInput 8-40 V operating range.\n")


def _first_power_over_rating(ws: Path) -> None:
    """VIN min + headroom is already above the 30 V rating."""
    (ws / "requirements.md").write_text("# mini\n\nInput 29-40 V operating range.\n")


def _no_input(ws: Path) -> None:
    """No input net in constraints voltages[], and a firmware that can spin."""
    _spin(ws)
    cp = ws / "kicad" / "constraints.json"
    c = json.loads(cp.read_text())
    c["voltages"] = [v for v in c["voltages"] if v["net"] == "GND"]
    cp.write_text(json.dumps(c))


ALL = VARIANTS + [_range_over_rating, _first_power_over_rating, _no_input]


def _generate(tmp_path: Path, variant) -> tuple[dict, design.Design]:
    ws = tmp_path / "mini-bldc"
    shutil.copytree(FIX, ws)
    variant(ws)
    d = design.load(ws)
    return procgen.generate(d), d


def _supply_volts(proc: dict) -> list[tuple[str, float]]:
    return [(s["id"], s["set"]["v"]) for st in proc["stages"] for s in st["steps"]
            if s["type"] == "supply"]


@pytest.mark.parametrize("variant", ALL, ids=[v.__name__.strip("_") for v in ALL])
def test_no_supply_step_above_the_rated_input(tmp_path, variant):
    proc, d = _generate(tmp_path, variant)
    inp = proc["design"]["input"]
    supplies = _supply_volts(proc)
    if inp is None:
        assert not supplies, f"supply steps with no input net to rate them: {supplies}"
        return
    caps = [d.voltages[inp]] + ([proc["design"]["vin_range"][1]]
                                if proc["design"]["vin_range"] else [])
    over = [(sid, v) for sid, v in supplies if v > min(caps)]
    assert not over, f"supply above min({caps}) V: {over}"


def _skipped(proc: dict) -> dict[str, str]:
    return {s["stage"]: s["reason"] for s in proc["skipped"]}


def test_a_vin_max_above_the_rating_is_listed_not_applied(tmp_path):
    proc, _ = _generate(tmp_path, _range_over_rating)
    why = _skipped(proc)["limits:vin-max"]
    assert "requirements VIN max 40 V" in why and "voltages[VM_IN] 30 V" in why
    assert "limits:vin-min" not in _skipped(proc)
    assert 8 in [v for _, v in _supply_volts(proc)]


def test_a_first_power_above_the_rating_stops_every_supply_step(tmp_path):
    proc, _ = _generate(tmp_path, _first_power_over_rating)
    why = _skipped(proc)["power-up"]
    assert "31 V" in why and "voltages[VM_IN] 30 V" in why
    assert not _supply_volts(proc)
    assert "limits" in _skipped(proc)


def test_no_spin_without_a_power_up_stage(tmp_path):
    proc, _ = _generate(tmp_path, _no_input)
    assert "full-function" in _skipped(proc)
    assert "full-function" not in [st["id"] for st in proc["stages"]]

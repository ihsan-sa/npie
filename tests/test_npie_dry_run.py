"""Every fixture variant passes a dry run (GOALS.md, Every fixture variant
passes a dry run): test_npie_run_safe_end dry-runs each one to `passed`, and
this file holds procgen to the two rules whose breach made three of them
fail.

A board procgen never powers (no input net, or a first power above the
input's rating) still got a programming stage, "the board stays powered from
the supply", and a blocks stage talking to its firmware, so a run failed at
the flash. Now both need the power-up stage, as the rails stage did.

A boost's open-loop D0 aimed at half safety.vout_ov_v and nothing else, so a
manifest output-voltage trip below that level, or an output rated below it,
was no bound: `fallbacks` (vout_ov_v 400 V, the ovp trip at 22 V) ran D0 to
150 V, the trip fired, and every switching check after it read a stopped
bridge. Now D0 aims at half the lowest of the three.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NPIE = ROOT / "skill" / "scripts"
if str(NPIE) not in sys.path:
    sys.path.insert(0, str(NPIE))

from npielib import design, procgen  # noqa: E402
from test_npie_limit_trace import FIX, _boost  # noqa: E402
from test_npie_rated_input import ALL, _first_power_over_rating, _no_input  # noqa: E402


def _generate(tmp_path: Path, variant) -> tuple[dict, design.Design]:
    ws = tmp_path / "mini-bldc"
    shutil.copytree(FIX, ws)
    variant(ws)
    d = design.load(ws)
    return procgen.generate(d), d


def _steps(proc: dict) -> list[dict]:
    return [s for st in proc["stages"] for s in st["steps"]]


@pytest.mark.parametrize("variant", [_no_input, _first_power_over_rating],
                         ids=["no-input", "first-power-over-rating"])
def test_nothing_needs_a_board_procgen_never_powers(tmp_path, variant):
    proc, _ = _generate(tmp_path, variant)
    ids = [st["id"] for st in proc["stages"]]
    assert "power-up" not in ids
    assert not {"rails", "programming", "blocks"} & set(ids), ids
    skipped = {s["stage"]: s["reason"] for s in proc["skipped"]}
    assert "power-up stage was not generated" in skipped["programming"]
    assert "power-up stage was not generated" in skipped["blocks"]
    assert not [s["id"] for s in _steps(proc) if s["type"] in ("flash", "console")]


def _levels(proc: dict, d: design.Design) -> dict[str, float]:
    """What the open loop must stay under, worked out here, not by procgen."""
    m, out = d.manifest or {}, proc["design"]["boost"]["out"]
    lv = {f"trips[{t['name']}]": float(t["threshold"]) for t in m.get("trips", [])
          if t.get("unit") == "V" and "OUT" in str(t.get("sense_net", "")).upper()}
    if (m.get("safety") or {}).get("vout_ov_v"):
        lv["safety.vout_ov_v"] = float(m["safety"]["vout_ov_v"])
    if out in d.voltages:
        lv[f"voltages[{out}]"] = float(d.voltages[out])
    return lv


BOOSTS = [v for v in ALL if "boost" in v.__name__ or v.__name__ == "_fallbacks"]


def test_the_boost_variants_are_found():
    assert {v.__name__ for v in BOOSTS} == {"_boost", "_boost_out", "_boost_low", "_fallbacks"}


@pytest.mark.parametrize("variant", BOOSTS, ids=[v.__name__.strip("_") for v in BOOSTS])
def test_d0_keeps_the_output_under_every_trip_and_its_rating(tmp_path, variant):
    proc, d = _generate(tmp_path, variant)
    steps = _steps(proc)
    vin = next(s["set"]["v"] for s in steps if s["type"] == "supply")
    d0 = next(float(s["send"].split()[1]) for s in steps
              if (s.get("text") or "").startswith("open loop at D0"))
    lv = _levels(proc, d)
    assert lv and vin / (1 - d0) < min(lv.values()), (vin, d0, lv)
    # the limits stage's under-voltage check switches at the same D0
    uv = [s for s in steps if s["id"].startswith("limits.") and str(s.get("send")).startswith("duty")]
    assert all(s["send"] == f"duty {d0:g}" for s in uv), uv


def test_d0_names_an_output_trip_below_the_over_voltage_level(tmp_path):
    """The boost fixture's ovp trip (22 V) is below its vout_ov_v (24 V)."""
    proc, _ = _generate(tmp_path, _boost)
    d0 = next(s for s in _steps(proc) if (s.get("text") or "").startswith("open loop at D0"))
    assert "manifest trips[ovp].threshold 22 V" in d0["derived_from"], d0["derived_from"]
    assert d0["send"] == f"duty {round(1 - 10 / (procgen.D0_AIM_FRAC * 22), 3):g}"

"""Every protection the firmware gives is tested or listed as not (GOALS.md,
Every firmware protection is tested or listed as not).

The manifest's input protections (safety.vbus_uv_v, vbus_ov_v, i_trip_a)
and each `trips[]` entry are design facts a procedure either checks or says
it doesn't. procgen tested the under-voltage trip only when it sat below
VIN min, listed the over-voltage trip only when it sat above VIN max, never
mentioned i_trip_a, and dropped every trip on a board with no boost, each
without a word. A board whose trip sits inside its own operating range is a
design that contradicts itself, and the procedure has to say so. A boost
output with no rating whose max duty can't reach its trip crashed procgen.

Each protection must be named, as `safety.<key>` or `trips[<name>]`, in a
step's `derived_from` or in a `skipped` reason, unless its whole stage is
skipped (`limits` for the safety keys, `power-stage` for the trips).
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
from test_npie_limit_trace import FIX, _boost, _boost_out  # noqa: E402
from test_npie_rated_input import ALL  # noqa: E402

SAFETY = ("vbus_uv_v", "vbus_ov_v", "i_trip_a")


def _manifest(ws: Path, edit) -> None:
    mf = ws / "firmware" / "fwe-manifest.json"
    m = json.loads(mf.read_text())
    edit(m)
    mf.write_text(json.dumps(m))


def _uv_in_range(ws: Path) -> None:
    """An under-voltage trip at 9 V on an 8-24 V input."""
    _manifest(ws, lambda m: m["safety"].update(vbus_uv_v=9.0))


def _ov_in_range(ws: Path) -> None:
    """An over-voltage trip at 20 V on an 8-24 V input."""
    _manifest(ws, lambda m: m["safety"].update(vbus_ov_v=20.0))


def _trips_no_boost(ws: Path) -> None:
    """The boost variant's manifest trips on a board with no boost block."""
    probe = ws.parent / "probe"
    shutil.copytree(ws, probe)
    _boost(probe)
    trips = json.loads((probe / "firmware" / "fwe-manifest.json").read_text())["trips"]
    shutil.rmtree(probe)
    _manifest(ws, lambda m: m.update(trips=trips))


def _unrated_out(ws: Path) -> None:
    """The boost on its own /VOUT with no rating, and a max duty that can't
    reach the 22 V ovp trip from the first power."""
    _boost_out(ws)
    cp = ws / "kicad" / "constraints.json"
    c = json.loads(cp.read_text())
    c["voltages"] = [v for v in c["voltages"] if v["net"] != "/VOUT"]
    cp.write_text(json.dumps(c))
    _manifest(ws, lambda m: m["pwm"].update(max_duty=0.2))


def _ocp_unsensed(ws: Path) -> None:
    """The boost with its ocp trip sensing a net the netlist doesn't have."""
    _boost(ws)
    _manifest(ws, lambda m: next(t for t in m["trips"] if t["name"] == "ocp")
              .update(sense_net="NO_SUCH_NET"))


NEW = [_uv_in_range, _ov_in_range, _trips_no_boost, _unrated_out, _ocp_unsensed]


def _generate(tmp_path: Path, variant) -> tuple[dict, dict]:
    ws = tmp_path / "mini-bldc"
    shutil.copytree(FIX, ws)
    variant(ws)
    d = design.load(ws)
    return procgen.generate(d), d.manifest or {}


def _skipped(proc: dict) -> dict[str, str]:
    return {s["stage"]: s["reason"] for s in proc["skipped"]}


@pytest.mark.parametrize("variant", ALL + NEW, ids=[v.__name__.strip("_") for v in ALL + NEW])
def test_every_protection_is_tested_or_listed(tmp_path, variant):
    proc, m = _generate(tmp_path, variant)
    said = [s.get("derived_from") or "" for st in proc["stages"] for s in st["steps"]]
    said += [s["reason"] for s in proc["skipped"]]
    skipped = _skipped(proc)
    wanted = [(f"safety.{k}", "limits") for k in SAFETY if (m.get("safety") or {}).get(k)
              is not None]
    wanted += [(f"trips[{t['name']}]", "power-stage") for t in m.get("trips") or []]
    unsaid = [name for name, stage in wanted
              if stage not in skipped and not any(name in s for s in said)]
    assert not unsaid, f"protections neither tested nor listed: {unsaid}"


def test_an_under_voltage_trip_inside_the_range_is_listed(tmp_path):
    proc, _ = _generate(tmp_path, _uv_in_range)
    why = _skipped(proc)["limits:uv"]
    assert "9 V" in why and "VIN min 8 V" in why and "not tested" in why
    assert not any("vbus_uv_v" in (s.get("derived_from") or "")
                   for st in proc["stages"] for s in st["steps"])


def test_an_over_voltage_trip_inside_the_range_is_listed(tmp_path):
    proc, _ = _generate(tmp_path, _ov_in_range)
    why = _skipped(proc)["limits:ov"]
    assert "20 V" in why and "VIN max 24 V" in why and "not tested" in why


def test_the_input_over_current_trip_is_listed(tmp_path):
    proc, _ = _generate(tmp_path, lambda ws: None)
    assert "safety.i_trip_a 10 A" in _skipped(proc)["limits:i-trip"]


def test_trips_on_a_board_with_no_boost_are_listed(tmp_path):
    proc, _ = _generate(tmp_path, _trips_no_boost)
    why = _skipped(proc)["power-stage"]
    assert "trips[ovp]" in why and "trips[ocp]" in why and "no boost" in why
    assert "power-stage" not in [st["id"] for st in proc["stages"]]


def test_an_unrated_output_its_duty_cannot_trip_is_listed(tmp_path):
    proc, _ = _generate(tmp_path, _unrated_out)
    why = _skipped(proc)["power-stage:ovp"]
    assert "pwm.max_duty and nothing reaches the 22 V trip" in why
    assert "trips[ocp]" in " ".join(s.get("derived_from") or ""
                                    for st in proc["stages"] for s in st["steps"])


def test_a_trip_on_a_missing_sense_net_is_listed(tmp_path):
    proc, _ = _generate(tmp_path, _ocp_unsensed)
    assert "'NO_SUCH_NET'" in _skipped(proc)["power-stage:ocp"]

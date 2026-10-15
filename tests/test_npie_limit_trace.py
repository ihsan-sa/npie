"""Every pass/fail limit traces to the board's design files (GOALS.md, Limits
from the design): each step carrying a limit, an `expect` or a console
`fields` entry, says in `derived_from` which design input it came from.

The design inputs are the ones procedure.json lists under `inputs`: the
netlist, constraints.json, the BOM, requirements.md and the fwe manifest
(and bringup/overrides.yaml when a person overrides a limit). Fixture boards
only, so the check runs everywhere.

A bare input name is not enough: `derived_from` has to point into the input,
as in `manifest commands[duty]`, `manifest safety.vout_ov_v`, `constraints
voltages[+3V3]`, `requirements VIN` or `netlist U3`. That is still only a
format check, so a reviewer has to confirm that the named field really gives
the limit. A limit that comes from a procgen constant because the design
sets none says `procgen default`, and fails here: the fixture's constraints
carry a `bringup` block for the short-check floors and the spin current.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NPIE = ROOT / "skill" / "scripts"
FIX = ROOT / "tests" / "fixtures" / "npie" / "mini-bldc"
if str(NPIE) not in sys.path:
    sys.path.insert(0, str(NPIE))

from npielib import design, procgen  # noqa: E402

DESIGN_INPUT_RE = re.compile(
    r"\bmanifest [a-z_]+(\.[a-z_]+|\[[^\]]+\])"
    r"|\bconstraints [a-z_]+\["
    r"|\brequirements VIN\b"
    r"|\bnetlist [A-Z+/][\w+/*-]*"
    r"|\bBOM [A-Z]+\d")
EVN_BLOCK = {"topology": "board-to-board-header", "block": "B1",
             "name": "J9 2x20 2.54 male to EVN JP8", "operating_point": {"vio_v": 3.3}}


def _mini_bldc(ws: Path) -> None:
    pass


def _no_manifest(ws: Path) -> None:
    shutil.rmtree(ws / "firmware")


def _evn_header(ws: Path) -> None:
    cp = ws / "kicad" / "constraints.json"
    c = json.loads(cp.read_text())
    c["blocks"] = [EVN_BLOCK]
    cp.write_text(json.dumps(c))


def _spin(ws: Path) -> None:
    mf = ws / "firmware" / "fwe-manifest.json"
    m = json.loads(mf.read_text())
    m["commands"].append({"name": "spin", "args": "", "reply": "OK {json}", "safe": False})
    mf.write_text(json.dumps(m))


def _boost(ws: Path) -> None:
    """A boost block and a manifest pwm block make Q1/Q2 a boost, so procgen
    emits power-stage: closed loop, open loop, PWM, dead time and both trips."""
    cp = ws / "kicad" / "constraints.json"
    c = json.loads(cp.read_text())
    c.setdefault("blocks", []).append({"topology": "boost", "block": "B2", "name": "Q1/Q2 boost"})
    cp.write_text(json.dumps(c))
    mf = ws / "firmware" / "fwe-manifest.json"
    m = json.loads(mf.read_text())
    m["pwm"] = {"freq_hz": 200000, "dead_time_ns": 50, "max_duty": 0.8,
                "outputs": [{"role": "hrtim_lo", "net": "PHASE_A"},
                            {"role": "hrtim_hi", "net": "PHASE_A"}]}
    m["safety"].update({"vout_ov_v": 24.0, "vout_target_v": 20.0})
    m["trips"] = [
        {"name": "ovp", "sense_net": "VOUT_DIV", "threshold": 22.0, "unit": "V",
         "comparator": "COMP1", "fault_input": "FLT1", "evt_regex": "^EVT .*ovp",
         "outputs": "inactive", "latched": True, "clear": "clear"},
        {"name": "ocp", "sense_net": "LS_SRC_A", "threshold": 8.0, "unit": "A",
         "comparator": "COMP2", "fault_input": "FLT2", "evt_regex": "^EVT .*ocp",
         "outputs": "inactive", "latched": True, "clear": "clear"}]
    mf.write_text(json.dumps(m))


@pytest.mark.parametrize("variant", [_mini_bldc, _no_manifest, _evn_header, _spin, _boost],
                         ids=["mini-bldc", "no-manifest", "evn-header", "spin", "boost"])
def test_every_limit_names_its_design_input(tmp_path, variant):
    ws = tmp_path / "mini-bldc"
    shutil.copytree(FIX, ws)
    variant(ws)
    proc = procgen.generate(design.load(ws))
    untraced = []
    for s in (s for st in proc["stages"] for s in st["steps"]
              if s.get("expect") or s.get("fields")):
        src = s.get("derived_from") or ""
        if "procgen default" in src or not DESIGN_INPUT_RE.search(src):
            untraced.append(f"{s['id']} {s.get('text')!r}: {src!r}")
    assert not untraced, "limits that name no design input:\n" + "\n".join(untraced)


def _bringup(ws: Path, block: dict | None) -> None:
    cp = ws / "kicad" / "constraints.json"
    c = json.loads(cp.read_text())
    c.pop("bringup", None)
    if block is not None:
        c["bringup"] = block
    cp.write_text(json.dumps(c))


def _limit_steps(proc: dict) -> dict:
    steps = [s for st in proc["stages"] for s in st["steps"] if s.get("expect")]
    return {s["text"]: s for s in steps
            if s["text"] in ("VM_IN to GND is not a short", "+3V3 to GND is not a short",
                             "spin current")}


def test_the_design_sets_the_short_floors_and_spin_current(tmp_path):
    ws = tmp_path / "mini-bldc"
    shutil.copytree(FIX, ws)
    _spin(ws)
    _bringup(ws, {"short_floor_power_ohm": 470, "short_floor_logic_ohm": 47,
                  "spin_ilimit_a": 2.5})
    by = _limit_steps(procgen.generate(design.load(ws)))
    assert by["VM_IN to GND is not a short"]["expect"]["min"] == 470
    assert "constraints bringup[short_floor_power_ohm]=470" in \
        by["VM_IN to GND is not a short"]["derived_from"]
    assert by["+3V3 to GND is not a short"]["expect"]["min"] == 47
    assert by["spin current"]["expect"]["max"] == 2.25
    assert "constraints bringup[spin_ilimit_a]=2.5" in by["spin current"]["derived_from"]


def test_without_a_bringup_block_the_defaults_say_so(tmp_path):
    ws = tmp_path / "mini-bldc"
    shutil.copytree(FIX, ws)
    _spin(ws)
    _bringup(ws, None)
    by = _limit_steps(procgen.generate(design.load(ws)))
    assert by["VM_IN to GND is not a short"]["expect"]["min"] == procgen.R_FLOOR_POWER
    assert by["+3V3 to GND is not a short"]["expect"]["min"] == procgen.R_FLOOR_LOGIC
    assert by["spin current"]["expect"]["max"] == round(0.9 * procgen.SPIN_ILIMIT_A, 3)
    assert all("procgen default" in s["derived_from"] for s in by.values())


def test_a_bringup_limit_that_is_not_a_number_is_refused(tmp_path):
    ws = tmp_path / "mini-bldc"
    shutil.copytree(FIX, ws)
    _bringup(ws, {"short_floor_power_ohm": "1k"})
    with pytest.raises(design.DesignError, match="short_floor_power_ohm"):
        procgen.generate(design.load(ws))


def test_the_boost_variant_reaches_every_power_stage_check(tmp_path):
    ws = tmp_path / "mini-bldc"
    shutil.copytree(FIX, ws)
    _boost(ws)
    proc = procgen.generate(design.load(ws))
    assert not [s for s in proc["skipped"] if s["stage"].startswith("power-stage")]
    steps = next(st for st in proc["stages"] if st["id"] == "power-stage")["steps"]
    texts = {s.get("text") for s in steps}
    assert {"the voltage loop regulates the output", "PHASE_A PWM frequency",
            "PHASE_A duty", "ovp trips on a real over-voltage",
            "ocp trip reported"} <= texts
    assert any(s.get("quantity") == "deadtime" for s in steps)

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
the limit. A limit that comes from a procgen constant and nothing in the
design says `procgen default`, and fails here unless RULE_ONLY lists it.
RULE_ONLY is empty: the bench limits no other input gives (short floors,
spin current) come from constraints `bringup{}`.
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
# limits that are procgen constants with nothing in the design behind them yet,
# by step text (the short floors and spin current now come from constraints
# bringup{}, which the fixture carries)
RULE_ONLY: tuple[str, ...] = ()
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


def _hall(ws: Path) -> None:
    """Hall sensor nets on U1, so procgen emits the logic analyser step."""
    net = ws / "kicad" / "mini-bldc.net"
    hall = "".join(f'    (net (code "{900 + i}") (name "/HALL_{c}")\n'
                   f'      (node (ref "U1") (pin "{20 + i}") (pintype "input")))\n'
                   for i, c in enumerate("ABC"))
    net.write_text(re.sub(r"(\(nets\s*\n)", lambda m: m.group(1) + hall, net.read_text(), count=1))


def _rails(ws: Path) -> None:
    """The rails stage's other branches: /VM_PROT rated like the input (a rail
    that follows it), a D2 catch diode from the buck's SW node to +5V (a
    catch-diode converter), and a +5V load light enough that the first
    current limit is not clamped to FIRST_ILIMIT_A."""
    net = ws / "kicad" / "mini-bldc.net"
    s = net.read_text()
    s = s.replace('    (comp (ref "D1")',
                  '    (comp (ref "D2") (value "SS34") (footprint "X"))\n    (comp (ref "D1")', 1)
    s = re.sub(r'(\(name "/regulators/BUCK_SW"\)\n)',
               lambda m: m.group(1) + '      (node (ref "D2") (pin "2") (pintype "passive"))\n', s)
    s = re.sub(r'(\(name "\+5V"\)\n)',
               lambda m: m.group(1) + '      (node (ref "D2") (pin "1") (pintype "passive"))\n', s)
    s = re.sub(r"(\(nets\s*\n)", lambda m: m.group(1) + (
        '    (net (code "950") (name "/VM_PROT")\n'
        '      (node (ref "U2") (pin "3") (pintype "power_in")))\n'), s, count=1)
    net.write_text(s)
    cp = ws / "kicad" / "constraints.json"
    c = json.loads(cp.read_text())
    c["voltages"].append({"net": "/VM_PROT", "voltage": 30})
    for p in c["power"]:
        if p["net"] == "+5V":
            p["current_a"] = 0.1
    cp.write_text(json.dumps(c))


def _boost_out(ws: Path) -> None:
    """The boost with Q1's drain on its own /VOUT, not the input, so the boost
    output is a rail the rails stage measures at idle."""
    _boost(ws)
    net = ws / "kicad" / "mini-bldc.net"
    drain = '      (node (ref "Q1") (pin "5") (pinfunction "D_5") (pintype "passive"))\n'
    s = net.read_text().replace(drain, "", 1)
    s = re.sub(r"(\(nets\s*\n)", lambda m: m.group(1) + (
        f'    (net (code "951") (name "/VOUT")\n{drain}    )\n'), s, count=1)
    net.write_text(s)
    cp = ws / "kicad" / "constraints.json"
    c = json.loads(cp.read_text())
    c["voltages"].append({"net": "/VOUT", "voltage": 30})
    cp.write_text(json.dumps(c))


def _boost_low(ws: Path) -> None:
    """The boost with its over-voltage level at twice the input, so D0 is held
    up at procgen's DUTY_MIN."""
    _boost(ws)
    mf = ws / "firmware" / "fwe-manifest.json"
    m = json.loads(mf.read_text())
    m["safety"]["vout_ov_v"] = 20.0
    mf.write_text(json.dumps(m))


def _fallbacks(ws: Path) -> None:
    """Every input procgen falls back from: an EVN header with no vio_v, no
    operating range in requirements.md (the setpoint is half the input rating),
    a load light enough that the first current limit sits on ILIM_FLOOR_A, and
    a boost with no pwm.max_duty whose over-voltage level asks for more duty
    than DUTY_MAX_DEFAULT."""
    _boost(ws)
    cp = ws / "kicad" / "constraints.json"
    c = json.loads(cp.read_text())
    c["blocks"].append({k: v for k, v in EVN_BLOCK.items() if k != "operating_point"})
    for p in c["power"]:
        if p["net"] in ("+5V", "+3V3"):
            p["current_a"] = 0.001
    cp.write_text(json.dumps(c))
    (ws / "requirements.md").write_text("# mini\n")
    mf = ws / "firmware" / "fwe-manifest.json"
    m = json.loads(mf.read_text())
    del m["pwm"]["max_duty"]
    m["safety"]["vout_ov_v"] = 400.0
    mf.write_text(json.dumps(m))


@pytest.mark.parametrize("variant", [_mini_bldc, _no_manifest, _evn_header, _spin, _boost,
                                     _rails, _boost_out, _boost_low],
                         ids=["mini-bldc", "no-manifest", "evn-header", "spin", "boost",
                              "rails", "boost-out", "boost-low"])
def test_every_limit_names_its_design_input(tmp_path, variant):
    ws = tmp_path / "mini-bldc"
    shutil.copytree(FIX, ws)
    variant(ws)
    proc = procgen.generate(design.load(ws))
    untraced = []
    for s in (s for st in proc["stages"] for s in st["steps"]
              if s.get("expect") or s.get("fields")):
        src = s.get("derived_from") or ""
        if "procgen default" in src:
            if not any(re.search(r, s.get("text") or "") for r in RULE_ONLY):
                untraced.append(f"{s['id']} {s.get('text')!r}: {src!r} (not in RULE_ONLY)")
        elif not DESIGN_INPUT_RE.search(src):
            untraced.append(f"{s['id']} {s.get('text')!r}: {src!r}")
    assert not untraced, "limits that name no design input:\n" + "\n".join(untraced)


def _bringup(ws: Path, bringup: dict | None) -> None:
    cp = ws / "kicad" / "constraints.json"
    c = json.loads(cp.read_text())
    if bringup is None:
        c.pop("bringup", None)
    else:
        c["bringup"] = bringup
    cp.write_text(json.dumps(c))


def _limit(proc: dict, text: str) -> dict:
    return next(s for st in proc["stages"] for s in st["steps"] if s.get("text") == text)


def test_short_floors_and_spin_current_come_from_constraints_bringup(tmp_path):
    ws = tmp_path / "mini-bldc"
    shutil.copytree(FIX, ws)
    _spin(ws)
    _bringup(ws, {"short_floor_power_ohm": 2200, "short_floor_logic_ohm": 330,
                  "spin_current_a": 0.5})
    proc = procgen.generate(design.load(ws))
    vm, v33 = _limit(proc, "VM_IN to GND is not a short"), _limit(proc, "+3V3 to GND is not a short")
    assert vm["expect"]["min"] == 2200 and "bringup[short_floor_power_ohm]" in vm["derived_from"]
    assert v33["expect"]["min"] == 330 and "bringup[short_floor_logic_ohm]" in v33["derived_from"]
    spin = _limit(proc, "spin current")
    assert spin["expect"]["max"] == 0.45 and "bringup[spin_current_a]" in spin["derived_from"]


def test_without_bringup_the_defaults_say_so(tmp_path):
    ws = tmp_path / "mini-bldc"
    shutil.copytree(FIX, ws)
    _spin(ws)
    _bringup(ws, None)
    proc = procgen.generate(design.load(ws))
    for text, const, v in (("VM_IN to GND is not a short", "R_FLOOR_POWER", 1000),
                           ("+3V3 to GND is not a short", "R_FLOOR_LOGIC", 100)):
        s = _limit(proc, text)
        assert s["expect"]["min"] == v and f"procgen default {const}" in s["derived_from"]
    spin = _limit(proc, "spin current")
    assert spin["expect"]["max"] == 0.9
    assert "procgen default SPIN_ILIMIT_A" in spin["derived_from"]


def test_the_fallbacks_say_so(tmp_path):
    ws = tmp_path / "mini-bldc"
    shutil.copytree(FIX, ws)
    _fallbacks(ws)
    proc = procgen.generate(design.load(ws))
    pins = [s for st in proc["stages"] if st["id"] == "host-link" for s in st["steps"]
            if s.get("expect")]
    assert pins and all("procgen default VIO_DEFAULT_V" in s["derived_from"] for s in pins)
    assert pins[0]["expect"]["nominal"] == procgen.VIO_DEFAULT_V
    vbus = _limit(proc, "firmware reads the input voltage")
    assert vbus["fields"]["vbus_v"]["nominal"] == 15
    assert "constraints voltages[VM_IN]" in vbus["derived_from"]
    idle = _limit(proc, "idle input current")
    assert idle["expect"]["max"] == round(procgen.ILIM_CEIL_FRAC * procgen.ILIM_FLOOR_A, 4)
    duty = _limit(proc, "PHASE_A duty")
    assert duty["expect"]["nominal"] == procgen.DUTY_MAX_DEFAULT
    assert "procgen default DUTY_MAX_DEFAULT" in duty["derived_from"]


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

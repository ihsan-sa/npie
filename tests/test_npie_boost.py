"""npie on a GaN boost: PCB-0026-A (STM32G474 HRTIM driving an LMG2100).

The board's own netlist and fwe manifest (pwm, dead_time_ns, trips) come from
the boards repo, so these cases skip without it. The SimBoard cases that need
no board use the mini-bldc fixture or a manifest written here.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NPIE = ROOT / "skill" / "scripts"
FIX = ROOT / "tests" / "fixtures" / "npie" / "mini-bldc"
sys.path.insert(0, str(NPIE))
sys.path.insert(0, str(ROOT / "tests"))

from _boards import board_path, need_board  # noqa: E402
from npielib import design, instruments, procgen  # noqa: E402

BOARD = "PCB-0026-A_gan-boost-48v"


def _ws(tmp_path) -> Path:
    need_board(BOARD)
    src, ws = board_path(BOARD), tmp_path / BOARD
    ws.mkdir()
    for d in ("kicad", "fab"):
        shutil.copytree(src / d, ws / d)
    shutil.copy(src / "requirements.md", ws)
    (ws / "firmware").mkdir()
    shutil.copy(src / "firmware" / "fwe-manifest.json", ws / "firmware")
    return ws


def _man(ws) -> dict:
    return json.loads((ws / "firmware" / "fwe-manifest.json").read_text())


def _stage(proc, sid):
    return next(st for st in proc["stages"] if st["id"] == sid)


def _sim(proc, man):
    b = instruments.SimBoard(proc)
    b.set_manifest(man)
    b.supply(14.0, 0.2, True)
    b.flashed = True
    return b, instruments.SimConsole(b)


def test_integrated_half_bridge_and_boost_are_found(tmp_path):
    proc = procgen.generate(design.load(_ws(tmp_path)))
    dz = proc["design"]
    assert dz["half_bridges"] == [{"phase": "/power_stage/SW", "hs": "U201", "ls": "U201",
                                   "bus": "+48V", "ls_src": "GND", "ic": "U201"}]
    assert dz["boost"] == {"out": "+48V", "sw": "/power_stage/SW", "ic": "U201"}
    # a GaN half-bridge IC has no body diode; its switch node still gets a short check
    assert {"stage": "unpowered:U201", "reason": "U201 LMG2100R044 is an integrated "
            "half-bridge: no body diode to check"} in proc["skipped"]
    unp = _stage(proc, "unpowered")["steps"]
    assert not [s for s in unp if s.get("quantity") == "diode"]
    assert any(s.get("points", {}).get("plus", {}).get("net") == "/power_stage/SW" for s in unp)
    # bootstrap nets are not rails; the idle output follows the input
    assert "/power_stage/HB" not in dz["rails"] and "/regulators/BOOT" not in dz["rails"]
    out = next(s for s in _stage(proc, "rails")["steps"] if s.get("text") == "+48V voltage")
    assert out["expect"] == {"nominal": 14.0, "min": 11.0, "max": 14.14, "unit": "V"}


def test_power_stage_checks_vout_pwm_dead_time_and_every_trip(tmp_path):
    ws = _ws(tmp_path)
    man, proc = _man(ws), procgen.generate(design.load(ws))
    steps = _stage(proc, "power-stage")["steps"]
    by_text = {s.get("text"): s for s in steps}
    reg = by_text["the voltage loop regulates the output"]
    assert (reg["send"], reg["fields"]["vout_v"]["min"]) == ("status", 45.6)
    # open loop aimed at half the 55 V OVP level, and the meter reads Vin/(1-D)
    assert by_text["open loop at D0 = 0.491"]["send"] == "duty 0.491"
    vo = by_text["+48V follows the duty"]
    assert (vo["expect"]["min"], vo["expect"]["max"]) == (24.754, 30.255)
    assert by_text["PWM_LO PWM frequency"]["expect"]["nominal"] == 1e6
    dt = by_text["dead time PWM_LO falling to PWM_HI rising"]
    assert (dt["quantity"], dt["channel2"], dt["expect"]["min"], dt["expect"]["max"]) == \
        ("deadtime", 2, 8.0, 13.0)
    assert dt["points"]["ch2"]["net"] == "/PWM_HI"
    # one provocation per manifest trip, each expecting its own EVT line
    for t in man["trips"]:
        hit = [s for s in steps if s.get("expect_re") == t["evt_regex"]]
        assert len(hit) == 1, t["name"]
    ovp = next(s for s in steps if s.get("text") == "ovp trips on a real over-voltage")
    assert ovp["send"] == "duty 0.758" and 14 / (1 - 0.758) < 0.97 * 60
    inj = [s for s in steps if s.get("provoke")]
    assert [s["provoke"] for s in inj] == [{"trip": "ocp", "on": True},
                                           {"trip": "ocp", "on": False}]
    assert "TP301 (ISNS)" in inj[0]["text"]
    assert sum(s.get("send") == "clear" for s in steps) == 2
    assert sum("outputs_on" in (s.get("fields") or {}) for s in steps) == 2
    # the motor boards' 'duty 0 0.5 0' gate test is not generated for a boost
    assert not [s for s in _stage(proc, "blocks")["steps"]
                if str(s.get("send")).startswith("duty")]


def test_under_voltage_is_checked_while_switching(tmp_path):
    proc = procgen.generate(design.load(_ws(tmp_path)))
    lim = _stage(proc, "limits")["steps"]
    i = next(k for k, s in enumerate(lim) if s.get("expect_re") == r"^EVT .*uv")
    assert lim[i - 1]["set"]["v"] == 10.0
    assert lim[i - 2]["send"] == "duty 0.491"
    assert lim[i - 3]["set"]["v"] == 14.0          # D0 is set for the first-power input
    assert [s.get("send") for s in lim[i + 2:i + 4]] == ["disarm", "clear"]


def test_the_motor_board_keeps_its_idle_uv_check_and_its_duty_channels(tmp_path):
    ws = tmp_path / "mini-bldc"
    shutil.copytree(FIX, ws)
    proc = procgen.generate(design.load(ws))
    assert proc["design"]["boost"] is None
    lim = _stage(proc, "limits")["steps"]
    i = next(k for k, s in enumerate(lim) if s.get("expect_re") == r"^EVT .*uv")
    assert lim[i - 1]["type"] == "supply" and not str(lim[i - 2].get("send")).startswith("duty")
    man = json.loads((ws / "firmware" / "fwe-manifest.json").read_text())
    # no duty args in this manifest: one duty per half-bridge the netlist has
    b, con = _sim(proc, man)
    assert b.nduty == len(proc["design"]["half_bridges"]) == 1
    # the manifest's duty args set the count
    next(c for c in man["commands"] if c["name"] == "duty")["args"] = "<a> <b> <c>"
    b, con = _sim(proc, man)
    assert b.nduty == 3
    con.send("arm")
    con.send("duty 0 0.5 0")
    assert b.duty == [0.0, 0.5, 0.0] and b.switching()


def test_sim_run_of_the_full_procedure_passes(tmp_path):
    ws = _ws(tmp_path)
    r = subprocess.run([sys.executable, str(NPIE / "procedure_gen.py"), "--workspace", str(ws)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    r = subprocess.run([sys.executable, str(NPIE / "npie_run.py"), "start", "--workspace",
                        str(ws), "--dry-run"], capture_output=True, text=True)
    out = json.loads(r.stdout)
    assert r.returncode == 0 and out["status"] == "passed", out
    rec = json.loads((Path(out["run"]) / "run.json").read_text())
    vals = {s["id"]: s.get("value") for s in rec["steps"]}
    evts = [v for v in vals.values() if isinstance(v, str) and v.startswith("EVT {\"trip\"")]
    assert evts == ['EVT {"trip":{"new":["ovp_hw"]}}', 'EVT {"trip":{"new":["ocp_hw"]}}',
                    'EVT {"trip":{"new":["vin_uv"]}}']


def test_sim_trips_latch_drop_the_outputs_and_hold_until_cleared(tmp_path):
    ws = _ws(tmp_path)
    man, proc = _man(ws), procgen.generate(design.load(ws))
    b, con = _sim(proc, man)
    assert b.nduty == 1
    ovp, ocp = (next(t for t in man["trips"] if t["name"] == n) for n in ("ovp", "ocp"))
    # below the OVP threshold nothing trips; above it the trip latches once
    con.send("duty 0.5")
    assert con.read_until("^OK ", 1) and b.switching() and b.vout() == 28.0
    assert con.read_until(ovp["evt_regex"], 1) is None
    con.send("duty 0.76")
    assert con.read_until(ovp["evt_regex"], 1) and not b.switching()
    con.send("duty 0.5")
    assert con.read_until("^ERR fault latched", 1)
    con.send("clear")
    assert con.read_until("^OK ", 1) and b.fault_latched is None
    # an injected trip holds `clear` off until the injection is gone
    con.send("duty 0.5")
    b.provoke("ocp", True)
    assert re.search(ocp["evt_regex"], con.read_until("^EVT ", 1))
    con.send("clear")
    assert con.read_until("^ERR active", 1)
    b.provoke("ocp", False)
    con.send("clear")
    assert con.read_until("^OK ", 1)
    # regulating, an open-loop duty is refused, as the firmware does
    con.send("arm")
    con.send("duty 0.5")
    assert con.read_until("^ERR state regulating", 1)


def test_sim_under_voltage_latches_only_while_switching(tmp_path):
    ws = _ws(tmp_path)
    b, con = _sim(procgen.generate(design.load(ws)), _man(ws))
    b.supply(10.0, 0.2, True)
    assert b.fault_latched is None and con.read_until(r"^EVT .*uv", 1) is None
    b.supply(14.0, 0.2, True)
    con.send("duty 0.491")
    b.supply(10.0, 0.2, True)
    assert b.fault_latched == "vin_uv" and con.read_until(r"^EVT .*uv", 1)


def test_a_trip_the_bench_cannot_reach_is_skipped_with_its_reason(tmp_path):
    ws = _ws(tmp_path)
    man = _man(ws)
    for t in man["trips"]:
        if t["name"] == "ovp":
            t["threshold"] = 80.0          # above the 60 V output rating
        else:
            t["sense_net"] = "NO_SUCH_NET"
    (ws / "firmware" / "fwe-manifest.json").write_text(json.dumps(man))
    proc = procgen.generate(design.load(ws))
    skipped = {s["stage"]: s["reason"] for s in proc["skipped"]}
    assert "reaches the 80 V trip" in skipped["power-stage:ovp"]
    assert "'NO_SUCH_NET' is not in the netlist" in skipped["power-stage:ocp"]
    steps = _stage(proc, "power-stage")["steps"]
    assert not [s for s in steps if s.get("send") == "clear" or s.get("provoke")]
    # the open-loop checks before the trips are still there
    assert any(s.get("quantity") == "deadtime" for s in steps)

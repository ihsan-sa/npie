"""npie procedure generation: limits derived from the design, stages in order,
firmware stages following the fwe manifest (reference/design.md section 2).

Fixture: tests/fixtures/npie/mini-bldc, a synthetic board (VIN 8-24 V, buck to
+5V, LDO to +3V3, one half-bridge with a shunt and an INA240, a reset switch)
small enough to reason about every step. The real PCB-0018 case runs only
when the boards repo is cloned (tests/_boards.py).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
NPIE = ROOT / "skill" / "scripts"
FIX = ROOT / "tests" / "fixtures" / "npie" / "mini-bldc"
if str(NPIE) not in sys.path:
    sys.path.insert(0, str(NPIE))

from npielib import design, procgen  # noqa: E402


def _ws(tmp_path, manifest=True) -> Path:
    ws = tmp_path / "mini-bldc"
    shutil.copytree(FIX, ws)
    if not manifest:
        shutil.rmtree(ws / "firmware")
    return ws


def _steps(proc, stage=None):
    return [s for st in proc["stages"] if stage in (None, st["id"]) for s in st["steps"]]


def _by_text(proc, text):
    hits = [s for s in _steps(proc) if s.get("text") == text]
    assert len(hits) == 1, (text, hits)
    return hits[0]


def test_design_analysis(tmp_path):
    proc = procgen.generate(design.load(_ws(tmp_path)))
    assert proc["schema"] == "npie-procedure/1"
    assert proc["design"]["input"] == "/VM_IN"
    # the switch node and the phase are not rails; GND is not a rail
    assert proc["design"]["rails"] == ["+5V", "+3V3"]
    hb = proc["design"]["half_bridges"]
    assert [(b["hs"], b["ls"], b["phase"]) for b in hb] == [("Q1", "Q2", "/PHASE_A")]
    assert [s["id"] for s in proc["stages"]] == [
        "visual", "unpowered", "power-up", "rails", "programming", "blocks", "limits"]
    assert set(proc["inputs"]) == {"netlist", "constraints", "bom", "requirements", "manifest"}


def test_ids_are_sequential_per_stage(tmp_path):
    proc = procgen.generate(design.load(_ws(tmp_path)))
    for st in proc["stages"]:
        assert [s["id"] for s in st["steps"]] == [
            f"{st['id']}.{i:02d}" for i in range(1, len(st["steps"]) + 1)]


def test_every_limit_says_where_it_came_from(tmp_path):
    proc = procgen.generate(design.load(_ws(tmp_path)))
    for s in _steps(proc):
        if s.get("expect"):
            assert s.get("derived_from"), s["id"]


def test_rail_limits(tmp_path):
    proc = procgen.generate(design.load(_ws(tmp_path)))
    v5 = _by_text(proc, "+5V voltage")["expect"]
    assert (v5["min"], v5["max"]) == (4.75, 5.25)
    v33 = _by_text(proc, "+3V3 voltage")["expect"]
    assert (v33["min"], v33["max"]) == (3.135, 3.465)
    # the buck output gets a ripple check, the LDO output does not
    assert _by_text(proc, "+5V ripple")["expect"]["max"] == 0.1
    assert not [s for s in _steps(proc) if s.get("text") == "+3V3 ripple"]


def test_short_floors_and_body_diodes(tmp_path):
    proc = procgen.generate(design.load(_ws(tmp_path)))
    assert _by_text(proc, "VM_IN to GND is not a short")["expect"]["min"] == 1000.0
    assert _by_text(proc, "+3V3 to GND is not a short")["expect"]["min"] == 100.0
    diodes = [s for s in _steps(proc, "unpowered") if s.get("quantity") == "diode"]
    assert [(d["points"]["plus"]["net"], d["points"]["minus"]["net"]) for d in diodes] == [
        ("/PHASE_A", "/VM_IN"), ("/LS_SRC_A", "/PHASE_A")]


def test_every_probed_measure_is_preceded_by_a_placement(tmp_path):
    proc = procgen.generate(design.load(_ws(tmp_path)))
    for st in proc["stages"]:
        for prev, s in zip(st["steps"], st["steps"][1:]):
            if s["type"] in ("measure", "scope") and s.get("points"):
                # consecutive readings on the same points share one placement
                if prev["type"] == s["type"] and prev.get("points") == s["points"]:
                    continue
                assert prev["type"] == "human", s["id"]
                assert s["points"]["plus"]["label"] in prev["text"], s["id"]


def test_power_up_is_current_limited_and_confirmed(tmp_path):
    proc = procgen.generate(design.load(_ws(tmp_path)))
    st = _steps(proc, "power-up")
    on = next(i for i, s in enumerate(st) if s["type"] == "supply" and s["set"]["output"])
    assert st[on - 1]["type"] == "human"          # a person says go before it energises
    assert st[on]["set"] == {"v": 10.0, "i_limit": 0.2, "output": True}   # VIN min 8 + 2
    cur = next(s for s in st if s["type"] == "measure")
    assert cur["expect"]["max"] == pytest.approx(0.18)


def test_bridge_switching_needs_a_person_first(tmp_path):
    proc = procgen.generate(design.load(_ws(tmp_path)))
    st = _steps(proc, "blocks")
    arm = next(i for i, s in enumerate(st) if s.get("send") == "arm")
    assert st[arm - 1]["type"] == "human" and "DISCONNECTED" in st[arm - 1]["text"]
    assert any(s.get("send") == "disarm" for s in st[arm:])


def test_sense_amp_offset_is_mid_ref(tmp_path):
    proc = procgen.generate(design.load(_ws(tmp_path)))
    e = _by_text(proc, "U3 sense-amp offset")["expect"]
    assert e["nominal"] == pytest.approx(1.65)


def test_limits_stay_inside_the_rated_input(tmp_path):
    proc = procgen.generate(design.load(_ws(tmp_path)))
    vs = [s["set"]["v"] for s in _steps(proc) if s["type"] == "supply"]
    assert max(vs) == 24.0                        # requirements max, never the 30 V OV trip
    assert any(k["stage"] == "limits:ov" for k in proc["skipped"])
    last = _steps(proc)[-1]
    assert last["type"] == "supply" and last["set"]["output"] is False


def test_no_manifest_skips_firmware_stages(tmp_path):
    proc = procgen.generate(design.load(_ws(tmp_path, manifest=False)))
    ids = [s["id"] for s in proc["stages"]]
    assert "programming" not in ids
    assert {k["stage"] for k in proc["skipped"]} >= {"programming", "blocks:firmware"}
    assert not [s for s in _steps(proc) if s["type"] in ("console", "flash")]
    assert "manifest" not in proc["inputs"]


def test_missing_netlist_is_exit_2(tmp_path):
    ws = _ws(tmp_path)
    shutil.rmtree(ws / "kicad")
    r = subprocess.run([sys.executable, str(NPIE / "procedure_gen.py"),
                        "--workspace", str(ws)], capture_output=True, text=True)
    assert r.returncode == 2
    assert "netlist" in json.loads(r.stdout)["error"]


def test_cli_writes_json_and_ascii_markdown(tmp_path):
    ws = _ws(tmp_path)
    r = subprocess.run([sys.executable, str(NPIE / "procedure_gen.py"),
                        "--workspace", str(ws)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    proc = json.loads((ws / "bringup" / "procedure.json").read_text())
    assert out["steps"] == len(_steps(proc))
    md = (ws / "bringup" / "procedure.md").read_bytes()
    md.decode("ascii")
    assert b"rails.02" in md
    # the bench example binds exactly the roles the procedure uses, and parses
    bench = yaml.safe_load((ws / "bringup" / "bench.example.yaml").read_text())
    assert out["bench_example"].endswith("bench.example.yaml")
    assert sorted(bench["roles"]) == proc["roles"]
    assert bench["roles"]["psu"]["driver"] == "scpi-psu"


def test_real_bldc_board():
    from _boards import board_path, need_board
    need_board("PCB-0018-A_bldc-motor-driver")
    proc = procgen.generate(design.load(board_path("PCB-0018-A_bldc-motor-driver")))
    assert proc["design"]["input"] == "/power_in/VM_IN"
    assert {"+5V", "+3V3", "+12V", "VM"} <= set(proc["design"]["rails"])
    assert len(proc["design"]["half_bridges"]) == 3


def test_overrides_replace_a_generated_limit(tmp_path):
    ws = _ws(tmp_path)
    base = procgen.generate(design.load(ws))
    (ws / "bringup").mkdir()
    (ws / "bringup" / "overrides.yaml").write_text("rails.02: {min: 4.9, max: 5.1}\n")
    proc = procgen.generate(design.load(ws))
    got = {s["id"]: s for s in _steps(proc)}
    was = {s["id"]: s for s in _steps(base)}
    assert got["rails.02"]["expect"]["min"] == 4.9 and got["rails.02"]["expect"]["max"] == 5.1
    assert "overrides.yaml" in got["rails.02"]["derived_from"]
    assert proc["overrides"] == [{"step": "rails.02", "generated": was["rails.02"]["expect"],
                                  "override": {"min": 4.9, "max": 5.1}}]
    # every other step is untouched
    assert got["rails.04"] == was["rails.04"]
    assert "overrides" in proc["inputs"] and "overrides" not in base["inputs"]


@pytest.mark.parametrize("text", ["nosuch.01: {max: 1}\n", "visual.01: {max: 1}\n", "- 1\n"])
def test_bad_overrides_are_exit_2(tmp_path, text):
    ws = _ws(tmp_path)
    (ws / "bringup").mkdir()
    (ws / "bringup" / "overrides.yaml").write_text(text)
    r = subprocess.run([sys.executable, str(NPIE / "procedure_gen.py"), "--workspace", str(ws)],
                       capture_output=True, text=True)
    assert r.returncode == 2, r.stdout
    assert "overrides.yaml" in json.loads(r.stdout)["error"]


def test_manifest_status_and_pwm_hz_add_checks(tmp_path):
    ws = _ws(tmp_path)
    proc = procgen.generate(design.load(ws))
    st = _by_text(proc, "firmware reads the input voltage")
    assert st["send"] == "status" and st["fields"]["vbus_v"]["min"] == 9.5
    hz = _by_text(proc, "PHASE_A PWM frequency")
    assert (hz["quantity"], hz["expect"]["min"], hz["expect"]["max"]) == ("freq", 19600, 20400)
    # without the fields fwe added, neither check is generated
    man = ws / "firmware" / "fwe-manifest.json"
    m = json.loads(man.read_text())
    del m["safety"]["pwm_hz"]
    for c in m["commands"]:
        c.pop("reply_fields", None)
    man.write_text(json.dumps(m))
    texts = {s.get("text") for s in _steps(procgen.generate(design.load(ws)))}
    assert "firmware reads the input voltage" not in texts
    assert "PHASE_A PWM frequency" not in texts

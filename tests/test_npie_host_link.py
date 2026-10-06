"""The EVN host-link stage (procgen host_link): a board that mates the
LFE5UM5G-85F-EVN's JP8 gets the rework, EEPROM and 0xF8 checks, then the J1
pin check, before its own first power. Each case builds its own workspace."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from _boards import board_path, need_board

ROOT = Path(__file__).resolve().parents[1]
NPIE = ROOT / "skill" / "scripts"
FIX = ROOT / "tests" / "fixtures" / "npie" / "mini-bldc"
EVN_BLOCK = {"topology": "board-to-board-header", "block": "B1",
             "name": "J9 2x20 2.54 male to EVN JP8", "operating_point": {"vio_v": 3.3}}


def _gen(ws: Path) -> dict:
    r = subprocess.run([sys.executable, str(NPIE / "procedure_gen.py"), "--workspace", str(ws)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads((ws / "bringup" / "procedure.json").read_text())


def _fixture(tmp_path, blocks) -> Path:
    ws = tmp_path / "mini-bldc"
    shutil.copytree(FIX, ws)
    cp = ws / "kicad" / "constraints.json"
    c = json.loads(cp.read_text())
    c["blocks"] = blocks
    cp.write_text(json.dumps(c))
    return ws


def _dry_run(ws: Path) -> dict:
    e = {k: v for k, v in os.environ.items() if k != "NPIE_BENCH_HOST"}
    r = subprocess.run([sys.executable, str(NPIE / "npie_run.py"), "start", "--workspace",
                        str(ws), "--dry-run"], capture_output=True, text=True, env=e)
    return json.loads(r.stdout)


def _stage(proc, sid):
    return next((st for st in proc["stages"] if st["id"] == sid), None)


def test_evn_header_adds_host_link_before_power_up(tmp_path):
    proc = _gen(_fixture(tmp_path, [EVN_BLOCK]))
    order = [st["id"] for st in proc["stages"]]
    assert order.index("unpowered") < order.index("host-link") < order.index("power-up")
    steps = _stage(proc, "host-link")["steps"]
    text = " ".join(s.get("text", "") for s in steps)
    # brief: "R22/R23 stay fitted because they carry I2C. Only R34/R35 get fitted as 0R"
    assert "R34 and R35 fitted as 0R" in text and "R22 and R23 still fitted" in text
    assert "come off" not in text and "remove R22" not in text
    assert "EEPROM to a file" in text and "fixFT2232_ecp5evn" in text
    assert "P2 is the FPGA's rx, P3 its tx" in text
    # the 0xF8 id read comes after the EEPROM switch, and nothing before it is automatic
    kinds = [s["type"] for s in steps]
    assert kinds.index("pwm") > 2 and set(kinds[:kinds.index("pwm")]) == {"human"}
    # J9 pins 1 and 17 read vio_v +/-5 %, pins 6 and 9 read GND, each typed by a person
    reads = {s["text"].split(" pin ")[1].split(",")[0]: s["expect"] for s in steps
             if s.get("value")}
    assert set(reads) == {"1", "17", "6", "9"}
    assert reads["1"] == reads["17"] == {"nominal": 3.3, "min": 3.135, "max": 3.465, "unit": "V"}
    assert reads["6"]["nominal"] == reads["9"]["nominal"] == 0.0
    assert all("J9 pin" in s["text"] for s in steps if s.get("value"))
    assert "pwm" in proc["roles"]


def test_no_evn_header_no_host_link(tmp_path):
    other = {**EVN_BLOCK, "name": "J9 2x20 2.54 male to a Pi header"}
    proc = _gen(_fixture(tmp_path, [other]))
    assert _stage(proc, "host-link") is None and "pwm" not in proc["roles"]


def test_host_link_passes_on_the_sim_bench(tmp_path):
    ws = _fixture(tmp_path, [EVN_BLOCK])
    _gen(ws)
    out = _dry_run(ws)
    assert out["status"] == "passed", out
    rec = json.loads((Path(out["run"]) / "run.json").read_text())
    pwm = [r for r in rec["steps"] if r["id"].startswith("host-link") and r["type"] == "pwm"]
    assert pwm and pwm[0]["verdict"] == "pass"


def test_pcb_0025_procedure_has_the_evn_steps(tmp_path):
    need_board("PCB-0025-A_pwm-fpga-8ch")
    src, ws = board_path("PCB-0025-A_pwm-fpga-8ch"), tmp_path / "PCB-0025-A_pwm-fpga-8ch"
    ws.mkdir()
    shutil.copytree(src / "kicad", ws / "kicad")
    shutil.copytree(src / "fab", ws / "fab")
    shutil.copy(src / "requirements.md", ws)
    proc = _gen(ws)
    reads = [s for s in _stage(proc, "host-link")["steps"] if s.get("value")]
    assert [s["text"].split(" on ")[2].split(",")[0] for s in reads] == \
        ["J1 pin 1", "J1 pin 17", "J1 pin 6", "J1 pin 9"]
    assert _dry_run(ws)["status"] == "passed"

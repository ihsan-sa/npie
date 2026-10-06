"""The RF bench's drive sources: /fwe's FPGA PWM generator and the SDG6032X.

The protocol cases talk to the simulated instruments directly; the run cases
add a pwm/awg stage to the mini-bldc procedure and run npie_run.py on the
simulated bench. Each builds its own state. Nothing here opens an instrument.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from test_npie_run import NPIE, _record, _run, _ws

sys.path.insert(0, str(NPIE))
from npielib.instruments import (BenchError, Pwm8, Pwm8Gateware, ScpiSdg,  # noqa: E402
                                 SdgModel)


def _x(gw: Pwm8Gateware, *b: int) -> bytes:
    gw.write(bytes(b))
    return gw.read(8)


# ---------------------------------------------------- the gateware model


def test_gateware_model_answers_as_pwm8_ctrl():
    gw = Pwm8Gateware(n=56)
    assert _x(gw, ord("R"), 0x00) == bytes([0xF8])
    assert _x(gw, ord("R"), 0x02) == bytes([56])
    assert _x(gw, ord("R"), 0x05) == bytes([128])
    assert _x(gw, ord("X")) == b"?"
    assert _x(gw, ord("R"), 0x06) == b"E"               # unknown address
    assert _x(gw, ord("W"), 0x02, 9) == b"E"            # read-only N
    # phase < N, duty <= N: the edges are kept and refused as the RTL does
    assert _x(gw, ord("W"), 0x10, 55) == b"K"
    assert _x(gw, ord("W"), 0x10, 56) == b"E"
    assert _x(gw, ord("W"), 0x1F, 56) == b"K"
    assert _x(gw, ord("W"), 0x1F, 57) == b"E"
    assert _x(gw, ord("R"), 0x10) == bytes([55])
    # the outputs change only on a commit
    assert gw.duty_on[7] == 0
    assert _x(gw, ord("W"), 0x01, 0x03) == b"K"
    assert gw.enable and gw.duty_on[7] == 56 and gw.phase_on[0] == 55


# ---------------------------------------------------- the PWM driver


def test_pwm_set_quantises_commits_and_turns_off():
    gw = Pwm8Gateware(n=56)
    p = Pwm8(gw, 13.5602678e6)
    got = p.set(f_hz=13.56e6, duty=0.5, phase_deg={"1": 180, "2": 30}, enable=True)
    assert got["enable"] and gw.enable
    assert got["duty"] == [0.5] * 8 and gw.duty_on == [28] * 8
    assert gw.phase_on[:3] == [0, 28, 5]                 # 30 deg is 4.67 steps -> 5
    assert got["phase_deg"][2] == pytest.approx(5 * 360 / 56)
    # a later write without enable keeps the generator running
    assert p.set(duty={7: 0.25})["enable"] and gw.duty_on[7] == 14
    p.off()
    assert not gw.enable and not p.state()["enable"]


def test_pwm_refuses_what_the_gateware_cannot_do():
    p = Pwm8(Pwm8Gateware(n=56), 13.56e6)
    with pytest.raises(BenchError, match="fixes f"):
        p.set(f_hz=6.78e6)
    with pytest.raises(BenchError, match="no channel"):
        p.set(duty={8: 0.5})
    with pytest.raises(BenchError, match="not within"):
        p.set(duty=1.2)


def test_pwm_needs_the_bitstream_and_a_reply():
    class Silent:
        def write(self, data):
            pass

        def read(self, n=1):
            return b""

    class Wrong(Silent):
        def read(self, n=1):
            return b"\x00"

    with pytest.raises(BenchError, match="no reply"):
        Pwm8(Silent(), 13.56e6)
    with pytest.raises(BenchError, match="id register"):
        Pwm8(Wrong(), 13.56e6)


# ---------------------------------------------------- the SDG6032X driver


def test_sdg_sets_and_reads_back():
    m = SdgModel()
    g = ScpiSdg({}, inst=m)
    got = g.set(2, wave="square", f_hz=13.56e6, vpp=3.3, offset=1.65, duty=0.5,
                phase_deg=90, load=50, output=True)
    assert got == {"channel": 2, "wave": "SQUARE", "f_hz": 13.56e6, "vpp": 3.3,
                   "offset": 1.65, "duty": 0.5, "phase_deg": 90.0, "output": True,
                   "load": "50"}
    assert m.mode == "PHASE-LOCKED" and m.ch[1]["out"] == "OFF"
    g.off()
    assert m.ch[2]["out"] == "OFF"


def test_sdg_refuses_a_setting_that_does_not_stick_and_a_stranger():
    class Clamps(SdgModel):         # an instrument that limits the square wave
        def write(self, cmd):
            super().write(cmd)
            for c in self.ch.values():
                c["FRQ"] = min(c["FRQ"], 120e6)

    with pytest.raises(BenchError, match="FRQ reads back"):
        ScpiSdg({}, inst=Clamps()).set(1, f_hz=200e6)
    assert ScpiSdg({}, inst=Clamps()).set(1, f_hz=100e6)["f_hz"] == 100e6

    class Other(SdgModel):
        def query(self, cmd):
            return "RIGOL TECHNOLOGIES,DG1022Z,X,1" if cmd == "*IDN?" else super().query(cmd)

    with pytest.raises(BenchError, match="not a Siglent"):
        ScpiSdg({}, inst=Other())


# ---------------------------------------------------- a run that drives them


def _rf_stage(ws: Path, pwm_set: dict) -> None:
    """Add a stage that powers up, drives both sources and scopes the PWM."""
    pj = ws / "bringup" / "procedure.json"
    proc = json.loads(pj.read_text())
    pt = {"ref": "J9", "pin": "1", "net": "/PWM0", "label": "J9 (PWM0)"}
    gnd = {"ref": "J9", "pin": "2", "net": "GND", "label": "J9 (GND)"}
    proc["stages"].append({"id": "rf-drive", "title": "Drive sources", "steps": [
        {"id": "rf-drive.01", "type": "supply", "role": "psu",
         "set": {"v": 12.0, "i_limit": 0.5, "output": True}},
        {"id": "rf-drive.02", "type": "pwm", "role": "pwm", "set": pwm_set},
        {"id": "rf-drive.03", "type": "scope", "role": "scope", "channel": 1,
         "points": {"plus": pt, "minus": gnd}, "quantity": "freq",
         "expect": {"nominal": 13.56e6, "min": 13.5e6, "max": 13.6e6, "unit": "Hz"},
         "derived_from": "the generator's f_rf"},
        {"id": "rf-drive.04", "type": "awg", "role": "awg", "channel": 1,
         "set": {"f_hz": 13.56e6, "vpp": 3.3, "offset": 1.65, "duty": 0.5,
                 "load": "hiz", "output": True}},
    ]})
    pj.write_text(json.dumps(proc))


def test_run_drives_the_pwm_and_awg_and_scopes_f_rf(tmp_path):
    ws = _ws(tmp_path)
    _rf_stage(ws, {"f_hz": 13.56e6, "duty": 0.5, "phase_deg": {"4": 180}, "enable": True})
    rc, out = _run("start", "--workspace", str(ws), "--dry-run")
    assert rc == 0 and out["status"] == "passed", out
    by = {r["id"]: r for r in _record(out)["steps"]}
    assert by["rf-drive.02"]["applied"]["phase_deg"][4] == 180.0
    assert by["rf-drive.03"]["value"] == pytest.approx(13.56e6, rel=1e-3)
    assert by["rf-drive.04"]["applied"]["output"] is True


def test_run_with_a_frequency_the_bitstream_lacks_aborts_safe(tmp_path):
    ws = _ws(tmp_path)
    _rf_stage(ws, {"f_hz": 6.78e6, "duty": 0.5, "enable": True})
    rc, out = _run("start", "--workspace", str(ws), "--dry-run")
    assert out["status"] == "aborted", out
    rec = _record(out)
    assert rec["error"].startswith("rf-drive.02:") and "fixes f" in rec["error"]
    assert rec["safe_state"].startswith("supply outputs off")
    assert rec["steps"][-1]["id"] == "rf-drive.01"

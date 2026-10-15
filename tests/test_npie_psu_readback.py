"""The record says the supply is off only when it reads back off (GOALS.md,
The supply is off when the record says so).

ScpiPsu only wrote: VOLT, CURR, OUTP and, at the end of every run, OUTP
OFF. A supply that clamped a setpoint to a front-panel limit, sat in local
mode or had another channel selected took none of it without a word, and
the run went on testing the board against a setpoint it wasn't at. Bench
safe_off counted any off() that didn't raise as done, so run.json said
"supply outputs off" of a supply whose output was still on. The pwm and awg
drivers already read back every write; now the supply does too.

Nothing real is opened: the VISA resource is test_npie_live_drivers' fake,
with a PsuModel behind the supply's settings.
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

import procedure_gen  # noqa: E402
from npielib import instruments, runner  # noqa: E402
from test_npie_live_drivers import FIX, PsuModel, _Visa  # noqa: E402

CFG = {"bench": "t", "roles": {"psu": {"driver": "scpi-psu", "resource": "TCPIP::psu",
                                       "channel": 2}}}


@pytest.fixture
def bench_host(monkeypatch):
    monkeypatch.setenv("NPIE_BENCH_HOST", "1")


def _psu(model: PsuModel):
    return instruments.ScpiPsu(CFG["roles"]["psu"], inst=_Visa(model))


def test_a_setpoint_the_supply_holds_is_read_back():
    m = PsuModel()
    _psu(m).set(v=12, i_limit=0.25, output=True)
    assert m.set[2] == {"VOLT": 12.0, "CURR": 0.25, "OUTP": True}


def test_a_clamped_voltage_is_refused_before_the_output_goes_on():
    m = PsuModel(clamp_v=20)
    with pytest.raises(instruments.BenchError, match=r"channel 2 VOLT\? reads back 20, set 24"):
        _psu(m).set(v=24, i_limit=0.25, output=True)
    assert m.set[2]["OUTP"] is False


def test_an_output_that_wont_turn_off_raises():
    m = PsuModel(stuck_on=True)
    p = _psu(m)
    p.set(v=5, i_limit=0.1, output=True)
    with pytest.raises(instruments.BenchError, match="still reads on after OUTP OFF"):
        p.off()


def test_a_supply_that_answers_on_and_off_in_words():
    class Words(PsuModel):
        def query(self, cmd):
            got = super().query(cmd)
            return {"1": "ON", "0": "OFF"}[got] if cmd == "OUTP?" else got
    m = Words()
    p = _psu(m)
    p.set(v=5, i_limit=0.1, output=True)
    p.off()
    assert m.set[2]["OUTP"] is False


def test_bench_names_the_supply_in_a_refused_setpoint(monkeypatch, bench_host):
    monkeypatch.setattr(instruments, "_visa", lambda resource: _Visa(PsuModel(clamp_v=20)))
    with pytest.raises(instruments.BenchError, match=r"psu \(scpi-psu\): set\(.*VOLT\? reads back"):
        instruments.Bench(CFG).role("psu").set(v=24, i_limit=0.25, output=True)


def _live_run(tmp_path: Path, monkeypatch, model: PsuModel) -> runner.Run:
    ws = tmp_path / "mini-bldc"
    shutil.copytree(FIX, ws)
    assert procedure_gen.main(["--workspace", str(ws)]) == 0
    monkeypatch.setattr(instruments, "_visa", lambda resource: _Visa(model))
    run = runner.create(ws, bench="t", bench_cfg=CFG, label=None, faults=None, hold=False)
    run.open_bench()
    run.bench.role("psu").set(v=5, i_limit=0.1, output=True)
    return run


@pytest.mark.parametrize("status", ["passed", "failed", "aborted"])
def test_an_ended_run_says_off_when_the_supply_reads_off(tmp_path, monkeypatch, bench_host,
                                                          status):
    m = PsuModel()
    run = _live_run(tmp_path, monkeypatch, m)
    run.finish(status)
    assert m.set[2]["OUTP"] is False
    assert run.rec["safe_state"].startswith("supply outputs off"), run.rec["safe_state"]


@pytest.mark.parametrize("status", ["passed", "failed", "aborted"])
def test_an_ended_run_never_says_off_of_a_supply_still_on(tmp_path, monkeypatch, bench_host,
                                                           status):
    m = PsuModel(stuck_on=True)
    run = _live_run(tmp_path, monkeypatch, m)
    run.finish(status)
    assert m.set[2]["OUTP"] is True
    s = run.rec["safe_state"]
    assert s.startswith("supply not confirmed off") and "still reads on" in s, s
    assert "supply outputs off" not in s

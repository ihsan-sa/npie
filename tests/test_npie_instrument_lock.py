"""One process drives an instrument at a time (GOALS.md, One process drives
each instrument).

run.lock (#83) stops two processes driving one run, but two runs (two
`start`s, or two workspaces whose bench.yaml name the same supply) still
opened one instrument at once: the second set its own voltage on the first
run's supply mid-step, and turned it off when it ended. Bench now holds a
host-wide lock on each live instrument's address while it has it open, and
the OS lets go when the process dies.

Nothing real is opened: VISA is test_npie_live_drivers' fake, and the lock
dir is the test's own (NPIE_LOCK_DIR), so no other run on this host is met.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NPIE = ROOT / "skill" / "scripts"
if str(NPIE) not in sys.path:
    sys.path.insert(0, str(NPIE))

import procedure_gen  # noqa: E402
from npielib import instruments, runner  # noqa: E402
from test_npie_live_drivers import FIX, PsuModel, _Visa  # noqa: E402

PSU = {"driver": "scpi-psu", "resource": "TCPIP::psu", "channel": 1}
CFG = {"bench": "t", "roles": {"psu": PSU}}
PROBE = {"bench": "t", "roles": {"probe": {"driver": "swd", "tool": "probe-rs"}}}


@pytest.fixture
def models(tmp_path, monkeypatch):
    """A PsuModel per VISA resource, NPIE_BENCH_HOST=1 and a lock dir of the test's own."""
    monkeypatch.setenv("NPIE_BENCH_HOST", "1")
    monkeypatch.setenv("NPIE_LOCK_DIR", str(tmp_path / "locks"))
    m: dict = {}
    monkeypatch.setattr(instruments, "_visa",
                        lambda resource: _Visa(m.setdefault(resource, PsuModel())))
    return m


def test_a_second_bench_is_refused_an_instrument_another_has_open(models):
    a = instruments.Bench(CFG)
    a.role("psu")
    with pytest.raises(instruments.BenchError,
                       match=rf"psu \(scpi-psu\) at TCPIP::psu is open in another npie "
                             rf"process \(pid {os.getpid()}\)"):
        instruments.Bench(CFG).role("psu")
    a.close()
    instruments.Bench(CFG).role("psu")      # let go: the next bench takes it


def test_instruments_at_other_addresses_are_not_held(models):
    instruments.Bench(CFG).role("psu")
    other = {"bench": "u", "roles": {"psu": {**PSU, "resource": "TCPIP::psu2"}}}
    instruments.Bench(other).role("psu")


def test_two_roles_at_one_address_share_the_bench_s_hold(models):
    one_box = {"bench": "t", "roles": {"psu": PSU,
                                       "dmm": {"driver": "scpi-dmm", "resource": "TCPIP::psu"}}}
    b = instruments.Bench(one_box)
    b.role("psu")
    b.role("dmm")


def test_a_driver_that_fails_to_open_lets_go(models, monkeypatch):
    def refuse(resource):
        raise instruments.BenchError(f"cannot open {resource}: no route")
    monkeypatch.setattr(instruments, "_visa", refuse)
    with pytest.raises(instruments.BenchError, match="no route"):
        instruments.Bench(CFG).role("psu")
    monkeypatch.setattr(instruments, "_visa", lambda resource: _Visa(PsuModel()))
    instruments.Bench(CFG).role("psu")


def test_a_sim_bench_holds_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("NPIE_LOCK_DIR", str(tmp_path / "locks"))
    ws = tmp_path / "mini-bldc"
    shutil.copytree(FIX, ws)
    assert procedure_gen.main(["--workspace", str(ws)]) == 0
    run = runner.create(ws, bench="sim", bench_cfg=None, label=None, faults=None, hold=False)
    runner.advance(run)
    assert run.rec["status"] == "passed"
    assert not (tmp_path / "locks").exists()


def test_another_process_holds_until_it_dies(models, tmp_path):
    """A real second process: its hold refuses this one, names its pid, and
    goes when it is SIGKILLed, with no chance to let go itself."""
    child = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(f"""
            import sys, time
            sys.path.insert(0, {str(NPIE)!r})
            from npielib import instruments
            bench = instruments.Bench({PROBE!r})
            bench.role("probe")
            print("held", flush=True)
            time.sleep(60)
        """)], stdout=subprocess.PIPE, text=True, env=os.environ.copy())
    try:
        assert child.stdout.readline().strip() == "held"
        with pytest.raises(instruments.BenchError,
                           match=rf"probe \(swd\) at swd:probe-rs is open in another npie "
                                 rf"process \(pid {child.pid}\)"):
            instruments.Bench(PROBE).role("probe")
    finally:
        child.send_signal(signal.SIGKILL)
        child.wait(10)
    instruments.Bench(PROBE).role("probe")


def test_a_run_refused_the_supply_never_touches_it(models, tmp_path):
    """Run B, at its first supply step, on a supply run A has on: B aborts
    naming the holder, writes nothing to A's supply (not even its own
    end-of-run OUTP OFF), and its record doesn't say the supply is off."""
    a = instruments.Bench(CFG)
    a.role("psu").set(v=12, i_limit=0.5, output=True)
    ws = tmp_path / "mini-bldc"
    shutil.copytree(FIX, ws)
    assert procedure_gen.main(["--workspace", str(ws)]) == 0
    run = runner.create(ws, bench="t", bench_cfg=CFG, label=None, faults=None, hold=False)
    run.rec["cursor"] = next(s["id"] for s in run.steps if s["type"] == "supply")
    runner.advance(run)
    assert run.rec["status"] == "aborted"
    assert "open in another npie process" in run.rec["error"], run.rec["error"]
    assert models["TCPIP::psu"].set[1] == {"VOLT": 12.0, "CURR": 0.5, "OUTP": True}
    s = run.rec["safe_state"]
    assert s.startswith("supply not confirmed off") and "another npie process" in s, s
    run.release()
    a.close()


def test_a_run_lets_go_of_its_instruments_when_released(models, tmp_path):
    ws = tmp_path / "mini-bldc"
    shutil.copytree(FIX, ws)
    assert procedure_gen.main(["--workspace", str(ws)]) == 0
    run = runner.create(ws, bench="t", bench_cfg=CFG, label=None, faults=None, hold=False)
    run.open_bench()
    run.bench.role("psu")
    with pytest.raises(instruments.BenchError, match="another npie process"):
        instruments.Bench(CFG).role("psu")
    run.release()
    instruments.Bench(CFG).role("psu")

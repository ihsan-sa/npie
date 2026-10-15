"""A run resumes only on the supply it left (GOALS.md, A resumed run finds the
supply as it left it).

A run waiting on a person has no process, so it holds no instrument (#83,
#85): another run, or someone at the front panel, can set or turn off its
supply in the gap, which on a real bench can be days. resume then carried on
testing the board at a setpoint the procedure never asked for. Now resume
reads the supply back first and, if it isn't as the run's last supply step
left it, aborts the run, supply off.

Nothing real is opened: VISA is test_npie_live_drivers' fake, one PsuModel
per resource, and the lock dir is the test's own.
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
from test_npie_instrument_lock import CFG, models  # noqa: E402,F401
from test_npie_live_drivers import FIX  # noqa: E402


def _waiting(tmp_path: Path):
    """A live run stopped at the first human step after the supply went on,
    as advance leaves it: the supply step recorded, the bench let go."""
    ws = tmp_path / "mini-bldc"
    shutil.copytree(FIX, ws)
    assert procedure_gen.main(["--workspace", str(ws)]) == 0
    run = runner.create(ws, bench="t", bench_cfg=CFG, label=None, faults=None, hold=False)
    for i, h in enumerate(run.steps):
        prev = [p for p in run.steps[:i] if p["type"] == "supply"]
        if h["type"] == "human" and prev and prev[-1]["set"]["output"]:
            break
    else:
        pytest.fail("mini-bldc has no human step after the supply goes on")
    left = prev[-1]
    run.open_bench()
    runner.execute(run, left)
    run.rec.update(status="awaiting_human", cursor=h["id"])
    run.save()
    run.release()
    return run.dir, left, h


def _resume(rdir: Path, h: dict) -> runner.Run:
    run = runner.Run(rdir)
    value = runner._sim_value(h)
    runner.confirm(run, h["id"], "ann", None if value is None else str(value), None)
    try:
        runner.advance(run)
    finally:
        run.release()
    return run


def _other_bench(do) -> None:
    b = instruments.Bench(CFG)
    do(b.role("psu"))
    b.close()


def test_a_supply_left_as_it_was_lets_the_run_go_on(models, tmp_path):
    rdir, left, h = _waiting(tmp_path)
    run = _resume(rdir, h)
    assert run.rec["cursor"] != h["id"]
    assert "left it" not in run.rec.get("error", ""), run.rec["error"]


@pytest.mark.parametrize("change,reads", [
    (lambda psu, s: psu.set(v=float(s["v"]) + 1, i_limit=s["i_limit"], output=True), "output on"),
    (lambda psu, s: psu.set(v=s["v"], i_limit=float(s["i_limit"]) / 2, output=True), "output on"),
    (lambda psu, s: psu.off(), "output off"),
], ids=["voltage", "current-limit", "turned-off"])
def test_a_supply_changed_while_the_run_waited_aborts_it(models, tmp_path, change, reads):
    rdir, left, h = _waiting(tmp_path)
    _other_bench(lambda psu: change(psu, left["set"]))
    run = _resume(rdir, h)
    assert run.rec["status"] == "aborted"
    err = run.rec["error"]
    assert err.startswith(f"{h['id']}: the supply reads ") and reads in err, err
    assert f"as {left['id']} left it" in err and "waited on a person" in err, err
    assert run.rec["safe_state"].startswith("supply outputs off"), run.rec["safe_state"]
    assert models["TCPIP::psu"].set[1]["OUTP"] is False
    assert next(r for r in run.rec["steps"] if r["id"] == h["id"])["confirmed_by"] == "ann"
    with pytest.raises(runner.RunError, match="run is aborted"):
        runner.advance(runner.Run(rdir))


def test_scpi_psu_state_reads_its_own_channel(models):
    psu = instruments.ScpiPsu({"resource": "TCPIP::psu", "channel": 2})
    psu.set(v=5, i_limit=0.25, output=True)
    psu.i.write("INST:NSEL 1")
    assert psu.state() == {"v": 5.0, "i_limit": 0.25, "output": True}

"""A run resumes only on the pwm and awg outputs it left (GOALS.md, A resumed
run finds its drive sources as it left them).

#86 made resume read the supply back, but a run waiting on a person holds no
instrument (#85), so another run, or someone at the generator's front panel,
could change the pwm generator's duty or phase, or an awg channel's
amplitude, or turn either off, and resume carried on driving the board at a
setting the procedure never asked for. Now resume reads each one the run
drove back against its last step's `applied` and, on a mismatch, aborts the
run, supply and drive outputs off.

Nothing real is opened: VISA is test_npie_live_drivers' fake, one per
resource, the pwm link is a Pwm8Gateware per port, and the lock dir is the
test's own.
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

import procedure_gen  # noqa: E402
from npielib import instruments, runner  # noqa: E402
from test_npie_live_drivers import FIX, _Visa  # noqa: E402
from test_npie_rf_instruments import _rf_stage, hand_written  # noqa: E402

CFG = {"bench": "t", "roles": {
    "psu": {"driver": "scpi-psu", "resource": "TCPIP::psu", "channel": 1},
    "pwm": {"driver": "fwe-pwm8", "port": "/dev/ttyPWM", "f_rf_hz": 13.56e6},
    "awg": {"driver": "scpi-sdg", "resource": "TCPIP::awg"},
}}
HUMAN = {"id": "rf-drive.05", "type": "human", "role": "operator",
         "text": "Check the RF drive on the scope."}


@pytest.fixture
def live(tmp_path, monkeypatch):
    """A fake VISA resource and pwm gateware per address, kept between benches."""
    monkeypatch.setenv("NPIE_BENCH_HOST", "1")
    monkeypatch.setenv("NPIE_LOCK_DIR", str(tmp_path / "locks"))
    visas: dict = {}
    gws: dict = {}
    monkeypatch.setattr(instruments, "_visa", lambda r: visas.setdefault(r, _Visa()))
    monkeypatch.setattr(instruments, "_SerialLink",
                        lambda cfg: gws.setdefault(cfg["port"], instruments.Pwm8Gateware(n=56)))
    hand_written(monkeypatch)
    return visas, gws


def _ws(tmp_path: Path) -> Path:
    """mini-bldc with an RF stage that drives both sources, then asks a person."""
    ws = tmp_path / "mini-bldc"
    shutil.copytree(FIX, ws)
    assert procedure_gen.main(["--workspace", str(ws)]) == 0
    _rf_stage(ws, {"f_hz": 13.56e6, "duty": 0.5, "phase_deg": {"4": 180}, "enable": True})
    pj = ws / "bringup" / "procedure.json"
    proc = json.loads(pj.read_text())
    proc["stages"][-1]["steps"].append(HUMAN)
    pj.write_text(json.dumps(proc))
    return ws


def _waiting(tmp_path: Path) -> Path:
    """A live run stopped at the person's step after the RF drive, as advance
    leaves it: the drive steps recorded, the bench let go."""
    run = runner.create(_ws(tmp_path), bench="t", bench_cfg=CFG, label=None,
                        faults=None, hold=False)
    run.open_bench()
    for s in run.steps[run.index("rf-drive.01"):run.index(HUMAN["id"])]:
        if s["type"] != "scope":     # the fake scope reads 1.0, not f_rf
            assert runner.execute(run, s)["verdict"] == "pass"
    run.rec.update(status="awaiting_human", cursor=HUMAN["id"])
    run.save()
    run.release()
    return run.dir


def _resume(rdir: Path) -> runner.Run:
    run = runner.Run(rdir)
    runner.confirm(run, HUMAN["id"], "ann", None, None)
    try:
        runner.advance(run)
    finally:
        run.release()
    return run


def _other_bench(role: str, do) -> None:
    b = instruments.Bench(CFG)
    do(b.role(role))
    b.close()


def test_drive_sources_left_as_they_were_let_the_run_go_on(live, tmp_path):
    """And, ended by a process that never drove them, it turns them off."""
    visas, gws = live
    run = _resume(_waiting(tmp_path))
    assert run.rec["status"] == "passed", run.rec.get("error")
    assert gws["/dev/ttyPWM"].enable is False
    assert visas["TCPIP::awg"].sdg.ch[1]["out"] == "OFF"


@pytest.mark.parametrize("role,change,reads,step", [
    ("pwm", lambda p: p.set(duty={"0": 0.25}), "the pwm generator reads duty", "rf-drive.02"),
    ("pwm", lambda p: p.set(phase_deg={"4": 90}), "the pwm generator reads phase_deg", "rf-drive.02"),
    ("pwm", lambda p: p.off(), "the pwm generator reads enable False", "rf-drive.02"),
    ("awg", lambda a: a.set(channel=1, vpp=5.0), "awg channel 1 reads vpp 5.0", "rf-drive.04"),
    ("awg", lambda a: a.off(), "awg channel 1 reads output False", "rf-drive.04"),
], ids=["pwm-duty", "pwm-phase", "pwm-off", "awg-vpp", "awg-off"])
def test_a_drive_source_changed_while_the_run_waited_aborts_it(live, tmp_path,
                                                               role, change, reads, step):
    visas, gws = live
    rdir = _waiting(tmp_path)
    _other_bench(role, change)
    run = _resume(rdir)
    assert run.rec["status"] == "aborted"
    err = run.rec["error"]
    assert err.startswith(f"{HUMAN['id']}: {reads}"), err
    assert f"as {step} left it" in err and "waited on a person" in err, err
    assert run.rec["safe_state"].startswith("supply outputs off"), run.rec["safe_state"]
    assert visas["TCPIP::psu"].psu.set[1]["OUTP"] is False
    assert gws["/dev/ttyPWM"].enable is False
    assert visas["TCPIP::awg"].sdg.ch[1]["out"] == "OFF"
    assert next(r for r in run.rec["steps"] if r["id"] == HUMAN["id"])["confirmed_by"] == "ann"
    with pytest.raises(runner.RunError, match="run is aborted"):
        runner.advance(runner.Run(rdir))


def test_a_held_sim_run_resumes_past_its_drive_sources(tmp_path, monkeypatch):
    """The sim keeps its pwm and awg on the pickled board, so a run held at
    every person's step reads back what it left and goes on to passed."""
    hand_written(monkeypatch)
    run = runner.advance(runner.create(_ws(tmp_path), bench="sim", bench_cfg=None,
                                       label=None, faults=[], hold=True))
    rdir, seen = run.dir, []
    while run.rec["status"] == "awaiting_human":
        sid = run.rec["cursor"]
        seen.append(sid)
        run = runner.Run(rdir)
        value = runner._sim_value(run.steps[run.index(sid)])
        runner.confirm(run, sid, "ann", None if value is None else str(value), None)
        runner.advance(run)
    assert run.rec["status"] == "passed", run.rec.get("error")
    assert HUMAN["id"] in seen

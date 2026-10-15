"""Every run on record: no run dir is left "running" with no report.

test_npie_run_report.py covers the ways a run ends as designed. This covers
the ways npie_run.py can stop once it has made a run dir: a fault argument
that parses only halfway, a bench that can't be built, Ctrl-C mid-step (at
start and at resume), and SIGTERM or SIGHUP mid-step (a killed process, a job
timeout, a dropped ssh session), sent for real to npie_run.py's own process. Each must either be refused before a run dir
exists, or leave the run aborted, its supply off and its report.tex written.
Nothing here opens an instrument.
"""
from __future__ import annotations

import json
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NPIE = ROOT / "skill" / "scripts"
FIX = ROOT / "tests" / "fixtures" / "npie" / "mini-bldc"
sys.path.insert(0, str(NPIE))
import npie_run  # noqa: E402
from npielib import instruments  # noqa: E402


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.delenv("NPIE_BENCH_HOST", raising=False)
    w = tmp_path / "mini-bldc"
    shutil.copytree(FIX, w)
    r = subprocess.run([sys.executable, str(NPIE / "procedure_gen.py"), "--workspace", str(w)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    yield w
    _no_orphans(w)


def _no_orphans(w: Path):
    """Every run dir is on record: a report.tex, and not 'running' (no
    process is behind it once npie_run.py has returned)."""
    for rdir in sorted((w / "bringup" / "runs").glob("*")):
        rec = json.loads((rdir / "run.json").read_text(encoding="utf-8"))
        assert rec["status"] != "running", rdir
        assert (rdir / "report.tex").is_file(), rdir


def _main(capsys, *args):
    try:
        rc = npie_run.main(list(args))
    except KeyboardInterrupt:   # escaping would stop the whole pytest session
        pytest.fail("Ctrl-C escaped npie_run.main with a run under way")
    return rc, json.loads(capsys.readouterr().out)


def _aborted(out, why):
    run = Path(out["run"])
    assert out["status"] == "aborted" and why in out["error"], out
    assert out["safe_state"].startswith("supply outputs off"), out
    assert why in (run / "report.tex").read_text(encoding="ascii")


def test_a_rail_fault_with_no_volts_is_refused_before_a_run_dir(ws, capsys):
    for bad in ("rail:+5V=abc", "rail:+5V"):
        rc, out = _main(capsys, "start", "--workspace", str(ws), "--dry-run", "--fault", bad)
        assert rc == 2 and "rail:<net>=<volts>" in out["error"], out
    assert not list((ws / "bringup" / "runs").glob("*"))


def test_a_bench_that_cannot_be_built_aborts_on_record(ws, capsys, monkeypatch):
    def broken(self, *a, **k):
        raise RuntimeError("sim board would not build")
    monkeypatch.setattr(instruments.SimBoard, "__init__", broken)
    rc, out = _main(capsys, "start", "--workspace", str(ws), "--dry-run")
    assert rc == 2
    assert out["status"] == "aborted" and "RuntimeError" in out["error"], out
    assert "RuntimeError" in (Path(out["run"]) / "report.tex").read_text(encoding="ascii")


def test_ctrl_c_mid_step_turns_the_supply_off_and_leaves_the_report(ws, capsys, monkeypatch):
    def ctrl_c(self, *a, **k):
        raise KeyboardInterrupt
    monkeypatch.setattr(instruments.SimDmm, "measure", ctrl_c)
    rc, out = _main(capsys, "start", "--workspace", str(ws), "--dry-run")
    assert rc == 130
    _aborted(out, "interrupted")


def test_ctrl_c_at_resume_turns_the_supply_off_and_leaves_the_report(ws, capsys, monkeypatch):
    rc, out = _main(capsys, "start", "--workspace", str(ws), "--dry-run", "--hold")
    run = out["run"]

    def ctrl_c(*a, **k):      # the next instrument step, whatever its type
        raise KeyboardInterrupt
    monkeypatch.setattr(npie_run.runner, "execute", ctrl_c)
    for _ in range(10):       # human steps can come back to back
        _main(capsys, "confirm", "--run", run, "--step", out["awaiting"]["step"], "--by", "ihsan")
        rc, out = _main(capsys, "resume", "--run", run)
        if rc != 1 or out["status"] != "awaiting_human":
            break
    assert rc == 130
    _aborted(out, "interrupted")


# npie_run.py in its own process, with the first DMM reading hanging until a
# signal arrives
HANG = """
import pathlib, sys, time
sys.path.insert(0, sys.argv[1])
import npie_run
from npielib import instruments
def hang(self, *a, **k):
    pathlib.Path(sys.argv[2]).touch()
    time.sleep(120)
instruments.SimDmm.measure = hang
sys.exit(npie_run.main(sys.argv[3:]))
"""


@pytest.mark.parametrize("name", npie_run.STOP_SIGNALS)
def test_a_stop_signal_mid_step_turns_the_supply_off_and_leaves_the_report(ws, tmp_path, name):
    sig = getattr(signal, name, None)
    if sig is None:
        pytest.skip(f"no {name} on this platform")
    ready = tmp_path / "mid-step"
    p = subprocess.Popen([sys.executable, "-c", HANG, str(NPIE), str(ready),
                          "start", "--workspace", str(ws), "--dry-run"],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        for _ in range(600):
            if ready.exists() or p.poll() is not None:
                break
            time.sleep(0.05)
        assert ready.exists(), p.communicate(timeout=30)
        p.send_signal(sig)
        stdout, stderr = p.communicate(timeout=60)
    finally:
        if p.poll() is None:
            p.kill()
    assert p.returncode == 128 + sig, stdout + stderr
    _aborted(json.loads(stdout), f"interrupted by {name}")


def test_stop_signal_handlers_are_put_back(ws, capsys):
    before = signal.getsignal(signal.SIGTERM)
    _main(capsys, "start", "--workspace", str(ws), "--dry-run")
    assert signal.getsignal(signal.SIGTERM) is before


def test_ctrl_c_before_a_run_exists_is_left_alone(ws, monkeypatch):
    def ctrl_c(*a, **k):
        raise KeyboardInterrupt
    monkeypatch.setattr(npie_run.runner, "create", ctrl_c)
    with pytest.raises(KeyboardInterrupt):
        npie_run.main(["start", "--workspace", str(ws), "--dry-run"])

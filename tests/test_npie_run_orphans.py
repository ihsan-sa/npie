"""Every run on record: no run dir is left "running" with no report.

test_npie_run_report.py covers the ways a run ends as designed. This covers
the ways npie_run.py can stop once it has made a run dir: a fault argument
that parses only halfway, a bench that can't be built, and Ctrl-C mid-step
(at start and at resume). Each must either be refused before a run dir
exists, or leave the run aborted, its supply off and its report.tex written.
Nothing here opens an instrument.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
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


def test_ctrl_c_before_a_run_exists_is_left_alone(ws, monkeypatch):
    def ctrl_c(*a, **k):
        raise KeyboardInterrupt
    monkeypatch.setattr(npie_run.runner, "create", ctrl_c)
    with pytest.raises(KeyboardInterrupt):
        npie_run.main(["start", "--workspace", str(ws), "--dry-run"])

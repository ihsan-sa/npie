"""One process drives a run at a time, and a run whose process died is ended on record.

test_npie_run_orphans.py covers every way npie_run.py stops that Python sees.
SIGKILL (out of memory, a `kill -9`, the host going down) gives it no say: the
run stays "running" with no report and, on a real bench, its supply as the
step left it. And nothing stopped a second `resume` or `confirm` from driving
a run another process was still driving, so two processes drove one bench.
Here npie_run.py runs in its own process, hung mid-step on a DMM reading,
while this one tries to drive the same run, then is killed outright.
Nothing here opens an instrument.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from test_npie_run_orphans import HANG

ROOT = Path(__file__).resolve().parents[1]
NPIE = ROOT / "skill" / "scripts"
FIX = ROOT / "tests" / "fixtures" / "npie" / "mini-bldc"
sys.path.insert(0, str(NPIE))
import npie_run  # noqa: E402
from npielib import runner  # noqa: E402


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.delenv("NPIE_BENCH_HOST", raising=False)
    w = tmp_path / "mini-bldc"
    shutil.copytree(FIX, w)
    r = subprocess.run([sys.executable, str(NPIE / "procedure_gen.py"), "--workspace", str(w)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return w


def _main(capsys, *args):
    rc = npie_run.main(list(args))
    return rc, json.loads(capsys.readouterr().out)


@pytest.fixture
def hung(ws, tmp_path):
    """npie_run.py start, in its own process, hung on its first DMM reading."""
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
        (rdir,) = (ws / "bringup" / "runs").glob("*")
        yield p, rdir
    finally:
        if p.poll() is None:
            p.kill()
            p.communicate(timeout=30)


def _rec(rdir: Path) -> dict:
    return json.loads((rdir / "run.json").read_text(encoding="utf-8"))


def test_a_run_another_process_drives_is_refused(hung, capsys):
    p, rdir = hung
    before = (rdir / "run.json").read_bytes()
    cursor = _rec(rdir)["cursor"]
    for args in (("resume",), ("confirm", "--step", cursor, "--by", "ihsan")):
        rc, out = _main(capsys, args[0], "--run", str(rdir), *args[1:])
        assert rc == 2 and "another npie_run.py" in out["error"], out
        assert f"pid {p.pid}" in out["error"], out
    assert (rdir / "run.json").read_bytes() == before
    assert not (rdir / "report.tex").exists()
    p.terminate()           # the process that holds it still ends the run itself
    stdout, stderr = p.communicate(timeout=60)
    out = json.loads(stdout)
    assert out["status"] == "aborted" and "interrupted" in out["error"], stdout + stderr
    assert out["safe_state"].startswith("supply outputs off"), out


@pytest.mark.parametrize("cmd", ["resume", "confirm"])
def test_a_killed_run_is_aborted_on_record_by_the_next_call(hung, capsys, cmd):
    p, rdir = hung
    p.kill()                # SIGKILL: no handler, no abort, no report
    p.communicate(timeout=30)
    rec = _rec(rdir)
    assert rec["status"] == "running" and not (rdir / "report.tex").exists()
    args = ("--step", rec["cursor"], "--by", "ihsan") if cmd == "confirm" else ()
    rc, out = _main(capsys, cmd, "--run", str(rdir), *args)
    assert rc == 2 and out["status"] == "aborted", out
    assert out["error"].startswith(rec["cursor"] + ": ") and "stopped without ending it" in out["error"]
    assert out["safe_state"].startswith("supply outputs off"), out
    assert "stopped without ending it" in (rdir / "report.tex").read_text(encoding="ascii")
    assert (rdir / "report.pdf").read_bytes().startswith(b"%PDF-")
    assert not [r for r in _rec(rdir)["steps"] if r.get("confirmed_by") == "ihsan"]
    rc, out = _main(capsys, "resume", "--run", str(rdir))
    assert rc == 2 and "run is aborted" in out["error"], out


def test_npie_run_lets_go_of_the_run_when_it_returns(ws, capsys):
    rc, out = _main(capsys, "start", "--workspace", str(ws), "--dry-run", "--hold")
    assert rc == 1 and out["status"] == "awaiting_human", out
    run = runner.Run(Path(out["run"]))
    run.claim()             # would raise if start still held it
    run.release()
    rc, out = _main(capsys, "confirm", "--run", out["run"], "--step", out["awaiting"]["step"],
                    "--by", "ihsan")
    assert out["status"] in ("running", "awaiting_human"), out
    rc, out = _main(capsys, "resume", "--run", out["run"])
    assert out["status"] in ("awaiting_human", "passed"), out

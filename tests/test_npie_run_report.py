"""Every run on record: npie_run.py leaves the run's report whenever it stops.

Each case dry-runs the mini-bldc fixture to one way a run can stop (passed,
failed, aborted, waiting on a person) and checks that the run dir holds a
report.tex that names that status, and that the JSON names the report. An
ended run also gets report.pdf where lualatex is on the host; elsewhere the
JSON says why not. With NPIE_REQUIRE_PDF=1, as CI sets it, a host without
lualatex fails here instead of skipping the PDF. Nothing here opens an
instrument.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NPIE = ROOT / "skill" / "scripts"
FIX = ROOT / "tests" / "fixtures" / "npie" / "mini-bldc"
sys.path.insert(0, str(NPIE))
import npie_report  # noqa: E402
import npie_run  # noqa: E402
from npielib import instruments  # noqa: E402

PDF_HOST = bool(npie_report.lualatex() and npie_report.house_style())
REQUIRE_PDF = os.environ.get("NPIE_REQUIRE_PDF") == "1"
CI = ROOT / ".github" / "workflows" / "check.yml"


def _ws(tmp_path) -> Path:
    ws = tmp_path / "mini-bldc"
    shutil.copytree(FIX, ws)
    r = subprocess.run([sys.executable, str(NPIE / "procedure_gen.py"), "--workspace", str(ws)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return ws


def _run(*args):
    e = {k: v for k, v in os.environ.items() if k != "NPIE_BENCH_HOST"}
    r = subprocess.run([sys.executable, str(NPIE / "npie_run.py"), *args],
                       capture_output=True, text=True, env=e)
    return r.returncode, json.loads(r.stdout)


def _on_record(out, status, ended=True):
    run = Path(out["run"])
    rep = out["report"]
    assert out["status"] == status and rep["tex"] == str(run / "report.tex"), out
    tex = (run / "report.tex").read_text(encoding="ascii")
    assert f"{npie_report.esc(status)}, " in tex       # the title block's status line
    assert ("Not a pass" in tex) == (status != "passed")
    if ended and PDF_HOST:
        assert rep["ok"] and (run / "report.pdf").read_bytes()[:5] == b"%PDF-", rep
    else:
        assert rep["pdf"] is None and rep["warnings"], rep
        assert not (run / "report.pdf").exists()
    if not ended:
        assert "run not ended" in rep["warnings"][0]
    return tex


def test_a_host_that_requires_the_pdf_can_build_it():
    assert PDF_HOST or not REQUIRE_PDF, (
        "NPIE_REQUIRE_PDF=1 but lualatex or housestyle.sty is missing, so no report PDF "
        "would be built")


def test_ci_builds_the_report_pdf():
    # the only place make check is sure to meet lualatex; without it every PDF
    # assertion here skips and no check ever compiles a report
    ci = CI.read_text(encoding="utf-8")
    assert "texlive-luatex" in ci and 'NPIE_REQUIRE_PDF: "1"' in ci, ci


def test_a_passed_run_leaves_its_report(tmp_path):
    rc, out = _run("start", "--workspace", str(_ws(tmp_path)), "--dry-run")
    assert rc == 0
    _on_record(out, "passed")


def test_a_failed_run_leaves_its_report(tmp_path):
    rc, out = _run("start", "--workspace", str(_ws(tmp_path)), "--dry-run",
                   "--fault", "short:+5V")
    assert rc == 1
    tex = _on_record(out, "failed")
    assert out["failed"]["id"] in tex and "Safe state: supply outputs off" in tex


def test_an_aborted_run_leaves_its_report(tmp_path, monkeypatch, capsys):
    def boom(self, *a, **k):
        raise ValueError("could not convert string to float: 'ERR'")
    monkeypatch.setattr(instruments.SimDmm, "measure", boom)
    monkeypatch.delenv("NPIE_BENCH_HOST", raising=False)
    rc = npie_run.main(["start", "--workspace", str(_ws(tmp_path)), "--dry-run"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 2
    tex = _on_record(out, "aborted")
    assert "ValueError" in tex


def test_a_run_waiting_on_a_person_leaves_its_report_so_far(tmp_path):
    rc, out = _run("start", "--workspace", str(_ws(tmp_path)), "--dry-run", "--hold")
    assert rc == 1
    _on_record(out, "awaiting_human", ended=False)
    # confirming updates it; a failed confirmation ends the run and records why
    run, step = out["run"], out["awaiting"]["step"]
    rc, out = _run("confirm", "--run", run, "--step", step, "--by", "ihsan")
    _on_record(out, "awaiting_human", ended=False)
    rc, out = _run("resume", "--run", run)
    rc, out = _run("confirm", "--run", run, "--step", out["awaiting"]["step"], "--by", "ihsan",
                   "--fail", "R12 missing")
    assert rc == 1
    assert "R12 missing" in _on_record(out, "failed")


def test_status_does_not_rewrite_the_report(tmp_path):
    rc, out = _run("start", "--workspace", str(_ws(tmp_path)), "--dry-run", "--hold")
    tex = Path(out["run"]) / "report.tex"
    tex.unlink()
    rc, st = _run("status", "--run", out["run"])
    assert "report" not in st and not tex.exists()


def test_a_report_error_does_not_change_the_runs_outcome(tmp_path, monkeypatch, capsys):
    def broken(*a, **k):
        raise KeyError("board")
    monkeypatch.setattr(npie_report, "build_tex", broken)
    monkeypatch.delenv("NPIE_BENCH_HOST", raising=False)
    rc = npie_run.main(["start", "--workspace", str(_ws(tmp_path)), "--dry-run"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["status"] == "passed"
    assert not out["report"]["ok"] and "KeyError" in out["report"]["warnings"][0]

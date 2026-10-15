"""Every run on record: npie_run.py leaves the run's report whenever it stops.

Each case dry-runs the mini-bldc fixture to one way a run can stop (passed,
failed, aborted, waiting on a person) and checks that the run dir holds a
report.tex that names that status, and that the JSON names the report. Each
run, a waiting one too, also gets report.pdf: in the house style where
lualatex is on the host, elsewhere a plain one, and the JSON says why. With NPIE_REQUIRE_PDF=1,
as CI sets it, a host without lualatex fails here instead of passing on the
plain PDF. Nothing here opens an instrument.
"""
from __future__ import annotations

import json
import os
import re
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


def _on_record(out, status):
    run = Path(out["run"])
    rep = out["report"]
    assert out["status"] == status and rep["tex"] == str(run / "report.tex"), out
    tex = (run / "report.tex").read_text(encoding="ascii")
    assert f"{npie_report.esc(status)}, " in tex       # the title block's status line
    assert ("Not a pass" in tex) == (status != "passed")
    # every run leaves a PDF, ended or waiting: the house style's, else the plain one
    assert rep["pdf"] == str(run / "report.pdf"), rep
    pdf = (run / "report.pdf").read_bytes()
    assert pdf[:5] == b"%PDF-", rep
    assert rep["ok"] if PDF_HOST else "plain PDF" in rep["warnings"][-1], rep
    if not PDF_HOST:     # the plain PDF holds the report's words
        assert _flat("\n".join(npie_report.plain_text(tex))) in _flat(pdf_text(pdf))
    return tex


def pdf_text(pdf: bytes) -> str:
    """A plain report PDF's text, after checking its xref points at each of
    its objects (what a reader follows to open it)."""
    assert pdf.startswith(b"%PDF-") and pdf.rstrip().endswith(b"%%EOF")
    xref = int(re.search(rb"startxref\n(\d+)\n", pdf)[1])
    head, *entries = pdf[xref:].split(b"trailer")[0].decode("ascii").splitlines()[1:]
    assert entries and int(head.split()[1]) == len(entries)
    for n, e in enumerate(entries[1:], 1):
        assert pdf[int(e[:10]):].startswith(f"{n} 0 obj\n".encode()), (n, e)
    rows = re.findall(rb"\(((?:\\.|[^\\)])*)\) Tj", pdf)
    return "\n".join(re.sub(r"\\(.)", r"\1", r.decode("ascii")) for r in rows)


def _flat(s: str) -> str:
    return re.sub(r"\s+", "", s)


def test_a_host_without_lualatex_still_leaves_a_pdf(tmp_path):
    # NPIE_LUALATEX naming no program stands for a bench host with no TeX at
    # all, and for a compile that fails: either way the plain PDF is written
    ws = _ws(tmp_path)
    env = {k: v for k, v in os.environ.items() if k != "NPIE_BENCH_HOST"}
    env["NPIE_LUALATEX"] = str(tmp_path / "no-lualatex")
    r = subprocess.run([sys.executable, str(NPIE / "npie_run.py"), "start", "--workspace", str(ws),
                        "--dry-run", "--fault", "short:+5V"], capture_output=True, text=True, env=env)
    out = json.loads(r.stdout)
    rep, run = out["report"], Path(out["run"])
    assert r.returncode == 1 and out["status"] == "failed"
    assert not rep["ok"] and rep["pdf"] == str(run / "report.pdf"), rep
    assert rep["warnings"][0].startswith("lualatex FileNotFoundError"), rep
    text = pdf_text((run / "report.pdf").read_bytes())
    assert text.startswith("Plain report: lualatex FileNotFoundError")
    for want in ["failed, ", "** Not a pass", out["failed"]["id"],
                 "Safe state: supply outputs off", "Where each limit came from"]:
        assert _flat(want) in _flat(text), want
    tex = (run / "report.tex").read_text(encoding="ascii")
    assert _flat("\n".join(npie_report.plain_text(tex))) in _flat(text)


def test_the_plain_text_undoes_every_escape():
    s = "R_1 & 50% {x} $5 #2 a\\b <c> d|e ^f ~g"
    assert npie_report.detex(npie_report.esc(s)) == s
    assert npie_report.detex(npie_report.sid("rails.02")) == "rails.02"


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
    # a run left waiting may never be resumed, so it has its PDF too, saying
    # it has not ended and what it waits on
    rc, out = _run("start", "--workspace", str(_ws(tmp_path)), "--dry-run", "--hold")
    assert rc == 1
    tex = _on_record(out, "awaiting_human")
    run, step = out["run"], out["awaiting"]["step"]
    assert _flat(npie_report.esc(f"It has not ended: it waits on a person at {step} "
                                 f"({out['awaiting']['text']})")) in _flat(tex)
    # confirming updates it; a failed confirmation ends the run and records why
    rc, out = _run("confirm", "--run", run, "--step", step, "--by", "ihsan")
    tex = _on_record(out, "awaiting_human")
    assert npie_report.esc(f"It has not ended: {step} is confirmed and the run waits to be "
                           "resumed from it") in tex
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


def test_a_run_anywhere_on_the_host_leaves_its_report_with_its_captures(tmp_path):
    # a board workspace can sit under any path, a person's name with an accent
    # in it included; the report names each capture from the run dir, so no
    # character of that path ends up in report.tex
    where = tmp_path / "José's bench #2 50% done"
    where.mkdir()
    rc, out = _run("start", "--workspace", str(_ws(where)), "--dry-run")
    assert rc == 0, out
    assert not any(w.startswith("report not written") for w in out["report"]["warnings"]), out
    tex = _on_record(out, "passed")
    run = Path(out["run"])
    caps = sorted(p.relative_to(run).as_posix() for p in (run / "captures").glob("*.png"))
    assert caps, "the fixture's dry run saves no scope capture"
    for c in caps:
        assert r"\hsdiagram[width=\linewidth]{" + c + "}" in tex, c
    assert str(tmp_path) not in tex


def _broken(*a, **k):
    raise KeyError("board José")    # an error message can carry anything


def _bare(out) -> str:
    """The run.json-only PDF's text, after checking it is what the JSON names."""
    run, rep = Path(out["run"]), out["report"]
    assert not rep["ok"] and rep["tex"] is None and rep["pdf"] == str(run / "report.pdf"), rep
    assert rep["warnings"][0].startswith("report.tex was not built: KeyError"), rep
    assert not (run / "report.tex").exists()
    text = pdf_text((run / "report.pdf").read_bytes())
    assert _flat(text).startswith(_flat("Plain report from run.json alone, since the report.tex "
                                        "was not built: KeyError: 'board Jos?'.")), text[:200]
    rec = json.loads((run / "run.json").read_text(encoding="utf-8"))
    assert _flat(f"Run {run.name}: {rec['status']}") in _flat(text)
    if rec.get("safe_state"):
        assert _flat(f"safe_state: {rec['safe_state']}") in _flat(text)
    for r in rec["steps"]:      # every step it ran, with its verdict
        assert _flat(f"{r['id']} | {r['type']} | {r.get('verdict', '-')}") in _flat(text), r["id"]
    return text


def test_a_report_error_does_not_change_the_runs_outcome(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(npie_report, "build_tex", _broken)
    monkeypatch.delenv("NPIE_BENCH_HOST", raising=False)
    rc = npie_run.main(["start", "--workspace", str(_ws(tmp_path)), "--dry-run"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["status"] == "passed"
    _bare(out)


def test_a_failed_run_whose_report_cannot_be_built_still_leaves_a_pdf(tmp_path, monkeypatch, capsys):
    # a bug in build_tex used to leave an ended run with no report at all
    monkeypatch.setattr(npie_report, "build_tex", _broken)
    monkeypatch.delenv("NPIE_BENCH_HOST", raising=False)
    rc = npie_run.main(["start", "--workspace", str(_ws(tmp_path)), "--dry-run",
                        "--fault", "short:+5V"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1 and out["status"] == "failed"
    text = _bare(out)
    assert _flat("safe_state: supply outputs off") in _flat(text)
    assert _flat(f"{out['failed']['id']} | ") in _flat(text)


def test_a_waiting_run_whose_report_cannot_be_built_drops_the_stale_one(tmp_path, monkeypatch,
                                                                          capsys):
    # start's report.tex says the run waits at its first step; once a confirm
    # can't rebuild it, it would say so of a run that has moved on
    rc, out = _run("start", "--workspace", str(_ws(tmp_path)), "--dry-run", "--hold")
    run, step = out["run"], out["awaiting"]["step"]
    assert (Path(run) / "report.tex").is_file()
    monkeypatch.setattr(npie_report, "build_tex", _broken)
    monkeypatch.delenv("NPIE_BENCH_HOST", raising=False)
    npie_run.main(["confirm", "--run", run, "--step", step, "--by", "ihsan"])
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "awaiting_human"
    assert _flat(f"{step} | human | pass | - | by ihsan") in _flat(_bare(out))


def test_a_run_dir_with_no_procedure_still_gets_a_pdf(tmp_path, capsys):
    rc, out = _run("start", "--workspace", str(_ws(tmp_path)), "--dry-run")
    run = Path(out["run"])
    (run / "procedure.json").unlink()
    assert npie_report.main(["--run", str(run)]) == 1
    rep = json.loads(capsys.readouterr().out)
    assert rep["tex"] is None and rep["warnings"][0].startswith(
        "report.tex was not built: FileNotFoundError"), rep
    assert "Run " + run.name + ": passed" in pdf_text((run / "report.pdf").read_bytes())
    (run / "run.json").unlink()      # no run.json: not a run
    assert npie_report.main(["--run", str(run)]) == 2

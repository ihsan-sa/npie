"""Every run on record, with where its limits came from: a run's report shows
each step's whole limit (its `expect` and every console field's) and the
step's `derived_from`, and calls out on its first page a limit a person set
in bringup/overrides.yaml, beside the generated one it replaced.

Each fixture variant of test_npie_limit_fields is built into report.tex for a
run that has reached no step yet, so every limit procgen emits is checked
without running it. One dry run with an override checks a real run's report,
and its PDF where lualatex is on the host (CI requires it). Nothing here
opens an instrument.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NPIE = ROOT / "skill" / "scripts"
if str(NPIE) not in sys.path:
    sys.path.insert(0, str(NPIE))

import npie_report  # noqa: E402
from npielib import design, procgen  # noqa: E402
from test_npie_limit_fields import VARIANTS  # noqa: E402
from test_npie_limit_trace import FIX  # noqa: E402

PDF_HOST = bool(npie_report.lualatex() and npie_report.house_style())
SOURCES = "Where each limit came from"
# a run that has reached no step: every step's row shows its limit, "not run"
NOT_STARTED = {"board": "mini-bldc", "bench": "sim", "status": "running", "steps": [],
               "started": "-", "procedure_sha256": "0" * 64, "procedure_generated": "-",
               "faults": [], "seed": 0}


def _section(tex: str, title: str) -> str:
    after = tex.split(r"\section{" + title + "}", 1)[1]
    return re.split(r"\\section\{|\\end\{document\}", after, maxsplit=1)[0]


def _rows(section: str, step_id: str) -> list[str]:
    head = npie_report.sid(step_id) + " & "
    return [line for line in section.splitlines() if line.startswith(head)]


def _limited(proc: dict) -> list[dict]:
    return [s for st in proc["stages"] for s in st["steps"] if s.get("expect") or s.get("fields")]


@pytest.mark.parametrize("variant", VARIANTS, ids=lambda v: v.__name__.strip("_").replace("_", "-"))
def test_every_limit_and_its_source_is_in_the_report(tmp_path, variant):
    ws = tmp_path / "mini-bldc"
    shutil.copytree(FIX, ws)
    variant(ws)
    proc = procgen.generate(design.load(ws))
    tex = npie_report.build_tex(tmp_path, NOT_STARTED, proc)
    meas, src = _section(tex, "Measurements"), _section(tex, SOURCES)
    missing = []
    for s in _limited(proc):
        want = [npie_report.limits(s["expect"])] if s.get("expect") else []
        want += [f"{npie_report.esc(k)}: {npie_report.limits(v)}"
                 for k, v in (s.get("fields") or {}).items()]
        row = _rows(meas, s["id"])
        if len(row) != 1 or not all(w in row[0] for w in want):
            missing.append(f"{s['id']} {s.get('text')!r}: limits {want} not in {row}")
        row = _rows(src, s["id"])
        if len(row) != 1 or npie_report.esc(s["derived_from"]) not in row[0]:
            missing.append(f"{s['id']} {s.get('text')!r}: source {s['derived_from']!r} not in {row}")
    assert _limited(proc) and not missing, "limits off the record:\n" + "\n".join(missing)


def test_a_limit_a_person_set_is_called_out(tmp_path):
    ws = tmp_path / "mini-bldc"
    shutil.copytree(FIX, ws)
    (ws / "bringup").mkdir(exist_ok=True)
    (ws / "bringup" / "overrides.yaml").write_text("rails.02: {min: 4.9, max: 5.1}\n")
    r = subprocess.run([sys.executable, str(NPIE / "procedure_gen.py"), "--workspace", str(ws)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    gen = json.loads((ws / "bringup" / "procedure.json").read_text())["overrides"][0]["generated"]
    env = {k: v for k, v in os.environ.items() if k != "NPIE_BENCH_HOST"}
    r = subprocess.run([sys.executable, str(NPIE / "npie_run.py"), "start", "--workspace", str(ws),
                        "--dry-run"], capture_output=True, text=True, env=env)
    out = json.loads(r.stdout)
    tex = (Path(out["run"]) / "report.tex").read_text(encoding="ascii")
    callout = tex.split(r"\begin{hscallout}{Limits set by a person}", 1)[1].split(r"\end{hscallout}")[0]
    assert npie_report.sid("rails.02") + " 4.9 .. 5.1 V" in callout, callout
    assert f"(generated {npie_report.limits(gen)})" in callout, callout
    assert "overridden in bringup/overrides.yaml" in _rows(_section(tex, SOURCES), "rails.02")[0]
    if PDF_HOST:
        assert out["report"]["ok"] and out["report"]["pdf"], out["report"]

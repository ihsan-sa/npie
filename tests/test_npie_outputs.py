"""npie shopping list and run report (reference/design.md sections 1 and 6).

Each case copies the mini-bldc fixture, generates its procedure and runs the
script under test as a subprocess, so the contract (JSON out, exit 0/1/2) is
what is checked. Nothing here opens an instrument.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NPIE = ROOT / "skill" / "scripts"
FIX = ROOT / "tests" / "fixtures" / "npie" / "mini-bldc"
sys.path.insert(0, str(NPIE))
import npie_report  # noqa: E402


def _py(script, *args):
    e = {k: v for k, v in os.environ.items() if k != "NPIE_BENCH_HOST"}
    r = subprocess.run([sys.executable, str(NPIE / script), *args],
                       capture_output=True, text=True, env=e)
    return r.returncode, json.loads(r.stdout)


def _ws(tmp_path, spin=False) -> Path:
    ws = tmp_path / "mini-bldc"
    shutil.copytree(FIX, ws)
    if spin:
        mf = ws / "firmware" / "fwe-manifest.json"
        m = json.loads(mf.read_text())
        m["commands"].append({"name": "spin", "args": "", "reply": "OK {json}", "safe": False})
        mf.write_text(json.dumps(m))
    rc, out = _py("procedure_gen.py", "--workspace", str(ws))
    assert rc == 0, out
    return ws


def _proc(ws):
    return json.loads((ws / "bringup" / "procedure.json").read_text())


# ---- shopping.py

def test_shopping_lists_each_role_the_procedure_uses(tmp_path):
    ws = _ws(tmp_path)
    rc, out = _py("shopping.py", "--workspace", str(ws))
    assert rc == 0, out
    proc = _proc(ws)
    assert sorted(e["role"] for e in out["equipment"]) == sorted(proc["roles"])
    psu = next(e for e in out["equipment"] if e["role"] == "psu")
    vmax = max(st["set"]["v"] for s in proc["stages"] for st in s["steps"]
               if st["type"] == "supply")
    assert f"highest setting {vmax:g} V" in psu["spec"]
    # the supply is rated above the highest setting, never at it
    assert float(psu["spec"].split(" V")[0].lstrip(">= ")) > vmax
    scope = next(e for e in out["equipment"] if e["role"] == "scope")
    assert ">= 50 MHz" in scope["spec"]      # 20 kHz PWM x10 is under the floor
    assert all(e["steps"] for e in out["equipment"])
    md = (ws / "bringup" / "shopping.md").read_text()
    assert "probe-rs" in md and "| console |" in md


def test_shopping_motor_only_with_full_function_stage(tmp_path):
    rc, out = _py("shopping.py", "--workspace", str(_ws(tmp_path / "a")))
    assert rc == 0 and not any("motor" in p["what"] for p in out["parts"])
    rc, out = _py("shopping.py", "--workspace", str(_ws(tmp_path / "b", spin=True)))
    motor = [p for p in out["parts"] if "motor" in p["what"]]
    assert rc == 0 and motor and all(s.startswith("full-function.") for s in motor[0]["steps"])


def test_shopping_without_procedure_is_an_error(tmp_path):
    ws = tmp_path / "mini-bldc"
    shutil.copytree(FIX, ws)
    rc, out = _py("shopping.py", "--workspace", str(ws))
    assert rc == 2 and not out["ok"] and "procedure_gen" in out["error"]


# ---- npie_report.py

def _dry(ws, *extra):
    rc, out = _py("npie_run.py", "start", "--workspace", str(ws), "--dry-run", *extra)
    return Path(out["run"])


def test_report_tex_of_a_passed_run(tmp_path):
    run = _dry(_ws(tmp_path))
    rc, out = _py("npie_report.py", "--run", str(run), "--tex-only")
    assert rc == 1 and out["pdf"] is None and "--tex-only" in out["warnings"][0]
    tex = (run / "report.tex").read_text(encoding="ascii")
    assert "Not a pass" not in tex and "passed" in tex
    assert r"\begin{hsfigure}" in tex                 # the ripple capture
    assert "Human steps" in tex and r"\texttt{sim}" in tex
    rec = json.loads((run / "run.json").read_text())
    fl = next(r for r in rec["steps"] if r["type"] == "flash")
    assert fl["artifact_sha256"] == "0" * 64          # fixture manifest's elf hash
    assert fl["artifact_sha256"][:16] in tex


def test_report_tex_of_a_failed_run_says_so(tmp_path):
    run = _dry(_ws(tmp_path), "--fault", "short:+5V")
    rc, out = _py("npie_report.py", "--run", str(run), "--tex-only")
    assert rc == 1 and out["status"] == "failed"
    tex = (run / "report.tex").read_text(encoding="ascii")
    rec = json.loads((run / "run.json").read_text())
    failed = [r["id"] for r in rec["steps"] if r["verdict"] == "fail"]
    assert "Not a pass" in tex and failed and failed[0] in tex
    assert "Safe state: supply outputs off" in tex
    assert "not run" in tex                           # steps after the stop


def test_report_escapes_to_ascii():
    assert npie_report.esc("R_1 & 50% {x} µ") == r"R\_1 \& 50\% \{x\} ?"
    assert npie_report.limits({"min": 1.0, "max": None, "unit": "ohm"}) == ">= 1 ohm"
    assert npie_report.limits({"edges_min": 2}) == ">= 2 edges"


@pytest.mark.skipif(not (npie_report.lualatex() and npie_report.house_style()),
                    reason="lualatex not on this host (or NPIE_HOUSE_STYLE names no housestyle.sty)")
def test_report_pdf_builds(tmp_path):
    run = _dry(_ws(tmp_path))
    rc, out = _py("npie_report.py", "--run", str(run))
    assert rc == 0 and out["ok"], out
    assert (run / "report.pdf").read_bytes()[:5] == b"%PDF-"
    assert not list(run.glob("*.aux")) and not list(run.glob("*.log"))


def test_report_of_a_non_run_is_an_error(tmp_path):
    rc, out = _py("npie_report.py", "--run", str(tmp_path))
    assert rc == 2 and not out["ok"]

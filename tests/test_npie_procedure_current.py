"""A run holds a board only to the limits procgen gives from the design now
(GOALS.md, Every run follows the procedure procgen makes now).

The input hashes catch a design that changed under a procedure, but not a
procedure.json an older npie wrote from the same design (one from before
#77 aimed the open-loop D0 past the ovp trip; one from before #64 set a
supply above the input's rating) or one edited by hand, though limits are
generated, not hand-written. `runner.not_generated` regenerates the
procedure and names each step that differs: `start` refuses it, and a run
waiting on a person is aborted with the supply off at `confirm` or `resume`.
Nothing here opens an instrument.
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

import npie_run  # noqa: E402
import procedure_gen  # noqa: E402
from npielib import procgen, runner  # noqa: E402
from test_npie_limit_fields import VARIANTS  # noqa: E402
from test_npie_limit_trace import FIX  # noqa: E402


def _ws(tmp_path: Path, variant=lambda ws: None) -> Path:
    ws = tmp_path / "mini-bldc"
    shutil.copytree(FIX, ws)
    variant(ws)
    assert procedure_gen.main(["--workspace", str(ws)]) == 0
    return ws


def _edit(ws: Path, change) -> None:
    pj = ws / "bringup" / "procedure.json"
    proc = json.loads(pj.read_text())
    change(proc)
    pj.write_text(json.dumps(proc, indent=1) + "\n")


def _start(ws: Path, capsys) -> tuple[int, dict]:
    capsys.readouterr()
    rc = npie_run.main(["start", "--workspace", str(ws), "--dry-run"])
    return rc, json.loads(capsys.readouterr().out)


def _older_npie(monkeypatch) -> None:
    """procgen as a later npie has it: the first power-up's current limit moved."""
    monkeypatch.setattr(procgen, "FIRST_ILIMIT_A", procgen.FIRST_ILIMIT_A * 1.37)


@pytest.mark.parametrize("variant", VARIANTS, ids=lambda v: v.__name__.strip("_"))
def test_a_fresh_procedure_is_what_procgen_makes(tmp_path, capsys, variant):
    ws = _ws(tmp_path, variant)
    proc = json.loads((ws / "bringup" / "procedure.json").read_text())
    assert runner.not_generated(proc, ws) == []


# each edit returns what the refusal must name
def _widen(proc):
    s = next(s for s in runner._steps(proc) if s.get("expect", {}).get("max") is not None)
    s["expect"]["max"] *= 10
    return s["id"]


def _drop(proc):
    return proc["stages"][-1]["steps"].pop()["id"]


def _add(proc):
    st = proc["stages"][0]
    st["steps"].append({"id": f"{st['id']}.99", "type": "human", "text": "extra"})
    return f"{st['id']}.99"


def _reorder(proc):
    proc["stages"][0], proc["stages"][1] = proc["stages"][1], proc["stages"][0]
    return "stages"


def _unskip(proc):
    proc["skipped"] = []
    return "skipped"


EDITS = {"a limit widened": _widen, "a step dropped": _drop, "a step added": _add,
         "stages reordered": _reorder, "the skipped list emptied": _unskip}


@pytest.mark.parametrize("name", EDITS)
def test_start_refuses_a_procedure_edited_by_hand(tmp_path, capsys, name):
    ws = _ws(tmp_path)
    named = []
    _edit(ws, lambda proc: named.append(EDITS[name](proc)))
    rc, out = _start(ws, capsys)
    assert rc == 2 and "not what procedure_gen.py makes" in out["error"], out
    assert named[0] in out["error"], out["error"]
    assert not (ws / "bringup" / "runs").exists()


def test_start_refuses_a_procedure_an_older_npie_wrote(tmp_path, capsys, monkeypatch):
    ws = _ws(tmp_path)
    proc = json.loads((ws / "bringup" / "procedure.json").read_text())
    supply = next(s["id"] for s in runner._steps(proc) if s["type"] == "supply")
    _older_npie(monkeypatch)
    rc, out = _start(ws, capsys)
    assert rc == 2 and supply in out["error"], out
    assert not (ws / "bringup" / "runs").exists()
    assert procedure_gen.main(["--workspace", str(ws)]) == 0     # regenerated, it runs
    rc, out = _start(ws, capsys)
    assert rc == 0 and out["status"] == "passed", out


def _held(ws: Path) -> runner.Run:
    run = runner.create(ws, bench="sim", bench_cfg=None, label=None, faults=[], hold=True)
    runner.advance(run)
    assert run.rec["status"] == "awaiting_human"
    return run


def _aborted(run: runner.Run) -> None:
    rec = run.rec
    assert rec["status"] == "aborted", rec.get("error")
    assert "no longer what procedure_gen.py makes" in rec["error"], rec["error"]
    assert rec["safe_state"].startswith("supply outputs off"), rec["safe_state"]
    with pytest.raises(runner.RunError, match="aborted"):
        runner.advance(runner.Run(run.dir))


def test_resume_aborts_a_run_when_npie_changes_while_it_waits(tmp_path, capsys, monkeypatch):
    run = _held(_ws(tmp_path))
    _older_npie(monkeypatch)
    _aborted(runner.advance(runner.Run(run.dir)))


def test_confirm_aborts_a_run_when_npie_changes_while_it_waits(tmp_path, capsys, monkeypatch):
    run = _held(_ws(tmp_path))
    _older_npie(monkeypatch)
    run = runner.Run(run.dir)
    run.open_bench()
    step = run.rec["cursor"]
    runner.confirm(run, step, "tester", None, None)
    _aborted(run)
    assert not any(r["id"] == step and r.get("confirmed_by") for r in run.rec["steps"])

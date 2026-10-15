"""A human step is answered by a named person (SKILL.md rule 2; GOALS.md,
Every live confirmation names a person and a reading).

`confirm` took any non-blank `--by`, so a live run's step could be confirmed
`--by sim`, which is what a simulated run records for itself: the report then
showed a step on a real bench, the first power-up or the motor stage perhaps,
as if nobody had done it. And a typed `--value nan` or `inf` was taken as a
reading: nan failed the run on a typo and went into run.json as `NaN`, which
isn't JSON. Both are now refused with nothing recorded, so the person can
answer again.

Nothing real is opened: the live run is test_npie_resume_supply's, on
test_npie_live_drivers' fake VISA.
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
from npielib import runner  # noqa: E402
from test_npie_instrument_lock import models  # noqa: E402,F401
from test_npie_limit_trace import FIX, _evn_header  # noqa: E402
from test_npie_resume_supply import _waiting  # noqa: E402


def _record(rdir: Path) -> str:
    return (rdir / "run.json").read_text(encoding="utf-8")


@pytest.mark.parametrize("by", ["sim", "SIM", " sim "])
def test_a_live_step_confirmed_as_sim_is_refused(models, tmp_path, by):
    rdir, _, h = _waiting(tmp_path)
    before = _record(rdir)
    with pytest.raises(runner.RunError, match="--by sim .* name the person"):
        runner.confirm(runner.Run(rdir), h["id"], by, str(runner._sim_value(h)), None)
    assert _record(rdir) == before


def test_npie_run_confirm_as_sim_on_a_live_run_exits_2(models, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NPIE_BENCH_HOST", "1")
    rdir, _, h = _waiting(tmp_path)
    capsys.readouterr()     # procedure_gen's own JSON
    before = _record(rdir)
    rc = npie_run.main(["confirm", "--run", str(rdir), "--step", h["id"], "--by", "sim"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 2 and "--by sim" in out["error"], out
    assert _record(rdir) == before
    rc = npie_run.main(["confirm", "--run", str(rdir), "--step", h["id"], "--by", "ann"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1 and out["status"] == "awaiting_human", out
    rec = json.loads(_record(rdir))
    assert next(r for r in rec["steps"] if r["id"] == h["id"])["confirmed_by"] == "ann"


def _held_at_a_reading(tmp_path: Path) -> tuple[runner.Run, dict]:
    """A held dry run of the EVN-header board, stopped at the first step that
    asks a person to type a reading against a limit."""
    ws = tmp_path / "evn"
    shutil.copytree(FIX, ws)
    _evn_header(ws)
    assert procedure_gen.main(["--workspace", str(ws)]) == 0
    run = runner.create(ws, bench="sim", bench_cfg=None, label=None, faults=None, hold=True)
    runner.advance(run)
    while run.rec["status"] == "awaiting_human":
        s = run.steps[run.index(run.rec["cursor"])]
        if s.get("value") and s.get("expect"):
            return run, s
        runner.confirm(run, s["id"], "ann", None, None)
        runner.advance(run)
    pytest.fail(f"the EVN-header board's run {run.rec['status']} before a typed reading")


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "NaN", "1e999"])
def test_a_reading_that_is_not_a_finite_number_is_refused(tmp_path, value):
    run, s = _held_at_a_reading(tmp_path)
    before = _record(run.dir)
    with pytest.raises(runner.RunError, match="not a finite number"):
        runner.confirm(run, s["id"], "ann", value, None)
    assert _record(run.dir) == before
    run = runner.Run(run.dir)
    runner.confirm(run, s["id"], "ann", str(s["expect"]["nominal"]), None)
    assert run.rec["status"] == "awaiting_human"
    done = next(r for r in run.rec["steps"] if r["id"] == s["id"])
    assert done["verdict"] == "pass" and done["confirmed_by"] == "ann"


def test_a_held_dry_run_records_the_person_who_answered(tmp_path):
    run, s = _held_at_a_reading(tmp_path)
    assert {r["confirmed_by"] for r in run.rec["steps"] if r["type"] == "human"} == {"ann"}

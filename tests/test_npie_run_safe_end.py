"""Every ended run leaves the supply off (GOALS.md, Every ended run leaves
the supply off; SKILL.md rule 3).

The runner turned the outputs off only when a run failed or aborted. A
passed run left them as its last step did, and a generated procedure turns
the supply off only at the end of its limits stage, which a board with no
operating range in requirements.md skips (`_no_range` here). So a
passed run on such a board, which on a real bench may end with nobody
there, left the board powered and, after the RF stage, the pwm generator
and the awg driving it. Each fixture variant of test_npie_rated_input that
powers the board is dry-run to its end here, plus the RF drive stage of
test_npie_rf_instruments, and the simulated bench must be off.
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
from npielib import runner  # noqa: E402
from test_npie_limit_trace import FIX, _fallbacks  # noqa: E402
from test_npie_rated_input import ALL, _first_power_over_rating, _no_input  # noqa: E402
from test_npie_rf_instruments import _rf_stage  # noqa: E402


def _no_range(ws: Path) -> None:
    """No operating range in requirements.md, so no limits stage and the
    procedure's last supply step (power-up's) leaves the output on."""
    (ws / "requirements.md").write_text("# mini\n")


# Left out, as no dry run of theirs passes: first_power_over_rating and
# no_input never power the board, and fail at the flash step on an unpowered
# sim board; fallbacks fails a power-stage scope step on the sim board.
POWERED = [v for v in ALL if v not in (_first_power_over_rating, _no_input, _fallbacks)]
POWERED.append(_no_range)


def _ws(tmp_path: Path, variant) -> Path:
    ws = tmp_path / "mini-bldc"
    shutil.copytree(FIX, ws)
    variant(ws)
    assert procedure_gen.main(["--workspace", str(ws)]) == 0
    return ws


def _run_to_end(ws: Path) -> runner.Run:
    run = runner.create(ws, bench="sim", bench_cfg=None, label=None, faults=[],
                        hold=False, seed=1)
    runner.advance(run)
    assert run.rec["status"] == "passed", (run.rec.get("error"), run.rec["steps"][-1:])
    return run


def test_a_procedure_can_end_with_the_supply_on(tmp_path, capsys):
    """The case the runner has to cover: no operating range, no limits
    stage, so the procedure's last supply step leaves the output on."""
    proc = json.loads((_ws(tmp_path, _no_range) / "bringup" / "procedure.json").read_text())
    assert "limits" not in [st["id"] for st in proc["stages"]]
    last = [s for st in proc["stages"] for s in st["steps"] if s["type"] == "supply"][-1]
    assert last["set"]["output"] is True


@pytest.mark.parametrize("variant", POWERED, ids=[v.__name__.strip("_") for v in POWERED])
def test_a_passed_run_leaves_the_supply_off(tmp_path, capsys, variant):
    run = _run_to_end(_ws(tmp_path, variant))
    assert not run.bench.sim.on
    assert run.rec["safe_state"].startswith("supply outputs off"), run.rec["safe_state"]


def test_a_passed_run_turns_off_the_sources_it_drove(tmp_path, capsys):
    ws = _ws(tmp_path, lambda ws: None)
    _rf_stage(ws, {"f_hz": 13.56e6, "duty": 0.5, "phase_deg": {"4": 180}, "enable": True})
    run = _run_to_end(ws)
    board = run.bench.sim
    by = {r["id"]: r for r in run.rec["steps"]}
    assert by["rf-drive.04"]["applied"]["output"] is True    # the run did drive them
    assert not board.on
    assert not board.pwm_gw.enable
    assert [board.awg_model.ch[c]["out"] for c in (1, 2)] == ["OFF", "OFF"]

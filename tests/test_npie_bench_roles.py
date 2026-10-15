"""A live run starts only on a bench that binds each of its roles to that
role's driver (GOALS.md, Every role is bound to its own driver).

Bench.role took any LIVE driver for any role, and nothing read bench.yaml
before a step needed it. So a bench.yaml that left out the scope, or bound
it to scpi-dmm (whose measure takes two arguments, not the scope's four),
let a run power the board and then abort at its first scope step. Now each
live driver names the role it speaks (instruments.SERVES), its methods take
what its sim twin's do, Bench.role refuses a driver bound to another role,
and runner.create refuses a live bench that can't drive the procedure, with
no run made and nothing opened.

Nothing real is opened: NPIE_BENCH_HOST is unset, so any door would refuse.
"""
from __future__ import annotations

import inspect
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NPIE = ROOT / "skill" / "scripts"
if str(NPIE) not in sys.path:
    sys.path.insert(0, str(NPIE))

import procedure_gen  # noqa: E402
from npielib import instruments, runner  # noqa: E402
from test_npie_live_drivers import EXAMPLE_ROLES, FIX  # noqa: E402

# what the runner tells only a simulated board (a person's step it can't see)
SIM_ONLY = {"human_done"}
# sim factories that build the live driver class over a model transport
SHARED = {"pwm": instruments.Pwm8, "awg": instruments.ScpiSdg}


def _ws(tmp_path: Path) -> Path:
    ws = tmp_path / "mini-bldc"
    shutil.copytree(FIX, ws)
    assert procedure_gen.main(["--workspace", str(ws)]) == 0
    return ws


def _create(ws: Path, roles: dict):
    return runner.create(ws, bench="t", bench_cfg={"bench": "t", "roles": roles},
                         label=None, faults=None, hold=False)


@pytest.fixture
def off_host(monkeypatch):
    monkeypatch.delenv("NPIE_BENCH_HOST", raising=False)


def test_each_role_has_one_live_driver_and_each_driver_one_role():
    assert set(instruments.SERVES) == set(instruments.LIVE)
    assert sorted(instruments.SERVES.values()) == sorted(instruments.SIM)
    assert {r: c["driver"] for r, c in EXAMPLE_ROLES.items()} == \
        {r: d for d, r in instruments.SERVES.items()}


@pytest.mark.parametrize("driver", sorted(instruments.SERVES))
def test_a_live_driver_takes_its_sim_twin_s_calls(driver):
    role = instruments.SERVES[driver]
    sim = SHARED.get(role) or instruments.SIM[role]
    live = instruments.LIVE[driver]
    live = SHARED.get(role) or live      # fwe_pwm8 is a factory for Pwm8
    for name, fn in inspect.getmembers(sim, inspect.isfunction):
        if name.startswith("_") or name in SIM_ONLY:
            continue
        assert hasattr(live, name), f"{driver} has no {name}(), which the {role} sim has"
        want = list(inspect.signature(fn).parameters)
        got = inspect.signature(getattr(live, name)).parameters
        assert set(want) <= set(got), f"{driver}.{name} takes {list(got)}, sim {want}"


def test_bench_role_refuses_a_driver_bound_to_another_role(off_host):
    bench = instruments.Bench({"roles": {"scope": EXAMPLE_ROLES["dmm"]}})
    with pytest.raises(instruments.BenchError, match="scope: driver scpi-dmm is a dmm driver"):
        bench.role("scope")
    assert not bench._held       # refused before it held or opened anything


@pytest.mark.parametrize("roles, why", [
    ({k: v for k, v in EXAMPLE_ROLES.items() if k != "scope"}, "scope is not bound"),
    ({**EXAMPLE_ROLES, "scope": EXAMPLE_ROLES["dmm"]}, "scope: driver scpi-dmm is a dmm driver"),
    ({**EXAMPLE_ROLES, "dmm": {"driver": "keithley"}}, "dmm: unknown driver 'keithley'"),
    ({**EXAMPLE_ROLES, "psu": "scpi-psu"}, "psu: unknown driver None"),
    ({}, "dmm is not bound; "),
])
def test_a_live_run_is_refused_a_bench_that_cannot_drive_it(tmp_path, off_host, roles, why):
    ws = _ws(tmp_path)
    with pytest.raises(runner.RunError, match="bench.yaml can't drive this procedure") as e:
        _create(ws, roles)
    assert why in str(e.value)
    assert not (ws / "bringup" / "runs").exists()


def test_the_bench_example_starts_a_live_run(tmp_path, off_host):
    run = _create(_ws(tmp_path), dict(EXAMPLE_ROLES))
    assert run.rec["status"] == "running" and run.bench is None


def test_a_role_the_procedure_does_not_use_need_not_be_bound(tmp_path, off_host):
    ws = _ws(tmp_path)
    used = {s["role"] for s in _create(ws, dict(EXAMPLE_ROLES)).steps
            if s.get("role") and s["type"] != "human"}
    assert used < set(EXAMPLE_ROLES), "mini-bldc uses every role; pick a smaller fixture"
    _create(ws, {r: EXAMPLE_ROLES[r] for r in used})


def test_a_dry_run_needs_no_bench(tmp_path):
    run = runner.create(_ws(tmp_path), bench="sim", bench_cfg=None, label=None,
                        faults=[], hold=False)
    assert run.rec["status"] == "running"

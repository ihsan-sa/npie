"""npie runner against the simulated bench (reference/design.md section 3).

Each case copies the mini-bldc fixture, generates its procedure and runs
npie_run.py as a subprocess, so the script contract (JSON out, exit 0/1/2) is
what is under test. Nothing here opens an instrument.
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


def _ws(tmp_path) -> Path:
    ws = tmp_path / "mini-bldc"
    shutil.copytree(FIX, ws)
    r = subprocess.run([sys.executable, str(NPIE / "procedure_gen.py"), "--workspace", str(ws)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return ws


def _run(*args, env=None):
    e = {k: v for k, v in os.environ.items() if k != "NPIE_BENCH_HOST"}
    e.update(env or {})
    r = subprocess.run([sys.executable, str(NPIE / "npie_run.py"), *args],
                       capture_output=True, text=True, env=e)
    return r.returncode, json.loads(r.stdout)


def _record(out) -> dict:
    return json.loads((Path(out["run"]) / "run.json").read_text())


def test_dry_run_passes_and_records_every_step(tmp_path):
    ws = _ws(tmp_path)
    rc, out = _run("start", "--workspace", str(ws), "--dry-run")
    assert rc == 0 and out["status"] == "passed", out
    rec = _record(out)
    proc = json.loads((Path(out["run"]) / "procedure.json").read_text())
    ids = [s["id"] for st in proc["stages"] for s in st["steps"]]
    assert [r["id"] for r in rec["steps"]] == ids
    assert all(r["verdict"] == "pass" for r in rec["steps"])
    humans = [r for r in rec["steps"] if r["type"] == "human"]
    assert humans and all(r["confirmed_by"] == "sim" and r["confirmed_at"] for r in humans)
    # the ripple step took a screenshot; the finished sim run leaves no pickle behind
    caps = [c for r in rec["steps"] for c in r.get("captures", [])]
    assert caps and all((Path(out["run"]) / c).stat().st_size > 0 for c in caps)
    assert not (Path(out["run"]) / "sim-board.pickle").exists()
    assert Path(out["run"]).parent == ws / "bringup" / "runs"


@pytest.mark.parametrize("fault,stops_at", [
    ("short:/VM_IN", "unpowered.03"),
    ("short:+5V", "unpowered.05"),
    ("rail:+3V3=3.9", "rails.04"),
    ("no-flash", "programming.02"),
    ("no-banner", "programming.04"),
])
def test_fault_stops_at_its_step_with_supply_off(tmp_path, fault, stops_at):
    rc, out = _run("start", "--workspace", str(_ws(tmp_path)), "--dry-run", "--fault", fault)
    assert rc == 1 and out["status"] == "failed", out
    assert out["cursor"] == stops_at and out["failed"]["id"] == stops_at
    assert out["safe_state"].startswith("supply outputs off")
    rec = _record(out)
    # nothing after the failed step ran
    assert rec["steps"][-1]["id"] == stops_at
    assert [r for r in rec["steps"] if r["verdict"] == "fail"] == [rec["steps"][-1]]


def test_unknown_fault_is_refused(tmp_path):
    rc, out = _run("start", "--workspace", str(_ws(tmp_path)), "--dry-run",
                   "--fault", "short:/NOSUCH")
    assert rc == 2 and "names a net no step probes" in out["error"]


def test_hold_waits_for_a_person_and_records_who(tmp_path):
    ws = _ws(tmp_path)
    rc, out = _run("start", "--workspace", str(ws), "--dry-run", "--hold")
    assert rc == 1 and out["status"] == "awaiting_human" and out["awaiting"]["step"] == "visual.01"
    run = out["run"]
    assert (Path(run) / "sim-board.pickle").exists()
    # only the step the run waits on can be confirmed
    rc, bad = _run("confirm", "--run", run, "--step", "visual.02", "--by", "ihsan")
    assert rc == 2 and "waiting on visual.01" in bad["error"]
    # walk every human step; a value step gets a typed reading
    for _ in range(100):
        a = out["awaiting"]
        extra = ["--value", "green on"] if a["value"] else []
        rc, c = _run("confirm", "--run", run, "--step", a["step"], "--by", "ihsan", *extra)
        assert rc == 1 and c["status"] == "awaiting_human", c
        rc, out = _run("resume", "--run", run)
        if out["status"] != "awaiting_human":
            break
    assert rc == 0 and out["status"] == "passed", out
    rec = _record(out)
    humans = [r for r in rec["steps"] if r["type"] == "human"]
    assert humans and {r["confirmed_by"] for r in humans} == {"ihsan"}
    assert any(r.get("value") == "green on" for r in humans)
    # the sim board kept its state between calls: flashed once, banner seen after reset
    assert next(r for r in rec["steps"] if r["id"] == "blocks.06")["verdict"] == "pass"
    assert not (Path(run) / "sim-board.pickle").exists()


def test_confirm_fail_ends_the_run_with_supply_off(tmp_path):
    rc, out = _run("start", "--workspace", str(_ws(tmp_path)), "--dry-run", "--hold")
    run = out["run"]
    while out["awaiting"]["step"] != "power-up.07":     # the touch test, supply on
        a = out["awaiting"]
        _run("confirm", "--run", run, "--step", a["step"], "--by", "ihsan")
        rc, out = _run("resume", "--run", run)
    rc, out = _run("confirm", "--run", run, "--step", "power-up.07", "--by", "ihsan",
                   "--fail", "U1 hot")
    assert rc == 1 and out["status"] == "failed"
    assert out["safe_state"].startswith("supply outputs off")
    r = _record(out)["steps"][-1]
    assert (r["id"], r["verdict"], r["note"], r["confirmed_by"]) == \
        ("power-up.07", "fail", "U1 hot", "ihsan")
    rc, again = _run("resume", "--run", run)
    assert rc == 2 and "run is failed" in again["error"]


def test_stale_procedure_is_refused(tmp_path):
    ws = _ws(tmp_path)
    cons = ws / "kicad" / "constraints.json"
    cons.write_text(cons.read_text() + "\n")
    rc, out = _run("start", "--workspace", str(ws), "--dry-run")
    assert rc == 2 and "stale" in out["error"] and "constraints" in out["error"]
    # a new input (an override) makes it stale too
    ws2 = _ws(tmp_path / "b")
    (ws2 / "bringup" / "overrides.yaml").write_text("rails.02: {max: 5.2}\n")
    rc, out = _run("start", "--workspace", str(ws2), "--dry-run")
    assert rc == 2 and "overrides" in out["error"]


def test_edited_snapshot_is_refused(tmp_path):
    rc, out = _run("start", "--workspace", str(_ws(tmp_path)), "--dry-run", "--hold")
    snap = Path(out["run"]) / "procedure.json"
    snap.write_text(snap.read_text().replace('"max": 5.25', '"max": 9'))
    rc, out = _run("resume", "--run", out["run"])
    assert rc == 2 and "changed after the run started" in out["error"]


def test_live_bench_needs_the_bench_host_flag(tmp_path):
    ws = _ws(tmp_path)
    bench = tmp_path / "bench.yaml"
    bench.write_text("bench: t\nroles: {}\n")
    rc, out = _run("start", "--workspace", str(ws), "--bench", str(bench))
    assert rc == 2 and "NPIE_BENCH_HOST" in out["error"]
    assert not (ws / "bringup" / "runs").exists()
    # with the flag a live run stops at human steps like --hold, and a bench
    # that binds no DMM aborts at the first measurement, supply turned off
    host = {"NPIE_BENCH_HOST": "1"}
    rc, out = _run("start", "--workspace", str(ws), "--bench", str(bench), env=host)
    assert rc == 1 and out["status"] == "awaiting_human" and out["bench"] == "t"
    run = out["run"]
    while out["status"] == "awaiting_human":
        step = out["awaiting"]["step"]
        rc, refused = _run("confirm", "--run", run, "--step", step, "--by", "ihsan")
        assert rc == 2 and "NPIE_BENCH_HOST" in refused["error"]
        _run("confirm", "--run", run, "--step", step, "--by", "ihsan", env=host)
        rc, refused = _run("resume", "--run", run)
        assert rc == 2 and "NPIE_BENCH_HOST" in refused["error"]
        rc, out = _run("resume", "--run", run, env=host)
    assert rc == 2 and out["status"] == "aborted" and "role 'dmm'" in out["error"]
    assert out["cursor"] == "unpowered.03"
    assert out["safe_state"].startswith("no supply reachable")


def test_fault_flags_need_dry_run(tmp_path):
    bench = tmp_path / "bench.yaml"
    bench.write_text("bench: t\n")
    rc, out = _run("start", "--workspace", str(_ws(tmp_path)), "--bench", str(bench),
                   "--fault", "no-flash", env={"NPIE_BENCH_HOST": "1"})
    assert rc == 2 and "--dry-run only" in out["error"]


def test_console_fields_are_checked_against_limits():
    sys.path.insert(0, str(NPIE))
    from npielib import runner
    lim = {"vbus_v": {"min": 9.5, "max": 10.5}}
    assert runner._check_fields('OK {"vbus_v": 10.1}', lim) == (True, {"vbus_v": 10.1})
    assert runner._check_fields('OK {"vbus_v": 12.0}', lim)[0] is False
    assert runner._check_fields('OK {"armed": true}', lim) == (False, {"vbus_v": None})
    assert runner._check_fields("OK not-json", lim)[0] is False


def test_sim_console_answers_a_hook_with_its_expected_reply(tmp_path):
    # a hook that expects more than "OK " (PCB-0018's version hook) still passes
    ws = tmp_path / "mini-bldc"
    shutil.copytree(FIX, ws)
    mf = ws / "firmware" / "fwe-manifest.json"
    m = json.loads(mf.read_text())
    m["test_hooks"].append({"name": "version", "send": "version", "timeout_s": 5,
                            "expect": '^OK \\{"board":"MINI-1"', "needs": []})
    mf.write_text(json.dumps(m))
    r = subprocess.run([sys.executable, str(NPIE / "procedure_gen.py"), "--workspace", str(ws)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout
    rc, out = _run("start", "--workspace", str(ws), "--dry-run")
    assert rc == 0 and out["status"] == "passed", out
    got = [s for s in _record(out)["steps"] if s.get("value", "") and "MINI-1" in str(s["value"])]
    assert got and got[0]["value"].startswith('OK {"board":"MINI-1"')


def test_out_of_limit_value_turns_the_supply_off(tmp_path):
    # the generator writes no human step with numeric limits yet, so this case gives
    # the first held step a value + expect of its own (confirm reads run.steps)
    sys.path.insert(0, str(NPIE))
    from npielib import runner
    run = runner.create(_ws(tmp_path), bench="sim", bench_cfg=None, label=None, faults=[],
                        hold=True, seed=1)
    run.open_bench()
    runner.advance(run)
    assert run.rec["status"] == "awaiting_human"
    s = run.steps[run.index(run.rec["cursor"])]
    s.update(value={"unit": "V"}, expect={"min": 4.9, "max": 5.1, "unit": "V"})
    runner.confirm(run, s["id"], "ihsan", "12", None)
    assert run.rec["status"] == "failed" and runner.exit_code(run) == 1
    assert run.rec["steps"][-1]["verdict"] == "fail" and run.rec["steps"][-1]["value"] == 12.0
    # no --fail given, yet the supply still goes off (the sim bench is reachable)
    assert run.rec["safe_state"].startswith("supply outputs off")


def test_in_limit_value_passes_and_keeps_waiting_for_resume(tmp_path):
    sys.path.insert(0, str(NPIE))
    from npielib import runner
    run = runner.create(_ws(tmp_path), bench="sim", bench_cfg=None, label=None, faults=[],
                        hold=True, seed=1)
    run.open_bench()
    runner.advance(run)
    s = run.steps[run.index(run.rec["cursor"])]
    s.update(value={"unit": "V"}, expect={"min": 4.9, "max": 5.1, "unit": "V"})
    runner.confirm(run, s["id"], "ihsan", "5.0", None)
    assert run.rec["status"] == "awaiting_human" and run.rec["steps"][-1]["verdict"] == "pass"
    assert not run.rec.get("safe_state")


def test_a_driver_exception_aborts_with_the_supply_off(tmp_path, monkeypatch):
    sys.path.insert(0, str(NPIE))
    from npielib import instruments, runner
    ws = _ws(tmp_path)

    def boom(self, *a, **k):
        raise ValueError("could not convert string to float: 'ERR'")
    monkeypatch.setattr(instruments.SimDmm, "measure", boom)
    run = runner.create(ws, bench="sim", bench_cfg=None, label=None, faults=[],
                        hold=False, seed=1)
    run.open_bench()
    runner.advance(run)
    assert run.rec["status"] == "aborted" and runner.exit_code(run) == 2
    assert "ValueError" in run.rec["error"]
    assert run.rec["safe_state"].startswith("supply outputs off")
    assert json.loads((run.dir / "run.json").read_text())["status"] == "aborted"

"""Without NPIE_BENCH_HOST=1, no real instrument driver is ever opened
(GOALS.md, Every hardware door checks the bench host).

Bench.role refuses a live role, but that alone leaves a driver built any
other way (a test, a new script, #19's ESP32 driver) free to open VISA, a
serial port or a subprocess. So each door to hardware calls
instruments._bench_host before it opens anything: these tests build every
LIVE driver directly from its bench.example line with every transport rigged
to fail the test, and read every script's source for a transport call that
isn't behind the check.
"""
from __future__ import annotations

import ast
import sys
import types

import pytest
import yaml

from test_npie_run import NPIE

sys.path.insert(0, str(NPIE))
import procedure_gen  # noqa: E402
from npielib import instruments  # noqa: E402
from npielib.instruments import Bench, BenchError  # noqa: E402

# (script, function) whose transport call opens no hardware
NOT_HARDWARE = {("npie_report.py", "compile_pdf")}     # lualatex


def _touched(*a, **k):
    raise AssertionError("a transport was opened without NPIE_BENCH_HOST=1")


@pytest.fixture
def rigged(monkeypatch):
    monkeypatch.delenv("NPIE_BENCH_HOST", raising=False)
    monkeypatch.setitem(sys.modules, "pyvisa", types.SimpleNamespace(ResourceManager=_touched))
    monkeypatch.setitem(sys.modules, "serial", types.SimpleNamespace(Serial=_touched))
    monkeypatch.setattr(instruments, "subprocess",
                        types.SimpleNamespace(run=_touched, Popen=_touched))
    monkeypatch.setattr(instruments, "shutil", types.SimpleNamespace(which=_touched))


def _example(driver: str) -> dict:
    for line in procedure_gen.BENCH_EXAMPLE.values():
        cfg = yaml.safe_load(line)
        if cfg["driver"] == driver:
            return cfg
    raise AssertionError(f"BENCH_EXAMPLE has no line for the {driver} driver")


@pytest.mark.parametrize("driver", sorted(instruments.LIVE))
def test_live_driver_refused_without_bench_host(driver, rigged):
    bench = Bench({"roles": {"x": _example(driver)}})
    with pytest.raises(BenchError, match="NPIE_BENCH_HOST"):
        bench.role("x")


@pytest.mark.parametrize("driver", sorted(instruments.LIVE))
def test_live_driver_built_directly_refused(driver, rigged):
    with pytest.raises(BenchError, match="NPIE_BENCH_HOST"):
        instruments.LIVE[driver](_example(driver))


def test_door_methods_refused_without_bench_host(rigged, tmp_path):
    """A driver that got built anyway (here, by skipping __init__) still
    can't run its subprocess."""
    logic = object.__new__(instruments.SigrokLogic)
    logic.dev = "fx2lafw"
    with pytest.raises(BenchError, match="NPIE_BENCH_HOST"):
        logic.capture(["D0"], 1e6, 0.1)
    probe = object.__new__(instruments.SwdProbe)
    probe.tool = "probe-rs"
    with pytest.raises(BenchError, match="NPIE_BENCH_HOST"):
        probe.flash({"flash": {"commands": {"probe-rs": ["probe-rs", "{elf}"]}},
                     "artifact": {"elf": "a.elf"}}, tmp_path)


def test_live_driver_allowed_on_bench_host(monkeypatch):
    monkeypatch.setenv("NPIE_BENCH_HOST", "1")
    monkeypatch.setitem(instruments.LIVE, "serial", lambda cfg: "opened")
    assert Bench({"roles": {"x": {"driver": "serial"}}}).role("x").upper() == "OPENED"


def _is_door(call: ast.Call) -> bool:
    f = call.func
    if not isinstance(f, ast.Attribute):
        return False
    if f.attr in ("open_resource", "Serial"):
        return True
    return f.attr in ("run", "Popen") and isinstance(f.value, ast.Name) \
        and f.value.id == "subprocess"


def _doors(tree: ast.AST):
    """(function name or None, door call, the lines _bench_host is called on
    in that function), for every door call, by its innermost function."""
    out = []

    def visit(node, fn, guards):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                g = [c.lineno for c in ast.walk(child) if isinstance(c, ast.Call)
                     and isinstance(c.func, ast.Name) and c.func.id == "_bench_host"]
                visit(child, child.name, g)
                continue
            if isinstance(child, ast.Call) and _is_door(child):
                out.append((fn, child, guards))
            visit(child, fn, guards)

    visit(tree, None, [])
    return out


def test_every_door_calls_bench_host_first():
    found, unguarded = set(), []
    for py in sorted(NPIE.rglob("*.py")):
        rel = py.relative_to(NPIE).as_posix()
        for fn, call, guards in _doors(ast.parse(py.read_text(), str(py))):
            if (rel, fn) in NOT_HARDWARE:
                found.add((rel, fn))
            elif not any(g < call.lineno for g in guards):
                unguarded.append(f"{rel}:{call.lineno} in {fn}: {ast.unparse(call.func)}")
    assert not unguarded, "transport opened without _bench_host first:\n" + "\n".join(unguarded)
    assert found == NOT_HARDWARE, f"stale NOT_HARDWARE entries: {NOT_HARDWARE - found}"

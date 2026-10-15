"""Every number in a limit is named (GOALS.md, Every limit field names its
source): no procgen constant moves a limit without the limit saying so.

test_npie_limit_trace checks per step that `derived_from` points into a design
input, which a step passes on the strength of one field (say its `max`) while
another field (its `min`) comes from a procgen constant nobody sees. This
check works per field: it nudges each numeric procgen constant in turn,
regenerates every fixture variant, and any step whose `expect` or `fields`
moved must have its `derived_from` move with it, so the constant's value is
written where a reviewer reads the limit. UNDISCLOSED lists the
(constant, step text) pairs that don't yet, and is to be emptied.

A constant no variant's limit moves with passes that unseen (GOALS.md, Every
limit constant is reached by a fixture), so each must move some limit,
unless it sets no limit at all (NOT_A_LIMIT) or is listed in UNREACHED,
which is to be emptied.

Only module constants are nudged, so a number written inline in a limit (say
`round(v * 0.95, 3)`) would slip past. test_no_inline_number_in_a_limit reads
procgen's source and fails on any number other than 0 and 1 (and round()'s
digits) inside a dict literal with a limit key, so every such number is a
constant the nudge reaches.
"""
from __future__ import annotations

import ast
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NPIE = ROOT / "skill" / "scripts"
if str(NPIE) not in sys.path:
    sys.path.insert(0, str(NPIE))

from npielib import design, instruments, procgen  # noqa: E402
from test_npie_limit_trace import (FIX, _boost, _bringup, _evn_header,  # noqa: E402
                                   _hall, _mini_bldc, _no_manifest, _rails, _spin)


def _defaults(ws: Path) -> None:
    """No constraints bringup{}, so the short floors and the spin current fall
    back to procgen's constants."""
    _spin(ws)
    _bringup(ws, None)


VARIANTS = [_mini_bldc, _no_manifest, _evn_header, _spin, _boost, _hall, _defaults,
            _rails]
NUDGE = 1.37
# (constant, step text) where the constant moves the limit and derived_from
# doesn't say so
UNDISCLOSED: set[tuple[str, str]] = set()
# constants that shape no expect or fields (a console duty, a wait, a person's
# instructions); the check holds them to moving no limit
NOT_A_LIMIT = {"OVP_OVERSHOOT", "INJECT_R_OHM", "INJECT_ILIM_A", "TRIP_SETTLE_S"}
# limit constants no fixture variant reaches yet, so nothing shows the limit
# names them; to be emptied by variants that reach them
UNREACHED = {"BOOST_IDLE_DROP_V"}


def _constants() -> list[str]:
    def numeric(v):
        if isinstance(v, tuple):
            return bool(v) and all(numeric(x) for x in v)
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    return sorted(k for k, v in vars(procgen).items()
                  if k.isupper() and numeric(v) and not hasattr(instruments, k))


def _nudged(v):
    if isinstance(v, tuple):
        return tuple(_nudged(x) for x in v)
    if isinstance(v, int):
        return max(v + 1, round(v * NUDGE))
    return v * NUDGE


def _limits(ws: Path) -> dict[tuple[str, str], tuple]:
    proc = procgen.generate(design.load(ws))
    return {(s["id"], s.get("text") or ""): (s.get("expect"), s.get("fields"),
                                              s.get("derived_from"))
            for st in proc["stages"] for s in st["steps"]
            if s.get("expect") or s.get("fields")}


@pytest.fixture(scope="module")
def workspaces(tmp_path_factory):
    out = []
    for v in VARIANTS:
        ws = tmp_path_factory.mktemp(v.__name__.strip("_")) / "mini-bldc"
        shutil.copytree(FIX, ws)
        v(ws)
        out.append(ws)
    return out


def test_the_nudge_finds_the_constants():
    names = _constants()
    assert {"IDLE_FLOOR_A", "TOL_REG", "DIODE_V", "DUTY_TOL", "INJECT_R_OHM",
            "ILIM_CEIL_FRAC", "FOLLOW_TOL", "HALL_EDGES_MIN"} <= set(names)
    assert "PWM8_ID" not in names and "SCHEMA" not in names


LIMIT_KEYS = {"nominal", "min", "max", "edges_min"}


def _inline_numbers(src: str) -> list[str]:
    """Numbers other than 0 and 1 written inside a limit dict literal."""
    found = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Dict) or not LIMIT_KEYS & {
                k.value for k in node.keys if isinstance(k, ast.Constant)}:
            continue
        for v in node.values:
            digits = {id(c.args[1]) for c in ast.walk(v) if isinstance(c, ast.Call)
                      and getattr(c.func, "id", None) == "round" and len(c.args) > 1}
            for c in ast.walk(v):
                if (isinstance(c, ast.Constant) and type(c.value) in (int, float)
                        and c.value not in (0, 1) and id(c) not in digits):
                    found.append(f"line {c.lineno}: {ast.unparse(v)}")
    return found


def test_the_scan_finds_an_inline_number():
    assert _inline_numbers('e = {"min": round(v * 0.95, 3), "unit": "V"}')
    assert _inline_numbers('s(expect={"edges_min": 2})')
    assert not _inline_numbers('e = {"min": round(v * (1 - TOL), 3), "max": 0}')


def test_no_inline_number_in_a_limit():
    found = _inline_numbers(Path(procgen.__file__).read_text())
    assert not found, ("numbers written inline in a procgen limit, which the nudge "
                       "can't reach; make each a module constant and name it in "
                       "derived_from:\n" + "\n".join(found))


def test_every_constant_that_moves_a_limit_is_named(workspaces, monkeypatch):
    base = [_limits(ws) for ws in workspaces]
    found: set[tuple[str, str]] = set()
    moved_any = set()
    for name in _constants():
        with monkeypatch.context() as m:
            m.setattr(procgen, name, _nudged(getattr(procgen, name)))
            for ws, before in zip(workspaces, base):
                after = _limits(ws)
                for key in before.keys() & after.keys():
                    (e0, f0, d0), (e1, f1, d1) = before[key], after[key]
                    if (e0, f0) != (e1, f1):
                        moved_any.add(name)
                        if d0 == d1:
                            found.add((name, key[1]))
    assert moved_any, "the nudge moved no limit: the check is checking nothing"
    unreached = sorted(set(_constants()) - moved_any - UNREACHED - NOT_A_LIMIT)
    assert not unreached, ("constants no fixture variant's limit moves with, so "
                           "nothing shows they are named:\n" + "\n".join(unreached))
    reached = sorted(moved_any & (UNREACHED | NOT_A_LIMIT))
    assert not reached, ("UNREACHED or NOT_A_LIMIT entries that now move a limit, "
                         "drop them:\n" + "\n".join(reached))
    new = sorted(found - UNDISCLOSED)
    stale = sorted(UNDISCLOSED - found)
    assert not new, ("constants that move a limit its derived_from doesn't name:\n"
                     + "\n".join(f"{c}: {t!r}" for c, t in new))
    assert not stale, ("UNDISCLOSED entries now named, drop them:\n"
                       + "\n".join(f"{c}: {t!r}" for c, t in stale))

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

Only module constants are nudged: a number written inline in procgen (the
90 % of a current limit, the -5 %/+1 % of a rail following the input) is not
seen here until it becomes a constant.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NPIE = ROOT / "skill" / "scripts"
if str(NPIE) not in sys.path:
    sys.path.insert(0, str(NPIE))

from npielib import design, instruments, procgen  # noqa: E402
from test_npie_limit_trace import (FIX, _boost, _evn_header, _mini_bldc,  # noqa: E402
                                   _no_manifest, _spin)

VARIANTS = [_mini_bldc, _no_manifest, _evn_header, _spin, _boost]
NUDGE = 1.37
# (constant, step text) where the constant moves the limit and derived_from
# doesn't say so
UNDISCLOSED: set[tuple[str, str]] = set()


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
    assert {"IDLE_FLOOR_A", "TOL_REG", "DIODE_V", "DUTY_TOL", "INJECT_R_OHM"} <= set(names)
    assert "PWM8_ID" not in names and "SCHEMA" not in names


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
    new = sorted(found - UNDISCLOSED)
    stale = sorted(UNDISCLOSED - found)
    assert not new, ("constants that move a limit its derived_from doesn't name:\n"
                     + "\n".join(f"{c}: {t!r}" for c, t in new))
    assert not stale, ("UNDISCLOSED entries now named, drop them:\n"
                       + "\n".join(f"{c}: {t!r}" for c, t in stale))

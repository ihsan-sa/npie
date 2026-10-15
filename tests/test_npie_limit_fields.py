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
unless it sets no limit at all (NOT_A_LIMIT, held to moving none). UNREACHED
lists any a new constant leaves without a variant, and is to stay empty.

Only module constants are nudged, so a number written inline (say
`round(v * 0.95, 3)` in a limit, or a `.get("vio_v", 3.3)` fallback feeding
one) would slip past (GOALS.md, No number hides anywhere in procgen's limit
math). test_no_loose_number_in_procgen reads procgen's source and fails on
any number other than 0 and 1 outside its module constants, so every such
number is a constant the nudge reaches. It passes round()'s digits, f-string
formatting, subscripts, a step's timing and channel keywords, and the
arithmetic in ARITHMETIC.
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
from test_npie_limit_trace import (FIX, _boost, _boost_low, _boost_out, _bringup,  # noqa: E402
                                   _evn_header, _fallbacks, _hall, _mini_bldc,
                                   _no_manifest, _rails, _spin)


def _defaults(ws: Path) -> None:
    """No constraints bringup{}, so the short floors and the spin current fall
    back to procgen's constants."""
    _spin(ws)
    _bringup(ws, None)


VARIANTS = [_mini_bldc, _no_manifest, _evn_header, _spin, _boost, _hall, _defaults,
            _rails, _boost_out, _boost_low, _fallbacks]
NUDGE = 1.37
# (constant, step text) where the constant moves the limit and derived_from
# doesn't say so
UNDISCLOSED: set[tuple[str, str]] = set()
# constants that shape no expect or fields (a console duty, a wait, a person's
# instructions, a supply setting); the check holds them to moving no limit
NOT_A_LIMIT = {"OVP_OVERSHOOT", "INJECT_R_OHM", "INJECT_ILIM_A", "TRIP_SETTLE_S",
               "OV_AIM_RATING_FRAC", "VDDA_DEFAULT_V", "SPIN_V_DEFAULT", "UV_UNDERSHOOT_V"}
# limit constants no fixture variant reaches yet, so nothing shows the limit
# names them; add a variant that reaches one rather than listing it
UNREACHED: set[str] = set()


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


# a step's timing and wiring, not a pass/fail limit
STEP_KW = {"timeout_s", "seconds", "channel", "channel2", "samplerate", "duration_s"}
# arithmetic, not a bench choice, by the expression the number sits in: a
# two-pin part, the mean of two references
ARITHMETIC = {"len(self.d.pins_of(n['ref'])) == 2", "sum(vref) / 2"}


def _loose_numbers(src: str) -> list[str]:
    """Numbers other than 0 and 1 outside the module's constants."""
    tree = ast.parse(src)
    parent, passed = {}, set()
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[id(child)] = node
        if isinstance(node, ast.Assign) and node in tree.body or isinstance(node, ast.JoinedStr):
            passed.add(id(node))
        elif isinstance(node, ast.Subscript):
            passed.add(id(node.slice))
        elif isinstance(node, ast.keyword) and node.arg in STEP_KW:
            passed.add(id(node.value))
        elif (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "round"
              and len(node.args) > 1):
            passed.add(id(node.args[1]))
    found = []
    for node in ast.walk(tree):
        if (not isinstance(node, ast.Constant) or type(node.value) not in (int, float)
                or node.value in (0, 1)):
            continue
        up, at = node, ast.unparse(parent[id(node)])
        while id(up) in parent and id(up) not in passed:
            up = parent[id(up)]
        if id(up) not in passed and at not in ARITHMETIC:
            found.append(f"line {node.lineno}: {at}")
    return found


def test_the_scan_finds_a_loose_number():
    assert _loose_numbers('def f(v):\n    return {"min": round(v * 0.95, 3), "unit": "V"}')
    assert _loose_numbers('def f(v):\n    return min(v + 2.0, 9)')
    assert _loose_numbers('def f(hdr):\n    return hdr.get("vio_v", 3.3)')
    assert not _loose_numbers('X = 0.5\n'
                              'def f(s, h, a, v):\n'
                              '    s.step("wait", seconds=2.0, timeout_s=h.get("t", 5))\n'
                              '    return f"{v * 100:g}", a[2], round(v * X, 3), {"max": 0}')


def test_no_loose_number_in_procgen():
    found = _loose_numbers(Path(procgen.__file__).read_text())
    assert not found, ("numbers written inline in procgen, which the nudge can't "
                       "reach; make each a module constant and name it in "
                       "derived_from:\n" + "\n".join(found))


def test_every_source_names_its_entry(workspaces):
    """`constraints power[]` names a list, not the entry a limit came from
    (GOALS.md, Every design source names its entry)."""
    bare = []
    for ws in workspaces:
        proc = procgen.generate(design.load(ws))
        for s in (s for st in proc["stages"] for s in st["steps"]):
            if "[]" in (s.get("derived_from") or ""):
                bare.append(f"{ws.parent.name} {s['id']} {s.get('text')!r}: {s['derived_from']!r}")
    assert not bare, "sources that name a list but not its entry:\n" + "\n".join(bare)


def test_the_first_current_limit_names_its_rail(workspaces):
    proc = procgen.generate(design.load(workspaces[0]))
    steps = [s for st in proc["stages"] for s in st["steps"]]
    supply = next(s for s in steps if s.get("role") == "psu" and s.get("set")
                  and s.get("derived_from"))
    idle = next(s for s in steps if s.get("text") == "idle input current")
    for s in (supply, idle):
        assert "constraints power[+5V]" in s["derived_from"], s["derived_from"]
        assert "the heaviest rail" in s["derived_from"]


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

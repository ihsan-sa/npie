"""Energising is always announced (GOALS.md, Every energising step is
announced; SKILL.md rule 3): the first power-up, any manifest command not
marked `safe: true` and the motor stage are each preceded by a human step
in their own stage.

procgen wrote `safe:false` into `derived_from` by name but never read the
manifest's flag. The limits stage switched a boost open loop at D0 for the
under-voltage trip with no human step in that stage at all, and a manifest
`test_hooks` entry ran unattended whatever command it sent. Every fixture
variant of test_npie_rated_input is checked, plus manifests whose test hook
sends an unsafe or an unlisted command.

"Preceded" is held loosely: some human step earlier in the same stage. An
unsafe test hook is held tighter: the step right before it is a human step
naming what it sends.
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

from npielib import design, procgen  # noqa: E402
from test_npie_limit_trace import FIX, _boost, _mini_bldc  # noqa: E402
from test_npie_rated_input import ALL  # noqa: E402


def _hooks(ws: Path, *hooks: dict, commands: tuple[dict, ...] = ()) -> None:
    mf = ws / "firmware" / "fwe-manifest.json"
    m = json.loads(mf.read_text())
    m["commands"].extend(commands)
    m["test_hooks"].extend(hooks)
    mf.write_text(json.dumps(m))


def _unsafe_hook(ws: Path) -> None:
    """A test hook that pulses the gate drive, a command marked safe: false."""
    _hooks(ws, {"name": "gate-pulse", "send": "pulse 10", "expect": "^OK ", "timeout_s": 5},
           commands=({"name": "pulse", "args": "us", "reply": "OK {json}", "safe": False},))


def _unlisted_hook(ws: Path) -> None:
    """A test hook whose command the manifest doesn't list, so nothing says it's safe."""
    _hooks(ws, {"name": "relay", "send": "relay on", "expect": "^OK ", "timeout_s": 5})


VARIANTS = ALL + [_unsafe_hook, _unlisted_hook]


def _generate(tmp_path: Path, variant) -> tuple[dict, dict]:
    ws = tmp_path / "mini-bldc"
    shutil.copytree(FIX, ws)
    variant(ws)
    d = design.load(ws)
    return procgen.generate(d), {c["name"]: c for c in (d.manifest or {}).get("commands", [])}


def _unsafe(step: dict, cmds: dict) -> bool:
    words = (step.get("send") or "").split()
    return bool(words) and cmds.get(words[0], {}).get("safe") is not True


def _energising(proc: dict, cmds: dict) -> list[tuple[dict, dict, str]]:
    """(stage, step, why) for every step that energises something."""
    out, on = [], False
    for st in proc["stages"]:
        for s in st["steps"]:
            if s["type"] == "console" and _unsafe(s, cmds):
                out.append((st, s, f"sends {s['send']!r}, not marked safe"))
            elif s["type"] == "supply":
                if s["set"].get("output") and not on:
                    out.append((st, s, "turns the supply on"))
                on = bool(s["set"].get("output"))
            elif st["id"] == "full-function" and s["type"] != "human":
                out.append((st, s, "in the motor stage"))
    return out


@pytest.mark.parametrize("variant", VARIANTS, ids=[v.__name__.strip("_") for v in VARIANTS])
def test_every_energising_step_is_announced(tmp_path, variant):
    proc, cmds = _generate(tmp_path, variant)
    bare = []
    for st, s, why in _energising(proc, cmds):
        before = st["steps"][:st["steps"].index(s)]
        if not any(b["type"] == "human" for b in before):
            bare.append(f"{s['id']} {why}")
    assert not bare, "energising steps with no human step before them in their stage:\n" \
        + "\n".join(bare)


def test_the_check_sees_each_kind(tmp_path):
    """The check isn't vacuous: on the boost it finds the first power-up, the
    power stage's and the limits stage's unsafe duty."""
    proc, cmds = _generate(tmp_path, _boost)
    seen = {(st["id"], why.split(",")[0]) for st, _, why in _energising(proc, cmds)}
    assert ("power-up", "turns the supply on") in seen
    assert any(sid == "power-stage" and w.startswith("sends 'duty") for sid, w in seen)
    assert any(sid == "limits" and w.startswith("sends 'duty") for sid, w in seen)


@pytest.mark.parametrize("variant", [_unsafe_hook, _unlisted_hook],
                         ids=["unsafe-hook", "unlisted-hook"])
def test_an_unsafe_test_hook_is_announced_by_what_it_sends(tmp_path, variant):
    proc, cmds = _generate(tmp_path, variant)
    blocks = next(st for st in proc["stages"] if st["id"] == "blocks")["steps"]
    hooks = [i for i, s in enumerate(blocks)
             if (s.get("derived_from") or "").startswith("manifest test_hooks[")
             and _unsafe(s, cmds)]
    assert hooks, "the hook is not in the blocks stage"
    for i in hooks:
        prev = blocks[i - 1] if i else None
        assert prev and prev["type"] == "human" and f"`{blocks[i]['send']}`" in prev["text"], \
            (blocks[i]["id"], prev)


def test_a_safe_test_hook_runs_unattended(tmp_path):
    """The fixture's selftest is marked safe: true, so no human step is added."""
    proc, _ = _generate(tmp_path, _mini_bldc)
    blocks = next(st for st in proc["stages"] if st["id"] == "blocks")["steps"]
    assert blocks[0]["type"] == "console" and blocks[0]["send"] == "selftest"


def test_the_limits_stage_says_it_switches_the_boost(tmp_path):
    proc, _ = _generate(tmp_path, _boost)
    limits = next(st for st in proc["stages"] if st["id"] == "limits")["steps"]
    duty = next(i for i, s in enumerate(limits) if (s.get("send") or "").startswith("duty "))
    said = [s["text"] for s in limits[:duty] if s["type"] == "human"]
    d0 = limits[duty]["send"].split()[1]
    assert any(f"D0 = {d0}" in t and "under-voltage" in t for t in said), said

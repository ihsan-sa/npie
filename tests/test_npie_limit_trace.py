"""Every pass/fail limit traces to the board's design files (GOALS.md, Limits
from the design): each step carrying a limit, an `expect` or a console
`fields` entry, says in `derived_from` which design input it came from.

The design inputs are the ones procedure.json lists under `inputs`: the
netlist, constraints.json, the BOM, requirements.md and the fwe manifest
(and bringup/overrides.yaml when a person overrides a limit). Fixture boards
only, so the check runs everywhere.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NPIE = ROOT / "skill" / "scripts"
FIX = ROOT / "tests" / "fixtures" / "npie" / "mini-bldc"
if str(NPIE) not in sys.path:
    sys.path.insert(0, str(NPIE))

from npielib import design, procgen  # noqa: E402

DESIGN_INPUT_RE = re.compile(r"\b(netlist|constraints|BOM|requirements|manifest)\b")
EVN_BLOCK = {"topology": "board-to-board-header", "block": "B1",
             "name": "J9 2x20 2.54 male to EVN JP8", "operating_point": {"vio_v": 3.3}}


def _mini_bldc(ws: Path) -> None:
    pass


def _no_manifest(ws: Path) -> None:
    shutil.rmtree(ws / "firmware")


def _evn_header(ws: Path) -> None:
    cp = ws / "kicad" / "constraints.json"
    c = json.loads(cp.read_text())
    c["blocks"] = [EVN_BLOCK]
    cp.write_text(json.dumps(c))


def _spin(ws: Path) -> None:
    mf = ws / "firmware" / "fwe-manifest.json"
    m = json.loads(mf.read_text())
    m["commands"].append({"name": "spin", "args": "", "reply": "OK {json}", "safe": False})
    mf.write_text(json.dumps(m))


@pytest.mark.parametrize("variant", [_mini_bldc, _no_manifest, _evn_header, _spin],
                         ids=["mini-bldc", "no-manifest", "evn-header", "spin"])
def test_every_limit_names_its_design_input(tmp_path, variant):
    ws = tmp_path / "mini-bldc"
    shutil.copytree(FIX, ws)
    variant(ws)
    proc = procgen.generate(design.load(ws))
    untraced = [f"{s['id']} {s.get('text')!r}: {s.get('derived_from')!r}"
                for st in proc["stages"] for s in st["steps"]
                if (s.get("expect") or s.get("fields"))
                and not DESIGN_INPUT_RE.search(s.get("derived_from") or "")]
    assert not untraced, "limits that name no design input:\n" + "\n".join(untraced)

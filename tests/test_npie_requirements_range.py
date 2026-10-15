"""The operating range is read however requirements.md writes it (GOALS.md,
Every way requirements write the VIN range is read).

requirements.md is prose a person writes, and design._vin_range read only
'A-B V operating' with an ASCII hyphen. An en dash ('8–24 V operating',
which editors put in for you), 'to', a unit on both numbers, 'VDC' or the
'Operating input: 8-24 V' order gave no range at all, without a word: the
first power fell back to a procgen default and the VIN corners were
skipped as "no operating range", on a board whose design gives one.
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

from npielib import design, procgen  # noqa: E402
from test_npie_limit_trace import FIX  # noqa: E402

READ = [
    "Input 8-24 V operating range.",
    "Input 8 - 24 V operating range.",
    "Input 8-24V operating range.",
    "Input 8–24 V operating range.",       # en dash
    "Input 8 — 24 V operating range.",     # em dash
    "Input 8 V-24 V operating range.",
    "Input 8 to 24 V operating range.",
    "Input 8 V to 24 V operating range.",
    "Input 8..24 V operating.",
    "Input 8-24 VDC operating range.",
    "Input 8-24 V DC operating range.",
    "Operating input range: 8–24 V.",
    "- **Operating voltage:** 8 V to 24 V",
    "VIN operating 8-24 VDC, 30 V absolute maximum.",
]

NOT_A_RANGE = [
    "Output 0-5 V analog.",                         # no 'operating'
    "Operating temperature -20 to 70 C.",           # not volts
    "Operating temperature -20 to 70 C.\nInput 8-24 V nominal.",  # never on one line
    "Operating frequency 1-2 MHz.",
]


def _req(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "requirements.md"
    p.write_text("# board\n\n" + text + "\n", encoding="utf-8")
    return p


@pytest.mark.parametrize("text", READ)
def test_each_wording_gives_the_range(tmp_path, text):
    assert design._vin_range(_req(tmp_path, text)) == (8.0, 24.0)


@pytest.mark.parametrize("text", NOT_A_RANGE)
def test_no_range_where_the_words_give_none(tmp_path, text):
    assert design._vin_range(_req(tmp_path, text)) is None


def test_the_first_range_wins(tmp_path):
    text = "Input 8–24 V operating range.\nAux 3-5 V operating."
    assert design._vin_range(_req(tmp_path, text)) == (8.0, 24.0)


def test_an_en_dash_range_reaches_the_vin_corners(tmp_path):
    ws = tmp_path / "mini-bldc"
    shutil.copytree(FIX, ws)
    (ws / "requirements.md").write_text("# mini\n\nInput 8–24 V operating range.\n",
                                        encoding="utf-8")
    proc = procgen.generate(design.load(ws))
    assert tuple(proc["design"]["vin_range"]) == (8.0, 24.0)
    skipped = {s["stage"] for s in proc["skipped"]}
    assert not {"limits", "limits:vin-min", "limits:vin-max"} & skipped
    want = {"requirements VIN min 8 V": 8.0, "requirements VIN max 24 V": 24.0}
    corners = {s["derived_from"]: s["set"]["v"] for st in proc["stages"] for s in st["steps"]
               if s["type"] == "supply" and s.get("derived_from") in want}
    assert corners == want

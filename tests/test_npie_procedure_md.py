"""Limits from the design, as the person at the bench reads them: procedure.md
gives every step with a limit one row whose Limit cell holds its whole limit
(its `expect` and every console field's, by name) and whose From cell is its
`derived_from`.

Each fixture variant of test_npie_limit_fields is rendered, so every limit
procgen emits is checked. A row is split into cells the way GitHub's markdown
does (on any '|' not escaped), so a manifest regex with an alternation in it
must not split a row. Nothing here opens an instrument.
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
if str(NPIE) not in sys.path:
    sys.path.insert(0, str(NPIE))

from npielib import design, procgen, render  # noqa: E402
from test_npie_limit_fields import VARIANTS  # noqa: E402
from test_npie_limit_trace import FIX  # noqa: E402

BANNER = "^(fwe mini|fwe boot) "


def _cells(line: str) -> list[str]:
    parts = re.split(r"(?<!\\)\|", line)
    return [p.strip().replace(r"\|", "|") for p in parts[1:-1]]


def _rows(md: str) -> dict[str, list[list[str]]]:
    rows: dict[str, list[list[str]]] = {}
    for line in md.splitlines():
        if line.startswith("| ") and not line.startswith("| Step |"):
            c = _cells(line)
            rows.setdefault(c[0], []).append(c)
    return rows


def _bounds(e: dict) -> list[str]:
    out = [f"{e[k]:g}" for k in ("min", "max", "edges_min") if e.get(k) is not None]
    return out + ([e["unit"]] if e.get("unit") else [])


def _render(tmp_path: Path, variant) -> tuple[dict, str]:
    ws = tmp_path / "mini-bldc"
    shutil.copytree(FIX, ws)
    variant(ws)
    proc = procgen.generate(design.load(ws))
    md = render.procedure_md(proc)
    md.encode("ascii")
    return proc, md


@pytest.mark.parametrize("variant", VARIANTS, ids=lambda v: v.__name__.strip("_").replace("_", "-"))
def test_every_limit_and_its_source_is_in_the_procedure(tmp_path, variant):
    proc, md = _render(tmp_path, variant)
    rows = _rows(md)
    limited, missing = 0, []
    for s in (s for st in proc["stages"] for s in st["steps"]):
        row = rows.get(s["id"], [])
        if len(row) != 1 or len(row[0]) != 4:
            missing.append(f"{s['id']}: not one four-cell row: {row}")
            continue
        _, _, limit, source = row[0]
        if source != s.get("derived_from", "").replace("±", "+/-"):
            missing.append(f"{s['id']}: From {source!r} is not {s.get('derived_from')!r}")
        if not (s.get("expect") or s.get("fields")):
            continue
        limited += 1
        want = _bounds(s["expect"]) if s.get("expect") else []
        for k, e in (s.get("fields") or {}).items():
            want += [f"{k}:"] + _bounds(e)
        if not want or not all(w in limit for w in want):
            missing.append(f"{s['id']} {s.get('text')!r}: Limit {limit!r} lacks {want}")
    assert limited and not missing, "limits off procedure.md:\n" + "\n".join(missing)


def test_a_regex_alternation_does_not_split_a_row(tmp_path):
    def banner(ws: Path) -> None:
        p = ws / "firmware" / "fwe-manifest.json"
        m = json.loads(p.read_text())
        m["uart"]["banner_regex"] = BANNER
        p.write_text(json.dumps(m))

    proc, md = _render(tmp_path, banner)
    step = next(s for st in proc["stages"] for s in st["steps"]
                if s.get("derived_from") == "manifest uart.banner_regex")
    (row,) = _rows(md)[step["id"]]
    assert len(row) == 4 and BANNER in row[1], row

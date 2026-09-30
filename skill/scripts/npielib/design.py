"""Read what npie needs from an hwde board workspace, and nothing more.

A workspace is `<boards_root>/<board>` (HWDE_BOARDS_ROOT, default ~/dev/boards,
the same place hwde looks). Everything here is read-only: npie never edits a
design file. The loaded Design carries the sha256 of every input, so a
procedure records which design it was generated from.

Inputs (reference/design.md section 1): kicad/<board>.net (kicadsexpr netlist),
kicad/constraints.json (voltages[], power[]), fab/BOM.csv, requirements.md
(operating range), firmware/fwe-manifest.json (optional) and
bringup/overrides.yaml (optional: step id -> expect fields a person set).
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import sexpdata
import yaml


class DesignError(Exception):
    """A workspace input is missing or does not parse (exit 2)."""


def boards_root() -> Path:
    return Path(os.environ.get("HWDE_BOARDS_ROOT") or "~/dev/boards").expanduser()


def resolve_workspace(ws: str) -> Path:
    """A path, or a bare board name under boards_root()."""
    p = Path(ws).expanduser()
    if p.is_dir():
        return p.resolve()
    q = boards_root() / ws
    if q.is_dir():
        return q.resolve()
    raise DesignError(f"no board workspace at {ws} (nor under {boards_root()})")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ------------------------------------------------------------ netlist


def _sym(x):
    return x.value() if isinstance(x, sexpdata.Symbol) else x


def _kids(node, tag):
    return [k for k in node if isinstance(k, list) and k and _sym(k[0]) == tag]


def _atom(node, tag, default=None):
    for k in _kids(node, tag):
        if len(k) > 1:
            return str(_sym(k[1]))
    return default


def _pinfunc(raw: str, pin: str) -> str:
    """KiCad 10 writes pinfunction as '<name>_<pin>' ('S_1', 'REF1_7')."""
    suffix = "_" + pin
    return raw[: -len(suffix)] if raw.endswith(suffix) else raw


def parse_netlist(path: Path) -> dict:
    """-> {"components": {ref: {value, footprint}},
           "nets": {name: [{ref, pin, func, type}]}}"""
    try:
        with open(path, encoding="utf-8") as fh:
            data = sexpdata.load(fh)
    except OSError as exc:
        raise DesignError(f"cannot read netlist {path}: {exc}") from exc
    except Exception as exc:  # sexpdata raises plain exceptions
        raise DesignError(f"netlist {path} does not parse: {exc}") from exc
    if not isinstance(data, list) or not data or str(_sym(data[0])) != "export":
        raise DesignError(f"netlist {path}: not a kicadsexpr export")
    comps: dict[str, dict] = {}
    for blk in _kids(data, "components"):
        for c in _kids(blk, "comp"):
            ref = _atom(c, "ref")
            if ref:
                comps[ref] = {"value": _atom(c, "value") or "",
                              "footprint": _atom(c, "footprint") or ""}
    nets: dict[str, list] = {}
    for blk in _kids(data, "nets"):
        for n in _kids(blk, "net"):
            name = _atom(n, "name")
            if name is None or name.startswith("unconnected-"):
                continue
            nodes = []
            for nd in _kids(n, "node"):
                pin = _atom(nd, "pin") or ""
                nodes.append({"ref": _atom(nd, "ref"), "pin": pin,
                              "func": _pinfunc(_atom(nd, "pinfunction", "") or "", pin),
                              "type": _atom(nd, "pintype", "") or ""})
            nets[name] = nodes
    return {"components": comps, "nets": nets}


# ------------------------------------------------------------ the design


@dataclass
class Design:
    workspace: Path
    board: str
    components: dict
    nets: dict
    voltages: dict                    # net -> nominal V (constraints voltages[])
    power: list                       # constraints power[] entries
    vin_range: tuple | None           # (min, max) operating input, from requirements
    manifest: dict | None
    overrides: dict = field(default_factory=dict)  # step id -> expect fields
    inputs: dict = field(default_factory=dict)   # name -> {path, sha256}

    # ---- netlist queries

    def pins_of(self, ref: str) -> list[dict]:
        """[{net, pin, func}] for one part."""
        return [{"net": n, "pin": x["pin"], "func": x["func"]}
                for n, nodes in self.nets.items() for x in nodes if x["ref"] == ref]

    def refs(self, prefix: str) -> list[str]:
        pat = re.compile(rf"^{prefix}\d+$")
        return sorted((r for r in self.components if pat.match(r)), key=_refkey)

    def value(self, ref: str) -> str:
        return self.components.get(ref, {}).get("value", "")


def _refkey(ref: str):
    m = re.match(r"([A-Za-z]+)(\d+)", ref)
    return (m.group(1), int(m.group(2))) if m else (ref, 0)


_RANGE_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*V\s+operating", re.IGNORECASE)


def _vin_range(req: Path) -> tuple | None:
    """The first 'A-B V operating' in requirements.md, e.g. '10-28 V operating'."""
    if not req.is_file():
        return None
    m = _RANGE_RE.search(req.read_text(encoding="utf-8", errors="replace"))
    return (float(m.group(1)), float(m.group(2))) if m else None


def load(ws: str | Path) -> Design:
    root = resolve_workspace(str(ws))
    board = root.name
    kdir = root / "kicad"
    nets = sorted(kdir.glob("*.net")) if kdir.is_dir() else []
    if not nets:
        raise DesignError(f"{root}: no kicad/*.net netlist (export it with hwde first)")
    netfile = kdir / f"{board}.net"
    netfile = netfile if netfile.is_file() else nets[0]
    inputs = {"netlist": netfile}
    parsed = parse_netlist(netfile)

    cons_path = kdir / "constraints.json"
    voltages, power = {}, []
    if cons_path.is_file():
        inputs["constraints"] = cons_path
        try:
            cons = json.loads(cons_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise DesignError(f"{cons_path}: {exc}") from exc
        voltages = {v["net"]: float(v["voltage"]) for v in cons.get("voltages", [])
                    if "net" in v and "voltage" in v}
        power = [p for p in cons.get("power", []) if "net" in p]

    bom = root / "fab" / "BOM.csv"
    if bom.is_file():
        inputs["bom"] = bom
        with open(bom, encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                for ref in (row.get("Designator") or "").split(","):
                    ref = ref.strip()
                    if ref in parsed["components"]:
                        parsed["components"][ref]["lcsc"] = row.get("LCSC Part #", "")

    req = root / "requirements.md"
    if req.is_file():
        inputs["requirements"] = req

    manifest = None
    man_path = root / "firmware" / "fwe-manifest.json"
    if man_path.is_file():
        inputs["manifest"] = man_path
        try:
            manifest = json.loads(man_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise DesignError(f"{man_path}: {exc}") from exc
        if not str(manifest.get("schema", "")).startswith("fwe-manifest/1"):
            raise DesignError(f"{man_path}: schema {manifest.get('schema')!r}, "
                              "npie reads fwe-manifest/1")

    overrides = {}
    ov_path = root / "bringup" / "overrides.yaml"
    if ov_path.is_file():
        inputs["overrides"] = ov_path
        try:
            overrides = yaml.safe_load(ov_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise DesignError(f"{ov_path}: {exc}") from exc
        if not isinstance(overrides, dict) or not all(
                isinstance(v, dict) for v in overrides.values()):
            raise DesignError(f"{ov_path}: expected a mapping of step id -> expect fields")

    return Design(
        workspace=root, board=board,
        components=parsed["components"], nets=parsed["nets"],
        voltages=voltages, power=power, vin_range=_vin_range(req),
        manifest=manifest, overrides=overrides,
        inputs={k: {"path": str(p.relative_to(root)), "sha256": sha256(p)}
                for k, p in inputs.items()},
    )

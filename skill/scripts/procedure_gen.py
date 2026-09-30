#!/usr/bin/env python
"""procedure_gen.py - write a board's staged bring-up procedure from its design.

  procedure_gen.py --workspace <board dir | board name> [--out-dir DIR] [--stdout]

Reads the hwde workspace (netlist, constraints, BOM, requirements, fwe
manifest, bringup/overrides.yaml; npielib/design.py) and writes <workspace>/bringup/procedure.json
(schema npie-procedure/1, reference/procedure-schema.md), procedure.md, the
same steps for a person at the bench, and bench.example.yaml, one line per
instrument role the procedure uses for the bench host to fill in. --out-dir writes them elsewhere (a
scratch dir, a test); --stdout prints the procedure instead of writing.

Prints JSON {ok, board, procedure, markdown, bench_example, stages, steps,
skipped, overrides}.
Exit 0 written, 2 error (a missing or unparsable input, an override
naming a step the procedure does not have). Never interactive.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from npielib import design, procgen, render  # noqa: E402


# role -> the bench.yaml line a bench host edits (reference/design.md section 4)
BENCH_EXAMPLE = {
    "psu": '{driver: scpi-psu, resource: "USB0::...::INSTR", channel: 1}',
    "dmm": '{driver: scpi-dmm, resource: "TCPIP0::<ip>::INSTR"}',
    "scope": '{driver: scpi-scope, resource: "USB0::...::INSTR"}',
    "logic": '{driver: sigrok, device: fx2lafw}',
    "console": '{driver: serial, port: /dev/ttyUSB0, baud: 115200}',
    "probe": '{driver: swd, tool: probe-rs}',
}


def bench_example(proc: dict) -> str:
    L = [f"# bench.yaml for {proc['board']}: copy to bench.yaml on the bench host and",
         "# fill in each resource. The box never uses this file.",
         "bench: owner-laptop", "roles:"]
    L += [f"  {r}: {BENCH_EXAMPLE[r]}" for r in proc["roles"] if r in BENCH_EXAMPLE]
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--out-dir")
    ap.add_argument("--stdout", action="store_true")
    a = ap.parse_args(argv)
    try:
        d = design.load(a.workspace)
        proc = procgen.generate(d)
    except design.DesignError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2
    if a.stdout:
        print(json.dumps(proc, indent=1))
        return 0
    out = Path(a.out_dir) if a.out_dir else d.workspace / "bringup"
    out.mkdir(parents=True, exist_ok=True)
    pj, pm = out / "procedure.json", out / "procedure.md"
    pj.write_text(json.dumps(proc, indent=1) + "\n", encoding="utf-8")
    pm.write_text(render.procedure_md(proc), encoding="ascii", errors="replace")
    be = out / "bench.example.yaml"
    be.write_text(bench_example(proc), encoding="ascii")
    print(json.dumps({
        "ok": True, "board": d.board, "procedure": str(pj), "markdown": str(pm), "bench_example": str(be),
        "stages": [s["id"] for s in proc["stages"]],
        "steps": sum(len(s["steps"]) for s in proc["stages"]),
        "skipped": proc["skipped"],
        "overrides": [o["step"] for o in proc["overrides"]],
    }, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

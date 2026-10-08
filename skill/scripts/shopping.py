#!/usr/bin/env python
"""shopping.py - the equipment and parts a board's bring-up procedure needs.

  shopping.py --workspace <board dir | board name> [--procedure FILE] [--out FILE]

Reads <workspace>/bringup/procedure.json (procedure_gen.py writes it; run that
first) and derives, from the steps themselves, what the bench must have: each
instrument role with the minimum spec the steps ask of it (supply volts and
amps, meter functions and ranges, scope bandwidth and channels, analyser
channels and rate, the USB-UART's levels, the SWD probe the manifest's flash
command drives), then the parts (leads, clips, wires, a motor when the
full-function stage is in). Every line names the steps that need it, so an
item nobody needs is never listed. Writes <workspace>/bringup/shopping.md
(--out elsewhere).

Prints JSON {ok, board, shopping, equipment: [{role, what, spec, steps}],
parts: [{what, qty, spec, steps}]}.
Exit 0 written, 2 error (no procedure, unreadable JSON). Never interactive.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from npielib import design  # noqa: E402

SCOPE_BW_FLOOR_HZ = 50e6    # below this a bench scope is not worth buying
SCOPE_BW_PER_HZ = 10        # bandwidth >= 10x the fastest signal measured
PSU_HEADROOM = 1.2          # supply current rated 20 % above the highest limit
# flash tool (manifest flash.commands key) -> the probe to buy for it
PROBES = {
    "probe-rs": "a CMSIS-DAP, ST-LINK/V2+ or J-Link SWD probe (all driven by probe-rs)",
    "openocd": "a CMSIS-DAP or ST-LINK SWD probe (OpenOCD)",
    "pyocd": "a CMSIS-DAP SWD probe (pyOCD)",
    "stm32cubeprogrammer": "an ST-LINK/V3 SWD probe",
    "jlink": "a SEGGER J-Link",
}


def _nice(x: float, steps=(1, 1.5, 2, 3, 5, 6, 8, 10)) -> float:
    """Round up to the next common rating (30 V, 5 A, 100 MHz)."""
    if x <= 0:
        return 0.0
    e = 10 ** math.floor(math.log10(x))
    return next(s * e for s in steps if s * e >= x - 1e-12)


def _steps(proc: dict):
    for s in proc["stages"]:
        for st in s["steps"]:
            yield s["id"], st


def _ids(xs) -> str:
    xs = list(xs)
    return ", ".join(xs[:4]) + (f" (+{len(xs) - 4} more)" if len(xs) > 4 else "")


def derive(proc: dict, manifest: dict | None) -> tuple[list, list]:
    by = {}
    for stage, st in _steps(proc):
        by.setdefault(st.get("role"), []).append((stage, st))
    eq, parts = [], []

    if "psu" in by:
        sets = [st["set"] for _, st in by["psu"] if st["type"] == "supply"]
        v = max((s["v"] for s in sets), default=0)
        i = max((s["i_limit"] for s in sets), default=0)
        inject = any((st.get("provoke") or {}).get("on") for _, st in _steps(proc))
        eq.append({"role": "psu", "what": "programmable bench power supply, "
                   + ("two channels (the second injects on a trip's sense net)"
                      if inject else "one channel"),
                   "spec": f">= {_nice(v):g} V and >= {_nice(i * PSU_HEADROOM):g} A, "
                           "a settable current limit and output switch, SCPI over USB or LAN; "
                           f"highest setting {v:g} V / {i:g} A",
                   "steps": [st["id"] for _, st in by["psu"]]})
    if "dmm" in by:
        q = sorted({st["quantity"] for _, st in by["dmm"]})
        vmax = max((st["expect"].get("max") or st["expect"].get("nominal") or 0
                    for _, st in by["dmm"] if st["quantity"] == "voltage"), default=0)
        eq.append({"role": "dmm", "what": "bench multimeter",
                   "spec": f"functions {', '.join(q)}"
                           + (f"; DC volts to >= {_nice(vmax):g} V" if vmax else "")
                           + "; SCPI over USB or LAN",
                   "steps": [st["id"] for _, st in by["dmm"]]})
    if "scope" in by:
        ch = max(max(st.get("channel", 1), st.get("channel2", 1)) for _, st in by["scope"])
        hz = max((st["expect"].get("max") or 0 for _, st in by["scope"]
                  if st["quantity"] == "freq"), default=0)
        # a dead time is read off edges a fifth of it long: bw = 0.35 / rise
        dt = min((st["expect"]["nominal"] for _, st in by["scope"]
                  if st["quantity"] == "deadtime"), default=None)
        bw = max(SCOPE_BW_FLOOR_HZ, _nice(hz * SCOPE_BW_PER_HZ),
                 _nice(0.35 / (dt * 1e-9 / 5)) if dt else 0)
        q = sorted({st["quantity"] for _, st in by["scope"]})
        eq.append({"role": "scope", "what": "digital oscilloscope with 10x probes",
                   "spec": f">= {bw / 1e6:g} MHz, >= {max(ch, 2)} channels, automatic "
                           f"measurements ({', '.join(q)}) and screenshots over SCPI",
                   "steps": [st["id"] for _, st in by["scope"]]})
    if "logic" in by:
        ch = max(len(st.get("channels", [])) for _, st in by["logic"])
        rate = max(st.get("samplerate", 0) for _, st in by["logic"])
        eq.append({"role": "logic", "what": "logic analyser supported by sigrok",
                   "spec": f">= {max(ch, 8)} channels at >= {_nice(rate / 1e6):g} MS/s "
                           "(an fx2lafw 8-channel clone is enough)",
                   "steps": [st["id"] for _, st in by["logic"]]})
    uart = (manifest or {}).get("uart") or {}
    if "console" in by:
        eq.append({"role": "console", "what": "USB-UART adapter",
                   "spec": f"{uart.get('levels', '3V3')} logic levels, "
                           f"{uart.get('baud', 115200)} baud, TX/RX/GND on loose leads",
                   "steps": [st["id"] for _, st in by["console"]]})
    fl = (manifest or {}).get("flash") or {}
    if "probe" in by:
        tools = list((fl.get("commands") or {}).keys())
        probe = next((PROBES[t] for t in tools if t in PROBES),
                     "an SWD probe the manifest's flash command drives")
        eq.append({"role": "probe", "what": "SWD programming probe",
                   "spec": f"{probe}; a cable to fit {fl.get('connector', 'the SWD header')}",
                   "steps": [st["id"] for _, st in by["probe"]]})

    meas = [st["id"] for _, st in by.get("dmm", []) + by.get("scope", [])]
    if meas:
        parts.append({"what": "micro hook clips / test-point grabbers", "qty": 4,
                      "spec": "for test points and passive pads", "steps": meas})
    if "psu" in by:
        parts.append({"what": "supply leads, banana to croc clip, red + black", "qty": 1,
                      "spec": "rated for the supply current", "steps": [by["psu"][0][1]["id"]]})
    if "console" in by or "logic" in by:
        parts.append({"what": "female-female jumper wires, 2.54 mm", "qty": 10,
                      "spec": "UART and analyser to the board's headers",
                      "steps": [st["id"] for _, st in by.get("console", []) + by.get("logic", [])][:1]})
    ff = [st["id"] for stage, st in _steps(proc) if stage == "full-function"]
    if ff:
        v = next((st["set"]["v"] for stage, st in _steps(proc)
                  if stage == "full-function" and st["type"] == "supply"), 0)
        parts.append({"what": "BLDC motor", "qty": 1,
                      "spec": f"rated for >= {v:g} V, low power, with a shaft clamp", "steps": ff})
    parts.append({"what": "ESD wrist strap and mat", "qty": 1, "spec": "", "steps": []})
    return eq, parts


def markdown(proc: dict, eq: list, parts: list) -> str:
    L = [f"# Bring-up shopping list: {proc['board']}", "",
         f"Derived from bringup/procedure.json ({proc.get('generated', '?')}) by "
         "npie shopping.py. Every line names the steps that need it; regenerate "
         "after the procedure changes.", "", "## Equipment", "",
         "| Role | What | Minimum spec | Needed by |", "|---|---|---|---|"]
    L += [f"| {e['role']} | {e['what']} | {e['spec']} | {_ids(e['steps'])} |" for e in eq]
    L += ["", "## Parts", "", "| What | Qty | Spec | Needed by |", "|---|---|---|---|"]
    L += [f"| {p['what']} | {p['qty']} | {p['spec']} | {_ids(p['steps']) or 'all'} |"
          for p in parts]
    if proc.get("skipped"):
        L += ["", "Stages not in this procedure (nothing bought for them): "
              + "; ".join(f"{s['stage']} - {s['reason']}" for s in proc["skipped"]) + "."]
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--procedure")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    try:
        ws = design.resolve_workspace(a.workspace)
        pj = Path(a.procedure) if a.procedure else ws / "bringup" / "procedure.json"
        proc = json.loads(pj.read_text(encoding="utf-8"))
        mf = ws / "firmware" / "fwe-manifest.json"
        manifest = json.loads(mf.read_text(encoding="utf-8")) if mf.is_file() else None
    except (design.DesignError, OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": f"{exc} (run procedure_gen.py first)"}))
        return 2
    eq, parts = derive(proc, manifest)
    out = Path(a.out) if a.out else ws / "bringup" / "shopping.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(markdown(proc, eq, parts), encoding="ascii", errors="replace")
    print(json.dumps({"ok": True, "board": proc["board"], "shopping": str(out),
                      "equipment": eq, "parts": parts}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

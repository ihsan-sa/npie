"""Design -> npie-procedure/1 (reference/design.md section 2).

Every limit is derived from a design fact and says which one in the step's
`derived_from`. The rules, in one place so a wrong limit is fixed here:

- unpowered: resistance to GND above a floor (100 ohm logic rails, 1 kohm the
  input and power-stage nets), diode mode 0.3-0.8 V across each FET body diode.
- power-up: supply at VIN min + 2 V, current limit 2x the full-load estimate
  capped at FIRST_ILIMIT_A; idle current between IDLE_FLOOR_A and 90 % of the
  limit (a supply sitting in current limit is a fail).
- rails: nominal from constraints voltages[], +/-5 %, +/-8 % for a rail made
  through a catch diode (asynchronous converter); a rail rated like the input
  follows the supply setpoint (-5 %/+1 %). Ripple 2 % pk-pk on switched rails.
- host link: a board whose constraints blocks[] has a board-to-board header
  naming "EVN JP8" sits under Lattice's LFE5UM5G-85F-EVN and talks to the
  FPGA through FT2232H port B. Before its own first power: the EVN rework
  (R34/R35 fitted as 0R, R22/R23 left fitted because they carry I2C), the
  EEPROM backup and fixFT2232_ecp5evn, and the fwe-pwm8 id read (0xF8), all
  confirmed by a person; then, mated, a meter reads the block's vio_v on the
  header's pins 1 and 17 and GND on pins 6 and 9 (Raspberry Pi numbering),
  +/-5 % of vio_v.
- blocks: a current-sense amp's output at zero current sits at the mean of its
  REF pins' nets; everything firmware-driven comes from the fwe manifest.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from .design import Design, DesignError
from .instruments import PWM8_ID

SCHEMA = "npie-procedure/1"

FIRST_ILIMIT_A = 0.2
SPIN_ILIMIT_A = 1.0
IDLE_FLOOR_A = 0.002
EFFICIENCY = 0.8
R_FLOOR_LOGIC = 100.0
R_FLOOR_POWER = 1000.0
DIODE_V = (0.3, 0.8)
TOL_REG = 0.05
TOL_ASYNC = 0.08
RIPPLE_FRAC = 0.02

EVN_HEADER_RE = re.compile(r"\bEVN JP8\b")
EVN_V_PINS, EVN_GND_PINS = ("1", "17"), ("6", "9")   # JP8, Raspberry Pi numbering

_INPUT_RE = re.compile(r"(^|/)(VIN|VM|VBAT|VBUS|VSUP|VCC_IN|VIN_RAW)(_IN)?$|_IN$", re.I)
_NOT_RAIL_RE = re.compile(r"BST|/G[HL][A-Z]?$|GATE|_SW$|(^|/)SW|PHASE|SNB", re.I)


def _short(net: str) -> str:
    return net.rsplit("/", 1)[-1] or net


class _Builder:
    def __init__(self, d: Design):
        self.d = d
        self.stages: list[dict] = []
        self.skipped: list[dict] = []
        self.roles: set[str] = set()
        self._stage = None

    # ---- stage/step plumbing

    def stage(self, sid: str, title: str):
        self._stage = {"id": sid, "title": title, "steps": []}
        self.stages.append(self._stage)

    def step(self, type_: str, **kw) -> dict:
        st = self._stage
        s = {"id": f"{st['id']}.{len(st['steps']) + 1:02d}", "type": type_, **kw}
        if "role" in s:
            self.roles.add(s["role"])
        st["steps"].append(s)
        return s

    def human(self, text: str, **kw):
        return self.step("human", text=text, **kw)

    def skip(self, stage: str, reason: str):
        self.skipped.append({"stage": stage, "reason": reason})

    # ---- netlist helpers

    def point(self, net: str) -> dict:
        """Where to probe a net: its test point, else a passive's pad, else any pad."""
        nodes = self.d.nets.get(net, [])
        tps = [n for n in nodes if re.match(r"^TP\d+$", n["ref"] or "")]
        passives = [n for n in nodes if re.match(r"^[RCL]\d+$", n["ref"] or "")
                    and len(self.d.pins_of(n["ref"])) == 2]
        passives.sort(key=lambda n: (not n["ref"].startswith("C"), n["ref"]))
        pick = (tps or passives or nodes or [None])[0]
        if pick is None:
            return {"ref": None, "pin": None, "net": net, "label": _short(net)}
        if pick in tps:
            label = f"{pick['ref']} ({_short(net)})"
        else:
            label = f"{pick['ref']} pin {pick['pin']} ({_short(net)})"
        return {"ref": pick["ref"], "pin": pick["pin"], "net": net, "label": label}

    def probe(self, instr: str, plus: dict, minus: dict, mode: str) -> dict:
        return self.human(f"{instr} on {mode}: red (+) on {plus['label']}, "
                          f"black (-) on {minus['label']}.")

    # ---- design analysis

    def analyse(self):
        d = self.d
        self.gnd = "GND" if "GND" in d.nets else next(
            (n for n in d.nets if re.search(r"GND", n, re.I)), None)
        pdn_false = {p["net"] for p in d.power if p.get("pdn") is False}
        cands = [n for n in d.voltages if n != self.gnd and n in d.nets]
        tp_nets = {x_net for x_net, nodes in d.nets.items()
                   if any(re.match(r"^TP\d+$", n["ref"] or "") for n in nodes)}
        named_in = [n for n in cands if _INPUT_RE.search(n)]
        named_in.sort(key=lambda n: (n not in tp_nets, -d.voltages[n], n))
        self.input = named_in[0] if named_in else (
            max(cands, key=lambda n: d.voltages[n]) if cands else None)
        vin_rating = d.voltages.get(self.input)
        self.rails = []
        for n in cands:
            if n == self.input or n in pdn_false or _NOT_RAIL_RE.search(n):
                continue
            v = d.voltages[n]
            follows = vin_rating is not None and v == vin_rating
            self.rails.append({"net": n, "v": v, "follows_input": follows,
                               "async": self._via_catch_diode(n),
                               "switched": self._switched(n)})
        self.rails.sort(key=lambda r: (not r["follows_input"], -r["v"], r["net"]))
        self.bridges = self._half_bridges()

    def _via_catch_diode(self, net: str) -> bool:
        for n in self.d.nets.get(net, []):
            if re.match(r"^D\d+$", n["ref"] or ""):
                other = [p["net"] for p in self.d.pins_of(n["ref"]) if p["net"] != net]
                if any(re.search(r"SW", o) for o in other):
                    return True
        return False

    def _switched(self, net: str) -> bool:
        """Made by a switcher: an inductor or catch diode joins it to a SW node."""
        for n in self.d.nets.get(net, []):
            if re.match(r"^[LD]\d+$", n["ref"] or ""):
                if any(re.search(r"SW", p["net"]) for p in self.d.pins_of(n["ref"])):
                    return True
        return False

    def _half_bridges(self) -> list[dict]:
        fets = {}
        for q in self.d.refs("Q"):
            f = {p["func"]: p["net"] for p in self.d.pins_of(q)}
            if "D" in f and "S" in f:
                fets[q] = f
        out = []
        for hs, fh in fets.items():
            phase = fh["S"]
            for ls, fl in fets.items():
                if ls != hs and fl["D"] == phase:
                    out.append({"phase": phase, "hs": hs, "ls": ls,
                                "bus": fh["D"], "ls_src": fl["S"]})
        out.sort(key=lambda b: b["phase"])
        return out

    # ---- stages

    def visual(self):
        self.stage("visual", "Visual inspection (unpowered)")
        d = self.d
        pol = [r for r in d.refs("D") + d.refs("U") + d.refs("Q")]
        ecaps = [r for r in d.refs("C") if re.search(r"\d+\s*uF\s+\d+V", d.value(r))
                 and re.search(r"CAP-TH|CP_|Elec", d.components[r]["footprint"])]
        self.human("Check the board for damage, solder bridges on fine-pitch ICs "
                   f"({', '.join(d.refs('U'))}) and missing parts against fab/BOM.csv.")
        self.human("Check orientation of polarised parts: "
                   + ", ".join(f"{r} {d.value(r)}" for r in pol + ecaps) + ".")

    def unpowered(self):
        self.stage("unpowered", "Unpowered resistance and diode checks")
        self.human("Board unpowered, every cable off. Discharge the input capacitors.")
        g = self.point(self.gnd)
        nets = ([self.input] if self.input else []) + [r["net"] for r in self.rails]
        nets += [b["phase"] for b in self.bridges]
        for n in dict.fromkeys(nets):
            power = n == self.input or any(b["phase"] == n for b in self.bridges) \
                or any(r["net"] == n and r["follows_input"] for r in self.rails)
            floor = R_FLOOR_POWER if power else R_FLOOR_LOGIC
            p = self.point(n)
            self.probe("DMM", p, g, "resistance")
            self.step("measure", role="dmm", quantity="resistance",
                      points={"plus": p, "minus": g},
                      expect={"nominal": None, "min": floor, "max": None, "unit": "ohm"},
                      derived_from=f"short check {_short(n)}-GND: floor "
                                   f"{'power net' if power else 'logic rail'} {floor:g} ohm",
                      text=f"{_short(n)} to GND is not a short")
        for b in self.bridges:
            ph, bus = self.point(b["phase"]), self.point(b["bus"])
            src = self.point(b["ls_src"])
            for plus, minus, fet in ((ph, bus, b["hs"]), (src, ph, b["ls"])):
                self.probe("DMM", plus, minus, "diode")
                self.step("measure", role="dmm", quantity="diode",
                          points={"plus": plus, "minus": minus},
                          expect={"nominal": 0.5, "min": DIODE_V[0], "max": DIODE_V[1],
                                  "unit": "V"},
                          derived_from=f"{fet} body diode (netlist S={_short(plus['net'])}, "
                                       f"D={_short(minus['net'])})",
                          text=f"{fet} body diode conducts one way")

    def host_link(self):
        hdr = next((b for b in self.d.blocks
                    if b.get("topology") == "board-to-board-header"
                    and EVN_HEADER_RE.search(b.get("name", ""))), None)
        if hdr is None:
            return
        self.stage("host-link", "EVN host link (before this board's first power)")
        j = hdr["name"].split()[0]
        vio = float((hdr.get("operating_point") or {}).get("vio_v", 3.3))
        src = f"constraints blocks[{hdr.get('block', '?')}] {hdr['name']}"
        self.human("EVN unplugged from USB and from this board. Check the host-link "
                   "rework on the LFE5UM5G-85F-EVN: R34 and R35 fitted as 0R, and R22 "
                   "and R23 still fitted (they carry I2C; do not remove them).",
                   derived_from="Lattice EVN user guide: R34/R35 connect FT2232H port B "
                                "to the FPGA UART")
        self.human("Plug the EVN into USB. Read FT2232H port B's EEPROM to a file and "
                   "keep it, before anything writes to it.",
                   derived_from="the EEPROM write below is the only way back")
        self.human("Switch port B from FIFO to UART: fixFT2232_ecp5evn -v 0x403 "
                   "-p 0x6010, then unplug and replug the EVN. Port B now enumerates "
                   "as the second serial port (P2 is the FPGA's rx, P3 its tx).",
                   derived_from="fwe fpga-boards lfe5um5g-85f-evn uart_note")
        self.human("Load the PWM bitstream onto the EVN, then confirm the next step "
                   "reads the generator's id over port B.")
        self.step("pwm", role="pwm", set={"enable": False},
                  derived_from=f"fwe-pwm8-reg/1 id register 0x00 reads 0x{PWM8_ID:02X}; "
                               "any other byte aborts the run",
                  text=f"the PWM UART answers 0x{PWM8_ID:02X}")
        self.human(f"EVN off USB. Mate this board's {j} with the EVN's JP8, then plug "
                   "the EVN back into USB. This board's own supply stays off.")
        tol = TOL_REG
        for pins, nom in ((EVN_V_PINS, vio), (EVN_GND_PINS, 0.0)):
            for pin in pins:
                lo, hi = (round(nom * (1 - tol), 3), round(nom * (1 + tol), 3)) if nom \
                    else (round(-vio * tol, 3), round(vio * tol, 3))
                what = f"{vio:g} V" if nom else "GND"
                self.human(f"DMM on DC volts: red (+) on {j} pin {pin}, black (-) on "
                           f"the EVN's GND (its USB shell). Type the reading ({what}).",
                           value={"unit": "V"},
                           expect={"nominal": nom, "min": lo, "max": hi, "unit": "V"},
                           derived_from=f"{src}: JP8 pin {pin} is {what}, vio_v "
                                        f"{vio:g} V +/-{tol * 100:g} %")

    def power_up(self):
        self.stage("power-up", "Current-limited power-up")
        d = self.d
        if not self.input:
            self.skip("power-up", "no input net found in constraints voltages[]")
            return
        if d.vin_range:
            v = min(d.vin_range[0] + 2.0, d.vin_range[1])
            v_src = f"requirements VIN {d.vin_range[0]:g}-{d.vin_range[1]:g} V: min + 2 V"
        else:
            v = round(d.voltages[self.input] / 2.0, 1)
            v_src = "no operating range in requirements: half the input rating"
        load_w = max((r["v"] * self._rail_current(r["net"]) for r in self.rails
                      if not r["follows_input"]), default=0.0)
        full_a = load_w / EFFICIENCY / v
        ilim = min(FIRST_ILIMIT_A, max(0.05, round(2 * full_a, 2)))
        self.v_first, self.ilim = v, ilim
        inp, g = self.point(self.input), self.point(self.gnd)
        self.step("supply", role="psu", set={"v": v, "i_limit": ilim, "output": False},
                  derived_from=f"{v_src}; limit min({FIRST_ILIMIT_A} A, 2x full load "
                               f"{full_a:.3f} A)")
        self.human(f"Connect the supply: + to {inp['label']}, - to {g['label']}. "
                   f"Supply set to {v:g} V / {ilim:g} A, output still off.")
        self.human(f"About to switch the supply on at {v:g} V with a {ilim:g} A limit. "
                   "Hands clear; watch for smoke or a hot part.")
        self.step("supply", role="psu", set={"v": v, "i_limit": ilim, "output": True})
        self.step("wait", seconds=1.0)
        self.step("measure", role="psu", quantity="current",
                  expect={"nominal": None, "min": IDLE_FLOOR_A,
                          "max": round(0.9 * ilim, 4), "unit": "A"},
                  derived_from="alive floor; below 90 % of the limit (not in CC)",
                  text="idle input current")
        self.human("Touch-test: no part is hot a few seconds after power-up.")

    def _rail_current(self, net: str) -> float:
        for p in self.d.power:
            if p["net"] == net:
                return float(p.get("current_a", 0.0))
        return 0.0

    def rails_stage(self):
        self.stage("rails", "Rail voltages and ripple")
        if not hasattr(self, "v_first"):
            self.skip("rails", "power-up stage was not generated")
            return
        g = self.point(self.gnd)
        for r in self.rails:
            p = self.point(r["net"])
            if r["follows_input"]:
                nom = self.v_first
                exp = {"nominal": nom, "min": round(nom * 0.95, 3),
                       "max": round(nom * 1.01, 3), "unit": "V"}
                src = f"follows the input (rated like {_short(self.input)}): setpoint -5 %/+1 %"
            else:
                tol = TOL_ASYNC if r["async"] else TOL_REG
                nom = r["v"]
                exp = {"nominal": nom, "min": round(nom * (1 - tol), 3),
                       "max": round(nom * (1 + tol), 3), "unit": "V"}
                src = (f"constraints voltages[{_short(r['net'])}]={nom:g} V, "
                       f"+/-{tol * 100:g} %{' (catch-diode converter)' if r['async'] else ''}")
            self.probe("DMM", p, g, "DC volts")
            self.step("measure", role="dmm", quantity="voltage",
                      points={"plus": p, "minus": g}, expect=exp, derived_from=src,
                      text=f"{_short(r['net'])} voltage")
        for r in self.rails:
            if not r["switched"] or r["follows_input"]:
                continue
            p = self.point(r["net"])
            self.human(f"Scope channel 1 on {p['label']}, ground spring to the nearest "
                       "GND pad, AC coupled, 20 MHz bandwidth limit.")
            self.step("scope", role="scope", channel=1, quantity="vpp",
                      points={"plus": p, "minus": g}, screenshot=True,
                      expect={"nominal": None, "min": None,
                              "max": round(r["v"] * RIPPLE_FRAC, 4), "unit": "V"},
                      derived_from=f"switched rail ripple <= {RIPPLE_FRAC * 100:g} % of "
                                   f"{r['v']:g} V",
                      text=f"{_short(r['net'])} ripple")

    def programming(self):
        self.stage("programming", "Programming and first boot")
        m = self.d.manifest
        if not m:
            self.skip("programming", "no firmware/fwe-manifest.json in the workspace")
            return
        fl, ua = m.get("flash", {}), m.get("uart", {})
        self.human(f"Connect the SWD probe to {fl.get('connector', 'the SWD header')}; "
                   "the board stays powered from the supply.")
        self.step("flash", role="probe", artifact="elf",
                  derived_from=f"manifest flash.commands, firmware {m.get('version', '?')}")
        self.human(f"Connect the USB-UART ({ua.get('levels', '3V3')}, "
                   f"{ua.get('baud', 115200)} {ua.get('format', '8N1')}) to "
                   f"{ua.get('connector', 'the UART header')}: adapter RX to pin "
                   f"{ua.get('tx_pin', '?')} (board TX), adapter TX to pin "
                   f"{ua.get('rx_pin', '?')} (board RX), GND to GND. Then press reset.")
        self.step("console", role="console", send=None,
                  expect_re=ua.get("banner_regex", "."), timeout_s=5,
                  derived_from="manifest uart.banner_regex", text="boot banner")
        if any(c.get("name") == "version" for c in m.get("commands", [])):
            self.step("console", role="console", send="version", expect_re="^OK ",
                      timeout_s=2, derived_from="manifest commands[version]",
                      text="firmware answers")

    def blocks(self):
        self.stage("blocks", "Per-block tests")
        d, m = self.d, self.d.manifest
        g = self.point(self.gnd)
        for h in (m or {}).get("test_hooks", []):
            self.step("console", role="console", send=h["send"], expect_re=h["expect"],
                      timeout_s=h.get("timeout_s", 5),
                      derived_from=f"manifest test_hooks[{h['name']}]",
                      text=f"firmware test hook {h['name']}")
        st = {c["name"]: c for c in (m or {}).get("commands", [])}.get("status")
        if st and "vbus_v" in st.get("reply_fields", []) and hasattr(self, "v_first"):
            v = self.v_first
            self.step("console", role="console", send="status", expect_re="^OK ",
                      timeout_s=2, fields={"vbus_v": {"nominal": v, "min": round(v * 0.95, 3),
                                                      "max": round(v * 1.05, 3), "unit": "V"}},
                      derived_from="manifest commands[status].reply_fields vbus_v "
                                   "vs the supply setting +/-5%",
                      text="firmware reads the input voltage")
        for u in d.refs("U"):
            f = {p["func"]: p["net"] for p in d.pins_of(u)}
            if "OUT" in f and "REF1" in f and "REF2" in f:
                vref = [0.0 if n == self.gnd else d.voltages.get(n) for n in (f["REF1"], f["REF2"])]
                if None in vref:
                    continue
                mid = sum(vref) / 2
                p = self.point(f["OUT"])
                self.probe("DMM", p, g, "DC volts")
                self.step("measure", role="dmm", quantity="voltage",
                          points={"plus": p, "minus": g},
                          expect={"nominal": mid, "min": round(mid * 0.97, 3),
                                  "max": round(mid * 1.03, 3), "unit": "V"},
                          derived_from=f"{u} {d.value(u)} REF1={_short(f['REF1'])}, "
                                       f"REF2={_short(f['REF2'])}: zero-current output "
                                       f"at their mean +/-3 %",
                          text=f"{u} sense-amp offset")
        leds = d.refs("D")
        leds = [r for r in leds if re.search(r"LED", d.value(r), re.I)]
        if leds:
            self.human("Note which LEDs are lit: "
                       + ", ".join(f"{r} {d.value(r)}" for r in leds) + ".",
                       value={"unit": "text"})
        if not m:
            self.skip("blocks:firmware", "no fwe manifest: buttons, inputs and gate "
                                         "drive need firmware")
            return
        cmds = {c["name"]: c for c in m.get("commands", [])}
        for sw in d.refs("SW"):
            nets = [p["net"] for p in d.pins_of(sw) if p["net"] != self.gnd]
            reset = any(re.search(r"NRST|RESET", n, re.I) for n in nets)
            self.human(f"Press and release {sw} ({d.value(sw)}).")
            self.step("console", role="console", send=None,
                      expect_re=m.get("uart", {}).get("banner_regex", "^EVT ")
                      if reset else "^EVT ",
                      timeout_s=5, derived_from=f"netlist {sw} on {', '.join(map(_short, nets))}",
                      text=f"{sw} seen by firmware")
        hall = [n for n in d.nets if re.search(r"HALL_[A-C]$", n)]
        if hall:
            conn = self._connector_for(hall)
            self.human(f"Connect the logic analyser to the Hall inputs "
                       f"({', '.join(map(_short, hall))}) at {conn}; plug in the Hall "
                       "sensor and turn the motor slowly by hand during the capture.")
            self.step("logic", role="logic", channels=[_short(n) for n in hall],
                      samplerate=1_000_000, duration_s=3.0, expect={"edges_min": 2},
                      derived_from="netlist HALL_* nets", text="Hall inputs toggle")
        if self.bridges and {"arm", "duty", "disarm"} <= set(cmds):
            self.human("Motor DISCONNECTED. The next steps switch the bridge with no load "
                       f"at {getattr(self, 'v_first', 0):g} V. Confirm the phase outputs "
                       "are free.")
            self.step("console", role="console", send="arm", expect_re="^OK ",
                      timeout_s=2, derived_from="manifest commands[arm] safe:false")
            for i, b in enumerate(self.bridges):
                duty = ["0"] * len(self.bridges)
                duty[i] = "0.5"
                p = self.point(b["phase"])
                self.step("console", role="console", send="duty " + " ".join(duty),
                          expect_re="^OK ", timeout_s=2,
                          derived_from="manifest commands[duty]")
                self.human(f"Scope channel 1 on {p['label']}, DC coupled, 10x probe.")
                self.step("scope", role="scope", channel=1, quantity="duty",
                          points={"plus": p, "minus": g}, screenshot=True,
                          expect={"nominal": 0.5, "min": 0.45, "max": 0.55, "unit": ""},
                          derived_from="commanded duty 0.5 +/-0.05",
                          text=f"{_short(b['phase'])} switches ({b['hs']}/{b['ls']})")
                hz = (m.get("safety") or {}).get("pwm_hz")
                if hz:
                    self.step("scope", role="scope", channel=1, quantity="freq",
                              points={"plus": p, "minus": g},
                              expect={"nominal": float(hz), "min": hz * 0.98,
                                      "max": hz * 1.02, "unit": "Hz"},
                              derived_from="manifest safety.pwm_hz +/-2%",
                              text=f"{_short(b['phase'])} PWM frequency")
            self.step("console", role="console", send="disarm", expect_re="^OK ",
                      timeout_s=2, derived_from="manifest commands[disarm]")
        elif self.bridges:
            self.skip("blocks:gate-drive", "manifest has no arm/duty/disarm commands")

    def _connector_for(self, nets: list[str]) -> str:
        want = {_short(n) for n in nets} | {_short(n) + "_RAW" for n in nets}
        for j in self.d.refs("J"):
            pins = [p for p in self.d.pins_of(j) if _short(p["net"]) in want]
            if pins:
                return f"{j} pins " + ", ".join(sorted((p["pin"] for p in pins), key=int))
        return "the sensor connector"

    def full_function(self):
        self.stage("full-function", "Full function with a motor")
        cmds = {c["name"] for c in (self.d.manifest or {}).get("commands", [])}
        if "spin" not in cmds:
            self.skip("full-function", "firmware has no spin command yet "
                                       "(manifest stage "
                                       f"{(self.d.manifest or {}).get('stage', 'none')})")
            self.stages.pop()
            return
        v = getattr(self, "v_first", 12.0)
        self.human("Motor connected to the phase outputs, shaft free and clamped. "
                   f"The next steps spin it at {v:g} V with a {SPIN_ILIMIT_A:g} A limit.")
        self.step("supply", role="psu", set={"v": v, "i_limit": SPIN_ILIMIT_A, "output": True},
                  derived_from=f"first spin: {SPIN_ILIMIT_A:g} A limit, low load")
        self.step("console", role="console", send="arm", expect_re="^OK ", timeout_s=2,
                  derived_from="manifest commands[arm] safe:false")
        self.step("console", role="console", send="spin", expect_re="^OK ", timeout_s=2,
                  derived_from="manifest commands[spin]")
        self.step("wait", seconds=3.0)
        self.step("measure", role="psu", quantity="current",
                  expect={"nominal": None, "min": IDLE_FLOOR_A,
                          "max": round(0.9 * SPIN_ILIMIT_A, 3), "unit": "A"},
                  derived_from="spinning below 90 % of the limit", text="spin current")
        self.human("Confirm the motor turns smoothly without noise or stalls.")
        self.step("console", role="console", send="disarm", expect_re="^OK ", timeout_s=2,
                  derived_from="manifest commands[disarm]")
        self.step("supply", role="psu", set={"v": v, "i_limit": self.ilim, "output": True})

    def limits(self):
        self.stage("limits", "Operating range and protection")
        d = self.d
        if not d.vin_range or not hasattr(self, "v_first"):
            self.skip("limits", "no operating range in requirements.md")
            self.stages.pop()
            return
        lo, hi = d.vin_range
        for v in (lo, hi):
            self.step("supply", role="psu", set={"v": v, "i_limit": self.ilim, "output": True},
                      derived_from=f"requirements VIN {'min' if v == lo else 'max'} {v:g} V")
            self.step("wait", seconds=1.0)
            self.step("measure", role="psu", quantity="current",
                      expect={"nominal": None, "min": IDLE_FLOOR_A,
                              "max": round(0.9 * self.ilim, 4), "unit": "A"},
                      derived_from="idle current holds across the input range",
                      text=f"idle current at {v:g} V")
        saf = (d.manifest or {}).get("safety", {})
        uv = saf.get("vbus_uv_v")
        if uv is not None and uv < lo:
            self.step("supply", role="psu",
                      set={"v": round(uv - 0.5, 2), "i_limit": self.ilim, "output": True},
                      derived_from=f"manifest safety.vbus_uv_v {uv:g} V - 0.5 V")
            self.step("console", role="console", send=None, expect_re=r"^EVT .*uv",
                      timeout_s=5, derived_from="under-voltage trip reported",
                      text=f"UV trip below {uv:g} V")
            self.step("supply", role="psu",
                      set={"v": self.v_first, "i_limit": self.ilim, "output": True})
            self.step("console", role="console", send=saf.get("fault_clear", "clear"),
                      expect_re="^OK ", timeout_s=2, derived_from="manifest safety.fault_clear")
        ov = saf.get("vbus_ov_v")
        if ov is not None and ov > hi:
            self.skip("limits:ov", f"over-voltage trip at {ov:g} V is above the rated "
                                   f"input max {hi:g} V; not applied")
        self.step("supply", role="psu", set={"v": self.v_first, "i_limit": self.ilim,
                                             "output": False},
                  text="end of run: supply off")


def generate(d: Design, now: datetime | None = None) -> dict:
    b = _Builder(d)
    b.analyse()
    b.visual()
    b.unpowered()
    b.host_link()
    b.power_up()
    b.rails_stage()
    b.programming()
    b.blocks()
    b.full_function()
    b.limits()
    stages = [s for s in b.stages if s["steps"]]
    applied = _apply_overrides(stages, d.overrides)
    return {
        "schema": SCHEMA,
        "board": d.board,
        "generated": (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "inputs": d.inputs,
        "design": {"input": b.input, "gnd": b.gnd,
                   "rails": [r["net"] for r in b.rails],
                   "half_bridges": b.bridges,
                   "vin_range": list(d.vin_range) if d.vin_range else None},
        "roles": sorted(b.roles),
        "skipped": b.skipped,
        "overrides": applied,
        "stages": stages,
    }


def _apply_overrides(stages: list[dict], overrides: dict) -> list[dict]:
    """bringup/overrides.yaml: a person's limit wins over the generated one.

    Only `expect` fields are overridden, and each one is recorded (step, the
    generated value, the new one) so the report shows what a person changed.
    A step id the procedure does not have is an error, not a silent no-op.
    """
    by_id = {s["id"]: s for st in stages for s in st["steps"]}
    unknown = sorted(set(overrides) - set(by_id))
    if unknown:
        raise DesignError(f"bringup/overrides.yaml names steps the procedure "
                          f"does not have: {', '.join(unknown)}")
    applied = []
    for sid, fields in sorted(overrides.items()):
        s = by_id[sid]
        if "expect" not in s:
            raise DesignError(f"bringup/overrides.yaml: step {sid} has no limits to override")
        applied.append({"step": sid, "generated": dict(s["expect"]), "override": dict(fields)})
        s["expect"].update(fields)
        s["derived_from"] = s.get("derived_from", "") + " (overridden in bringup/overrides.yaml)"
    return applied

"""One driver layer: a procedure step names a role, bench.yaml binds the role
to a driver (reference/design.md section 4).

Roles and the calls the runner makes:
  psu      set(v, i_limit, output), measure(quantity) -> float, off()
  dmm      measure(quantity, points) -> float
  scope    measure(channel, quantity, points, channel2=None) -> float, screenshot(path);
           quantity deadtime (ns) is channel's falling edge to channel2's rising
  logic    capture(channels, samplerate, duration_s) -> {channel: edges}
  console  send(line), read_until(regex, timeout_s) -> line | None
  probe    flash(manifest, workspace) -> (ok, log)
  pwm      set(f_hz, duty, phase_deg, enable) -> applied, off()
  awg      set(channel, wave, f_hz, vpp, offset, duty, phase_deg, load, output)
           -> applied, off()

Drivers: scpi-psu/scpi-dmm/scpi-scope/scpi-sdg (pyvisa + pyvisa-py), sigrok
(sigrok-cli), serial (pyserial), fwe-pwm8 (pyserial, the FPGA PWM
generator's register protocol), swd (the manifest's flash command), and sim
for every role. The pwm and awg sims are models of the instrument's own
protocol (bytes for the gateware, SCPI for the SDG) with the live driver
class on top, so the tests exercise the code a real bench runs. pyvisa and
pyserial are imported only when a live driver is built, so the box - which
only ever runs sim - never needs them. The live drivers are written against
the generic SCPI sets (Siglent's own for the SDG) and have not met a real
instrument yet (LEARNINGS verify-later); the sim drivers are what the tests
exercise.
"""
from __future__ import annotations

import random
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path


class BenchError(Exception):
    """A role is missing, a driver is unknown or an instrument call failed."""


# =================================================================== sim


class SimBoard:
    """A plausible board behind every simulated instrument.

    Readings come from the procedure's own limits (so a healthy sim board
    passes), with seeded noise inside the middle of each band. `faults` makes
    chosen steps fail: 'short:<net>' (resistance ~0, supply sits in current
    limit, rails collapse), 'rail:<net>=<volts>', 'no-banner', 'no-flash'.

    A boost (the procedure's design.boost) follows its firmware: `arm`
    regulates the output at safety.vout_target_v, `duty <d>` from off runs
    open loop at Vin/(1-d) and is refused while regulating, and each manifest
    trip latches - outputs off, one EVT line, `clear` refused while its cause
    stands. An output-voltage trip fires when the output crosses its
    threshold; any other when a person's step injects on its sense net
    (the step's `provoke`). Input under-voltage latches only while switching.
    """

    def __init__(self, proc: dict, faults: list[str] | None = None, seed: int = 1):
        self.rng = random.Random(seed)
        self.man_banner = None
        self.uv = None
        self.table: dict = {}
        for st in proc["stages"]:
            for s in st["steps"]:
                if s.get("expect") and s.get("points"):
                    key = (s["quantity"], s["points"]["plus"]["net"],
                           s["points"]["minus"]["net"])
                    self.table.setdefault(key, s["expect"])
        self.follows = {
            s["points"]["plus"]["net"] for st in proc["stages"] for s in st["steps"]
            if s.get("quantity") == "voltage" and "follows the input" in s.get("derived_from", "")}
        # the reply each console step waits for, so a hook that expects more
        # than "OK" (version -> ^OK {"board":...) gets a line that fits
        self.hooks = {s["send"].split()[0]: s["expect_re"] for st in proc["stages"]
                      for s in st["steps"] if s["type"] == "console" and s.get("send")
                      and s["expect_re"].startswith("^OK")}
        self.faults = faults or []
        self.shorts = {f.split(":", 1)[1] for f in self.faults if f.startswith("short:")}
        self.rail_over = {}
        for f in self.faults:
            if f.startswith("rail:"):
                net, v = f[5:].rsplit("=", 1)
                self.rail_over[net] = float(v)
        self.v, self.ilim, self.on = 0.0, 0.0, False
        self.flashed = False
        # one duty per half-bridge, until the manifest's duty args say otherwise
        self.nduty = len((proc.get("design") or {}).get("half_bridges") or []) or 3
        self.armed, self.duty = False, [0.0] * self.nduty
        self.fault_latched = None
        self.boost = (proc.get("design") or {}).get("boost")
        self.mode = "off"                 # boost: off, run (arm) or open (duty)
        self.trips: list[dict] = []
        self.injected: set[str] = set()
        self.uv_switching = False
        self.vout_target = self.pwm_hz = None
        self.rx: list[str] = []
        # the FPGA PWM generator's geometry: the picked ECP5 board's until an
        # fpga manifest says otherwise; the gateware model lives here so the
        # scope sees what the generator drives
        self.pwm_n, self.pwm_f = 56, 13.56e6
        self.pwm_gw: Pwm8Gateware | None = None
        self.awg_model: SdgModel | None = None

    def set_manifest(self, man: dict | None):
        if man and man.get("kind") == "fpga":
            geo, clk = man.get("geometry") or {}, man.get("clock") or {}
            self.pwm_n = int(geo.get("steps_per_period") or self.pwm_n)
            self.pwm_f = float(clk.get("f_rf_actual_hz") or geo.get("f_rf_hz") or self.pwm_f)
        elif man:
            self.man_banner = man.get("uart", {}).get("banner_regex")
            saf = man.get("safety", {})
            self.uv = saf.get("vbus_uv_v")
            duty = next((c for c in man.get("commands", []) if c.get("name") == "duty"), None)
            if duty and duty.get("args", "").count("<"):
                self.nduty = duty["args"].count("<")
                self.duty = [0.0] * self.nduty
            self.trips = man.get("trips") or []
            self.uv_switching = bool(man.get("pwm"))
            self.vout_target = saf.get("vout_target_v")
            self.pwm_hz = (man.get("pwm") or {}).get("freq_hz") or saf.get("pwm_hz")

    def pwm_running(self) -> bool:
        return bool(self.pwm_gw and self.pwm_gw.enable and max(self.pwm_gw.duty_on) > 0)

    # ---- physics, such as it is

    def _noise(self, lo, hi, nom):
        if lo is not None and hi is not None:
            mid = nom if nom is not None else (lo + hi) / 2
            span = min(mid - lo, hi - mid) * 0.3
            return mid + self.rng.uniform(-span, span)
        if lo is not None:
            return (lo if lo > 0 else 1.0) * self.rng.uniform(5, 20)
        if hi is not None:
            return hi * self.rng.uniform(0.1, 0.4)
        return nom or 0.0

    def powered(self) -> bool:
        return self.on and not self.shorts and self.v >= 5.0

    def switching(self) -> bool:
        if not self.powered():
            return False
        if self.boost:
            return self.mode in ("run", "open")
        return self.armed and max(self.duty) > 0

    def vout(self) -> float:
        """A boost's output: regulated, Vin/(1-d) open loop, else the input."""
        if not self.powered():
            return 0.0
        if self.switching() and self.mode == "run" and self.vout_target:
            return float(self.vout_target)
        if self.switching() and self.mode == "open":
            return self.v / (1.0 - min(self.duty[0], 0.95))
        return self.v

    def _evt(self, name: str, regex: str | None) -> str | None:
        for lit in (f"{name}_hw", name) if regex else (name,):
            line = 'EVT {"trip":{"new":["%s"]}}' % lit
            if regex is None or re.search(regex, line):
                return line
        return None

    def trip(self, name: str, regex: str | None = None):
        """Latch a trip: both outputs off, one EVT line."""
        if self.fault_latched:
            return
        self.fault_latched = name
        self.mode, self.armed, self.duty = "off", False, [0.0] * self.nduty
        line = self._evt(name, regex)
        if line:
            self.rx.append(line)

    def _check_trips(self):
        for t in self.trips:
            hit = t["name"] in self.injected or (
                t.get("unit") == "V" and self.switching() and self.vout() >= t["threshold"])
            if hit:
                self.trip(t["name"], t.get("evt_regex"))

    def provoke(self, trip: str, on: bool):
        """A person injects on a trip's sense net (or stops)."""
        if on:
            self.injected.add(trip)
            self._check_trips()
        else:
            self.injected.discard(trip)

    def reading(self, quantity: str, plus: str, minus: str) -> float:
        if quantity == "resistance":
            if plus in self.shorts:
                return self.rng.uniform(0.1, 0.5)
        if quantity == "voltage":
            if not self.powered():
                return self.rng.uniform(0.0, 0.01)
            if plus in self.rail_over:
                return self.rail_over[plus]
            if self.boost and plus == self.boost["out"] and self.switching():
                return self.vout() * self.rng.uniform(0.985, 0.995)
            if plus in self.follows:
                return self.v * self.rng.uniform(0.985, 0.995)
        e = self.table.get((quantity, plus, minus))
        if e is None:
            raise BenchError(f"sim: nothing known about {quantity} {plus} -> {minus}")
        return self._noise(e.get("min"), e.get("max"), e.get("nominal"))

    def supply(self, v, ilim, on):
        was = self.powered()
        self.v, self.ilim, self.on = v, ilim, on
        now = self.powered()
        if now and not was:
            self.boot()
        if self.uv is not None and on and v < self.uv and self.flashed:
            if self.uv_switching:
                # this firmware latches input trips only while it switches
                if self.switching():
                    self.trip("vin_uv")
            else:
                self.fault_latched = "uv"
                self.rx.append('EVT {"fault":"uv","vbus_v":%.2f}' % v)

    def current(self) -> float:
        if not self.on:
            return 0.0
        if self.shorts:
            return self.ilim
        base = 0.030 * 12.0 / max(self.v, 1.0) + 0.004
        if self.armed or self.switching():
            base += 0.01
        return min(self.ilim, base * self.rng.uniform(0.95, 1.05))

    def boot(self):
        self.armed, self.mode = False, "off"
        if self.flashed and "no-banner" not in self.faults and self.man_banner:
            self.rx.append(_example_for(self.man_banner) + " sim")
            self.rx.append('EVT {"boot":"power"}')

    def reset(self):
        if self.powered():
            self.boot()

    def command(self, line: str) -> str:
        if not (self.powered() and self.flashed):
            return ""
        word = line.split()[0] if line.split() else ""
        if self.boost and word in ("arm", "duty", "disarm", "clear", "status"):
            return self._boost_command(word, line.split()[1:])
        if word == "arm":
            if self.fault_latched:
                return "ERR fault latched"
            self.armed = True
        elif word == "disarm":
            self.armed, self.duty = False, [0.0] * self.nduty
        elif word == "duty":
            if not self.armed:
                return "ERR not armed"
            self.duty = ([float(x) for x in line.split()[1:]] + [0.0] * self.nduty)[:self.nduty]
        elif word == "clear":
            self.fault_latched = None
        elif word == "reset":
            self.boot()
        elif word == "status":
            return 'OK {"sim":true,"armed":%s,"vbus_v":%.3f}' % (
                "true" if self.armed else "false", self.v * self.rng.uniform(0.99, 1.01))
        want = self.hooks.get(word)
        if want and not re.search(want, 'OK {"sim":true}'):
            lit = _example_for(want)
            if re.search(want, lit):
                return lit + ("}" if lit.count("{") > lit.count("}") else "")
        return 'OK {"sim":true}'


    def _boost_command(self, word: str, args: list[str]) -> str:
        if word == "arm":
            if self.fault_latched:
                return "ERR fault latched"
            if self.mode != "off":
                return "ERR state regulating: disarm first"
            self.mode, self.armed = "run", True
            return 'OK {"state":"softstart","target_v":%.1f}' % (self.vout_target or 0)
        if word == "duty":
            if self.fault_latched:
                return "ERR fault latched"
            if self.mode == "run":
                return "ERR state regulating: disarm first"
            if len(args) != self.nduty:
                return "ERR args"
            self.mode, self.armed = "open", True
            self.duty = [float(x) for x in args]
            # the reply goes out first; a trip the new duty causes follows it
            self.rx.append('OK {"state":"open","duty":%.3f}' % self.duty[0])
            self._check_trips()
            return ""
        if word == "disarm":
            self.mode, self.armed, self.duty = "off", False, [0.0] * self.nduty
            return 'OK {"state":"off","outputs_on":false}'
        if word == "clear":
            if self.fault_latched in self.injected:
                return "ERR active comparator still high"
            self.fault_latched = None
            return 'OK {"trips":[]}'
        return ('OK {"sim":true,"state":"%s","outputs_on":%s,"trips":[%s],"vbus_v":%.3f,'
                '"vin_v":%.3f,"vout_v":%.3f,"duty":%.3f}') % (
            self.mode, "true" if self.switching() else "false",
            f'"{self.fault_latched}"' if self.fault_latched else "",
            self.v, self.v, self.vout() * self.rng.uniform(0.995, 1.005), self.duty[0])


def _example_for(regex: str) -> str:
    """A literal line matching a simple anchored regex like '^fwe PCB-0018-A '."""
    lit = regex.lstrip("^").rstrip("$")
    lit = re.sub(r"\\(.)", r"\1", lit)
    return lit.rstrip()


class SimPsu:
    def __init__(self, board: SimBoard):
        self.b = board

    def set(self, v, i_limit, output):
        self.b.supply(float(v), float(i_limit), bool(output))

    def measure(self, quantity):
        return self.b.current() if quantity == "current" else (self.b.v if self.b.on else 0.0)

    def off(self):
        self.b.supply(self.b.v, self.b.ilim, False)


class SimDmm:
    def __init__(self, board: SimBoard):
        self.b = board

    def measure(self, quantity, points):
        return self.b.reading(quantity, points["plus"]["net"], points["minus"]["net"])


class SimScope:
    def __init__(self, board: SimBoard):
        self.b = board
        self.last = None

    def measure(self, channel, quantity, points, channel2=None):
        net = points["plus"]["net"]
        switching = self.b.switching()
        pwm = self.b.pwm_running()
        if pwm and quantity in ("freq", "duty"):
            # the generator's own output: f_rf, and its widest channel's duty
            gw = self.b.pwm_gw
            val = self.b.pwm_f * self.b.rng.uniform(0.9999, 1.0001) if quantity == "freq" \
                else max(gw.duty_on) / gw.n + self.b.rng.uniform(-0.01, 0.01)
        elif quantity == "duty":
            val = max(self.b.duty) if switching else 0.0
            val += self.b.rng.uniform(-0.01, 0.01) if val else 0.0
        elif quantity in ("freq", "deadtime") and not switching:
            val = 0.0
        elif quantity == "freq" and self.b.pwm_hz:
            val = float(self.b.pwm_hz) * self.b.rng.uniform(0.999, 1.001)
        else:
            val = self.b.reading(quantity, net, points["minus"]["net"]) \
                if self.b.powered() else 0.0
        self.last = (quantity, net, val)
        return val

    def screenshot(self, path: Path):
        _sim_png(path, self.last)


class SimLogic:
    def __init__(self, board: SimBoard):
        self.b = board

    def capture(self, channels, samplerate, duration_s):
        n = 12 if self.b.powered() else 0
        return {c: n for c in channels}


class SimConsole:
    def __init__(self, board: SimBoard):
        self.b = board

    def send(self, line):
        reply = self.b.command(line)
        if reply:
            self.b.rx.append(reply)

    def read_until(self, regex, timeout_s):
        pat = re.compile(regex)
        while self.b.rx:
            line = self.b.rx.pop(0)
            if pat.search(line):
                return line
        # nothing buffered: a user button press is the only event left to model
        if self.b.powered() and self.b.flashed and pat.search('EVT {"button":"user"}') \
                and "EVT" in regex:
            return 'EVT {"button":"user"}'
        return None

    def human_done(self, step: dict):
        """The runner tells the sim console what a person just did."""
        if step.get("provoke"):
            self.b.provoke(**step["provoke"])
        if re.search(r"press", step.get("text", ""), re.I) and \
                re.search(r"reset", step.get("text", ""), re.I):
            self.b.reset()


class Pwm8Gateware:
    """The /fwe gateware's register file, byte for byte (fwe rtl/pwm8_ctrl.v).

    A link: write() feeds it the bytes the host sends, read() returns its
    replies. A commit copies the registers to the outputs at once (the real
    core waits for the next period start, 74 ns away).
    """

    def __init__(self, n: int = 56, ch: int = 8, w: int = 4, taps: int = 128):
        self.n, self.ch, self.w, self.taps = n, ch, w, taps
        self.enable = False
        self.reg = {k: [0] * ch for k in (PWM8_PHASE, PWM8_DUTY, PWM8_TRIM, PWM8_FINE)}
        self.duty_on, self.phase_on = [0] * ch, [0] * ch
        self.st, self.is_write, self.addr = 0, False, 0
        self.out = bytearray()

    def _limit(self, kind: int) -> int:       # the largest value a register takes
        return {PWM8_PHASE: self.n - 1, PWM8_DUTY: self.n, PWM8_TRIM: self.n - 1,
                PWM8_FINE: self.taps - 1}[kind]

    def _read(self, addr: int) -> int | None:
        fixed = {0x00: PWM8_ID, PWM8_CTRL: int(self.enable), PWM8_N: self.n,
                 0x03: self.w, 0x04: self.ch, 0x05: self.taps}
        if addr in fixed:
            return fixed[addr]
        kind, k = addr & 0xF8, addr & 0x07
        return self.reg[kind][k] if kind in self.reg and k < self.ch else None

    def _write(self, addr: int, v: int) -> bool:
        if addr == PWM8_CTRL:
            self.enable = bool(v & 1)
            if v & 2:
                self.duty_on, self.phase_on = list(self.reg[PWM8_DUTY]), list(self.reg[PWM8_PHASE])
            return True
        kind, k = addr & 0xF8, addr & 0x07
        if kind in self.reg and k < self.ch and v <= self._limit(kind):
            self.reg[kind][k] = v
            return True
        return False

    def write(self, data: bytes):
        for b in data:
            if self.st == 0:
                if b in (ord("W"), ord("R")):
                    self.is_write, self.st = b == ord("W"), 1
                else:
                    self.out.append(ord("?"))
            elif self.st == 1 and self.is_write:
                self.addr, self.st = b, 2
            elif self.st == 1:
                v = self._read(b)
                self.out.append(ord("E") if v is None else v)
                self.st = 0
            else:
                self.out.append(ord("K") if self._write(self.addr, b) else ord("E"))
                self.st = 0

    def read(self, n: int = 1) -> bytes:
        got, self.out = bytes(self.out[:n]), self.out[n:]
        return got


def _sim_pwm(board: SimBoard):
    if board.pwm_gw is None:
        board.pwm_gw = Pwm8Gateware(n=board.pwm_n)
    return Pwm8(board.pwm_gw, board.pwm_f)


class SdgModel:
    """A Siglent SDG6032X as its SCPI set answers (SDG programming guide):
    BSWV and OUTP per channel, MODE, *IDN?. Enough for ScpiSdg's round trip."""

    def __init__(self):
        self.ch = {c: {"WVTP": "SINE", "FRQ": 1000.0, "AMP": 0.004, "OFST": 0.0,
                       "DUTY": 50.0, "PHSE": 0.0, "out": "OFF", "LOAD": "HZ"} for c in (1, 2)}
        self.mode = "INDEPENDENT"

    def write(self, cmd: str):
        m = re.fullmatch(r"C([12]):(BSWV|OUTP) (.+)", cmd.strip())
        if m:
            c, what, args = self.ch[int(m.group(1))], m.group(2), m.group(3).split(",")
            if what == "OUTP":
                if args[0] in ("ON", "OFF"):
                    c["out"] = args[0]
                if "LOAD" in args:
                    c["LOAD"] = args[args.index("LOAD") + 1]
                return
            for k, v in zip(args[::2], args[1::2]):
                c[k] = v if k == "WVTP" else float(v)
        elif cmd.startswith("MODE "):
            self.mode = cmd.split(" ", 1)[1]
        else:
            raise BenchError(f"sim sdg: no such command {cmd!r}")

    def query(self, cmd: str) -> str:
        if cmd == "*IDN?":
            return "Siglent Technologies,SDG6032X,SIM0000000000,6.01.01.33"
        m = re.fullmatch(r"C([12]):(BSWV|OUTP)\?", cmd.strip())
        if not m:
            raise BenchError(f"sim sdg: no such query {cmd!r}")
        n, c = m.group(1), self.ch[int(m.group(1))]
        if m.group(2) == "OUTP":
            return f"C{n}:OUTP {c['out']},LOAD,{c['LOAD']},PLRT,NOR"
        return (f"C{n}:BSWV WVTP,{c['WVTP']},FRQ,{c['FRQ']:g}HZ,PERI,{1 / c['FRQ']:g}S,"
                f"AMP,{c['AMP']:g}V,OFST,{c['OFST']:g}V,DUTY,{c['DUTY']:g},PHSE,{c['PHSE']:g}")


def _sim_awg(board: SimBoard):
    if board.awg_model is None:
        board.awg_model = SdgModel()
    return ScpiSdg({}, inst=board.awg_model)


class SimProbe:
    def __init__(self, board: SimBoard):
        self.b = board

    def flash(self, manifest, workspace):
        if "no-flash" in self.b.faults or not self.b.powered():
            return False, "sim: target not responding"
        self.b.flashed = True
        self.b.boot()
        return True, "sim: flashed and verified"


def _sim_png(path: Path, last):
    """A small scope-style picture so reports have a real figure to place."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    q, net, val = last or ("vpp", "?", 0.0)
    t = np.linspace(0, 4e-5, 800)
    if q == "duty":
        y = ((t * 25e3) % 1.0 < val).astype(float) * 12.0
    else:
        y = val / 2 * np.sin(2 * np.pi * 400e3 * t)
    fig, ax = plt.subplots(figsize=(4, 2.4), dpi=100)
    ax.plot(t * 1e6, y, lw=0.8)
    ax.set_xlabel("us")
    ax.set_title(f"SIM {net} {q}={val:.3g}", fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


# =================================================================== live


def _visa(resource: str):
    try:
        import pyvisa
    except ImportError as exc:
        raise BenchError("pyvisa is not installed on this host "
                         "(pip install pyvisa pyvisa-py)") from exc
    try:
        return pyvisa.ResourceManager("@py").open_resource(resource, timeout=5000)
    except Exception as exc:
        raise BenchError(f"cannot open {resource}: {exc}") from exc


class ScpiPsu:
    def __init__(self, cfg):
        self.i, self.ch = _visa(cfg["resource"]), int(cfg.get("channel", 1))

    def set(self, v, i_limit, output):
        self.i.write(f"INST:NSEL {self.ch}")
        self.i.write(f"VOLT {float(v):.3f}")
        self.i.write(f"CURR {float(i_limit):.3f}")
        self.i.write(f"OUTP {'ON' if output else 'OFF'}")

    def measure(self, quantity):
        self.i.write(f"INST:NSEL {self.ch}")
        return float(self.i.query("MEAS:CURR?" if quantity == "current" else "MEAS:VOLT?"))

    def off(self):
        self.i.write(f"INST:NSEL {self.ch}")
        self.i.write("OUTP OFF")


class ScpiDmm:
    _Q = {"voltage": "MEAS:VOLT:DC?", "current": "MEAS:CURR:DC?",
          "resistance": "MEAS:RES?", "diode": "MEAS:DIOD?"}

    def __init__(self, cfg):
        self.i = _visa(cfg["resource"])

    def measure(self, quantity, points):
        return float(self.i.query(self._Q[quantity]))


class ScpiScope:
    _Q = {"vpp": "MEAS:VPP? CHAN{c}", "mean": "MEAS:VAVG? CHAN{c}",
          "freq": "MEAS:FREQ? CHAN{c}", "duty": "MEAS:PDUT? CHAN{c}",
          # channel's falling edge to channel2's rising edge, in seconds
          "deadtime": "MEAS:FRD? CHAN{c},CHAN{c2}"}

    def __init__(self, cfg):
        self.i = _visa(cfg["resource"])

    def measure(self, channel, quantity, points, channel2=None):
        v = float(self.i.query(":" + self._Q[quantity].format(c=channel, c2=channel2 or 2)))
        if quantity == "deadtime":
            return v * 1e9
        return v / 100.0 if quantity == "duty" and v > 1.0 else v

    def screenshot(self, path: Path):
        self.i.write(":DISP:DATA? PNG")
        path.write_bytes(bytes(self.i.read_raw())[11:])   # strip the IEEE block header


class SigrokLogic:
    def __init__(self, cfg):
        if not shutil.which("sigrok-cli"):
            raise BenchError("sigrok-cli is not on PATH")
        self.dev = cfg.get("device", "fx2lafw")

    def capture(self, channels, samplerate, duration_s):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "cap.csv"
            subprocess.run(["sigrok-cli", "-d", self.dev, "-c", f"samplerate={samplerate}",
                            "-C", ",".join(channels),
                            "--time", f"{int(duration_s * 1000)}ms", "-O", "csv",
                            "-o", str(out)], check=True, timeout=duration_s + 30)
            rows = [[x.strip() for x in r.split(",")] for r in out.read_text().splitlines()
                    if r and not r.startswith(";")]
        # columns follow the device's channel order, so find each by its header name
        head = rows[0] if rows else []
        edges = {c: 0 for c in channels}
        for c in channels:
            if c not in head:
                raise BenchError(f"sigrok capture has no column {c} (got {head})")
            i = head.index(c)
            col = [r[i] for r in rows[1:] if len(r) > i]
            edges[c] = sum(1 for a, b in zip(col, col[1:]) if a != b)
        return edges


class SerialConsole:
    def __init__(self, cfg):
        try:
            import serial
        except ImportError as exc:
            raise BenchError("pyserial is not installed on this host") from exc
        self.s = serial.Serial(cfg["port"], int(cfg.get("baud", 115200)), timeout=0.1)
        self.buf: list[str] = []

    def send(self, line):
        self.s.write((line + "\n").encode("ascii"))

    def read_until(self, regex, timeout_s):
        pat, end = re.compile(regex), time.monotonic() + timeout_s
        while True:
            while self.buf:
                line = self.buf.pop(0)
                if pat.search(line):
                    return line
            if time.monotonic() > end:
                return None
            raw = self.s.readline()
            if raw:
                self.buf.append(raw.decode("ascii", "replace").rstrip("\r\n"))


# fwe-pwm8-reg/1, the register protocol of /fwe's 8-channel PWM gateware
# (fwe skill/reference/fpga.md, rtl/pwm8_ctrl.v). Bytes at 8N1, not text.
PWM8_ID, PWM8_CTRL, PWM8_N = 0xF8, 0x01, 0x02
PWM8_PHASE, PWM8_DUTY, PWM8_TRIM, PWM8_FINE = 0x10, 0x18, 0x20, 0x28
PWM8_F_TOL = 100e-6     # the PLLs land f_rf 19.75 ppm off on the ECP5 board


def _per_channel(v, ch: int) -> dict[int, float]:
    """A procedure's per-channel value: one number for every channel, or
    {channel: value} with the channel as an int or a JSON string key."""
    if isinstance(v, dict):
        out = {int(k): float(x) for k, x in v.items()}
        bad = [k for k in out if not 0 <= k < ch]
        if bad:
            raise BenchError(f"pwm: no channel {bad} (the generator has 0..{ch - 1})")
        return out
    return {k: float(v) for k in range(ch)}


class Pwm8:
    """The FPGA PWM generator over a byte link (serial live, Pwm8Gateware sim).

    'W' addr value -> 'K' or 'E'; 'R' addr -> the value or 'E'; any other
    first byte -> '?'. Only known addresses are ever read, so a reply byte of
    0x45 is the value, not 'E'. The bitstream fixes the frequency: there is no
    frequency register, so set() refuses an f_hz the generator was not built
    for instead of pretending to tune it (the SDG6032X is the tunable source).
    """

    def __init__(self, link, f_rf_hz: float):
        self.link, self.f_rf = link, float(f_rf_hz)
        ident = self.read(0x00)
        if ident != PWM8_ID:
            raise BenchError(f"pwm: id register reads 0x{ident:02X}, not 0x{PWM8_ID:02X}; "
                             "is the PWM bitstream loaded, R34/R35 fitted as 0R and FT2232H "
                             "port B switched to UART with fixFT2232_ecp5evn?")
        self.n, self.ch = self.read(PWM8_N), self.read(0x04)

    def _reply(self, sent: bytes) -> int:
        self.link.write(sent)
        r = self.link.read(1)
        if len(r) != 1:
            raise BenchError(f"pwm: no reply to {sent!r}")
        return r[0]

    def read(self, addr: int) -> int:
        return self._reply(bytes([ord("R"), addr]))

    def write(self, addr: int, value: int):
        r = self._reply(bytes([ord("W"), addr, value]))
        if r != ord("K"):
            raise BenchError(f"pwm: W 0x{addr:02X} {value} answered {chr(r)!r} "
                             "(read-only, unknown or out of range)")

    def set(self, f_hz=None, duty=None, phase_deg=None, enable=None) -> dict:
        """Write duty and phase, then enable and commit in one ctrl write so
        every channel changes at the same period start (fpga.md, To outphase).
        Returns what landed: values quantised to the N steps of a period."""
        if f_hz is not None and abs(float(f_hz) / self.f_rf - 1) > PWM8_F_TOL:
            raise BenchError(f"pwm: the bitstream fixes f at {self.f_rf:.6g} Hz; "
                             f"{float(f_hz):.6g} Hz needs another build or the awg")
        want = {}
        for k, frac in _per_channel(duty, self.ch).items() if duty is not None else ():
            if not 0 <= frac <= 1:
                raise BenchError(f"pwm: duty {frac} on channel {k} is not within 0..1")
            want[PWM8_DUTY + k] = round(frac * self.n)
        for k, deg in _per_channel(phase_deg, self.ch).items() if phase_deg is not None else ():
            want[PWM8_PHASE + k] = round(deg % 360 / 360 * self.n) % self.n
        for addr, v in want.items():
            self.write(addr, v)
        for addr, v in want.items():
            got = self.read(addr)
            if got != v:
                raise BenchError(f"pwm: 0x{addr:02X} reads back {got}, wrote {v}")
        on = bool(self.read(PWM8_CTRL) & 1) if enable is None else bool(enable)
        self.write(PWM8_CTRL, int(on) | 2)
        for _ in range(10):         # the commit lands at the next period start
            if not self.read(PWM8_CTRL) & 4:
                break
        else:
            raise BenchError("pwm: commit still pending after 10 reads")
        return self.state()

    def state(self) -> dict:
        n = self.n
        return {"f_hz": self.f_rf, "steps_per_period": n,
                "enable": bool(self.read(PWM8_CTRL) & 1),
                "duty": [self.read(PWM8_DUTY + k) / n for k in range(self.ch)],
                "phase_deg": [self.read(PWM8_PHASE + k) * 360 / n for k in range(self.ch)]}

    def off(self):
        self.write(PWM8_CTRL, 0)    # enable off takes effect at once


class _SerialLink:
    def __init__(self, cfg):
        try:
            import serial
        except ImportError as exc:
            raise BenchError("pyserial is not installed on this host") from exc
        self.s = serial.Serial(cfg["port"], int(cfg.get("baud", 115200)), timeout=0.5)
        self.s.reset_input_buffer()

    def write(self, data: bytes):
        self.s.write(data)

    def read(self, n: int = 1) -> bytes:
        return self.s.read(n)


def fwe_pwm8(cfg) -> Pwm8:
    """bench.yaml: pwm: {driver: fwe-pwm8, port: /dev/ttyUSB1, baud: 115200,
    f_rf_hz: 13560267.8} - f_rf_hz is the fpga manifest's clock.f_rf_actual_hz."""
    if "f_rf_hz" not in cfg:
        raise BenchError("pwm: bench.yaml needs f_rf_hz (the fpga manifest's "
                         "clock.f_rf_actual_hz)")
    return Pwm8(_SerialLink(cfg), cfg["f_rf_hz"])


class ScpiSdg:
    """Siglent SDG6000X (the SDG6032X) over pyvisa, in Siglent's own SCPI set:
    Cn:BSWV for the waveform, Cn:OUTP for output and load, MODE PHASE-LOCKED
    so a phase means one channel against the other. Written from the SDG
    programming guide; not yet run on a real SDG6032X (LEARNINGS verify-later).
    """
    _KEYS = {"wave": "WVTP", "f_hz": "FRQ", "vpp": "AMP", "offset": "OFST",
             "duty": "DUTY", "phase_deg": "PHSE"}

    def __init__(self, cfg, inst=None):
        self.i = inst if inst is not None else _visa(cfg["resource"])
        idn = self.i.query("*IDN?")
        if "SDG" not in idn:
            raise BenchError(f"awg: {idn.strip()!r} is not a Siglent SDG")

    def set(self, channel=1, wave="SQUARE", f_hz=None, vpp=None, offset=None,
            duty=None, phase_deg=None, load=None, output=None) -> dict:
        c = int(channel)
        if c not in (1, 2):
            raise BenchError(f"awg: no channel {c} (the SDG6032X has 1 and 2)")
        want = {"wave": str(wave).upper(), "f_hz": f_hz, "vpp": vpp, "offset": offset,
                "duty": None if duty is None else float(duty) * 100, "phase_deg": phase_deg}
        args = [f"{self._KEYS[k]},{v if k == 'wave' else format(float(v), 'g')}"
                for k, v in want.items() if v is not None]
        if phase_deg is not None:
            self.i.write("MODE PHASE-LOCKED")
        self.i.write(f"C{c}:BSWV {','.join(args)}")
        if load is not None:
            self.i.write(f"C{c}:OUTP LOAD,{'HZ' if str(load).lower() in ('hz', 'hiz') else int(load)}")
        if output is not None:
            self.i.write(f"C{c}:OUTP {'ON' if output else 'OFF'}")
        got = self.state(c)
        for k, v in want.items():
            if v is None or k == "wave":
                continue
            g = got[k] * (100 if k == "duty" else 1)
            if abs(g - float(v)) > 1e-3 * max(abs(float(v)), 1e-3):
                raise BenchError(f"awg: C{c} {self._KEYS[k]} reads back {g}, set {v}")
        if got["wave"] != want["wave"]:
            raise BenchError(f"awg: C{c} wave reads back {got['wave']}, set {want['wave']}")
        return got

    def state(self, channel: int) -> dict:
        bswv = self.i.query(f"C{channel}:BSWV?").strip().split(" ", 1)[1].split(",")
        kv = dict(zip(bswv[::2], bswv[1::2]))
        num = lambda k: float(re.sub(r"[A-Za-z]+$", "", kv[k])) if k in kv else None  # noqa: E731
        outp = self.i.query(f"C{channel}:OUTP?").strip().split(" ", 1)[1].split(",")
        return {"channel": channel, "wave": kv.get("WVTP"), "f_hz": num("FRQ"),
                "vpp": num("AMP"), "offset": num("OFST"),
                "duty": None if num("DUTY") is None else num("DUTY") / 100,
                "phase_deg": num("PHSE"), "output": outp[0] == "ON",
                "load": outp[outp.index("LOAD") + 1] if "LOAD" in outp else None}

    def off(self):
        for c in (1, 2):
            self.i.write(f"C{c}:OUTP OFF")


class SwdProbe:
    def __init__(self, cfg):
        self.tool = cfg.get("tool", "probe-rs")

    def flash(self, manifest, workspace):
        cmds = manifest.get("flash", {}).get("commands", {})
        if self.tool not in cmds:
            raise BenchError(f"manifest has no flash command for {self.tool}")
        elf = str(Path(workspace) / "firmware" / manifest["artifact"]["elf"])
        argv = [a.replace("{elf}", elf) for a in cmds[self.tool]]
        r = subprocess.run(argv, capture_output=True, text=True, timeout=120)
        return r.returncode == 0, (r.stdout + r.stderr)[-2000:]


LIVE = {"scpi-psu": ScpiPsu, "scpi-dmm": ScpiDmm, "scpi-scope": ScpiScope,
        "scpi-sdg": ScpiSdg, "sigrok": SigrokLogic, "serial": SerialConsole,
        "fwe-pwm8": fwe_pwm8, "swd": SwdProbe}
SIM = {"psu": SimPsu, "dmm": SimDmm, "scope": SimScope, "logic": SimLogic,
       "console": SimConsole, "probe": SimProbe, "pwm": _sim_pwm, "awg": _sim_awg}


class Bench:
    """Role -> driver instance. Built lazily so a run opens only what it uses."""

    def __init__(self, cfg: dict, sim_board: SimBoard | None = None):
        self.cfg, self.sim = cfg, sim_board
        self.name = "sim" if sim_board else cfg.get("bench", "bench")
        self._open: dict = {}

    def role(self, name: str):
        if name not in self._open:
            if self.sim is not None:
                self._open[name] = SIM[name](self.sim)
            else:
                rc = self.cfg.get("roles", {}).get(name)
                if not rc:
                    raise BenchError(f"bench has no instrument for role {name!r}")
                drv = LIVE.get(rc.get("driver"))
                if drv is None:
                    raise BenchError(f"unknown driver {rc.get('driver')!r} for {name}")
                self._open[name] = drv(rc)
        return self._open[name]

    def safe_off(self) -> bool:
        """Supply outputs off, then the outputs of any drive source this run
        opened (pwm, awg); True when a supply was reachable."""
        try:
            self.role("psu").off()
            ok = True
        except Exception:       # the supply itself failed; the record says so
            ok = False
        for name in ("pwm", "awg"):
            if name in self._open:
                try:
                    self._open[name].off()
                except Exception:   # a dead link: the supply is what made it safe
                    pass
        return ok

"""One driver layer: a procedure step names a role, bench.yaml binds the role
to a driver (reference/design.md section 4).

Roles and the calls the runner makes:
  psu      set(v, i_limit, output), measure(quantity) -> float, off()
  dmm      measure(quantity, points) -> float
  scope    measure(channel, quantity, points) -> float, screenshot(path)
  logic    capture(channels, samplerate, duration_s) -> {channel: edges}
  console  send(line), read_until(regex, timeout_s) -> line | None
  probe    flash(manifest, workspace) -> (ok, log)

Drivers: scpi-psu/scpi-dmm/scpi-scope (pyvisa + pyvisa-py), sigrok
(sigrok-cli), serial (pyserial), swd (the manifest's flash command), and sim
for every role. pyvisa and pyserial are imported only when a live driver is
built, so the box - which only ever runs sim - never needs them. The live
drivers are written against the generic SCPI sets and have not met a real
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
                      for s in st["steps"] if s["type"] == "console" and s.get("send")}
        self.faults = faults or []
        self.shorts = {f.split(":", 1)[1] for f in self.faults if f.startswith("short:")}
        self.rail_over = {}
        for f in self.faults:
            if f.startswith("rail:"):
                net, v = f[5:].rsplit("=", 1)
                self.rail_over[net] = float(v)
        self.v, self.ilim, self.on = 0.0, 0.0, False
        self.flashed = False
        self.armed, self.duty = False, [0.0, 0.0, 0.0]
        self.fault_latched = None
        self.rx: list[str] = []

    def set_manifest(self, man: dict | None):
        if man:
            self.man_banner = man.get("uart", {}).get("banner_regex")
            self.uv = man.get("safety", {}).get("vbus_uv_v")

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

    def reading(self, quantity: str, plus: str, minus: str) -> float:
        if quantity == "resistance":
            if plus in self.shorts:
                return self.rng.uniform(0.1, 0.5)
        if quantity == "voltage":
            if not self.powered():
                return self.rng.uniform(0.0, 0.01)
            if plus in self.rail_over:
                return self.rail_over[plus]
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
            self.fault_latched = "uv"
            self.rx.append('EVT {"fault":"uv","vbus_v":%.2f}' % v)

    def current(self) -> float:
        if not self.on:
            return 0.0
        if self.shorts:
            return self.ilim
        base = 0.030 * 12.0 / max(self.v, 1.0) + 0.004
        if self.armed:
            base += 0.01
        return min(self.ilim, base * self.rng.uniform(0.95, 1.05))

    def boot(self):
        self.armed = False
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
        if word == "arm":
            if self.fault_latched:
                return "ERR fault latched"
            self.armed = True
        elif word == "disarm":
            self.armed, self.duty = False, [0.0, 0.0, 0.0]
        elif word == "duty":
            if not self.armed:
                return "ERR not armed"
            self.duty = [float(x) for x in line.split()[1:]] + [0.0] * 3
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

    def measure(self, channel, quantity, points):
        net = points["plus"]["net"]
        switching = self.b.armed and self.b.powered() and max(self.b.duty) > 0
        if quantity == "duty":
            val = max(self.b.duty) if switching else 0.0
            val += self.b.rng.uniform(-0.01, 0.01) if val else 0.0
        elif quantity == "freq" and not switching:
            val = 0.0
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
        if re.search(r"press", step.get("text", ""), re.I) and \
                re.search(r"reset", step.get("text", ""), re.I):
            self.b.reset()


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
          "freq": "MEAS:FREQ? CHAN{c}", "duty": "MEAS:PDUT? CHAN{c}"}

    def __init__(self, cfg):
        self.i = _visa(cfg["resource"])

    def measure(self, channel, quantity, points):
        v = float(self.i.query(":" + self._Q[quantity].format(c=channel)))
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
        "sigrok": SigrokLogic, "serial": SerialConsole, "swd": SwdProbe}
SIM = {"psu": SimPsu, "dmm": SimDmm, "scope": SimScope, "logic": SimLogic,
       "console": SimConsole, "probe": SimProbe}


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
        """Supply outputs off; True when a supply was reachable."""
        try:
            self.role("psu").off()
            return True
        except Exception:       # the supply itself failed; the record says so
            return False

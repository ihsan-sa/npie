# Where the npie runner runs

The runner (`npie_run.py`) is the only part of npie that touches hardware.
Where it runs decides what path exists between the box and the bench.

| Host | How the instruments attach | What it opens | Verdict |
|---|---|---|---|
| **Owner's laptop** | USB/LAN to the laptop, as they are today | nothing new: the laptop already reaches the box over SSH; run records go back by `git push` to the boards repo | **default** |
| The box directly | USB cables to the box, or instruments on the box's LAN | a USB device path and lab hosts on the box's network | refused without owner approval for bench instruments; the box is headless and far from the bench |
| The server, for a USB-first board | the board's own USB-C into the server, or the board on Wi-Fi | the board's CH340 (found by VID:PID) and a TCP connection to the board | allowed by the owner's flow ("plug it into a laptop or the server"); the server is set up by its owner, never by npie (below) |
| A Pi (or any small host) at the bench | USB/LAN to the Pi | a new host on the network and, for the box to drive it, an SSH path or tunnel box -> Pi | a later option, owner approval first |
| A custom adapter board | one USB device carrying PSU switch, DMM mux, SWD and UART | a USB device on whichever host it plugs into | a later option for repeat testing (a bed-of-nails fixture); not needed for one-off bring-up |

## The default, in practice

On the laptop:

```
git clone https://github.com/ihsan-sa/npie && git clone <boards>
python -m venv .venv && .venv/bin/pip install -r npie/requirements.txt
# optional: sigrok-cli, probe-rs or openocd on PATH
cp <board>/bringup/bench.example.yaml <board>/bringup/bench.yaml   # fill in resources
export NPIE_BENCH_HOST=1      # this host may open instruments; the box never sets it
npie_run.py start --workspace <board> --bench <board>/bringup/bench.yaml
```

A Claude Code session on the laptop can drive the run and relay human
steps; without one, the owner runs the same commands. Either way the run
directory is committed to the boards repo and pushed, and the box reads it
from there (report, review, next steps). The box never holds a handle on an
instrument.

`--dry-run` (the simulated bench) runs anywhere, the box included, because
it opens nothing.

## A USB-first board on any host

A board like the hi-fi amp (an ESP32 behind a CH340 on its own USB-C) needs
no instrument: the host runs the procedure over the board's USB, then over
TCP once the board is on Wi-Fi. A laptop and the server run the same code.
What a host needs before it can be this board's bench host, none of which
npie sets up:

- this repo, the boards repo and `pip install -r requirements.txt` plus
  `pip install esptool` (only a host that flashes needs esptool);
- `NPIE_BENCH_HOST=1` in the environment of the session that runs
  `npie_run.py`, and nowhere else;
- permission to open the CH340's serial device (on Linux the user in
  `dialout`, or a udev rule for `1a86:7523`); the CH340 driver is in Linux
  and macOS, Windows may need WCH's;
- for the PD stage on a one-port board, a route to the board's Wi-Fi
  address on the console port (3333 by default);
- bench.yaml with `esp32: {driver: esp32, port: auto}`; `serial:` when two
  boards share the host.

## What needs the owner

Wiring any row other than the default, and any change that lets the box
reach the laptop's hardware (a reverse tunnel, a VISA-over-LAN route, a USB
redirector), is on the owner's approval list. npie does not ship scripts for
those paths; a session that wants one asks first.

# npie design - bring-up and test engineer

npie takes a board hwde designed and /fwe programmed, and brings it up on a
bench: it writes a staged procedure from the design, drives the instruments
through one driver layer, stops for a person wherever hands are needed, and
leaves a dated record and a report PDF in the board's workspace. The name is
the owner's: an NPI (new product introduction) engineer.

This file is the design. `procedure-schema.md` is the procedure and record
format, `bench-hosts.md` is where the runner lives, `SKILL.md` is the
playbook a session follows.

## 1. What it reads and writes

Reads, from an hwde board workspace (`$HWDE_BOARDS_ROOT/<board>`, default `~/dev/boards`), never
editing any of it:

| Input | Used for |
|---|---|
| `kicad/<board>.net` | nets, parts on each net, test points, connectors, the SWD/UART pinouts |
| `kicad/constraints.json` | `voltages[]` (rail nominals), `power[]` (design currents per rail) |
| `fab/BOM.csv` | values of the parts a step names (fuse ratings, LED colours) |
| `requirements.md` | the operating range (VM min/max) when constraints does not carry it |
| `firmware/fwe-manifest.json` | flash command, UART protocol, test hooks, safety limits (/fwe's contract, schema `fwe-manifest/1`) |

Writes, all under `<board>/bringup/`:

```
bringup/
  procedure.json        generated; the machine-readable procedure (schema npie-procedure/1)
  procedure.md          the same procedure for a person at the bench
  shopping.md           equipment and parts the procedure needs
  bench.yaml            which instrument fills which role on this bench (hand-edited, per bench)
  runs/<YYYYMMDD-HHMMSS>-<label>/
    run.json            every step: measurements, verdict, who confirmed, timestamps
    captures/           scope screenshots, logic captures, console logs
    report.pdf          the run's report (house style)
```

`procedure.json` is regenerated whenever the design or the manifest changes;
it carries the sha256 of every input, so a run records exactly which design
it tested and `npie_run.py` refuses a procedure whose inputs changed since
it was generated (regenerate, then run).

## 2. The procedure: stages, steps, limits

A procedure is a list of stages in a fixed order; a stage that fails stops
the run (later stages assume the earlier ones held). The stages, and where
each limit comes from:

1. **visual** - human steps only: orientation of polarised parts (the
   diodes, electrolytics and ICs the netlist lists), solder bridges on
   fine-pitch ICs, the part number silk. Confirmed by a person.
2. **unpowered** - DMM resistance from every rail net to GND, from input
   to GND, and between power-stage nodes. Limit: above a floor that rules
   out a short (default 100 ohm for logic rails, 1 kohm for the input and
   stage nets, whose bulk capacitors make the reading climb). Diode-mode
   checks where the netlist shows a body diode across two probe points
   (phase -> VM, GND -> phase on a half-bridge): 0.3-0.8 V.
3. **power-up** - current-limited. Supply at the lowest in-range input
   (VM min + 2 V, never below the regulator's VIN min), current limit =
   idle-budget x 2, capped at 0.2 A for a first power-up. Expected idle
   current: the sum of the low-voltage rails' idle draw referred to the
   input through an assumed 80 % efficiency; the upper limit is the limit
   the supply is set to, the lower limit a floor that says something is
   alive (2 mA).
4. **rails** - DC voltage of every regulated rail at its test point or the
   nearest pad the netlist names. Limit: nominal from `voltages[]`, +/-5 %
   for LDO and buck rails, +/-8 % for boost rails and anything without a
   known regulator. Ripple on switching rails by scope when a scope is on
   the bench (limit: 2 % of nominal pk-pk).
5. **programming** - flash with the manifest's command for the probe the
   bench names, then read the boot banner on the console
   (`banner_regex`) and the boot event.
6. **blocks** - the manifest's `test_hooks` (unattended), then per-block
   checks the netlist implies: sense offsets (CSA outputs at mid-supply),
   bus-voltage reading vs the supply setpoint, LEDs and buttons (human),
   Hall/encoder inputs (logic analyser or console), gate drive with no
   motor (a `safe: false` command, so it is preceded by a human step).
7. **full-function** - motor connected, low duty, current limit held; a
   person confirms rotation and the supply current stays under the limit.
8. **limits** - operating range corners (VM min and max from the
   requirements), under-voltage trip from the manifest's `vbus_uv_v`
   (expect the fault event within +/-0.5 V). Nothing above the rated
   maximum input is applied; over-voltage tests beyond it are listed as
   not run, with the reason.

Every measured step carries `expect` = {nominal, min, max, unit} and a
`derived_from` string naming the design fact it came from (`constraints
voltages[+5V]=5`, `power_tree idle`, `manifest safety.vbus_uv_v`), so a
limit that looks wrong can be traced to the input that made it.

Limits are generated, not hand-written; a board whose limits need
adjusting gets `bringup/overrides.yaml` (step id -> expect), which the
generator applies and records.

### Step types

| type | does | needs a person? |
|---|---|---|
| `human` | instruction text; waits for a confirmation (and an optional typed value) | yes |
| `supply` | set a supply channel: voltage, current limit, output on/off | no |
| `measure` | one reading from a DMM/supply/e-load, compared with `expect` | no (probe placement is its own `human` step before it) |
| `scope` | capture a channel, measure (mean, pk-pk, freq, duty), screenshot | no |
| `logic` | sigrok capture, decode, compare (edges seen, frequency) | no |
| `flash` | run the manifest's flash command for the bench's probe | no |
| `console` | send a line, match the reply (`OK <json>`), check JSON fields against limits | no |
| `wait` | settle time | no |

A step that energises something risky (`safe: false` manifest commands, the
first power-up, the motor stage) is always preceded by a `human` step that
states what is about to happen.

Probe placement is its own human step, grouped: consecutive measurements on
the same pair of points share one placement. When a net has no test point,
the step names the nearest pad the netlist gives (`U203 pin 5 (+3V3)`),
preferring a two-pin passive's pad over an IC pin.

## 3. Running: non-interactive, resumable

The runner follows the repo's script contract (argparse, JSON out, exit
0/1/2, no interactivity). A `human` step does not block on stdin: the run
stops with status `awaiting_human` and names the step. The person (or a
session relaying them over Slack) answers, and the run resumes:

```
npie_run.py start   --workspace W (--bench bench.yaml | --dry-run [--hold] [--fault F]) [--label L]
npie_run.py confirm --run R --step S --by NAME [--value V] [--fail "why"]
npie_run.py resume  --run R
npie_run.py status  --run R
```

`start` refuses a procedure whose design inputs (sha256 of each, an input
that appeared or vanished included) differ from the ones it was generated
from, then copies procedure.json into the run directory; the run executes
that snapshot and refuses to go on if it is edited. Only the step the run
waits on can be confirmed. `--by` is required and recorded with the time:
the record says who confirmed each human step. A `--fail` confirmation fails
that step, turns the supply off and ends the run at once. Exit codes: 0 run
passed, 1 a step failed or the run is waiting on a person (the payload's
`status` says which), 2 error or refusal (the run is `aborted` when an
instrument call failed mid-run).

Safety in the runner, independent of the procedure: on any failed step and
on any error it turns the supply outputs off before it stops, and records
that in `safe_state`. A live bench (`--bench`) is refused unless the host
sets `NPIE_BENCH_HOST=1`; only the bench host does (bench-hosts.md), so the
box cannot open an instrument by accident.

`--dry-run` runs against the simulated bench (`SimBoard`, section 4) and
confirms human steps itself as `sim`, so one call runs the whole procedure.
`--hold` makes it stop at human steps like a real bench, for exercising the
confirm path; the simulated board's state is kept in the run directory
(`sim-board.pickle`) between calls and removed when the run ends.
`--fault` injects `short:<net>`, `rail:<net>=<V>`, `no-banner` or
`no-flash`, and must name a net some step probes.

## 4. Instruments: one driver layer

Steps name a **role** (`psu`, `dmm`, `scope`, `awg`, `eload`, `logic`,
`console`, `probe`, `pwm`), never an instrument. `bench.yaml` binds roles to
drivers and addresses:

```yaml
bench: owner-laptop
roles:
  psu:     {driver: scpi-psu, resource: "USB0::0x1AB1::0x0E11::DP8C...::INSTR", channel: 1}
  dmm:     {driver: scpi-dmm, resource: "TCPIP0::192.168.1.50::INSTR"}
  scope:   {driver: scpi-scope, resource: "USB0::0x1AB1::0x04CE::DS1Z...::INSTR"}
  logic:   {driver: sigrok, device: "fx2lafw"}
  console: {driver: serial, port: /dev/ttyUSB0}
  probe:   {driver: swd, tool: probe-rs}
  pwm:     {driver: fwe-pwm8, port: /dev/ttyUSB1, baud: 115200, f_rf_hz: 13560267.8}
  awg:     {driver: scpi-sdg, resource: "TCPIP0::192.168.1.60::INSTR"}
```

Drivers (`scripts/npielib/instruments.py`), each a small class per role:

- **scpi-*** over pyvisa. A generic SCPI set per role (`MEAS:VOLT:DC?`,
  `APPL`, `OUTP`, `:MEAS:VPP?`, `:DISP:DATA?`) with a per-model table for
  the commands that differ (Rigol, Siglent, Keysight). pyvisa-py is the
  backend, so no vendor VISA install is needed.
- **sigrok** through `sigrok-cli` (capture to a file, decode with its
  protocol decoders), not the Python bindings, which are not packaged.
- **serial** through pyserial, speaking the manifest's line protocol
  (`OK`/`ERR`/`EVT`, events accepted between a command and its reply).
- **fwe-pwm8** through pyserial, speaking /fwe's FPGA PWM register
  protocol (`fwe-pwm8-reg/1`, fwe `reference/fpga.md`): per-channel duty
  and phase in steps of the period, enable and commit. `f_rf_hz` comes from
  the fpga manifest's `clock.f_rf_actual_hz`; the bitstream fixes it.
- **scpi-sdg** is the Siglent SDG6032X in Siglent's own SCPI (`Cn:BSWV`,
  `Cn:OUTP`, `MODE PHASE-LOCKED`), every setting read back.
- **swd** runs the manifest's `flash.commands[<tool>]` with `{elf}` filled
  in; the tool comes from bench.yaml.
- **sim** - every role has a simulated driver backed by one `SimBoard`
  model: the supply's setpoint drives rail voltages, the idle current and
  the console, all from the same procedure's expected values, with seeded
  noise so readings vary but pass. Fault injection (`--sim-fault
  rail:+3V3=0` / `short:+5V` / `no-banner`) makes a chosen step fail, so
  the tests cover the failure paths (supply off on fail, stop at stage).
  The pwm and awg sims are protocol models (the gateware's register file
  byte for byte, the SDG's SCPI) under the live driver classes, and the
  simulated scope reads f_rf and duty off the PWM generator while it runs.

The tests run only against `sim`. No driver is ever opened against real
hardware from the box (section 5).

## 5. Where it runs (see bench-hosts.md)

The instruments are at the owner's laptop. The default is that **the
runner runs on the laptop**: a checkout of this repo and the boards repo,
`pip install` of the npie requirements, and `npie_run.py` against a
`bench.yaml` naming the laptop's USB/LAN instruments. The box generates
procedures and reports and reads the pushed run records; it never opens a
path to the laptop's hardware. Other hosts (a Pi at the bench, an adapter
board) and their costs are in `bench-hosts.md`. Any of them that links the
box to lab equipment, a USB device, a network host or a tunnel is on the
owner's approval list and is not wired by this skill.

## 6. Reports

`npie_report.py --run R` builds `report.pdf` in pdf-material-builder's
house style the way hwde's report_gen does (lualatex, housestyle.sty by
path, ASCII-only .tex): board and procedure identity (input hashes), a
summary table per stage, every measured step with expect/actual/verdict,
the human confirmations with who and when, and the scope captures as
figures. A failed or incomplete run still gets a report, marked so on its
first page with the failed step and the safe state it left. lualatex comes
from `NPIE_LUALATEX` else PATH and the style from `NPIE_HOUSE_STYLE` else
pdf-material-builder's skill dir; without either (or `--tex-only`) the
script writes `report.tex` only and exits 1.

`shopping.py` reads the procedure, not the design: each instrument role the
steps use, with the least spec those steps ask of it (supply volts, and amps
with 20 % headroom, meter functions, scope bandwidth 10x the fastest
measured frequency and never under 50 MHz, analyser channels and rate, the
USB-UART's levels, the probe the manifest's flash tool drives), then the
parts, a motor only when the full-function stage is in. Every line names
the steps that need it.

## 7. Firmware contract

npie reads only `firmware/fwe-manifest.json` (schema `fwe-manifest/1`,
owned by /fwe; see its reference/manifest.md). It uses: `flash.commands`,
`flash.connector`, `uart` (connector, pins, baud, banner_regex),
`commands` (names, `safe`), `test_hooks` (send, expect, timeout_s, needs),
`safety` (vbus_uv_v, vbus_ov_v, i_trip_a), `artifact.sha256[<kind>]` (recorded in
the run). A board with no manifest gets a procedure whose programming and
firmware-driven block stages are listed as skipped with that reason.

---
name: npie
description: Bring-up and test engineer for boards hwde designed. From a board workspace (netlist, constraints, requirements, BOM, the /fwe firmware manifest) it writes a staged bring-up and qualification procedure with pass/fail limits derived from the design, runs it through one instrument driver layer (SCPI/pyvisa, sigrok, serial, SWD) with human-confirmed steps, and records every run with a report PDF in the board's bringup/. Runs against a simulated bench anywhere; real instruments only on the bench host (the owner's laptop by default). Invoke via /npie <task> [board].
---

# npie playbook

You are the bench engineer. The procedure is generated from the design, not
written by you: when a limit looks wrong, fix the rule (or the board's
`bringup/overrides.yaml`), never the step by hand. Design and formats:
`reference/design.md`, `reference/procedure-schema.md`,
`reference/bench-hosts.md`.

## Route the task

| The ask | Do |
|---|---|
| "write / update the bring-up procedure for <board>" | `scripts/procedure_gen.py --workspace <board>` -> `bringup/procedure.{json,md}` |
| "what do I need to buy / have on the bench" | `scripts/shopping.py --workspace <board>` -> `bringup/shopping.md` |
| "dry-run it", "check the procedure" | `scripts/npie_run.py start --workspace <board> --dry-run` (simulated bench; runs anywhere; `--fault short:<net>` to see a failure stop it, `--hold` to stop at human steps) |
| "bring the board up", "run the tests" | on the bench host only (`NPIE_BENCH_HOST=1`): `npie_run.py start --workspace <board> --bench bringup/bench.yaml` |
| a person answers a human step | `npie_run.py confirm --run <dir> --step <id> --by <name> [--value V] [--fail "why"]`, then `resume` |
| "where is the run", "what is it waiting on" | `npie_run.py status --run <dir>` |
| "this limit is wrong" | a person adds `<step id>: {min: .., max: ..}` to `bringup/overrides.yaml`, then regenerate |
| "report", "write up the run" | `scripts/npie_report.py --run <dir>` -> `report.pdf` |

Every script follows the repo contract: argparse, JSON on stdout, exit 0
pass / 1 a failed step or a run waiting on a person / 2 error, never
interactive.

## Rules that do not bend

1. **The box never touches lab hardware.** No instrument, USB device, lab
   network host or tunnel is opened from the box; `--dry-run` is the only
   run the box does. Wiring any path from the box to the bench is on the
   owner's approval list: ask, do not script it.
2. **A human step is answered by a named person.** `--by` is recorded with
   the time. You may relay a confirmation the person gave you (Slack, the
   terminal); you never confirm one yourself, and a simulated run records
   its confirmations as `sim`.
3. **Energising is always announced.** The first power-up, any `safe: false`
   firmware command and the motor stage are each preceded by a human step
   saying what is about to happen. The runner turns the supply off on any
   failure after power-up.
4. **Nothing above the rated input.** Limits tests stay inside the
   requirements' operating range; a protection trip above it is listed as
   not applied.
5. **Regenerate after any design or firmware change.** The procedure
   carries its inputs' hashes and the runner refuses a stale one.

## Firmware

npie reads `firmware/fwe-manifest.json` (schema `fwe-manifest/1`, /fwe's
contract) and nothing else of the firmware. No manifest: the programming
and firmware-driven stages are listed as skipped, with the reason, and the
rest of the procedure still runs.

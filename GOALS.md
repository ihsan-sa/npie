# npie goals

npie is a Claude Code skill that brings up and tests a circuit board. It writes a staged bring-up and qualification procedure whose pass/fail limits come from the board's design, runs it through one instrument driver layer, and records every run with a report PDF; it runs on a simulated bench anywhere and opens real instruments only on a marked bench host.

## Standing goals

Drafted by a dispatch run from the paragraph above, for the owner to keep, edit or replace.

- **Limits from the design:** every pass/fail limit in a generated procedure traces to the board's design files.
- **One driver layer:** every instrument, real or simulated, is driven through `npielib/instruments.py`.
- **Every run on record:** every run leaves a report PDF.
- **Safe anywhere:** the whole skill runs on a simulated bench anywhere, and opens real instruments only on a marked bench host.

## Proposed goals

Filed by runs. Each says what done looks like as something a run can check, and keeps its status (open, done, failed, dropped) with the pull requests that moved it. At most three are open at once.

### Land #19, with the newer class-D amp parts recognised

- **Status:** open
- **What:** [#19](https://github.com/ihsan-sa/npie/pull/19) adds USB-first bring-up for ESP32 audio boards: an ESP32-over-USB-serial driver and its sim twin in `skill/scripts/npielib/instruments.py`, a two-stage plan (host USB power, then the PD charger) in `skill/scripts/npielib/procgen.py`, new runner step types, and the firmware self-test contract in `skill/reference/fwe-selftest.md`. Its CI passes, but nobody has reviewed it yet. Before it lands, widen the amp matcher in `procgen.py`, which accepts only `TAS5825M|TPA3245` today, so it also accepts TAS5825P, TAS5828M and TPA3255, each with the same checks as its sibling: TPA3255 like TPA3245, with PVDD up to 28 V, and TAS5825P and TAS5828M I2C-controlled like TAS5825M. Update the part lists in the `procgen.py` docstring and `skill/reference/design.md` to match, and add a case to `tests/test_npie_usb_first.py` whose fixture board uses one of the new parts. Then review the whole pull request against its own description and merge it.
- **Blocked:** the widening has to land on #19's branch (`track/esp32-usb-bringup`), and a dispatch run can't push there or merge it into its own branch (`git merge` is refused). Someone with push access makes the change on that branch, or lets runs run `git merge`. See `.dispatch/notes/21.md`.
- **Done when:** #19 is merged into main; on main, the amp matcher in `skill/scripts/npielib/procgen.py` accepts TAS5825M, TAS5825P, TAS5828M, TPA3245 and TPA3255; a test in `tests/test_npie_usb_first.py` generates the USB-first plan for a board whose amp is one of the three new parts; and `make check` passes on main.

## Later

Goals waiting for an open slot. When a proposed goal closes, the run that closed it moves the top one here up into Proposed goals as open.

## Closed

What has landed, from the git history (a line per PR).

- [#1](https://github.com/ihsan-sa/npie/pull/1) Import /npie from hwde as a standalone skill
- [#2](https://github.com/ihsan-sa/npie/pull/2) Add a make check to npie
- [#3](https://github.com/ihsan-sa/npie/pull/3) Drive the FPGA PWM generator and SDG6032X from npie
- [#4](https://github.com/ihsan-sa/npie/pull/4) Add the RF test system's bring-up procedure and EVN setup
- [#5](https://github.com/ihsan-sa/npie/pull/5) Test PWM, dead time and trips on the 1 MHz GaN boost
- [#6](https://github.com/ihsan-sa/npie/pull/6) Run make check on every pull request and push to main
- [#13](https://github.com/ihsan-sa/npie/pull/13) Add make lint running compileall on skill and tests
- [#14](https://github.com/ihsan-sa/npie/pull/14) Refuse real instrument drivers unless NPIE_BENCH_HOST=1
- [#15](https://github.com/ihsan-sa/npie/pull/15) Handle empty-netlist and no-BOM boards in procedure generation
- [#16](https://github.com/ihsan-sa/npie/pull/16) Name the instrument and call in instrument errors
- [#17](https://github.com/ihsan-sa/npie/pull/17) Test the scope dead-time query on a simulated bench
- [#18](https://github.com/ihsan-sa/npie/pull/18) Add --help smoke tests for every skill script

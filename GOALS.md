# npie goals

npie is a Claude Code skill that brings up and tests a circuit board. It writes a staged bring-up and qualification procedure whose pass/fail limits come from the board's design, runs it through one instrument driver layer, and records every run with a report PDF; it runs on a simulated bench anywhere and opens real instruments only on a marked bench host.

## Standing goals

- **Limits from the design:** every pass/fail limit in a generated procedure traces to the board's design files.
- **One driver layer:** every instrument, real or simulated, is driven through one instrument driver layer.
- **Every run on record:** every run leaves a report PDF.
- **Safe anywhere:** it runs on a simulated bench anywhere, and opens real instruments only on a marked bench host.

## Proposed goals

Filed by runs. Each says what done looks like as something a run can check, and keeps its status (open, done, failed, dropped) with the pull requests that moved it. At most three are open at once.

### Land #19, with the newer class-D amp parts recognised

- **Serves:** One driver layer
- **Status:** open
- **What:** [#19](https://github.com/ihsan-sa/npie/pull/19) adds USB-first bring-up for ESP32 audio boards: an ESP32-over-USB-serial driver and its sim twin in `skill/scripts/npielib/instruments.py`, a two-stage plan (host USB power, then the PD charger) in `skill/scripts/npielib/procgen.py`, new runner step types, and the firmware self-test contract in `skill/reference/fwe-selftest.md`. Its CI passes, but nobody has reviewed it yet. Before it lands, widen the amp matcher in `procgen.py`, which accepts only `TAS5825M|TPA3245` today, so it also accepts TAS5825P, TAS5828M and TPA3255, each with the same checks as its sibling: TPA3255 like TPA3245, with PVDD up to 28 V, and TAS5825P and TAS5828M I2C-controlled like TAS5825M. Update the part lists in the `procgen.py` docstring and `skill/reference/design.md` to match, and add a case to `tests/test_npie_usb_first.py` whose fixture board uses one of the new parts. Then review the whole pull request against its own description and merge it.
- **Blocked:** the widening has to land on #19's branch (`track/esp32-usb-bringup`), and a dispatch run can't push there or merge it into its own branch (`git merge` is refused). Someone with push access makes the change on that branch, or lets runs run `git merge`. See `.dispatch/notes/21.md`.
- **Done when:** #19 is merged into main; on main, the amp matcher in `skill/scripts/npielib/procgen.py` accepts TAS5825M, TAS5825P, TAS5828M, TPA3245 and TPA3255; a test in `tests/test_npie_usb_first.py` generates the USB-first plan for a board whose amp is one of the three new parts; and `make check` passes on main.
- **Pull requests:** none landed yet; the work is [#19](https://github.com/ihsan-sa/npie/pull/19) itself.

### CI compiles every report to a PDF

- **Serves:** Every run on record
- **Done when:** `make check VENV=/home/dispatch-w-npie/venvs/npie` passes with `tests/test_npie_run_report.py` finding that `.github/workflows/check.yml` installs `texlive-luatex` and runs `make check` with `NPIE_REQUIRE_PDF: "1"`, and failing, not skipping, when that is set on a host without lualatex; and the `check` workflow passes on main with it, so each ended run in the report tests has a `report.pdf` that starts `%PDF-`.
- **Status:** open
- **Pull requests:** the PR from `dispatch/53` (no host that runs the checks had lualatex, CI included, so every PDF assertion skipped and no check had ever compiled a report. CI now installs TeX Live and sets `NPIE_REQUIRE_PDF=1`. This PR's own CI run is the first compile, and if it is green on main, the goal is done).

## Later

Goals waiting for an open slot. When a proposed goal closes, the run that closed it moves the top one here up into Proposed goals as open.

## Closed

What has landed, from the git history (a line per PR). A proposed goal that closes moves here whole, after the PR lines, with its final Status.

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
- [#28](https://github.com/ihsan-sa/npie/pull/28) Check every generated limit names its design input
- [#30](https://github.com/ihsan-sa/npie/pull/30) Record the boost trace step in GOALS.md and run notes
- [#34](https://github.com/ihsan-sa/npie/pull/34) Close the limit-trace goal in GOALS.md and add run notes
- [#36](https://github.com/ihsan-sa/npie/pull/36) Write each run's report from npie_run.py when it stops
- [#38](https://github.com/ihsan-sa/npie/pull/38) Close the limit-field goal in GOALS.md and add run notes
- [#40](https://github.com/ihsan-sa/npie/pull/40) Run every generated step on the live drivers; add awg example
- [#42](https://github.com/ihsan-sa/npie/pull/42) Check the bench host at every hardware door, not just Bench
- [#44](https://github.com/ihsan-sa/npie/pull/44) Abort and report a run on Ctrl-C or a bench that won't build
- [#46](https://github.com/ihsan-sa/npie/pull/46) Abort and report a run on SIGTERM or SIGHUP, not just Ctrl-C
- [#48](https://github.com/ihsan-sa/npie/pull/48) Name every number in a procgen limit so the nudge reaches it
- [#50](https://github.com/ihsan-sa/npie/pull/50) Reach the boost idle drop and close the fixture-reach goal
- [#52](https://github.com/ihsan-sa/npie/pull/52) Close the loose-number goal in GOALS.md and add run notes

### Every limit names its design input

- **Serves:** Limits from the design
- **Done when:** `make check` passes with `tests/test_npie_limit_trace.py` generating, from fixture boards only (no boards repo), every stage procgen emits, `power-stage` for a boost included, and finding no step with `expect` or `fields` whose `derived_from` does not point into a design input (a field of the netlist, constraints, BOM, requirements or manifest, such as `manifest commands[duty]` or `constraints voltages[+3V3]`), and the test's `RULE_ONLY` list of limits that are procgen constants (today the short-check floors and the spin current) is empty.
- **Status:** done
- **Pull requests:** [#28](https://github.com/ihsan-sa/npie/pull/28) (the check on the mini-bldc fixture and its variants; seven untraced `derived_from` wordings fixed, plus five on paths no fixture reaches yet); [#30](https://github.com/ihsan-sa/npie/pull/30) (a boost variant of mini-bldc, so the check covers every `power-stage` step and both kinds of trip, all already traced); the PR from `dispatch/33` (the short-check floors and the spin current read from an optional constraints `bringup{}` block, which the fixture carries; without it procgen falls back to its constants and says `procgen default`; `RULE_ONLY` emptied).

### Every run writes its own report

- **Serves:** Every run on record
- **Done when:** `make check VENV=/home/dispatch-w-npie/venvs/npie` passes with `tests/test_npie_run_report.py` dry-running `npie_run.py` to each way a run stops (passed, failed, aborted, waiting on a person, and failed by a person's confirmation), and finding in each run dir a `report.tex` that names the run's status and a `report` entry in the JSON that gives the PDF, or says why there is none (lualatex missing, or the run not ended yet); on a host with lualatex the ended runs must have `report.pdf`.
- **Status:** done
- **Pull requests:** [#36](https://github.com/ihsan-sa/npie/pull/36) (`npie_run.py` calls `npie_report.write()` whenever start, confirm or resume leaves a run: the `.tex` every time, the PDF once the run ends; a report error is a warning and never the run's outcome. Before this, a run left only `run.json` until someone ran `npie_report.py`, and the report tests covered only passed and failed runs).

### Every limit field names its source

- **Serves:** Limits from the design
- **Done when:** `make check VENV=/home/dispatch-w-npie/venvs/npie` passes with `tests/test_npie_limit_fields.py` nudging each numeric procgen constant in turn, regenerating every fixture variant of `tests/test_npie_limit_trace.py`, and finding no step whose `expect` or `fields` moved while its `derived_from` stayed the same, with its `UNDISCLOSED` list empty.
- **Status:** done
- **Pull requests:** the PR from `dispatch/37` (the per-step trace check passed a step on one field's source while another field came from a hidden constant; nudging found nine such steps from `IDLE_FLOOR_A`, `DIODE_V` and `FIRST_ILIMIT_A`, and their `derived_from` now states the alive floor, the diode band and the 90 % current ceiling. Numbers written inline in procgen, not as constants, aren't nudged yet).

### Every generated step runs on the live drivers

- **Serves:** One driver layer
- **Done when:** `make check VENV=/home/dispatch-w-npie/venvs/npie` passes with `tests/test_npie_live_drivers.py` binding each fixture variant's procedure (those of `tests/test_npie_limit_trace.py`, plus a Hall-sensor variant and the pwm/awg stage of `tests/test_npie_rf_instruments.py`) to the bench that procedure_gen's `bench.example.yaml` describes, running every instrument step through `runner.execute` on the `LIVE` drivers over fake transports, and finding no step a live driver refuses, every role in `instruments.SIM` opened live by some variant, and every role in `BENCH_EXAMPLE` bound to a driver in `LIVE`.
- **Status:** done
- **Pull requests:** [#40](https://github.com/ihsan-sa/npie/pull/40) (before it, only the sim twins had ever met a generated step, and they take any quantity the live DMM and scope don't. The check found the bench example had no `awg` line, so a procedure with an awg step got a bench.yaml that couldn't drive it, and `bench_example` dropped such a role without saying so. It now has the line, and an unknown role is written as a comment. No fixture reached the logic step, so the test adds Hall nets).

### Every hardware door checks the bench host

- **Serves:** Safe anywhere
- **Done when:** `make check VENV=/home/dispatch-w-npie/venvs/npie` passes with `tests/test_npie_bench_guard.py` building every driver in `instruments.LIVE` directly, not through `Bench`, from its line in procedure_gen's `BENCH_EXAMPLE`, with `NPIE_BENCH_HOST` unset and pyvisa, pyserial, `subprocess.run` and `shutil.which` faked to fail the test if touched, and finding each one refused with a `BenchError` naming `NPIE_BENCH_HOST`; and with the same file finding, by reading the source of every script under `skill/scripts`, that each call that opens a transport (`open_resource`, `serial.Serial`, `subprocess.run`/`Popen`) sits in a function that calls `_bench_host` before it, except those named in its `NOT_HARDWARE` list (the report's lualatex).
- **Status:** done
- **Pull requests:** [#42](https://github.com/ihsan-sa/npie/pull/42) (before it, only `Bench.role` checked `NPIE_BENCH_HOST`; the drivers opened VISA, serial, sigrok-cli and the flash tool with no check, so a driver built any other way, as tests already do, skipped it. Now `_visa`, a new `_serial`, `SigrokLogic` and `SwdProbe` call `instruments._bench_host` first, and `Bench.role` uses the same check. With the `SwdProbe.flash` check removed, two of the new tests fail).

### No run is left without its report

- **Serves:** Every run on record
- **Done when:** `make check VENV=/home/dispatch-w-npie/venvs/npie` passes with `tests/test_npie_run_orphans.py` driving `npie_run.py` into each way it can stop once a run dir may exist (a `rail:` fault with no volts, a bench that can't be built, Ctrl-C mid-step at start and at resume) and finding each one refused before any run dir exists, or its run aborted with `report.tex` written, the interrupted ones with `safe_state` saying the supply went off and exit 130; and, after every case, no run dir under `bringup/runs` that is still `running` or has no `report.tex`.
- **Status:** done
- **Pull requests:** [#44](https://github.com/ihsan-sa/npie/pull/44) (before it, `--fault rail:+5V=abc` passed `create`, made a run dir and then crashed building the `SimBoard`; a bench that failed to build did the same, since it was opened outside `advance`'s try; and Ctrl-C, not an `Exception`, went past the abort, so the run stayed `running` with no report and, on a real bench, its supply on. Now `create` refuses the fault, `advance` opens the bench inside its try, and an interrupt aborts the run, turns the supply off and saves it before `npie_run.py` writes the report and exits 130. With the interrupt handler removed, both Ctrl-C tests fail).

### A killed run still turns the supply off and writes its report

- **Serves:** Every run on record
- **Done when:** `make check VENV=/home/dispatch-w-npie/venvs/npie` passes with `tests/test_npie_run_orphans.py` starting `npie_run.py --dry-run` in its own process, holding it mid-step, sending it each of `npie_run.STOP_SIGNALS` (SIGTERM and SIGHUP) for real, and finding the process exit with 128 + the signal, its run aborted "interrupted by <signal>" with `safe_state` saying the supply went off and `report.tex` written; and with the signal handlers `npie_run.main` installs put back when it returns.
- **Status:** done
- **Pull requests:** [#46](https://github.com/ihsan-sa/npie/pull/46) (before it, only Ctrl-C took #44's abort path. Python has no handler for SIGTERM or SIGHUP, so a killed process, a job timeout or a dropped ssh session ended `npie_run.py` on the spot: the run stayed `running` with no report and, on a real bench, the supply on. Now `npie_run.main` turns either signal into a `KeyboardInterrupt` subclass for the length of the call, ignores a second one while the run aborts, and exits 128 + the signal. With the handlers not installed, both signal tests fail and leave the run `running`).

### No number hides inline in a limit

- **Serves:** Limits from the design
- **Done when:** `make check VENV=/home/dispatch-w-npie/venvs/npie` passes with `tests/test_npie_limit_fields.py` reading procgen's source and finding no number other than 0 and 1 (and `round()`'s digits) inside a dict literal with a `nominal`, `min`, `max` or `edges_min` key, so the nudge in the same file reaches every number in a limit, with its `UNDISCLOSED` list still empty.
- **Status:** done
- **Pull requests:** [#48](https://github.com/ihsan-sa/npie/pull/48) (the limit-field goal nudged only module constants, and it said so. These numbers were written inline in limits: the 90 % current ceiling (four steps), the -5 %/+1 % of a rail following the input, the +/-5 % firmware vbus reading, the +/-3 % sense-amp offset, the 0.5 +/-0.05 bridge duty, the +/-2 % PWM frequency (two steps), the dead time's +1 ns round-up, the diode's 0.5 V nominal and the Hall check's 2 edges. The last two weren't written in `derived_from` at all. Each is now a constant, `derived_from` prints its value, and the nudge finds every one disclosed).

### Every limit constant is reached by a fixture

- **Serves:** Limits from the design
- **Done when:** `make check VENV=/home/dispatch-w-npie/venvs/npie` passes with `tests/test_npie_limit_fields.py` finding every numeric procgen constant it nudges move some step's `expect` or `fields` in some fixture variant, except those in its `NOT_A_LIMIT` list, which it holds to moving none, and with its `UNREACHED` list empty.
- **Status:** done
- **Pull requests:** [#50](https://github.com/ihsan-sa/npie/pull/50) (the nudge checked only that some constant moved some limit, so a constant no fixture reaches passed unseen, and twelve did. Four set no limit at all (a console duty, a wait, a person's injection wiring) and are now `NOT_A_LIMIT`. Four new fixture variants reach the other eight, and each moves a limit whose `derived_from` names it. `hall` reaches the Hall edge count. `defaults` (no `bringup{}`) reaches the short floors and the spin current. `rails` (a rail rated like the input, a catch diode to +5V, a light enough load that the first current limit isn't clamped) reaches `FOLLOW_TOL`, `TOL_ASYNC` and `EFFICIENCY`. `boost-out` (Q1's drain on its own `/VOUT`) reaches `BOOST_IDLE_DROP_V`. `rails` and `boost-out` also pass the limit-trace check).

### No number hides anywhere in procgen's limit math

- **Serves:** Limits from the design
- **Done when:** `make check VENV=/home/dispatch-w-npie/venvs/npie` passes with `tests/test_npie_limit_fields.py` reading procgen's source and finding no number other than 0 and 1 outside its module constants (round()'s digits, f-string formatting, subscripts and the timing and channel keywords of a step aside), except those in its `ARITHMETIC` list, and with `UNDISCLOSED` and `UNREACHED` still empty; and with `tests/test_npie_limit_trace.py` finding that an EVN header with no `vio_v` gives limits that say `procgen default`.
- **Status:** done
- **Pull requests:** [#52](https://github.com/ihsan-sa/npie/pull/52) (the #48 scan looked only inside limit dicts, but numbers outside them still set limits. These were the setpoint's VIN min + 2 V and half-rating fallback, the first current limit's 2x load and 0.05 A floor, the open-loop duty's 0.05 floor and 0.9 max fallback, and D0's aim at half the over-voltage level. The EVN header's 3.3 V `vio_v` fallback also said `constraints blocks[...] vio_v`, as if the design gave it. The vbus reading and a rail following the input never printed the setpoint they're checked against. Each number is now a constant: the nudge finds each of the eight above move a limit that names it, the fallbacks and the duty floor through two new variants, `fallbacks` and `boost-low`. Four set no limit (`OV_AIM_RATING_FRAC`, `VDDA_DEFAULT_V`, `SPIN_V_DEFAULT`, `UV_UNDERSHOOT_V`) and are `NOT_A_LIMIT`. A missing `vio_v` or `pwm.max_duty` now says `procgen default`).

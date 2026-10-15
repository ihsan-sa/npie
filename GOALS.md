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
- **Pull requests:** [#54](https://github.com/ihsan-sa/npie/pull/54) (no host that runs the checks had lualatex, CI included, so every PDF assertion skipped and no check had ever compiled a report. CI now installs TeX Live and sets `NPIE_REQUIRE_PDF=1`. #54 is merged, but a run can't see CI, so the goal stays open until someone confirms the `check` workflow is green on main).

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
- [#54](https://github.com/ihsan-sa/npie/pull/54) Build every report PDF in CI and fail when lualatex is missing
- [#56](https://github.com/ihsan-sa/npie/pull/56) Show every limit and its source in the run report
- [#58](https://github.com/ihsan-sa/npie/pull/58) Abort a waiting run when its design changes before resume
- [#60](https://github.com/ihsan-sa/npie/pull/60) Name the rail behind each current limit, not constraints power[]
- [#62](https://github.com/ihsan-sa/npie/pull/62) Close the PDF-on-any-host goal; update docs and run notes
- [#64](https://github.com/ihsan-sa/npie/pull/64) Hold every supply setpoint to the input's rated voltage
- [#67](https://github.com/ihsan-sa/npie/pull/67) Write the report PDF for a run waiting on a person too
- [#69](https://github.com/ihsan-sa/npie/pull/69) Read the VIN range however requirements.md writes it
- [#71](https://github.com/ihsan-sa/npie/pull/71) Refuse an override the runner can't hold a step to
- [#73](https://github.com/ihsan-sa/npie/pull/73) Show every console field's limit in procedure.md
- [#75](https://github.com/ihsan-sa/npie/pull/75) Turn the supply off when a run passes, not only when it fails

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

### Every report shows each limit and where it came from

- **Serves:** Every run on record
- **Done when:** `make check VENV=/home/dispatch-w-npie/venvs/npie` passes with `tests/test_npie_report_limits.py` building `report.tex` for every fixture variant of `tests/test_npie_limit_fields.py` and finding, for each step with `expect` or `fields`, its whole limit (each console field's included) in its Measurements row and its `derived_from` in its row of "Where each limit came from"; and, dry-running a board with `bringup/overrides.yaml`, a first-page callout naming the overridden step with its limit and the generated one it replaced (and, where lualatex is on the host, the PDF built).
- **Status:** done
- **Pull requests:** [#56](https://github.com/ihsan-sa/npie/pull/56) (the report printed only `expect`, so a console step checked by `fields` (the vbus reading, the closed-loop Vout, a trip's outputs) showed its value with no limit. It skipped every human step in Measurements, so a reading a person typed against a limit (the EVN header's pin voltages) wasn't on record at all. No `derived_from` was in it, so a passed report couldn't show that a person, not the design, had set a limit. Each is now in the report, and a person's override is called out on the first page. The new sections are compiled to PDF first in this PR's CI).

### A run never goes on against a changed design

- **Serves:** Limits from the design
- **Done when:** `make check VENV=/home/dispatch-w-npie/venvs/npie` passes with `tests/test_npie_run.py` holding a dry run on a person, changing a design input (`constraints.json` edited, or an `overrides.yaml` added), and finding both `confirm` and `resume` abort the run with exit 2: the error names the changed input, `safe_state` says the supply went off, `report.tex` is written, `confirm` records no answer, and a later `resume` is refused.
- **Status:** done
- **Pull requests:** [#58](https://github.com/ihsan-sa/npie/pull/58) (only `start` checked the design inputs' hashes. A run waiting on a person, which can be days on a real bench, resumed after `constraints.json` changed or an override was added, and went on testing the board against limits the design no longer gave. Now `confirm` and `resume` check them again first and, if any changed, abort the run with the supply off, naming the inputs, so the report records why and a new run starts from the regenerated procedure. With the check off, all four cases of the new test fail).

### Every design source names its entry

- **Serves:** Limits from the design
- **Done when:** `make check VENV=/home/dispatch-w-npie/venvs/npie` passes with `tests/test_npie_limit_fields.py` finding, in every fixture variant, no step whose `derived_from` names a list without its entry (a bare `[]`, as in `constraints power[]`), and the first current limit and the idle current naming the `constraints power[<rail>]` entry they come from; and with `tests/test_npie_limit_trace.py` accepting `constraints <field>[...]` only with an entry in the brackets.
- **Status:** done
- **Pull requests:** [#60](https://github.com/ihsan-sa/npie/pull/60) (the trace regex took `constraints power[` as a design input, so `constraints power[]` passed. The first current limit, and the idle-current ceilings built on it (at power-up, while regulating, and at VIN min and max), said "limit from constraints power[]" and never named the rail. The full load is the heaviest rail's, not the rails' sum, and nothing said that either. They now name the rail and its watts and say "the heaviest rail". A rail that follows the input and the sense-amp offset also had a bare `voltages[]`, and now name their rails. Whether the full load should be a sum is the owner's call, so this PR doesn't change it).

### Every ended run leaves a PDF on any host

- **Serves:** Every run on record
- **Done when:** `make check VENV=/home/dispatch-w-npie/venvs/npie` passes with `tests/test_npie_run_report.py` finding a `report.pdf` that starts `%PDF-` in the run dir of every ended run (passed, failed, aborted, failed by a person), with or without lualatex on the host, and, with lualatex forced missing, a PDF whose xref points at each of its objects and whose text holds the run's status and failed step; and with `tests/test_npie_report_limits.py` finding every step's limit and `derived_from` in the plain report's text for every fixture variant.
- **Status:** done
- **Pull requests:** [#62](https://github.com/ihsan-sa/npie/pull/62) (only a host with lualatex and the house style got a PDF. Anywhere else, which means every bench host without TeX Live and the host that runs these checks, an ended run left `report.tex` and no PDF, and a failed compile left none either. Now `npie_report.write()` falls back to a plain PDF: `plain_text()` turns the `.tex` back into words, and `plain_pdf()` sets them in Courier on A4, in pure Python with no font embedded. Its first line says why it is plain. `ok` stays false and the exit stays 1, so a house-style PDF is still what CI's `NPIE_REQUIRE_PDF` holds to).

### No supply setpoint above the input's rating

- **Serves:** Limits from the design
- **Done when:** `make check VENV=/home/dispatch-w-npie/venvs/npie` passes with `tests/test_npie_rated_input.py` generating every fixture variant of `tests/test_npie_limit_fields.py`, plus a requirements VIN range that tops out above the input's rating in constraints `voltages[]`, one whose first power is already above it, and a board with no input net whose firmware can spin, and finding no `supply` step above the input's rating or the requirements' VIN max; the over-rated corner and first power listed in `skipped` with both numbers; and no supply step at all, full-function included, when there is no input net.
- **Status:** done
- **Pull requests:** [#64](https://github.com/ihsan-sa/npie/pull/64) (procgen took the first power and the VIN corners from requirements.md alone and never compared them with the input's rating in constraints, so "Input 8-40 V operating" on a 30 V-rated input gave a 40 V supply step, against SKILL.md's rule 4. With no input net, full-function spun the motor at a 12 V procgen constant (`SPIN_V_DEFAULT`) that no design input gave. Now `_Builder.over_rating` holds each of those setpoints to the rating, and a setpoint above it is listed as not applied. Full-function needs the power-up stage's setpoint, and `SPIN_V_DEFAULT` is gone).

### A run waiting on a person has its PDF too

- **Serves:** Every run on record
- **Done when:** `make check VENV=/home/dispatch-w-npie/venvs/npie` passes with `tests/test_npie_run_report.py` holding a dry run on a person (`--hold`) and finding, after `start` and after a `confirm` that leaves it waiting, a `report.pdf` starting `%PDF-` whose report names the run as not ended and says what it waits on: the person and the step (with its text) after `start`, a `resume` from the confirmed step after `confirm`.
- **Status:** done
- **Pull requests:** [#67](https://github.com/ihsan-sa/npie/pull/67) (`npie_run.py` built the PDF only once a run ended, so a run left waiting on a person, which on a real bench can be days or for good, had only `report.tex` and no PDF. Now every `start`, `confirm` and `resume` writes the PDF, and a waiting run's first page says it has not ended and names the step, with its text, that waits on a person, or the `resume` it needs after a confirmation. Before this, the callout said only "It stopped at <step>").

### Every way requirements write the VIN range is read

- **Serves:** Limits from the design
- **Done when:** `make check VENV=/home/dispatch-w-npie/venvs/npie` passes with `tests/test_npie_requirements_range.py` finding `design._vin_range` read 8-24 V from each wording in its `READ` list (an ASCII hyphen, an en or em dash, `to`, `..`, a unit on both numbers, `VDC`, and `Operating input: 8-24 V`), no range from each line in `NOT_A_RANGE` (no "operating", not volts, not on one line), and, on the fixture with `Input 8–24 V operating range.`, neither VIN corner skipped and the supply set to 8 V and 24 V by steps that say `requirements VIN min 8 V` and `requirements VIN max 24 V`.
- **Status:** done
- **Pull requests:** [#69](https://github.com/ihsan-sa/npie/pull/69) (requirements.md is prose a person writes, and the range regex took only `A-B V operating` with an ASCII hyphen. An en dash, which editors put in for you, or `8 V to 24 V`, `VDC`, or `Operating input: 8-24 V` gave no range, with no warning: the first power fell back to half the input's rating (`VIN_NO_RANGE_FRAC`) and both VIN corners were skipped as "no operating range", on a board whose design gives one).

### Every override is a limit the runner holds a step to

- **Serves:** Limits from the design
- **Done when:** `make check VENV=/home/dispatch-w-npie/venvs/npie` passes with `tests/test_npie_overrides.py` finding procgen refuse, naming the step, each override in its `REFUSED` list (a key the runner doesn't check, such as `mx` or `unit`, or `min` on a logic step; a value that isn't a finite number: null, `5.2 V`, true, nan, inf; an empty override; a min above the max; a nominal outside them), `procedure_gen.py` exit 2 on one without writing `procedure.json`, and every limit of every fixture variant of `tests/test_npie_limit_fields.py` accepted unchanged as its own override.
- **Status:** done
- **Pull requests:** [#71](https://github.com/ihsan-sa/npie/pull/71) (`bringup/overrides.yaml` is the one way a person, not the design, sets a limit, and procgen merged whatever it held into `expect`. A misspelt `mx: 5.2` left the generated max in force while the report called the step overridden. `max: null` dropped the bound, so any reading passed. `max: 5.2 V` reached the runner as a string and aborted the run at that step on the bench. `{}` was recorded as an override, and a min above the max failed every reading. `procgen._override_error` now refuses each, naming the step).

### Every limit is in the procedure a person follows

- **Serves:** Limits from the design
- **Done when:** `make check VENV=/home/dispatch-w-npie/venvs/npie` passes with `tests/test_npie_procedure_md.py` rendering procedure.md for every fixture variant of `tests/test_npie_limit_fields.py` and finding each step in exactly one four-cell table row whose From cell is its `derived_from` and, for each step with `expect` or `fields`, whose Limit cell holds every bound and unit of its `expect` and of each console field, by the field's name; and a manifest banner regex with a `|` in it still giving that step one four-cell row with the whole regex in it.
- **Status:** done
- **Pull requests:** [#73](https://github.com/ihsan-sa/npie/pull/73) (procedure.md, the page a person follows at the bench, printed only `expect`, so a console step checked by `fields` (the firmware's vbus reading, the closed-loop Vout, the OVP and OCP trips' `outputs_on`) had an empty Limit cell, which #56 had fixed in the report but not here. A `|` in a manifest regex, such as `^(fwe mini|fwe boot) `, split its row into five cells, so the regex was cut in two and the From column shifted. `render.step_limit_text` now prints each field's limit by name, and each cell escapes `|`. With the render change stashed, 11 of the 12 new tests fail).

### Every ended run leaves the supply off

- **Serves:** Safe anywhere
- **Done when:** `make check VENV=/home/dispatch-w-npie/venvs/npie` passes with `tests/test_npie_run_safe_end.py` finding that a board with no operating range in requirements.md gives a procedure whose last supply step leaves the output on, and, dry-running to `passed` every fixture variant of `tests/test_npie_rated_input.py` whose dry run passes plus that board and the RF drive stage of `tests/test_npie_rf_instruments.py`, the simulated supply off with `safe_state` saying so, and the pwm generator and both awg channels the RF run drove off.
- **Status:** done
- **Pull requests:** [#75](https://github.com/ihsan-sa/npie/pull/75) (the runner turned the outputs off only when a run failed or aborted. A passed run left them as its last step did, and procgen's only end-of-run supply-off is in the limits stage, which a board with no operating range skips. So a passed run on such a board, or one after a stage that drives the pwm or awg, left the board powered and driven, on a real bench perhaps with nobody there. `runner.Run.finish` now calls `safe_off` for every ended run. With the runner change stashed, 13 of the 14 new tests fail, all but the one that checks the procedure).

### Every fixture variant passes a dry run

- **Serves:** Safe anywhere
- **Done when:** `make check VENV=/home/dispatch-w-npie/venvs/npie` passes with `tests/test_npie_run_safe_end.py` dry-running to `passed` every fixture variant of `tests/test_npie_rated_input.py`, with no exclusion list, and `tests/test_npie_dry_run.py` finding no rails, programming or blocks stage (and no flash or console step) in a procedure with no power-up stage, and in every boost variant an open-loop D0 whose Vin/(1-D0) is below safety.vout_ov_v, each output-voltage trip's threshold and the output net's rating.
- **Status:** done
- **Pull requests:** the PR from `dispatch/76` (#75's test left out three variants whose dry runs failed. A board procgen never powers (no input net, or a first power above the input's rating) still got a programming stage, which says "the board stays powered from the supply", and a blocks stage that talks to its firmware, so the run failed at the flash. Both stages now need the power-up stage, as rails already did. A boost's open-loop D0 was aimed at half `safety.vout_ov_v` and nothing else, so `fallbacks` (vout_ov_v 400 V, ovp trip at 22 V, output rated 30 V) asked for 150 V at D0. The trip fired and the scope steps after it read a stopped bridge. On a board with no hardware trip, that output would have gone past its rating. `procgen._ov_level` now gives the lowest of the three, for the power stage and for the limits stage's under-voltage check. On the `boost` fixture, D0 drops from 0.167 to 0.091 (ovp at 22 V, under vout_ov_v 24 V). `fallbacks` moved to its own 400 V-rated `/VOUT`, so it still reaches `DUTY_MAX_DEFAULT`. With the procgen change stashed, 5 tests fail).

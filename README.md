# npie

A Claude Code skill that brings up and tests a circuit board. Point it at a
board workspace designed with [hwde](https://github.com/ihsan-sa/hwde) and it
writes a staged bring-up and qualification procedure whose pass/fail limits
come from the design (netlist, constraints, requirements, BOM, the firmware
manifest). It then runs that procedure through one instrument driver layer
(SCPI over pyvisa, sigrok, serial, SWD), stops for a person to confirm the
steps only a person can do, and records every run with a report PDF in the
board's `bringup/` directory.

It runs against a simulated bench anywhere. Real instruments are opened only
on a host you mark as the bench host (`NPIE_BENCH_HOST=1`).

## Install

Clone the repo and link (or copy) the `skill/` directory in as a skill:

```
git clone https://github.com/ihsan-sa/npie
ln -s "$PWD/npie/skill" ~/.claude/skills/npie
python -m venv npie/.venv && npie/.venv/bin/pip install -r npie/requirements.txt
```

Then in Claude Code, `/npie <task> [board]`, for example
`/npie write the bring-up procedure for my-board` or `/npie dry-run it`.
The scripts also run by hand; `skill/SKILL.md` lists them.

## Board workspaces

npie works on hwde board workspaces. A board named on the command line is
looked up under `HWDE_BOARDS_ROOT` (default `~/dev/boards`), the same place
hwde keeps them; a path works too. npie only reads the design and writes
under the board's `bringup/`.

## Toolchain

- Python 3.10+ with `requirements.txt` (pyyaml, sexpdata, matplotlib, numpy;
  pyvisa, pyvisa-py and pyserial for real instruments; pytest for the tests).
- LuaLaTeX for the report PDF. Without it, `npie_report.py` still writes
  `report.tex` and says why there is no PDF. The house style
  (`skill/reference/house-style/housestyle.sty`) and its fonts
  (`skill/assets/fonts`, SIL Open Font License) are vendored here, so nothing
  else needs installing. `NPIE_LUALATEX` and `NPIE_HOUSE_STYLE` override
  where each is found.
- On the bench host only, and optional: `sigrok-cli`, and `probe-rs` or
  `openocd` for SWD.

## Tests

```
.venv/bin/python -m pytest tests
```

`make check` does the same with `NPIE_BENCH_HOST` cleared, so it only ever
uses the simulated bench; it builds `.venv` from `requirements.txt` first if needed.

They run against a small synthetic board in `tests/fixtures` and a simulated
bench. The one test that needs a real board from the boards repo is skipped,
with the reason, when that board is not under `HWDE_BOARDS_ROOT`.

## Layout

- `skill/`: `SKILL.md` (the entry point), `scripts/`, `reference/` (design,
  procedure schema, bench hosts), `assets/fonts/`, `LEARNINGS.md`.
- `tests/`: the suite and its fixture.

## License

MIT, see `LICENSE`. The vendored fonts keep their own licenses, in
`skill/assets/fonts`.

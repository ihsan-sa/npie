#!/usr/bin/env python
"""npie_run.py - run a board's bring-up procedure on a bench and record it.

  npie_run.py start   --workspace W (--dry-run [--hold] [--fault F ...] | --bench bench.yaml) [--label L]
  npie_run.py confirm --run R --step S --by NAME [--value V] [--fail "why"]
  npie_run.py resume  --run R
  npie_run.py status  --run R

start refuses a procedure whose design inputs changed since procedure_gen.py
wrote it, snapshots it into <workspace>/bringup/runs/<stamp>-<label>/ and runs
to the first human step, the first failure or the end. A human step never
blocks: the run stops with status awaiting_human; confirm records who
answered (and the reading, when the step asks for one), resume carries on.
confirm --fail fails the step and ends the run.

--dry-run runs against the simulated bench (npielib/instruments.SimBoard): it
opens nothing and runs anywhere. It confirms human steps itself as "sim"
unless --hold, which stops at them like a real bench. --fault injects a
simulated fault (short:<net>, rail:<net>=<V>, no-banner, no-flash).
--bench drives real instruments and refuses unless NPIE_BENCH_HOST=1 is set,
which only the bench host sets (reference/bench-hosts.md): the box never
opens an instrument.

On any failed step or instrument error the supply outputs go off before the
run stops. Prints JSON {ok, run, status, cursor, awaiting?, failed?, ...}.
Exit 0 passed, 1 failed or awaiting a person (status says which), 2 error or
refusal (stale procedure, wrong step, aborted on an instrument error).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import yaml  # noqa: E402

from npielib import design, runner  # noqa: E402


def _out(payload: dict, code: int) -> int:
    print(json.dumps(payload, indent=1))
    return code


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    st = sub.add_parser("start")
    st.add_argument("--workspace", required=True)
    g = st.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--bench")
    st.add_argument("--label")
    st.add_argument("--hold", action="store_true")
    st.add_argument("--fault", action="append", default=[])
    st.add_argument("--seed", type=int, default=1)
    cf = sub.add_parser("confirm")
    cf.add_argument("--run", required=True)
    cf.add_argument("--step", required=True)
    cf.add_argument("--by", required=True)
    cf.add_argument("--value")
    cf.add_argument("--fail")
    for name in ("resume", "status"):
        sub.add_parser(name).add_argument("--run", required=True)
    a = ap.parse_args(argv)

    try:
        if a.cmd == "start":
            if not a.dry_run and (a.hold or a.fault):
                raise runner.RunError("--hold and --fault are for --dry-run only")
            cfg = None
            if a.bench:
                if os.environ.get("NPIE_BENCH_HOST") != "1":
                    raise runner.RunError(
                        "live bench refused: NPIE_BENCH_HOST=1 is not set on this host "
                        "(only the bench host sets it; reference/bench-hosts.md)")
                cfg = yaml.safe_load(Path(a.bench).read_text(encoding="utf-8")) or {}
            run = runner.create(Path(a.workspace), bench="sim" if a.dry_run else
                                cfg.get("bench", "bench"), bench_cfg=cfg, label=a.label,
                                faults=a.fault, hold=a.hold, seed=a.seed)
            run.open_bench(cfg)
            runner.advance(run)
        else:
            run = runner.Run(Path(a.run))
            if not run.sim and os.environ.get("NPIE_BENCH_HOST") != "1":
                raise runner.RunError("live bench refused: NPIE_BENCH_HOST=1 is not set")
            if a.cmd == "confirm":
                # any failed confirmation (--fail or a value out of limits) turns
                # the supply off; the bench is lazy, so opening it here is free
                run.open_bench()
                runner.confirm(run, a.step, a.by, a.value, a.fail)
            elif a.cmd == "resume":
                runner.advance(run)
    except (runner.RunError, design.DesignError, OSError, yaml.YAMLError) as exc:
        return _out({"ok": False, "error": str(exc)}, 2)
    return _out(runner.summary(run), runner.exit_code(run))


if __name__ == "__main__":
    sys.exit(main())

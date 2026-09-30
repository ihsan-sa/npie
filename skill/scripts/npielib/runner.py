"""Execute an npie-procedure/1 against a bench and keep the record (run.json).

The run is a directory, `<workspace>/bringup/runs/<YYYYMMDD-HHMMSS>-<label>/`:
run.json (schema npie-run/1, reference/procedure-schema.md), procedure.json
(the snapshot the run executes, so a later regeneration cannot change a run
half-way) and captures/ (scope screenshots). A simulated run that holds at
human steps also keeps sim-board.pickle there until it finishes, because the
simulated board's state has to survive between invocations the way a real
board's does.

The engine never waits on a person: a human step ends the invocation with
status awaiting_human, `confirm` records the answer and `advance` carries on.
Any failed step or instrument error turns the supply outputs off before the
run stops (reference/design.md section 3).
"""
from __future__ import annotations

import hashlib
import json
import pickle
import time
from datetime import datetime, timezone
from pathlib import Path

from . import design, instruments

SCHEMA = "npie-run/1"
SIM_PICKLE = "sim-board.pickle"


class RunError(Exception):
    """A refusal: stale procedure, wrong step confirmed, bad bench (exit 2)."""


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stale_inputs(proc: dict, ws: Path) -> list[str]:
    """Design inputs whose sha256 differs from the one the procedure recorded,
    including inputs that appeared or vanished since it was generated."""
    cur = design.load(ws).inputs
    names = set(cur) | set(proc.get("inputs", {}))
    return sorted(n for n in names
                  if cur.get(n, {}).get("sha256") != proc.get("inputs", {}).get(n, {}).get("sha256"))


def within(value, e: dict) -> bool:
    if value is None:
        return False
    lo, hi = e.get("min"), e.get("max")
    return (lo is None or value >= lo) and (hi is None or value <= hi)


def _steps(proc: dict) -> list[dict]:
    return [s for st in proc["stages"] for s in st["steps"]]


# ------------------------------------------------------------------ run dir


class Run:
    def __init__(self, rdir: Path):
        self.dir = rdir
        self.rec = json.loads((rdir / "run.json").read_text(encoding="utf-8"))
        self.proc = json.loads((rdir / "procedure.json").read_text(encoding="utf-8"))
        if _sha(rdir / "procedure.json") != self.rec["procedure_sha256"]:
            raise RunError(f"{rdir}/procedure.json changed after the run started")
        self.steps = _steps(self.proc)
        self.ws = Path(self.rec["workspace"])
        self.bench = None

    def save(self):
        (self.dir / "run.json").write_text(json.dumps(self.rec, indent=1) + "\n",
                                           encoding="utf-8")

    @property
    def sim(self) -> bool:
        return self.rec["bench"] == "sim"

    def index(self, sid: str | None) -> int:
        if sid is None:
            return len(self.steps)
        for i, s in enumerate(self.steps):
            if s["id"] == sid:
                return i
        raise RunError(f"step {sid} is not in this run's procedure")

    def record(self, step: dict, **kw) -> dict:
        r = {"id": step["id"], "type": step["type"], "t": now(), **kw}
        if step.get("expect") is not None:
            r["expect"] = step["expect"]
        self.rec["steps"] = [x for x in self.rec["steps"] if x["id"] != step["id"]]
        self.rec["steps"].append(r)
        return r

    # ---- bench

    def open_bench(self, bench_cfg: dict | None = None):
        if self.sim:
            pk = self.dir / SIM_PICKLE
            if pk.is_file():
                board = pickle.loads(pk.read_bytes())
            else:
                board = instruments.SimBoard(self.proc, faults=self.rec.get("faults"),
                                             seed=self.rec.get("seed", 1))
                board.set_manifest(_manifest(self.ws))
            self.bench = instruments.Bench({}, sim_board=board)
        else:
            cfg = bench_cfg if bench_cfg is not None else self.rec["bench_config"]
            self.bench = instruments.Bench(cfg)
        return self.bench

    def keep_sim(self):
        if self.sim and self.bench is not None:
            pk = self.dir / SIM_PICKLE
            if self.rec["status"] in ("running", "awaiting_human"):
                pk.write_bytes(pickle.dumps(self.bench.sim))
            elif pk.exists():
                pk.unlink()

    def safe_off(self, why: str):
        ok = self.bench.safe_off() if self.bench is not None else False
        self.rec["safe_state"] = (f"supply outputs off at {now()} ({why})" if ok
                                  else f"no supply reachable to turn off ({why})")

    def finish(self, status: str, why: str | None = None):
        self.rec["status"] = status
        self.rec["finished"] = now()
        if status != "passed":
            self.safe_off(why or status)


def _manifest(ws: Path) -> dict | None:
    p = ws / "firmware" / "fwe-manifest.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def create(ws: Path, *, bench: str, bench_cfg: dict | None, label: str | None,
           faults: list[str] | None, hold: bool, seed: int = 1) -> Run:
    ws = design.resolve_workspace(str(ws))
    pj = ws / "bringup" / "procedure.json"
    if not pj.is_file():
        raise RunError(f"{pj} missing: run procedure_gen.py first")
    proc = json.loads(pj.read_text(encoding="utf-8"))
    stale = stale_inputs(proc, ws)
    if stale:
        raise RunError("procedure.json is stale (changed since it was generated: "
                       + ", ".join(stale) + "); rerun procedure_gen.py")
    probed = {p["net"] for s in _steps(proc) for p in (s.get("points") or {}).values()}
    for f in faults or []:
        kind, _, arg = f.partition(":")
        net = arg.rsplit("=", 1)[0] if kind == "rail" else arg
        if f in ("no-banner", "no-flash") or (kind in ("short", "rail") and net in probed):
            continue
        raise RunError(f"--fault {f!r}: not a known fault, or names a net no step probes "
                       f"(probed: {', '.join(sorted(probed))})")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    base = ws / "bringup" / "runs" / f"{stamp}-{label or bench}"
    rdir, n = base, 1
    while rdir.exists():
        n += 1
        rdir = base.with_name(f"{base.name}-{n}")
    (rdir / "captures").mkdir(parents=True)
    (rdir / "procedure.json").write_bytes(pj.read_bytes())
    rec = {
        "schema": SCHEMA, "board": proc["board"], "workspace": str(ws),
        "procedure_sha256": _sha(pj), "procedure_generated": proc["generated"],
        "bench": bench, "started": now(), "finished": None,
        "status": "running", "cursor": _steps(proc)[0]["id"], "steps": [],
        "safe_state": None,
    }
    if bench == "sim":
        rec.update(faults=faults or [], seed=seed, hold=hold)
    else:
        rec["bench_config"] = bench_cfg
    (rdir / "run.json").write_text(json.dumps(rec, indent=1) + "\n", encoding="utf-8")
    return Run(rdir)


# ------------------------------------------------------------------ steps


def _check_fields(line: str, fields: dict) -> tuple[bool, dict]:
    """`OK {json}` / `EVT {json}`: each dotted path within its expect."""
    try:
        obj = json.loads(line.split(" ", 1)[1])
    except (IndexError, json.JSONDecodeError):
        return False, {}
    got, ok = {}, True
    for path, e in fields.items():
        v = obj
        for k in path.split("."):
            v = v.get(k) if isinstance(v, dict) else None
        got[path] = v
        ok = ok and isinstance(v, (int, float)) and within(float(v), e)
    return ok, got


def execute(run: Run, s: dict) -> dict:
    """One non-human step -> its record (verdict pass/fail)."""
    t, b = s["type"], run.bench
    if t == "supply":
        b.role("psu").set(**s["set"])
        return run.record(s, verdict="pass", set=s["set"])
    if t == "wait":
        if not run.sim:
            time.sleep(float(s["seconds"]))
        return run.record(s, verdict="pass")
    if t == "measure":
        inst = b.role(s["role"])
        v = inst.measure(s["quantity"]) if s["role"] == "psu" \
            else inst.measure(s["quantity"], s["points"])
        v = float(v)
        return run.record(s, verdict="pass" if within(v, s["expect"]) else "fail",
                          value=v, unit=s["expect"].get("unit", ""))
    if t == "scope":
        sc = b.role("scope")
        v = float(sc.measure(s.get("channel", 1), s["quantity"], s["points"]))
        caps = []
        if s.get("screenshot"):
            p = run.dir / "captures" / f"{s['id']}.png"
            sc.screenshot(p)
            caps.append(str(p.relative_to(run.dir)))
        return run.record(s, verdict="pass" if within(v, s["expect"]) else "fail",
                          value=v, unit=s["expect"].get("unit", ""), captures=caps)
    if t == "logic":
        edges = b.role("logic").capture(s["channels"], s["samplerate"], s["duration_s"])
        need = s["expect"]["edges_min"]
        ok = all(edges.get(c, 0) >= need for c in s["channels"])
        return run.record(s, verdict="pass" if ok else "fail", value=edges)
    if t == "flash":
        man = _manifest(run.ws)
        if man is None:
            raise RunError("flash step but no firmware/fwe-manifest.json")
        ok, log = b.role("probe").flash(man, run.ws)
        return run.record(s, verdict="pass" if ok else "fail", log=log,
                          artifact_sha256=((man.get("artifact") or {}).get("sha256") or {})
                          .get(s.get("artifact", "elf")))
    if t == "console":
        con = b.role("console")
        if s.get("send"):
            con.send(s["send"])
        line = con.read_until(s["expect_re"], float(s.get("timeout_s", 2)))
        ok, got = line is not None, {}
        if ok and s.get("fields"):
            ok, got = _check_fields(line, s["fields"])
        r = run.record(s, verdict="pass" if ok else "fail", value=line)
        if got:
            r["fields"] = got
        return r
    raise RunError(f"step {s['id']}: unknown type {t!r}")


def _sim_value(s: dict):
    v = s.get("value")
    if not v:
        return None
    e = s.get("expect")
    if not e:
        return "sim"
    if e.get("nominal") is not None:
        return e["nominal"]
    lo, hi = e.get("min"), e.get("max")
    return (lo + hi) / 2 if lo is not None and hi is not None else (lo if lo is not None else hi)


def advance(run: Run) -> Run:
    """Run from the cursor to the next human step, the first failure or the end."""
    if run.rec["status"] not in ("running", "awaiting_human"):
        raise RunError(f"run is {run.rec['status']}; start a new one")
    auto = run.sim and not run.rec.get("hold")
    run.rec["status"] = "running"
    if run.bench is None:
        run.open_bench()
    try:
        for s in run.steps[run.index(run.rec["cursor"]):]:
            run.rec["cursor"] = s["id"]
            if s["type"] == "human":
                done = next((r for r in run.rec["steps"] if r["id"] == s["id"]), None)
                if done is None and auto:
                    done = run.record(s, verdict="pass", confirmed_by="sim",
                                      confirmed_at=now(), value=_sim_value(s))
                if done is None:
                    run.rec["status"] = "awaiting_human"
                    return run
                if done["verdict"] == "fail":
                    run.finish("failed", f"{s['id']} failed by {done['confirmed_by']}")
                    return run
                if run.sim:     # tell the simulated board what the person did
                    run.bench.role("console").human_done(s)
                continue
            r = execute(run, s)
            if r["verdict"] == "fail":
                run.finish("failed", f"{s['id']} out of limits")
                return run
        run.rec["cursor"] = None
        missing = [r["id"] for r in run.rec["steps"]
                   if r["type"] == "human" and not r.get("confirmed_by")]
        run.finish("failed" if missing else "passed",
                   "unconfirmed human steps" if missing else None)
        return run
    except Exception as exc:     # any driver error (VISA, bad reply, sigrok, flash) aborts safely
        run.rec["error"] = f"{run.rec['cursor']}: {type(exc).__name__}: {exc}"
        run.finish("aborted", "error")
        return run
    finally:
        run.save()
        run.keep_sim()


def confirm(run: Run, step: str, by: str, value: str | None, fail: str | None) -> Run:
    """Record a person's answer to the human step the run is waiting on."""
    if run.rec["status"] != "awaiting_human":
        raise RunError(f"run is {run.rec['status']}, not waiting on a person")
    if step != run.rec["cursor"]:
        raise RunError(f"the run is waiting on {run.rec['cursor']}, not {step}")
    if not by.strip():
        raise RunError("--by must name who confirmed")
    s = run.steps[run.index(step)]
    rec = {"confirmed_by": by, "confirmed_at": now()}
    verdict = "pass"
    if s.get("value"):
        if value is None and not fail:
            raise RunError(f"step {step} asks for a value ({s['value'].get('unit')}): --value")
        if value is not None and s.get("expect"):
            try:
                num = float(value)
            except ValueError as exc:
                raise RunError(f"--value {value!r} is not a number") from exc
            rec.update(value=num, unit=s["expect"].get("unit", ""))
            verdict = "pass" if within(num, s["expect"]) else "fail"
        elif value is not None:
            rec["value"] = value
    if fail:
        verdict, rec["note"] = "fail", fail
    run.record(s, verdict=verdict, **rec)
    if verdict == "fail":
        run.finish("failed", f"{step} failed by {by}")
    run.save()
    run.keep_sim()
    return run


def summary(run: Run) -> dict:
    rec = run.rec
    out = {"ok": rec["status"] in ("passed", "awaiting_human", "running"),
           "run": str(run.dir), "board": rec["board"], "bench": rec["bench"],
           "status": rec["status"], "cursor": rec["cursor"],
           "done": len(rec["steps"]), "total": len(run.steps),
           "safe_state": rec.get("safe_state")}
    if rec["status"] == "awaiting_human":
        s = run.steps[run.index(rec["cursor"])]
        out["awaiting"] = {"step": s["id"], "text": s["text"], "value": s.get("value")}
    fails = [r for r in rec["steps"] if r["verdict"] == "fail"]
    if fails:
        out["failed"] = fails[-1]
    if rec.get("error"):
        out["error"] = rec["error"]
    return out


def exit_code(run: Run) -> int:
    return {"passed": 0, "aborted": 2}.get(run.rec["status"], 1)

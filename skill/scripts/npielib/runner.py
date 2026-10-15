"""Execute an npie-procedure/1 against a bench and keep the record (run.json).

The run is a directory, `<workspace>/bringup/runs/<YYYYMMDD-HHMMSS>-<label>/`:
run.json (schema npie-run/1, reference/procedure-schema.md), procedure.json
(the snapshot the run executes, so a later regeneration cannot change a run
half-way) and captures/ (scope screenshots). A simulated run that holds at
human steps also keeps sim-board.pickle there until it finishes, because the
simulated board's state has to survive between invocations the way a real
board's does. run.lock is held by the process driving the run (Run.claim).

The engine never waits on a person: a human step ends the invocation with
status awaiting_human, `confirm` records the answer and `advance` carries on.
A run that ends, passed, failed or aborted, turns the supply outputs off,
then any pwm or awg outputs the run drove, before it stops (reference/design.md
section 3). A run resumed from a person reads the supply, the pwm generator
and each awg channel it drove back first, and aborts if one isn't as the
run's last step on it left it.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import pickle
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import fcntl
except ImportError:      # Windows
    fcntl = None
    import msvcrt

from . import design, instruments, procgen

SCHEMA = "npie-run/1"
SIM_PICKLE = "sim-board.pickle"
LOCK = "run.lock"        # held by the process driving the run; the OS lets go when it dies


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


def not_generated(proc: dict, ws: Path) -> list[str]:
    """Where a procedure is not what procedure_gen.py makes from the design
    now: the steps that differ, appeared or vanished, then any other part
    (`stages` for their order or titles). Limits are generated, not
    hand-written (reference/design.md section 2), and the input hashes alone
    don't show a procedure an older npie wrote (say one whose open-loop D0
    sat past a trip) or one edited by hand."""
    gen = json.loads(json.dumps(procgen.generate(design.load(ws))))
    a = {s["id"]: s for s in _steps(proc)}
    b = {s["id"]: s for s in _steps(gen)}
    out = sorted(i for i in set(a) | set(b) if a.get(i) != b.get(i))
    out += sorted(k for k in (set(proc) | set(gen)) - {"generated", "stages"}
                  if proc.get(k) != gen.get(k))

    def shape(p):
        return [(st["id"], st["title"], [s["id"] for s in st["steps"]]) for st in p["stages"]]
    if shape(proc) != shape(gen):
        out.append("stages")
    return out


def _abort_if_stale(run: "Run") -> bool:
    """A run that waited on a person while the design, or the npie that
    generated its procedure, changed would go on against limits they no
    longer give: abort it, supply off."""
    stale = stale_inputs(run.proc, run.ws)
    if stale:
        why = ("design inputs changed since the run started (" + ", ".join(stale) + ")")
    else:
        changed = not_generated(run.proc, run.ws)
        if not changed:
            return False
        why = ("procedure.json is no longer what procedure_gen.py makes from the "
               "design (" + ", ".join(changed) + ")")
    run.rec["error"] = (f"{run.rec['cursor']}: {why}; rerun procedure_gen.py "
                        "and start a new run")
    run.finish("aborted", "design changed")
    return True


def _supply_moved(run: "Run") -> str | None:
    """How the supply differs from what the run's last supply step left it
    at, or None. A run waiting on a person holds no instrument, so another
    run, or a person at the front panel, may have set or turned off its
    supply in the gap; carrying on would test the board at a setpoint the
    procedure never asked for."""
    done = {r["id"]: r for r in run.rec["steps"]}
    left = next((s for s in reversed(run.steps[:run.index(run.rec["cursor"])])
                 if s["type"] == "supply" and s["id"] in done), None)
    if left is None:      # the run hasn't touched the supply yet
        return None
    want, got = left["set"], run.bench.role("psu").state()
    same = bool(got["output"]) == bool(want["output"])
    if same and want["output"]:   # with the output off, the next supply step sets it
        same = (abs(float(got["v"]) - float(want["v"])) <= instruments.PSU_READBACK_V
                and abs(float(got["i_limit"]) - float(want["i_limit"]))
                <= instruments.PSU_READBACK_A)
    if same:
        return None

    def shown(st):
        return f"{float(st['v']):g} V, {float(st['i_limit']):g} A, output {'on' if st['output'] else 'off'}"
    return f"the supply reads {shown(got)}, not {shown(want)} as {left['id']} left it"


def _same(got, want) -> bool:
    """A read-back against the one a step recorded: numbers to the 0.1 % an
    awg setting reads back to (ScpiSdg.set), lists item by item, the rest equal."""
    if isinstance(want, list):
        return isinstance(got, list) and len(got) == len(want) and all(map(_same, got, want))
    if isinstance(want, (int, float)) and not isinstance(want, bool):
        return (isinstance(got, (int, float)) and not isinstance(got, bool)
                and abs(got - want) <= 1e-3 * max(abs(want), 1e-3))
    return got == want


def _drive_moved(run: "Run") -> str | None:
    """How the pwm generator, or an awg channel, differs from what the run's
    last step on it read back (its record's `applied`), or None. Like the
    supply (_supply_moved), a source the run drives is free while it waits on
    a person, and carrying on would drive the board at a duty, phase or
    amplitude the procedure never asked for."""
    left: dict = {}
    for s in run.steps[:run.index(run.rec["cursor"])]:
        if s["type"] in ("pwm", "awg"):
            left[(s["type"], s.get("channel", 1) if s["type"] == "awg" else None)] = s["id"]
    done = {r["id"]: r for r in run.rec["steps"]}
    for (kind, ch), sid in sorted(left.items(), key=lambda kv: run.index(kv[1])):
        want = done.get(sid, {}).get("applied")
        if want is None:
            continue
        got = run.bench.role(kind).state() if kind == "pwm" else run.bench.role(kind).state(ch)
        moved = [k for k in want if not _same(got.get(k), want[k])]
        if moved:
            what = "the pwm generator" if kind == "pwm" else f"awg channel {ch}"
            return (f"{what} reads " + ", ".join(f"{k} {got.get(k)}" for k in moved)
                    + ", not " + ", ".join(f"{want[k]}" for k in moved)
                    + f" as {sid} left it")
    return None


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
        self._lock = None

    def claim(self):
        """Hold the run for this process until release() or the process ends.
        Two processes driving one run would drive one bench; and a run still
        "running" that a process can claim is one whose process died
        (abort_orphaned)."""
        f = open(self.dir / LOCK, "a+", encoding="ascii")
        try:
            f.seek(0)
            if fcntl is not None:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            try:
                f.seek(0)
                pid = f.read().strip()
            except OSError:      # Windows won't read a locked byte
                pid = ""
            f.close()
            raise RunError(f"another npie_run.py process{f' (pid {pid})' if pid else ''} "
                           f"is driving {self.dir.name}; wait for it to stop, or stop it") from None
        f.seek(0)
        f.truncate()
        f.write(f"{os.getpid()}\n")
        f.flush()
        self._lock = f

    def release(self):
        """Let go of the bench's instruments, then of the run."""
        if self.bench is not None:
            self.bench.close()
        f, self._lock = self._lock, None
        if f is None:
            return
        if fcntl is None:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        f.close()

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
        if self.bench is not None:
            # Bench.safe_off turns off the drive sources it has open; a run
            # resumed after a person's step drove them from an earlier process
            for kind in sorted({r["type"] for r in self.rec["steps"]} & {"pwm", "awg"}):
                try:
                    self.bench.role(kind)
                except Exception:   # held by another run, or gone: not ours to turn off
                    pass
        err = self.bench.safe_off() if self.bench is not None else "no bench was opened"
        self.rec["safe_state"] = (f"supply outputs off at {now()} ({why})" if err is None
                                  else f"supply not confirmed off at {now()}: {err} ({why})")

    def finish(self, status: str, why: str | None = None):
        # a passed run too: a procedure need not end with the supply off (its
        # last supply-off step is in the limits stage, which a board without
        # an operating range skips), and nobody may be at the bench when it ends
        self.rec["status"] = status
        self.rec["finished"] = now()
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
    changed = not_generated(proc, ws)
    if changed:
        raise RunError("procedure.json is not what procedure_gen.py makes from this "
                       "design now (written by an older npie, or edited: "
                       + ", ".join(changed) + "); rerun procedure_gen.py")
    probed = {p["net"] for s in _steps(proc) for p in (s.get("points") or {}).values()}
    for f in faults or []:
        kind, _, arg = f.partition(":")
        net = arg.rsplit("=", 1)[0] if kind == "rail" else arg
        if kind == "rail":
            try:
                float(arg.rsplit("=", 1)[1])
            except (IndexError, ValueError):
                raise RunError(f"--fault {f!r}: a rail fault is rail:<net>=<volts>") from None
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
        extra = {"channel2": s["channel2"]} if "channel2" in s else {}
        v = float(sc.measure(s.get("channel", 1), s["quantity"], s["points"], **extra))
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
    if t == "pwm":
        # the driver writes, reads every register back and refuses an f the
        # bitstream was not built for; the record keeps the quantised result
        applied = b.role("pwm").set(**s["set"])
        return run.record(s, verdict="pass", set=s["set"], applied=applied)
    if t == "awg":
        applied = b.role("awg").set(channel=s.get("channel", 1), **s["set"])
        return run.record(s, verdict="pass", set=s["set"], applied=applied)
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
    resumed = run.rec["status"] == "awaiting_human"
    run.rec["status"] = "running"
    try:
        if run.bench is None:     # inside the try: a bench that can't be built aborts the run
            run.open_bench()
        if _abort_if_stale(run):
            return run
        moved = (_supply_moved(run) or _drive_moved(run)) if resumed else None
        if moved:
            run.rec["error"] = (f"{run.rec['cursor']}: {moved} (changed while the run "
                                "waited on a person); start a new run")
            run.finish("aborted", "bench changed")
            return run
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
    except KeyboardInterrupt as exc:   # Ctrl-C (or a signal npie_run.py turns into one)
        by = f" by {exc}" if str(exc) else ""   # mid-step: supply off and on record, then stop
        run.rec["error"] = f"{run.rec['cursor']}: interrupted{by}"
        run.finish("aborted", "interrupted")
        raise
    finally:
        run.save()
        run.keep_sim()


def abort_orphaned(run: Run) -> bool:
    """A run still "running" that this process could claim was left by one
    that died mid-step with no say (SIGKILL, out of memory, its host went
    down): nothing is known of that step or of the supply since, so abort it,
    supply off, rather than carry on as if the step had not begun."""
    if run.rec["status"] != "running":
        return False
    run.rec["error"] = (f"{run.rec['cursor']}: the npie_run.py process running it stopped "
                        "without ending it (killed, or its host went down)")
    try:
        if run.bench is None:
            run.open_bench()
    except Exception as exc:     # safe_state then says the supply is not confirmed off
        run.rec["error"] += f"; bench not opened: {type(exc).__name__}: {exc}"
    run.finish("aborted", "process lost")
    run.save()
    run.keep_sim()
    return True


def confirm(run: Run, step: str, by: str, value: str | None, fail: str | None) -> Run:
    """Record a person's answer to the human step the run is waiting on."""
    if run.rec["status"] != "awaiting_human":
        raise RunError(f"run is {run.rec['status']}, not waiting on a person")
    if step != run.rec["cursor"]:
        raise RunError(f"the run is waiting on {run.rec['cursor']}, not {step}")
    if not by.strip():
        raise RunError("--by must name who confirmed")
    if not run.sim and by.strip().lower() == "sim":     # the record would read as a simulated run's
        raise RunError("--by sim is what a simulated run records; on a live run name the "
                       "person who did the step")
    if _abort_if_stale(run):     # the step's text and limit may no longer be the design's
        run.save()
        run.keep_sim()
        return run
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
            if not math.isfinite(num):     # a typo, not a reading; and NaN isn't JSON
                raise RunError(f"--value {value!r} is not a finite number")
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

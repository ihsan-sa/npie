#!/usr/bin/env python
"""npie_report.py - a bring-up run's report PDF, in the house style.

  npie_report.py --run <run dir> [--tex-only]

Reads <run>/run.json and <run>/procedure.json (npie_run.py writes both) and
writes <run>/report.tex and <run>/report.pdf: the run's identity (board,
bench, dates, procedure and firmware hashes, simulated faults), a summary
per stage, every measured step with its limits, value and verdict, every
human step with who confirmed it and when, the skipped stages with their
reasons, and the scope captures as figures. A failed, aborted or unfinished
run still gets a report; its first page says so and names the step it
stopped at and the safe state it left.

Set in pdf-material-builder's house style: housestyle.sty used by path
(NPIE_HOUSE_STYLE, else the copy vendored in reference/house-style beside
these scripts), lualatex from NPIE_LUALATEX else PATH, two passes staged in a
temp dir so only report.pdf lands in the run. The .tex is pure ASCII.

Prints JSON {ok, run, status, tex, pdf, warnings}.
Exit 0 PDF written; 1 degraded (--tex-only, or lualatex / the house style
missing or the compile failed: report.tex written, no PDF); 2 error (no
run.json or procedure.json). Never interactive.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PDF_TIMEOUT = 300  # seconds per lualatex pass
_ESC = {"\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "$": r"\$", "&": r"\&",
        "#": r"\#", "%": r"\%", "_": r"\_", "^": r"\^{}", "~": r"\~{}",
        "<": r"\textless{}", ">": r"\textgreater{}", "|": r"\textbar{}"}


def esc(x) -> str:
    """Any value -> LaTeX-safe ASCII (non-ASCII becomes '?')."""
    if x is None:
        return ""
    return "".join(_ESC.get(c, c if 32 <= ord(c) < 127 else ("?" if ord(c) > 127 else " "))
                   for c in str(x))


def num(v) -> str:
    return f"{v:.6g}" if isinstance(v, (int, float)) and not isinstance(v, bool) else esc(v)


def limits(e: dict | None) -> str:
    if not e:
        return ""
    if "edges_min" in e:
        return f">= {e['edges_min']} edges"
    u = e.get("unit") or ""
    lo, hi = e.get("min"), e.get("max")
    if lo is not None and hi is not None:
        s = f"{num(lo)} .. {num(hi)}"
    elif lo is not None:
        s = f">= {num(lo)}"
    elif hi is not None:
        s = f"<= {num(hi)}"
    else:
        s = "-"
    return f"{s} {esc(u)}".strip()


def sid(x) -> str:
    """A step id, never hyphenated across lines."""
    return r"\mbox{" + esc(x) + "}"


def table(colspec: str, head: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return r"\emph{(none)}" + "\n"
    out = [r"{\small", r"\begin{longtable}{" + colspec + "}", r"\hstoprule",
           " & ".join(r"\hshead{" + h + "}" for h in head) + r" \\", r"\hline", r"\endhead"]
    out += [" & ".join(r) + r" \\" for r in rows]
    return "\n".join(out + [r"\hline", r"\end{longtable}", "}"]) + "\n"


def build_tex(rdir: Path, run: dict, proc: dict) -> str:
    steps = {st["id"]: (s, st) for s in proc["stages"] for st in s["steps"]}
    got = {r["id"]: r for r in run["steps"]}
    status = run["status"]
    sim = run["bench"] == "sim"
    npass = sum(r.get("verdict") == "pass" for r in run["steps"])
    nfail = sum(r.get("verdict") == "fail" for r in run["steps"])
    human = [r for r in run["steps"] if r["type"] == "human"]

    B = [r"\hstitleblock{Bring-up report" + (" (simulated bench)" if sim else "") + "}{"
         + esc(run["board"]) + "}{Run " + esc(rdir.name) + ": " + esc(status)
         + f", {npass} of {len(steps)} steps passed.}}"]
    if status != "passed":
        fails = [r["id"] for r in run["steps"] if r.get("verdict") == "fail"]
        why = run.get("error") or ("" if fails or not run.get("cursor")
                                   else f"It stopped at {run['cursor']}")
        B.append(r"\begin{hscallout}{Not a pass}" + "This run is " + esc(status)
                 + (". Failed: " + esc(", ".join(fails)) if fails else "")
                 + (". " + esc(why) if why else "") + ". Safe state: "
                 + esc(run.get("safe_state") or "none recorded") + r".\end{hscallout}")
    B.append(r"\begin{hsstatrow}[4]" + "".join(
        r"\hsstat{" + str(v) + "}{" + k + "}" for k, v in
        [("steps passed", npass), ("steps failed", nfail),
         ("steps not reached", len(steps) - len(got)), ("human confirmations", len(human))])
        + r"\end{hsstatrow}")

    flash = next((r for r in run["steps"] if r["type"] == "flash"), {})
    ident = [["Board", esc(run["board"])], ["Bench", esc(run["bench"])],
             ["Started", esc(run["started"])], ["Finished", esc(run.get("finished") or "-")],
             ["Procedure", esc(run["procedure_sha256"][:16]) + " (generated "
              + esc(run.get("procedure_generated")) + ")"],
             ["Firmware", esc((flash.get("artifact_sha256") or "not flashed")[:16])]]
    ident += [[esc(n), esc(v.get("sha256", "")[:16])] for n, v in proc.get("inputs", {}).items()]
    if sim:
        ident += [["Simulated faults", esc(", ".join(run.get("faults") or []) or "none")],
                  ["Seed", esc(run.get("seed"))]]
    B += [r"\section{Run}", table(r"p{0.25\linewidth}p{0.65\linewidth}", ["Item", "Value"], ident)]

    rows = []
    for s in proc["stages"]:
        rs = [got.get(st["id"]) for st in s["steps"]]
        rows.append([esc(s["id"]), str(len(rs)), str(sum(bool(r) and r.get("verdict") == "pass" for r in rs)),
                     str(sum(bool(r) and r.get("verdict") == "fail" for r in rs)),
                     str(sum(r is None for r in rs))])
    B += [r"\section{Stages}", table("lrrrr", ["Stage", "Steps", "Pass", "Fail", "Not run"], rows)]
    if proc.get("skipped"):
        B.append(r"\noindent Not in this procedure: " + esc("; ".join(
            f"{k['stage']} ({k['reason']})" for k in proc["skipped"])) + ".\n")

    rows = []
    for i, (_, st) in steps.items():
        if st["type"] in ("human", "wait", "supply"):
            continue
        r = got.get(i, {})
        val = r.get("value")
        if isinstance(val, dict):
            val = ", ".join(f"{k}={v}" for k, v in val.items())
        elif r.get("fields"):
            val = ", ".join(f"{k}={num(v)}" for k, v in r["fields"].items())
        what = st.get("text") or (st.get("send") and "reply to " + st["send"])
        rows.append([sid(i), esc(what or st.get("quantity") or st["type"]),
                     limits(st.get("expect")), num(val) + " " + esc(r.get("unit") or ""),
                     esc(r.get("verdict", "not run"))])
    B += [r"\section{Measurements}",
          table(r"p{0.17\linewidth}p{0.31\linewidth}p{0.18\linewidth}p{0.14\linewidth}l",
                ["Step", "What", "Limits", "Value", "Verdict"], rows)]

    rows = [[sid(r["id"]), esc(steps.get(r["id"], (0, {}))[1].get("text", "")),
             esc(r.get("confirmed_by")), esc(r.get("confirmed_at")),
             esc(r.get("verdict")) + (": " + esc(r["note"]) if r.get("note") else "")]
            for r in human]
    B += [r"\section{Human steps}",
          (r"\noindent A simulated run confirms its own human steps as \texttt{sim}." + "\n"
           if sim else ""),
          table(r"p{0.17\linewidth}p{0.37\linewidth}p{0.1\linewidth}p{0.16\linewidth}l",
                ["Step", "Action", "By", "At", "Result"], rows)]

    caps = [(r["id"], c) for r in run["steps"] for c in r.get("captures") or []
            if (rdir / c).is_file()]
    if caps:
        B.append(r"\section{Scope captures}")
        for i, c in caps:
            st = steps.get(i, (0, {}))[1]
            B.append(r"\begin{hsfigure}{" + esc(i) + "}{" + esc(st.get("text") or "")
                     + " (" + esc(c) + r")}\hsdiagram[width=\linewidth]{"
                     + (rdir / c).resolve().as_posix() + r"}\end{hsfigure}")

    pre = [r"% Generated by npie_report.py - do not hand-edit.",
           r"\documentclass[11pt]{article}", r"\newcommand\hspaper{a4paper}",
           r"\usepackage[nodiagramkit]{housestyle}", r"\usepackage{longtable}",
           r"\hsslug{" + esc(run["board"]) + r" \textperiodcentered\ bring-up}",
           r"\renewcommand{\sectionmark}[1]{\markboth{#1}{}}", r"\hssection{\leftmark}",
           r"\begin{document}", r"\sloppy"]
    tex = "\n".join(pre + B + [r"\end{document}"]) + "\n"
    tex.encode("ascii")  # raises if anything slipped through esc()
    return tex


def lualatex() -> str | None:
    return os.environ.get("NPIE_LUALATEX") or shutil.which("lualatex")


def house_style() -> Path | None:
    d = Path(os.environ.get("NPIE_HOUSE_STYLE")
             or Path(__file__).resolve().parents[1] / "reference" / "house-style")
    return d if (d / "housestyle.sty").is_file() else None


def compile_pdf(tex: Path, engine: str, style: Path) -> str | None:
    """Two passes in a temp dir; moves report.pdf beside the .tex. None on
    success, else why not."""
    env = dict(os.environ, TEXINPUTS=str(style) + os.pathsep + os.environ.get("TEXINPUTS", ""))
    with tempfile.TemporaryDirectory(prefix="npie_report_") as td:
        for _ in range(2):
            try:
                cp = subprocess.run([engine, "-interaction=nonstopmode", "-halt-on-error",
                                     "-output-directory", td, str(tex)], capture_output=True,
                                    text=True, errors="replace", timeout=PDF_TIMEOUT, env=env)
            except (OSError, subprocess.TimeoutExpired) as exc:
                return f"lualatex {type(exc).__name__}"
            if cp.returncode != 0:
                tail = [l for l in cp.stdout.splitlines() if l.startswith("!")][:1]
                return f"lualatex rc={cp.returncode}: {(tail or [''])[0][:160]}"
        shutil.move(str(Path(td) / (tex.stem + ".pdf")), tex.with_suffix(".pdf"))
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--run", required=True)
    ap.add_argument("--tex-only", action="store_true")
    a = ap.parse_args(argv)
    rdir = Path(a.run).expanduser().resolve()
    try:
        run = json.loads((rdir / "run.json").read_text(encoding="utf-8"))
        proc = json.loads((rdir / "procedure.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": f"not a run dir: {exc}"}))
        return 2
    tex = rdir / "report.tex"
    tex.write_text(build_tex(rdir, run, proc), encoding="ascii")
    warn = []
    if a.tex_only:
        warn.append("--tex-only: no PDF built")
    elif not lualatex():
        warn.append("lualatex not found (set NPIE_LUALATEX)")
    elif not house_style():
        warn.append("housestyle.sty not found (set NPIE_HOUSE_STYLE)")
    else:
        why = compile_pdf(tex, lualatex(), house_style())
        if why:
            warn.append(why)
    pdf = tex.with_suffix(".pdf")
    ok = not warn and pdf.is_file()
    print(json.dumps({"ok": ok, "run": str(rdir), "status": run["status"], "tex": str(tex),
                      "pdf": str(pdf) if ok else None, "warnings": warn}, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

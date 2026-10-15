#!/usr/bin/env python
"""npie_report.py - a bring-up run's report PDF, in the house style.

  npie_report.py --run <run dir> [--tex-only]

Reads <run>/run.json and <run>/procedure.json (npie_run.py writes both) and
writes <run>/report.tex and <run>/report.pdf: the run's identity (board,
bench, dates, procedure and firmware hashes, simulated faults), a summary
per stage, every measured step with its limits (each console field's too),
value and verdict, where each limit came from (its `derived_from`), every
human step with who confirmed it and when, the skipped stages with their
reasons, and the scope captures as figures. A limit a person set in
bringup/overrides.yaml is called out on the first page with the generated
one it replaced. A failed, aborted or unfinished
run still gets a report; its first page says so and names the step it
stopped at and the safe state it left; one waiting on a person says it has
not ended and what it waits on. npie_run.py calls write() itself whenever a
run stops, a wait included, so this script is for a rebuild.

Set in pdf-material-builder's house style: housestyle.sty used by path
(NPIE_HOUSE_STYLE, else the copy vendored in reference/house-style beside
these scripts), lualatex from NPIE_LUALATEX else PATH, two passes staged in a
temp dir so only report.pdf lands in the run. The .tex is pure ASCII.
Where lualatex or the house style is missing, or the compile fails,
report.pdf is a plain one instead: the .tex's words in Courier (captures
named, not drawn), written in pure Python so every host leaves a PDF.
Where report.tex itself can't be built (procedure.json missing or broken,
or a bug here), report.pdf is a plain one of what run.json alone says: the
status, why it stopped, the safe state and each step's result.

Prints JSON {ok, run, status, tex, pdf, warnings}.
Exit 0 house-style PDF written; 1 degraded (--tex-only: report.tex, no PDF;
or lualatex / the house style missing or the compile failed: report.tex and
the plain report.pdf; or report.tex not built: the run.json-only
report.pdf); 2 error (no readable run.json). Never interactive.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

PDF_TIMEOUT = 300  # seconds per lualatex pass
PLAIN_COLS, PLAIN_ROWS = 96, 72  # Courier 8 pt on A4, for the plain PDF
_ESC = {"\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "$": r"\$", "&": r"\&",
        "#": r"\#", "%": r"\%", "_": r"\_", "^": r"\^{}", "~": r"\~{}",
        "<": r"\textless{}", ">": r"\textgreater{}", "|": r"\textbar{}"}
_SAFE_PATH = re.compile(r"[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*")  # a file name TeX reads as is


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


def step_limits(st: dict) -> str:
    """A step's whole limit: its expect and each console field's."""
    parts = [limits(st.get("expect"))] if st.get("expect") else []
    parts += [f"{esc(k)}: {limits(v)}" for k, v in (st.get("fields") or {}).items()]
    return "; ".join(parts)


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
        if status == "awaiting_human":    # not ended: this report is the run so far
            cur = run["cursor"]
            why = (f"It has not ended: {cur} is confirmed and the run waits to be resumed "
                   "from it" if cur in got else f"It has not ended: it waits on a person at "
                   f"{cur} ({steps.get(cur, (0, {}))[1].get('text', '')})")
        B.append(r"\begin{hscallout}{Not a pass}" + "This run is " + esc(status)
                 + (". Failed: " + esc(", ".join(fails)) if fails else "")
                 + (". " + esc(why) if why else "") + ". Safe state: "
                 + esc(run.get("safe_state") or "none recorded") + r".\end{hscallout}")
    if proc.get("overrides"):
        B.append(r"\begin{hscallout}{Limits set by a person}" + "In bringup/overrides.yaml, not "
                 "from the design: " + "; ".join(
                     f"{sid(o['step'])} {limits({**o['generated'], **o['override']})} "
                     f"(generated {limits(o['generated'])})" for o in proc["overrides"])
                 + r".\end{hscallout}")
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
        if st["type"] in ("human", "wait", "supply") and not (st.get("expect") or st.get("fields")):
            continue  # a person's typed reading has a limit, so it is a measurement too
        r = got.get(i, {})
        val = r.get("value")
        if isinstance(val, dict):
            val = ", ".join(f"{k}={v}" for k, v in val.items())
        elif r.get("fields"):
            val = ", ".join(f"{k}={num(v)}" for k, v in r["fields"].items())
        what = st.get("text") or (st.get("send") and "reply to " + st["send"])
        rows.append([sid(i), esc(what or st.get("quantity") or st["type"]),
                     step_limits(st), num(val) + " " + esc(r.get("unit") or ""),
                     esc(r.get("verdict", "not run"))])
    B += [r"\section{Measurements}",
          table(r"p{0.17\linewidth}p{0.31\linewidth}p{0.18\linewidth}p{0.14\linewidth}l",
                ["Step", "What", "Limits", "Value", "Verdict"], rows)]

    rows = [[sid(i), esc(st.get("derived_from") or "not stated")]
            for i, (_, st) in steps.items() if st.get("expect") or st.get("fields")]
    B += [r"\section{Where each limit came from}",
          table(r"p{0.17\linewidth}p{0.75\linewidth}", ["Step", "Source"], rows)]

    rows = [[sid(r["id"]), esc(steps.get(r["id"], (0, {}))[1].get("text", "")),
             esc(r.get("confirmed_by")), esc(r.get("confirmed_at")),
             esc(r.get("verdict")) + (": " + esc(r["note"]) if r.get("note") else "")]
            for r in human]
    B += [r"\section{Human steps}",
          (r"\noindent A simulated run confirms its own human steps as \texttt{sim}." + "\n"
           if sim else ""),
          table(r"p{0.17\linewidth}p{0.37\linewidth}p{0.1\linewidth}p{0.16\linewidth}l",
                ["Step", "Action", "By", "At", "Result"], rows)]

    # a capture is named from the run dir, which lualatex runs in, so the
    # .tex holds nothing of where the run sits on the host
    caps = [(r["id"], c) for r in run["steps"] for c in r.get("captures") or []
            if (rdir / c).is_file()]
    if caps:
        B.append(r"\section{Scope captures}")
        for i, c in caps:
            st = steps.get(i, (0, {}))[1]
            B.append(r"\begin{hsfigure}{" + esc(i) + "}{" + esc(st.get("text") or "")
                     + " (" + esc(c) + ")}" + (r"\hsdiagram[width=\linewidth]{" + c + "}"
                                               if _SAFE_PATH.fullmatch(c) else
                                               r"\emph{(not drawn: its file name)}")
                     + r"\end{hsfigure}")

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
    """Two passes in a temp dir, run from the .tex's own dir so it and the
    captures are named from there; moves report.pdf beside the .tex. None on
    success, else why not."""
    env = dict(os.environ, TEXINPUTS=str(style) + os.pathsep + os.environ.get("TEXINPUTS", ""))
    with tempfile.TemporaryDirectory(prefix="npie_report_") as td:
        for _ in range(2):
            try:
                cp = subprocess.run([engine, "-interaction=nonstopmode", "-halt-on-error",
                                     "-output-directory", td, tex.name], cwd=tex.parent,
                                    capture_output=True, text=True, errors="replace",
                                    timeout=PDF_TIMEOUT, env=env)
            except (OSError, subprocess.TimeoutExpired) as exc:
                return f"lualatex {type(exc).__name__}"
            if cp.returncode != 0:
                tail = [l for l in cp.stdout.splitlines() if l.startswith("!")][:1]
                return f"lualatex rc={cp.returncode}: {(tail or [''])[0][:160]}"
        shutil.move(str(Path(td) / (tex.stem + ".pdf")), tex.with_suffix(".pdf"))
    return None


_CHARS = {"textbackslash": "\\", "textless": "<", "textgreater": ">", "textbar": "|",
          "^": "^", "~": "~", "textperiodcentered": "-"}
_CMD = re.compile(r"\\([A-Za-z]+|.)(\{\})?")


def _group(s: str, i: int) -> tuple[str, int]:
    """The {...} group opening at s[i] -> (its inside, the index after it)."""
    d, j = 0, i
    while j < len(s):
        if s[j] == "\\":
            j += 2
            continue
        d += {"{": 1, "}": -1}.get(s[j], 0)
        j += 1
        if d == 0:
            break
    return s[i + 1:j - 1], j


def detex(s: str) -> str:
    """esc()'d LaTeX -> its text: escapes undone, commands and braces dropped."""
    out, i = [], 0
    while i < len(s):
        m = _CMD.match(s, i)
        if not m:
            out.append("" if s[i] in "{}" else s[i])
            i += 1
        elif m[1] in _CHARS:
            out.append(_CHARS[m[1]])
            i = m.end()
        else:
            out.append(m[1] if m[1] in "{}$&#%_" else " " if m[1] == " " else "")
            i += 1 + len(m[1])
    return "".join(out)


def plain_text(tex: str) -> list[str]:
    """report.tex's words as plain lines, in its order: what a report PDF
    holds where lualatex can't set it in the house style."""
    body = tex.split(r"\begin{document}", 1)[1].split(r"\end{document}", 1)[0]
    out = []
    for line in body.splitlines():
        if line.startswith(r"\hstitleblock"):
            i, args = len(r"\hstitleblock"), []
            for _ in range(3):
                a, i = _group(line, i)
                args.append(detex(a))
            out += args
        elif line.startswith(r"\begin{hscallout}"):
            title, i = _group(line, len(r"\begin{hscallout}"))
            out += ["", f"** {detex(title)}: " + detex(line[i:].replace(r"\end{hscallout}", ""))]
        elif line.startswith(r"\begin{hsstatrow}"):
            stats = re.findall(r"\\hsstat\{(\d+)\}\{([^{}]*)\}", line)
            out += ["", ", ".join(f"{v} {k}" for v, k in stats) + "."]
        elif line.startswith(r"\section{"):
            t = detex(_group(line, len(r"\section"))[0])
            out += ["", t, "=" * len(t)]
        elif line.startswith(r"\begin{hsfigure}"):
            i = len(r"\begin{hsfigure}")
            fid, i = _group(line, i)
            cap, i = _group(line, i)
            out.append(f"Figure {detex(fid)}: {detex(cap)} (not drawn here)")
        elif line.rstrip().endswith("\\\\"):  # a table row
            out.append(" | ".join(detex(c).strip() for c in
                                  re.split(r"(?<!\\)&", line.rstrip()[:-2])))
        elif line not in ("{\\small", "}", r"\sloppy") and not line.startswith(
                (r"\begin{longtable}", r"\end{longtable}", r"\hstoprule", r"\hline", r"\endhead")):
            out.append(detex(line).strip())
    return out


def _pdf_str(s: str) -> str:
    s = "".join(c if 32 <= ord(c) < 127 else "?" for c in s)  # the PDF is written as ASCII
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def bare_lines(rdir: Path, run: dict, why: str) -> list[str]:
    """What run.json alone says of a run, for a report.tex that couldn't be
    built: its status, why it stopped, its safe state and each step it ran."""
    out = [f"Plain report from run.json alone, since the {why}. npie_report.py --run "
           "<this dir> rebuilds the full report once that is fixed.", "",
           f"Bring-up report{' (simulated bench)' if run.get('bench') == 'sim' else ''}",
           f"Board: {run.get('board')}", f"Run {rdir.name}: {run.get('status')}"]
    out += [f"{k}: {run[k]}" for k in ("error", "cursor", "safe_state", "bench", "started",
                                       "finished", "procedure_sha256") if run.get(k)]
    def n(v):
        return f"{v:.6g}" if isinstance(v, (int, float)) and not isinstance(v, bool) else str(v)
    out += ["", "Steps run", "========="]
    for r in run.get("steps") or []:
        if not isinstance(r, dict):
            out.append(str(r))
            continue
        val = r.get("value")
        if isinstance(val, dict):
            val = ", ".join(f"{k}={v}" for k, v in val.items())
        elif r.get("fields"):
            val = ", ".join(f"{k}={n(v)}" for k, v in r["fields"].items())
        cells = [r.get("id"), r.get("type"), r.get("verdict", "-"),
                 "-" if val is None else f"{n(val)} {r.get('unit') or ''}".strip()]
        if r.get("confirmed_by"):
            cells.append(f"by {r['confirmed_by']}")
        out.append(" | ".join(str(c) for c in cells))
    return out


def plain_pdf(lines: list[str], path: Path) -> None:
    """A text-only PDF of lines: Courier on A4, no fonts embedded, no tools
    needed, so any host can leave one."""
    rows = [r for l in lines for r in (textwrap.wrap(l, PLAIN_COLS, subsequent_indent="  ",
                                                      break_on_hyphens=False) or [""])]
    pages = [rows[i:i + PLAIN_ROWS] for i in range(0, len(rows), PLAIN_ROWS)] or [[]]
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>"]
    kids = []
    for pg in pages:
        body = "BT /F1 8 Tf 10 TL 50 800 Td\n" + "".join(
            f"({_pdf_str(r)}) Tj T*\n" for r in pg) + "ET"
        objs.append(f"<< /Length {len(body)} >>\nstream\n{body}\nendstream")
        objs.append("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
                    f"/Resources << /Font << /F1 3 0 R >> >> /Contents {len(objs)} 0 R >>")
        kids.append(len(objs))
    objs[1] = (f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] "
               f"/Count {len(kids)} >>")
    out, offs = b"%PDF-1.4\n", []
    for n, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{n} 0 obj\n{o}\nendobj\n".encode("ascii")
    xref = len(out)
    out += (f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n"
            + "".join(f"{o:010d} 00000 n \n" for o in offs)
            + f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
            ).encode("ascii")
    path.write_bytes(out)


def write(rdir: Path, tex_only: bool = False, why_tex_only: str = "--tex-only") -> dict:
    """report.tex, then report.pdf unless tex_only -> {ok, run, status, tex,
    pdf, warnings}. Where the house-style PDF can't be built, report.pdf is
    the plain one and ok is false. Where report.tex can't be built (no
    procedure.json, or a bug here), report.pdf is what run.json alone says
    and tex is None. Raises OSError / ValueError on a dir with no readable
    run.json; npie_run.py calls this whenever a run stops."""
    run = json.loads((rdir / "run.json").read_text(encoding="utf-8"))
    tex = rdir / "report.tex"
    pdf = tex.with_suffix(".pdf")
    try:
        proc = json.loads((rdir / "procedure.json").read_text(encoding="utf-8"))
        src = build_tex(rdir, run, proc)
    except Exception as exc:     # the run still ends on record, from run.json alone
        why = f"report.tex was not built: {type(exc).__name__}: {exc}"
        tex.unlink(missing_ok=True)  # an earlier one would claim an earlier status
        plain_pdf(bare_lines(rdir, run, why), pdf)
        return {"ok": False, "run": str(rdir), "status": run.get("status"), "tex": None,
                "pdf": str(pdf), "warnings": [why, "plain PDF from run.json alone written instead"]}
    tex.write_text(src, encoding="ascii")
    warn = []
    if tex_only:
        warn.append(f"{why_tex_only}: no PDF built")
    else:
        if not lualatex():
            why = "lualatex not found (set NPIE_LUALATEX)"
        elif not house_style():
            why = "housestyle.sty not found (set NPIE_HOUSE_STYLE)"
        else:
            why = compile_pdf(tex, lualatex(), house_style())
        if why:
            plain_pdf([f"Plain report: {why}. report.tex beside this is the house-style "
                       "source; npie_report.py --run <this dir> rebuilds it where lualatex is.",
                       ""] + plain_text(src), pdf)
            warn += [why, "plain PDF written instead of the house style"]
    return {"ok": not warn and pdf.is_file(), "run": str(rdir), "status": run["status"],
            "tex": str(tex), "pdf": str(pdf) if not tex_only and pdf.is_file() else None,
            "warnings": warn}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--run", required=True)
    ap.add_argument("--tex-only", action="store_true")
    a = ap.parse_args(argv)
    try:
        out = write(Path(a.run).expanduser().resolve(), a.tex_only)
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": f"not a run dir: {exc}"}))
        return 2
    print(json.dumps(out, indent=1))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

"""npie-procedure/1 -> markdown a person follows at the bench (ASCII only)."""
from __future__ import annotations


def _num(x) -> str:
    return "-" if x is None else f"{x:g}"


def limit_text(e: dict | None) -> str:
    if not e:
        return ""
    if "edges_min" in e:
        return f">= {e['edges_min']} edges per channel"
    u = e.get("unit", "")
    lo, hi, nom = e.get("min"), e.get("max"), e.get("nominal")
    if lo is not None and hi is not None:
        s = f"{_num(lo)} .. {_num(hi)} {u}"
    elif lo is not None:
        s = f">= {_num(lo)} {u}"
    elif hi is not None:
        s = f"<= {_num(hi)} {u}"
    else:
        s = ""
    return (s + (f" (nominal {_num(nom)})" if nom is not None else "")).strip()


def step_text(s: dict) -> str:
    t = s["type"]
    if t == "human":
        return "PERSON: " + s["text"]
    if t == "supply":
        st = s["set"]
        return (f"Supply {st['v']:g} V, limit {st['i_limit']:g} A, output "
                f"{'ON' if st['output'] else 'off'}")
    if t == "measure":
        return f"Measure {s['quantity']}: {s.get('text', '')}"
    if t == "scope":
        return f"Scope ch{s['channel']} {s['quantity']}: {s.get('text', '')}"
    if t == "logic":
        return f"Logic capture {', '.join(s['channels'])} for {s['duration_s']:g} s"
    if t == "flash":
        return f"Flash the firmware ({s['artifact']}) with the bench's SWD probe"
    if t == "console":
        send = f"send `{s['send']}`" if s.get("send") else "read"
        return f"Console {send}, expect `{s['expect_re']}` within {s['timeout_s']:g} s"
    if t == "wait":
        return f"Wait {s['seconds']:g} s"
    return t


def procedure_md(proc: dict) -> str:
    L = [f"# Bring-up procedure: {proc['board']}", "",
         f"Generated {proc['generated']} by npie from the design inputs below; "
         "regenerate it after any design or firmware change.", "",
         "| Input | sha256 |", "|---|---|"]
    for k, v in proc["inputs"].items():
        L.append(f"| {k}: `{v['path']}` | `{v['sha256'][:12]}` |")
    L += ["", f"Instruments: {', '.join(proc['roles'])}.", ""]
    if proc["skipped"]:
        L += ["Not in this procedure:", ""]
        L += [f"- {s['stage']}: {s['reason']}" for s in proc["skipped"]]
        L.append("")
    if proc.get("overrides"):
        L += ["Limits a person set in bringup/overrides.yaml:", ""]
        L += [f"- {o['step']}: {limit_text(o['generated'])} -> "
              f"{limit_text({**o['generated'], **o['override']})}" for o in proc["overrides"]]
        L.append("")
    for st in proc["stages"]:
        L += [f"## {st['title']}", "", "| Step | Action | Limit | From |", "|---|---|---|---|"]
        for s in st["steps"]:
            L.append(f"| {s['id']} | {step_text(s)} | {limit_text(s.get('expect'))} | "
                     f"{s.get('derived_from', '')} |")
        L.append("")
    return "\n".join(L).replace("±", "+/-") + "\n"

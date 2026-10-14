"""npie-procedure/1 -> markdown a person follows at the bench (ASCII only)."""
from __future__ import annotations


def _num(x) -> str:
    return "-" if x is None else f"{x:g}"


def limit_text(e: dict | None) -> str:
    if not e:
        return ""
    if "edges_min" in e:
        return f">= {e['edges_min']} edges per channel"
    if e.get("any_of"):
        return " or ".join(limit_text({**x, "unit": e.get("unit", "")}) for x in e["any_of"])
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


def fields_text(fields: dict | None) -> str:
    """A self-test step's per-field limits."""
    return "; ".join(f"{k} {limit_text(e)}" for k, e in (fields or {}).items())


def _override_text(o: dict) -> str:
    g, new = o["generated"], o["override"]
    if all(isinstance(v, dict) for v in new.values()):      # per reply field
        return "; ".join(f"{k}: {limit_text(g[k])} -> {limit_text({**g[k], **v})}"
                         for k, v in new.items())
    return f"{limit_text(g)} -> {limit_text({**g, **new})}"


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
    if t == "flash" and s.get("role") == "esp32":
        return f"Flash the firmware ({s['artifact']}) with esptool over the board's USB"
    if t == "flash":
        return f"Flash the firmware ({s['artifact']}) with the bench's SWD probe"
    if t == "console":
        send = f"send `{s['send']}`" if s.get("send") else "read"
        return f"Console {send}, expect `{s['expect_re']}` within {s['timeout_s']:g} s"
    if t == "usb":
        return f"Find the USB serial bridge {s['vid_pid']} on this host ({s.get('text', '')})"
    if t == "boot":
        return f"Reset into the app, read the boot log until `{s['expect_re']}`"
    if t == "selftest":
        return f"Self-test `{' '.join(['st', s['test'], *s.get('args', [])])}`: {s.get('text', '')}"
    if t == "connect":
        return (f"Console over {s['transport'].upper()}"
                + (f" at the address {s['address_from']} reported" if s.get("address_from") else ""))
    if t == "wait":
        return f"Wait {s['seconds']:g} s"
    if t in ("pwm", "awg"):
        what = "PWM generator" if t == "pwm" else f"Generator ch{s.get('channel', 1)}"
        sets = ", ".join(f"{k} {({True: 'on', False: 'off'}.get(v, v))}"
                         for k, v in s["set"].items())
        return f"{what}: {sets}" + (f" ({s['text']})" if s.get("text") else "")
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
        L += [f"- {o['step']}: {_override_text(o)}" for o in proc["overrides"]]
        L.append("")
    for st in proc["stages"]:
        L += [f"## {st['title']}", "", "| Step | Action | Limit | From |", "|---|---|---|---|"]
        for s in st["steps"]:
            lim = limit_text(s.get("expect")) or fields_text(s.get("fields"))
            L.append(f"| {s['id']} | {step_text(s)} | {lim} | "
                     f"{s.get('derived_from', '')} |")
        L.append("")
    return "\n".join(L).replace("±", "+/-") + "\n"

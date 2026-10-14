"""USB-first audio boards: the ESP32 driver, its sim twin and the two-stage plan.

The fixture tests/fixtures/npie/hifi-usb stands in for hifi-lite/hifi-max (an
ESP32-WROVER-E behind a CH340C and a CH224A PD sink on one USB-C, a PCM1863
loopback ADC, a TAS5825M amp). Everything runs on the simulated bench or
against in-process fakes; nothing opens a USB device, and the one socket test
stays on 127.0.0.1.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NPIE = ROOT / "skill" / "scripts"
FIX = ROOT / "tests" / "fixtures" / "npie" / "hifi-usb"
sys.path.insert(0, str(NPIE))

from npielib import design, instruments, procgen  # noqa: E402
from npielib.instruments import BenchError, Esp32, find_usb_port  # noqa: E402


def _copy(tmp_path, edit=None) -> Path:
    ws = tmp_path / "hifi-usb"
    shutil.copytree(FIX, ws)
    if edit:
        edit(ws)
    return ws


def _gen(ws: Path) -> dict:
    r = subprocess.run([sys.executable, str(NPIE / "procedure_gen.py"), "--workspace", str(ws)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads((ws / "bringup" / "procedure.json").read_text())


def _run(*args):
    e = {k: v for k, v in os.environ.items() if k != "NPIE_BENCH_HOST"}
    r = subprocess.run([sys.executable, str(NPIE / "npie_run.py"), *args],
                       capture_output=True, text=True, env=e)
    return r.returncode, json.loads(r.stdout)


def _steps(proc):
    return [s for st in proc["stages"] for s in st["steps"]]


def _cons(ws: Path, fn):
    p = ws / "kicad" / "constraints.json"
    c = json.loads(p.read_text())
    fn(c)
    p.write_text(json.dumps(c))


def _pd(c, **kw):
    next(b for b in c["blocks"] if b["kind"] == "pd-sink").update(kw)


# ------------------------------------------------------------- the plan


def test_plan_is_usb_then_pd_with_limits_from_the_design(tmp_path):
    proc = _gen(_copy(tmp_path))
    assert [st["id"] for st in proc["stages"]] == ["usb", "pd"]
    assert proc["roles"] == ["esp32"]          # no bench instrument: the loopback measures
    uf = proc["design"]["usb_first"]
    assert uf["single_port"] and uf["usb_connector"] == "J1"
    assert [t["name"] for t in uf["taps"]] == ["dac_l", "dac_r", "sub", "spk_l", "spk_r"]
    usb = proc["stages"][0]["steps"]
    assert [s["type"] for s in usb] == ["human", "usb", "flash", "boot", "selftest",
                                        "selftest", "selftest", "selftest"]
    assert usb[0]["plug"] == {"usb": True}
    dac_l = next(s for s in usb if s.get("args", [None])[0] == "dac_l")
    # 2.1 Vrms full scale at -20 dBFS, +/-1 dB; THD+N from the block's dac limit
    assert dac_l["fields"]["vrms"]["nominal"] == pytest.approx(0.21)
    assert dac_l["fields"]["vrms"]["min"] == pytest.approx(0.21 * 10 ** (-1 / 20), abs=1e-5)
    assert dac_l["fields"]["thdn_db"]["max"] == -85
    assert dac_l["fields"]["freq_hz"]["min"] == 995.0
    pd = proc["stages"][1]["steps"]
    assert pd[0]["plug"] == {"usb": False, "charger": True}
    assert pd[1] == {**pd[1], "type": "connect", "transport": "tcp", "address_from": "usb.08"}
    contract = next(s for s in pd if s.get("test") == "pd")["fields"]["contract_v"]
    assert [b["nominal"] for b in contract["any_of"]] == [28.0, 20.0]
    spk_l = next(s for s in pd if s.get("args", [None])[0] == "spk_l")
    assert spk_l["fields"]["vrms"]["nominal"] == pytest.approx(0.21 * 10 ** (12 / 20), abs=1e-5)
    # speakers go on only after a person says so, and a person hears them
    kinds = [s["type"] for s in pd]
    assert kinds.index("human", 1) < kinds.index("selftest", kinds.index("human", 1))
    assert pd[-1]["type"] == "human"


def test_lite_takes_one_20v_contract(tmp_path):
    ws = _copy(tmp_path, lambda w: _cons(w, lambda c: _pd(c, contract_v=20, fallback_v=None)))
    proc = _gen(ws)
    f = next(s for s in _steps(proc) if s.get("test") == "pd")["fields"]["contract_v"]
    assert "any_of" not in f and (f["min"], f["max"]) == (19.0, 21.0)
    assert "EPR" not in proc["stages"][1]["steps"][0]["text"]


def test_two_connectors_keep_the_console_on_usb(tmp_path):
    def split(ws):
        net = ws / "kicad" / "hifi-usb.net"
        t = net.read_text()
        t = t.replace('(comp (ref "J4")', '(comp (ref "J5") (value "USB-C PD in") (footprint "X"))\n'
                                         '    (comp (ref "J4")')
        t = t.replace('(node (ref "J1") (pin "A5") (pinfunction "CC1") (pintype "passive"))',
                      '(node (ref "J5") (pin "A5") (pinfunction "CC1") (pintype "passive"))')
        t = t.replace('(node (ref "J1") (pin "B5") (pinfunction "CC2") (pintype "passive"))',
                      '(node (ref "J5") (pin "B5") (pinfunction "CC2") (pintype "passive"))')
        net.write_text(t)
    proc = _gen(_copy(tmp_path, split))
    assert not proc["design"]["usb_first"]["single_port"]
    assert not [s for s in _steps(proc) if s["type"] == "connect" or s.get("test") == "net"]
    assert "J5" in proc["stages"][1]["steps"][0]["text"]
    rc, out = _run("start", "--workspace", str(tmp_path / "hifi-usb"), "--dry-run")
    assert rc == 0 and out["status"] == "passed", out


def test_not_usb_first_without_the_pd_sink(tmp_path):
    ws = _copy(tmp_path, lambda w: (w / "kicad" / "hifi-usb.net").write_text(
        (w / "kicad" / "hifi-usb.net").read_text().replace('"CH224A"', '"TPS25750"')))
    proc = procgen.generate(design.load(ws))
    assert proc["design"]["usb_first"] is None
    assert "usb" not in [st["id"] for st in proc["stages"]]


def test_tap_missing_from_manifest_is_skipped_with_reason(tmp_path):
    def drop(ws):
        p = ws / "firmware" / "fwe-manifest.json"
        m = json.loads(p.read_text())
        del m["selftest"]["taps"]["sub"]
        p.write_text(json.dumps(m))
    proc = _gen(_copy(tmp_path, drop))
    assert not [s for s in _steps(proc) if s.get("args", [None])[0] == "sub"]
    assert any(k["stage"] == "tone:sub" and "selftest.taps" in k["reason"] for k in proc["skipped"])


def test_no_selftest_section_skips_self_test_and_pd(tmp_path):
    def drop(ws):
        p = ws / "firmware" / "fwe-manifest.json"
        m = json.loads(p.read_text())
        del m["selftest"]
        p.write_text(json.dumps(m))
    proc = _gen(_copy(tmp_path, drop))
    assert [st["id"] for st in proc["stages"]] == ["usb"]
    assert {k["stage"] for k in proc["skipped"]} >= {"usb:self-test", "pd"}


def test_field_override_changes_one_limit(tmp_path):
    def ov(ws):
        (ws / "bringup").mkdir()
        (ws / "bringup" / "overrides.yaml").write_text("usb.06: {thdn_db: {max: -80}}\n")
    proc = _gen(_copy(tmp_path, ov))
    s = next(s for s in _steps(proc) if s["id"] == "usb.06")
    assert s["fields"]["thdn_db"]["max"] == -80 and s["fields"]["vrms"]["nominal"] == pytest.approx(0.21)
    assert proc["overrides"][0]["generated"] == {"thdn_db": {"nominal": None, "min": None,
                                                             "max": -85, "unit": "dB"}}


# ------------------------------------------------------- the sim bench


def test_dry_run_passes_and_records_the_self_test(tmp_path):
    ws = _copy(tmp_path)
    _gen(ws)
    rc, out = _run("start", "--workspace", str(ws), "--dry-run")
    assert rc == 0 and out["status"] == "passed", out
    rec = json.loads((Path(out["run"]) / "run.json").read_text())
    by = {r["id"]: r for r in rec["steps"]}
    assert by["usb.02"]["value"] == "/dev/sim-ch340"
    assert "hifi-fw" in by["usb.04"]["value"] and "rst:0x1" in by["usb.04"]["log"]
    assert by["usb.06"]["reply"]["tap"] == "dac_l"
    assert set(by["pd.06"]["fields"]) == {"freq_hz", "vrms", "thdn_db"}
    assert by["pd.02"]["applied"] == {"transport": "tcp", "host": "192.0.2.10", "port": 3333}


def test_held_run_keeps_the_wifi_console_across_invocations(tmp_path):
    ws = _copy(tmp_path)
    _gen(ws)
    _, out = _run("start", "--workspace", str(ws), "--dry-run", "--hold")
    run = out["run"]
    seen = []
    while out["status"] == "awaiting_human":
        seen.append(out["cursor"])
        _run("confirm", "--run", run, "--step", out["cursor"], "--by", "tester")
        _, out = _run("resume", "--run", run)
    assert out["status"] == "passed", out
    assert seen == ["usb.01", "pd.01", "pd.05", "pd.09"]


@pytest.mark.parametrize("fault,stop", [
    ("no-usb", "usb.02"), ("no-flash", "usb.03"), ("no-banner", "usb.04"),
    ("tap:dac_r", "usb.07"), ("tap:spk_l", "pd.06"), ("pd:15", "pd.03")])
def test_a_fault_stops_at_its_step(tmp_path, fault, stop):
    ws = _copy(tmp_path)
    _gen(ws)
    rc, out = _run("start", "--workspace", str(ws), "--dry-run", "--fault", fault)
    assert rc == 1 and out["status"] == "failed" and out["cursor"] == stop, out
    assert "no bench supply" in out["safe_state"]


def test_charger_without_epr_falls_back_to_20v(tmp_path):
    ws = _copy(tmp_path)
    _gen(ws)
    rc, out = _run("start", "--workspace", str(ws), "--dry-run", "--fault", "pd:20")
    assert rc == 0, out
    rec = json.loads((Path(out["run"]) / "run.json").read_text())
    pd = next(r for r in rec["steps"] if r.get("reply", {}) and r["reply"].get("test") == "pd")
    assert pd["reply"]["epr"] is False and 19 <= pd["fields"]["contract_v"] <= 21


def test_unknown_tap_fault_is_refused(tmp_path):
    ws = _copy(tmp_path)
    _gen(ws)
    rc, out = _run("start", "--workspace", str(ws), "--dry-run", "--fault", "tap:woofer")
    assert rc == 2 and "dac_l" in out["error"]


def test_report_lists_self_test_fields(tmp_path):
    ws = _copy(tmp_path)
    _gen(ws)
    _, out = _run("start", "--workspace", str(ws), "--dry-run")
    r = subprocess.run([sys.executable, str(NPIE / "npie_report.py"), "--run", out["run"],
                        "--tex-only"], capture_output=True, text=True)
    tex = (Path(out["run"]) / "report.tex").read_text()
    assert "thdn\\_db" in tex and "contract\\_v" in tex, r.stdout + r.stderr


# ------------------------------------------------------- the live driver


class _P:
    def __init__(self, device, vid, pid, serial_number=None):
        self.device, self.vid, self.pid, self.serial_number = device, vid, pid, serial_number


def test_port_found_by_vid_pid_on_any_host():
    ch340 = [_P("/dev/ttyUSB3", 0x1A86, 0x7523, "A1"), _P("/dev/ttyACM0", 0x0483, 0x5740)]
    assert find_usb_port("1a86:7523", ch340) == "/dev/ttyUSB3"
    with pytest.raises(BenchError, match="data cable"):
        find_usb_port("1a86:7523", ch340[1:])
    two = ch340 + [_P("COM7", 0x1A86, 0x7523, "B2")]
    with pytest.raises(BenchError, match="set port or serial"):
        find_usb_port("1a86:7523", two)
    assert find_usb_port("1a86:7523", two, serial_number="B2") == "COM7"


class _Lines:
    """A transport that replays canned lines after each write."""

    def __init__(self, script):
        self.script, self.out, self.sent = list(script), [], []

    def write_line(self, line):
        self.sent.append(line)
        self.out += self.script.pop(0) if self.script else []

    def readline(self, timeout_s):
        return self.out.pop(0) if self.out else None

    def reset_into_app(self):
        self.out += ["ets Jun  8 2016", "I (300) app: start", "hifi-fw 0.1.0"]

    def close(self):
        pass


def _esp(script):
    link = _Lines(script)
    e = Esp32({}, ports=lambda: [_P("/dev/x", 0x1A86, 0x7523)],
              open_usb=lambda port, baud: link, open_tcp=None, flasher=None)
    return e, link


def test_selftest_skips_log_lines_and_other_replies():
    e, link = _esp([["I (900) wifi: sta connected", 'OK {"test":"rails","v3v3_v":3.3}',
                     'OK {"test":"tone","tap":"dac_l","vrms":0.21}']])
    assert e.selftest("tone", ["dac_l", 1000, -20]) == {"test": "tone", "tap": "dac_l", "vrms": 0.21}
    assert link.sent == ["st tone dac_l 1000 -20"]


def test_selftest_err_and_silence():
    e, _ = _esp([["ERR tap not wired"], []])
    assert e.selftest("tone", ["sub", 1000, -20]) == {"error": "tap not wired"}
    assert e.selftest("rails", timeout_s=0.1) is None


def test_boot_reads_the_log_to_the_banner():
    e, _ = _esp([])
    ok, log = e.boot("^hifi-fw ", 1)
    assert ok and log[0].startswith("ets") and log[-1] == "hifi-fw 0.1.0"


def test_tcp_transport_on_loopback():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def board():
        c, _ = srv.accept()
        buf = b""
        while b"\n" not in buf:
            buf += c.recv(64)
        assert buf == b"st pd\n"
        c.sendall(b'I (5) log line\r\nOK {"test":"pd","contract_v":28.0,"epr":true}\r\n')
        c.close()
    t = threading.Thread(target=board)
    t.start()
    e = Esp32({"transport": "tcp", "host": "127.0.0.1", "tcp_port": srv.getsockname()[1]})
    assert e.transport == "tcp"
    assert e.selftest("pd", timeout_s=5) == {"test": "pd", "contract_v": 28.0, "epr": True}
    e.off()
    t.join()
    srv.close()


def test_flash_needs_esptool_only_when_it_flashes(monkeypatch):
    import importlib.util
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda n, *a: None if n == "esptool" else real(n, *a))
    e = Esp32({}, ports=lambda: [_P("/dev/x", 0x1A86, 0x7523)])
    with pytest.raises(BenchError, match="esptool is not installed"):
        e.flash({"flash": {"esptool": {"images": []}}}, "/nowhere")


def test_live_esp32_refused_off_a_bench_host(monkeypatch):
    monkeypatch.delenv("NPIE_BENCH_HOST", raising=False)
    b = instruments.Bench({"roles": {"esp32": {"driver": "esp32", "port": "auto"}}})
    with pytest.raises(BenchError, match="NPIE_BENCH_HOST=1"):
        b.role("esp32")


def test_sim_board_answers_bad_commands_with_err():
    proc = procgen.generate(design.load(FIX))
    b = instruments.SimBoard(proc)
    h = b.hifi
    h.plug(usb=True)
    assert h.command("st rails") == ""              # nothing flashed: the ROM says nothing
    h.flashed = h.booted = True
    assert h.command("st tone woofer 1000 -20").startswith("ERR tone <tap>")
    assert h.command("st volume").startswith("ERR unknown test")
    assert h.command("selftest").startswith("ERR unknown command")
    assert '"vamp_v":0.000' in h.command("st rails")  # the amp waits for a PD contract
    h.plug(usb=False)
    assert h.command("st rails") == "" and h.link is None

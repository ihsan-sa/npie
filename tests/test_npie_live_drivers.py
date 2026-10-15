"""Every generated step runs on the live drivers (GOALS.md, Every generated
step runs on the live drivers): the sim twins are not the only drivers a
procedure has ever met.

The sim twins take whatever a step asks for (SimDmm reads any quantity), and
the live ones don't (ScpiDmm and ScpiScope know a fixed set of SCPI queries),
so a step can pass every simulated run and still fail on its first real
bench. Here each fixture variant of test_npie_limit_trace, plus the mini-bldc
procedure with test_npie_rf_instruments' pwm/awg stage, is bound to a bench
by the bench.example.yaml procedure_gen writes for it, and every instrument
step goes through runner.execute on the LIVE drivers. A live driver that
refuses a step (an unknown quantity or argument, a role the bench example
has no line for) fails the check. A fail verdict doesn't: the readings are fake.

Nothing real is opened. The transports under the drivers are fakes: the
VISA resource (an SdgModel answers the awg's commands), pyserial, the pwm's
byte link (a Pwm8Gateware), and sigrok-cli and the flash tool (subprocess).
NPIE_BENCH_HOST=1 is set inside the test only, after all of them are in place.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
NPIE = ROOT / "skill" / "scripts"
if str(NPIE) not in sys.path:
    sys.path.insert(0, str(NPIE))

import procedure_gen  # noqa: E402
from npielib import design, instruments, procgen, runner  # noqa: E402
from test_npie_limit_trace import (FIX, _boost, _evn_header, _mini_bldc,  # noqa: E402
                                   _no_manifest, _spin)
from test_npie_rf_instruments import _rf_stage  # noqa: E402


def _rf(ws: Path) -> None:
    pass        # the stage is added to the generated procedure, in _procedure


def _hall(ws: Path) -> None:
    """Hall sensor nets on U1, so procgen emits the logic analyser step."""
    net = ws / "kicad" / "mini-bldc.net"
    hall = "".join(f'    (net (code "{900 + i}") (name "/HALL_{c}")\n'
                   f'      (node (ref "U1") (pin "{20 + i}") (pintype "input")))\n'
                   for i, c in enumerate("ABC"))
    net.write_text(re.sub(r"(\(nets\s*\n)", lambda m: m.group(1) + hall, net.read_text(), count=1))


VARIANTS = [_mini_bldc, _no_manifest, _evn_header, _spin, _boost, _rf, _hall]
IDS = ["mini-bldc", "no-manifest", "evn-header", "spin", "boost", "rf-drive", "hall"]


class _Visa:
    """A VISA resource: numbers for the generic SCPI queries, and an SdgModel
    behind the SDG's own commands so ScpiSdg's read-back holds."""

    def __init__(self):
        self.sdg = instruments.SdgModel()

    def write(self, cmd):
        if re.match(r"C[12]:|MODE ", cmd):
            self.sdg.write(cmd)

    def query(self, cmd):
        if cmd == "*IDN?" or re.match(r"C[12]:", cmd):
            return self.sdg.query(cmd)
        return "1.0"

    def read_raw(self):
        return b"#9000000008" + b"\x89PNG\r\n\x1a\n"


class _Serial:
    def __init__(self, port, baud, timeout):
        pass

    def write(self, data):
        pass

    def readline(self):
        return b""

    def read(self, n=1):
        return b""

    def reset_input_buffer(self):
        pass


def _subprocess_run(argv, **kw):
    if argv[0] == "sigrok-cli":
        chans = argv[argv.index("-C") + 1].split(",")
        rows = [",".join(chans)] + [",".join([str(i % 2)] * len(chans)) for i in range(6)]
        Path(argv[argv.index("-o") + 1]).write_text("\n".join(rows) + "\n")
    return subprocess.CompletedProcess(argv, 0, "fake: ok", "")


@pytest.fixture
def fake_transports(monkeypatch):
    clock = iter(range(10 ** 9))
    monkeypatch.setattr(instruments, "_visa", lambda resource: _Visa())
    monkeypatch.setattr(instruments, "_SerialLink", lambda cfg: instruments.Pwm8Gateware())
    monkeypatch.setitem(sys.modules, "serial", types.SimpleNamespace(Serial=_Serial))
    monkeypatch.setattr(instruments, "subprocess",
                        types.SimpleNamespace(run=_subprocess_run))
    monkeypatch.setattr(instruments, "shutil",
                        types.SimpleNamespace(which=lambda name: f"/fake/{name}"))
    monkeypatch.setattr(instruments, "time",
                        types.SimpleNamespace(monotonic=lambda: float(next(clock))))
    monkeypatch.setattr(runner, "time", types.SimpleNamespace(sleep=lambda s: None))
    monkeypatch.setenv("NPIE_BENCH_HOST", "1")


class _Run:
    """What runner.execute reads from a run, on a live bench."""
    sim = False

    def __init__(self, ws: Path, bench):
        self.ws, self.bench, self.dir = ws, bench, ws / "bringup" / "runs" / "live"
        (self.dir / "captures").mkdir(parents=True)

    def record(self, step, **kw):
        return {"id": step["id"], **kw}


def _procedure(tmp_path: Path, variant) -> tuple[Path, dict]:
    ws = tmp_path / "mini-bldc"
    shutil.copytree(FIX, ws)
    variant(ws)
    proc = procgen.generate(design.load(ws))
    if variant is _rf:
        pj = ws / "bringup" / "procedure.json"
        pj.parent.mkdir(exist_ok=True)
        pj.write_text(json.dumps(proc))
        _rf_stage(ws, {"f_hz": 13.56e6, "duty": 0.5, "phase_deg": {"4": 180}, "enable": True})
        proc = json.loads(pj.read_text())
        proc["roles"] = sorted({s["role"] for s in runner._steps(proc) if s.get("role")})
    return ws, proc


def _run_live(tmp_path: Path, variant) -> tuple[list[str], set[str]]:
    ws, proc = _procedure(tmp_path, variant)
    bench = instruments.Bench(yaml.safe_load(procedure_gen.bench_example(proc)))
    run = _Run(ws, bench)
    refused = []
    for s in runner._steps(proc):
        if s["type"] == "human":
            continue
        try:
            runner.execute(run, s)
        except Exception as exc:
            refused.append(f"{s['id']} ({s['type']} {s.get('role')}): {exc}")
    return refused, set(bench._open)


@pytest.mark.parametrize("variant", VARIANTS, ids=IDS)
def test_every_step_runs_on_the_live_drivers(tmp_path, fake_transports, variant):
    refused, _ = _run_live(tmp_path, variant)
    assert not refused, "steps a live driver refuses:\n" + "\n".join(refused)


def test_the_variants_reach_every_live_driver(tmp_path, fake_transports):
    opened = set()
    for v, name in zip(VARIANTS, IDS):
        opened |= _run_live(tmp_path / name, v)[1]
    assert opened == set(instruments.SIM), f"roles no variant opens live: {set(instruments.SIM) - opened}"


def test_a_quantity_only_the_sim_knows_is_refused(tmp_path, fake_transports):
    ws, proc = _procedure(tmp_path, _mini_bldc)
    step = dict(next(s for s in runner._steps(proc) if s["type"] == "scope"), quantity="risetime")
    bench = instruments.Bench(yaml.safe_load(procedure_gen.bench_example(proc)))
    with pytest.raises(instruments.BenchError, match="KeyError: 'risetime'"):
        runner.execute(_Run(ws, bench), step)


def test_every_role_has_a_live_driver_in_the_bench_example():
    drivers = {r: yaml.safe_load(f"x: {line}")["x"]["driver"]
               for r, line in procedure_gen.BENCH_EXAMPLE.items()}
    assert set(drivers) == set(instruments.SIM)
    assert {r: d for r, d in drivers.items() if d not in instruments.LIVE} == {}

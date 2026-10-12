"""Without NPIE_BENCH_HOST=1, no real instrument driver is ever opened."""
from __future__ import annotations

import sys

import pytest

from test_npie_run import NPIE

sys.path.insert(0, str(NPIE))
from npielib import instruments  # noqa: E402
from npielib.instruments import Bench, BenchError  # noqa: E402


@pytest.mark.parametrize("driver", sorted(instruments.LIVE))
def test_live_driver_refused_without_bench_host(driver, monkeypatch):
    monkeypatch.delenv("NPIE_BENCH_HOST", raising=False)

    def opened(*a, **k):
        raise AssertionError(f"{driver} touched hardware")

    monkeypatch.setitem(instruments.LIVE, driver, opened)
    monkeypatch.setattr(instruments, "_visa", opened)
    bench = Bench({"roles": {"x": {"driver": driver, "resource": "TCPIP::1.2.3.4::INSTR"}}})
    with pytest.raises(BenchError, match="NPIE_BENCH_HOST"):
        bench.role("x")


def test_live_driver_allowed_on_bench_host(monkeypatch):
    monkeypatch.setenv("NPIE_BENCH_HOST", "1")
    monkeypatch.setitem(instruments.LIVE, "serial", lambda cfg: "opened")
    assert Bench({"roles": {"x": {"driver": "serial"}}}).role("x").upper() == "OPENED"

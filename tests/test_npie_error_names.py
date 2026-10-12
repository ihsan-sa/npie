"""A failed instrument call raises an error naming the instrument and the call."""
from __future__ import annotations

import sys

import pytest

from test_npie_run import NPIE

sys.path.insert(0, str(NPIE))
from npielib.instruments import Bench, BenchError, SimBoard  # noqa: E402


def _bench() -> Bench:
    return Bench({}, sim_board=SimBoard({"stages": []}))


def test_driver_refusal_names_instrument_and_call():
    with pytest.raises(BenchError) as e:
        _bench().role("awg").set(channel=3, f_hz=1e6)
    msg = str(e.value)
    assert msg.startswith("awg (sim): set(")
    assert "channel=3" in msg and "f_hz=1000000.0" in msg
    assert "no channel 3" in msg


def test_foreign_exception_is_wrapped_and_chained():
    with pytest.raises(BenchError, match=r"dmm \(sim\): measure\('voltage', \{\}\) failed: KeyError") as e:
        _bench().role("dmm").measure("voltage", {})
    assert isinstance(e.value.__cause__, KeyError)


def test_successful_calls_pass_through():
    psu = _bench().role("psu")
    psu.set(12, 1, True)
    assert psu.measure("voltage") == 12.0

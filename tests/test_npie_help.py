"""Every script in skill/scripts prints usage and exits 0 on --help."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

NPIE = Path(__file__).resolve().parents[1] / "skill" / "scripts"
SCRIPTS = sorted(p.name for p in NPIE.glob("*.py"))


def test_scripts_found():
    assert SCRIPTS


@pytest.mark.parametrize("script", SCRIPTS)
def test_help_exits_zero(script):
    r = subprocess.run([sys.executable, str(NPIE / script), "--help"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "usage:" in r.stdout

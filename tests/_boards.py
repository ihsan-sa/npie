"""Real board workspaces for the few tests that need one.

Boards live in their own repo, found the way hwde and npie find it:
HWDE_BOARDS_ROOT, default ~/dev/boards. A test that really needs a shipped
board asks for it here: it runs when the boards repo is cloned, and SKIPS with
the reason when it is not - never a silent pass, never a failure on a machine
without the boards repo. Anything a small fixture under tests/fixtures can
stand in for uses the fixture instead.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

BOARDS = Path(os.environ.get("HWDE_BOARDS_ROOT") or "~/dev/boards").expanduser()


def board_path(name: str) -> Path:
    """Where board `name` lives in the boards repo (may not exist)."""
    return BOARDS / name


def need_board(*names: str) -> None:
    """Skip the calling test unless every named board is in the boards repo."""
    gone = [n for n in names if not board_path(n).is_dir()]
    if gone:
        pytest.skip(f"needs the real board(s) {', '.join(gone)} from the "
                    f"boards repo ({BOARDS}; set HWDE_BOARDS_ROOT)")

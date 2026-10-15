"""Every test locks instruments in a dir of its own (instruments.LOCK_DIR_ENV),
so a bench a test leaves open never meets another test's, or another
checkout's on this host."""
import pytest


@pytest.fixture(autouse=True)
def _own_lock_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("NPIE_LOCK_DIR", str(tmp_path / "locks"))

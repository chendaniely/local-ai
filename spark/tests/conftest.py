"""What every spark test shares."""

import os

import pytest


@pytest.fixture(autouse=True)
def fake_environment(monkeypatch, tmp_path_factory):
    """Every test runs in an environment it built, never the shell's. The code under test reads the environment (keys,
    uv's settings) and passes it on (to engines, `uv sync`, -validate, scripts), and a failing test prints what it was
    given: an assertion's diff, a traceback's arguments, a frame's locals. Dan's shell can hold secrets. So every
    variable goes, and only these come back: PATH, so the tools tests run are found; LANG, so they read text as they
    would; and HOME, a new, empty folder. A test that needs another sets it, with a stand-in value."""
    kept = {name: os.environ[name] for name in ("PATH", "LANG") if name in os.environ}
    for name in list(os.environ):
        monkeypatch.delenv(name)
    for name, value in kept.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("HOME", str(tmp_path_factory.mktemp("home")))

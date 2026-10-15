"""What every spark test shares."""

import os

import pytest


@pytest.fixture(autouse=True)
def fake_environment(monkeypatch, tmp_path_factory):
    """Every test runs in an environment it built, never the shell's. The code under test reads the environment (keys,
    uv's settings) and passes it on (to engines, `uv sync`, -validate, scripts), and a failing test prints what it was
    given: an assertion's diff, a traceback's arguments, a frame's locals. Dan's shell can hold secrets. So every
    variable goes, and only these come back: PATH, so the tools tests run are found; LANG, so they read text as they
    would; TZ, when the run sets one, so every test that doesn't pin its own zone, and every process such a test
    starts, reads the clock in that zone; and HOME, a new, empty folder. A test that needs another sets it, with a
    stand-in value.

    Before TZ was kept (2026-10-14), a run given a zone didn't keep it: a process a test started read the system's
    zone, and the tests' own process read the run's only until something called tzset (datetime.strptime's first call
    does), then the system's. So `TZ=UTC make test` on the Spark passed brake tests that failed in CI, which runs in
    UTC."""
    kept = {name: os.environ[name] for name in ("PATH", "LANG", "TZ") if name in os.environ}
    for name in list(os.environ):
        monkeypatch.delenv(name)
    for name, value in kept.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("HOME", str(tmp_path_factory.mktemp("home")))

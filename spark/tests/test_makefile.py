"""The Makefile's deploy targets, run against stand-ins for the tools they call: what each runs, and what make says and
exits with when one fails. (make install-units' tests are in test_bootstrap.py, with bootstrap's.)"""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def fakes(tmp_path: Path, **tools: str) -> dict[str, str]:
    """A stand-in for each tool, named by its keyword, that logs its call in tmp_path/calls and then runs its body. The
    environment is built, not copied: PATH with the stand-ins first, a HOME of its own, and CALLS."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in tools.items():
        (bindir / name).write_text(f'#!/usr/bin/env bash\necho "{name} $*" >> "$CALLS"\n{body}\n')
        (bindir / name).chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    return {"PATH": f"{bindir}:{os.environ['PATH']}", "HOME": str(home), "CALLS": str(tmp_path / "calls")}


def make(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    # -s: GNU make 4 prints "Entering directory" lines under -C otherwise.
    return subprocess.run(["make", "-s", "-C", str(ROOT), *args], capture_output=True, text=True, env=env)


def calls(tmp_path: Path) -> list[str]:
    path = tmp_path / "calls"
    return path.read_text().splitlines() if path.exists() else []


@pytest.mark.parametrize("pull", [0, 1], ids=["pulled", "failed"])
def test_make_pull_shows_the_pulls_journal_whether_or_not_it_failed(tmp_path, pull):
    # A file that fails makes the pull exit 1, and the FAILED line that says why is in its journal: make shows the
    # journal then too, and still fails.
    env = fakes(tmp_path, systemctl=f"exit {pull}", journalctl='echo "pull: coder: coder.gguf: FAILED — a stand-in"')
    result = make("pull", env=env)
    assert calls(tmp_path) == ["systemctl start local-ai-pull.service",
                               "journalctl -u local-ai-pull.service -n 40 --no-pager"]
    assert "FAILED — a stand-in" in result.stdout
    assert (result.returncode == 0) == (pull == 0), result.stderr


def test_make_pull_is_one_recipe_line_that_keeps_the_pulls_status():
    # On two lines, make stopped at the failed start, before the journal.
    assert make("-n", "pull").stdout.splitlines() == [
        "systemctl start local-ai-pull.service; s=$?; journalctl -u local-ai-pull.service -n 40 --no-pager; exit $s"]

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOOKS = ROOT / ".githooks"


def fake_bin(tmp_path: Path, *tools: str) -> Path:
    """A directory holding only the named tools, to use as the hook's whole PATH."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for tool in tools:
        found = shutil.which(tool)
        assert found, tool
        (bindir / tool).symlink_to(found)
    return bindir


def test_hooks_are_executable():
    for name in ("pre-commit", "commit-msg"):
        assert os.access(HOOKS / name, os.X_OK), name


def test_pre_commit_refuses_without_gitleaks(tmp_path):
    # A PATH with git and bash but no gitleaks: the hook must refuse, not pass.
    bindir = fake_bin(tmp_path, "git", "bash", "dirname", "cat")
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    result = subprocess.run(
        ["bash", str(HOOKS / "pre-commit")],
        cwd=repo,
        env={"PATH": str(bindir), "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "gitleaks is not installed" in result.stderr


@pytest.mark.skipif(shutil.which("gitleaks") is None, reason="needs the real gitleaks; CI's tests job has none")
@pytest.mark.parametrize("hook", ["pre-commit", "commit-msg"])
def test_hook_refuses_clearly_without_uv(tmp_path, hook):
    # gitleaks is there, so the hook gets past its gitleaks checks; uv is not on PATH, as in a GUI
    # git client that never sourced the shell profile.
    bindir = fake_bin(tmp_path, "git", "bash", "dirname", "cat", "gitleaks")
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    message = tmp_path / "msg"
    message.write_text("hello\n")
    result = subprocess.run(
        ["bash", str(HOOKS / hook), str(message)],
        cwd=repo,
        env={"PATH": str(bindir), "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "uv is not on this shell's PATH" in result.stderr

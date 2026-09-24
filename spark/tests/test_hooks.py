import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOOKS = ROOT / ".githooks"


def test_hooks_are_executable():
    for name in ("pre-commit", "commit-msg"):
        assert os.access(HOOKS / name, os.X_OK), name


def test_pre_commit_refuses_without_gitleaks(tmp_path):
    # A PATH with git and bash but no gitleaks: the hook must refuse, not pass.
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for tool in ("git", "bash", "dirname", "cat"):
        found = shutil.which(tool)
        assert found, tool
        (bindir / tool).symlink_to(found)
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

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


NEW_GITLEAKS = """#!/bin/sh
# Stands in for gitleaks 8.19 or later: it has the `git` command.
exit 0
"""

OLD_GITLEAKS = """#!/bin/sh
# Stands in for gitleaks 8.16, Ubuntu's archive version: it has no `git` command.
if [ "$1" = git ]; then echo 'Error: unknown command "git" for "gitleaks"' >&2; exit 1; fi
exit 0
"""


def gitleaks_has_git() -> bool:
    """Whether the gitleaks on PATH has the `git` command (8.19 or later), as the hooks require."""
    found = shutil.which("gitleaks")
    return found is not None and subprocess.run([found, "git", "--help"], capture_output=True).returncode == 0


def run_hook(tmp_path: Path, hook: str, bindir: Path) -> subprocess.CompletedProcess[str]:
    """Run a hook in a scratch repository with `bindir` as its whole PATH."""
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    message = tmp_path / "msg"
    message.write_text("hello\n")
    return subprocess.run(
        ["bash", str(HOOKS / hook), str(message)],
        cwd=repo,
        env={"PATH": str(bindir), "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
    )


def make_hooks(tmp_path: Path, gitleaks: str, denylist: str) -> tuple[subprocess.CompletedProcess[str], str]:
    """Run `make hooks` with a stand-in gitleaks and denylist, and return the result and the
    core.hooksPath it left. GIT_DIR points git at a scratch repository, so this clone's own
    config is never touched."""
    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    (bindir / "gitleaks").write_text(gitleaks)
    (bindir / "gitleaks").chmod(0o755)
    (tmp_path / "denylist").write_text(denylist)
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("MAKE") and k != "MFLAGS"}
    env.update(
        PATH=f"{bindir}:{env['PATH']}",
        GIT_DIR=str(repo / ".git"),
        LOCAL_AI_DENYLIST=str(tmp_path / "denylist"),
    )
    result = subprocess.run(["make", "-C", str(ROOT), "hooks"], env=env, capture_output=True, text=True)
    hooks_path = subprocess.run(
        ["git", "config", "--get", "core.hooksPath"], cwd=repo, capture_output=True, text=True
    ).stdout.strip()
    return result, hooks_path


def test_make_hooks_turns_the_hooks_on(tmp_path):
    result, hooks_path = make_hooks(tmp_path, NEW_GITLEAKS, "secret-project\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert hooks_path == ".githooks"


@pytest.mark.parametrize("denylist", ["", "# a comment\n\n"], ids=["empty", "comments-only"])
def test_make_hooks_refuses_a_denylist_with_no_terms(tmp_path, denylist):
    # The hooks would refuse every commit with it, so `make hooks` must not say "on".
    result, hooks_path = make_hooks(tmp_path, NEW_GITLEAKS, denylist)
    assert result.returncode != 0
    assert "denylist has no terms" in result.stdout + result.stderr
    assert hooks_path == ""


def test_make_hooks_refuses_a_gitleaks_without_the_git_command(tmp_path):
    # With 8.16 on PATH the hooks refuse every commit, so `make hooks` must not say "on".
    result, hooks_path = make_hooks(tmp_path, OLD_GITLEAKS, "secret-project\n")
    assert result.returncode != 0
    assert "8.19 or later" in result.stdout + result.stderr
    assert hooks_path == ""


def test_hooks_are_executable():
    for name in ("pre-commit", "commit-msg"):
        assert os.access(HOOKS / name, os.X_OK), name


@pytest.mark.parametrize("hook", ["pre-commit", "commit-msg"])
def test_hook_refuses_without_gitleaks(tmp_path, hook):
    # A PATH with git and bash but no gitleaks: the hook must refuse, not pass.
    result = run_hook(tmp_path, hook, fake_bin(tmp_path, "git", "bash", "dirname", "cat"))
    assert result.returncode == 1
    assert "gitleaks is not installed" in result.stderr


@pytest.mark.parametrize("hook", ["pre-commit", "commit-msg"])
def test_hook_refuses_a_gitleaks_without_the_git_command(tmp_path, hook):
    bindir = fake_bin(tmp_path, "git", "bash", "dirname", "cat")
    (bindir / "gitleaks").write_text(OLD_GITLEAKS)
    (bindir / "gitleaks").chmod(0o755)
    result = run_hook(tmp_path, hook, bindir)
    assert result.returncode == 1
    assert "it needs 8.19 or later" in result.stderr


@pytest.mark.skipif(
    not gitleaks_has_git(), reason="needs a real gitleaks with the git command (8.19+); CI's tests job has none"
)
@pytest.mark.parametrize("hook", ["pre-commit", "commit-msg"])
def test_hook_refuses_clearly_without_uv(tmp_path, hook):
    # gitleaks is there, so the hook gets past its gitleaks checks; uv is not on PATH, as in a GUI
    # git client that never sourced the shell profile.
    result = run_hook(tmp_path, hook, fake_bin(tmp_path, "git", "bash", "dirname", "cat", "gitleaks"))
    assert result.returncode == 1
    assert "uv is not on this shell's PATH" in result.stderr

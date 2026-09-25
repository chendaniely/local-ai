import os
import pwd
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "stack/host/bootstrap.sh"


def dry_run(env: dict[str, str] | None = None) -> list[str]:
    out = subprocess.run(
        ["bash", str(SCRIPT), "--dry-run"], check=True, capture_output=True, text=True, env=env
    ).stdout
    return out.splitlines()


def test_dry_run_lists_every_step():
    text = "\n".join(dry_run())
    for expected in [
        "useradd --create-home --shell /bin/bash agent",
        "useradd --system",
        "systemctl set-default multi-user.target",
        "apt-mark hold",
        "/etc/default/earlyoom",
        "ufw allow OpenSSH",
        "50-local-ai.rules",
        "/etc/local-ai/secrets",
    ]:
        assert expected in text, expected


def test_ssh_is_allowed_before_the_firewall_turns_on():
    lines = dry_run()
    allow = next(i for i, line in enumerate(lines) if "ufw allow OpenSSH" in line)
    enable = next(i for i, line in enumerate(lines) if "ufw --force enable" in line)
    assert allow < enable


def usermod_lines(user: str) -> list[str]:
    return [line for line in dry_run() if "usermod" in line and line.rstrip().endswith(f" {user}")]


def test_agent_never_gets_docker_sudo_or_admin():
    lines = usermod_lines("agent")
    # No matching line would make this test check nothing, so a format change must fail it.
    assert lines, "no usermod line for agent in the dry run"
    for line in lines:
        for forbidden in ("docker", "sudo", "spark-admin"):
            assert forbidden not in line


def test_the_engine_user_never_joins_docker():
    lines = usermod_lines("spark")
    assert lines, "no usermod line for spark in the dry run"
    for line in lines:
        assert "docker" not in line


def test_a_dry_run_names_whoever_runs_it_as_the_admin():
    # A dry run has no sudo, so no SUDO_USER. It must still show the admin the real run would get —
    # the person running it — never an assumed login name (the Spark's login is not `dan`).
    me = pwd.getpwuid(os.getuid()).pw_name
    lines = dry_run({k: v for k, v in os.environ.items() if k != "SUDO_USER"})
    assert f"+ usermod -aG spark-admin,spark-users,adm {me}" in lines
    assert f"+ chmod 0700 /home/{me} /home/agent" in lines


def test_under_sudo_the_admin_is_the_sudo_user():
    # `make bootstrap` runs under sudo, where the invoking user is root; SUDO_USER is the admin.
    lines = dry_run({**os.environ, "SUDO_USER": "alice"})
    assert "+ usermod -aG spark-admin,spark-users,adm alice" in lines
    assert "+ chmod 0700 /home/alice /home/agent" in lines


def test_the_engine_user_cannot_change_what_root_runs():
    line = next(line for line in dry_run() if "install -d" in line and "/opt/local-ai/etc" in line)
    assert "-o root -g spark-admin" in line


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed")
def test_shellcheck_is_clean():
    subprocess.run(["shellcheck", str(SCRIPT)], check=True)

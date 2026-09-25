import os
import pwd
import re
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


# Packages as `dpkg-query -W -f='${db:Status-Abbrev}\t${Package}\n'` reports them: the GPU stack's
# names from brightroar (2026-09-24), plus neighbours the hold must leave alone.
INSTALLED = {
    "nvidia-driver-580-open": "ii",
    "libnvidia-compute-580": "ii",
    "nvidia-container-toolkit": "ii",
    "cuda-toolkit-13-0": "ii",
    "cuda-toolkit-13-0-config-common": "ii",
    "cuda-toolkit-13-config-common": "ii",
    "cuda-toolkit-config-common": "ii",
    # The repo's major-version meta: not on brightroar, but one `apt install` away. It must not
    # turn into a `*-13` pattern, which would hold gcc-13.
    "cuda-toolkit-13": "ii",
    "libcublas-13-0": "ii",
    "libnvjitlink-13-0": "ii",
    "gds-tools-13-0": "ii",
    "linux-nvidia-hwe-24.04": "ii",
    "linux-image-nvidia-hwe-24.04": "ii",
    "linux-headers-nvidia-hwe-24.04": "ii",
    "linux-modules-nvidia-580-open-nvidia-hwe-24.04": "ii",
    "linux-modules-nvidia-580-open-7.0.0-1019-nvidia": "ii",
    "linux-modules-nvidia-580-open-6.11.0-1016-nvidia": "rc",  # removed; only its config is left
    "linux-firmware": "ii",
    "gcc-13": "ii",
    "libnvme1": "ii",
    "openssh-server": "ii",
}

FAKE_DPKG_QUERY = """#!/usr/bin/env bash
# Stands in for dpkg-query -W -f=... PATTERN...: prints the fixture's packages that match each
# pattern, and exits 1 if any pattern matched nothing, as dpkg-query does.
status=0
for pat in "$@"; do
  case "$pat" in -*) continue ;; esac
  found=0
  while IFS=$'\\t' read -r st pkg; do
    if [[ $pkg == $pat ]]; then printf '%s \\t%s\\n' "$st" "$pkg"; found=1; fi
  done < "$DPKG_FIXTURE"
  (( found )) || status=1
done
exit "$status"
"""


def test_the_gpu_stack_is_held_as_one_set(tmp_path):
    # The kernel, the NVIDIA modules built for it, the driver and CUDA only work together. Holding
    # the driver without the kernel lets `apt upgrade` install a kernel with no NVIDIA module.
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "dpkg-query"
    fake.write_text(FAKE_DPKG_QUERY)
    fake.chmod(0o755)
    fixture = tmp_path / "installed.tsv"
    fixture.write_text("".join(f"{status}\t{pkg}\n" for pkg, status in INSTALLED.items()))
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "DPKG_FIXTURE": str(fixture)}

    hold = [line for line in dry_run(env) if line.startswith("+ apt-mark hold")]

    assert len(hold) == 1, hold
    assert set(hold[0].split()[3:]) == {
        "nvidia-driver-580-open",
        "libnvidia-compute-580",
        "nvidia-container-toolkit",
        "cuda-toolkit-13-0",
        "cuda-toolkit-13-0-config-common",
        "cuda-toolkit-13-config-common",
        "cuda-toolkit-config-common",
        "cuda-toolkit-13",
        "libcublas-13-0",
        "libnvjitlink-13-0",
        "gds-tools-13-0",
        "linux-nvidia-hwe-24.04",
        "linux-image-nvidia-hwe-24.04",
        "linux-headers-nvidia-hwe-24.04",
        "linux-modules-nvidia-580-open-nvidia-hwe-24.04",
        "linux-modules-nvidia-580-open-7.0.0-1019-nvidia",
    }


def test_every_tool_the_repo_and_dan_use_is_installed_not_assumed():
    # DGX OS happens to ship some of these; a rebuild must not depend on that.
    install = next(line for line in dry_run() if line.startswith("+ apt-get install"))
    packages = set(install.split()[4:])
    for pkg in ["git", "curl", "openssl", "shellcheck", "gh", "python3-dev", "r-base", "r-base-dev"]:
        assert pkg in packages, pkg


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


def install_d_line(path: str) -> str:
    """The dry run's `install -d` line that creates exactly `path`."""
    lines = [line for line in dry_run() if "install -d" in line and path in line.split()]
    assert len(lines) == 1, lines
    return lines[0]


def test_root_owns_the_state_directory_so_spark_cannot_swap_what_root_creates_in_it():
    # Every re-run (upgrade day included) creates spark's directories inside /var/lib/local-ai as
    # root, and `install -d` follows a symlink. If spark owned the parent, it could swap a child for
    # a link to /etc/systemd/system and have root hand that directory to it.
    assert "-o root -g root -m 0755" in install_d_line("/var/lib/local-ai")
    assert "-o spark -g spark -m 0750" in install_d_line("/var/lib/local-ai/hf")
    assert "-o spark -g spark-admin -m 2770" in install_d_line("/var/lib/local-ai/brake")


def test_bootstrap_never_acts_inside_agents_home():
    # agent controls everything under its home, so a root `install`, `chown` or `chmod` there can
    # be pointed anywhere by a planted symlink. The home itself sits in root's /home: that is safe.
    for line in dry_run():
        assert not any(word.startswith("/home/agent/") for word in line.split()), line


def earlyoom_args() -> list[str]:
    text = (ROOT / "stack/host/earlyoom.default").read_text()
    value = next(line for line in text.splitlines() if line.startswith("EARLYOOM_ARGS="))
    value = value.removeprefix("EARLYOOM_ARGS=")
    assert value.startswith('"') and value.endswith('"'), value
    # systemd splits the unit's $EARLYOOM_ARGS on spaces and does not interpret quotes, so a quote
    # or a space inside a regex would break it.
    assert '"' not in value[1:-1] and "'" not in value
    return value[1:-1].split()


def test_earlyoom_never_picks_the_ssh_daemon_or_its_session_processes():
    # OpenSSH 9.8 and later run each login as `sshd-session`, not `sshd`.
    args = earlyoom_args()
    avoid = re.compile(args[args.index("--avoid") + 1])
    for name in ("sshd", "sshd-session"):
        assert avoid.search(name), name
    assert not avoid.search("llama-server")


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed")
def test_shellcheck_is_clean():
    subprocess.run(["shellcheck", str(SCRIPT)], check=True)

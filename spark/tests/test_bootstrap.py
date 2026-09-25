import os
import pwd
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "stack/host/bootstrap.sh"


def dry_run(env: dict[str, str] | None = None) -> list[str]:
    """The dry run's lines. Without an `env`, it runs on a host with no packages installed
    (NO_PACKAGES), so what the tests see never depends on the machine running them."""
    out = subprocess.run(
        ["bash", str(SCRIPT), "--dry-run"],
        check=True,
        capture_output=True,
        text=True,
        env=NO_PACKAGES if env is None else env,
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
    "nvidia-kernel-common-580": "ii",
    "nvidia-driver-580": "un",  # known to dpkg, never installed
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

# What the hold must hold, given INSTALLED.
GPU_SET = {
    "nvidia-driver-580-open",
    "nvidia-kernel-common-580",
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

FAKE_APT_MARK = """#!/usr/bin/env bash
# Stands in for apt-mark. `hold PKG...` records each package in $APT_MARK_HELD, except any named in
# $APT_MARK_IGNORES (a hold that silently didn't take); `showhold` lists what was recorded.
case "$1" in
  hold)
    shift
    for pkg in "$@"; do
      [[ " ${APT_MARK_IGNORES:-} " == *" $pkg "* ]] || printf '%s\\n' "$pkg" >> "$APT_MARK_HELD"
    done
    ;;
  showhold) if [[ -f "$APT_MARK_HELD" ]]; then sort -u "$APT_MARK_HELD"; fi ;;
  *) echo "fake apt-mark: unexpected: $*" >&2; exit 1 ;;
esac
"""


def gpu_env(tmp_path: Path, installed: dict[str, str]) -> dict[str, str]:
    """An environment whose dpkg-query reports `installed` (package → status) and whose apt-mark
    only writes to a file, so even the real (not dry-run) hold changes nothing on this machine."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, text in (("dpkg-query", FAKE_DPKG_QUERY), ("apt-mark", FAKE_APT_MARK)):
        (bindir / name).write_text(text)
        (bindir / name).chmod(0o755)
    fixture = tmp_path / "installed.tsv"
    fixture.write_text("".join(f"{status}\t{pkg}\n" for pkg, status in installed.items()))
    return {
        **os.environ,
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "DPKG_FIXTURE": str(fixture),
        "APT_MARK_HELD": str(tmp_path / "held"),
    }


# A host where dpkg-query finds no packages at all, as on the Mac. The default dry run uses it, so the
# hold step reads the same everywhere: not the Spark's held GPU set, not whatever CI's runner
# carries, and never a package state on the host that happens to stop the hold.
_NO_PACKAGES_DIR = tempfile.TemporaryDirectory(prefix="test-bootstrap-")
NO_PACKAGES = gpu_env(Path(_NO_PACKAGES_DIR.name), {})


def held(tmp_path: Path) -> set[str]:
    path = tmp_path / "held"
    return set(path.read_text().split()) if path.exists() else set()


def script(*args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, env=env)


def real_hold(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """The real hold, not its dry run, against gpu_env's fakes. Sourcing the script defines its
    functions without running it; --dry-run is passed so a broken guard would only print a plan."""
    return subprocess.run(
        ["bash", "-c", 'source "$1" --dry-run && DRY_RUN=0 && hold_gpu_stack', "bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
    )


def hold_line(lines: list[str]) -> set[str]:
    hold = [line for line in lines if line.startswith("+ apt-mark hold")]
    assert len(hold) == 1, hold
    return set(hold[0].split()[3:])


def test_the_gpu_stack_is_held_as_one_set(tmp_path):
    # The kernel, the NVIDIA modules built for it, the driver and CUDA only work together. Holding
    # the driver without the kernel lets `apt upgrade` install a kernel with no NVIDIA module.
    assert hold_line(dry_run(gpu_env(tmp_path, INSTALLED))) == GPU_SET


def test_a_held_set_is_still_the_whole_set(tmp_path):
    # On a bootstrapped box dpkg reports the set as hi (held), not ii. A re-run must still find all
    # of it, CUDA's libraries included, and say how much is already held — not "nothing installed".
    installed = {pkg: "hi" if status == "ii" else status for pkg, status in INSTALLED.items()}
    installed["linux-modules-nvidia-580-open-7.0.0-1019-nvidia"] = "ii"  # new since the last hold
    lines = dry_run(gpu_env(tmp_path, installed))
    assert hold_line(lines) == GPU_SET
    assert f"==> GPU set: {len(GPU_SET)} packages, {len(GPU_SET) - 1} already held" in lines


def test_an_interrupted_dpkg_run_stops_the_hold(tmp_path):
    # After an interrupted upgrade the driver can be unpacked but not configured (iU) and its
    # kernel-common package half-configured (iF). Holding around them would leave both free to move.
    installed = {**INSTALLED, "nvidia-driver-580-open": "iU", "nvidia-kernel-common-580": "iF"}
    result = script("--dry-run", env=gpu_env(tmp_path, installed))
    assert result.returncode == 1
    assert "nvidia-driver-580-open (iU)" in result.stderr
    assert "nvidia-kernel-common-580 (iF)" in result.stderr
    assert "finish dpkg first: sudo dpkg --configure -a" in result.stderr
    assert "apt-mark hold" not in result.stdout


@pytest.mark.parametrize("state", ["iH", "iW", "it", "iiR"])
def test_every_other_unfinished_state_stops_the_hold_too(tmp_path, state):
    # Half-installed, awaiting or pending triggers, or flagged for reinstall: not ii, hi or absent.
    installed = {**INSTALLED, "libcublas-13-0": state}
    result = script("--dry-run", env=gpu_env(tmp_path, installed))
    assert result.returncode == 1
    assert f"libcublas-13-0 ({state})" in result.stderr


@pytest.mark.parametrize("state", ["ri", "pi", "rH", "pF"])
def test_a_removal_that_did_not_finish_is_left_to_apt_to_finish(tmp_path, state):
    # Selected for removal but still installed (ri, pi), or a removal that stopped partway (rH, pF),
    # as when upgrade day's full-upgrade is cut off while it removes the old kernel's modules.
    # `dpkg --configure -a` alone leaves these as they are, so the hint runs apt again after it.
    modules = "linux-modules-nvidia-580-open-7.0.0-1019-nvidia"
    result = script("--dry-run", env=gpu_env(tmp_path, {**INSTALLED, modules: state}))
    assert result.returncode == 1
    assert f"{modules} ({state})" in result.stderr
    assert "sudo dpkg --configure -a && sudo apt full-upgrade" in result.stderr
    assert "apt-mark hold" not in result.stdout


def test_the_real_hold_checks_every_package_is_held(tmp_path):
    result = real_hold(gpu_env(tmp_path, INSTALLED))
    assert result.returncode == 0, result.stderr
    assert held(tmp_path) == GPU_SET
    assert f"==> GPU set held: {len(GPU_SET)} packages" in result.stdout.splitlines()


def test_a_hold_that_did_not_take_fails_loudly(tmp_path):
    # apt-mark can exit 0 without holding everything. The kernel meta left free to move is the one
    # outcome the hold exists to prevent.
    env = {**gpu_env(tmp_path, INSTALLED), "APT_MARK_IGNORES": "linux-image-nvidia-hwe-24.04"}
    result = real_hold(env)
    assert result.returncode == 1
    assert "did not hold" in result.stderr
    assert "linux-image-nvidia-hwe-24.04" in result.stderr


def test_the_real_hold_refuses_when_nothing_matches(tmp_path):
    # Renamed packages, or a dpkg-query that fails outright, must not end in "done" with nothing held.
    env = gpu_env(tmp_path, {"gcc-13": "ii", "openssh-server": "ii"})
    result = real_hold(env)
    assert result.returncode == 1
    assert "nothing installed matches" in result.stderr
    assert held(tmp_path) == set()
    # A dry run may be a preview off the Spark (the Mac, CI): it says so and carries on.
    preview = script("--dry-run", env=env)
    assert preview.returncode == 0
    assert "a real run stops here" in preview.stdout


def test_the_real_hold_refuses_a_set_without_the_kernel(tmp_path):
    # If the kernel's metapackages stop matching the patterns (renamed), `apt upgrade` can install a
    # kernel with no NVIDIA module while the rest of the set sits held.
    installed = {pkg: status for pkg, status in INSTALLED.items() if not pkg.startswith("linux-")}
    result = real_hold(gpu_env(tmp_path, installed))
    assert result.returncode == 1
    assert "no kernel" in result.stderr
    assert held(tmp_path) == set()


@pytest.mark.parametrize("args", [("--hold-gpu", "--dry-run"), ("--dry-run", "--hold-gpu")])
def test_hold_gpu_mode_runs_only_the_hold(tmp_path, args):
    # Upgrade day re-holds the set without the rest of bootstrap: no packages, no desktop stop, no
    # earlyoom restart, no owner and mode resets.
    result = script(*args, env=gpu_env(tmp_path, INSTALLED))
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    commands = [line for line in lines if line.startswith("+ ")]
    assert len(commands) == 1, commands
    assert hold_line(lines) == GPU_SET
    assert not any(line.startswith("==> done") for line in lines)


def test_make_hold_gpu_re_holds_the_set_and_nothing_else(tmp_path):
    # Upgrade day's re-hold, from the front door: `make hold-gpu-dry-run` previews exactly the hold,
    # and `make hold-gpu` runs the same mode under sudo. -n prints the recipe without running it.
    preview = subprocess.run(
        ["make", "-s", "-C", str(ROOT), "hold-gpu-dry-run"],
        capture_output=True,
        text=True,
        env=gpu_env(tmp_path, INSTALLED),
    )
    assert preview.returncode == 0, preview.stderr
    lines = preview.stdout.splitlines()
    commands = [line for line in lines if line.startswith("+ ")]
    assert len(commands) == 1, commands
    assert hold_line(lines) == GPU_SET
    recipe = subprocess.run(
        ["make", "-n", "-C", str(ROOT), "hold-gpu"], capture_output=True, text=True, check=True
    )
    assert "sudo bash stack/host/bootstrap.sh --hold-gpu" in recipe.stdout.splitlines()
    makefile = (ROOT / "Makefile").read_text().splitlines()
    phony = next(line for line in makefile if line.startswith(".PHONY:"))
    assert {"hold-gpu", "hold-gpu-dry-run"} <= set(phony.split()[1:])


def test_a_default_dry_run_never_reads_the_hosts_own_packages(tmp_path, monkeypatch):
    # On the Spark the GPU set is installed and held, and CI's runner carries packages of its own. A
    # host whose package states would stop the hold must not fail tests that aren't about the hold.
    host = gpu_env(tmp_path, {**INSTALLED, "nvidia-driver-580-open": "iU"})
    for name in ("PATH", "DPKG_FIXTURE"):
        monkeypatch.setenv(name, host[name])
    assert any("a real run stops here: nothing installed matches" in line for line in dry_run())


def test_an_unknown_option_is_refused_before_anything_runs():
    # A mistyped --dry-run under sudo must not turn into a real run.
    result = script("--dry-run", "--dryrun", env=dict(os.environ))
    assert result.returncode == 2
    assert "unknown option" in result.stderr
    assert result.stdout == ""


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
    lines = dry_run({k: v for k, v in NO_PACKAGES.items() if k != "SUDO_USER"})
    assert f"+ usermod -aG spark-admin,spark-users,adm {me}" in lines
    assert f"+ chmod 0700 /home/{me} /home/agent" in lines


def test_under_sudo_the_admin_is_the_sudo_user():
    # `make bootstrap` runs under sudo, where the invoking user is root; SUDO_USER is the admin.
    lines = dry_run({**NO_PACKAGES, "SUDO_USER": "alice"})
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

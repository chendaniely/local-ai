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
    only writes to a file, so even the real (not dry-run) hold changes nothing on this machine. It is
    built, not copied: nothing of the test's own environment but PATH, which finds the fakes and the
    tools the script runs, and a HOME of its own (Task 7's fix round 2: a launch that fails prints
    the environment it was given)."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, text in (("dpkg-query", FAKE_DPKG_QUERY), ("apt-mark", FAKE_APT_MARK)):
        (bindir / name).write_text(text)
        (bindir / name).chmod(0o755)
    fixture = tmp_path / "installed.tsv"
    fixture.write_text("".join(f"{status}\t{pkg}\n" for pkg, status in installed.items()))
    home = tmp_path / "home"
    home.mkdir()
    return {
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "HOME": str(home),
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
    env = gpu_env(tmp_path, INSTALLED)
    preview = subprocess.run(
        ["make", "-s", "-C", str(ROOT), "hold-gpu-dry-run"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert preview.returncode == 0, preview.stderr
    lines = preview.stdout.splitlines()
    commands = [line for line in lines if line.startswith("+ ")]
    assert len(commands) == 1, commands
    assert hold_line(lines) == GPU_SET
    recipe = subprocess.run(
        ["make", "-n", "-C", str(ROOT), "hold-gpu"], capture_output=True, text=True, check=True, env=env
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


def test_the_scripts_environment_is_built_not_copied(tmp_path):
    # Task 7's fix round 2: a launch that fails prints the environment it was given, and pytest prints the arguments
    # of a call in a failing assert (so names and homes are taken first, here). The script's environment holds
    # nothing of the test's own: PATH, to find the fakes and the tools, a HOME of its own, and the fakes' settings.
    # NO_PACKAGES is built at import, before any fixture runs, so it too must be built, not copied.
    env = gpu_env(tmp_path, {})
    names, import_names = sorted(env), sorted(NO_PACKAGES)
    home, test_home, import_home = env["HOME"], os.environ["HOME"], NO_PACKAGES["HOME"]
    assert names == import_names == ["APT_MARK_HELD", "DPKG_FIXTURE", "HOME", "PATH"]
    assert home != test_home and home != import_home


def test_an_unknown_option_is_refused_before_anything_runs():
    # A mistyped --dry-run under sudo must not turn into a real run.
    result = script("--dry-run", "--dryrun", env=NO_PACKAGES)
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


def test_spark_gets_its_cache_folders_from_root():
    # spark can't write its home, so the units that run engines or pull models set XDG_CACHE_HOME
    # and CUDA_CACHE_PATH to these. Root creates them directly under its own parent, never inside a
    # folder spark owns.
    for path in ("/var/lib/local-ai/cache", "/var/lib/local-ai/cuda-cache"):
        assert "-o spark -g spark -m 0750" in install_d_line(path)


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


# make install-units: root's own copies of what `spark apply` stages for root, the four units and
# the Compose project as `spark render` writes them (Task 6), read as the admin from that folder.
FAKE_RUNUSER = """#!/usr/bin/env bash
# Stands in for runuser -u USER -- COMMAND...: logs the call in $CALLS, then runs the command as
# whoever runs the tests, since only root can switch users. $SWAP_ON_READ, "PATH|fifo" or
# "PATH|zero", swaps the file the command reads for a FIFO or a link to /dev/zero first, as
# something racing bootstrap between its check and its read could.
echo "runuser $*" >> "$CALLS"
while [[ $# -gt 0 && "$1" != "--" ]]; do shift; done
shift
for last; do :; done
if [[ -n "${SWAP_ON_READ:-}" && "$last" == "${SWAP_ON_READ%|*}" ]]; then
  rm -f "$last"
  if [[ "${SWAP_ON_READ#*|}" == fifo ]]; then mkfifo "$last"; else ln -s /dev/zero "$last"; fi
fi
exec "$@"
"""

FAKE_INSTALL = """#!/usr/bin/env bash
# Stands in for GNU install, and logs each call in $CALLS. `install -d [-o U] [-g G] [-m MODE] DIR...`
# makes each folder with MODE; `install [-o U] [-g G] [-m MODE] SRC DEST` replaces DEST with a copy
# of SRC, a link at DEST included and never followed, as GNU install does. Only root can set an
# owner, so the owner and group are left as they are.
echo "install $*" >> "$CALLS"
dirs=0 mode=0755
while [[ $# -gt 0 ]]; do
  case "$1" in
    -d) dirs=1; shift ;;
    -o|-g) shift 2 ;;
    -m) mode="$2"; shift 2 ;;
    *) break ;;
  esac
done
if (( dirs )); then
  for dir in "$@"; do mkdir -p "$dir"; chmod "$mode" "$dir"; done
else
  rm -f "$2"
  cp "$1" "$2"
  chmod "$mode" "$2"
fi
"""

FAKE_SYSTEMD = """#!/usr/bin/env bash
# Stands in for systemctl as make install-units uses it. daemon-reload and enable are logged in $CALLS
# and change $SYSTEMD_STATE: a reload leaves its time there, and enable records each unit, which
# is-enabled then reads. `show --property=NeedDaemonReload --value UNIT...` says yes for a unit
# whose file in $BOOTSTRAP_UNIT_DIR is newer than the last reload, as systemd does for a loaded unit.
state="$SYSTEMD_STATE"
case "$1" in
  daemon-reload) echo "systemctl $*" >> "$CALLS"; touch "$state/reloaded" ;;
  enable) echo "systemctl $*" >> "$CALLS"; shift; printf '%s\\n' "$@" >> "$state/enabled" ;;
  is-enabled) for unit; do :; done; [[ -f "$state/enabled" ]] && grep -qxF "$unit" "$state/enabled" ;;
  show)
    shift
    for unit; do
      case "$unit" in --*) continue ;; esac
      if [[ -f "$state/reloaded" && "$BOOTSTRAP_UNIT_DIR/$unit" -nt "$state/reloaded" ]]; then echo yes; else echo no; fi
    done
    ;;
  *) echo "fake systemctl: unexpected: $*" >&2; exit 1 ;;
esac
"""

ROOT_UNITS = ["local-ai-llama-swap.service", "local-ai-brake.service", "local-ai-compose.service",
              "local-ai-pull.service"]
# The order install-units reads and installs them in: the units, then the Compose project.
ROOT_FILES = [f"systemd/{unit}" for unit in ROOT_UNITS] + ["compose/compose.yaml", "compose/searxng/settings.yml"]


def rendered_roots() -> dict[str, str]:
    """What `spark apply` stages for root: the four units and the Compose project, as `spark render`
    writes them (Task 6)."""
    from spark.registry import load_registry
    from spark.render import installed_path, render
    from spark.versions import load_versions

    fixtures = Path(__file__).parent / "fixtures"
    files = render(load_registry(fixtures / "models.yaml"), load_versions(fixtures / "versions.yaml"),
                   (fixtures / "models.yaml").read_text(), templates=ROOT / "stack/templates")
    return {rel: text for rel, text in files.items() if installed_path(rel) is not None}


def built_env(tmp_path: Path, bindir: Path, **settings: str) -> dict[str, str]:
    """An environment built, not copied, as gpu_env's is: PATH with the fakes in `bindir` first, a
    HOME of its own under `tmp_path`, and `settings`. Nothing else of the test's own environment."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return {"PATH": f"{bindir}:{os.environ['PATH']}", "HOME": str(home), **settings}


def install_env(tmp_path: Path) -> dict[str, str]:
    """Stand-ins for make install-units: the staging folder `spark apply` writes, holding what it
    stages for root; root's two folders; and runuser, install and systemctl, which log to
    tmp_path/calls and change nothing outside tmp_path. The environment is built (built_env)."""
    for folder in ("bin", "units", "systemd"):
        (tmp_path / folder).mkdir()
    for rel, text in rendered_roots().items():
        (tmp_path / "stage" / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "stage" / rel).write_text(text)
    for name, text in (("runuser", FAKE_RUNUSER), ("install", FAKE_INSTALL), ("systemctl", FAKE_SYSTEMD)):
        (tmp_path / "bin" / name).write_text(text)
        (tmp_path / "bin" / name).chmod(0o755)
    return built_env(
        tmp_path,
        tmp_path / "bin",
        CALLS=str(tmp_path / "calls"),
        SYSTEMD_STATE=str(tmp_path / "systemd"),
        BOOTSTRAP_STAGED=str(tmp_path / "stage"),
        BOOTSTRAP_UNIT_DIR=str(tmp_path / "units"),
        BOOTSTRAP_COMPOSE_DIR=str(tmp_path / "compose"),
        # Root's copies must be root's own. Here whoever runs the tests stands in for root.
        BOOTSTRAP_ROOT_USER=pwd.getpwuid(os.getuid()).pw_name,
        SUDO_USER="alice",
    )


def install_units(env: dict[str, str], answer: str = "") -> subprocess.CompletedProcess[str]:
    """make install-units for real, not its dry run, against install_env's stand-ins, with `answer`
    typed at its question (see real_hold)."""
    return subprocess.run(
        ["bash", "-c", 'source "$1" --dry-run && DRY_RUN=0 && install_units', "bash", str(SCRIPT)],
        input=answer,
        capture_output=True,
        text=True,
        env=env,
    )


def copy_path(tmp_path: Path, rel: str) -> Path:
    """Where the stand-ins keep root's copy of a staged file."""
    top, _, rest = rel.partition("/")
    return tmp_path / ("units" if top == "systemd" else "compose") / rest


def installed(tmp_path: Path) -> dict[str, str]:
    """Root's copies, as the stand-ins hold them, keyed as `spark apply` stages them."""
    return {rel: copy_path(tmp_path, rel).read_text() for rel in ROOT_FILES if copy_path(tmp_path, rel).exists()}


def calls(tmp_path: Path) -> list[str]:
    path = tmp_path / "calls"
    return path.read_text().splitlines() if path.exists() else []


def changes(tmp_path: Path) -> list[str]:
    """What the stand-ins were asked to change: everything logged but runuser's reads."""
    return [line for line in calls(tmp_path) if not line.startswith("runuser ")]


def test_install_units_shows_what_root_will_run_then_asks_and_installs_it(tmp_path):
    result = install_units(install_env(tmp_path), "y\n")
    assert result.returncode == 0, result.stderr
    # Nothing is installed yet, so it shows the whole of every file before it asks.
    for rel in ROOT_FILES:
        assert f"--- installed: {copy_path(tmp_path, rel)}" in result.stdout
        assert f"+++ staged: {tmp_path}/stage/{rel}" in result.stdout
    assert installed(tmp_path) == rendered_roots()  # every file staged for root, nothing else
    for rel in ROOT_FILES:
        assert copy_path(tmp_path, rel).stat().st_mode & 0o777 == 0o644
    for folder in ("compose", "compose/searxng"):
        assert (tmp_path / folder).stat().st_mode & 0o777 == 0o755
    assert changes(tmp_path)[0] == f"install -d -o root -g root -m 0755 {tmp_path}/compose {tmp_path}/compose/searxng"
    copies = [line for line in changes(tmp_path)[1:-2]]
    assert [line.split()[-1] for line in copies] == [str(copy_path(tmp_path, rel)) for rel in ROOT_FILES]
    assert all(line.startswith("install -o root -g root -m 0644 ") for line in copies)
    assert changes(tmp_path)[-2:] == [
        "systemctl daemon-reload",
        "systemctl enable local-ai-llama-swap.service local-ai-brake.service local-ai-compose.service",
    ]  # the pull unit runs only when asked
    assert "make apply" in result.stdout.splitlines()[-1]


def test_install_units_reads_each_staged_file_as_the_admin_never_as_root(tmp_path):
    install_units(install_env(tmp_path), "y\n")
    reads = [line for line in calls(tmp_path) if line.startswith("runuser ")]
    assert reads == [f"runuser -u alice -- timeout 10 head -c 65537 -- {tmp_path}/stage/{rel}"
                     for rel in ROOT_FILES]  # within 10 s and one byte past the 64 KiB cap


@pytest.mark.parametrize("answer", ["n\n", "\n", ""], ids=["no", "enter", "end-of-input"])
def test_anything_but_yes_installs_nothing(tmp_path, answer):
    result = install_units(install_env(tmp_path), answer)
    assert result.returncode == 1 and "nothing was installed" in result.stderr
    assert installed(tmp_path) == {} and changes(tmp_path) == []


@pytest.mark.parametrize("planted", ["link", "folder", "missing"])
def test_a_staged_file_that_isnt_a_regular_file_is_refused(tmp_path, planted):
    # The staging folder is the admin's, so anything running as the admin can plant a link there. A
    # link to a file only root can read must never become a copy root installs, or shows.
    env = install_env(tmp_path)
    secret = tmp_path / "shadow"
    secret.write_text("root's own secret\n")
    brake = tmp_path / "stage/systemd/local-ai-brake.service"
    brake.unlink()
    if planted == "link":
        brake.symlink_to(secret)
    elif planted == "folder":
        brake.mkdir()
    result = install_units(env, "y\n")
    assert result.returncode == 1
    assert f"{brake} is missing or isn't a regular file" in result.stderr
    assert "root's own secret" not in result.stdout + result.stderr
    assert installed(tmp_path) == {} and changes(tmp_path) == []


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any file")
def test_a_staged_file_the_admin_cant_read_is_refused(tmp_path):
    env = install_env(tmp_path)
    (tmp_path / "stage/systemd/local-ai-brake.service").chmod(0)
    result = install_units(env, "y\n")
    assert result.returncode == 1 and "alice can't read" in result.stderr
    assert installed(tmp_path) == {} and changes(tmp_path) == []


@pytest.mark.parametrize("sudo_user", [None, "root"], ids=["no-sudo-user", "root"])
def test_install_units_needs_sudo_from_the_admins_own_account(tmp_path, sudo_user):
    env = install_env(tmp_path)
    if sudo_user is None:
        del env["SUDO_USER"]
    else:
        env["SUDO_USER"] = sudo_user
    result = install_units(env, "y\n")
    assert result.returncode == 1 and "with sudo from your own account" in result.stderr
    assert calls(tmp_path) == []  # it read nothing and installed nothing


def test_run_again_with_nothing_changed_it_changes_nothing(tmp_path):
    env = install_env(tmp_path)
    install_units(env, "y\n")
    (tmp_path / "calls").unlink()
    result = install_units(env)  # no answer: it must not ask
    assert result.returncode == 0, result.stderr
    assert "nothing to install" in result.stdout and "--- installed" not in result.stdout
    assert changes(tmp_path) == []  # no install, no reload, no enable


def test_only_what_changed_is_shown_and_installed_again(tmp_path):
    env = install_env(tmp_path)
    install_units(env, "y\n")
    (tmp_path / "calls").unlink()
    brake = tmp_path / "stage/systemd/local-ai-brake.service"
    brake.write_text(brake.read_text() + "# a new line\n")
    result = install_units(env, "y\n")
    assert result.returncode == 0, result.stderr
    assert "+# a new line" in result.stdout and "local-ai-llama-swap.service" not in result.stdout
    assert [line.split()[-1] for line in changes(tmp_path) if line.startswith("install ")] == [
        str(tmp_path / "units/local-ai-brake.service")]
    assert "systemctl daemon-reload" in changes(tmp_path)
    assert not any(line.startswith("systemctl enable") for line in changes(tmp_path))  # enabled already


def test_a_copy_that_isnt_roots_own_regular_file_is_installed_again(tmp_path):
    env = install_env(tmp_path)
    install_units(env, "y\n")
    (tmp_path / "calls").unlink()
    (tmp_path / "units/local-ai-brake.service").chmod(0o664)  # group-writable
    pull = tmp_path / "units/local-ai-pull.service"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.write_text(pull.read_text())
    pull.unlink()
    pull.symlink_to(elsewhere)  # the same text, through a link
    (tmp_path / "compose").chmod(0o775)
    result = install_units(env, "y\n")
    assert result.returncode == 0, result.stderr
    assert f"{tmp_path}/units/local-ai-brake.service: the same text, but not root's own regular file" in result.stdout
    assert f"{pull}: a link, which root's own regular file replaces" in result.stdout
    assert [line.split()[-1] for line in changes(tmp_path) if line.startswith("install ")] == [
        f"{tmp_path}/compose/searxng", str(tmp_path / "units/local-ai-brake.service"), str(pull)]
    assert not pull.is_symlink() and elsewhere.read_text() == pull.read_text()
    assert (tmp_path / "units/local-ai-brake.service").stat().st_mode & 0o777 == 0o644
    assert (tmp_path / "compose").stat().st_mode & 0o777 == 0o755


def test_a_copy_that_another_user_owns_is_installed_again(tmp_path):
    env = install_env(tmp_path)
    install_units(env, "y\n")
    (tmp_path / "calls").unlink()
    result = install_units({**env, "BOOTSTRAP_ROOT_USER": "nobody"}, "y\n")  # not whoever owns them
    assert result.returncode == 0, result.stderr
    assert len([line for line in changes(tmp_path) if line.startswith("install -o")]) == len(ROOT_FILES)


def test_run_again_after_a_run_cut_off_before_its_reload_it_reloads(tmp_path):
    # The earlier run installed a new unit and stopped before telling systemd, so systemd still
    # runs the old definition. The files match now, and systemd says it needs a reload.
    env = install_env(tmp_path)
    install_units(env, "y\n")
    (tmp_path / "calls").unlink()
    reloaded = (tmp_path / "systemd/reloaded").stat().st_mtime
    os.utime(tmp_path / "units/local-ai-brake.service", (reloaded + 5, reloaded + 5))
    result = install_units(env)
    assert result.returncode == 0 and "nothing to install" in result.stdout
    assert changes(tmp_path) == ["systemctl daemon-reload"]


def test_install_units_dry_run_shows_the_changes_and_changes_nothing(tmp_path):
    result = script("--install-units", "--dry-run", env=install_env(tmp_path))
    assert result.returncode == 0, result.stderr
    assert f"+++ staged: {tmp_path}/stage/systemd/local-ai-brake.service" in result.stdout
    assert [line for line in result.stdout.splitlines() if line.startswith("+ ")] == [
        f"+ install -d -o root -g root -m 0755 {tmp_path}/compose {tmp_path}/compose/searxng",
        *(f"+ install -o root -g root -m 0644 {tmp_path}/stage/{rel} {copy_path(tmp_path, rel)}" for rel in ROOT_FILES),
        "+ systemctl daemon-reload",
        "+ systemctl enable local-ai-llama-swap.service local-ai-brake.service local-ai-compose.service",
    ]
    assert calls(tmp_path) == [] and installed(tmp_path) == {}  # a dry run reads as whoever runs it


def test_make_install_units_runs_the_install_mode_under_sudo():
    # -s: GNU make 4 prints "Entering directory" lines under -C otherwise.
    recipe = subprocess.run(["make", "-s", "-n", "-C", str(ROOT), "install-units"],
                            capture_output=True, text=True, check=True)
    assert recipe.stdout.splitlines() == [
        "trap 'sudo -k' EXIT INT TERM HUP; sudo bash stack/host/bootstrap.sh --install-units"]
    preview = subprocess.run(["make", "-s", "-n", "-C", str(ROOT), "install-units-dry-run"],
                             capture_output=True, text=True, check=True)
    assert preview.stdout.splitlines() == ["bash stack/host/bootstrap.sh --install-units --dry-run"]
    phony = next(line for line in (ROOT / "Makefile").read_text().splitlines() if line.startswith(".PHONY:"))
    assert {"install-units", "install-units-dry-run"} <= set(phony.split()[1:])


def test_install_units_is_a_mode_of_its_own():
    # --dry-run first, so a broken guard would only print a plan.
    result = script("--dry-run", "--hold-gpu", "--install-units", env=NO_PACKAGES)
    assert result.returncode == 2 and result.stdout == ""
    assert "separate modes" in result.stderr  # not the unknown option it is before this task


def test_install_units_lists_the_units_render_writes():
    # bootstrap names the units by hand; this keeps its lists and render's the same.
    from spark.render import UNITS

    text = SCRIPT.read_text()
    listed = {name: re.search(rf"^{name}=\((.*)\)", text, re.MULTILINE).group(1).split()
              for name in ("ROOT_UNITS", "ENABLED_UNITS")}
    assert listed["ROOT_UNITS"] == list(UNITS)
    roots = rendered_roots()
    assert listed["ENABLED_UNITS"] == [unit for unit in UNITS if "\n[Install]\n" in roots[f"systemd/{unit}"]]


# A carriage return, an escape sequence and a C1 control such as CSI (U+009B, C2 9B in UTF-8) can
# make a terminal hide a line of the diff; a NUL makes diff print only "Binary files … differ".
# Built at run time, never written in the repo.
HIDDEN = "ExecStartPre=+/bin/sh -c 'touch /tmp/planted'"
CONTROL = {"cr-and-escape": HIDDEN + chr(13) + chr(27) + "[2K# nothing to see\n", "nul": HIDDEN + chr(0) + "\n",
           "c1-csi": HIDDEN + chr(0x9B) + "2K# nothing to see\n",
           "c1-first": HIDDEN + chr(0x80) + "\n", "c1-last": HIDDEN + chr(0x9F) + "\n"}


@pytest.mark.parametrize("mode", ["real", "dry-run"])
@pytest.mark.parametrize("payload", sorted(CONTROL))
def test_a_staged_file_with_control_characters_is_refused_before_anything_shows(tmp_path, mode, payload):
    env = install_env(tmp_path)
    brake = tmp_path / "stage/systemd/local-ai-brake.service"
    brake.write_text(brake.read_text() + CONTROL[payload])
    result = install_units(env, "y\n") if mode == "real" else script("--install-units", "--dry-run", env=env)
    assert result.returncode == 1
    assert f"{brake} holds control characters" in result.stderr
    assert "planted" not in result.stdout + result.stderr  # no line of it, no diff
    assert installed(tmp_path) == {} and changes(tmp_path) == []


def test_printable_text_beyond_ascii_is_no_control(tmp_path):
    # The templates' own em dashes (E2 80 94) pass, and so do C2 A0 to C2 BF, printable: only C2 80
    # to C2 9F encode C1 controls.
    env = install_env(tmp_path)
    brake = tmp_path / "stage/systemd/local-ai-brake.service"
    brake.write_text(brake.read_text() + "# " + chr(0xA0) + chr(0xB7) + " caf" + chr(0xE9) + "\n")
    assert install_units(env, "y\n").returncode == 0
    assert installed(tmp_path)["systemd/local-ai-brake.service"] == brake.read_text()


def test_a_staged_file_swapped_for_a_fifo_after_the_check_times_out(tmp_path):
    brake = tmp_path / "stage/systemd/local-ai-brake.service"
    env = {**install_env(tmp_path), "SWAP_ON_READ": f"{brake}|fifo", "BOOTSTRAP_READ_TIMEOUT": "1"}
    result = install_units(env, "y\n")
    assert result.returncode == 1 and f"alice can't read {brake}, or not within 1 s" in result.stderr
    assert installed(tmp_path) == {} and changes(tmp_path) == []


def test_a_staged_file_swapped_for_an_endless_device_is_cut_off_at_the_cap(tmp_path):
    brake = tmp_path / "stage/systemd/local-ai-brake.service"
    result = install_units({**install_env(tmp_path), "SWAP_ON_READ": f"{brake}|zero"}, "y\n")
    assert result.returncode == 1 and f"{brake} is over 65536 bytes" in result.stderr
    assert installed(tmp_path) == {} and changes(tmp_path) == []


def test_make_install_units_drops_sudos_cached_credential_whatever_happens(tmp_path):
    # Otherwise the `make apply` Dan runs next, in the same terminal, would run with sudo's cache warm.
    # "int" is a Ctrl-C at the password or the question, which reaches the terminal's whole foreground
    # job: here make gets a session of its own, and the stand-in sends SIGINT to all of it.
    import time

    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "sudo").write_text('#!/usr/bin/env bash\necho "sudo $*" >> "$CALLS"\n[[ "$1" == -k ]] && exit 0\n'
                                 'if [[ "$SUDO_DOES" == int ]]; then kill -INT 0; sleep 5; fi\nexit "$SUDO_DOES"\n')
    (bindir / "sudo").chmod(0o755)
    for does in ("0", "1", "int"):
        calls_file = tmp_path / f"calls-{does}"
        env = built_env(tmp_path, bindir, CALLS=str(calls_file), SUDO_DOES=does)
        result = subprocess.run(["make", "-s", "-C", str(ROOT), "install-units"], capture_output=True, text=True,
                                env=env, start_new_session=True)
        assert (result.returncode == 0) == (does == "0"), does  # the install's own outcome
        for _ in range(50):  # make can end before its shell's trap has run
            calls = calls_file.read_text().splitlines()
            if len(calls) > 1:
                break
            time.sleep(0.1)
        assert calls[0] == "sudo bash stack/host/bootstrap.sh --install-units", does
        assert calls[1:] and set(calls[1:]) == {"sudo -k"}, does


def test_the_install_units_environments_are_built_not_copied(tmp_path, monkeypatch):
    # As gpu_env's is (test_the_scripts_environment_is_built_not_copied): nothing of the test's own environment reaches
    # the script, or make, but PATH, behind the fakes. They get a HOME of their own, and the fakes' settings.
    monkeypatch.setenv("THE_TESTS_OWN", "never the script's")
    env = install_env(tmp_path)
    assert sorted(env) == ["BOOTSTRAP_COMPOSE_DIR", "BOOTSTRAP_ROOT_USER", "BOOTSTRAP_STAGED", "BOOTSTRAP_UNIT_DIR",
                           "CALLS", "HOME", "PATH", "SUDO_USER", "SYSTEMD_STATE"]
    assert env["PATH"] == f"{tmp_path / 'bin'}:{os.environ['PATH']}" and env["HOME"] != os.environ["HOME"]
    # What test_make_install_units_drops_sudos_cached_credential_whatever_happens gives make.
    sudo = built_env(tmp_path, tmp_path / "bin", CALLS="calls", SUDO_DOES="0")
    assert sorted(sudo) == ["CALLS", "HOME", "PATH", "SUDO_DOES"] and sudo["HOME"] != os.environ["HOME"]

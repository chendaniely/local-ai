import grp
import os
import pwd
import re
import shutil
import signal
import subprocess
import tempfile
import time
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
# Stands in for dpkg-query -W -f=FORMAT PATTERN...: prints each fixture package that matches a
# pattern, in FORMAT: ${db:Status-Abbrev} is the fixture's two status letters and a space,
# ${Package} the name and ${Version} the version. It exits 1 if any pattern matched nothing, as
# dpkg-query does.
status=0
format='${db:Status-Abbrev}\\t${Package}\\n'
abbrev='${db:Status-Abbrev}' package='${Package}' version='${Version}'
for arg in "$@"; do
  case "$arg" in -f=*) format="${arg#-f=}" ;; esac
done
for pat in "$@"; do
  case "$pat" in -*) continue ;; esac
  found=0
  while IFS=$'\\t' read -r st pkg ver; do
    if [[ $pkg == $pat ]]; then
      line=${format//"$abbrev"/"$st "}
      line=${line//"$package"/$pkg}
      line=${line//"$version"/$ver}
      printf '%b' "$line"
      found=1
    fi
  done < "$DPKG_FIXTURE"
  (( found )) || status=1
done
exit "$status"
"""

FAKE_APT_MARK = """#!/usr/bin/env bash
# Stands in for apt-mark. `hold PKG...` records each package in $APT_MARK_HELD, except any named in
# $APT_MARK_IGNORES (a hold that silently didn't take), and turns its first status letter in
# $DPKG_FIXTURE to h, as a real hold does; `unhold` turns it back to i. `showhold` lists what was
# recorded. `hold` and `unhold` are also logged in $CALLS, when a test sets it. $APT_MARK_CUT names a
# file holding how many holds to cut off, as Ctrl-C or TERM would: the shell that ran apt-mark and
# the script above it get TERM, and nothing is held. Those are $PPID and its parent, which are
# rehold's subshell and the script only while the hold runs in rehold's subshell, as upgrade day's
# holds do. Called any other way, as real_hold calls hold_gpu_stack, the parent's parent is something
# else, pytest itself there: so only upgrade_env sets $APT_MARK_CUT. $APT_MARK_KILL, likewise, names a
# file holding how many holds to kill as the OOM killer or a kill by pid would: SIGKILL to the shell
# that ran apt-mark alone, rehold's subshell, so the script above it gets no signal at all.
mark() {  # mark LETTER PKG...: set each package's first status letter in the fixture
  local letter="$1" st pkg ver
  shift
  while IFS=$'\\t' read -r st pkg ver; do
    if [[ " $* " == *" $pkg "* ]]; then st="$letter${st:1}"; fi
    printf '%s\\t%s\\t%s\\n' "$st" "$pkg" "$ver"
  done < "$DPKG_FIXTURE" > "$DPKG_FIXTURE.new"
  mv "$DPKG_FIXTURE.new" "$DPKG_FIXTURE"
}
case "$1" in
  hold)
    [[ -z "${CALLS:-}" ]] || echo "apt-mark $*" >> "$CALLS"
    if [[ -s "${APT_MARK_CUT:-}" ]] && (( $(cat "$APT_MARK_CUT") > 0 )); then
      echo $(( $(cat "$APT_MARK_CUT") - 1 )) > "$APT_MARK_CUT"
      script="$(ps -o ppid= -p "$PPID" | tr -d ' ')"
      kill -TERM "$PPID" "$script"
      exit 143
    fi
    if [[ -s "${APT_MARK_KILL:-}" ]] && (( $(cat "$APT_MARK_KILL") > 0 )); then
      echo $(( $(cat "$APT_MARK_KILL") - 1 )) > "$APT_MARK_KILL"
      kill -KILL "$PPID"
      exit 137
    fi
    shift
    took=()
    for pkg in "$@"; do
      [[ " ${APT_MARK_IGNORES:-} " == *" $pkg "* ]] && continue
      printf '%s\\n' "$pkg" >> "$APT_MARK_HELD"
      took+=("$pkg")
    done
    mark h "${took[@]}"
    ;;
  unhold)
    [[ -z "${CALLS:-}" ]] || echo "apt-mark $*" >> "$CALLS"
    shift
    mark i "$@"
    ;;
  showhold) if [[ -f "$APT_MARK_HELD" ]]; then sort -u "$APT_MARK_HELD"; fi ;;
  *) echo "fake apt-mark: unexpected: $*" >&2; exit 1 ;;
esac
"""


def dpkg_lines(installed: dict[str, str], versions: dict[str, str] | None = None) -> str:
    """The fixture as the stand-ins read it: status, package and version (1.0 unless given)."""
    return "".join(f"{status}\t{pkg}\t{(versions or {}).get(pkg, '1.0')}\n" for pkg, status in installed.items())


def gpu_env(tmp_path: Path, installed: dict[str, str]) -> dict[str, str]:
    """An environment whose dpkg-query reports `installed` (package → status) and whose apt-mark
    only writes to files, so even the real (not dry-run) hold changes nothing on this machine. It is
    built, not copied: nothing of the test's own environment but PATH, which finds the fakes and the
    tools the script runs, and a HOME of its own (Task 7's fix round 2: a launch that fails prints
    the environment it was given)."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, text in (("dpkg-query", FAKE_DPKG_QUERY), ("apt-mark", FAKE_APT_MARK)):
        (bindir / name).write_text(text)
        (bindir / name).chmod(0o755)
    fixture = tmp_path / "installed.tsv"
    fixture.write_text(dpkg_lines(installed))
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
# carries, and never a package state on the host that happens to stop the hold. Its Hugging Face
# cache is a stand-in that doesn't exist, as on a new box (Task 27), so the cache's step never reads
# the Spark's own cache, its units or its deployed registry either.
_NO_PACKAGES_DIR = tempfile.TemporaryDirectory(prefix="test-bootstrap-")
NO_PACKAGES = {**gpu_env(Path(_NO_PACKAGES_DIR.name), {}),
               "BOOTSTRAP_HF_CACHE": str(Path(_NO_PACKAGES_DIR.name) / "no-hf-cache")}


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
    # Task 9's fix round 1 (M2): it drops sudo's cached credential on the way out, as install-units does.
    assert "trap 'sudo -k' EXIT INT TERM HUP; sudo bash stack/host/bootstrap.sh --hold-gpu" in recipe.stdout.splitlines()
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


# Upgrade day. The box before it: the set held (hi), and one package held for another reason.
HELD_BEFORE = {**{pkg: "hi" if pkg in GPU_SET else status for pkg, status in INSTALLED.items()},
               "docker-ce": "hi"}
OLD, NEW = "7.0.0-1019-nvidia", "7.0.0-1020-nvidia"
# The set released (ii), as apt-mark unhold leaves it.
RELEASED = {pkg: "ii" if pkg in GPU_SET else status for pkg, status in HELD_BEFORE.items()}
# After apt moved the set: released, the new kernel's modules in, the old kernel's removed.
AFTER = {**RELEASED, f"linux-modules-nvidia-580-open-{NEW}": "ii", f"linux-modules-nvidia-580-open-{OLD}": "rc"}
NEW_SET = GPU_SET - {f"linux-modules-nvidia-580-open-{OLD}"} | {f"linux-modules-nvidia-580-open-{NEW}"}

# `apt-get -s dist-upgrade` for a set that moves cleanly: a new kernel with its modules.
GOOD_PLAN = f"""NOTE: This is only a simulation!
Inst linux-image-{NEW} (7.0.0-1020.20 example [arm64])
Inst linux-modules-nvidia-580-open-{NEW} (7.0.0-1020.20 example [arm64])
Inst linux-image-nvidia-hwe-24.04 [7.0.0-1019.19] (7.0.0-1020.20 example [arm64])
Inst linux-modules-nvidia-580-open-nvidia-hwe-24.04 [7.0.0-1019.19] (7.0.0-1020.20 example [arm64])
Remv linux-modules-nvidia-580-open-{OLD} [7.0.0-1019.19]
Conf linux-image-{NEW} (7.0.0-1020.20 example [arm64])
"""
# The driver moves before its modules for the new kernel exist: apt drops the modules metapackage.
PLAN_WITHOUT_THE_METAPACKAGE = f"""Inst linux-image-{NEW} (7.0.0-1020.20 example [arm64])
Inst linux-image-nvidia-hwe-24.04 [7.0.0-1019.19] (7.0.0-1020.20 example [arm64])
Inst nvidia-driver-580-open [580.178-0ubuntu1] (580.200-0ubuntu1 example [arm64])
Remv linux-modules-nvidia-580-open-nvidia-hwe-24.04 [7.0.0-1019.19]
Remv linux-modules-nvidia-580-open-{OLD} [7.0.0-1019.19]
"""
# A new kernel whose modules aren't published yet.
PLAN_WITHOUT_MODULES = f"""Inst linux-image-{NEW} (7.0.0-1020.20 example [arm64])
Inst linux-image-nvidia-hwe-24.04 [7.0.0-1019.19] (7.0.0-1020.20 example [arm64])
"""
# A new driver branch: the 580 metapackage and driver go, the 590 ones come.
PLAN_NEW_DRIVER_BRANCH = f"""Inst linux-image-{NEW} (7.0.0-1020.20 example [arm64])
Inst linux-modules-nvidia-590-open-{NEW} (7.0.0-1020.20 example [arm64])
Inst linux-modules-nvidia-590-open-nvidia-hwe-24.04 (7.0.0-1020.20 example [arm64])
Inst nvidia-driver-590-open (590.40-0ubuntu1 example [arm64])
Remv linux-modules-nvidia-580-open-nvidia-hwe-24.04 [7.0.0-1019.19]
Remv nvidia-driver-580-open [580.178-0ubuntu1]
"""

FAKE_APT_GET = """#!/usr/bin/env bash
# Stands in for apt-get: `-s dist-upgrade` prints $APT_PLAN. The real `dist-upgrade` answers as
# $APT_ANSWER says: yes, and it moves the set; no, and it aborts, changing nothing; partial, and it
# fails after moving it. A move makes the installed packages $DPKG_AFTER and the installed kernels
# $KERNELS_AFTER, and grub.cfg $GRUB_AFTER, since installing a kernel runs update-grub. Every call
# is logged.
moved() { cp "$DPKG_AFTER" "$DPKG_FIXTURE"; cp "$KERNELS_AFTER" "$KERNELS"; cp "$GRUB_AFTER" "$BOOTSTRAP_GRUB_CFG"; }
echo "apt-get $*" >> "$CALLS"
case "$*" in
  update) ;;
  "-s dist-upgrade") cat "$APT_PLAN" ;;
  dist-upgrade)
    case "$APT_ANSWER" in
      no) echo "Abort."; exit 1 ;;
      partial) moved; echo "E: Sub-process /usr/bin/dpkg returned an error code (1)" >&2; exit 100 ;;
      *) moved ;;
    esac
    ;;
  *) echo "fake apt-get: unexpected: $*" >&2; exit 1 ;;
esac
"""

FAKE_DPKG = """#!/usr/bin/env bash
echo "dpkg $*" >> "$CALLS"
"""

FAKE_SYSTEMCTL = """#!/usr/bin/env bash
# Stands in for systemctl. `is-active [--quiet] UNIT` says the unit's state, as systemd does: active for a unit in
# $ACTIVE_UNITS, the state $UNIT_STATES gives it (UNIT=STATE words), else inactive. It exits 0 only for active or
# reloading, and prints nothing with --quiet. A unit whose state is `unanswerable` fails as systemctl does without its
# bus: an error, no state, exit 1. `stop` is logged.
case "$1" in
  is-active)
    for unit; do :; done
    state=inactive
    if [[ " ${ACTIVE_UNITS:-} " == *" $unit "* ]]; then state=active; fi
    for pair in ${UNIT_STATES:-}; do if [[ "${pair%%=*}" == "$unit" ]]; then state="${pair#*=}"; fi; done
    if [[ "$state" == unanswerable ]]; then echo "Failed to connect to bus: No such file or directory" >&2; exit 1; fi
    [[ "$2" == --quiet ]] || echo "$state"
    [[ "$state" == active || "$state" == reloading ]]
    ;;
  stop) echo "systemctl $*" >> "$CALLS" ;;
  *) echo "fake systemctl: unexpected: $*" >&2; exit 1 ;;
esac
"""

FAKE_LINUX_VERSION = """#!/usr/bin/env bash
# Stands in for linux-version: `list` prints the kernels in the file $KERNELS, and `sort --reverse`
# puts the newest first (the test kernels sort by name).
case "$1" in
  list) cat "$KERNELS" ;;
  sort) sort -r ;;
esac
"""

FAKE_MODINFO = """#!/usr/bin/env bash
# Stands in for `modinfo -k KERNEL -F version nvidia`: only the kernels in $MODULE_KERNELS have one.
if [[ " $MODULE_KERNELS " == *" $2 "* ]]; then echo 580.200; else echo "modinfo: ERROR: Module nvidia not found." >&2; exit 1; fi
"""

FAKE_GRUB_EDITENV = """#!/usr/bin/env bash
# Stands in for `grub-editenv FILE list` as root runs it: prints FILE's name=value lines. Like the
# real one, it creates a missing FILE as an empty block, and fails on one that isn't a GRUB
# environment block.
[[ "$2" == list ]] || { echo "fake grub-editenv: unexpected: $*" >&2; exit 1; }
[[ -e "$1" ]] || printf '# GRUB Environment Block\\n' > "$1"
head -1 "$1" | grep -qx '# GRUB Environment Block' || { echo "grub-editenv: error: invalid environment block." >&2; exit 1; }
grep -v '^#' "$1" || true  # an empty block lists nothing, and that is no error
"""

# The root filesystem's UUID is in grub.cfg's linux lines and entry ids, and can be in grubenv. This
# one is made at run time, so no UUID, not even a made-up one, is written in the repo. Nothing the
# GRUB check prints may carry it.
ROOT_FS_UUID = "-".join(os.urandom(n).hex() for n in (4, 2, 2, 2, 6))
PREVIOUS = "7.0.0-1018-nvidia"  # a kernel older than OLD

# grub.cfg as Ubuntu's grub-mkconfig (GRUB 2.12) writes it, cut down to what the check reads and what
# carries the UUID: 00_header's default= lines, then 10_linux's entry 0 and its submenu.
GRUB_CFG = """### BEGIN /etc/grub.d/00_header ###
if [ -s $prefix/grubenv ]; then
  set have_grubenv=true
  load_env
fi
if [ "${initrdfail}" = 1 ]; then
   set next_entry="${prev_entry}"
   set prev_entry=
   save_env prev_entry
fi
if [ "${next_entry}" ] ; then
   set default="${next_entry}"
   set next_entry=
   save_env next_entry
   set boot_once=true
else
   set default=@DEFAULT@
fi
@EXTRA@
### BEGIN /etc/grub.d/10_linux ###
menuentry 'Ubuntu' --class ubuntu $menuentry_id_option 'gnulinux-simple-@UUID@' {
	search --no-floppy --fs-uuid --set=root @UUID@
	linux	/boot/vmlinuz-@ENTRY0@ root=UUID=@UUID@ ro  quiet splash $vt_handoff
	initrd	/boot/initrd.img-@ENTRY0@
}
submenu 'Advanced options for Ubuntu' $menuentry_id_option 'gnulinux-advanced-@UUID@' {
@SUBMENU@}
"""
SUBMENU_ENTRY = """	menuentry 'Ubuntu, with Linux @KERNEL@' --class ubuntu $menuentry_id_option 'gnulinux-@KERNEL@-advanced-@UUID@' {
		search --no-floppy --fs-uuid --set=root @UUID@
		linux	/boot/vmlinuz-@KERNEL@ root=UUID=@UUID@ ro  quiet splash $vt_handoff
		initrd	/boot/initrd.img-@KERNEL@
	}
"""
# What 00_header adds when the default is a bare title nested in the submenu: a third default= line.
TITLE_REWRITE = ('if [ "${default}" = "Ubuntu, with Linux @KERNEL@" ]; then '
                 'default="Advanced options for Ubuntu>Ubuntu, with Linux @KERNEL@"; fi')


def grub_cfg(*kernels: str, default: str = '"0"', extra: str = "", bare: bool = False) -> str:
    """grub.cfg for `kernels` in menu order, entry 0 first, GRUB starting `default`. `extra` is
    another line after the default= lines; `bare` drops `set` from the else branch's default=."""
    submenu = "".join(SUBMENU_ENTRY.replace("@KERNEL@", kernel) for kernel in kernels)
    text = GRUB_CFG.replace("@SUBMENU@", submenu).replace("@ENTRY0@", kernels[0])
    text = text.replace("@DEFAULT@", default).replace("@EXTRA@", extra.replace("@KERNEL@", kernels[-1]))
    if bare:
        text = text.replace("   set default=" + default, "   default=" + default)
    return text.replace("@UUID@", ROOT_FS_UUID)


def grubenv(**entries: str) -> str:
    """A GRUB environment block as grub-editenv writes it: its header, name=value lines, then # to
    1024 bytes."""
    text = "# GRUB Environment Block\n" + "".join(f"{name}={value}\n" for name, value in entries.items())
    return text + "#" * (1024 - len(text))


def entry_id(kernel: str) -> str:
    """The submenu entry's id for `kernel`, as a default or a grubenv entry names it: it carries the UUID."""
    return f"gnulinux-advanced-{ROOT_FS_UUID}>gnulinux-{kernel}-advanced-{ROOT_FS_UUID}"


def upgrade_env(tmp_path: Path, plan: str, *, answer: str = "yes", after: dict[str, str] = AFTER,
                after_versions: dict[str, str] | None = None, module_kernels: str = f"{OLD} {NEW}",
                cut_holds: int = 0, kernels_after: tuple[str, ...] = (OLD, NEW),
                grub_before: str | None = None, grub_after: str | None = None,
                env_block: str | None = None) -> dict[str, str]:
    """gpu_env, plus stand-ins for everything upgrade day runs. Nothing touches this machine: the
    fakes only log to tmp_path/calls. Before the move OLD is the newest kernel and GRUB boots it;
    the move installs `kernels_after`, and update-grub leaves `grub_after`, NEW first unless given."""
    env = gpu_env(tmp_path, HELD_BEFORE)
    for name, text in (("apt-get", FAKE_APT_GET), ("dpkg", FAKE_DPKG), ("systemctl", FAKE_SYSTEMCTL),
                       ("linux-version", FAKE_LINUX_VERSION), ("modinfo", FAKE_MODINFO),
                       ("grub-editenv", FAKE_GRUB_EDITENV)):
        (tmp_path / "bin" / name).write_text(text)
        (tmp_path / "bin" / name).chmod(0o755)
    (tmp_path / "plan").write_text(plan)
    (tmp_path / "after.tsv").write_text(dpkg_lines(after, after_versions))
    (tmp_path / "cut").write_text(str(cut_holds))
    (tmp_path / "kernels").write_text(f"{PREVIOUS}\n{OLD}\n")
    (tmp_path / "kernels.after").write_text("".join(f"{kernel}\n" for kernel in (PREVIOUS, *kernels_after)))
    (tmp_path / "grub.cfg").write_text(grub_before or grub_cfg(OLD, PREVIOUS))
    (tmp_path / "grub.cfg.after").write_text(grub_after or grub_cfg(NEW, OLD, PREVIOUS))
    (tmp_path / "grubenv").write_text(env_block or grubenv(saved_entry="", next_entry=""))
    return {**env, "CALLS": str(tmp_path / "calls"), "APT_PLAN": str(tmp_path / "plan"),
            "DPKG_AFTER": str(tmp_path / "after.tsv"), "APT_ANSWER": answer,
            "ACTIVE_UNITS": "local-ai-llama-swap.service local-ai-brake.service",
            "KERNELS": str(tmp_path / "kernels"), "KERNELS_AFTER": str(tmp_path / "kernels.after"),
            "MODULE_KERNELS": module_kernels, "APT_MARK_CUT": str(tmp_path / "cut"),
            "BOOTSTRAP_GRUB_CFG": str(tmp_path / "grub.cfg"), "GRUB_AFTER": str(tmp_path / "grub.cfg.after"),
            "BOOTSTRAP_GRUBENV": str(tmp_path / "grubenv")}


def real_upgrade(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Upgrade day for real, not its dry run, against upgrade_env's fakes (see real_hold). Whatever
    happens, nothing it prints carries the root filesystem's UUID."""
    result = subprocess.run(
        ["bash", "-c", 'source "$1" --dry-run && DRY_RUN=0 && upgrade_gpu', "bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert ROOT_FS_UUID not in result.stdout + result.stderr
    return result


def sorted_set(packages: set[str]) -> str:
    return " ".join(sorted(packages))


START_THE_STACK = "systemctl start local-ai-llama-swap.service local-ai-brake.service"
RECOVERY = "'If it goes wrong' in website/how-to/updates.md"


def test_upgrade_day_moves_the_set_and_holds_it_again(tmp_path):
    result = real_upgrade(upgrade_env(tmp_path, GOOD_PLAN))
    assert result.returncode == 0, result.stderr
    assert calls(tmp_path) == [
        f"apt-mark unhold {sorted_set(GPU_SET)}",  # the set, not docker-ce, held for its own reasons
        "dpkg --configure -a",
        "apt-get update",
        "apt-get -s dist-upgrade",
        "systemctl stop local-ai-llama-swap.service",
        "systemctl stop local-ai-brake.service",
        "apt-get dist-upgrade",
        f"apt-mark hold {sorted_set(NEW_SET)}",
    ]
    assert held(tmp_path) == NEW_SET
    assert f"==> GRUB boots {OLD}, the newest kernel" in result.stdout  # before anything moved
    assert f"==> ready: {NEW}, the newest kernel, has NVIDIA driver 580.200, and GRUB boots it" in result.stdout
    assert "sudo reboot, then make doctor" in result.stdout


@pytest.mark.parametrize("plan, reason", [
    (PLAN_WITHOUT_THE_METAPACKAGE, "it removes linux-modules-nvidia-580-open-nvidia-hwe-24.04"),
    (PLAN_WITHOUT_MODULES, f"it installs the kernel {NEW} with no NVIDIA modules for it"),
], ids=["removes-the-modules-metapackage", "kernel-without-modules"])
def test_a_plan_that_leaves_a_kernel_without_its_module_is_refused_before_anything_moves(tmp_path, plan, reason):
    result = real_upgrade(upgrade_env(tmp_path, plan))
    assert result.returncode == 1
    assert reason in result.stderr and "nothing was installed or removed" in result.stderr
    assert "try again next upgrade day" in result.stderr
    assert "apt-get dist-upgrade" not in calls(tmp_path)  # only the simulation ran
    assert not any(line.startswith("systemctl stop") for line in calls(tmp_path))
    assert calls(tmp_path)[-1] == f"apt-mark hold {sorted_set(GPU_SET)}"  # held again on the way out
    assert held(tmp_path) == GPU_SET


def test_a_new_driver_branch_is_refused_as_a_move_made_by_hand(tmp_path):
    result = real_upgrade(upgrade_env(tmp_path, PLAN_NEW_DRIVER_BRANCH))
    assert result.returncode == 1
    assert ("the driver branch changes: it removes linux-modules-nvidia-580-open-nvidia-hwe-24.04 "
            "and installs linux-modules-nvidia-590-open-nvidia-hwe-24.04") in result.stderr
    assert "planned and made by hand" in result.stderr
    assert "try again next upgrade day" not in result.stderr
    assert "apt-get dist-upgrade" not in calls(tmp_path)
    assert held(tmp_path) == GPU_SET


def test_answering_no_holds_the_set_again_and_says_how_to_start_the_stack(tmp_path):
    result = real_upgrade(upgrade_env(tmp_path, GOOD_PLAN, answer="no"))
    assert result.returncode == 1
    assert calls(tmp_path)[-2:] == ["apt-get dist-upgrade", f"apt-mark hold {sorted_set(GPU_SET)}"]
    assert held(tmp_path) == GPU_SET
    # The hold letter went from h to i and back, and nothing else changed: nothing moved, so
    # starting the stack again is safe.
    assert START_THE_STACK in result.stderr and RECOVERY not in result.stderr


def test_a_no_whose_hold_stops_still_says_how_to_start_the_stack(tmp_path):
    # Dan answers no, and the way out's hold then stops on a hold that didn't take. Nothing moved,
    # so the verdict is still the start hint, with no warning against a reboot; the hold's own
    # message says what is left to do.
    env = {**upgrade_env(tmp_path, GOOD_PLAN, answer="no"), "APT_MARK_IGNORES": "linux-image-nvidia-hwe-24.04"}
    result = real_upgrade(env)
    assert result.returncode == 1
    assert "did not hold" in result.stderr and "then run make hold-gpu" in result.stderr
    assert held(tmp_path) == GPU_SET - {"linux-image-nvidia-hwe-24.04"}
    assert START_THE_STACK in result.stderr
    assert "reboot" not in result.stderr and RECOVERY not in result.stderr


def test_a_kernel_without_a_module_stops_the_reboot(tmp_path):
    # apt reported success, but the newest kernel, which GRUB boots next by default, has no NVIDIA
    # module.
    result = real_upgrade(upgrade_env(tmp_path, GOOD_PLAN, module_kernels=OLD))
    assert result.returncode == 1
    assert f"DON'T REBOOT — {NEW}" in result.stderr
    assert held(tmp_path) == NEW_SET  # held before the check
    # The set moved: no hint to reboot or start the stack, only the way to the recovery.
    assert "sudo reboot" not in result.stdout and "systemctl start" not in result.stderr
    assert RECOVERY in result.stderr


def test_a_move_that_fails_partway_points_at_the_recovery(tmp_path):
    # apt stops partway: the driver is unpacked but not configured, so the hold can't hold it.
    half_moved = {**AFTER, "nvidia-driver-580-open": "iU"}
    result = real_upgrade(upgrade_env(tmp_path, GOOD_PLAN, answer="partial", after=half_moved))
    assert result.returncode == 1
    assert "nvidia-driver-580-open (iU)" in result.stderr and "run make hold-gpu" in result.stderr
    assert "systemctl start" not in result.stderr and RECOVERY in result.stderr


def test_a_move_that_only_changes_versions_counts_as_moved(tmp_path):
    # The same packages in the same states, the driver at a new version, and then apt fails: the set
    # moved, so there is no hint to start the stack or reboot.
    result = real_upgrade(upgrade_env(tmp_path, GOOD_PLAN, answer="partial", after=RELEASED,
                                      after_versions={"nvidia-driver-580-open": "580.200"}))
    assert result.returncode == 100  # apt's own status: the hold on the way out worked
    assert held(tmp_path) == GPU_SET
    assert "systemctl start" not in result.stderr and RECOVERY in result.stderr


def test_a_new_kernel_without_its_module_gets_no_start_hint(tmp_path):
    # apt installs a new kernel, which isn't in the set, and fails before its NVIDIA modules come:
    # the set looks as it was, but the next boot would start a kernel without a module.
    result = real_upgrade(upgrade_env(tmp_path, GOOD_PLAN, answer="partial", after=RELEASED, module_kernels=OLD))
    assert result.returncode == 100
    assert held(tmp_path) == GPU_SET
    assert "the newest kernel has no NVIDIA module" in result.stderr and RECOVERY in result.stderr
    assert "systemctl start" not in result.stderr


def test_a_cut_off_hold_is_tried_again_on_the_way_out(tmp_path):
    # Ctrl-C or TERM while the set is being held again must not leave it released.
    result = real_upgrade(upgrade_env(tmp_path, GOOD_PLAN, cut_holds=1))
    assert result.returncode == 143
    assert "holding the GPU set again" in result.stderr
    assert [line for line in calls(tmp_path) if line.startswith("apt-mark hold")] == [
        f"apt-mark hold {sorted_set(NEW_SET)}"] * 2
    assert held(tmp_path) == NEW_SET


def test_a_hold_cut_off_twice_says_what_is_left_to_do(tmp_path):
    result = real_upgrade(upgrade_env(tmp_path, GOOD_PLAN, cut_holds=2))
    assert result.returncode == 130
    assert "stopped while holding the GPU set again — run make hold-gpu" in result.stderr
    assert "don't reboot until step 5's checks pass" in result.stderr  # apt had run
    assert held(tmp_path) == set()


def test_a_signal_during_the_way_outs_own_hold_leaves_the_set_released(tmp_path):
    # After a "no", the way out's hold is the first one, and it isn't tried again: one signal there
    # leaves the set released, and says what to do.
    result = real_upgrade(upgrade_env(tmp_path, GOOD_PLAN, answer="no", cut_holds=1))
    assert result.returncode == 130
    assert "stopped while holding the GPU set again — run make hold-gpu" in result.stderr
    assert held(tmp_path) == set()


@pytest.mark.parametrize("plan, answer", [(GOOD_PLAN, "no"), (PLAN_WITHOUT_MODULES, "yes")],
                         ids=["after-a-no", "after-a-refused-plan"])
def test_a_hold_killed_on_its_own_on_the_way_out_says_run_make_hold_gpu(tmp_path, plan, answer):
    # Task 10's review (M1). A signal that kills only the hold's subshell, as the OOM killer or a kill by pid would,
    # never reaches the script's trap. The set is still released: the way out says to run make hold-gpu, as after a
    # signal that did reach the script, and never to start the stack.
    env = {**upgrade_env(tmp_path, plan, answer=answer), "APT_MARK_KILL": str(tmp_path / "kill")}
    (tmp_path / "kill").write_text("1")
    result = real_upgrade(env)
    assert result.returncode == 130
    assert "stopped while holding the GPU set again — run make hold-gpu" in result.stderr
    assert "systemctl start" not in result.stderr
    assert held(tmp_path) == set()


def test_a_hold_after_the_move_killed_twice_says_run_make_hold_gpu_and_not_to_reboot(tmp_path):
    # The hold after apt's move is tried once more, whatever cut it off; killed again, the set stays released.
    env = {**upgrade_env(tmp_path, GOOD_PLAN), "APT_MARK_KILL": str(tmp_path / "kill")}
    (tmp_path / "kill").write_text("2")
    result = real_upgrade(env)
    assert result.returncode == 130
    assert [line for line in calls(tmp_path) if line.startswith("apt-mark hold")] == [
        f"apt-mark hold {sorted_set(NEW_SET)}"] * 2
    assert "stopped while holding the GPU set again — run make hold-gpu" in result.stderr
    assert "don't reboot until step 5's checks pass" in result.stderr
    assert "systemctl start" not in result.stderr and "sudo reboot" not in result.stdout
    assert held(tmp_path) == set()


def test_the_way_out_sets_its_signal_trap_before_it_clears_its_exit_trap(tmp_path):
    # Task 10's review (M1): with the EXIT trap cleared first, a signal between the two runs upgrade_gpu's own
    # `exit 1xx` with no way out left, and the script ends without a word, the set released.
    env = upgrade_env(tmp_path, GOOD_PLAN)
    result = subprocess.run(
        ["bash", "-c", 'source "$1" --dry-run && DRY_RUN=0 REHELD=1 && '
                       'trap() { echo "trap $*" >> "$CALLS"; builtin trap "$@"; } && on_upgrade_exit',
         "bash", str(SCRIPT)],
        capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
    assert calls(tmp_path) == ["trap cut_off_while_holding HUP INT TERM", "trap - EXIT"]


def test_a_grub_that_wont_boot_the_newest_kernel_refuses_before_anything_is_released(tmp_path):
    # Someone ran grub-reboot, so the next boot starts another entry. Found before the release, it
    # leaves the set held and nothing moved, as updates.md step 2 says.
    env = upgrade_env(tmp_path, GOOD_PLAN, env_block=grubenv(saved_entry="", next_entry=entry_id(PREVIOUS)))
    result = real_upgrade(env)
    assert result.returncode == 1
    assert "not moving the GPU set: grubenv's next_entry picks another entry for the next boot" in result.stderr
    assert "Nothing was released or moved" in result.stderr
    assert calls(tmp_path) == []  # no release, no dpkg, no apt
    assert (tmp_path / "installed.tsv").read_text() == dpkg_lines(HELD_BEFORE)  # still held


def test_a_grub_that_wont_boot_the_new_kernel_after_the_move_says_dont_reboot(tmp_path):
    # update-grub put the old kernel first after the move (GRUB_FLAVOUR_ORDER can): the next boot
    # would start OLD, whatever the new kernel has.
    result = real_upgrade(upgrade_env(tmp_path, GOOD_PLAN, grub_after=grub_cfg(OLD, NEW, PREVIOUS)))
    assert result.returncode == 1
    assert f"DON'T REBOOT — GRUB boots {OLD}, not {NEW}" in result.stderr and RECOVERY in result.stderr
    assert held(tmp_path) == NEW_SET  # held before the checks
    assert "sudo reboot" not in result.stdout and "systemctl start" not in result.stderr


def test_a_grub_refusal_after_a_move_that_moved_nothing_still_starts_nothing(tmp_path):
    # apt moved nothing in the set, but GRUB's default changed meanwhile: no hint to start the stack
    # or reboot, only the way to the recovery.
    env = upgrade_env(tmp_path, GOOD_PLAN, after=RELEASED, kernels_after=(OLD,),
                      grub_after=grub_cfg(OLD, PREVIOUS, default='"1"'))
    result = real_upgrade(env)
    assert result.returncode == 1
    assert "DON'T REBOOT — grub.cfg's default isn't entry 0" in result.stderr
    assert "GRUB may not boot the newest kernel. Don't reboot or start the stack yet" in result.stderr
    assert "systemctl start" not in result.stderr


def grub_env(tmp_path: Path) -> dict[str, str]:
    """grub_check's environment, built (built_env): grub-editenv's stand-in first on PATH, and the stand-in
    grub.cfg and grubenv."""
    return built_env(tmp_path, tmp_path / "bin", BOOTSTRAP_GRUB_CFG=str(tmp_path / "grub.cfg"),
                     BOOTSTRAP_GRUBENV=str(tmp_path / "grubenv"))


def grub_check(tmp_path: Path, kernel: str, cfg: str | None, env_block: str | None) -> subprocess.CompletedProcess[str]:
    """grub_boots KERNEL against a stand-in grub.cfg and grubenv; None leaves that file out. Nothing
    it prints may carry the root filesystem's UUID."""
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin/grub-editenv").write_text(FAKE_GRUB_EDITENV)
    (tmp_path / "bin/grub-editenv").chmod(0o755)
    for name, text in (("grub.cfg", cfg), ("grubenv", env_block)):
        if text is not None:
            (tmp_path / name).write_text(text)
    result = subprocess.run(
        ["bash", "-c", 'source "$1" --dry-run && grub_boots "$2"', "bash", str(SCRIPT), kernel],
        capture_output=True,
        text=True,
        env=grub_env(tmp_path),
    )
    assert ROOT_FS_UUID not in result.stdout + result.stderr
    return result


@pytest.mark.parametrize("default, env_block", [
    ('"0"', grubenv(saved_entry="", next_entry="")),
    ('"0"', grubenv(saved_entry=entry_id(OLD))),  # "0" never reads saved_entry
    ('"${saved_entry}"', grubenv()),
    ('"${saved_entry}"', grubenv(saved_entry="0")),
    ('"${saved_entry}"', grubenv(saved_entry="", next_entry="", prev_entry="")),  # as grub-reboot leaves them
], ids=["0", "0-with-saved-entry", "saved-none", "saved-0", "saved-empty"])
def test_the_stock_grub_boots_the_newest_kernel(tmp_path, default, env_block):
    result = grub_check(tmp_path, NEW, grub_cfg(NEW, OLD, default=default), env_block)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


@pytest.mark.parametrize("cfg, env_block, reason", [
    (grub_cfg(OLD, NEW), grubenv(), f"GRUB boots {OLD}, not {NEW}"),
    (grub_cfg(NEW, OLD, extra=TITLE_REWRITE), grubenv(), "grub.cfg's default= lines aren't the stock two"),
    (grub_cfg(NEW, OLD, bare=True), grubenv(), "grub.cfg's default= lines aren't the stock two"),
    (grub_cfg(NEW, OLD, default=f'"{entry_id(OLD)}"'), grubenv(), "grub.cfg's default isn't entry 0"),
    (grub_cfg(NEW, OLD), grubenv(next_entry=entry_id(OLD)), "grubenv's next_entry picks another entry"),
    (grub_cfg(NEW, OLD), grubenv(prev_entry=entry_id(OLD)), "grubenv's prev_entry picks another entry"),
    (grub_cfg(NEW, OLD, default='"${saved_entry}"'), grubenv(saved_entry=entry_id(OLD)),
     "grubenv's saved_entry picks another entry than 0"),
    (None, grubenv(), "can't read"),
    (grub_cfg(NEW, OLD), None, "can't read GRUB's environment block"),
    (grub_cfg(NEW, OLD), "saved_entry=0\n", "can't read GRUB's environment block"),  # grub-editenv refuses it
    (grub_cfg(NEW, OLD).replace(f"vmlinuz-{NEW}", "vmlinuz"), grubenv(), "entry 0 starts no vmlinuz-<version> kernel"),
    # Only a name that is a kernel version is ever printed, whatever grub.cfg holds.
    (grub_cfg(NEW, OLD).replace(f"vmlinuz-{NEW}", f"vmlinuz-{ROOT_FS_UUID}"), grubenv(),
     "entry 0 starts no vmlinuz-<version> kernel"),
], ids=["entry-0-an-older-kernel", "a-third-default-line", "a-bare-default", "an-id-as-the-default",
        "next-entry", "prev-entry", "saved-entry-not-0", "no-grub-cfg", "no-grubenv", "grubenv-read-fails",
        "entry-0-unnamed", "entry-0-not-a-version"])
def test_anything_else_says_why_and_never_what_it_read(tmp_path, cfg, env_block, reason):
    result = grub_check(tmp_path, NEW, cfg, env_block)
    assert result.returncode == 1 and reason in result.stdout, result
    # A check only reads: root's grub-editenv would create a missing grubenv, and pass on it.
    assert (tmp_path / "grubenv").exists() == (env_block is not None)


def test_no_newest_kernel_is_not_a_kernel_grub_boots(tmp_path):
    result = grub_check(tmp_path, "", grub_cfg(NEW, OLD), grubenv())
    assert result.returncode == 1 and "linux-version found no kernel" in result.stdout


def test_upgrade_gpu_dry_run_only_prints_the_steps(tmp_path):
    result = script("--upgrade-gpu", "--dry-run", env=upgrade_env(tmp_path, GOOD_PLAN))
    assert result.returncode == 0, result.stderr
    commands = [line.split("   (")[0] for line in result.stdout.splitlines() if line.startswith("+ ")]
    assert commands == [
        "+ grub_boots <the newest kernel>",
        f"+ apt-mark unhold {sorted_set(GPU_SET)}",
        "+ dpkg --configure -a",
        "+ apt-get update",
        "+ apt-get -s dist-upgrade",
        "+ systemctl stop local-ai-llama-swap.service",
        "+ systemctl stop local-ai-brake.service",
        "+ apt-get dist-upgrade",
        f"+ apt-mark hold {sorted_set(GPU_SET)}",
        "+ modinfo -k <the newest kernel> -F version nvidia",
        "+ grub_boots <the newest kernel>",
    ]
    assert calls(tmp_path) == []  # no stand-in was asked to change anything


def test_a_dry_run_whose_hold_would_stop_says_only_what_the_hold_says(tmp_path):
    # Task 10's scan (R12): a dry run releases nothing, so it must not say the set is "not held again yet".
    env = upgrade_env(tmp_path, GOOD_PLAN)
    (tmp_path / "installed.tsv").write_text(dpkg_lines({**HELD_BEFORE, "nvidia-driver-580-open": "iU"}))
    result = script("--upgrade-gpu", "--dry-run", env=env)
    assert result.returncode == 1
    assert "nvidia-driver-580-open (iU)" in result.stderr and "finish dpkg first" in result.stderr
    assert "not held again" not in result.stderr
    assert calls(tmp_path) == []


def test_a_dry_run_cut_off_by_a_signal_says_nothing_of_a_hold(tmp_path):
    # Task 10's review (M3): a dry run releases nothing, so it has no way out to hold the set again. A TERM or a
    # Ctrl-C during it ends it, and it says nothing of a hold. `run` stands in for the first step it prints, and
    # sends the signal. (Sourcing the script parses no options, so the dry run is DRY_RUN=1, set here.)
    env = upgrade_env(tmp_path, GOOD_PLAN)
    result = subprocess.run(
        ["bash", "-c", 'source "$1" --dry-run && DRY_RUN=1 && run() { kill -TERM $$; sleep 5; } && upgrade_gpu',
         "bash", str(SCRIPT)],
        capture_output=True, text=True, env=env)
    assert result.returncode == -signal.SIGTERM, result.stderr
    assert "hold" not in result.stderr
    assert calls(tmp_path) == []


def test_make_upgrade_gpu_refuses_to_start_outside_tmux(tmp_path):
    # A dropped SSH session in the middle of apt is the likeliest way to half-move the set.
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "sudo").write_text('#!/usr/bin/env bash\necho "sudo $*" >> "$CALLS"\nexit 1\n')
    (bindir / "sudo").chmod(0o755)
    env = built_env(tmp_path, bindir, TMUX="", CALLS=str(tmp_path / "calls"))
    result = subprocess.run(["make", "-s", "-C", str(ROOT), "upgrade-gpu"], capture_output=True, text=True, env=env)
    assert result.returncode != 0
    assert "tmux new -As upgrade" in result.stdout + result.stderr
    assert calls(tmp_path) == []  # sudo was never reached
    # -s: GNU make 4 prints "Entering directory" lines under -C otherwise.
    recipe = subprocess.run(["make", "-s", "-n", "-C", str(ROOT), "upgrade-gpu"],
                            capture_output=True, text=True, check=True)
    # It forgets sudo's cached credential on the way out (test_make_upgrade_gpu_drops_sudos_cached_credential_too).
    assert ("trap 'sudo -k' EXIT INT TERM HUP; sudo bash stack/host/bootstrap.sh --upgrade-gpu"
            in recipe.stdout.splitlines())
    phony = next(line for line in (ROOT / "Makefile").read_text().splitlines() if line.startswith(".PHONY:"))
    assert {"upgrade-gpu", "upgrade-gpu-dry-run"} <= set(phony.split()[1:])


@pytest.mark.parametrize("mode", ["--hold-gpu", "--install-units"])
def test_upgrade_gpu_is_a_mode_of_its_own(mode):
    # --dry-run first, so a broken guard would only print a plan.
    result = script("--dry-run", mode, "--upgrade-gpu", env=NO_PACKAGES)
    assert result.returncode == 2 and result.stdout == ""
    assert "separate modes" in result.stderr  # not the unknown option it is before this task


def test_bootstrap_installs_the_needrestart_override():
    line = next(line for line in dry_run() if "/etc/needrestart/conf.d/" in line)
    assert line.startswith("+ install -D -m 0644 ") and line.split()[-2].endswith("/stack/host/needrestart.conf")
    assert line.endswith(" /etc/needrestart/conf.d/local-ai.conf")


# How needrestart reads a /etc/needrestart/conf.d/*.conf file: as Perl, after its own config, which
# already overrides some services (dbus stands in for them). Prints each unit's restart setting:
# 0 is never restarted automatically; "default" is restarted when a library it uses was replaced.
NEEDRESTART_EVAL = r"""
our %nrconf = (override_rc => {qr(^dbus) => 0});
my $fn = shift;
eval do { local(@ARGV, $/) = $fn; <> };
die "Error parsing $fn: $@" if $@;
for my $unit (@ARGV) {
  my ($re) = grep { $unit =~ /$_/ } keys %{$nrconf{override_rc}};
  print "$unit ", (defined $re ? $nrconf{override_rc}{$re} : "default"), "\n";
}
"""


@pytest.mark.skipif(shutil.which("perl") is None, reason="perl not installed")
def test_needrestart_never_restarts_a_local_ai_unit():
    # Restart mode is automatic on this box, and llama-swap's unit stops every loaded model when it
    # restarts. The override must add to needrestart's own list, never replace it.
    units = ["local-ai-llama-swap.service", "local-ai-brake.service", "local-ai-compose.service",
             "local-ai-pull.service", "ssh.service", "dbus.service"]
    out = subprocess.run(["perl", "-e", NEEDRESTART_EVAL, str(ROOT / "stack/host/needrestart.conf"), *units],
                         capture_output=True, text=True, check=True).stdout
    assert out.splitlines() == [
        "local-ai-llama-swap.service 0",
        "local-ai-brake.service 0",
        "local-ai-compose.service 0",
        "local-ai-pull.service 0",
        "ssh.service default",
        "dbus.service 0",
    ]


def test_doctor_reads_the_holds_dry_run_as_it_is_printed(tmp_path):
    # `make doctor` judges the GPU set from this dry run, so the two agree on what the set is.
    from spark.doctor import judge_gpu_set

    held_set = {pkg: "hi" if status == "ii" else status for pkg, status in INSTALLED.items()}
    cases = {
        "held": (held_set, True, f"all {len(GPU_SET)} packages held"),
        "one-new": ({**held_set, "libcublas-13-0": "ii"}, False, f"1 of {len(GPU_SET)} packages aren't held"),
        "interrupted": ({**held_set, "nvidia-driver-580-open": "iU"}, False, "sudo dpkg --configure -a"),
        "nothing": ({}, False, "nothing installed matches"),
    }
    for name, (installed, ok, words) in cases.items():
        (tmp_path / name).mkdir()
        result = script("--hold-gpu", "--dry-run", env=gpu_env(tmp_path / name, installed))
        check = judge_gpu_set(result.returncode, result.stdout, result.stderr)
        assert check.ok is ok and words in check.detail, (name, check)


def test_doctor_expects_what_bootstrap_sets(tmp_path):
    # `make doctor` checks spark's folders and needrestart's override against what bootstrap sets them to (Task 10's
    # rulings R10 and R14), so the two agree. The Hugging Face cache isn't one of `directories`' (Task 27): the
    # cache's own step gives it to spark-pull, and its owner and mode are the ones that step sets and checks.
    from spark import doctor

    cache = Path(doctor.render.HF_HOME)
    for path, (user, group, mode) in doctor.SPARK_FOLDERS:
        if path == cache:
            continue
        assert f"-o {user} -g {group} -m {mode:04o}" in install_d_line(str(path)), path
    [(user, group, mode)] = [owner for path, owner in doctor.SPARK_FOLDERS if path == cache]
    hf = stand_in_cache(tmp_path)
    (tmp_path / "models.yaml").write_text(REGISTRY_WHISPER_TMP)
    result = cache_step(cache_env(tmp_path))
    assert result.returncode == 0, result.stderr
    assert f"chown -R -P --no-dereference {user}:{group} {hf}" in calls(tmp_path)
    assert f"chmod {mode:04o} {hf}" in calls(tmp_path)
    script_text = SCRIPT.read_text()
    assert f'HF_CACHE="${{BOOTSTRAP_HF_CACHE:-{cache}}}"' in script_text
    assert f'PULL_USER="${{BOOTSTRAP_PULL_USER:-{user}}}"' in script_text
    assert f'CACHE_GROUP="${{BOOTSTRAP_CACHE_GROUP:-{group}}}"' in script_text
    line = next(line for line in dry_run() if "/etc/needrestart/conf.d/" in line)
    assert line.split()[-2:] == [str(ROOT / doctor.NEEDRESTART_REPO), str(doctor.NEEDRESTART_CONF)]


def test_the_scripts_environment_is_built_not_copied(tmp_path):
    # Task 7's fix round 2: a launch that fails prints the environment it was given, and pytest prints the arguments
    # of a call in a failing assert (so names and homes are taken first, here). The script's environment holds
    # nothing of the test's own: PATH, to find the fakes and the tools, a HOME of its own, and the fakes' settings.
    # NO_PACKAGES is built at import, before any fixture runs, so it too must be built, not copied.
    env = gpu_env(tmp_path, {})
    names, import_names = sorted(env), sorted(NO_PACKAGES)
    home, test_home, import_home = env["HOME"], os.environ["HOME"], NO_PACKAGES["HOME"]
    assert names == ["APT_MARK_HELD", "DPKG_FIXTURE", "HOME", "PATH"]
    # Task 27: and the default dry run's stand-in for the Hugging Face cache, a path that doesn't exist.
    assert import_names == ["APT_MARK_HELD", "BOOTSTRAP_HF_CACHE", "DPKG_FIXTURE", "HOME", "PATH"]
    assert not Path(NO_PACKAGES["BOOTSTRAP_HF_CACHE"]).exists()
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
    # The Hugging Face cache was spark's here until Task 27, which gives it to spark-pull in a step of its own.
    assert "-o spark -g spark -m 0750" in install_d_line("/var/lib/local-ai/open-webui")
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


# make install-units: root's own copies of what `spark apply` stages for root, the units and the
# Compose project as `spark render` writes them (Task 6), read as the admin from that folder.
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
# whose file in $BOOTSTRAP_UNIT_DIR is newer than the last reload, as systemd does for a loaded unit,
# and keeps its arguments in $SYSTEMD_STATE/show. Given a template's name (`…@.service`), it fails
# as systemd 255 does: an error, exit 1, and nothing for the units after it (seen on the Spark,
# 2026-10-14).
state="$SYSTEMD_STATE"
case "$1" in
  daemon-reload) echo "systemctl $*" >> "$CALLS"; touch "$state/reloaded" ;;
  enable) echo "systemctl $*" >> "$CALLS"; shift; printf '%s\\n' "$@" >> "$state/enabled" ;;
  is-enabled) for unit; do :; done; [[ -f "$state/enabled" ]] && grep -qxF "$unit" "$state/enabled" ;;
  show)
    echo "systemctl $*" >> "$state/show"
    shift
    for unit; do
      case "$unit" in
        --*) continue ;;
        *@.service)
          echo "Failed to get properties: Unit name $unit is neither a valid invocation ID nor unit name." >&2
          exit 1
          ;;
      esac
      if [[ -f "$state/reloaded" && "$BOOTSTRAP_UNIT_DIR/$unit" -nt "$state/reloaded" ]]; then echo yes; else echo no; fi
    done
    ;;
  *) echo "fake systemctl: unexpected: $*" >&2; exit 1 ;;
esac
"""

ROOT_UNITS = ["local-ai-llama-swap.service", "local-ai-brake.service", "local-ai-compose.service",
              "local-ai-pull.service", "local-ai-front.socket", "local-ai-front.service",
              "local-ai-gate-status.socket", "local-ai-gate-control.socket", "local-ai-gate.service",
              "local-ai-notify@.service", "local-ai-brake-drill.service"]
# What install-units enables: every unit with an [Install]. The pull, the failure notifier and the
# S05 drill run only when started.
ENABLED_UNITS = ["local-ai-llama-swap.service", "local-ai-brake.service", "local-ai-compose.service",
                 "local-ai-front.socket", "local-ai-front.service", "local-ai-gate-status.socket",
                 "local-ai-gate-control.socket", "local-ai-gate.service"]
# The order install-units reads and installs them in: the units, then the Compose project.
ROOT_FILES = [f"systemd/{unit}" for unit in ROOT_UNITS] + ["compose/compose.yaml", "compose/searxng/settings.yml"]


def rendered_roots() -> dict[str, str]:
    """What `spark apply` stages for root: the units and the Compose project, as `spark render`
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
        f"systemctl enable {' '.join(ENABLED_UNITS)}",
    ]  # the pull, the notifier and the drill run only when started
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
        f"+ systemctl enable {' '.join(ENABLED_UNITS)}",
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


def bootstrap_lists() -> dict[str, list[str]]:
    """bootstrap's ROOT_UNITS and ENABLED_UNITS, as its arrays list them, over one line or several."""
    text = SCRIPT.read_text()
    return {name: re.search(rf"^{name}=\(([^)]*)\)", text, re.MULTILINE).group(1).split()
            for name in ("ROOT_UNITS", "ENABLED_UNITS")}


def test_install_units_lists_the_units_render_writes():
    # bootstrap names the units by hand; this keeps its lists and render's the same.
    from spark.render import UNITS

    listed = bootstrap_lists()
    assert listed["ROOT_UNITS"] == list(UNITS)
    roots = rendered_roots()
    assert listed["ENABLED_UNITS"] == [unit for unit in UNITS if "\n[Install]\n" in roots[f"systemd/{unit}"]]


def test_install_units_installs_and_enables_the_sockets():
    from spark.render import UNITS

    listed = bootstrap_lists()
    assert listed["ROOT_UNITS"] == list(UNITS) == ROOT_UNITS
    assert listed["ENABLED_UNITS"] == ENABLED_UNITS
    assert {"local-ai-front.socket", "local-ai-gate-status.socket", "local-ai-gate-control.socket",
            "local-ai-front.service", "local-ai-gate.service"} <= set(listed["ENABLED_UNITS"])
    assert not {"local-ai-notify@.service", "local-ai-pull.service", "local-ai-brake-drill.service"} & set(
        listed["ENABLED_UNITS"])


def test_install_units_never_asks_systemctl_show_about_a_template(tmp_path):
    # systemd 255 refuses `systemctl show` for a template's name, and then says nothing for the units after it, so
    # asking about local-ai-notify@.service would hide that the drill's unit, after it, needs a reload.
    env = install_env(tmp_path)
    install_units(env, "y\n")
    result = script("--install-units", "--dry-run", env=env)  # nothing changed, so it asks systemd
    assert result.returncode == 0, result.stderr
    [shown] = (tmp_path / "systemd/show").read_text().splitlines()
    units = shown.split()[4:]
    assert shown.split()[:4] == ["systemctl", "show", "--property=NeedDaemonReload", "--value"]
    assert "local-ai-brake-drill.service" in units and [unit for unit in units if unit.endswith("@.service")] == []
    assert units == [unit for unit in ROOT_UNITS if not unit.endswith("@.service")]
    # A run cut off before its reload, the drill's new copy installed: the next run reloads.
    (tmp_path / "calls").unlink()
    reloaded = (tmp_path / "systemd/reloaded").stat().st_mtime
    os.utime(tmp_path / "units/local-ai-brake-drill.service", (reloaded + 5, reloaded + 5))
    result = install_units(env)
    assert result.returncode == 0 and "nothing to install" in result.stdout
    assert changes(tmp_path) == ["systemctl daemon-reload"]


def test_the_polkit_step_names_the_seven_services():
    from spark.render import POLKIT_UNITS

    [line] = [line for line in dry_run() if line.startswith("==> polkit:")]
    assert "seven" in line and "four" not in line
    for unit in POLKIT_UNITS:
        assert unit.removeprefix("local-ai-").removesuffix(".service") in line, unit


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


# The stand-in for sudo in the test above, for the other targets that run sudo.
FAKE_SUDO = ('#!/usr/bin/env bash\necho "sudo $*" >> "$CALLS"\n[[ "$1" == -k ]] && exit 0\n'
             'if [[ "$SUDO_DOES" == int ]]; then kill -INT 0; sleep 5; fi\nexit "$SUDO_DOES"\n')


def forgets_sudos_credential_whatever_happens(tmp_path: Path, target: str, runs: str, **settings: str) -> None:
    """Runs `make TARGET` against FAKE_SUDO, which exits 0, exits 1 or sends a Ctrl-C to the whole job ("int"), and
    checks that each time it ran `runs`, then `sudo -k` and nothing else. `settings` go in make's environment."""
    import time

    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "sudo").write_text(FAKE_SUDO)
    (bindir / "sudo").chmod(0o755)
    for does in ("0", "1", "int"):
        calls_file = tmp_path / f"calls-{does}"
        env = built_env(tmp_path, bindir, CALLS=str(calls_file), SUDO_DOES=does, **settings)
        result = subprocess.run(["make", "-s", "-C", str(ROOT), target], capture_output=True, text=True, env=env,
                                start_new_session=True)
        assert (result.returncode == 0) == (does == "0"), (target, does)
        for _ in range(50):  # make can end before its shell's trap has run
            calls = calls_file.read_text().splitlines()
            if len(calls) > 1:
                break
            time.sleep(0.1)
        assert calls[0] == runs, (target, does)
        assert calls[1:] and set(calls[1:]) == {"sudo -k"}, (target, does)


@pytest.mark.parametrize(("target", "runs"), [("bootstrap", "sudo bash stack/host/bootstrap.sh"),
                                             ("hold-gpu", "sudo bash stack/host/bootstrap.sh --hold-gpu")])
def test_make_bootstrap_and_hold_gpu_drop_sudos_cached_credential_too(tmp_path, target, runs):
    # Task 9's fix round 1 (M2). The clone's own code runs next in that terminal (`make apply` runs spark from the
    # clone), so no target leaves sudo's cache warm, however it ends: a Ctrl-C at the password included ("int").
    recipe = subprocess.run(["make", "-s", "-n", "-C", str(ROOT), target], capture_output=True, text=True, check=True)
    assert recipe.stdout.splitlines() == [f"trap 'sudo -k' EXIT INT TERM HUP; {runs}"]
    forgets_sudos_credential_whatever_happens(tmp_path, target, runs)


def test_make_upgrade_gpu_drops_sudos_cached_credential_too(tmp_path):
    # Task 10's scan (R16), as for make bootstrap and make hold-gpu: nothing that runs next in that terminal rides
    # sudo's cache, and the reboot's sudo asks for the password again. The first line is the tmux check.
    runs = "sudo bash stack/host/bootstrap.sh --upgrade-gpu"
    recipe = subprocess.run(["make", "-s", "-n", "-C", str(ROOT), "upgrade-gpu"], capture_output=True, text=True,
                            check=True)
    assert recipe.stdout.splitlines()[1:] == [f"trap 'sudo -k' EXIT INT TERM HUP; {runs}"]
    forgets_sudos_credential_whatever_happens(tmp_path, "upgrade-gpu", runs, TMUX="tmux-stand-in")


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


def test_the_upgrade_environments_are_built_not_copied(tmp_path, monkeypatch):
    # Task 10's scan (R3), as for install_env: upgrade day's stand-ins, the GRUB check's and the tmux test's.
    monkeypatch.setenv("THE_TESTS_OWN", "never the script's")
    for folder in ("upgrade", "grub"):
        (tmp_path / folder).mkdir()
    upgrade = upgrade_env(tmp_path / "upgrade", GOOD_PLAN)
    assert sorted(upgrade) == ["ACTIVE_UNITS", "APT_ANSWER", "APT_MARK_CUT", "APT_MARK_HELD", "APT_PLAN",
                               "BOOTSTRAP_GRUBENV", "BOOTSTRAP_GRUB_CFG", "CALLS", "DPKG_AFTER", "DPKG_FIXTURE",
                               "GRUB_AFTER", "HOME", "KERNELS", "KERNELS_AFTER", "MODULE_KERNELS", "PATH"]
    grub = grub_env(tmp_path / "grub")
    assert sorted(grub) == ["BOOTSTRAP_GRUBENV", "BOOTSTRAP_GRUB_CFG", "HOME", "PATH"]
    # What test_make_upgrade_gpu_refuses_to_start_outside_tmux gives make.
    tmux = built_env(tmp_path, tmp_path / "bin", TMUX="", CALLS="calls")
    assert sorted(tmux) == ["CALLS", "HOME", "PATH", "TMUX"]
    assert os.environ["HOME"] not in (upgrade["HOME"], grub["HOME"], tmux["HOME"])


def test_the_apt_mark_stand_in_logs_only_when_asked_and_releases_as_apt_mark_does(tmp_path):
    # Task 10's scan (R17): its unhold, like its hold, logs to $CALLS only when a test sets it, and turns the status
    # letter from h back to i, as the real one does. The stand-in is run by its path, so the real apt-mark can't be
    # the one that runs (Task 10's review, M8).
    env = gpu_env(tmp_path, HELD_BEFORE)
    result = subprocess.run([str(tmp_path / "bin" / "apt-mark"), "unhold", "nvidia-driver-580-open"],
                            capture_output=True, text=True, env=env)
    assert (result.returncode, result.stderr) == (0, "")
    assert "ii\tnvidia-driver-580-open\t1.0\n" in (tmp_path / "installed.tsv").read_text()


# Task 27: bootstrap for Phase 2a. The users spark-front and spark-pull, the secrets folder root's alone, the gate's,
# launch's, whisper's and the pull's folders, the Hugging Face cache moved to spark-pull, and the failure notifier's
# script. The real steps run against stand-ins, as real_hold's do; only a dry run touches nothing at all.

STAND_IN_USER = pwd.getpwuid(os.getuid()).pw_name
STAND_IN_GROUP = grp.getgrgid(os.getgid()).gr_name
NOTIFIER = "/usr/local/libexec/local-ai-notify"

FAKE_REAL_LOGGED = r"""#!/usr/bin/env bash
# Stands in for chown or chmod, whichever name it runs as: logs the call in $CALLS, then runs the real tool, found on
# PATH after the stand-ins' folder, so the files change as they would. Only root can give a file to spark-pull or to
# root, so an owner (USER:GROUP) becomes $STAND_IN_OWNER, whoever runs the tests: the real chown then changes nothing
# but each file's change time, which shows the tests every file it reached, a link's own included.
#
# $CHOWN_RACE stands in for a process of spark's racing the move: after each chown, if that path doesn't exist, it
# makes it a folder writable by its group, as such a process could in a folder the chown hasn't reached yet.
tool="${0##*/}"
echo "$tool $*" >> "$CALLS"
args=()
for arg; do
  if [[ "$tool" == chown && "$arg" == *:* && "$arg" != /* ]]; then arg="$STAND_IN_OWNER"; fi
  args+=("$arg")
done
PATH="${PATH#*:}" "$tool" "${args[@]}"
status=$?
if [[ "$tool" == chown && -n "${CHOWN_RACE:-}" && ! -e "$CHOWN_RACE" ]]; then
  mkdir "$CHOWN_RACE" && PATH="${PATH#*:}" chmod 0775 "$CHOWN_RACE"
fi
exit "$status"
"""

FAKE_PGREP = r"""#!/usr/bin/env bash
# Stands in for `pgrep -l -u USERS`: logs its arguments in $CALLS, then prints $RUNNING ("PID NAME" lines) and exits 0
# when that is set, or exits 1, as pgrep does when nothing matches. With $PGREP_FAILS set, it fails as pgrep does on a
# user it doesn't know: exit 2.
echo "pgrep $*" >> "$CALLS"
if [[ -n "${PGREP_FAILS:-}" ]]; then echo "pgrep: invalid user name: spark-pull" >&2; exit 2; fi
if [[ -z "${RUNNING:-}" ]]; then exit 1; fi
printf '%s\n' "$RUNNING"
"""

FAKE_SYSCTL = r"""#!/usr/bin/env bash
# Stands in for `sysctl -n fs.protected_hardlinks`: prints $PROTECTED_HARDLINKS, or fails as sysctl does when it can't
# read the setting, if that is empty.
[[ "$*" == "-n fs.protected_hardlinks" ]] || { echo "fake sysctl: unexpected: $*" >&2; exit 1; }
[[ -n "${PROTECTED_HARDLINKS:-}" ]] || { echo "sysctl: cannot stat /proc/sys/fs/protected_hardlinks" >&2; exit 255; }
echo "$PROTECTED_HARDLINKS"
"""

# Phase 1's deployed registry names whisper's --tmp-dir inside the Hugging Face cache; 2a's names a folder of its own
# (Task 24). Only the line the check reads, as `spark render`'s registry writes it.
REGISTRY_HF_TMP = ('models:\n  stt:\n'
                   '    args: [--language, auto, --convert, --tmp-dir, /var/lib/local-ai/hf/tmp, --threads, "8"]\n')
REGISTRY_WHISPER_TMP = REGISTRY_HF_TMP.replace("/var/lib/local-ai/hf/tmp", "/var/lib/local-ai/whisper-tmp")
# The units whose processes write as spark or spark-pull: the three running at the 2a re-run (llama-swap, its
# engines and the brake; the pull), and the gate and the brake's drill, which run as spark from the cutover.
SPARKS_UNITS = ["local-ai-llama-swap.service", "local-ai-brake.service", "local-ai-pull.service",
                "local-ai-gate.service", "local-ai-brake-drill.service"]


def cache_env(tmp_path: Path, *, active: str = "", hardlinks: str = "1", pull_user: str = STAND_IN_USER,
              cache_group: str = STAND_IN_GROUP, **settings: str) -> dict[str, str]:
    """Stand-ins for the cache's move: the cache at tmp_path/hf (stand_in_cache makes it), the deployed registry at
    tmp_path/models.yaml, systemctl reporting the units in `active` active, sysctl reporting `hardlinks`, pgrep
    finding no process, runuser, and chown and chmod that log to tmp_path/calls and change only tmp_path's files.
    Whoever runs the tests stands in for spark-pull and spark's group, as for root in install_env, unless `pull_user`
    or `cache_group` says otherwise. `settings` go in the environment as they are: UNIT_STATES for systemctl,
    RUNNING and PGREP_FAILS for pgrep, CHOWN_RACE for chown. The environment is built (built_env)."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    for name, text in (("chown", FAKE_REAL_LOGGED), ("chmod", FAKE_REAL_LOGGED), ("systemctl", FAKE_SYSTEMCTL),
                       ("sysctl", FAKE_SYSCTL), ("runuser", FAKE_RUNUSER), ("pgrep", FAKE_PGREP)):
        (bindir / name).write_text(text)
        (bindir / name).chmod(0o755)
    return built_env(tmp_path, bindir, CALLS=str(tmp_path / "calls"), ACTIVE_UNITS=active,
                     PROTECTED_HARDLINKS=hardlinks, STAND_IN_OWNER=f"{STAND_IN_USER}:{STAND_IN_GROUP}",
                     BOOTSTRAP_HF_CACHE=str(tmp_path / "hf"), BOOTSTRAP_REGISTRY=str(tmp_path / "models.yaml"),
                     BOOTSTRAP_PULL_USER=pull_user, BOOTSTRAP_CACHE_GROUP=cache_group, SUDO_USER="alice", **settings)


def walk(root: Path) -> list[Path]:
    """root and everything under it, a link as itself, never followed."""
    found = [root]
    for top, dirs, files in os.walk(root):
        found += [Path(top, name) for name in dirs + files]
    return found


def folders(root: Path) -> list[Path]:
    """Every folder under root, not root itself and not a link to a folder."""
    return [path for path in walk(root)[1:] if path.is_dir() and not path.is_symlink()]


def ctimes(root: Path) -> dict[Path, int]:
    """Each path's change time, read without following a link: a chown or chmod moves it, even one that changes
    nothing else."""
    return {path: os.lstat(path).st_ctime_ns for path in walk(root)}


def stand_in_cache(tmp_path: Path, *, moved: bool = False) -> Path:
    """A Hugging Face cache as Phase 1's pull left it, spark's (whoever runs the tests stands in): a model's blob and
    the snapshot that links to it, as the hub writes them, hf_xet's logs, whisper's tmp-dir with a file in it, and
    two links out of the cache to a file and a folder outside it, as spark could have planted. `moved`: as the move
    leaves it, the top 2750 and every folder setgid, but hf/tmp, which the 2a re-run left spark's. The outside file
    and folder are returned through tmp_path/outside."""
    hf = tmp_path / "hf"
    snapshot = hf / "hub/models--org--model/snapshots/abc"
    snapshot.mkdir(parents=True)
    blobs = hf / "hub/models--org--model/blobs"
    blobs.mkdir()
    (blobs / "1234").write_text("weights\n")
    (snapshot / "model.gguf").symlink_to("../../blobs/1234")
    (hf / "xet/logs").mkdir(parents=True)
    (hf / "xet/logs/xet.log").write_text("a log line\n")
    (hf / "tmp").mkdir()
    (hf / "tmp/upload.wav").write_text("audio\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    outside.chmod(0o755)
    (outside / "victim").write_text("not the cache's\n")
    (outside / "victim").chmod(0o640)
    (hf / "planted").symlink_to(outside / "victim")
    (hf / "planted-folder").symlink_to(outside)
    for path in walk(hf)[1:]:
        if not path.is_symlink():
            path.chmod(0o755 if path.is_dir() else 0o644)  # as Phase 1's pull left them, under umask 022
    hf.chmod(0o750)
    if moved:
        for folder in folders(hf):
            if hf / "tmp" not in (folder, *folder.parents):
                folder.chmod(0o2755)
        hf.chmod(0o2750)
    time.sleep(0.05)  # so a change time the step sets differs from the one making the stand-in set
    return hf


def mode(path: Path) -> int:
    return os.lstat(path).st_mode & 0o7777


def cache_step(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """The cache's step for real, not its dry run, against cache_env's stand-ins (see real_hold); then the line the
    run goes on with, if the step let it."""
    return subprocess.run(
        ["bash", "-c", 'source "$1" --dry-run && DRY_RUN=0 && hf_cache_to_pull && echo "the run goes on"', "bash",
         str(SCRIPT)],
        capture_output=True, text=True, env=env)


def changing_calls(tmp_path: Path) -> list[str]:
    return [line for line in calls(tmp_path) if line.split()[0] in ("chown", "chmod", "install", "mkdir")]


def test_bootstrap_adds_spark_front_in_spark_users_and_spark_pull():
    lines = dry_run()
    for user in ("spark-front", "spark-pull"):
        assert f"+ groupadd --system {user}   (if missing)" in lines
    assert ("+ useradd --system --gid spark-front --home-dir /nonexistent --no-create-home --shell /usr/sbin/nologin "
            "spark-front   (if missing)") in lines
    assert ("+ useradd --system --gid spark-pull --home-dir /var/lib/local-ai --no-create-home --shell "
            "/usr/sbin/nologin spark-pull   (if missing)") in lines
    assert usermod_lines("spark-front") == ["+ usermod -aG spark-users spark-front"]
    # spark-pull is in its own group only, and the front in neither spark (the cache's group) nor spark-admin.
    assert usermod_lines("spark-pull") == []
    # Each user comes after its group, and both before anything is given to them.
    index = {line: i for i, line in enumerate(lines)}
    assert index["+ groupadd --system spark-pull   (if missing)"] < min(
        i for line, i in index.items() if line.startswith("+ useradd") and line.endswith("spark-pull   (if missing)"))
    assert max(i for line, i in index.items() if line.startswith("+ useradd")) < min(
        i for line, i in index.items() if "spark-pull" in line and ("install -d" in line or "chown" in line))


def test_spark_admin_holds_only_the_admin():
    # The control socket is root's, group spark-admin (Task 25's ruling): only spark-admin's members reach it, and on
    # this box that must be Dan alone.
    adding = [line for line in dry_run()
              if line.startswith("+ usermod") and "spark-admin" in line.split()[3].split(",")]
    me = pwd.getpwuid(os.getuid()).pw_name
    assert adding == [f"+ usermod -aG spark-admin,spark-users,adm {me}"]


FAKE_ACCOUNTS = r"""#!/usr/bin/env bash
# Stands in for getent, id, groupadd, useradd, usermod and chmod, whichever name it runs as. The first five work over
# $ACCOUNTS/passwd (name:group:home:shell) and $ACCOUNTS/group (name:members) as the real ones work over /etc/passwd
# and /etc/group: groupadd and useradd refuse a name that exists (exit 9), useradd a --gid that doesn't (6), usermod
# a user or a group that doesn't (6), and useradd without --gid makes the user's own group, as Ubuntu's does. Each
# call that changes something is logged in $CALLS; chmod is only logged, since the paths it names are this machine's.
tool="${0##*/}"
passwd="$ACCOUNTS/passwd" group="$ACCOUNTS/group"
has() { grep -q "^$2:" "$1"; }
case "$tool" in
  getent)
    [[ "$1" == group && $# == 2 ]] || { echo "fake getent: unexpected: $*" >&2; exit 1; }
    grep "^$2:" "$group" || exit 2 ;;
  id)
    [[ "$1" == -u && $# == 2 ]] || { echo "fake id: unexpected: $*" >&2; exit 1; }
    grep -n "^$2:" "$passwd" | cut -d: -f1 | grep . || { echo "id: '$2': no such user" >&2; exit 1; } ;;
  groupadd)
    echo "groupadd $*" >> "$CALLS"
    for name; do :; done
    if has "$group" "$name"; then echo "groupadd: group '$name' already exists" >&2; exit 9; fi
    echo "$name:" >> "$group" ;;
  useradd)
    echo "useradd $*" >> "$CALLS"
    gid="" home="/home/${*: -1}" shell=""
    while [[ $# -gt 1 ]]; do
      case "$1" in
        --gid) gid="$2"; shift 2 ;;
        --home-dir) home="$2"; shift 2 ;;
        --shell) shell="$2"; shift 2 ;;
        *) shift ;;
      esac
    done
    name="$1"
    if has "$passwd" "$name"; then echo "useradd: user '$name' already exists" >&2; exit 9; fi
    if [[ -n "$gid" ]] && ! has "$group" "$gid"; then echo "useradd: group '$gid' does not exist" >&2; exit 6; fi
    if [[ -z "$gid" ]]; then gid="$name"; has "$group" "$name" || echo "$name:" >> "$group"; fi
    echo "$name:$gid:$home:$shell" >> "$passwd" ;;
  usermod)
    echo "usermod $*" >> "$CALLS"
    [[ "$1" == -aG && $# == 3 ]] || { echo "fake usermod: unexpected: $*" >&2; exit 1; }
    has "$passwd" "$3" || { echo "usermod: user '$3' does not exist" >&2; exit 6; }
    IFS=, read -ra wanted <<<"$2"
    for g in "${wanted[@]}"; do has "$group" "$g" || { echo "usermod: group '$g' does not exist" >&2; exit 6; }; done
    for g in "${wanted[@]}"; do
      awk -F: -v g="$g" -v u="$3" 'BEGIN {OFS = ":"}
        $1 == g {n = split($2, m, ","); seen = 0; for (i = 1; i <= n; i++) if (m[i] == u) seen = 1
                 if (!seen) $2 = ($2 == "" ? u : $2 "," u)}
        {print}' "$group" > "$group.new"
      mv "$group.new" "$group"
    done ;;
  chmod) echo "chmod $*" >> "$CALLS" ;;
  *) echo "fake accounts: unexpected tool $tool" >&2; exit 1 ;;
esac
"""

# The accounts as Phase 0's bootstrap left brightroar (alice standing in for the admin), and as a new box has them.
BOX_PASSWD = ["alice:alice:/home/alice:/bin/bash", "spark:spark:/var/lib/local-ai:/usr/sbin/nologin",
              "agent:agent:/home/agent:/bin/bash"]
BOX_GROUP = ["alice:", "adm:alice", "video:spark,agent", "render:spark,agent", "docker:", "spark:", "agent:",
             "spark-users:spark,agent,alice", "spark-admin:alice"]
NEW_PASSWD = ["alice:alice:/home/alice:/bin/bash"]
NEW_GROUP = ["alice:", "adm:", "video:", "render:", "docker:"]


def accounts_env(tmp_path: Path, passwd: list[str], group: list[str]) -> dict[str, str]:
    """Stand-ins for the accounts' tools over tmp_path/accounts, which they change as the real ones change /etc;
    alice is the admin who ran sudo. The environment is built (built_env)."""
    bindir, accounts = tmp_path / "bin", tmp_path / "accounts"
    bindir.mkdir()
    accounts.mkdir()
    (accounts / "passwd").write_text("".join(f"{line}\n" for line in passwd))
    (accounts / "group").write_text("".join(f"{line}\n" for line in group))
    for name in ("getent", "id", "groupadd", "useradd", "usermod", "chmod"):
        (bindir / name).write_text(FAKE_ACCOUNTS)
        (bindir / name).chmod(0o755)
    return built_env(tmp_path, bindir, ACCOUNTS=str(accounts), CALLS=str(tmp_path / "calls"), SUDO_USER="alice")


def users_step(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """users_and_groups for real, not its dry run, against accounts_env's stand-ins (see real_hold)."""
    return subprocess.run(["bash", "-c", 'source "$1" --dry-run && DRY_RUN=0 && users_and_groups', "bash", str(SCRIPT)],
                          capture_output=True, text=True, env=env)


def accounts(tmp_path: Path) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """The stand-ins' users (name → group, home, shell) and groups (name → members)."""
    users = {line.split(":")[0]: line.split(":")[1:] for line in (tmp_path / "accounts/passwd").read_text().split()}
    groups = {name: [m for m in members.split(",") if m]
              for name, members in (line.split(":") for line in (tmp_path / "accounts/group").read_text().split())}
    return users, groups


@pytest.mark.parametrize("passwd, group", [(BOX_PASSWD, BOX_GROUP), (NEW_PASSWD, NEW_GROUP)], ids=["brightroar", "new"])
def test_the_users_and_groups_step_makes_each_account_once(tmp_path, passwd, group):
    env = accounts_env(tmp_path, passwd, group)
    first = users_step(env)
    assert first.returncode == 0, first.stderr
    users, groups = accounts(tmp_path)
    assert users["spark-front"] == ["spark-front", "/nonexistent", "/usr/sbin/nologin"]
    assert users["spark-pull"] == ["spark-pull", "/var/lib/local-ai", "/usr/sbin/nologin"]
    assert users["spark"] == ["spark", "/var/lib/local-ai", "/usr/sbin/nologin"]
    assert "spark-front" in groups["spark-users"] and groups["spark-admin"] == ["alice"]
    assert all(name == "spark-front" or "spark-front" not in members for name, members in groups.items()
               if name != "spark-users")  # its own group and spark-users, never spark's, the cache's group
    assert all("spark-pull" not in members for members in groups.values())  # its own group alone
    if passwd == BOX_PASSWD:  # only what 2a adds
        assert [line for line in calls(tmp_path) if line.startswith(("groupadd", "useradd"))] == [
            "groupadd --system spark-front", "groupadd --system spark-pull",
            "useradd --system --gid spark-front --home-dir /nonexistent --no-create-home --shell /usr/sbin/nologin "
            "spark-front",
            "useradd --system --gid spark-pull --home-dir /var/lib/local-ai --no-create-home --shell "
            "/usr/sbin/nologin spark-pull"]
    before = (tmp_path / "accounts/passwd").read_text(), (tmp_path / "accounts/group").read_text()
    (tmp_path / "calls").unlink()
    again = users_step(env)
    assert again.returncode == 0, again.stderr
    assert not [line for line in calls(tmp_path) if line.startswith(("groupadd", "useradd"))]
    assert ((tmp_path / "accounts/passwd").read_text(), (tmp_path / "accounts/group").read_text()) == before


FAKE_INSTALL_FOLDERS = FAKE_INSTALL  # install -d makes each folder with its mode, as install_env's does


def secrets_env(tmp_path: Path) -> dict[str, str]:
    """Stand-ins for the secrets folder's step: the folder at tmp_path/secrets, install as install_env's, and chown
    and chmod that log to tmp_path/calls and change only tmp_path's files."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, text in (("install", FAKE_INSTALL_FOLDERS), ("chown", FAKE_REAL_LOGGED), ("chmod", FAKE_REAL_LOGGED)):
        (bindir / name).write_text(text)
        (bindir / name).chmod(0o755)
    return built_env(tmp_path, bindir, CALLS=str(tmp_path / "calls"), BOOTSTRAP_SECRETS=str(tmp_path / "secrets"),
                     STAND_IN_OWNER=f"{STAND_IN_USER}:{STAND_IN_GROUP}")


def test_the_secrets_folder_becomes_root_only_and_its_files_0600():
    lines = dry_run()
    assert "+ install -d -o root -g root -m 0700 /etc/local-ai/secrets" in lines
    # A dry run never lists the folder: it says what a real run does to each file in it.
    assert "+ chown root:root /etc/local-ai/secrets/<each file>" in lines
    assert "+ chmod 0600 /etc/local-ai/secrets/<each file>" in lines
    assert lines.index("+ install -d -o root -g root -m 0700 /etc/local-ai/secrets") < lines.index(
        "+ chown root:root /etc/local-ai/secrets/<each file>")


def test_each_secret_file_becomes_root_root_0600_in_a_real_run(tmp_path):
    env = secrets_env(tmp_path)
    secrets = tmp_path / "secrets"
    secrets.mkdir(mode=0o750)
    secrets.chmod(0o750)  # root:spark 750, as Phase 1 left it
    for name in ("llama-swap.env", "hf.env"):
        (secrets / name).write_text("NAME=stand-in\n")
        (secrets / name).chmod(0o640)
    outside = tmp_path / "outside"
    outside.write_text("not a secret file\n")
    outside.chmod(0o644)
    (secrets / "a-link").symlink_to(outside)
    before = os.lstat(outside).st_ctime_ns
    time.sleep(0.05)
    result = subprocess.run(["bash", "-c", 'source "$1" --dry-run && DRY_RUN=0 && secrets_folder', "bash", str(SCRIPT)],
                            capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
    # install's stand-in sets the folder's mode with chmod, the logged one, right after the script's call.
    assert calls(tmp_path)[:2] == [f"install -d -o root -g root -m 0700 {secrets}", f"chmod 0700 {secrets}"]
    assert sorted(calls(tmp_path)[2:]) == sorted(
        f"{tool} {setting} {secrets / name}" for name in ("hf.env", "llama-swap.env")
        for tool, setting in (("chown", "root:root"), ("chmod", "0600")))
    for name in ("hf.env", "llama-swap.env"):
        assert calls(tmp_path).index(f"chown root:root {secrets / name}") < calls(tmp_path).index(
            f"chmod 0600 {secrets / name}")
        assert mode(secrets / name) == 0o600
    assert mode(secrets) == 0o700
    # Root acts on regular files only: a link in the folder, which no step makes, is left and named.
    assert mode(outside) == 0o644 and os.lstat(outside).st_ctime_ns == before
    assert f"left as it is: {secrets}/a-link, not a regular file" in result.stdout


def test_the_gate_launch_and_whisper_tmp_folders_are_sparks():
    for path in ("/var/lib/local-ai/gate", "/var/lib/local-ai/launch", "/var/lib/local-ai/whisper-tmp"):
        line = install_d_line(path)
        assert line.startswith("+ install -d -o spark -g spark -m 0750 "), line  # root's, in root's /var/lib/local-ai
    for path in ("tickets", "started", "refusals"):
        line = install_d_line(f"/var/lib/local-ai/launch/{path}")
        # launch is spark's, so spark makes these: root never makes a folder through one spark controls, where a
        # link spark planted would turn root's install -d into a chown of whatever it points at.
        assert line.startswith("+ runuser -u spark -- install -d -o spark -g spark -m 0750 "), line
    assert install_d_line("/var/lib/local-ai/pull-cache").startswith(
        "+ install -d -o spark-pull -g spark-pull -m 0750 ")
    lines = dry_run()
    assert lines.index(install_d_line("/var/lib/local-ai/launch")) < lines.index(
        install_d_line("/var/lib/local-ai/launch/tickets"))


def test_directories_never_name_the_cache():
    # install -d sets the owner and mode of a folder that exists, so a line naming the cache would hand it back to
    # whoever the line names, and drop its setgid bit, at every re-run.
    for line in dry_run({**NO_PACKAGES, "BOOTSTRAP_HF_CACHE": "/var/lib/local-ai/hf"}):
        if "install -d" in line:
            assert not [word for word in line.split() if word.rstrip("/") == "/var/lib/local-ai/hf"], line


def test_the_folders_installs_need_are_made_first():
    lines = dry_run()
    folder = lines.index("+ install -d -o root -g root -m 0755 /usr/local/libexec")
    [notifier] = [i for i, line in enumerate(lines) if line.startswith("+ install ") and line.endswith(NOTIFIER)]
    assert folder < notifier


def test_the_notifier_script_is_installed_root_owned():
    [line] = [line for line in dry_run() if line.endswith(f" {NOTIFIER}")]
    words = line.split()
    assert words[:7] == ["+", "install", "-o", "root", "-g", "root", "-m"] and words[7] == "0755", line
    assert words[8].endswith("/stack/host/local-ai-notify") and Path(words[8]).resolve() == (
        ROOT / "stack/host/local-ai-notify")
    # Where the failure notifier's unit runs it from (Task 25).
    unit = (ROOT / "stack/templates/local-ai-notify@.service").read_text()
    assert f"ExecStart={NOTIFIER} %i" in unit.splitlines()


def test_the_dry_run_prints_every_new_step():
    says = [line for line in dry_run() if line.startswith("==> ")]
    order = ["==> users and groups", "==> directories",
             "==> the Hugging Face cache: spark-pull's, with a setgid group that spark reads the model files through",
             "==> the failure notifier's script, which local-ai-notify@.service runs: root's own",
             "==> boot to a console; start a desktop on demand with: sudo systemctl start display-manager"]
    assert [line for line in says if line in order] == order


def test_the_cache_moves_to_spark_pull_with_a_setgid_group(tmp_path):
    env = cache_env(tmp_path)
    hf = stand_in_cache(tmp_path)
    (tmp_path / "models.yaml").write_text(REGISTRY_WHISPER_TMP)  # nothing names hf/tmp: it moves with the rest
    before, outside = ctimes(hf), ctimes(tmp_path / "outside")
    result = cache_step(env)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[-2:] == [f"==> moved: {hf} is spark-pull:spark 2750, its folders setgid",
                                               "the run goes on"]
    chowns = [line for line in calls(tmp_path) if line.startswith("chown")]
    assert chowns == [f"chown -R -P --no-dereference spark-pull:spark {hf}"]
    chmods = [line for line in calls(tmp_path) if line.startswith("chmod")]
    assert chmods[0] == f"chmod 2750 {hf}"
    assert all(line.startswith("chmod g+s ") for line in chmods[1:])
    assert sorted(word for line in chmods[1:] for word in line.split()[2:]) == sorted(map(str, folders(hf)))
    assert mode(hf) == 0o2750 and all(mode(folder) & 0o2000 for folder in folders(hf))
    # Every path in the cache was reached, a link's own owner included; nothing outside it was, through a link.
    after = ctimes(hf)
    assert [path for path in before if after[path] == before[path]] == []
    assert ctimes(tmp_path / "outside") == outside
    assert mode(tmp_path / "outside") == 0o755 and mode(tmp_path / "outside/victim") == 0o640


@pytest.mark.parametrize("unit", SPARKS_UNITS)
def test_the_cache_move_refuses_while_sparks_units_run(tmp_path, unit):
    env = cache_env(tmp_path, active=unit)
    hf = stand_in_cache(tmp_path)
    (tmp_path / "models.yaml").write_text(REGISTRY_HF_TMP)
    before, modes = ctimes(hf), {path: mode(path) for path in walk(hf)}
    result = cache_step(env)
    assert result.returncode == 1 and "the run goes on" not in result.stdout
    assert "the Hugging Face cache isn't spark-pull's yet, and it can't move now, so nothing in it was changed" in (
        result.stderr)
    assert f"  {unit} is active: stop it first (systemctl stop {unit})" in result.stderr.splitlines()
    assert changing_calls(tmp_path) == [] and ctimes(hf) == before and {p: mode(p) for p in walk(hf)} == modes


def test_every_reason_the_move_cant_go_ahead_is_named(tmp_path):
    env = cache_env(tmp_path, active=" ".join(SPARKS_UNITS[:2]), hardlinks="0", RUNNING="4242 llama-swap")
    stand_in_cache(tmp_path)
    result = cache_step(env)
    assert result.returncode == 1
    lines = result.stderr.splitlines()
    for unit in SPARKS_UNITS[:2]:
        assert f"  {unit} is active: stop it first (systemctl stop {unit})" in lines
    assert any(line.startswith("  processes run as spark or spark-pull (4242 llama-swap)") for line in lines)
    assert any(line.startswith("  fs.protected_hardlinks is 0, not 1") for line in lines)
    assert lines[-1] == "then run make bootstrap again: website/how-to/bootstrap.md, Phase 2a's re-run"


def test_a_re_run_after_the_move_changes_nothing(tmp_path):
    # Task 39's or Task 47's re-run, after the cutover: the stack up, and hf/tmp still spark's, as the 2a re-run left
    # it, though the deployed registry no longer names it (the close removes it, Task 51).
    env = cache_env(tmp_path, active=" ".join(SPARKS_UNITS), hardlinks="0")
    hf = stand_in_cache(tmp_path, moved=True)
    (tmp_path / "models.yaml").write_text(REGISTRY_WHISPER_TMP)
    before = ctimes(hf)
    result = cache_step(env)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[-2:] == [
        f"==> {hf} is already spark-pull:spark 2750, its folders setgid: nothing to move", "the run goes on"]
    assert changing_calls(tmp_path) == [] and ctimes(hf) == before


@pytest.mark.parametrize("odd", ["owner", "setgid", "top", "writable"])
def test_anything_not_yet_moved_counts_as_something_to_move(tmp_path, odd):
    # A move cut off partway, a folder that lost its setgid bit, or a file spark's group could write (Task 27's review,
    # M2): the step moves again, and refuses while the stack runs, rather than calling it done.
    env = cache_env(tmp_path, active="local-ai-brake.service", pull_user="nobody" if odd == "owner" else STAND_IN_USER)
    hf = stand_in_cache(tmp_path, moved=True)
    if odd == "setgid":
        (hf / "xet/logs").chmod(0o755)
    elif odd == "top":
        hf.chmod(0o750)
    elif odd == "writable":
        (hf / "xet/logs/xet.log").chmod(0o664)
    result = cache_step(env)
    assert result.returncode == 1 and "  local-ai-brake.service is active" in result.stderr


@pytest.mark.parametrize("hardlinks", ["0", "2", ""], ids=["off", "not-1", "unreadable"])
def test_the_cache_move_refuses_without_protected_hardlinks(tmp_path, hardlinks):
    env = cache_env(tmp_path, hardlinks=hardlinks)
    hf = stand_in_cache(tmp_path)
    before = ctimes(hf)
    result = cache_step(env)
    assert result.returncode == 1
    assert f"  fs.protected_hardlinks is {hardlinks or 'unreadable'}, not 1: with it off, a hard link spark made to " \
           "a file it doesn't own would go to spark-pull with the cache" in result.stderr.splitlines()
    assert changing_calls(tmp_path) == [] and ctimes(hf) == before


def test_hf_tmp_stays_sparks_while_the_registry_names_it(tmp_path):
    for case, registry in (("named", REGISTRY_HF_TMP), ("not-named", REGISTRY_WHISPER_TMP), ("unread", None)):
        root = tmp_path / case
        root.mkdir()
        env = cache_env(root)
        hf = stand_in_cache(root)
        if registry is not None:
            (root / "models.yaml").write_text(registry)
        if case != "not-named":
            # Kept, hf/tmp is spark's, and writable by spark's group is no matter: no check and no chmod enters it.
            (hf / "tmp").chmod(0o775)
            (hf / "tmp/upload.wav").chmod(0o664)
        before = ctimes(hf)
        result = cache_step(env)
        assert result.returncode == 0, (case, result.stderr)
        # The registry is read as the admin who ran sudo, never as root (install_units' rule).
        assert f"runuser -u alice -- timeout 10 head -c 1048577 -- {root}/models.yaml" in calls(root), case
        chowns = [line for line in calls(root) if line.startswith("chown")]
        after = ctimes(hf)
        if case == "not-named":
            assert chowns == [f"chown -R -P --no-dereference spark-pull:spark {hf}"]
            assert mode(hf / "tmp") & 0o2000 and after[hf / "tmp/upload.wav"] != before[hf / "tmp/upload.wav"]
            continue
        rest = sorted(str(path) for path in hf.iterdir() if path.name != "tmp")
        assert chowns[0] == f"chown --no-dereference spark-pull:spark {hf}", case
        assert sorted(word for line in chowns[1:] for word in line.removeprefix(
            "chown -R -P --no-dereference spark-pull:spark ").split()) == rest, case
        assert mode(hf / "tmp") == 0o775 and mode(hf / "tmp/upload.wav") == 0o664, case
        assert after[hf / "tmp"] == before[hf / "tmp"] and after[hf / "tmp/upload.wav"] == before[hf / "tmp/upload.wav"]
        assert all(after[path] != before[path] for path in before if hf / "tmp" not in (path, *path.parents)), case
        assert mode(hf) == 0o2750 and all(mode(f) & 0o2000 for f in folders(hf) if f != hf / "tmp"), case
        why = ("the deployed registry still names it as whisper's --tmp-dir" if case == "named" else
               f"the deployed registry, {root}/models.yaml, couldn't be read as alice, so it may still be whisper's")
        assert f"==> {hf}/tmp stays as it is, spark's: {why}" in result.stdout.splitlines(), case


def test_a_new_box_gets_an_empty_cache_that_spark_pull_owns(tmp_path):
    # Nothing to move, so nothing refuses, whatever runs.
    env = cache_env(tmp_path, active=" ".join(SPARKS_UNITS), hardlinks="0")
    result = cache_step(env)
    assert result.returncode == 0, result.stderr
    hf = tmp_path / "hf"
    assert calls(tmp_path) == [f"chown spark-pull:spark {hf}", f"chmod 2750 {hf}"]
    assert hf.is_dir() and mode(hf) == 0o2750
    assert f"==> made {hf}, empty: spark-pull:spark 2750" in result.stdout


@pytest.mark.parametrize("planted", ["link", "file"])
def test_a_cache_that_isnt_a_folder_is_refused(tmp_path, planted):
    env = cache_env(tmp_path)
    (tmp_path / "elsewhere").mkdir()
    if planted == "link":
        (tmp_path / "hf").symlink_to(tmp_path / "elsewhere")
    else:
        (tmp_path / "hf").write_text("")
    result = cache_step(env)
    assert result.returncode == 1 and f"{tmp_path}/hf is a link or not a folder" in result.stderr
    assert changing_calls(tmp_path) == []


def test_a_chown_that_didnt_take_stops_the_move_before_any_chmod(tmp_path):
    # Task 27's review (I1): the stand-in chown gives the files to whoever runs the tests, not to the user the check
    # expects, as a chown that silently failed, or a folder spark made where the walk had already been, would leave
    # them. The move stops before any chmod: chmod follows a link it is handed, and a folder that isn't spark-pull's
    # could still be swapped for one.
    env = cache_env(tmp_path, pull_user="nobody")
    hf = stand_in_cache(tmp_path)
    (tmp_path / "models.yaml").write_text(REGISTRY_WHISPER_TMP)
    modes = {path: mode(path) for path in walk(hf)}
    result = cache_step(env)
    assert result.returncode == 1 and "the run goes on" not in result.stdout
    assert [line for line in calls(tmp_path) if line.startswith("chmod")] == []
    assert {path: mode(path) for path in walk(hf)} == modes
    assert result.stderr.startswith(f"bootstrap: after the chown, {hf} isn't spark-pull:spark, or is writable by its "
                                    "group or others, so no chmod ran and nothing more was changed.")


def test_a_dry_run_shows_the_move_and_where_a_real_run_would_stop(tmp_path):
    env = cache_env(tmp_path, active="local-ai-llama-swap.service")
    hf = stand_in_cache(tmp_path)
    (tmp_path / "models.yaml").write_text(REGISTRY_HF_TMP)
    before = ctimes(hf)
    result = subprocess.run(
        ["bash", "-c", 'source "$1" --dry-run && DRY_RUN=1 && hf_cache_to_pull', "bash", str(SCRIPT)],
        capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
    assert (f"+ chown -R -P --no-dereference spark-pull:spark {hf}   (a real run stops here: "
            "local-ai-llama-swap.service is active)") in result.stdout.splitlines()
    assert changing_calls(tmp_path) == [] and ctimes(hf) == before
    # With nothing in its way, it prints the move, leaving hf/tmp out while the registry names it.
    (tmp_path / "calls").unlink(missing_ok=True)
    result = subprocess.run(
        ["bash", "-c", 'source "$1" --dry-run && DRY_RUN=1 && hf_cache_to_pull', "bash", str(SCRIPT)],
        capture_output=True, text=True, env={**env, "ACTIVE_UNITS": ""})
    assert [line for line in result.stdout.splitlines() if line.startswith("+ ")] == [
        f"+ chown --no-dereference spark-pull:spark {hf}",
        f"+ find -P {hf} -mindepth 1 -maxdepth 1 ! -path {hf}/tmp -exec chown -R -P --no-dereference spark-pull:spark "
        "{} +",
        "+ owned_and_closed   (a real run stops here, before any chmod, unless everything it moved is "
        "spark-pull:spark and writable by neither its group nor others)",
        f"+ chmod 2750 {hf}",
        f"+ find -P {hf} -mindepth 1 -path {hf}/tmp -prune -o -type d ! -type l -exec chmod g+s {{}} +",
    ]
    assert changing_calls(tmp_path) == [] and ctimes(hf) == before


def test_the_cache_step_comes_after_the_users_and_folders_it_uses():
    says = [line for line in dry_run() if line.startswith("==> ")]
    cache = next(i for i, line in enumerate(says) if line.startswith("==> the Hugging Face cache"))
    assert says.index("==> users and groups") < says.index("==> directories") < cache


# Task 27's review (I1, M1, M2): the guard sees every process of spark's and spark-pull's, takes a unit as stopped only
# when systemd says so, refuses a tree the move would leave spark able to change, and checks again, after the chown
# and before any chmod, that nothing of spark's can swap a path chmod is handed.


def test_any_process_of_sparks_or_spark_pulls_refuses_the_move(tmp_path):
    # A crontab job or a shell of spark's, outside every unit, could race the move.
    env = cache_env(tmp_path, RUNNING="4242 bash\n4243 sleep")
    hf = stand_in_cache(tmp_path)
    before = ctimes(hf)
    result = cache_step(env)
    assert result.returncode == 1 and "the run goes on" not in result.stdout
    assert ("  processes run as spark or spark-pull (4242 bash, 4243 sleep): stop each first, a unit with systemctl "
            "stop, anything else where it was started") in result.stderr.splitlines()
    assert "pgrep -l -u spark,spark-pull" in calls(tmp_path)  # pid and command name, never the arguments
    assert changing_calls(tmp_path) == [] and ctimes(hf) == before


def test_a_pgrep_that_cant_tell_refuses_the_move(tmp_path):
    env = cache_env(tmp_path, PGREP_FAILS="1")
    hf = stand_in_cache(tmp_path)
    before = ctimes(hf)
    result = cache_step(env)
    assert result.returncode == 1
    assert ("  pgrep couldn't tell whether anything runs as spark or spark-pull (exit 2): find out why, then run this "
            "again") in result.stderr.splitlines()
    assert changing_calls(tmp_path) == [] and ctimes(hf) == before


@pytest.mark.parametrize("state, refused", [
    ("deactivating", True), ("activating", True), ("reloading", True), ("unanswerable", True),
    ("failed", False), ("inactive", False),
])
def test_a_unit_counts_as_stopped_only_when_inactive_or_failed(tmp_path, state, refused):
    # systemctl is-active --quiet succeeds only for active and reloading, so a unit auto-restarting (activating) or
    # stopping (deactivating), or a systemctl that can't answer, looked stopped before the review.
    env = cache_env(tmp_path, UNIT_STATES=f"local-ai-brake.service={state}")
    hf = stand_in_cache(tmp_path)
    (tmp_path / "models.yaml").write_text(REGISTRY_WHISPER_TMP)
    before = ctimes(hf)
    result = cache_step(env)
    if not refused:
        assert result.returncode == 0, result.stderr
        return
    assert result.returncode == 1
    reason = ("  local-ai-brake.service didn't say whether it runs (systemctl is-active answered nothing): find out "
              "why, then run this again" if state == "unanswerable" else
              f"  local-ai-brake.service is {state}, not inactive or failed: stop it, and wait until it is (systemctl "
              "stop local-ai-brake.service)")
    assert reason in result.stderr.splitlines()
    assert changing_calls(tmp_path) == [] and ctimes(hf) == before


@pytest.mark.parametrize("path, bits", [("xet/logs", 0o775), ("xet/logs", 0o757), ("xet/logs/xet.log", 0o664),
                                        ("hub/models--org--model/blobs/1234", 0o646)],
                         ids=["folder-g+w", "folder-o+w", "file-g+w", "file-o+w"])
def test_anything_writable_by_its_group_or_others_is_refused_before_the_chown(tmp_path, path, bits):
    # The move keeps every mode, and leaves spark's group on everything: a path writable by its group would stay
    # writable by spark, and a folder spark can write is one whose entries it can swap for a link under chmod.
    # Refused, never stripped: coreutils 9.4's chmod -R can follow a link swapped in during its walk.
    env = cache_env(tmp_path)
    hf = stand_in_cache(tmp_path)
    (hf / path).chmod(bits)
    before = ctimes(hf)
    result = cache_step(env)
    assert result.returncode == 1 and "the run goes on" not in result.stdout
    assert (f"  paths in it writable by their group or others, which spark could still change after the move "
            f"({hf}/{path}): remove those bits as spark, their owner (sudo -u spark chmod go-w <path>)"
            ) in result.stderr.splitlines()
    assert changing_calls(tmp_path) == [] and ctimes(hf) == before


def test_a_file_with_a_second_name_is_refused(tmp_path):
    # With fs.protected_hardlinks on, spark can still link a file it owns, or can read and write, into the cache: the
    # move would hand that file to spark-pull wherever its other name is.
    env = cache_env(tmp_path)
    hf = stand_in_cache(tmp_path)
    (tmp_path / "sparks-own").write_text("state\n")
    (tmp_path / "sparks-own").chmod(0o644)
    os.link(tmp_path / "sparks-own", hf / "xet/logs/linked")
    before = ctimes(hf)
    result = cache_step(env)
    assert result.returncode == 1
    assert (f"  files in it with a second name, which the move would hand to spark-pull wherever the other name is "
            f"({hf}/xet/logs/linked): look at each with sudo ls -li, and remove the name that isn't the cache's"
            ) in result.stderr.splitlines()
    assert changing_calls(tmp_path) == [] and ctimes(hf) == before


def test_a_refusal_names_five_paths_at_most_and_nothing_a_terminal_acts_on(tmp_path):
    env = cache_env(tmp_path)
    hf = stand_in_cache(tmp_path)
    for n in range(7):
        (hf / f"open-{n}").write_text("")
        (hf / f"open-{n}").chmod(0o666)
    (hf / "open-0").rename(hf / ("open-" + chr(27) + "[2K"))
    result = cache_step(env)
    [line] = [line for line in result.stderr.splitlines() if line.startswith("  paths in it writable")]
    assert line.count(f"{hf}/open-") == 5 and ", and 2 more)" in line
    assert chr(27) not in result.stderr


def test_a_folder_made_writable_during_the_chown_stops_the_move_before_any_chmod(tmp_path):
    # Whatever runs: the first look found nothing, and a process started after it (a crontab job) makes a folder its
    # group can write while the chown walks. The second check, after the chown, stops the move before any chmod.
    for case, registry in (("keeps-hf-tmp", REGISTRY_HF_TMP), ("moves-hf-tmp", REGISTRY_WHISPER_TMP)):
        root = tmp_path / case
        root.mkdir()
        env = cache_env(root, CHOWN_RACE=str(root / "hf/hub/raced"))
        stand_in_cache(root)
        (root / "models.yaml").write_text(registry)
        result = cache_step(env)
        assert result.returncode == 1 and "the run goes on" not in result.stdout, case
        assert [line for line in calls(root) if line.startswith("chmod")] == [], case
        assert result.stderr.startswith(f"bootstrap: after the chown, {root}/hf/hub/raced isn't spark-pull:spark, or "
                                        "is writable by its group or others, so no chmod ran"), case


def test_no_chmod_is_handed_a_link(tmp_path):
    # chmod follows a link named on its command line: every path the setgid pass hands it is a folder.
    env = cache_env(tmp_path)
    hf = stand_in_cache(tmp_path)
    (tmp_path / "models.yaml").write_text(REGISTRY_WHISPER_TMP)
    result = cache_step(env)
    assert result.returncode == 0, result.stderr
    handed = [word for line in calls(tmp_path) if line.startswith("chmod g+s ") for word in line.split()[2:]]
    assert handed and all(Path(word).is_dir() and not Path(word).is_symlink() for word in handed)
    assert str(hf / "planted-folder") not in handed

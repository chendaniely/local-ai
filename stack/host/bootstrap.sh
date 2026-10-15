#!/usr/bin/env bash
# Host setup for brightroar (DGX OS, Ubuntu 24.04, aarch64).
#   Preview (changes nothing):  bash stack/host/bootstrap.sh --dry-run
#   Apply (Dan, once):          sudo bash stack/host/bootstrap.sh
#   Re-hold the GPU set only:   sudo bash stack/host/bootstrap.sh --hold-gpu   (upgrade day; add
#                               --dry-run to preview it)
#   Install root's copies:      make install-units   (of the units and the Compose project that
#                               make apply staged; it runs this script with --install-units, and
#                               make install-units-dry-run previews it)
#   Move the GPU set:           make upgrade-gpu   (upgrade day, in tmux; it runs this script with
#                               --upgrade-gpu, and make upgrade-gpu-dry-run previews it)
# Safe to re-run: every step checks before it changes anything.
set -euo pipefail

DRY_RUN=0
HOLD_ONLY=0
INSTALL_UNITS=0
UPGRADE=0
REHELD=1     # upgrade day: 0 from the moment the GPU set is released until it is held again
STOPPED=""   # upgrade day: the stack's units it stopped
MOVING=0     # upgrade day: 1 from the moment apt starts moving the set
MOVE_FROM="" # upgrade day: the set's contents just before that (gpu_set_contents)
GRUB_FAILED=0 # upgrade day: 1 when GRUB may not boot the newest kernel after the move

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Under sudo (make bootstrap) the admin is SUDO_USER; a dry run has no sudo, so it is whoever runs it.
ADMIN_USER="${SUDO_USER:-$(id -un)}"

say() { printf '==> %s\n' "$*"; }
run() {
  if (( DRY_RUN )); then printf '+ %s\n' "$*"; else "$@"; fi
}

parse_args() {
  local arg
  for arg in "$@"; do
    case "$arg" in
      --dry-run) DRY_RUN=1 ;;
      --hold-gpu) HOLD_ONLY=1 ;;
      --install-units) INSTALL_UNITS=1 ;;
      --upgrade-gpu) UPGRADE=1 ;;
      # A mistyped --dry-run under sudo must not turn into a real run.
      *) echo "bootstrap: unknown option '$arg' (the options are --dry-run, --hold-gpu, --install-units and --upgrade-gpu)" >&2; exit 2 ;;
    esac
  done
  if (( HOLD_ONLY + INSTALL_UNITS + UPGRADE > 1 )); then
    echo "bootstrap: --hold-gpu, --install-units and --upgrade-gpu are separate modes; pick one" >&2
    exit 2
  fi
}

preflight() {
  (( DRY_RUN )) && return 0
  [[ "$(id -u)" -eq 0 ]] || { echo "bootstrap: run it with sudo" >&2; exit 1; }
  [[ "$(uname -m)" == "aarch64" ]] || { echo "bootstrap: expected aarch64" >&2; exit 1; }
  grep -q '^ID=ubuntu' /etc/os-release || { echo "bootstrap: expected Ubuntu-based DGX OS" >&2; exit 1; }
  getent group docker >/dev/null || { echo "bootstrap: no docker group — is Docker installed?" >&2; exit 1; }
}

ensure_group() {
  if (( DRY_RUN )); then printf '+ groupadd --system %s   (if missing)\n' "$1"; return; fi
  getent group "$1" >/dev/null || groupadd --system "$1"
}

# A system user that can't log in, in its own group of the same name, made before it: ensure_system_user NAME HOME.
ensure_system_user() {
  local -a cmd=(useradd --system --gid "$1" --home-dir "$2" --no-create-home --shell /usr/sbin/nologin "$1")
  if (( DRY_RUN )); then printf '+ %s   (if missing)\n' "${cmd[*]}"; return; fi
  id -u "$1" >/dev/null 2>&1 || "${cmd[@]}"
}

ensure_agent_user() {
  if (( DRY_RUN )); then printf '+ useradd --create-home --shell /bin/bash agent   (if missing)\n'; return; fi
  id -u agent >/dev/null 2>&1 || useradd --create-home --shell /bin/bash agent
}

packages() {
  say "packages"
  run apt-get update
  # Everything the stack, the repo and Dan's own work use is listed here, even what DGX OS happens
  # to ship today (git, curl, openssl), so a rebuild never depends on the image.
  #   stack: earlyoom ufw tmux cmake build-essential ffmpeg jq
  #   repo and runbooks: git curl openssl shellcheck gh
  #   Dan's own work: python3-dev r-base r-base-dev
  run apt-get install -y earlyoom ufw tmux cmake build-essential ffmpeg jq \
    git curl openssl shellcheck gh \
    python3-dev r-base r-base-dev
}

# The GPU stack only works as a matched set: the kernel, the NVIDIA modules built for it (they need one
# exact driver version), the driver and CUDA. DGX OS ships it as one, so it is held as one — holding
# the driver alone would let `apt upgrade` install a kernel with no NVIDIA module. Upgrade day moves
# the whole set, then re-holds it with --hold-gpu: website/how-to/updates.md.
gpu_hold_patterns() {
  printf '%s\n' 'nvidia-*' 'libnvidia-*' 'cuda-*' 'linux-modules-nvidia-*' 'linux-*nvidia-hwe-*'
  # CUDA's libraries carry the toolkit's version (libcublas-13-0), not a cuda- prefix.
  { dpkg-query -W -f='${db:Status-Abbrev}\t${Package}\n' 'cuda-toolkit-*' 2>/dev/null || true; } |
    awk '($1 == "ii" || $1 == "hi") && $2 ~ /^cuda-toolkit-[0-9]+-[0-9]+$/ {sub(/^cuda-toolkit-/, "", $2); print "*-" $2}'
}

# A problem with the GPU set stops the real run. A dry run may be a preview off the Spark (the Mac,
# CI), so it says where the real run would stop and carries on.
refuse_hold() {
  if (( DRY_RUN )); then printf '+ apt-mark hold   (a real run stops here: %s)\n' "$1"; return 0; fi
  echo "bootstrap: $1" >&2
  exit 1
}

hold_gpu_stack() {
  say "hold the GPU stack — kernel, NVIDIA modules, driver, CUDA — it moves only on upgrade day"
  local pattern rows unfinished pkgs total held missing
  local -a patterns=()
  while IFS= read -r pattern; do patterns+=("$pattern"); done < <(gpu_hold_patterns)
  # dpkg-query exits non-zero when a pattern matches nothing; that must not abort the script. An
  # empty result is refused below.
  rows="$( { dpkg-query -W -f='${db:Status-Abbrev}\t${Package}\n' "${patterns[@]}" 2>/dev/null || true; } | sort -u)"
  # The status is want, state and error flag. ii is installed and hi installed and held; rc, un
  # and pn are not installed. Anything else (iU unpacked, iF half-configured, an R flag) is a dpkg
  # run that didn't finish, and holding around it would leave those packages free to move.
  unfinished="$(awk 'NF >= 2 && $1 != "ii" && $1 != "hi" && $1 !~ /^[uihrp][nc]$/ {print "  " $2 " (" $1 ")"}' <<<"$rows")"
  if [[ -n "$unfinished" ]]; then
    {
      echo "bootstrap: these GPU-set packages are not cleanly installed, so they can't be held:"
      echo "$unfinished"
      # A removal that is pending (ri, pi) or stopped partway (rH, pF) is apt's to finish:
      # dpkg --configure -a alone leaves it as it is.
      if awk 'NF >= 2 && $1 ~ /^[rp][^nc]/ {found = 1} END {exit !found}' <<<"$rows"; then
        echo "a removal didn't finish, and dpkg --configure -a alone can't finish it."
        echo "finish it first: sudo dpkg --configure -a && sudo apt full-upgrade — read what apt plans against website/how-to/updates.md before you answer — then run this again"
      else
        echo "finish dpkg first: sudo dpkg --configure -a — then run this again"
      fi
    } >&2
    exit 1
  fi
  pkgs="$(awk '$1 == "ii" || $1 == "hi" {print $2}' <<<"$rows" | LC_ALL=C sort -u)"
  if [[ -z "$pkgs" ]]; then
    refuse_hold "nothing installed matches ${patterns[*]}, so the hold would protect nothing — check the patterns against dpkg -l"
    return 0
  fi
  total="$(wc -l <<<"$pkgs" | tr -d ' ')"
  held="$(awk '$1 == "hi"' <<<"$rows" | wc -l | tr -d ' ')"
  say "GPU set: $total packages, $held already held"
  if ! grep -q '^linux-image-' <<<"$pkgs"; then
    refuse_hold "the GPU set has no kernel (no linux-image-* package matches the patterns), so apt upgrade could install a kernel with no NVIDIA module — fix gpu_hold_patterns"
    return 0
  fi
  # shellcheck disable=SC2086  # one package per word is intended
  run apt-mark hold $pkgs
  if (( DRY_RUN )); then return 0; fi
  # apt-mark can exit 0 and still leave a package unheld: check each one against apt's own list.
  missing="$(LC_ALL=C comm -23 <(printf '%s\n' "$pkgs") <(apt-mark showhold | LC_ALL=C sort -u))"
  if [[ -n "$missing" ]]; then
    {
      echo "bootstrap: apt-mark hold did not hold these, so apt upgrade can still move them:"
      awk '{print "  " $0}' <<<"$missing"
    } >&2
    exit 1
  fi
  say "GPU set held: $total packages"
}

# Upgrade day, as one command (make upgrade-gpu, in tmux). It releases the GPU set, reads apt's plan
# and refuses one that would leave a kernel without its NVIDIA module or change the driver branch,
# moves the set, holds it again with the hold and nothing else, and checks the newest kernel for its
# NVIDIA module before it asks for the reboot. It runs updates.md's GRUB check itself (grub_boots),
# before it releases the set and again before it asks for the reboot: the next boot starts the
# newest kernel only while GRUB boots it. Every way out after the release holds the set again.
# website/how-to/updates.md has the same steps by hand, and the recovery.

# The GPU set as dpkg has it now: each package's status letters, name and version, found with the
# hold's own patterns.
gpu_set_state() {
  local pattern
  local -a patterns=()
  while IFS= read -r pattern; do patterns+=("$pattern"); done < <(gpu_hold_patterns)
  { dpkg-query -W -f='${db:Status-Abbrev}\t${Package}\t${Version}\n' "${patterns[@]}" 2>/dev/null || true; } |
    LC_ALL=C sort -u
}

# The set's held members: not everything apt-mark lists, so a package held for another reason stays
# held.
held_gpu_set() {
  gpu_set_state | awk '$1 == "hi" {print $2}' | LC_ALL=C sort -u
}

# The set without its hold letter, the first status letter: apt-mark turns ii into hi and back
# without moving anything. Two of these, before and after, say whether apt moved anything in the
# set, a package's state or its version.
gpu_set_contents() {
  gpu_set_state | cut -c2- | LC_ALL=C sort
}

# The newest kernel, which the next boot starts only while GRUB boots it (grub_boots), and the
# version of the NVIDIA module built for a kernel: nothing when it has none.
newest_kernel() {
  linux-version list | linux-version sort --reverse | head -1
}
module_version() {
  modinfo -k "$1" -F version nvidia 2>/dev/null || true
}

# Where GRUB keeps its menu and its environment block. Tests point these at stand-ins.
GRUB_CFG="${BOOTSTRAP_GRUB_CFG:-/boot/grub/grub.cfg}"
GRUBENV="${BOOTSTRAP_GRUBENV:-/boot/grub/grubenv}"

# Whether the next boot starts KERNEL, read from what GRUB will do, as updates.md step 5's GRUB
# check reads it: entry 0's first linux line in grub.cfg names vmlinuz-KERNEL; the default= lines
# are exactly the stock two, set default="${next_entry}" and set default="0", or
# "${saved_entry}" while grubenv's saved_entry is empty or 0; and grubenv holds no next_entry or
# prev_entry with a value. Otherwise, or when a read fails, it prints why and fails. It never
# prints what it read: grub.cfg's linux lines and entry ids, and grubenv's entries, carry the root
# filesystem's UUID. A kernel is named only by a version it has checked is one.
grub_boots() {
  local want="$1" linux entry0 defaults other env saved
  local version='^[0-9]+\.[0-9]+[0-9A-Za-z.+~-]*$'
  # shellcheck disable=SC2016  # GRUB's own ${...}, matched as written
  local next='set default="${next_entry}"' saved_default='set default="${saved_entry}"'
  if [[ -z "$want" ]]; then echo "linux-version found no kernel"; return 1; fi
  if [[ ! -r "$GRUB_CFG" ]]; then echo "can't read $GRUB_CFG"; return 1; fi
  linux="$(grep -m1 -E '^[[:space:]]*linux[[:space:]]' "$GRUB_CFG" || true)"
  entry0="$(awk '{print $2}' <<<"$linux")"
  entry0="${entry0##*/}"
  if [[ "$entry0" != vmlinuz-* || ! "${entry0#vmlinuz-}" =~ $version ]]; then
    echo "grub.cfg's entry 0 starts no vmlinuz-<version> kernel"
    return 1
  fi
  entry0="${entry0#vmlinuz-}"
  if [[ "$entry0" != "$want" ]]; then echo "GRUB boots $entry0, not $want"; return 1; fi
  defaults="$(grep 'default=' "$GRUB_CFG" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' || true)"
  other="$(grep -vxF "$next" <<<"$defaults" || true)"
  if [[ "$(grep -cxF "$next" <<<"$defaults" || true)" != 1 || "$(grep -c . <<<"$other" || true)" != 1 ||
        "$other" != 'set default='* ]]; then
    echo "grub.cfg's default= lines aren't the stock two"
    return 1
  fi
  if [[ "$other" != 'set default="0"' && "$other" != "$saved_default" ]]; then
    echo "grub.cfg's default isn't entry 0"
    return 1
  fi
  if [[ ! -r "$GRUBENV" ]] || ! env="$(grub-editenv "$GRUBENV" list 2>/dev/null)"; then
    echo "can't read GRUB's environment block, $GRUBENV"
    return 1
  fi
  if grep -q '^next_entry=.' <<<"$env"; then echo "grubenv's next_entry picks another entry for the next boot"; return 1; fi
  if grep -q '^prev_entry=.' <<<"$env"; then echo "grubenv's prev_entry picks another entry after a failed boot"; return 1; fi
  saved="$(sed -n 's/^saved_entry=//p' <<<"$env")"
  if [[ "$other" == "$saved_default" && -n "$saved" && "$saved" != 0 ]]; then
    echo "grubenv's saved_entry picks another entry than 0"
    return 1
  fi
  return 0
}

# Reads `apt-get -s dist-upgrade` on stdin and prints why the plan must not run. It would remove the
# modules metapackage (which brings in each new kernel's NVIDIA modules), swap it for another driver
# branch's, or install a kernel, linux-image-<version>, with no linux-modules-nvidia-* ending in
# <version>. A kernel counts as new by that name, a digit after "linux-image-"; that DGX OS names its
# kernels so is not yet checked on the box (Phase 1's Task 12 lists them). The newest-kernel check
# before the reboot catches a kernel this misses.
refused_plan() {
  local plan kernel
  plan="$(awk '$1 == "Inst" || $1 == "Remv" {sub(/:.*/, "", $2); print $1, $2}')"
  awk '$2 ~ /^linux-modules-nvidia-.*-nvidia-hwe-/ {if ($1 == "Remv") gone[$2] = 1; else came[$2] = 1}
       END {
         for (g in gone) {
           other = ""
           for (c in came) if (!(c in gone)) other = c
           if (other != "") print "  the driver branch changes: it removes " g " and installs " other
           else print "  it removes " g ", the metapackage that brings in the NVIDIA modules for each new kernel"
         }
       }' <<<"$plan"
  while IFS= read -r kernel; do
    awk -v tail="-$kernel" '$1 == "Inst" && $2 ~ /^linux-modules-nvidia-/ && substr($2, length($2) - length(tail) + 1) == tail {found = 1} END {exit !found}' <<<"$plan" ||
      echo "  it installs the kernel $kernel with no NVIDIA modules for it"
  done < <(awk '$1 == "Inst" && $2 ~ /^linux-image-[0-9]/ {sub(/^linux-image-/, "", $2); print $2}' <<<"$plan")
}

# Holds the set again. A hold that ran to the end and stopped has said what is wrong, and running it
# again would say the same. A hold cut off by a signal (exit status 128 or more) leaves REHELD at 0,
# so the way out tries once more. A dry run released nothing, so there the hold's own words are all.
rehold() {
  local code=0
  ( hold_gpu_stack ) || code=$?
  if (( code == 0 )); then REHELD=1; return 0; fi
  if (( code < 128 )); then
    REHELD=1
    if (( ! DRY_RUN )); then
      echo "bootstrap: the GPU set is not held again yet — do what the hold says above, then run make hold-gpu" >&2
    fi
  fi
  return 1
}

# A signal while the way out holds the set again, whether it reached this script or killed only the
# hold's subshell: say what is left to do.
cut_off_while_holding() {
  echo "bootstrap: stopped while holding the GPU set again — run make hold-gpu" >&2
  if (( MOVING )); then
    echo "bootstrap: apt may have moved the set, so don't reboot until step 5's checks pass: 'If it goes wrong' in website/how-to/updates.md" >&2
  fi
  exit 130
}

on_upgrade_exit() {
  local code=$?
  # The signal trap goes in before the EXIT trap comes out: a signal between the two would otherwise run
  # upgrade_gpu's own `exit 1xx` with no way out left, and the script would end without a word, the
  # set released.
  trap cut_off_while_holding HUP INT TERM
  trap - EXIT
  if (( ! REHELD )); then
    echo "bootstrap: upgrade day stopped before the end — holding the GPU set again" >&2
    rehold || code=1
    # A signal that killed only the hold's subshell (the OOM killer, a kill by pid) never reaches this
    # script's trap, and leaves the set released: say so as the trap does, with no hint to start the stack.
    if (( ! REHELD )); then cut_off_while_holding; fi
  fi
  if (( code != 0 )); then
    # After apt started, the stack may start again only if the set is as it was and the newest
    # kernel still has its module: apt can install a new kernel, outside the set, and then fail.
    local unsafe=""
    if (( MOVING )); then
      if [[ "$(gpu_set_contents)" != "$MOVE_FROM" ]]; then
        unsafe="the GPU set moved before this stopped"
      elif [[ -z "$(module_version "$(newest_kernel)")" ]]; then
        unsafe="the newest kernel has no NVIDIA module"
      elif (( GRUB_FAILED )); then
        unsafe="GRUB may not boot the newest kernel"
      fi
    fi
    if [[ -n "$unsafe" ]]; then
      echo "bootstrap: $unsafe. Don't reboot or start the stack yet: follow 'If it goes wrong' in website/how-to/updates.md" >&2
    elif [[ -n "$STOPPED" ]]; then
      echo "bootstrap: nothing in the GPU set moved, so start what was stopped again: systemctl start$STOPPED" >&2
    fi
  fi
  exit "$code"
}

upgrade_gpu() {
  say "upgrade day: move the GPU set as one — website/how-to/updates.md"
  local pkgs refusal unit kernel version grub
  local -a stack_units=(local-ai-llama-swap.service local-ai-brake.service)
  # GRUB must boot the newest kernel before anything moves (updates.md step 2), so that after the
  # move the same check can only find what the move changed.
  if (( DRY_RUN )); then
    printf '+ grub_boots <the newest kernel>   (a real run stops here, before anything is released, unless GRUB boots it)\n'
  else
    kernel="$(newest_kernel)"
    if ! grub="$(grub_boots "$kernel")"; then
      {
        echo "bootstrap: not moving the GPU set: $grub."
        echo "Nothing was released or moved. Run step 5's GRUB check in website/how-to/updates.md, and bring what it prints to the Claude session working on the repo"
      } >&2
      exit 1
    fi
    say "GRUB boots $kernel, the newest kernel"
  fi
  pkgs="$(held_gpu_set)"
  # From here on, every way out holds the set again. A dry run releases nothing, so it sets no way out:
  # cut off by a signal, it just ends, and says nothing of a hold.
  if (( ! DRY_RUN )); then
    REHELD=0
    trap on_upgrade_exit EXIT
    trap 'exit 129' HUP
    trap 'exit 130' INT
    trap 'exit 143' TERM
  fi
  if [[ -z "$pkgs" ]]; then
    say "nothing in the GPU set is held; the end of this run holds it"
  else
    # shellcheck disable=SC2086  # one package per word is intended
    run apt-mark unhold $pkgs
  fi
  run dpkg --configure -a
  run apt-get update
  # apt-get's dist-upgrade is apt's full-upgrade: it may remove packages, which moving the set needs.
  # Read the plan first, and refuse it before anything moves.
  if (( DRY_RUN )); then
    printf '+ apt-get -s dist-upgrade   (a real run stops here if the plan would leave a kernel without its NVIDIA module, or changes the driver branch)\n'
  else
    refusal="$(apt-get -s dist-upgrade | refused_plan)"
    if [[ -n "$refusal" ]]; then
      {
        echo "bootstrap: not moving the GPU set, because of apt's plan:"
        echo "$refusal"
        if grep -q 'driver branch' <<<"$refusal"; then
          echo "nothing was installed or removed; a new driver branch is a move planned and made by hand (website/how-to/updates.md)"
        else
          echo "nothing was installed or removed; try again next upgrade day (website/how-to/updates.md)"
        fi
      } >&2
      exit 1
    fi
  fi
  # The engines use the GPU; the reboot starts them again, and the brake with them.
  for unit in "${stack_units[@]}"; do
    if (( DRY_RUN )); then printf '+ systemctl stop %s   (if running)\n' "$unit"; continue; fi
    if systemctl is-active --quiet "$unit"; then
      systemctl stop "$unit"
      STOPPED="$STOPPED $unit"
    fi
  done
  if (( ! DRY_RUN )); then
    MOVE_FROM="$(gpu_set_contents)"
    MOVING=1
  fi
  run apt-get dist-upgrade
  rehold || exit 1
  if (( DRY_RUN )); then
    printf '+ modinfo -k <the newest kernel> -F version nvidia\n'
    printf "+ grub_boots <the newest kernel>   (a real run says DON'T REBOOT unless GRUB boots it)\n"
    return 0
  fi
  kernel="$(newest_kernel)"
  version="$(module_version "$kernel")"
  if [[ -z "$version" ]]; then
    echo "bootstrap: DON'T REBOOT — $kernel, the newest kernel, has no NVIDIA module. See 'If it goes wrong' in website/how-to/updates.md" >&2
    exit 1
  fi
  if ! grub="$(grub_boots "$kernel")"; then
    GRUB_FAILED=1
    echo "bootstrap: DON'T REBOOT — $grub. See 'If it goes wrong' in website/how-to/updates.md" >&2
    exit 1
  fi
  say "ready: $kernel, the newest kernel, has NVIDIA driver $version, and GRUB boots it — record both, and CUDA's version, in changelog.md"
  say "now: sudo reboot, then make doctor"
}

users_and_groups() {
  say "users and groups"
  ensure_group spark
  ensure_group spark-users
  ensure_group spark-admin
  # Phase 2a: the front and the model pull run as users of their own, so neither the engines nor the
  # pull's downloader reaches the front, and the engines can't change the model files.
  ensure_group spark-front
  ensure_group spark-pull
  ensure_system_user spark /var/lib/local-ai
  # The front has no home: it keeps nothing but its uploads, in its unit's private /tmp.
  ensure_system_user spark-front /nonexistent
  # The pull shares spark's home, which is root's: its caches are folders of its own under it,
  # pull-cache (XDG_CACHE_HOME in its unit) and the Hugging Face cache.
  ensure_system_user spark-pull /var/lib/local-ai
  ensure_agent_user
  # spark is deliberately NOT in the docker group: docker is root-equivalent, and model engines run
  # as spark. Containers are started by root-owned units instead (Phase 1).
  run usermod -aG video,render,spark-users spark
  run usermod -aG video,render,spark-users agent
  # The front reaches the gate's status socket, which is group spark-users'. It is in no other
  # group: not spark, which reads the model files, and not spark-admin. spark-pull is in its own
  # group alone.
  run usermod -aG spark-users spark-front
  # adm: read every service's journal, so `make logs` works without sudo. The admin is the only
  # account bootstrap puts in spark-admin, the gate's control socket's group (Phase 2a).
  run usermod -aG spark-admin,spark-users,adm "$ADMIN_USER"
  run chmod 0700 "/home/$ADMIN_USER" /home/agent
}

# The secret files, root's alone from Phase 2a (Dan's decision, 2026-10-07): PID 1 reads them, for a
# unit's EnvironmentFile= or LoadCredential=, and so does root's Compose, so nothing reads them as
# spark. Only root has ever been able to write in this folder (Phase 1 left it root:spark 0750), so
# root changing a file in it acts through no path anyone else controls. Each regular file becomes
# root:root 0600; anything else in it is left as it is, and named. A dry run never lists the folder.
SECRETS="${BOOTSTRAP_SECRETS:-/etc/local-ai/secrets}"
secrets_folder() {
  local file
  run install -d -o root -g root -m 0700 "$SECRETS"
  if (( DRY_RUN )); then
    printf '+ chown root:root %s/<each file>\n+ chmod 0600 %s/<each file>\n' "$SECRETS" "$SECRETS"
    return 0
  fi
  for file in "$SECRETS"/* "$SECRETS"/.[!.]* "$SECRETS"/..?*; do
    [[ -e "$file" || -L "$file" ]] || continue
    if [[ -L "$file" || ! -f "$file" ]]; then
      say "left as it is: $file, not a regular file"
      continue
    fi
    chown root:root "$file"
    chmod 0600 "$file"
  done
}

directories() {
  say "directories"
  # Code and config are root-owned and group-writable by spark-admin. spark only reads them: it runs
  # the engines. What root runs isn't here: etc/ only stages the units and the Compose project, and
  # `make install-units` installs root's own copies of them.
  run install -d -o root -g spark-admin -m 2775 /opt/local-ai /opt/local-ai/app /opt/local-ai/bin /opt/local-ai/etc /opt/local-ai/python
  run install -d -o root -g spark-admin -m 0750 /etc/local-ai
  secrets_folder
  # State. The parent is root's and spark writes only inside its children: `install -d` follows a
  # symlink, so a spark-owned parent would let spark swap a child for a link that the next re-run
  # hands to it. It is also spark's home, and spark-pull's, so a cache under $HOME needs a child
  # of its own here and its variable (XDG_CACHE_HOME, CUDA_CACHE_PATH) set in the unit.
  run install -d -o root -g root -m 0755 /var/lib/local-ai
  # The Hugging Face cache isn't here: install -d sets the owner and mode of a folder that exists,
  # so every re-run would hand it back. Its own step, hf_cache_to_pull, gives it to spark-pull.
  # gate and launch: the gate's state, and the admission tickets and launch's records; whisper-tmp:
  # whisper's --tmp-dir, outside the cache (Phase 2a).
  run install -d -o spark -g spark -m 0750 /var/lib/local-ai/open-webui /var/lib/local-ai/searxng \
    /var/lib/local-ai/cache /var/lib/local-ai/cuda-cache /var/lib/local-ai/gate /var/lib/local-ai/launch \
    /var/lib/local-ai/whisper-tmp
  # launch's own folders are inside a folder spark owns, so spark makes them: root's install -d
  # there would follow a link spark planted, and hand whatever it points at to spark.
  run runuser -u spark -- install -d -o spark -g spark -m 0750 /var/lib/local-ai/launch/tickets \
    /var/lib/local-ai/launch/started /var/lib/local-ai/launch/refusals
  run install -d -o spark -g spark-admin -m 2770 /var/lib/local-ai/brake
  # The pull's own cache (XDG_CACHE_HOME in its unit): hf_xet and huggingface_hub keep more than
  # the model files.
  run install -d -o spark-pull -g spark-pull -m 0750 /var/lib/local-ai/pull-cache
  # Where the failure notifier's script goes (notifier, below), made before anything is installed
  # in it: /usr/local/libexec isn't on this box (checked 2026-10-14), and install doesn't make folders.
  run install -d -o root -g root -m 0755 /usr/local/libexec
  # Nothing inside agent's home: agent controls it, so root never writes there. agent makes its
  # own ~/work.
}

# The Hugging Face cache moves to spark-pull (Phase 2a), which downloads into it, with spark as its
# group and every folder setgid, so the engines, running as spark, read every model file through
# the group and can't change one, and every file the pull adds takes spark's group too. Its owners
# change only once, with chown -R -P --no-dereference, which never follows a link. Until then it is
# spark's, so root acts through folders spark controls: the move refuses, changing nothing, while
# any unit that runs as spark or spark-pull is active, so no process of theirs can race it, or while
# fs.protected_hardlinks is off, so no hard link spark made to someone else's file can be in it. A cache already moved needs nothing,
# whatever runs, so a re-run with the stack up passes. hf/tmp, whisper's --tmp-dir in Phase 1's
# registry, stays spark's while the deployed registry names it, and the check never looks at it:
# the close removes it once nothing does (Phase 2a's Task 51). Tests point these at stand-ins, and
# PULL_USER and CACHE_GROUP at whoever runs them: the commands always name spark-pull and spark.
HF_CACHE="${BOOTSTRAP_HF_CACHE:-/var/lib/local-ai/hf}"
DEPLOYED_REGISTRY="${BOOTSTRAP_REGISTRY:-/opt/local-ai/etc/models.yaml}"
PULL_USER="${BOOTSTRAP_PULL_USER:-spark-pull}"
CACHE_GROUP="${BOOTSTRAP_CACHE_GROUP:-spark}"
CACHE_UNITS=(local-ai-llama-swap.service local-ai-brake.service local-ai-pull.service local-ai-gate.service
  local-ai-brake-drill.service)
# Phase 1's registry names it in whisper's args, `--tmp-dir, /var/lib/local-ai/hf/tmp,`. Anything
# else naming it (a comment) errs the safe way: hf/tmp stays spark's.
OLD_WHISPER_TMP='/var/lib/local-ai/hf/tmp([^[:alnum:]_.-]|$)'
REGISTRY_LIMIT=1048576  # bytes: the registry is about 10 KB

# 0 when the cache is moved: spark-pull:spark 2750, everything in it spark-pull's and in spark's
# group, every folder setgid, hf/tmp not looked at. 1 when anything isn't; 2 when it can't be read.
cache_moved() {
  local top odd
  top="$(stat -c '%U:%G %a' -- "$HF_CACHE" 2>/dev/null)" || return 2
  [[ "$top" == "$PULL_USER:$CACHE_GROUP 2750" ]] || return 1
  odd="$(find -P "$HF_CACHE" -mindepth 1 -path "$HF_CACHE/tmp" -prune -o \
    \( ! -user "$PULL_USER" -o ! -group "$CACHE_GROUP" -o -type d ! -perm -2000 \) -print -quit 2>/dev/null)" ||
    return 2
  [[ -z "$odd" ]]
}

# Each reason the move can't go ahead now, one a line, with what to do about it.
move_refusals() {
  local unit hardlinks
  for unit in "${CACHE_UNITS[@]}"; do
    if systemctl is-active --quiet "$unit"; then echo "$unit is active: stop it first (systemctl stop $unit)"; fi
  done
  hardlinks="$(sysctl -n fs.protected_hardlinks 2>/dev/null || true)"
  if [[ "$hardlinks" != 1 ]]; then
    echo "fs.protected_hardlinks is ${hardlinks:-unreadable}, not 1: with it off, a hard link spark made to a file it doesn't own would go to spark-pull with the cache"
  fi
}

# 0 when the deployed registry names hf/tmp, 1 when it doesn't, 2 when it can't be read. A real run
# reads it as the admin who ran sudo, as install_units reads what make apply staged, within a time
# limit and a size cap.
registry_names_hf_tmp() {
  local text
  local -a read_cmd=(timeout "$READ_TIMEOUT" head -c "$((REGISTRY_LIMIT + 1))" -- "$DEPLOYED_REGISTRY")
  if (( ! DRY_RUN || EUID == 0 )); then read_cmd=(runuser -u "$ADMIN_USER" -- "${read_cmd[@]}"); fi
  text="$("${read_cmd[@]}" 2>/dev/null)" || return 2
  if (( $(printf '%s' "$text" | wc -c) > REGISTRY_LIMIT )); then return 2; fi
  if grep -qE -- "$OLD_WHISPER_TMP" <<<"$text"; then return 0; fi
  return 1
}

hf_cache_to_pull() {
  say "the Hugging Face cache: spark-pull's, with a setgid group that spark reads the model files through"
  local state=0 look=1 named=0 keep=0 refusals why="" line short=""
  if [[ ! -e "$HF_CACHE" && ! -L "$HF_CACHE" ]]; then
    # A new box: nothing to move. mkdir, not install -d, which would reset a cache that exists.
    run mkdir -m 0750 "$HF_CACHE"
    run chown spark-pull:spark "$HF_CACHE"
    run chmod 2750 "$HF_CACHE"
    say "made $HF_CACHE, empty: spark-pull:spark 2750"
    return 0
  fi
  if [[ -L "$HF_CACHE" || ! -d "$HF_CACHE" ]]; then
    echo "bootstrap: $HF_CACHE is a link or not a folder, so nothing was changed: look at it with sudo ls -ld $HF_CACHE" >&2
    exit 1
  fi
  cache_moved || state=$?
  if (( state == 0 )); then
    say "$HF_CACHE is already spark-pull:spark 2750, its folders setgid: nothing to move"
    return 0
  fi
  [[ -r "$HF_CACHE" && -x "$HF_CACHE" ]] || look=0
  if (( state == 2 || ! look )); then
    if (( ! DRY_RUN )); then
      echo "bootstrap: can't read $HF_CACHE to check whether it is spark-pull's yet, so nothing in it was changed" >&2
      exit 1
    fi
    say "a dry run can't look inside $HF_CACHE as you: it shows what a real run does if anything in it isn't spark-pull's yet"
  fi
  refusals="$(move_refusals)"
  if [[ -n "$refusals" ]]; then
    if (( DRY_RUN )); then
      # Each reason without what to do about it: the part before its colon.
      while IFS= read -r line; do short+="${short:+; }${line%%:*}"; done <<<"$refusals"
      printf '+ chown -R -P --no-dereference spark-pull:spark %s   (a real run stops here: %s)\n' "$HF_CACHE" "$short"
      return 0
    fi
    {
      echo "bootstrap: the Hugging Face cache isn't spark-pull's yet, and it can't move now, so nothing in it was changed:"
      while IFS= read -r line; do printf '  %s\n' "$line"; done <<<"$refusals"
      echo "then run make bootstrap again: website/how-to/bootstrap.md, Phase 2a's re-run"
    } >&2
    exit 1
  fi
  if (( ! look )) || [[ -e "$HF_CACHE/tmp" || -L "$HF_CACHE/tmp" ]]; then
    registry_names_hf_tmp || named=$?
    case "$named" in
      0) keep=1; why="the deployed registry still names it as whisper's --tmp-dir" ;;
      1) keep=0 ;;
      *) keep=1; why="the deployed registry, $DEPLOYED_REGISTRY, couldn't be read as $ADMIN_USER, so it may still be whisper's" ;;
    esac
  fi
  if (( keep )); then
    say "$HF_CACHE/tmp stays as it is, spark's: $why"
    run chown --no-dereference spark-pull:spark "$HF_CACHE"
    run find -P "$HF_CACHE" -mindepth 1 -maxdepth 1 ! -path "$HF_CACHE/tmp" \
      -exec chown -R -P --no-dereference spark-pull:spark {} +
    run chmod 2750 "$HF_CACHE"
    run find -P "$HF_CACHE" -mindepth 1 -path "$HF_CACHE/tmp" -prune -o -type d -exec chmod g+s {} +
  else
    run chown -R -P --no-dereference spark-pull:spark "$HF_CACHE"
    run chmod 2750 "$HF_CACHE"
    run find -P "$HF_CACHE" -mindepth 1 -type d -exec chmod g+s {} +
  fi
  (( DRY_RUN )) && return 0
  # As the hold checks apt-mark's work: the move must have taken.
  if ! cache_moved; then
    echo "bootstrap: the move didn't take: $HF_CACHE isn't spark-pull:spark 2750 with setgid folders yet" >&2
    exit 1
  fi
  say "moved: $HF_CACHE is spark-pull:spark 2750, its folders setgid"
}

# The failure notifier's script (Phase 2a), which local-ai-notify@.service runs on a unit's
# failure: root's own regular file, in a folder only root writes (directories makes it).
NOTIFIER="/usr/local/libexec/local-ai-notify"
notifier() {
  say "the failure notifier's script, which local-ai-notify@.service runs: root's own"
  run install -o root -g root -m 0755 "$HERE/local-ai-notify" "$NOTIFIER"
}

headless() {
  say "boot to a console; start a desktop on demand with: sudo systemctl start display-manager"
  run systemctl set-default multi-user.target
  if (( DRY_RUN )); then printf '+ systemctl stop display-manager   (if running)\n'; return; fi
  if systemctl is-active --quiet display-manager; then systemctl stop display-manager; fi
}

earlyoom_config() {
  say "earlyoom — the last-resort backstop below spark's own brake"
  run install -m 0644 "$HERE/earlyoom.default" /etc/default/earlyoom
  run systemctl enable earlyoom
  run systemctl restart earlyoom
}

needrestart_config() {
  say "needrestart — leave the stack's local-ai-* units alone after an apt run"
  run install -D -m 0644 "$HERE/needrestart.conf" /etc/needrestart/conf.d/local-ai.conf
}

firewall() {
  say "firewall: SSH only (tailnet traffic is governed by Tailscale ACLs, which ufw doesn't see)"
  run ufw default deny incoming
  run ufw default allow outgoing
  run ufw allow OpenSSH
  run ufw --force enable
}

polkit_rule() {
  say "polkit: spark-admin starts, stops and restarts the seven local-ai services without sudo (llama-swap, brake, compose, pull, front, gate, brake-drill), and nothing more"
  run install -m 0644 "$HERE/50-local-ai.rules" /etc/polkit-1/rules.d/50-local-ai.rules
}

# Root's own copies of the units and the Compose project that root runs (make install-units). They
# are regular files owned by root and not writable by group or others, in folders only root can
# write, so nothing running as the admin changes what root runs without sudo. `spark apply` stages
# them in STAGED, which the admin can write: each staged file must be a regular file, and root
# reads it as the admin who ran sudo, so a link planted there can't make root copy, or show, a
# file the admin can't read. The read has a time limit and a size cap, so a file swapped after the
# check can't hang root or fill /tmp. A copy holding a control character, one of ASCII's other than
# tab and newline or a C1 control in UTF-8, is refused, so none can make the terminal hide a line of
# the diff. It shows what would change, and asks, before it installs anything; run again with
# nothing changed, it changes nothing. Tests point these five at stand-ins.
STAGED="${BOOTSTRAP_STAGED:-/opt/local-ai/etc}"
UNIT_DIR="${BOOTSTRAP_UNIT_DIR:-/etc/systemd/system}"
COMPOSE_DIR="${BOOTSTRAP_COMPOSE_DIR:-/etc/local-ai/compose}"
ROOT_USER="${BOOTSTRAP_ROOT_USER:-root}"
READ_TIMEOUT="${BOOTSTRAP_READ_TIMEOUT:-10}"  # seconds for each staged file; reading one takes milliseconds
READ_LIMIT=65536  # bytes: each unit is about 1 KB and compose.yaml about 2 KB, so this leaves room to grow
# ROOT_UNITS is render.UNITS, and ENABLED_UNITS every one of them with an [Install]: the pull, the
# failure notifier (a template OnFailure= starts) and the S05 drill run only when started.
ROOT_UNITS=(local-ai-llama-swap.service local-ai-brake.service local-ai-compose.service local-ai-pull.service
  local-ai-front.socket local-ai-front.service local-ai-gate-status.socket local-ai-gate-control.socket
  local-ai-gate.service local-ai-notify@.service local-ai-brake-drill.service)
ENABLED_UNITS=(local-ai-llama-swap.service local-ai-brake.service local-ai-compose.service local-ai-front.socket
  local-ai-front.service local-ai-gate-status.socket local-ai-gate-control.socket local-ai-gate.service)
COMPOSE_FILES=(compose.yaml searxng/settings.yml)
STAGED_COPY=""  # install-units: root's private copy of what it read, removed on the way out

# The files root runs, one "staged|installed" pair a line.
root_files() {
  local unit file
  for unit in "${ROOT_UNITS[@]}"; do printf '%s|%s\n' "$STAGED/systemd/$unit" "$UNIT_DIR/$unit"; done
  for file in "${COMPOSE_FILES[@]}"; do printf '%s|%s\n' "$STAGED/compose/$file" "$COMPOSE_DIR/$file"; done
}

# Whether a path is root's own: there, not a link, owned by ROOT_USER, not writable by group or others.
roots_own() {
  [[ -e "$1" && ! -L "$1" && -z "$(find "$1" -prune \( ! -user "$ROOT_USER" -o -perm -020 -o -perm -002 \) -print)" ]]
}

install_units() {
  say "root's own copies of the units and the Compose project that root runs"
  local reader="" staged installed n=0 folders=0 folder answer from unit entry
  local -a changed=() enable=() read_cmd=() shown=()
  # A real run is root's, and so is a dry run under sudo: either reads as the admin who ran sudo.
  if (( ! DRY_RUN || EUID == 0 )); then
    if [[ -z "${SUDO_USER:-}" || "$SUDO_USER" == root ]]; then
      echo "bootstrap: run it with sudo from your own account (make install-units): root reads what make apply staged as you" >&2
      exit 1
    fi
    reader="$SUDO_USER"
  fi
  STAGED_COPY="$(mktemp -d)"
  trap 'rm -rf "$STAGED_COPY"' EXIT
  while IFS='|' read -r staged installed; do
    n=$((n + 1))
    if [[ -L "$staged" || ! -f "$staged" ]]; then
      echo "bootstrap: $staged is missing or isn't a regular file, so nothing was installed — run make apply, then this again" >&2
      exit 1
    fi
    read_cmd=(timeout "$READ_TIMEOUT" head -c "$((READ_LIMIT + 1))" -- "$staged")
    if [[ -n "$reader" ]]; then read_cmd=(runuser -u "$reader" -- "${read_cmd[@]}"); fi
    if ! "${read_cmd[@]}" > "$STAGED_COPY/$n"; then
      echo "bootstrap: ${reader:-your account} can't read $staged, or not within $READ_TIMEOUT s, so nothing was installed" >&2
      exit 1
    fi
    if (( $(wc -c < "$STAGED_COPY/$n") > READ_LIMIT )); then
      echo "bootstrap: $staged is over $READ_LIMIT bytes, far more than a unit or a Compose file, so nothing was installed" >&2
      exit 1
    fi
    # Only tab, newline, printable ASCII and bytes 0x80 to 0xFF, and among those no C1 control in
    # UTF-8 (C2 80 to C2 9F): a carriage return, an escape sequence or a C1 control such as CSI can
    # make a terminal hide a line of the diff, and a NUL makes diff show no lines at all.
    if (( $(LC_ALL=C tr -d '\011\012\040-\176\200-\377' < "$STAGED_COPY/$n" | wc -c) > 0 )) ||
      LC_ALL=C grep -aq $'\xc2[\x80-\x9f]' "$STAGED_COPY/$n"; then
      echo "bootstrap: $staged holds control characters, which could hide a line of the diff below, so nothing was installed" >&2
      exit 1
    fi
    if [[ ! -f "$installed" ]] || ! roots_own "$installed" || ! cmp -s "$STAGED_COPY/$n" "$installed"; then
      changed+=("$n|$staged|$installed")
    fi
  done < <(root_files)
  for folder in "$COMPOSE_DIR" "$COMPOSE_DIR/searxng"; do
    if [[ ! -d "$folder" ]] || ! roots_own "$folder"; then folders=1; fi
  done
  # What root will run, before root runs it: every change, and every copy that isn't root's own,
  # indented so a diff's + lines can't be taken for the commands below.
  if (( folders )); then
    echo "    $COMPOSE_DIR and its searxng folder: missing, or not root's own"
  fi
  if (( ${#changed[@]} )); then
    for entry in "${changed[@]}"; do
      IFS='|' read -r n staged installed <<<"$entry"
      from="$installed"
      if [[ -L "$installed" ]]; then echo "    $installed: a link, which root's own regular file replaces"; fi
      if [[ -L "$installed" || ! -f "$installed" ]]; then from=/dev/null; fi
      if [[ "$from" != /dev/null ]] && cmp -s "$from" "$STAGED_COPY/$n"; then
        echo "    $installed: the same text, but not root's own regular file"
      else
        { diff -u --label "installed: $installed" --label "staged: $staged" "$from" "$STAGED_COPY/$n" || true; } |
          sed 's/^/    /'
      fi
    done
  fi
  if (( ! folders && ${#changed[@]} == 0 )); then
    say "root's copies match what make apply staged: nothing to install"
  else
    if (( ! DRY_RUN )); then
      read -r -p "install these as root? [y/N] " answer || answer=""
      case "$answer" in
        [yY] | [yY][eE][sS]) ;;
        *) echo "bootstrap: nothing was installed" >&2; exit 1 ;;
      esac
    fi
    if (( folders )); then run install -d -o root -g root -m 0755 "$COMPOSE_DIR" "$COMPOSE_DIR/searxng"; fi
    if (( ${#changed[@]} )); then
      for entry in "${changed[@]}"; do
        IFS='|' read -r n staged installed <<<"$entry"
        from="$STAGED_COPY/$n"
        if (( DRY_RUN )); then from="$staged"; fi
        run install -o root -g root -m 0644 "$from" "$installed"
      done
    fi
  fi
  # systemd reads a unit file when told to: after this run installs one, or when a run that
  # installed one was cut off before it told systemd. systemd 255 refuses `systemctl show` for a
  # template's name (local-ai-notify@.service) and then says nothing for the units after it, so
  # templates are left out: a template's new copy from a run cut off before its reload is read
  # at the next run that installs anything.
  for unit in "${ROOT_UNITS[@]}"; do
    if [[ "$unit" != *@.service ]]; then shown+=("$unit"); fi
  done
  if (( ${#changed[@]} )) || [[ "$(systemctl show --property=NeedDaemonReload --value "${shown[@]}" 2>/dev/null || true)" == *yes* ]]; then
    run systemctl daemon-reload
  fi
  for unit in "${ENABLED_UNITS[@]}"; do
    systemctl is-enabled --quiet "$unit" 2>/dev/null || enable+=("$unit")
  done
  if (( ${#enable[@]} )); then run systemctl enable "${enable[@]}"; fi
  if (( ! DRY_RUN && (folders || ${#changed[@]}) )); then
    say "installed. make apply restarts each unit that runs an older definition, llama-swap only while no model is loaded (or make apply-now)"
  fi
}

main() {
  parse_args "$@"
  preflight
  if (( HOLD_ONLY )); then
    # Upgrade day: re-hold the set and nothing else — no desktop stop, no earlyoom restart, no
    # owner and mode resets.
    hold_gpu_stack
    return 0
  fi
  if (( INSTALL_UNITS )); then
    # Root's copies of what make apply staged, and nothing else.
    install_units
    return 0
  fi
  if (( UPGRADE )); then
    upgrade_gpu
    return 0
  fi
  packages
  hold_gpu_stack
  users_and_groups
  directories
  hf_cache_to_pull
  notifier
  headless
  earlyoom_config
  needrestart_config
  firewall
  polkit_rule
  say "done — continue with website/how-to/bootstrap.md, 'After bootstrap'"
}

# Run only when executed. Sourcing the file (the tests do) just defines the functions.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then main "$@"; fi

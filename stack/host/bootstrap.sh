#!/usr/bin/env bash
# Host setup for brightroar (DGX OS, Ubuntu 24.04, aarch64).
#   Preview (changes nothing):  bash stack/host/bootstrap.sh --dry-run
#   Apply (Dan, once):          sudo bash stack/host/bootstrap.sh
#   Re-hold the GPU set only:   sudo bash stack/host/bootstrap.sh --hold-gpu   (upgrade day; add
#                               --dry-run to preview it)
#   Install root's copies:      make install-units   (of the units and the Compose project that
#                               make apply staged; it runs this script with --install-units, and
#                               make install-units-dry-run previews it)
# Safe to re-run: every step checks before it changes anything.
set -euo pipefail

DRY_RUN=0
HOLD_ONLY=0
INSTALL_UNITS=0

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
      # A mistyped --dry-run under sudo must not turn into a real run.
      *) echo "bootstrap: unknown option '$arg' (the options are --dry-run, --hold-gpu and --install-units)" >&2; exit 2 ;;
    esac
  done
  if (( HOLD_ONLY && INSTALL_UNITS )); then
    echo "bootstrap: --hold-gpu and --install-units are separate modes; pick one" >&2
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

ensure_service_user() {
  if (( DRY_RUN )); then
    printf '+ useradd --system --gid spark --home-dir /var/lib/local-ai --no-create-home --shell /usr/sbin/nologin spark   (if missing)\n'
    return
  fi
  id -u spark >/dev/null 2>&1 || useradd --system --gid spark --home-dir /var/lib/local-ai --no-create-home --shell /usr/sbin/nologin spark
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

users_and_groups() {
  say "users and groups"
  ensure_group spark
  ensure_group spark-users
  ensure_group spark-admin
  ensure_service_user
  ensure_agent_user
  # spark is deliberately NOT in the docker group: docker is root-equivalent, and model engines run
  # as spark. Containers are started by root-owned units instead (Phase 1).
  run usermod -aG video,render,spark-users spark
  run usermod -aG video,render,spark-users agent
  # adm: read every service's journal, so `make logs` works without sudo.
  run usermod -aG spark-admin,spark-users,adm "$ADMIN_USER"
  run chmod 0700 "/home/$ADMIN_USER" /home/agent
}

directories() {
  say "directories"
  # Code and config are root-owned and group-writable by spark-admin. spark only reads them: it runs
  # the engines. What root runs isn't here: etc/ only stages the units and the Compose project, and
  # `make install-units` installs root's own copies of them.
  run install -d -o root -g spark-admin -m 2775 /opt/local-ai /opt/local-ai/app /opt/local-ai/bin /opt/local-ai/etc /opt/local-ai/python
  run install -d -o root -g spark-admin -m 0750 /etc/local-ai
  run install -d -o root -g spark -m 0750 /etc/local-ai/secrets
  # State. The parent is root's and spark writes only inside its children: `install -d` follows a
  # symlink, so a spark-owned parent would let spark swap a child for a link that the next re-run
  # hands to it. It is also spark's home, so a cache under $HOME needs a spark-owned child here
  # and its variable (XDG_CACHE_HOME, CUDA_CACHE_PATH) set in the unit.
  run install -d -o root -g root -m 0755 /var/lib/local-ai
  run install -d -o spark -g spark -m 0750 /var/lib/local-ai/hf /var/lib/local-ai/open-webui /var/lib/local-ai/searxng \
    /var/lib/local-ai/cache /var/lib/local-ai/cuda-cache
  run install -d -o spark -g spark-admin -m 2770 /var/lib/local-ai/brake
  # Nothing inside agent's home: agent controls it, so root never writes there. agent makes its
  # own ~/work.
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

firewall() {
  say "firewall: SSH only (tailnet traffic is governed by Tailscale ACLs, which ufw doesn't see)"
  run ufw default deny incoming
  run ufw default allow outgoing
  run ufw allow OpenSSH
  run ufw --force enable
}

polkit_rule() {
  say "polkit: spark-admin starts, stops and restarts the four local-ai units without sudo, and nothing more"
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
ROOT_UNITS=(local-ai-llama-swap.service local-ai-brake.service local-ai-compose.service local-ai-pull.service)
ENABLED_UNITS=(local-ai-llama-swap.service local-ai-brake.service local-ai-compose.service)  # pull runs when asked
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
  local -a changed=() enable=() read_cmd=()
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
  # installed one was cut off before it told systemd.
  if (( ${#changed[@]} )) || [[ "$(systemctl show --property=NeedDaemonReload --value "${ROOT_UNITS[@]}" 2>/dev/null || true)" == *yes* ]]; then
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
  packages
  hold_gpu_stack
  users_and_groups
  directories
  headless
  earlyoom_config
  firewall
  polkit_rule
  say "done — continue with website/how-to/bootstrap.md, 'After bootstrap'"
}

# Run only when executed. Sourcing the file (the tests do) just defines the functions.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then main "$@"; fi

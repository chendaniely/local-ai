#!/usr/bin/env bash
# Host setup for brightroar (DGX OS, Ubuntu 24.04, aarch64).
#   Preview (changes nothing):  bash stack/host/bootstrap.sh --dry-run
#   Apply (Dan, once):          sudo bash stack/host/bootstrap.sh
#   Re-hold the GPU set only:   sudo bash stack/host/bootstrap.sh --hold-gpu   (upgrade day; add
#                               --dry-run to preview it)
# Safe to re-run: every step checks before it changes anything.
set -euo pipefail

DRY_RUN=0
HOLD_ONLY=0

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
      # A mistyped --dry-run under sudo must not turn into a real run.
      *) echo "bootstrap: unknown option '$arg' (the options are --dry-run and --hold-gpu)" >&2; exit 2 ;;
    esac
  done
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
  # the engines, and root runs the units and the Compose file that live in etc/.
  run install -d -o root -g spark-admin -m 2775 /opt/local-ai /opt/local-ai/app /opt/local-ai/bin /opt/local-ai/etc /opt/local-ai/python
  run install -d -o root -g spark-admin -m 0750 /etc/local-ai
  run install -d -o root -g spark -m 0750 /etc/local-ai/secrets
  # State. The parent is root's and spark writes only inside its children: `install -d` follows a
  # symlink, so a spark-owned parent would let spark swap a child for a link that the next re-run
  # hands to it. It is also spark's home, so a cache under $HOME needs a spark-owned child here
  # and its variable (XDG_CACHE_HOME, CUDA_CACHE_PATH) set in the unit.
  run install -d -o root -g root -m 0755 /var/lib/local-ai
  run install -d -o spark -g spark -m 0750 /var/lib/local-ai/hf /var/lib/local-ai/open-webui /var/lib/local-ai/searxng
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
  say "let spark-admin manage the local-ai-* services without sudo"
  run install -m 0644 "$HERE/50-local-ai.rules" /etc/polkit-1/rules.d/50-local-ai.rules
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

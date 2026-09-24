#!/usr/bin/env bash
# Host setup for brightroar (DGX OS, Ubuntu 24.04, aarch64).
#   Preview (changes nothing):  bash stack/host/bootstrap.sh --dry-run
#   Apply (Dan, once):          sudo bash stack/host/bootstrap.sh
# Safe to re-run: every step checks before it changes anything.
set -euo pipefail

DRY_RUN=0
if [[ "${1:-}" == "--dry-run" ]]; then DRY_RUN=1; fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ADMIN_USER="${SUDO_USER:-dan}"

say() { printf '==> %s\n' "$*"; }
run() {
  if (( DRY_RUN )); then printf '+ %s\n' "$*"; else "$@"; fi
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
  run apt-get install -y earlyoom ufw tmux cmake build-essential ffmpeg jq
}

hold_gpu_stack() {
  say "hold the NVIDIA driver and CUDA packages — they are upgraded deliberately, on upgrade day"
  if (( DRY_RUN )); then
    printf '+ apt-mark hold <installed packages matching nvidia-*, libnvidia-*, cuda-*>\n'
    return
  fi
  local pkgs
  # dpkg-query exits non-zero when a pattern matches nothing; that must not abort the script.
  pkgs="$( { dpkg-query -W -f='${db:Status-Abbrev}\t${Package}\n' 'nvidia-*' 'libnvidia-*' 'cuda-*' 2>/dev/null || true; } | awk '$1 == "ii" {print $2}' | sort -u)"
  if [[ -n "$pkgs" ]]; then
    # shellcheck disable=SC2086  # one package per word is intended
    apt-mark hold $pkgs
  fi
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
  run install -d -o spark -g spark -m 0751 /var/lib/local-ai
  run install -d -o spark -g spark -m 0750 /var/lib/local-ai/hf /var/lib/local-ai/open-webui /var/lib/local-ai/searxng
  run install -d -o spark -g spark-admin -m 2770 /var/lib/local-ai/brake
  run install -d -o agent -g agent -m 0700 /home/agent/work
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
  preflight
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

main "$@"

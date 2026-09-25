---
title: "Bootstrap the Spark"
description: "Bounce the wired NIC, run make bootstrap, check the results, and set up agent's SSH and Claude Code login."
---

## Before you start

1. SSH into the Spark over **Wi-Fi** — not the wired interface. Bouncing the interface you're
   connected through would drop your own session.
2. Find the wired interface's name: `nmcli device status`.
3. Bounce it so it takes its DHCP reservation:

   ```bash
   sudo nmcli device disconnect <wired-iface> && sudo nmcli device connect <wired-iface>
   ```
4. Confirm the wired address now ends in `.201`.

## Run it

1. `make bootstrap-dry-run` — read the output before doing anything else. Its hold step prints
   `GPU set: N packages, M already held`: on a bootstrapped Spark, all of them held (151 at the
   2026-09-24 bootstrap). Off the Spark (the Mac, CI), with no DGX kernel installed, it says
   `a real run stops here` instead. A package that isn't cleanly installed stops the dry run too,
   with a hint for finishing it.
2. `make bootstrap` — run it over SSH; it stops any running desktop session.

## After bootstrap

- Log out and back in — new group membership only takes effect on a fresh login.
- `systemctl get-default` → `multi-user.target`
- `systemctl is-active earlyoom` → `active`
- `sudo ufw status` → OpenSSH allowed
- `id agent` → no `docker`, `sudo` or `spark-admin` in the list
- `apt-mark showhold | grep -E '^(linux-image-nvidia-hwe|nvidia-driver|cuda-toolkit)-'` → the
  kernel, the driver and CUDA among the held GPU set. From here on, updates follow
  [Updates](updates.md).
- `free -g` → record this as the new baseline

## Agent's SSH and Claude Code login

1. On the Mac, make agent its own key and copy the **public** half to the Spark. Keys and the
   Mac's `~/.ssh/config` are in [SSH from the Mac](ssh.md):

   ```bash
   ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519-brightroar_agent -C "agent@brightroar"
   scp ~/.ssh/id_ed25519-brightroar_agent.pub brightroar:agent-key.pub
   ```
2. Install it for `agent`, from an interactive `ssh brightroar` session. `sudo` needs a terminal
   to ask for your password, and a command piped into `ssh` doesn't have one:

   ```bash
   sudo install -d -m 700 -o agent -g agent /home/agent/.ssh && sudo tee -a /home/agent/.ssh/authorized_keys < ~/agent-key.pub >/dev/null && sudo chown agent:agent /home/agent/.ssh/authorized_keys && sudo chmod 600 /home/agent/.ssh/authorized_keys && rm ~/agent-key.pub
   ```
3. `ssh brightroar-agent` (the alias from [SSH from the Mac](ssh.md))
4. As `agent` — the installer refuses to run under sudo:

   ```bash
   curl -fsSL https://claude.ai/install.sh | bash
   ```
5. Log in: run `claude`. With no browser on the box, press `c` to copy the login URL, open it on
   the Mac, and paste the code back.

## Live checks (you run these — they need sudo)

- `sudo -u agent sh -c 'if test -x "$1"; then echo "OPEN: stop and fix permissions"; else echo "closed: good"; fi' _ "$HOME"`
  → `closed: good`. `$HOME` expands in your shell, so it names your home whatever your login is;
  `test -x` asks whether `agent` can enter it at all, without reading anything in it. The verdict
  comes from `agent`'s own shell, so a failed `sudo` shows its error, never a false "good". Plain
  `-u`, not `-iu`: `-i` re-reads the command in `agent`'s login shell, which would expand `$1`
  there, to nothing.
- `sudo -iu agent docker ps` → permission denied
- `sudo -iu agent nvidia-smi --query-gpu=name --format=csv,noheader` → the GPU's name, without the
  per-unit UUID that `nvidia-smi -L` prints

## Re-run once

`make bootstrap` again → finishes with no errors, and no new users, groups or config lines.

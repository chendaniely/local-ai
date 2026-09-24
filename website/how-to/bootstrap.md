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

1. `make bootstrap-dry-run` — read the output before doing anything else.
2. `make bootstrap` — run it over SSH; it stops any running desktop session.

## After bootstrap

- Log out and back in — new group membership only takes effect on a fresh login.
- `systemctl get-default` → `multi-user.target`
- `systemctl is-active earlyoom` → `active`
- `sudo ufw status` → OpenSSH allowed
- `id agent` → no `docker`, `sudo` or `spark-admin` in the list
- `free -g` → record this as the new baseline

## Agent's SSH and Claude Code login

1. Copy your Mac's **public** key to the Spark. On the Mac:

   ```bash
   scp ~/.ssh/<your-key>.pub brightroar:agent-key.pub
   ```
2. Install it for `agent`, from an interactive `ssh brightroar` session. `sudo` needs a terminal
   to ask for your password, and a command piped into `ssh` doesn't have one:

   ```bash
   sudo install -d -m 700 -o agent -g agent /home/agent/.ssh && sudo tee -a /home/agent/.ssh/authorized_keys < ~/agent-key.pub >/dev/null && sudo chown agent:agent /home/agent/.ssh/authorized_keys && sudo chmod 600 /home/agent/.ssh/authorized_keys && rm ~/agent-key.pub
   ```
3. `ssh agent@brightroar`
4. As `agent` — the installer refuses to run under sudo:

   ```bash
   curl -fsSL https://claude.ai/install.sh | bash
   ```
5. Log in: run `claude`. With no browser on the box, press `c` to copy the login URL, open it on
   the Mac, and paste the code back.

## Live checks (you run these — they need sudo)

- `sudo -iu agent test -r /home/dan/.secrets && echo "READABLE: stop and fix permissions" || echo "not readable: good"`
  → `not readable: good`. It tests access without ever printing the file.
- `sudo -iu agent docker ps` → permission denied
- `sudo -iu agent nvidia-smi --query-gpu=name --format=csv,noheader` → the GPU's name, without the
  per-unit UUID that `nvidia-smi -L` prints

## Re-run once

`make bootstrap` again → finishes with no errors, and no new users, groups or config lines.

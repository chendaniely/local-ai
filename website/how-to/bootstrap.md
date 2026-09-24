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

## After

- Log out and back in — new group membership only takes effect on a fresh login.
- `systemctl get-default` → `multi-user.target`
- `systemctl is-active earlyoom` → `active`
- `sudo ufw status` → OpenSSH allowed
- `id agent` → no `docker`, `sudo` or `spark-admin` in the list
- `free -g` → record this as the new baseline

## Agent's SSH and Claude Code login

1. Add your Mac's **public** key to `agent`:

   ```bash
   cat ~/.ssh/<your-key>.pub | ssh brightroar 'sudo install -d -m 700 -o agent -g agent /home/agent/.ssh && sudo tee -a /home/agent/.ssh/authorized_keys >/dev/null && sudo chown agent:agent /home/agent/.ssh/authorized_keys && sudo chmod 600 /home/agent/.ssh/authorized_keys'
   ```
2. `ssh agent@brightroar`
3. As `agent` — the installer refuses to run under sudo:

   ```bash
   curl -fsSL https://claude.ai/install.sh | bash
   ```
4. Log in: run `claude`. With no browser on the box, press `c` to copy the login URL, open it on
   the Mac, and paste the code back.

## Live checks (you run these — they need sudo)

- `sudo -iu agent cat /home/dan/.secrets` → permission denied
- `sudo -iu agent docker ps` → permission denied
- `sudo -iu agent nvidia-smi -L` → lists the GPU

## Re-run once

`make bootstrap` again → finishes with no errors and no changes.

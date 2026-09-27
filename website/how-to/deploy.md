---
title: "Deploy the stack"
description: "The first deploy on the Spark, the web UI over tailscale serve, and every later change."
---

Everything here runs **on the Spark**, from your clone of this repo, except in *The web UI's first
account*, where the tunnel and the browser are **on the Mac**.

## Before the first deploy

Phase 0 is done, bootstrap and the secret files included, and the engines are installed (the
Phase 1 plan, Task 11). **On the Spark**, check that your session has the groups bootstrap gave
you. This lists `spark-admin` and `adm` among them:

```bash
id -nG
```

If it doesn't, your session predates bootstrap: end tmux with `tmux kill-server`, log out, and log
back in.

`make bootstrap` has run from the clone you deploy from since `stack/host/` last changed: re-run it
whenever that folder changes. It stops a running desktop and restarts earlyoom, so run it over SSH
with nothing open on the desktop.

## First deploy

Root runs its own copies of the units and the Compose project, never the files `make apply` writes.
So the first deploy applies twice, with root's copies installed in between.

**On the Spark**, from the clone, render and stage:

```bash
make apply
```

On the first deploy it stops there. It stages the four units and the Compose project in
`/opt/local-ai/etc`, says root has no copy of them yet, and deploys nothing else.

`make install-units` installs root's copies. What it shows you is every staged file root would
install, and nothing else, but it isn't all that root runs: sudo also runs this clone's own
`Makefile` and `stack/host/bootstrap.sh`, which no diff shows and anything running as you can
change ([the plan](../design/plan.md#users-access-and-security)'s *Users, access and security*).
**On the Spark**, check that both are as committed first. This prints nothing when they are:

```bash
git status --short -- Makefile stack/host
```

Then, **on the Spark**, install root's copies. It runs under sudo, and it asks before it installs
anything:

```bash
make install-units
```

It shows each file it would install as root, in full the first time. Read what it shows before you
answer. Answer `y` (or `yes`), and it installs root's copies in `/etc/systemd/system` and
`/etc/local-ai/compose`, then enables llama-swap, the brake and the web services; any other answer
installs nothing. `make install-units-dry-run` shows the same and changes nothing.

It refuses, installing nothing, a staged file holding a control character, one of ASCII's other
than tab and newline or a C1 control in UTF-8, which could hide a line of what it shows. It refuses
one over 64 KiB, or one that takes over 10 s to read, too. However it ends, even with a Ctrl-C, it
runs `sudo -k`, which forgets sudo's cached credential in this terminal: nothing you run next there,
`make apply` included, can use it, and your next `sudo` asks for your password again.

**On the Spark**, apply again:

```bash
make apply
```

This time it syncs the app into `/opt/local-ai/app` and deploys llama-swap's config and the
registry. Nothing runs yet, so it restarts nothing.

**On the Spark**, download the model files. The `spark` user downloads them:

```bash
make pull
```

It prints nothing until the pull ends, then the pull's last 40 journal lines, whether or not it
failed. `make logs` is a snapshot, and the pull logs one line per file as each finishes, so to
follow it, **on the Spark**, in another pane:

```bash
journalctl -fu local-ai-pull
```

No sudo: bootstrap put you in `adm`, which reads every journal.

**On the Spark**, start the stack, then look at it. No sudo: the polkit rule lets you start, stop
and restart the four units, and nothing more.

```bash
systemctl start local-ai-llama-swap local-ai-brake local-ai-compose
make status
```

## The web UI's first account, straight after the first start

Do this before anything else. First, open a tunnel to the web UI in a spare terminal, and leave it
open; Ctrl-C closes it.

**On the Mac:**

```bash
ssh -N -L 3000:127.0.0.1:3000 brightroar
```

Then, on the Mac, open `http://127.0.0.1:3000` and create your account. The first account becomes
the admin, and signup is otherwise closed.

Until that account exists, whoever reaches the page first becomes the admin: any local user on the
Spark, `agent` included, and every device on the tailnet once the page is served there. An admin
reads every chat and can add Functions, Python that runs inside the container as root, with host
networking. That the first account can sign up with `ENABLE_SIGNUP` false comes from Phase 0's
research and is not yet tried on this box.

If the page refuses even the first signup, put the admin's email and password into
`open-webui.env` at prompts, in the pattern of [Secret files](secret-files.md), and restart the web
services.

- The password isn't shown.
- Both values go in as typed, since `IFS=` keeps a space at either end, and single-quoted, so
  Compose reads a `$` or a ` #` in them literally.
- The command refuses a value with a single quote or a backslash, writes nothing, and prints
  neither value. Compose reads `\'` as an escaped quote, so a password ending in `\` would leave
  the quote open, and Compose's error would print the password into the journal.

**On the Spark:**

```bash
sudo bash -c 'IFS= read -rp "admin email: " e; IFS= read -rsp "admin password: " p; echo; q=$(printf "\047"); case "$e$p" in *"$q"*|*\\*) echo "no single quote or backslash in either, please: the env file quotes each value with single quotes" >&2; exit 1 ;; esac; umask 027; printf "WEBUI_ADMIN_EMAIL=%s%s%s\nWEBUI_ADMIN_PASSWORD=%s%s%s\n" "$q" "$e" "$q" "$q" "$p" "$q" >> /etc/local-ai/secrets/open-webui.env; chgrp spark /etc/local-ai/secrets/open-webui.env'
systemctl restart local-ai-compose
```

Log in through the tunnel, on the Mac, with that email and password. Then, **on the Spark**, remove
both lines, and restart once more so the container no longer holds them:

```bash
sudo bash -c 'umask 027 && f=/etc/local-ai/secrets/open-webui.env && grep -v -e "^WEBUI_ADMIN_EMAIL=" -e "^WEBUI_ADMIN_PASSWORD=" "$f" > "$f.new" && chgrp spark "$f.new" && mv "$f.new" "$f"'
systemctl restart local-ai-compose
```

## The web UI

Only once the admin exists, and keys-only SSH and the Spark's repo-only GitHub token are done
([SSH from the Mac](ssh.md#keys-only) and [The Spark session](spark-session.md)), put the web UI
on the tailnet. **On the Spark:**

```bash
sudo tailscale serve --bg --https=443 http://127.0.0.1:3000
```

It survives reboots. `tailscale serve status` shows the address; it names your tailnet, so read it
privately. Log in with the account you made. To undo it, `sudo tailscale serve reset`; to serve it
again after that, run the command above again.

## Every later change

**On the Spark**, from the clone, get the change:

```bash
git pull
```

If it changed anything under `stack/host/`, run `make bootstrap` first. Then, **on the Spark**,
see what would change:

```bash
make apply-dry-run
```

If the real run would refuse, because llama-swap would restart while models are loaded, or while
apply can't tell whether they are, the dry run says so:
`apply: dry run — would refuse: …; with --now it would restart …`. It exits 1 and ends in make's own
error line, `make: *** … Error 1`. That line is the answer, not a fault: run it again when the
models are idle, or `make apply-now`.

Then, **on the Spark**, change it:

```bash
make apply
```

If a unit or the Compose project changed, apply stages it and deploys nothing else. Then, **on the
Spark**, run `make install-units`, read what it shows, and answer. It shows every staged file root
would install, but not the clone's own scripts, which sudo runs too. Then `make apply` again: that
second apply restarts each unit still running its older definition.

If llama-swap would restart, for its config or for its unit, while models are loaded, or while apply
can't tell whether they are, apply changes nothing and says so. Run it again when they're idle, or
`make apply-now` to restart llama-swap anyway.

If a model starts loading after apply's first look, apply deploys the files, puts llama-swap's
restart off, says so and exits 1; the next `make apply` makes the restart once the models are idle.
A restarted llama-swap that doesn't answer within 30 s, or answers with an error such as a wrong
key, gets a line saying so, and apply exits 1. A run cut off part-way is finished by the next
`make apply`, except for a unit whose start time apply can't read: it names that unit, with the
command to restart it.

## When something is wrong

**On the Spark**, look at the state first, then at the logs of whatever looks wrong:

```bash
make status
make logs s=llama-swap
```

`s=` also takes `brake`, `pull`, `compose`, `open-webui` and `searxng`. A refused load appears in
`make status` as a `refused` line with its reason, for an account in `spark-admin`; any other
account is told the brake's state is unknown to it.

While the brake holds new loads, `make status` says so on its `brake` line. Once memory is back,
`make brake-release` lifts the hold; it needs an account in `spark-admin`.

`make pull` ends with the pull's journal, failed or not, and a `FAILED` line's reason says which
cause it is. Only a wrong file name or revision is fixed in `stack/models.yaml`: fix it there, then
`make apply` and `make pull`. For the rest (a missing or gated repo, a bad or refused token, the
network or a server error, a full or unwritable disk), fix the cause, then `make pull` again.

If `make apply` keeps saying a file differs from root's copy, `make install-units-dry-run` shows the
difference, and `make install-units` installs it.

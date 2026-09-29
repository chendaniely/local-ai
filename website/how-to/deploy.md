---
title: "Deploy the stack"
description: "The first deploy on the Spark, the web UI over tailscale serve, and every later change."
---

Everything here runs **on the Spark**, from your clone of this repo, except in two places **on the
Mac**: sending your llama-swap key, in *Before the first deploy*, and the tunnel and the browser, in
*The web UI's first account*. In *The web UI*, you log in from a browser on the Mac or the
phone.

## Before the first deploy

Phase 0 is done, bootstrap and the secret files included, and the engines are installed (the
Phase 1 plan, Task 11).

Your shells on the Spark need your own llama-swap key: `make status` and `make apply` ask
llama-swap what's loaded (`GET /running`) with the key in `SPARK_API_KEY`. It's the same value as on
the Mac, one key per person, not per machine, and [Secret files](secret-files.md) put it only in the
Mac's `~/.secrets`. **On the Mac**, send it to the Spark's `~/.secrets`, without displaying it:

```bash
( . ~/.secrets; printf 'export SPARK_API_KEY=%s\n' "$SPARK_API_KEY" ) | ssh brightroar 'umask 077; cat >> ~/.secrets'
```

**On the Spark**, once, load it in every shell. This puts the line that loads it first in
`~/.bashrc`, above Ubuntu's early return for non-interactive shells:

```bash
grep -q '\.secrets' ~/.bashrc || sed -i '1i [ -f ~/.secrets ] && . ~/.secrets' ~/.bashrc
```

From then on the key is in every shell of yours on the Spark, a Claude Code session's included, so
that session's secrets guard, step 3 of [The Spark session](spark-session.md), must already hold.

**In the vault**, record in its entry note that the Spark's `~/.secrets` holds `SPARK_API_KEY` too,
with the same value as the Mac's: by name only, never the value.

Then end tmux with `tmux kill-server`, log out, and log back in: a new login picks up the key, and
the groups bootstrap gave you, which a session older than bootstrap lacks. **On the Spark**, in the
new session, check both. `id -nG` lists `spark-admin` and `adm` among your groups, and the second
line prints `True`. It prints only `True` or `False`, never the key.

```bash
id -nG
python3 -c "import os; print(bool(os.environ.get('SPARK_API_KEY')))"
```

`make bootstrap` has run from the clone you deploy from since `stack/host/` last changed: re-run it
whenever that folder changes. Its sudo runs this clone's own `Makefile` and
`stack/host/bootstrap.sh`, so first check that `git status --short -- Makefile stack/host` prints
nothing: both are as committed. It stops a running desktop and restarts earlyoom, so run it over SSH
with nothing open on the desktop. Like `make install-units`, it ends with `sudo -k`, so sudo's
cached credential doesn't outlast it.

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

Then, **on the Spark**, as you, install root's copies. It runs sudo itself, so don't type `sudo` in
front, and it asks before it installs anything:

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

It prints nothing until the pull ends, then this run's lines from the pull's journal, whether or not
it failed. `make logs` is a snapshot, and the pull logs one line per file as each finishes, so to
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
research and is not yet tried on this box. *(Tried 2026-09-28, in Phase 1's Task 13: Dan made the
admin account with sign-up closed, and Open WebUI then reported sign-up off.)*

If the page refuses even the first signup, put the admin's email and password into
`open-webui.env` at prompts, in the pattern of [Secret files](secret-files.md), and restart the web
services.

- The password isn't shown.
- Both values go in as typed, since `IFS=` keeps a space at either end, and single-quoted, so
  Compose reads a `$` or a ` #` in them literally.
- The command refuses a value with a single quote or a backslash, writes nothing, and prints
  neither value. Compose reads `\'` as an escaped quote, so a password ending in `\` would leave
  the quote open, and Compose's error would print the password into the journal.

**On the Spark**, run it a line at a time. Its first line asks for input, your sudo password and
then the admin's email and password, and pasted whole, the next line would be read as the answer:

```bash
sudo bash -c 'IFS= read -rp "admin email: " e; IFS= read -rsp "admin password: " p; echo; q=$(printf "\047"); case "$e$p" in *"$q"*|*\\*) echo "no single quote or backslash in either, please: the env file quotes each value with single quotes" >&2; exit 1 ;; esac; umask 027; printf "WEBUI_ADMIN_EMAIL=%s%s%s\nWEBUI_ADMIN_PASSWORD=%s%s%s\n" "$q" "$e" "$q" "$q" "$p" "$q" >> /etc/local-ai/secrets/open-webui.env; chgrp spark /etc/local-ai/secrets/open-webui.env'
systemctl restart local-ai-compose
```

Log in through the tunnel, on the Mac, with that email and password. Then, **on the Spark**, remove
both lines, and restart once more so the container no longer holds them. Run this a line at a time
too: sudo may ask for your password, and pasted whole, the next line would be read as the answer.

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
privately. Then, on the Mac or the phone, open that address and log in with the account you made.
To undo it, on the Spark, `sudo tailscale serve reset`; to serve it again after that, run the
command above again.

## Every later change

**On the Spark**, from the clone, get the change:

```bash
git pull
```

If it changed anything under `stack/host/`, run `make bootstrap` first, once
`git status --short -- Makefile stack/host` prints nothing: bootstrap's sudo runs both. Then, **on
the Spark**, see what would change:

```bash
make apply-dry-run
```

If the real run would refuse, because llama-swap would restart while models are loaded, or while
apply can't tell whether they are, the dry run says so:
`apply: dry run — would refuse: …; with --now it would restart …`. It exits 1 and ends in make's own
error line, `make: *** … Error 1`. That line is the answer, not a fault: run it again when the
models are idle, or `make apply-now`. The one exception is a refusal after
`apply: llama-swap GET /running: HTTP 401`: see *When something is wrong*.

Then, **on the Spark**, change it:

```bash
make apply
```

If a unit or the Compose project changed, apply stages it and deploys nothing else. Then, **on the
Spark**, check again that `git status --short -- Makefile stack/host` prints nothing, run
`make install-units`, read what it shows, and answer. It shows every staged file root would install,
but not the clone's own scripts, which sudo runs too. Then `make apply` again: that second apply
restarts each unit still running its older definition.

If llama-swap would restart, for its config or for its unit, while models are loaded, or while apply
can't tell whether they are, apply changes nothing and says so. Run it again when they're idle, or
`make apply-now` to restart llama-swap anyway. If it couldn't tell because llama-swap answered
`HTTP 401`, neither helps: see *When something is wrong*.

If a model starts loading after apply's first look, apply deploys the files, puts llama-swap's
restart off, says so and exits 1; the next `make apply` makes the restart once the models are idle.
A restarted llama-swap that doesn't answer within 30 s, or answers with an error such as a wrong
key, gets a line saying so, and apply exits 1. So does a restarted brake that isn't still running
3 s later, or that systemd has had to restart meanwhile. A run cut off part-way is finished by the
next `make apply`, except for a unit whose start time apply can't read: it names that unit, with
the command to restart it.

Then, **on the Spark**, check Phase 0's guardrails and the stack in one pass:

```bash
make doctor
```

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

The same line says whether llama-swap took the brake's own key, `LLAMASWAP_KEY_SPARK`, when the
brake started. If that start check failed, the brake can't unload a model, and `make doctor`'s
`stack units` line fails too. `make logs s=brake` shows its `ALERT`, with llama-swap's answer. The
key is in `llama-swap.env`, which [Secret files](secret-files.md) step 3 wrote. Once that's right,
**on the Spark**, restart the brake, and it checks again:

```bash
systemctl restart local-ai-brake
```

A `problem` line in `make status` that ends in `HTTP 401 (no key in $SPARK_API_KEY)` means this
shell has no key: set it up as *Before the first deploy* says, then log in afresh. `HTTP 401`
without that means llama-swap doesn't know the key this shell has. It should be the Mac's, which
[Secret files](secret-files.md) step 9 added to `llama-swap.env` as `LLAMASWAP_KEY_DAN_MAC`. Until
the key works, apply can't tell what's loaded, so it won't restart llama-swap, and `make apply-now`
would restart it only to fail the check that follows, which asks llama-swap with the same key.

Then, **on the Spark**, check Phase 0's guardrails and the stack in one pass. Each `FAIL` says what
to do. Its `root's copies` line fails when something root runs isn't root's own file, and says to
run `make install-units`. It comes after `make status`, not before, because it does more than look:
its end-to-end check loads the embeddings model if it isn't loaded. A load that starts clears the
last refusal record, the `refused` line `make status` shows, and a load that's refused replaces it.

```bash
make doctor
```

`make pull` ends with this run's lines from the pull's journal, failed or not, and a `FAILED` line's
reason says which cause it is. A repo, file name or revision that doesn't exist is fixed in
`stack/models.yaml`, then `make apply` and `make pull`: the pull reads the registry `make apply`
deployed. A gated or private repo needs your Hugging Face account to have access, and that account's
token in `hf.env`, the pull unit's optional secret file ([Secret files](secret-files.md) step 7),
then `make pull`. For the rest (a bad or refused token, the network or a server error, a full or
unwritable disk), fix the cause, then `make pull` again.

If `make apply` keeps saying a file differs from root's copy, `make install-units-dry-run` shows the
difference, and `make install-units` installs it.

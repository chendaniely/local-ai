---
title: "Deploy the stack"
description: "The first deploy on the Spark, moving a Phase 1 box to Phase 2a and back, the web UI over tailscale serve, every later change, an urgent fix during a phase's build, and reading an engine's log."
---

Everything here runs **on the Spark**, from your clone of this repo, except in two places **on the
Mac**: sending your llama-swap key, in *Before the first deploy*, and the tunnel and the browser, in
*The web UI's first account*. In *The web UI*, you log in from a browser on the Mac or the
phone.

A new box takes *First deploy*. A box already running Phase 1's stack, llama-swap on 9100, moves to
Phase 2a's once, by *Moving a Phase 1 box to Phase 2a*, which also holds the way back. *(Added
2026-10-15, Phase 2a's Task 35.)*

## Before the first deploy

Phase 0 is done, bootstrap and the secret files included, and the engines are installed (the
Phase 1 plan, Task 11).

Your shells on the Spark need your own llama-swap key: `make status` and `make apply` ask
llama-swap what's loaded (`GET /running`) with the key in `SPARK_API_KEY`. *(From the cutover, Phase
2a's Task 38, neither does: llama-swap then answers only the internal keys, and both ask the gate,
which needs no key; built in its Tasks 29 and 32, 2026-10-14 and 15. `make doctor` still uses this
key.)* It's the same value as on
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

**Phase 2a's files.** Before the first deploy of Phase 2a's stack, on a new box or on one moving
from Phase 1's, bootstrap has run with Phase 2a's part
([Bootstrap the Spark](bootstrap.md#phase-2as-re-run)), and [Secret files](secret-files.md)'
*Phase 2a's files* are done: the three internal keys, the
client keys' digests and the private key list (its steps 11 to 13), each checked (its steps 16 to
18). The units read them only when they start: *What `make apply` never restarts* says what to
restart when one changes. *(Added 2026-10-15, Phase 2a's Task 35.)*

ntfy's files are optional: the values file, `/etc/local-ai/values.env`, and the three header files,
which [ntfy on the Synology](ntfy.md) §8 installs once ntfy is set up (Dan's decision, 2026-10-14:
ntfy moves with the NAS phase, now Phase 5). Nothing in the first deploy needs them. Without them
the stack deploys and serves: `make status`'s *health* row reads `ntfy not set up (Task 2, Phase
5)`, and `make doctor`'s `ntfy` line reads `WARN` and fails nothing. Once they're installed, §8
restarts the gate and the brake, which read them only when they start.

## First deploy

Root runs its own copies of the units and the Compose project, never the files `make apply` writes.
So the first deploy applies twice, with root's copies installed in between.

**On the Spark**, from the clone, render and stage:

```bash
make apply
```

On the first deploy it stops there. It stages the units, eleven from Phase 2a (Phase 1 had four),
and the Compose project in `/opt/local-ai/etc`, says root has no copy of them yet, and deploys
nothing else.

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
`/etc/local-ai/compose`, then enables llama-swap, the brake and the web services, and from Phase 2a
the front, the gate and their three sockets too; any other answer installs nothing. It starts
nothing. `make install-units-dry-run` shows the same and changes nothing.

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

**On the Spark**, download the model files. The `spark-pull` user downloads them, from Phase 2a
(Phase 1's pull ran as `spark`):

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

**On the Spark**, start the stack, then look at it, the sockets before the services. The polkit rule
lets you start, stop and restart the stack's services without sudo, and nothing more: no socket,
since stopping the front's would free 9100 for any account to take. So sudo starts the three
sockets and asks for your password once, and `sudo -k` drops it before anything else runs, so the
clone's own code never runs with sudo's credential cached. Run it a line at a time: pasted whole,
the next line would be read as the password.

```bash
sudo systemctl start local-ai-front.socket local-ai-gate-status.socket local-ai-gate-control.socket
sudo -k
systemctl start local-ai-llama-swap.service local-ai-gate.service local-ai-front.service local-ai-brake.service local-ai-compose.service
make status
```

The front's socket takes 9100, and the gate loads the always-loaded models one at a time, which
`make status` shows. *(Changed 2026-10-15, Phase 2a's Task 35. Phase 1's stack had no sockets, and
its first start was `systemctl start local-ai-llama-swap local-ai-brake local-ai-compose`, with no
sudo.)*

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
sudo bash -c 'IFS= read -rp "admin email: " e; IFS= read -rsp "admin password: " p; echo; q=$(printf "\047"); case "$e$p" in *"$q"*|*\\*) echo "no single quote or backslash in either, please: the env file quotes each value with single quotes" >&2; exit 1 ;; esac; umask 077; printf "WEBUI_ADMIN_EMAIL=%s%s%s\nWEBUI_ADMIN_PASSWORD=%s%s%s\n" "$q" "$e" "$q" "$q" "$p" "$q" >> /etc/local-ai/secrets/open-webui.env'
systemctl restart local-ai-compose
```

Log in through the tunnel, on the Mac, with that email and password. Then, **on the Spark**, remove
both lines, and restart once more so the container no longer holds them. Run this a line at a time
too: sudo may ask for your password, and pasted whole, the next line would be read as the answer.

```bash
sudo bash -c 'umask 077 && f=/etc/local-ai/secrets/open-webui.env && grep -v -e "^WEBUI_ADMIN_EMAIL=" -e "^WEBUI_ADMIN_PASSWORD=" "$f" > "$f.new" && mv "$f.new" "$f"'
systemctl restart local-ai-compose
```

Both write as root with `umask 077` and no `chgrp`, so the file stays `0600 root:root`, as every
secret file is from Phase 2a's bootstrap re-run ([Secret files](secret-files.md#where-secrets-live)).
*(Changed 2026-10-15, Phase 2a's Task 35: both blocks wrote with `umask 027` and `chgrp spark`,
leaving the file `0640 root:spark`, Phase 1's rule. Harmless in a folder only root reads, and the
next bootstrap resets it, but no longer the rule.)*

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

On the Mac, the web UI can run as an app of its own: open the address in Safari, then **File → Add
to Dock**. The web app keeps a login of its own, apart from Safari's, so log in once inside it.
Chrome's **Install page as app** works too.

## `spark` on your PATH

The Makefile runs `spark` from your clone, through uv. A bare `spark` command in these docs, such
as `spark status` and, from Phase 2a's cutover, `spark load`, `unload`, `pin`, `unpin`,
`make-room`, `logs` and `session`, is the deployed one instead: the copy `make apply` syncs into
`/opt/local-ai/app`, the same code the services run. **On the Spark**, once, after a `make apply`
has synced the app, link it into `~/.local/bin`:

```bash
mkdir -p ~/.local/bin && ln -sf /opt/local-ai/app/.venv/bin/spark ~/.local/bin/spark && command -v spark
```

It prints the link's full path, `/home/<you>/.local/bin/spark`. If it prints nothing,
`~/.local/bin` didn't exist when this login began, so Ubuntu's `~/.profile` didn't put it on your
`PATH`: log in again. The link points at the deployed app's own entry point, so each later
`make apply` changes what it runs, and it never needs making again. None of these commands needs a
key: the gate's control socket, which only `spark-admin` can use, says who may, and `spark session`
uses its status socket, open to `spark-users`.

The link serves a login's shell only. A command run as `ssh brightroar <command>` gets a shell that
never reads `~/.profile`, so it doesn't find `~/.local/bin`: there, call
`/opt/local-ai/app/.venv/bin/spark` by its full path.

## Moving a Phase 1 box to Phase 2a

*(Added 2026-10-15, Phase 2a's Task 35: its Task 38's sequence, word for word. Not yet performed.)*

Once, on a box running Phase 1's stack: llama-swap on 9100, with the client keys. It comes after
*Before the first deploy*'s Phase 2a files (Phase 2a's Task 36) and after the web containers'
`cap_drop`, deployed from `main` (its Task 37); until it, nothing deploys from `phase-2a` (*An
urgent fix during a phase's build*). `local-ai-front.socket` owns 9100 from its start, so
llama-swap has to leave 9100 first.

**1. The way back, before anything moves.** If the cutover can't be fixed in place, the way back is
Phase 1's layout, llama-swap on 9100 with `llama-swap.env`'s client keys and Phase 1's brake key,
both still on the box (Task 36). Check that `main` renders it. **On the Spark:**

```bash
d=$(mktemp -d) && git worktree add "$d/main" main && (cd "$d/main" && uv run --frozen --quiet --project spark spark render --out "$d/out") && grep -h -e '-listen' -e 'EnvironmentFile' "$d/out/systemd/local-ai-llama-swap.service"; git worktree remove --force "$d/main"
```

Expected: `render: 8 files → …`, then `EnvironmentFile=/etc/local-ai/secrets/llama-swap.env` and
an `ExecStart` ending `-listen 127.0.0.1:9100`.

The rollback itself, only if it comes to that. **On the Spark** (Dan; sudo asks twice: at `make
install-units`, and again at the `disable` line, since `make install-units` ends with `sudo -k`).
Run it a line at a time: pasted whole, the next line would be read as sudo's password, or as `make
install-units`' answer. If `git worktree add` fails, a `../local-ai-rollback` left from an earlier
try among the causes, stop there: the `make` lines must run in that worktree, never in the
`phase-2a` clone. `main`'s units are installed before 2a's front lets go of 9100, so run the
`disable` line and `make apply-now` straight after each other: from the `disable` line until
llama-swap takes 9100 again, nothing holds the port, and any account could take it. That is
seconds, the time `make apply-now` takes to deploy and restart llama-swap, if nothing pauses in
between.

```bash
git worktree add ../local-ai-rollback main
cd ../local-ai-rollback
make apply
make install-units
sudo systemctl disable --now local-ai-front.service local-ai-front.socket local-ai-gate.service local-ai-gate-status.socket local-ai-gate-control.socket
sudo -k
make apply-now
```

Then, at once, **on the Spark**, start llama-swap and the brake. `main`'s `make apply-now` restarts
only what runs, and starts nothing. After a rollback from Step 4, which stopped both, it leaves them
stopped, and 9100 free since that stop. After a rollback from later, both run already, and this
does nothing:

```bash
systemctl start local-ai-llama-swap.service local-ai-brake.service
```

*(Changed 2026-10-15, at Phase 2a's Task 35's review, the controller's ruling, in Task 38's block
too. The block disabled 2a's front, gate and sockets first, then made the worktree and ran `main`'s
`make apply` and `make install-units`, so 9100 stayed free for minutes, through the review `make
install-units` asks for. And it had no start line, so after a failed Step 4 it left llama-swap and
the brake stopped, and 9100 free, while this said it restarts Phase 1's llama-swap.)*

Then `make doctor` from that worktree passes every one of Phase 1's fifteen checks but *secrets
folder*, which reports `root:root 700`, as 2a's bootstrap left it (Task 36), and is expected; and
*earlyoom*, since Phase 2a's bootstrap runs earlyoom without `--prefer` and `main`'s
`earlyoom.default` still has it (Task 28). None of the rollback's steps touches earlyoom. *(Corrected
2026-10-15, at Task 35's review: it passes them only once llama-swap and the brake run. Without the
start line, after a failed Step 4, its units, llama-swap and end-to-end checks failed too.)* Phase
1's pull, which runs as `spark`, can't write the model cache either, since 2a's bootstrap gave it to
`spark-pull` and leaves `spark` reading it through its group (not tried): no `make pull` from that
worktree.

The rollback puts back Phase 1's units, config and app, and nothing else: 2a's bootstrap and secret
files stay. Left in place are the accounts `spark-front` and `spark-pull`; the PAM hook that gives
`agent` its OOM score, its drop-in for `agent`'s user manager, and `agent`'s line in
`/etc/cron.deny`; earlyoom without `--prefer`; the polkit rule, which names 2a's seven services; 2a's
units, disabled, in `/etc/systemd/system`; the internal keys, the client keys' digests and the key
list; the root-only secrets folder; and the model cache, `spark-pull`'s. *(Added 2026-10-15, at
Task 35's review.)*

**2. Stage root's copies.** **On the Spark:**

```bash
make apply
```

Expected: it lists root's ten new or changed unit files (the compose unit is unchanged), stages
them, and stops, before it asks the gate anything (Task 32), since no gate runs yet.

**3. Install them** (Dan). **On the Spark**, check that `git status --short -- Makefile
stack/host` prints nothing, as *First deploy* says, then:

```bash
make install-units
```

Read every file it shows: they are what root will run. It installs root's copies and enables the
sockets and the services, and starts nothing new. Step 4 follows at once, with no reboot between: a
boot now would start the front and the gate against Phase 1's app, the front's socket and Phase 1's
llama-swap both wanting 9100.

**4. The cutover, its deploy** (Dan). **On the Spark:**

```bash
systemctl stop local-ai-brake.service local-ai-llama-swap.service
make apply
```

Stopping llama-swap stops every model and frees 9100; `make apply` then deploys the config, the
registry and the app with nothing to wait for (Task 32). Expected: it deploys, and exits 0.
Anything else — render or validation refusing, `uv sync` failing, the network dropping — leaves
Phase 1's app deployed under the new units root now holds: stop here, and run Step 1's rollback,
which restarts Phase 1's llama-swap. *(Corrected 2026-10-15, at Phase 2a's Task 35's review: it
starts llama-swap and the brake only with its start line, added that day, since `main`'s `make
apply-now` starts nothing. Run it at once: 9100 is free from this step's stop.)*

Nothing starts llama-swap again before Step 5. The front's unit starts a stopped llama-swap
whenever the front starts (its `Wants=`), but neither the front nor its socket runs yet. From the
cutover on, a step that keeps llama-swap stopped holds it down, as *Keeping llama-swap stopped*
says. *(Corrected 2026-10-15, at Phase 2a's Task 35, the controller's ruling: this said such a step
stops the front's socket too, which would free 9100 for any account to take.)*

**5. The cutover, its start**, only after Step 4 exited 0. **On the Spark** (Dan; sudo asks once;
a line at a time, since pasted whole, the next line would be read as the password):

```bash
sudo systemctl start local-ai-front.socket local-ai-gate-status.socket local-ai-gate-control.socket
sudo -k
systemctl start local-ai-llama-swap.service local-ai-gate.service local-ai-front.service local-ai-brake.service
```

The front's socket takes 9100, and the gate loads the residents one at a time. The polkit rule lets
you stop and start the services without sudo; only the sockets need it, and `sudo -k` drops it
before anything else runs, so the clone's own code never runs with sudo's credential cached
(*Lessons from Phase 0*). 9100 is free from Step 4's stop until the socket starts, the cutover's
one window.

**6. Check it.** `make status` reads as the plan's layout, its *health* row with `ntfy not set up
(Task 2, Phase 5)`. `make logs s=gate` shows the residents loading one after another, and its one
line that ntfy isn't set up. Once `make status` shows the always-loaded models loaded, `make doctor`
passes every line but `ntfy`, which reads `WARN  ntfy: not set up (Task 2, Phase 5)` and fails
nothing, until ntfy is set up in Phase 5. *(Corrected 2026-10-15, at Phase 2a's Task 35's review:
doctor came first here. Run straight after Step 5, its end-to-end line fails, the embeddings model
still loading, since doctor loads nothing and asks a model only once the gate shows it loaded.)*

The three internal keys: a call through the front with a client key is answered, not a 401 (pi's
and the web UI's answers below are such calls); the gate's own key reaches `/running`, so `make
status` shows the residents' states and `make logs s=gate` holds no 401 from llama-swap; and the
brake's start check passed, which `make status`'s *health* row shows as `brake ok (key checked …)`
and `make doctor`'s *stack units* line as `llama-swap took the brake's key at …`.

Then the listening ports, numbers only. **On the Spark:**

```bash
ss -Hltn | awk '{print $4}' | sed 's/.*://' | sort -n | uniq | tr '\n' ' '; echo
```

Expected among them: 800, 801, 803, 900 and 9100, and no 5800: llama-swap gives ports by the
models' sorted names, so Gemma has 800, the embeddings 801 and whisper 803, and 802 is the coder's
once it loads. `make logs s=front` holds no key, header or body. A request for a made-up model,
with your key on curl's stdin. **On the Spark:**

```bash
curl -s -H @- -H 'Content-Type: application/json' -d '{"model":"no-such-model","messages":[{"role":"user","content":"hi"}]}' http://127.0.0.1:9100/v1/chat/completions <<<"Authorization: Bearer $SPARK_API_KEY"; echo
```

Expected: a 404 body with `model_not_found`'s words, listing the models. **On the Mac**, through
`make tunnel`, pi answers. On the phone, the web UI chats, searches and transcribes. Then link bare
`spark` into your `PATH`, as *`spark` on your PATH* says. [Phase 2a's
plan](../design/phase-2a.md), Task 38, Steps 6 and 7, lists the rest: the units' accounts, sockets,
IP lists and sandboxes, and what `agent` can't do.

If any check fails, stop: whether it is fixed in place or the rollback (Step 1) runs is Dan's call.

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

*From the cutover (Phase 2a's Task 38; built 2026-10-15, in its Task 32):* when llama-swap or the
front would restart, the dry run asks the gate what's in flight and says what the real run would
wait for: `apply: dry run — would wait until nothing has been in flight for 60 s (for up to 15
minutes, then offer to drain now), then restart …`. It changes nothing, and asks the gate nothing
else. If the gate isn't answering, the real run would refuse, and the dry run says so:
`apply: dry run — would refuse: apply can't see what's in flight while the gate isn't answering;
with --now it would restart …`. It exits 1 and ends in make's own error line,
`make: *** … Error 1`. That line is the answer, not a fault: `make doctor` shows why the gate
isn't answering.

*Until the cutover, Phase 1's apply:* if the real run would refuse, because llama-swap would restart
while models are loaded, or while apply can't tell whether they are, the dry run says so:
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

*From the cutover (Phase 2a's Task 38; built 2026-10-15, in its Task 32):* a restart of llama-swap
stops every model, and one of the front cuts off every request it holds. So when either has to
restart, apply first waits for a quiet moment: no request in flight for 60 s, by the front's
counts. Requests are served as usual meanwhile. Each time what it waits on changes, apply says so:

> Waiting for a quiet moment: the coder answered 20 s ago, and it needs 60 s with nothing in
> flight. Ctrl-C leaves everything as it was; `make apply-now` restarts now.

Ctrl-C changes nothing while it waits, and neither does a dropped SSH session, but a long wait is
best run in tmux. After 15 minutes without a quiet minute, apply asks:

> No quiet minute in 15 minutes. Drain now, holding new requests while the 2 in flight finish?
> [y/N]

`y` holds new requests and waits, however long, for the requests in flight to finish, naming whose
they are. If the front stops reporting its counts to the gate, both waits say so, with `make logs
s=front`. Anything else changes nothing, and `make apply` waits again when it's run again.

From the quiet moment, or from that `y`, apply syncs the app, then the front holds every new
request until the restarts are done, and apply waits until nothing is in flight. *(Corrected
2026-10-15, the controller's ruling, from Phase 2a's Task 32 fix of that day: the sync now comes
before the hold, below.)* *(Corrected 2026-10-15, at Phase 2a's Task 32 review: the front holds
only once the gate's hold reaches it, and not at all while its call to the gate is down.)* apply goes on only when the front itself reports that it holds new requests, its
call to the gate open, with nothing in flight, in counts made since the hold began. It reads them
again just before restarting the front or llama-swap. Until then it waits and says what for. If it
got there by a quiet minute, it asks at the 15-minute deadline whether to keep holding new requests,
once the front has had a few seconds to report. After your `y` to drain now, it doesn't ask again.
*(Corrected 2026-10-15, at Phase 2a's Task 32 re-review: this said it asks as the quiet wait does,
which isn't so after a `y`.)* A request held past its key's wait gets
`restarting`'s words, never `llama_swap_down`'s. Only then does apply write the files; the app's
sync came just before the hold. *(Corrected 2026-10-15, the controller's ruling, from Phase 2a's
Task 32 fix of that day: this said "Only then does apply sync the app and write the files".)* Then
it restarts what the change needs, in order. Just before each restart of the gate, the
front or llama-swap, apply checks with the gate that it still holds new requests. If the hold has
ended, or the gate doesn't confirm it, apply restarts nothing more, says what to run, and exits 1.
The brake comes first, then the gate, after which apply waits up to 30 s for the gate to answer
again. The front follows, only if its own files changed, its key groups changed, or for `make
restart-front`. llama-swap goes last, after which apply waits up to 30 s for the gate to see it
answer again. The gate then releases the held requests and reloads the always-loaded models one at a
time, and apply ends `apply: done: …`. The compose unit, when it has to restart, restarts after
that, on its own.

~~So the app's sync runs under the hold: every new request waits at the front while it runs. It
usually takes seconds, and as long as its downloads when the lock changed. *(Added 2026-10-15, at
Phase 2a's Task 35, seen with uv 0.12.18 on a copy of the deployed app.)* uv also waits, saying
nothing, for two locks of its own: the deployed venv's, `/opt/local-ai/app/.venv/.lock`, and the
one it keeps in `/tmp` for the project, `uv-<hash>.lock`. uv makes both `0666`, and holding a lock
needs only read access, so any process on the box, `agent`'s included, can make the sync wait, up
to five minutes for each lock, after which uv syncs without it. So if apply's last line has said
that new requests are held until the restarts are done for over a minute, with nothing from uv
after it, press Ctrl-C: apply ends the hold at once, and the next `make apply` syncs the app
again.~~ A change that restarts neither llama-swap nor the front syncs with
no hold, and a wait there only delays apply.

*(Corrected 2026-10-15, the controller's ruling: the app's sync now runs before apply holds any
request: after the quiet minute, after your `y` to drain now, or after `make apply-now`'s question.
uv still waits, silently, if another process holds one of its two locks. But apply stops it after 3
minutes and says `apply: the app's sync waited on a lock: <path>, …`, then exits 1, with nothing
restarted and no request held. Once that process lets go, `make apply` syncs it again.)*

apply says `apply: syncing the app (uv sync, up to 3 minutes)` as the sync starts. It stops any sync
that hasn't finished within 3 minutes, a slow download's included, not only one waiting on a lock:
that one says `apply: the app's sync didn't finish within 3 minutes, …`. Either way the app's
environment may be part-way synced, and the next `make apply` syncs it again. Any
account can hold the venv's lock, so a sync can be refused this way again and again. To find what
holds the lock apply named, without sudo, **on the Spark**, set `p` to the path it named. The block
lists each process that holds or waits for that lock, by its account, pid and command, from
`/proc/locks`, where every lock on the box shows with its holder's pid and its file's inode:

```bash
p=/opt/local-ai/app/.venv/.lock
i=$(stat -c %i "$p") && awk -v i="$i" '{for (f = 2; f <= NF; f++) if ($f ~ ":" i "$") print $(f - 1)}' /proc/locks | sort -u | xargs -r ps -o user,pid,cmd -p
```

*(Added 2026-10-15, at Phase 2a's Task 35's review. The block ran against a scratch lock held by
`flock`, listing the holder and a waiter.)*

`make apply-now` skips the wait. If the gate lists requests in flight, it asks first, naming whose
they are:

> This restarts the model service now and cuts off the 2 requests in flight (pi on the Mac, agent).
> Continue? [y/N]

It holds new requests through the restarts all the same. A change that restarts neither llama-swap
nor the front applies at once, with no wait. `make restart-front` waits the same way as
`make apply`. Its words don't offer `make apply-now`, which wouldn't restart the front for the key
list.

If the gate isn't answering, apply can't see what's in flight. It refuses a restart of llama-swap or
the front, changes nothing, and says that `make apply-now` restarts anyway. `make apply-now` then
restarts without holding new requests, and without asking: it can't see what's in flight, so any
request in flight is cut off. When it restarts llama-swap it can't confirm that llama-swap
answers again, so it says so and exits 1. With llama-swap stopped, as at the cutover, apply deploys
without the gate. A gate that doesn't answer within 30 s of its own restart stops apply there: exit
1, the gate named, nothing more restarted. apply still asks the gate to end the hold on new
requests; if the gate doesn't answer that either, the hold ends by itself a minute after its last
renewal. After any other failure once requests are held, Ctrl-C or a dropped SSH session included,
apply ends the hold at once, then says what it left. The next `make apply` restarts each unit
still running older files.

*Until the cutover, Phase 1's apply:* if llama-swap would restart, for its config or for its unit,
while models are loaded, or while apply can't tell whether they are, apply changes nothing and says
so. Run it again when they're idle, or `make apply-now` to restart llama-swap anyway. If it couldn't
tell because llama-swap answered `HTTP 401`, neither helps: see *When something is wrong*. If a model
starts loading after apply's first look, apply deploys the files, puts llama-swap's restart off,
says so and exits 1; the next `make apply` makes the restart once the models are idle. A restarted
llama-swap that doesn't answer within 30 s, or answers with an error such as a wrong key, gets a
line saying so, and apply exits 1.

Either way, a restarted brake that isn't still running 3 s later, or that systemd has had to restart
meanwhile, gets a line saying so, and apply exits 1. A run cut off part-way is finished by the next
`make apply`, except for a unit whose start time apply can't read: it names that unit, with the
command to restart it.

Then, **on the Spark**, check Phase 0's guardrails and the stack in one pass:

```bash
make doctor
```

### What `make apply` never restarts

*(Added 2026-10-15, Phase 2a's Task 35, from its Task 31's review.)* apply restarts a service for
the files it deploys itself, and for root's copy of its unit, once `make install-units` has
installed a newer one. Two kinds of change it never restarts anything for.

**A socket unit:** `local-ai-front.socket`, `local-ai-gate-status.socket` or
`local-ai-gate-control.socket`. `make install-units` installs a changed one, and says so, but apply
restarts no socket, so it keeps its old definition until it starts again: at the next boot, or now,
with sudo, since the polkit rule leaves sockets out. The service it serves stops first and starts
after it, so it takes the new socket. For the front's, run it when `make status` shows nothing in
flight: the front's stop cuts off every request it holds, and 9100 is free until the socket starts
again. **On the Spark** (sudo asks once; a line at a time, since pasted whole, the next line would
be read as the password):

```bash
sudo systemctl stop local-ai-front.service local-ai-front.socket
sudo systemctl start local-ai-front.socket
sudo -k
systemctl start local-ai-front.service
```

For either of the gate's two. While the gate is down, models already loaded still answer, and a
request that needs a load gets `gate_down`, which pi retries. **On the Spark** (sudo asks once, a
line at a time, as above):

```bash
sudo systemctl stop local-ai-gate.service local-ai-gate-status.socket local-ai-gate-control.socket
sudo systemctl start local-ai-gate-status.socket local-ai-gate-control.socket
sudo -k
systemctl start local-ai-gate.service
```

**A secret, or a file, read only at start,** which apply never sees. **On the Spark**, after a
change to:

| What changed | Read by | Then, on the Spark |
|---|---|---|
| `internal-keys.env` | llama-swap (`EnvironmentFile=`) | `systemctl restart local-ai-llama-swap.service`. It stops every model and cuts off every request in flight, so check `make status` first; the gate then reloads the always-loaded models. A changed internal key changes its caller's `.key` file too: restart that caller after llama-swap, as the rows below say |
| `front.key` | the front (`LoadCredential=`) | `make restart-front`, which restarts the gate and then the front, once nothing has been in flight for a minute |
| `gate.key` | the gate (`LoadCredential=`) | `systemctl restart local-ai-gate.service` |
| `brake.key` | the brake (`LoadCredential=`) | `systemctl restart local-ai-brake.service` |
| `client-keys.sha256`, `/etc/local-ai/keys.yaml` | the front, and the key list the gate too (`LoadCredential=`) | `make restart-front` ([Secret files](secret-files.md)' *A new client key, after 2a*) |
| ntfy's three header files, `/etc/local-ai/values.env` | the gate and the brake (`LoadCredential=`, `EnvironmentFile=`) | `systemctl restart local-ai-gate.service local-ai-brake.service`, as [ntfy](ntfy.md) §8 says. The failure notifier reads its own at each run, so it needs nothing |

The polkit rule lets you restart each of these services without sudo.

### Keeping llama-swap stopped

*(Added 2026-10-15, Phase 2a's Task 35, the controller's ruling.)* From the cutover,
`systemctl stop local-ai-llama-swap` alone doesn't keep it stopped. The front's unit has
`Wants=local-ai-llama-swap.service`, so any start of the front starts a stopped llama-swap too:
the front's restart after a crash, or a connection to 9100 once its service alone is stopped, since
PID 1 still holds the port. Never stop the front's socket to prevent it: that frees 9100, and any
account, `agent` included, could take it and receive your clients' requests and their keys.

Hold llama-swap down instead, with a drop-in in `/run` whose condition is never met:
`ConditionPathExists=/dev/null/held-down`, a path that can't exist, since nothing can be made under
`/dev/null`. A condition in a drop-in joins the unit's own, and one that isn't met skips every
start of the unit, the front's `Wants=` included, without failing anything (systemd.unit(5)). So
the front still starts and keeps 9100, and a request waits its key's wait, then gets
`llama_swap_down`. A drop-in applies wherever the unit's file is (systemd.unit(5)). `systemctl
mask --runtime` wouldn't do: it writes to `/run/systemd/system`, and root's copy of the unit, in
`/etc/systemd/system`, comes before it.

The stop stops every model and cuts off every request in flight, so look at `make status` first,
and hold it down when nothing you need is running. **On the Spark**, hold it down, a line at a
time, since pasted whole, the next line would be read as sudo's password:

```bash
printf '[Unit]\nConditionPathExists=/dev/null/held-down\n' | sudo install -D -m 0644 /dev/stdin /run/systemd/system/local-ai-llama-swap.service.d/held-down.conf
sudo systemctl daemon-reload
sudo systemctl stop local-ai-llama-swap
sudo -k
```

**On the Spark**, prove that it holds: look at the unit with its drop-in, try to start it, and look
again. No sudo: the polkit rule lets you start it, and while the hold-down stands the start is
skipped.

```bash
systemctl cat local-ai-llama-swap
systemctl start local-ai-llama-swap
systemctl show -p DropInPaths,ActiveState,ConditionResult,ConditionTimestamp local-ai-llama-swap
```

Expected: `systemctl cat` ends with `# /run/systemd/system/local-ai-llama-swap.service.d/held-down.conf`
and the drop-in's two lines. `systemctl start` exits 0 and starts nothing. `systemctl show` names
`held-down.conf` in `DropInPaths`, and reads `ActiveState=inactive` and `ConditionResult=no`, with
the try's time in `ConditionTimestamp`. `make logs s=llama-swap` holds systemd's line that it `was
skipped because of an unmet condition check (ConditionPathExists=/dev/null/held-down)`. (From
systemd 255's man pages and source; not yet seen on this box.) If `ActiveState` reads `active` or
`activating`, the hold-down didn't take and llama-swap is starting. **On the Spark**, stop it at
once, and go no further:

```bash
systemctl stop local-ai-llama-swap
```

*(Changed 2026-10-15, at Phase 2a's Task 35's review: the check only looked, and before any try
`ConditionResult` reads `yes`, from the last real start, so it couldn't show that a start is
skipped. It tries one now. The note to look at `make status` first was added the same day.)*

**On the Spark**, when you're done, a line at a time:

```bash
sudo rm /run/systemd/system/local-ai-llama-swap.service.d/held-down.conf
sudo systemctl daemon-reload
sudo systemctl start local-ai-llama-swap
sudo -k
```

A reboot ends the hold-down too: `/run` is a tmpfs, so the drop-in is gone, and the units start as
at any boot. Until the cutover no front runs, so `systemctl stop local-ai-llama-swap` keeps it
stopped until you start it again or the box reboots.

## An urgent fix during a phase's build

(Added 2026-10-15, Phase 2a's Task 35, from Dan's decision of 2026-10-07: *Deploys during 2a* in
[Phase 2a's plan](../design/phase-2a.md).) While a phase is built on its own branch, nothing
deploys from that branch before its cutover. From `phase-2a`, nothing runs `make apply`, `make
apply-now` or `make install-units` before its Task 38: its brake would restart onto llama-swap's
new port, where nothing listens yet, and its `spark launch` would start no model without a ticket.
An urgent fix is made on `main` instead, deployed from a `main` worktree, pushed with Dan's OK, and
merged into the phase's branch. Phase 2a's Task 37, the web containers' `cap_drop`, goes this way.
This holds only until the cutover: from then on the box runs the phase's stack, and `main`'s
`make apply` would deploy the older one over it, as the rollback in *Moving a Phase 1 box to Phase
2a* does on purpose.

**On the Spark**, from the phase's clone, make the worktree:

```bash
git worktree add ../local-ai-main main && git -C ../local-ai-main pull --ff-only
```

In `../local-ai-main`, make the fix and its tests, and run `make test lint`. Deploy it from there,
in tmux, as *Every later change* says: `make apply`; if it stages a unit or the Compose project,
`make install-units`, then `make apply` again. `make doctor` there is `main`'s. Since Phase 2a's
bootstrap re-run (its Task 36), two of its lines fail, as expected: *secrets folder*, which reports
`root:root 700`, and *earlyoom*, since `main`'s `earlyoom.default` still has `--prefer` until 2a
merges. And no `make pull` from there: since that re-run, `main`'s pull, which runs as `spark`,
can't write the model cache, which is `spark-pull`'s (not tried; added 2026-10-15, at Task 35's
review). Then commit the fix there, on `main`, staging its files by path; run
[the scan before every push](leak-guards.md#before-every-push) with `main` as `<branch>`; and, with
Dan's OK, `git push origin main`.

**On the Spark**, back in the phase's clone, see first whether `main` brings in a change to the
workflows:

```bash
git diff --stat HEAD...main -- .github/workflows
```

If it lists anything, ~~the merge is made on the Mac, not here.~~ the Spark can't push the merge,
since its GitHub token can't push a branch that brings in a `.github/workflows/` change
(`CLAUDE.md`, *Building it*). ~~`main` is pushed already: remove the worktree, still **on the
Spark**, with `git worktree remove ../local-ai-main`, and make the merge on the Mac, which takes
the phase's branch as at any switch between the machines.~~ *(Corrected 2026-10-15, Dan's
decision, the route of plan.md's *Closing 2a*:)* make the merge here anyway, **on the Spark**, as
below: resolve every conflict, run the tests, and commit it **without pushing**. Then, **on the
Mac**, fetch the merge commit from the Spark over SSH, run the scan before every push, and push the
phase's branch from there. *(Added 2026-10-15, at Phase 2a's Task 35's review:
`origin/main` has changed `publish-website.yml` since 2a branched, so this holds for Task 37's
merge.)* If it lists nothing, **on the Spark**, merge it in, and remove the worktree:

```bash
git merge --no-ff main -m "chore(repo): merge main's urgent fix into phase-2a"
git worktree remove ../local-ai-main
```

A Claude session's merge message carries the 🤖 and the trailer its phase plan gives, as Task 37's
does. If the merge stops on a conflict, resolve each file so that what each side changed stays,
stage them by name, finish it with `git commit --no-edit`, and run `make test lint`. Phase 2a's
Task 37 shows how, for the four files its own merge was expected to conflict in.

## When something is wrong

**On the Spark**, look at the state first, then at the logs of whatever looks wrong:

```bash
make status
make logs s=llama-swap
```

`s=` also takes `brake`, `pull`, `compose`, `open-webui` and `searxng`, and `front`, `gate` and
`notify`, the failure notifier's runs, whose units arrive with Phase 2a's cutover. *Phase 1's `make
status`:* a refused load appears as a `refused` line with its reason, for an account in
`spark-admin`; any other account is told the brake's state is unknown to it.

*From the cutover (Phase 2a's Task 29),* `make status` asks the gate. A refusal, of a load or of a
request, appears under *recent* with its reason, the newest ten (`spark status --json` lists every
one the gate sends), and a load that starts no longer clears it. An account outside `spark-admin`
sees its own refusals and the system's events.

*Phase 1's, until the cutover:* `make status` shows only the last refusal, and the next load that
starts clears it. llama-swap's journal has only its own line for one, `starting <model> failed:
upstream command exited prematurely`: the engines' output, `spark launch`'s `spark: not starting
<model>: <reason>` line and an engine's crash included, stays in llama-swap's in-memory buffer,
which every restart of llama-swap wipes (Dan's decision, 2026-09-28: whisper-server logs each
upload's file name and its metadata, which the journal would keep). To read the refusals the buffer
still holds, **on the Spark**:

```bash
curl -s -m 4 -H @- http://127.0.0.1:9100/logs/stream/upstream <<<"Authorization: Bearer $SPARK_API_KEY" | grep -a 'not starting'
```

*From the cutover (Phase 2a's Tasks 20 and 30),* the front refuses that `curl` with
`route_not_served`, as it refuses every route outside the model API, and the refusals reach `make
status` from the gate. An engine's own lines, its crash and `spark launch`'s `spark: not starting
<model>: <reason>` included, still stay only in llama-swap's buffer, which every restart of
llama-swap wipes. `spark logs` reads a model's lines through the gate, for an account in
`spark-admin`, with no key: its last 20, or as many as `-n` asks, up to 1000. It takes the model's
name or one of its roles. **On the Spark**, the coder's, keeping only its refusals:

```bash
spark logs coder -n 1000 | grep 'not starting'
```

Without the `grep`, it prints every line it read. A bare `spark` is the deployed one, linked as
*`spark` on your PATH* says.

While the brake holds new loads, `make status` says so on its `brake` line. Once memory is back,
`make brake-release` lifts the hold; it needs an account in `spark-admin`.

The same line says whether llama-swap took the brake's own key, `LLAMASWAP_KEY_SPARK`, when the
brake started. If that start check failed, nothing shows that the brake can unload a model, and
`make doctor`'s `stack units` line fails too. `make logs s=brake` shows its `ALERT`, with why. If
llama-swap refused the key, the brake can't unload anything. If llama-swap didn't answer within
30 s of the brake's start, the brake can unload once it answers, but its record stays failed. The
key is in `llama-swap.env`, which [Secret files](secret-files.md) step 3 wrote. Once the key is
right, or llama-swap answers, **on the Spark**, restart the brake, and it checks again:

```bash
systemctl restart local-ai-brake
```

*From the cutover (Phase 2a's Tasks 23 and 29),* `make status`'s *health* row says it: `brake ok
(key checked …)` once the check passed, and `brake <state> (make logs s=brake)` otherwise. The
brake's key is then `brake.key`, and llama-swap knows it from `internal-keys.env`, both written by
[Secret files](secret-files.md) step 11; its step 17 compares the two, printing only `match` or
`MISMATCH`. After a fix, restart llama-swap if `internal-keys.env` changed, then the brake, as
*What `make apply` never restarts* says.

A `problem` line in `make status` that ends in `HTTP 401 (no key in $SPARK_API_KEY)` means this
shell has no key: set it up as *Before the first deploy* says, then log in afresh. `HTTP 401`
without that means llama-swap doesn't know the key this shell has. It should be the Mac's, which
[Secret files](secret-files.md) step 9 added to `llama-swap.env` as `LLAMASWAP_KEY_DAN_MAC`. Until
the key works, apply can't tell what's loaded, so it won't restart llama-swap, and `make apply-now`
would restart it only to fail the check that follows, which asks llama-swap with the same key.
*(Phase 1's apply. From the cutover apply asks the gate, never llama-swap, and needs no key: Phase
2a's Task 32, 2026-10-15.)*

*From the cutover,* `make status` and `make apply` send no key. Only `make doctor` sends yours: to
the front, which must take it, and to llama-swap's port, which must refuse it. When doctor says the
front doesn't know the key in `SPARK_API_KEY`, its digest isn't in `client-keys.sha256`, or the
front hasn't restarted since it was added. [Secret files](secret-files.md)' step 17 checks the
digests, its step 18 the key list, and its *A new client key, after 2a* adds one; then `make
restart-front`.

Then, **on the Spark**, check Phase 0's guardrails and the stack in one pass. Each `FAIL` says what
to do. A `WARN` passes and fails nothing: until ntfy is set up, in Phase 5, its `ntfy` line reads
`WARN  ntfy: not set up (Task 2, Phase 5)`. Its `root's copies` line fails when something root
runs isn't root's own file, and says to run `make install-units`. It comes after `make status`,
not before, because its requests leave a trace there: the refusals it asks for on purpose,
`route_not_served` for routes the front doesn't serve and S03's `model_not_found`, go into the
gate's *recent*. It loads no model. It asks a model something only once the gate shows it loaded,
so its request never starts a load, and otherwise its line fails, naming the `spark load` to run.
*(Corrected 2026-10-15, Phase 2a's Task 35: this said its end-to-end check loads the embeddings
model if it isn't loaded, so that a load that starts clears the last refusal record. That is
Phase 1's doctor; from Phase 2a's Task 33, doctor loads nothing.)*

```bash
make doctor
```

`spark doctor --full`, run from the clone's root, adds two heavy checks: a 170 MB upload to
speech-to-text through the front, and the privacy canary, a string sent through chat and
speech-to-text, then looked for where nothing should keep it.

`make pull` ends with this run's lines from the pull's journal, failed or not, and a `FAILED` line's
reason says which cause it is. A repo, file name or revision that doesn't exist is fixed in
`stack/models.yaml`, then `make apply` and `make pull`: the pull reads the registry `make apply`
deployed. A gated or private repo needs your Hugging Face account to have access, and that account's
token in `hf.env`, the pull unit's optional secret file ([Secret files](secret-files.md) step 7),
then `make pull`. For the rest (a bad or refused token, the network or a server error, a full or
unwritable disk), fix the cause, then `make pull` again.

If `make apply` keeps saying a file differs from root's copy, `make install-units-dry-run` shows the
difference, and `make install-units` installs it.

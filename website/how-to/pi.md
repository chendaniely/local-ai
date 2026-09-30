---
title: "pi, the coding agent"
description: "pi on the Mac through an SSH tunnel, and as agent in tmux on the Spark, once agent's Claude Code has the secrets guard."
---

pi reaches the Spark's models as one provider, `spark`: each chat model in the registry, under its
real name. The provider names the variable that holds your llama-swap key, never the key itself.
You set pi up in two places: on the Mac, where it goes through an SSH tunnel, and on the Spark,
where it runs as `agent`, inside tmux.

## The version

`agent`'s pi, on the Spark, is pinned to 0.85.1. Releases 0.86.0 through 0.87.1 are reported to
crash llama-server, most likely through a llama.cpp bug that their longer prompt triggers. Move it
up only to a release outside that range, and change `stack/versions.yaml` in the same commit. An
update takes pi off the pin: on 2026-09-28 one took it to 0.87.1, the newest release then and the
last in the range. So after any update, check `pi --version`, and put it back with the install line
under *On the Spark, as `agent`*.

The Mac's pi isn't pinned (Dan's decision, 2026-09-28): it comes from Homebrew, which moves it with
each upgrade, and holding a Homebrew install at one version is more trouble than the risk. That day
it was 0.87.1, inside the range, and its requests crashed no engine.

## On the Mac

**On the Mac**, pi comes from Homebrew, at whatever version Homebrew has; check it:

```bash
pi --version
```

*(Changed 2026-09-28, Dan's decision: this section installed pi at the pin with
`npm install -g --ignore-scripts @earendil-works/pi-coding-agent@0.85.1`, as Phase 1's Task 15 did,
until Homebrew's pi, 0.87.1, took over the `pi` command. If an engine ever crashes on a request from
the Mac's pi, that npm line is the way back to the pin, with Homebrew's pi removed first so the two
don't both claim `pi`.)*

pi reads your key from `SPARK_API_KEY`, so your shell must export it:
[Secret files](secret-files.md) put it in `~/.secrets`. **On the Mac**, check it without showing it.
This asks what a program you start would see, so it prints `True` only when the variable is
exported, not merely set:

```bash
python3 -c "import os; print(bool(os.environ.get('SPARK_API_KEY')))"
```

**On the Mac**, from your clone of this repo, add the `spark` provider to pi:

```bash
make clients
```

It writes the provider into `~/.pi/agent/models.json` and leaves every other provider there as it
was. The previous file, if there was one, is kept as `models.json.bak`, and the line `make clients`
prints says which.

**On the Mac**, in a spare terminal, from your clone, open the tunnel and leave it open. Ctrl-C
closes it.

```bash
make tunnel
```

None of the stack's ports listens beyond 127.0.0.1 in Phase 1, so pi reaches llama-swap only
through the tunnel.

In pi, `/model` → a Spark model. The footer names the model that answers.

## On the Spark, as `agent`

`agent` needs Node 22.19 or later, its own llama-swap key, uv, pi and a clone of this repo. Two
people set that up: you, with sudo, then `agent` itself. First, before `agent` holds a key, its
Claude Code gets the secrets guard.

### The secrets guard, before the key

*(Added 2026-09-28, from Phase 1's council: Task 15 set this up that day from commands that
reached no runbook.)* `agent`'s Claude Code, which [Bootstrap the Spark](bootstrap.md) installs and
logs in, gets the same guard as yours ([The Spark session](spark-session.md), step 3), with
`agent`'s paths, and a `~/.claude/CLAUDE.md` of its own that holds only your secrets rule. It comes
before the key, so no session of `agent`'s runs with the key and without the guard. The commands
are short lines: on 2026-09-28, a long `jq … | ssh` line lost its end when it was pasted.

**On the Mac**, copy the guard's script to `agent`:

```bash
ssh brightroar-agent 'mkdir -p ~/.claude/hooks'
scp ~/.claude/hooks/block-secret-access.sh brightroar-agent:.claude/hooks/
```

**On the Mac**, write `agent`'s guard file and its `CLAUDE.md` into a fresh private folder, copy
both into `agent`'s `~/.claude`, and remove the folder. The jq filter keeps only the guard's hook
and the deny rules, with `/Users/dan/` in a rule turned into `/home/agent/`; the Mac's other
settings stay behind. The awk keeps only the section of your `~/.claude/CLAUDE.md` whose heading
names secrets. The copy replaces any `CLAUDE.md` that `agent` has.

```bash
d=$(mktemp -d)
jq '{permissions: {deny: [.permissions.deny[]
      | gsub("//Users/dan/"; "//home/agent/")]},
    hooks: {PreToolUse: [.hooks.PreToolUse[]
      | select(any(.hooks[]; (.command // "")
        | test("block-secret-access")))]}}' \
  ~/.claude/settings.json > "$d/agent-guard.json" &&
awk '/^# /{keep = /secrets/} keep' \
  ~/.claude/CLAUDE.md > "$d/CLAUDE.md" &&
scp "$d/agent-guard.json" "$d/CLAUDE.md" brightroar-agent:.claude/
rm -r "$d"
```

**On the Mac**, log in as `agent`:

```bash
ssh brightroar-agent
```

Then, **on the Spark, as `agent`**, merge the guard into `agent`'s settings. This keeps every entry
`agent`'s file already has, saves the old file as `settings.json.bak`, and deletes the guard file
once it's merged:

```bash
cd ~/.claude && { [ -f settings.json ] || echo '{}' > settings.json; } &&
  cp -p settings.json settings.json.bak
jq -s '.[0] as $cur | .[1] as $add | $cur
  | .permissions.deny =
      ((($cur.permissions.deny // []) + $add.permissions.deny) | unique)
  | .hooks.PreToolUse = ([($cur.hooks.PreToolUse // [])[]
      | select(all(.hooks[]?; (.command // "")
        | test("block-secret-access") | not))]
      + $add.hooks.PreToolUse)' \
  settings.json agent-guard.json > settings.json.new &&
  mv settings.json.new settings.json && rm agent-guard.json
jq '{deny: (.permissions.deny | length),
  pretooluse: [.hooks.PreToolUse[].hooks[].command]}' settings.json
```

The last command shows at least as many deny rules as the Mac's file has (`agent`'s showed 35 on
2026-09-28), and `bash ~/.claude/hooks/block-secret-access.sh` among the hooks. Run again, after
the Mac's step again, the merge leaves `settings.json` as it was, and the backup then holds the
merged file. If the guard file isn't there, jq says it can't open it, `settings.json` stays as it
was, and the last command shows it unchanged. A `settings.json.new` is left beside it, holding
`agent`'s settings without the guard's hook: nothing uses it, and the next run replaces it.
*(Corrected 2026-09-28: this said that file was empty. jq 1.7 runs the filter on the file it
could read, then exits 2, which stops the `mv`. Re-run on stand-ins the same day.)* *(Run on
2026-09-28, on the Spark, on stand-in files in a scratch folder: jq 1.7, and gawk, mawk and
busybox's awk, with `ssh` and `scp` stood in for. The Mac's two blocks ran under bash there; they
haven't been run in the Mac's zsh, with its jq or its BSD awk.)*

Then check it. **On the Spark, as `agent`**, start Claude Code, or restart it if it's running, so
it loads the hook:

```bash
claude
```

`/hooks` lists the hook, and `/permissions` the deny rules. Then ask the session to run
`test -e ~/.secrets && echo present || echo absent`: the hook must refuse it. The command reads
nothing from the file either way, and the file doesn't exist yet. If it runs instead, stop, and fix
the guard before `agent` gets its key. Then leave `claude` and log out: the next step runs as you.

### Node, the key and pi

**On the Spark (you, with sudo):** install Node from NodeSource, and give `agent` its key. Run it a
line at a time: `less` shows you the setup script, and the next line runs it as root.

```bash
d=$(mktemp -d) && curl -fsSL https://deb.nodesource.com/setup_22.x -o "$d/nodesource_setup.sh" && less "$d/nodesource_setup.sh"
sudo bash "$d/nodesource_setup.sh" && sudo apt-get install -y nodejs   # only once you've read it: it runs as root
node --version                                                          # v22.19 or later
rm -r "$d"
sudo bash -c '. /etc/local-ai/secrets/llama-swap.env; [ -n "$LLAMASWAP_KEY_AGENT" ] || { echo "no LLAMASWAP_KEY_AGENT" >&2; exit 1; }; printf "export SPARK_API_KEY=%s\n" "$LLAMASWAP_KEY_AGENT" | runuser -u agent -- sh -c "umask 077; cat > /home/agent/.secrets"'
```

The setup script goes into a fresh private folder from `mktemp -d`. At a fixed `/tmp` name, a file
`agent` made first would be the one you read and then run as root, and `agent` could change it in
between. The last line copies `agent`'s own llama-swap key into its `~/.secrets` without displaying
it. Root only reads the service secrets: `runuser -u agent` runs the `sh` that writes the file as
`agent`, so a link `agent` planted at `~/.secrets` can't turn it into a write by root. `printf` is a
builtin, so the value never reaches a command line. If the key is missing, the line refuses and
writes nothing, as step 5 of [Secret files](secret-files.md) does. Run again, it rewrites the file
with the same line.

Then become `agent`, still on the Spark: `sudo -iu agent`. **On the Spark, as `agent`:** load the
key in every shell, install uv and pi into `agent`'s own `~/.local`, and clone this repo.

```bash
grep -q '\.secrets' ~/.bashrc || sed -i '1i [ -f ~/.secrets ] && . ~/.secrets' ~/.bashrc
curl -LsSf https://astral.sh/uv/install.sh | sh
npm install -g --prefix ~/.local --ignore-scripts @earendil-works/pi-coding-agent@0.85.1
git clone https://github.com/chendaniely/local-ai ~/work/local-ai   # makes ~/work too
exit
```

`agent` makes its own `~/work` with that clone; bootstrap doesn't, since it runs as root. The clone
is only for `spark clients`: `agent` never commits to this repo.

The key and `~/.local/bin` load in a fresh login. **On the Mac**, log in as `agent`:

```bash
ssh brightroar-agent
```

**On the Spark, as `agent`**, from its clone, add the `spark` provider to its pi:

```bash
cd ~/work/local-ai && uv run --frozen --project spark spark clients pi --write
```

Work inside tmux. **On the Spark, as `agent`**:

```bash
tmux new -As work
```

Then run `pi` in it. `Ctrl-b d` detaches, and `tmux attach -t work` picks it up after you log back
in.

## The context window

The Spark sets each model's context, not pi. It comes from `ctx` in `stack/models.yaml`, llama-server
reserves it when the model loads, and no request can go past it. Every model runs at its full
context (Dan's decision, 2026-09-28): 262,144 tokens for Gemma and for the coder. Gemma has two
slots, and they share that context, so one request can use all of it. Requests running at the same
time share it too: a pi session near the end of Gemma's window and a long phone chat can't both
fit, and llama-server makes room by dropping the cache of whichever is idle, which then has to be
read again.

pi learns each model's window from `contextWindow` in `~/.pi/agent/models.json`, which
`make clients` writes from the registry. So once a change to the registry's `ctx` is deployed on
the Spark and pushed, pull and run `make clients` again **on the Mac**, and **on the Spark, as
`agent`**, pull its clone and run the `spark clients pi --write` line above again. Not before: pi
told a bigger window than the engine serves sends requests the engine refuses with a `400`. pi
compacts a session by itself once it passes `contextWindow` minus `reserveTokens`, and `/compact`
does it by hand.

To make pi compact sooner, raise `reserveTokens` in `~/.pi/agent/settings.json`, on the machine whose
pi it is. `keepRecentTokens` sets how much of the recent conversation a compaction keeps whole. Both
are shown at their defaults, the same in pi 0.85.1's docs and in 0.87.1's:

```json
{
  "compaction": {
    "enabled": true,
    "reserveTokens": 16384,
    "keepRecentTokens": 20000
  }
}
```

Don't lower `contextWindow` in `models.json` instead: `make clients` replaces the whole `spark`
provider, so the edit is gone at its next run.

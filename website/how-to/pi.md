---
title: "pi, the coding agent"
description: "pi on the Mac through an SSH tunnel, and as agent in tmux on the Spark."
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
people set that up: you, with sudo, then `agent` itself.

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
told a bigger window than the engine serves sends requests the engine refuses with a `400`. pi compacts a session by itself once it passes
`contextWindow` minus `reserveTokens`, and `/compact` does it by hand.

To make pi compact sooner, raise `reserveTokens` in `~/.pi/agent/settings.json`, on the machine whose
pi it is. `keepRecentTokens` sets how much of the recent conversation a compaction keeps whole. Both
are shown at their defaults in pi 0.85.1's docs:

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

---
title: "pi, the coding agent"
description: "pi on the Mac through an SSH tunnel, and as agent in tmux on the Spark."
---

pi reaches the Spark's models as one provider, `spark`: each chat model in the registry, under its
real name. The provider names the variable that holds your llama-swap key, never the key itself.
You set pi up in two places: on the Mac, where it goes through an SSH tunnel, and on the Spark,
where it runs as `agent`, inside tmux.

## The version

pi is pinned to 0.85.1. Releases 0.86.0 through 0.87.1 are reported to crash llama-server, most
likely through a llama.cpp bug that their longer prompt triggers. Move up only to a release outside
that range, and change `stack/versions.yaml` in the same commit.

## On the Mac

**On the Mac**, install pi at its pin, then check it: `pi --version` prints `0.85.1`.

```bash
npm install -g --ignore-scripts @earendil-works/pi-coding-agent@0.85.1
pi --version
```

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

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

pi reads your key from `SPARK_API_KEY`, so your shell must export it: [Secret files](secret-files.md)
put it in `~/.secrets`. **On the Mac**, check it without showing it. This asks what a program you
start would see, so it prints `True` only when the variable is exported, not merely set:

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

Nothing on the Spark listens on the LAN in Phase 1, so pi reaches it only through the tunnel.

In pi, `/model` → a Spark model. The footer names the model that answers.

## On the Spark, as `agent`

`agent` needs Node 22.19 or later, which you install once. Then, **on the Spark, as `agent`**,
install pi into `agent`'s own `~/.local`:

```bash
npm install -g --prefix ~/.local --ignore-scripts @earendil-works/pi-coding-agent@0.85.1
```

`agent`'s own llama-swap key sits in its `~/.secrets`. You put it there with one command: root
reads the key from the service secrets, and `agent` writes its own file, so the value is never
displayed and root never writes in `agent`'s home.

**On the Spark, as `agent`**, from the agent's clone of this repo, add the `spark` provider to its
pi:

```bash
uv run --frozen --project spark spark clients pi --write
```

Work inside tmux. **On the Spark, as `agent`**:

```bash
tmux new -As work
```

Then run `pi` in it. `Ctrl-b d` detaches, and `tmux attach -t work` picks it up after you log back
in.

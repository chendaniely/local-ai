---
title: "opencode and Hermes on the Mac"
description: "The Spark's models in opencode and Hermes: one command writes both providers from the registry, with your key by reference."
---

*(Added 2026-10-14, with the `spark clients opencode` and `spark clients hermes` generators.)*

[pi on the Mac](pi.md) sets up what all three clients share: your shell exports `SPARK_API_KEY`,
and `make tunnel` forwards the Spark's models to `127.0.0.1:9100`. This page adds opencode and
Hermes to that. Both reach the Spark the way pi does, through the tunnel, with the key by
reference, never written into their files.

## 1. Write the providers

**On the Mac**, from your clone of this repo:

```bash
make clients
```

It writes the `spark` provider for pi, opencode and Hermes from the registry: each chat model by its
real name, with its context window. For one client alone: `make clients-pi`,
`make clients-opencode` or `make clients-hermes`. Each keeps everything else in its file, keeps the
previous file beside it as a `.bak` with the same permissions, and says which.

- **opencode:** `~/.config/opencode/opencode.json`, provider `spark`, key `{env:SPARK_API_KEY}`.
  Each model gets its context and output limits, so opencode knows how much context is left. A
  config with comments in it (JSONC) is refused, not rewritten: then copy the provider in by hand
  from what `uv run --frozen --project spark spark clients opencode` prints.
- **Hermes:** `~/.hermes/config.yaml`, under `providers.spark`, with `key_env: SPARK_API_KEY`.
  Hermes keeps the default model it had. The file is rewritten from its settings, so comments in it
  don't survive; the `.bak` keeps them.

## 2. Pick a model

- **opencode:** `/model`, then a `spark/…` model, such as `spark/qwen3.8-27b`.
- **Hermes:** `/model custom:spark:qwen3.8-27b` in a chat. To make a Spark model Hermes's default
  instead, **on the Mac**, from your clone:

  ```bash
  uv run --frozen --project spark spark clients hermes --write --default qwen3.8-27b
  ```

The tunnel has to be open while you use them: `make tunnel` in a spare terminal.

## If Hermes says 401

The key is wrong or missing. Hermes reads `SPARK_API_KEY` from its environment, so start it from a
shell that exports it (the check is on [pi's page](pi.md)). An earlier version of `make clients`
wrote a placeholder line, `SPARK_API_KEY=your-llama-swap-key-here`, into Hermes's `.env` file, and
Hermes sends that instead of your key. `make clients` now says if that line is still there: delete
that one line in your editor. The file holds your other keys, so don't print it.

## Not for Claude

Claude Code and Claude Desktop never use these providers: the repo's first rule keeps the Claude
path untouched.

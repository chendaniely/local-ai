---
title: "Posit Assistant on the Mac"
description: "The Spark's models in Posit Assistant — the TUI pa and Positron: make clients-posit writes the provider from the registry, and the key stays in the environment or the credential store, never in the file."
---

*(Added 2026-10-19, with the `spark clients posit` generator.)*

[Pi, the coding agent](pi.md) sets up what all [the harnesses](../architecture.qmd#harnesses)
share: your shell exports `SPARK_API_KEY`, and `make tunnel` forwards the Spark's models to
`127.0.0.1:9100`. This page adds Posit Assistant to that: its TUI, `pa`, and Positron itself. Both reach the Spark the way Pi does, through the
tunnel, over the front's OpenAI-compatible routes, with the same key. The key never goes into
Posit Assistant's files: `OPENAI_COMPATIBLE_API_KEY` in the environment for the TUI, Positron's
credential store for Positron.

## 1. Install

**The TUI, `pa`.** Posit's install script installs and updates it. These are development builds,
rebuilt as Posit merges changes, so check what you have:

**On the Mac:**

```bash
curl -fsSL https://assistant.posit.co/builds/install-tui.sh | bash
pa --version
```

**Positron.** Posit Assistant replaced Positron Assistant and Databot as of Positron 2026.07, so a
current Positron has it. The installed Positron 1.138.0 ships it as its `posit.assistant` 1.7.0
bootstrap extension (checked 2026-10-19).

## 2. Write the provider

**On the Mac**, from your clone of this repo:

```bash
make clients-posit
```

`make clients` writes it along with the other harnesses' providers, Pi's, OpenCode's and
Hermes's. It writes the built-in
`openai-compatible` provider into `~/.posit/ai/providers.json` from the registry: each chat model
by its real name, with its context window and its capabilities, and an allow list that keeps the
embeddings model and whisper out of the model picker. Every other provider in the file stays, the
previous file, if there was one, is kept beside it as `providers.json.bak` at its own
permissions, and the line the command prints says which. A file it can't read is refused, and so
is one that isn't the object Posit Assistant reads: then nothing is written.

**The provider dialog saves into the same file.** When you change something there (a key, a
provider added or removed), Positron rewrites the `openai-compatible` entry — keeping the
`baseUrl`, adding its own `protocol` key — and drops the `models` block, so the picker would
offer the embeddings model and whisper again. Run `make clients-posit` after such a save.

**Why the built-in provider.** Posit's docs recommend a custom entry for an OpenAI-compatible
endpoint, but a custom entry has no key path that works in both places: its only key mechanism,
`apiKeyHelper`, breaks Positron, which reads the same file and ignores any entry carrying it
(older Positron builds reject the whole file). The built-in `openai-compatible` provider is marked
legacy, but it is the only form with a documented key path for the TUI and Positron alike.
*(Checked against the live JSON schema and the front, 2026-10-19.)*

## 3. Give it the key

**For the TUI**, `pa` reads `OPENAI_COMPATIBLE_API_KEY` from its environment. Add one line to
`~/.secrets`, beside `SPARK_API_KEY`'s ([Secret files](secret-files.md) is where the key itself
comes from):

```bash
export OPENAI_COMPATIBLE_API_KEY="$SPARK_API_KEY"
```

That line is shell text in the secrets file, which your shell loads. Start a new terminal, or
reload the file, then check without showing the value. This asks what a program you start would
see, so it prints `True` only when the variable is exported, not merely set:

```bash
python3 -c "import os; print(bool(os.environ.get('OPENAI_COMPATIBLE_API_KEY')))"
```

**For Positron**, run the **Authentication: Configure Language Model Providers** command and enter
the key once, for OpenAI Compatible. Positron keeps it in its credential store;
`providers.json` never holds it.

## 4. Pick a model

**The TUI:** the model is set per session: start `pa` with it,
`pa -m openai-compatible/gemma-4-26b-a4b`. `pa --list-models` prints the exact
`provider/model-id` values it accepts. The current development build's default model is a Posit
Anthropic route that needs a sign-in, so pass `-m` for the Spark's models; there is no slash
command to switch models mid-session (2026-10-19). **Positron:** the assistant's model picker;
the pick is remembered in `~/.posit/assistant/settings.json` under its own `preferredModel` key.

The tunnel has to be open while you use either: `make tunnel` in a spare terminal. A first
request for a model that isn't loaded waits for the gate to load it, as pi's does: a request from
a terminal of yours waits up to 30 s for its load to start, and the coder's cold load takes a
while.

## If pa says no models are available

- **The tunnel or the key.** Check that `make tunnel` is running, that the check above prints
  `True`, and that `~/.posit/ai/providers.json` holds the `openai-compatible` entry `make
  clients-posit` wrote. `pa` reads the file when it starts: restart a `pa` that's running after a
  change.
- **A model outside the allow list, asked for by name,** is refused by `pa` itself. The allow list
  comes from the registry, so once a registry change is deployed on the Spark, pull and run
  `make clients-posit` again, as [pi's page](pi.md#the-context-window) has it for the windows.

## Not for Claude

Claude Code and Claude Desktop never use these providers: the repo's first rule keeps the Claude
path untouched.

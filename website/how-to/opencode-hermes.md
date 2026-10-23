---
title: "opencode and Hermes on the Mac"
description: "The Spark's models in opencode and Hermes: one command writes both providers from the registry, with your key by reference."
---

*(Added 2026-10-14, with the `spark clients opencode` and `spark clients hermes` generators.)*

[pi on the Mac](pi.md) sets up what all three clients share: your shell exports `SPARK_API_KEY`,
and `make tunnel` forwards the Spark's models to `127.0.0.1:9100`. This page adds opencode and
Hermes to that. Both reach the Spark the way pi does, through the tunnel, with the key by
reference, never written into their files.

*(Changed 2026-10-23, Task 44h.)* opencode and Hermes each have a key of their own, so the usage
meter can tell them apart from pi, which keeps `SPARK_API_KEY`. opencode's is `SPARK_OPENCODE_KEY`
and Hermes's `SPARK_HERMES_KEY`. Make them (next section) before `make clients`, or the two
clients send an empty key. The Spark accepts the two new keys only after the usage meter's deploy (Task 44l), so
until then `make clients` with the new defaults makes opencode and Hermes get 401s; point them back with
`--key-env SPARK_API_KEY` ([rolling back](#rolling-back-to-phase-1) shows the commands).

## 0. Make their keys

Do these in order, once per client. The commands are opencode's; for Hermes use
`SPARK_HERMES_KEY` and a key name of your own for it in the Spark's lists.

1. **On the Mac,** make the key in `~/.secrets`. The `grep` counts the lines that set it; run the
   `printf` only if it prints `0`, since a second line would replace the first one's value:

   ```bash
   grep -c '^export SPARK_OPENCODE_KEY=' ~/.secrets
   ```

   ```bash
   printf 'export SPARK_OPENCODE_KEY=%s\n' "$(openssl rand -hex 32)" >> ~/.secrets
   ```

2. **On the Mac,** send the Spark only the key's digest, never the key:

   ```bash
   ( . ~/.secrets; printf '%s' "$SPARK_OPENCODE_KEY" | shasum -a 256 | cut -d' ' -f1 ) | ssh brightroar 'umask 077; cat > ~/.spark-digest-in'
   ```

3. **On the Spark,** add the digest to the digests file with the digest-only command in
   [Secret files](secret-files.md) (step 19, "a key can also reach the Spark as its digest alone"),
   which checks the copy is 64 hex characters. Then add the key's line to the key list as step 20
   does, with the group `dan` and a label pi's retry list won't match (none with `503`, `timeout`
   or `rate limit` in it), and follow its restart steps (`make restart-front`).

4. **On the Mac,** the shell you made the key in never read it. Open a new terminal, then check
   that the variable is exported, which prints only `True` or `False`, never the key (if it prints
   `False`, the key's line is missing from your secrets file, or lacks `export`):

   ```bash
   python3 -c "import os; print(bool(os.environ.get('SPARK_OPENCODE_KEY')))"
   ```

   Then check the key without it touching a command line. Put the front's URL where
   `<front URL>` is; it should print `200`, and `401` means the Spark doesn't know the key yet:

   ```bash
   printf 'Authorization: Bearer %s\n' "$SPARK_OPENCODE_KEY" | curl -s -o /dev/null -w '%{http_code}\n' -H @- <front URL>/v1/models
   ```

5. **On the Mac,** only then write the providers, with `make clients` as the next section does.

The two key list ids you chose in step 3 are private (the key list's names never go in this repo),
so add those ids to the denylist on both machines (`~/.config/local-ai/denylist`), by hand. Don't
add the variable names `SPARK_OPENCODE_KEY` and `SPARK_HERMES_KEY`, or the words `opencode` and
`hermes`: these docs contain them, so the leak hook and the pre-push scan would fail on the repo's
own files.

## 1. Write the providers

**On the Mac**, from your clone of this repo:

```bash
make clients
```

It writes the `spark` provider for pi, opencode and Hermes from the registry: each chat model by its
real name, with its context window. For one client alone: `make clients-pi`,
`make clients-opencode` or `make clients-hermes`. Each keeps everything else in its file, keeps the
previous file beside it as a `.bak` with the same permissions, and says which.

- **opencode:** `~/.config/opencode/opencode.json`, provider `spark`, key `{env:SPARK_OPENCODE_KEY}`.
  Each model gets its context and output limits, so opencode knows how much context is left, and
  the reasoning efforts it can be sent (below, under *opencode's reasoning effort*). A
  config with comments in it (JSONC) is refused, not rewritten: then copy the provider in by hand
  from what `uv run --frozen --project spark spark clients opencode` prints.
- **Hermes:** `~/.hermes/config.yaml`, under `providers.spark`, with `key_env: SPARK_HERMES_KEY`.
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

**Hermes's reasoning effort.** Hermes sends its own reasoning effort with every request, and
Qwen3.8's chat template takes only `xhigh`, its default, `medium` and `low`: anything else, such as
Hermes's `max` or `ultra`, comes back as an error 500 naming the effort. So `make clients` sets
`xhigh` for each Spark model, under `agent.reasoning_overrides` in Hermes's config, and your other
providers keep your global setting. For quicker, shorter thinking in one session, `/reasoning
medium` or `/reasoning low`; leave out `--global`, which would change it for every provider.

**opencode's reasoning effort.** opencode gives each model a list of efforts to pick from and sends
the one you pick. For a provider like this one its own list is `low`, `medium` and `high`, and
Qwen3.8's template refuses `high`: `Error: Unexpected reasoning effort high`. So `make clients` gives
each Spark model its own list, `low`, `medium` and `xhigh`, which replaces opencode's. **Default**
sends no effort at all, and the template then uses its own default, `xhigh`, the most thinking: so
*default* and `xhigh` are the same. Gemma's template ignores the effort. *(Added 2026-10-14, checked
with opencode 2.0.25 against a stand-in server that recorded what each choice sent: `high` is now
refused inside opencode, before anything is sent.)*

## If opencode says Unauthorized

opencode 2 runs one **background service**, and every opencode window you open hands its requests
to it. The service has the environment it started with. A key you added to `~/.secrets` after it
started isn't in it, so opencode sends an empty key, and the Spark answers 401: opencode shows
`Error: Unauthorized`. A new terminal doesn't help, because the new window uses the same old
service. Restarting the service from a shell that has the key does.

**On the Mac**, in a terminal opened after the key went into `~/.secrets`:

```bash
opencode service restart
```

Then start `opencode` again. Other opencode windows were using the old service: start one again if it
stops answering. `opencode service status` shows the service's address when it's running, and
`opencode --standalone` runs one window with a private service of its own, leaving the shared one
alone. *(Seen 2026-10-14: the service had started that morning, before the fast coder's key was
made, and a new terminal still got 401 until the restart.)*

## If Hermes says 401

The key is wrong or missing. Hermes reads `SPARK_HERMES_KEY` from its environment, so start it from a
shell that exports it. **On the Mac**, check it without showing it; this prints `True` only when the
variable is exported, not merely set, and never the value *(corrected 2026-10-23: this said pi's page had the check
with this name, but pi's page checks `SPARK_API_KEY`)*:

```bash
python3 -c "import os; print(bool(os.environ.get('SPARK_HERMES_KEY')))"
```

An earlier version of
`make clients` wrote a placeholder line, `SPARK_API_KEY=your-llama-swap-key-here`, into Hermes's `.env` file, and
Hermes sends that instead of your key. `make clients` now says if that line is still there: delete
that one line in your editor. The file holds your other keys, so don't print it.

## Rolling back to Phase 1

Phase 1's llama-swap doesn't know the new keys. After [the rollback](deploy.md), **on the Mac**,
put both clients back on `SPARK_API_KEY`. These are also the commands to run before Task 44l's deploy, while the box
knows only that one key:

```bash
uv run --frozen --project spark spark clients opencode --write --key-env SPARK_API_KEY
```

```bash
uv run --frozen --project spark spark clients hermes --write --key-env SPARK_API_KEY
```

Then restart both: `opencode service restart`, and start Hermes again from a shell that exports
`SPARK_API_KEY`.

## Not for Claude

Claude Code and Claude Desktop never use these providers: the repo's first rule keeps the Claude
path untouched.

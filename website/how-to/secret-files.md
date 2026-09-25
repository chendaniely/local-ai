---
title: "Secret files"
description: "Every Phase 1 secret file, step by step, generated so no value is ever displayed."
---

You create the secrets Phase 1 needs, one command per step. None of them displays a value.
Ubuntu's `sh` is dash, which has no `read -s`, so the commands use `bash -c`.

## Where secrets live

In `/etc/local-ai/secrets/`: one file per service, with `KEY=value` lines and no `export`. The files
are owned by `root`, group `spark`, mode `0640`. Your own account can't list that folder, so nothing
running as you reads a secret by accident. Bootstrap created the folder.

**Run each step once.** Every step appends a line, so running one twice leaves a duplicate key.
The check in step 10 shows it if that happens.

## On the Spark

Run steps 1–7 in your own terminal on the Spark. Each one asks for your sudo password.

**1. llama-swap's key for agent** (`LLAMASWAP_KEY_AGENT`, into `llama-swap.env`):

```bash
sudo bash -c 'umask 027; printf "LLAMASWAP_KEY_AGENT=%s\n" "$(openssl rand -hex 32)" >> /etc/local-ai/secrets/llama-swap.env; chgrp spark /etc/local-ai/secrets/llama-swap.env'
```

**2. llama-swap's key for Open WebUI** (`LLAMASWAP_KEY_OPENWEBUI`). Step 5 copies it, so it must
come first:

```bash
sudo bash -c 'umask 027; printf "LLAMASWAP_KEY_OPENWEBUI=%s\n" "$(openssl rand -hex 32)" >> /etc/local-ai/secrets/llama-swap.env; chgrp spark /etc/local-ai/secrets/llama-swap.env'
```

**3. llama-swap's key for the stack itself** (`LLAMASWAP_KEY_SPARK`):

```bash
sudo bash -c 'umask 027; printf "LLAMASWAP_KEY_SPARK=%s\n" "$(openssl rand -hex 32)" >> /etc/local-ai/secrets/llama-swap.env; chgrp spark /etc/local-ai/secrets/llama-swap.env'
```

**4. Open WebUI's own secret** (`WEBUI_SECRET_KEY`, into `open-webui.env`). Without it, recreating
the container logs everyone out:

```bash
sudo bash -c 'umask 027; printf "WEBUI_SECRET_KEY=%s\n" "$(openssl rand -hex 32)" >> /etc/local-ai/secrets/open-webui.env; chgrp spark /etc/local-ai/secrets/open-webui.env'
```

**5. Open WebUI's three copies of its llama-swap key.** `OPENAI_API_KEYS`, `RAG_OPENAI_API_KEY` and
`AUDIO_STT_OPENAI_API_KEY` all hold `LLAMASWAP_KEY_OPENWEBUI`'s value. If step 2 hasn't run, this
refuses and writes nothing:

```bash
sudo bash -c '. /etc/local-ai/secrets/llama-swap.env; [ -n "$LLAMASWAP_KEY_OPENWEBUI" ] || { echo "create LLAMASWAP_KEY_OPENWEBUI first" >&2; exit 1; }; umask 027; for k in OPENAI_API_KEYS RAG_OPENAI_API_KEY AUDIO_STT_OPENAI_API_KEY; do printf "%s=%s\n" "$k" "$LLAMASWAP_KEY_OPENWEBUI"; done >> /etc/local-ai/secrets/open-webui.env; chgrp spark /etc/local-ai/secrets/open-webui.env'
```

**6. SearXNG's secret** (`SEARXNG_SECRET`, into `searxng.env`):

```bash
sudo bash -c 'umask 027; printf "SEARXNG_SECRET=%s\n" "$(openssl rand -hex 32)" >> /etc/local-ai/secrets/searxng.env; chgrp spark /etc/local-ai/secrets/searxng.env'
```

**7. Your Hugging Face token** (`HF_TOKEN`, into `hf.env`). Copy a read token from
<https://huggingface.co/settings/tokens>, run this, and paste at the `HF token:` prompt. Nothing
shows as you paste:

```bash
sudo bash -c 'read -rsp "HF token: " t; echo; umask 027; printf "HF_TOKEN=%s\n" "$t" >> /etc/local-ai/secrets/hf.env; chgrp spark /etc/local-ai/secrets/hf.env'
```

## The Mac's key

Your Mac gets its own llama-swap key (`LLAMASWAP_KEY_DAN_MAC`). It is generated on the Mac, kept in
the Mac's `~/.secrets` as `SPARK_API_KEY`, and copied to the Spark without being displayed.

**8. On the Mac, generate it.** If `SPARK_API_KEY` is already in your `~/.secrets`, skip this: a
second line would replace the first one's value.

```bash
grep -c '^export SPARK_API_KEY=' ~/.secrets    # 0 → run the next line; 1 → skip it
printf 'export SPARK_API_KEY=%s\n' "$(openssl rand -hex 32)" >> ~/.secrets
```

**9. Send it to the Spark and add it there.**

On the Mac, send it (nothing is displayed):

```bash
( . ~/.secrets; printf 'LLAMASWAP_KEY_DAN_MAC=%s\n' "$SPARK_API_KEY" ) | ssh brightroar 'umask 077; cat > ~/.spark-key-in'
```

Then on the Spark, add it to `llama-swap.env` and delete the copy:

```bash
sudo bash -c 'umask 027 && cat >> /etc/local-ai/secrets/llama-swap.env && chgrp spark /etc/local-ai/secrets/llama-swap.env' < ~/.spark-key-in && rm ~/.spark-key-in
```

Your shell hands the file over on standard input, so no home directory is named, and every step
is joined with `&&`: the copy is deleted only after its line is in `llama-swap.env`. If anything
fails, the file stays and nothing is lost.

## Check

**10. On the Spark, list the key names and permissions.** This shows names only, never values. A
line with no `=` in it is counted, never shown, since it could be a bare value:

```bash
sudo sh -c 'for f in /etc/local-ai/secrets/*.env; do echo "$f"; sed -n "s/=.*//p" "$f" | sed "s/^/  /"; n=$(grep -vc = "$f"); [ "$n" -eq 0 ] || echo "  and $n line(s) without =, not shown"; done; stat -c "%a %U:%G %n" /etc/local-ai/secrets/*.env'
```

You should see:

| File | Keys |
|---|---|
| `llama-swap.env` | `LLAMASWAP_KEY_AGENT`, `LLAMASWAP_KEY_OPENWEBUI`, `LLAMASWAP_KEY_SPARK`, `LLAMASWAP_KEY_DAN_MAC` |
| `open-webui.env` | `WEBUI_SECRET_KEY`, `OPENAI_API_KEYS`, `RAG_OPENAI_API_KEY`, `AUDIO_STT_OPENAI_API_KEY` |
| `searxng.env` | `SEARXNG_SECRET` |
| `hf.env` | `HF_TOKEN` |

Each key appears once, and every file is `640 root:spark`. If a key appears twice, its step ran
twice. This keeps the last copy of each key, the one the services actually use, without displaying
anything. Set `f` to the file that has the duplicate:

```bash
sudo bash -c 'umask 027 && f=/etc/local-ai/secrets/llama-swap.env && tac "$f" | awk -F= "!seen[\$1]++" | tac > "$f.new" && chgrp spark "$f.new" && mv "$f.new" "$f"'
```

If a file has lines without `=`, keep only its `KEY=value` lines, again without displaying
anything. Set `f` to that file:

```bash
sudo bash -c 'umask 027 && f=/etc/local-ai/secrets/llama-swap.env && grep = "$f" > "$f.new" && chgrp spark "$f.new" && mv "$f.new" "$f"'
```

Names alone can't show one failure: a copy in `open-webui.env` that no longer matches
`LLAMASWAP_KEY_OPENWEBUI`, after that key changed. This compares each copy with it by hash and
prints only `match` or `MISMATCH`. It reads each key's last copy, the one the services use, so a
leftover duplicate doesn't turn every line into a mismatch:

```bash
sudo bash -c 'd=/etc/local-ai/secrets; a=$(sed -n "s/^LLAMASWAP_KEY_OPENWEBUI=//p" $d/llama-swap.env | tail -n 1); [ -n "$a" ] || { echo "no LLAMASWAP_KEY_OPENWEBUI" >&2; exit 1; }; h=$(printf %s "$a" | sha256sum); for k in OPENAI_API_KEYS RAG_OPENAI_API_KEY AUDIO_STT_OPENAI_API_KEY; do v=$(sed -n "s/^$k=//p" $d/open-webui.env | tail -n 1); [ "$(printf %s "$v" | sha256sum)" = "$h" ] && echo "$k: match" || echo "$k: MISMATCH"; done'
```

Expected: three `match` lines. For a `MISMATCH`, run step 5 again, then the command above that
keeps each key's last copy, with `f` set to `open-webui.env`.

Don't open the files in an editor: that puts the values on screen.

## Record it

**11. In the vault**, record each secret by reference only: which file, and which variable name.
Never the value. For the Mac's key, note that it lives in the Mac's `~/.secrets` as
`SPARK_API_KEY`.

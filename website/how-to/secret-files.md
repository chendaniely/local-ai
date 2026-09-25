---
title: "Secret files"
description: "Every Phase 1 secret file, generated so the value is never displayed."
---

Every command below runs in **your** terminal. None of them displays a value. Ubuntu's `sh` is
dash, which has no `read -s`, so these all use `bash -c`.

## Where secrets live

Files live in `/etc/local-ai/secrets/` — owner `root`, group `spark`, mode `0640` — one file per
service, `KEY=value` lines, no `export`. Your own account can't list that folder, so nothing
running as you reads a secret by accident.

## A new random key

This is the shape every random key below follows:

```bash
sudo bash -c 'umask 027; printf "LLAMASWAP_KEY_AGENT=%s\n" "$(openssl rand -hex 32)" >> /etc/local-ai/secrets/llama-swap.env; chgrp spark /etc/local-ai/secrets/llama-swap.env'
```

## What Phase 1 needs

- `llama-swap.env` — `LLAMASWAP_KEY_DAN_MAC`, `LLAMASWAP_KEY_AGENT`, `LLAMASWAP_KEY_OPENWEBUI`,
  `LLAMASWAP_KEY_SPARK`. `LLAMASWAP_KEY_AGENT` is the example above; `LLAMASWAP_KEY_OPENWEBUI` and
  `LLAMASWAP_KEY_SPARK` follow the same pattern, changing only the key name. Create
  `LLAMASWAP_KEY_OPENWEBUI` before the `open-webui.env` step below, which copies it.
  `LLAMASWAP_KEY_DAN_MAC` comes from the Mac — see below.
- `open-webui.env` — `WEBUI_SECRET_KEY`, its own random value, same pattern, into
  `open-webui.env` instead. Without it, recreating the container logs everyone out. Then three
  more keys, which all hold the *same* value — `LLAMASWAP_KEY_OPENWEBUI`'s. If that key doesn't
  exist yet, the command refuses and writes nothing:

  ```bash
  sudo bash -c '. /etc/local-ai/secrets/llama-swap.env; [ -n "$LLAMASWAP_KEY_OPENWEBUI" ] || { echo "create LLAMASWAP_KEY_OPENWEBUI first" >&2; exit 1; }; umask 027; for k in OPENAI_API_KEYS RAG_OPENAI_API_KEY AUDIO_STT_OPENAI_API_KEY; do printf "%s=%s\n" "$k" "$LLAMASWAP_KEY_OPENWEBUI"; done >> /etc/local-ai/secrets/open-webui.env; chgrp spark /etc/local-ai/secrets/open-webui.env'
  ```
- `searxng.env` — `SEARXNG_SECRET`, same pattern, into `searxng.env`.
- `hf.env` — `HF_TOKEN`, pasted without echo:

  ```bash
  sudo bash -c 'read -rsp "HF token: " t; echo; umask 027; printf "HF_TOKEN=%s\n" "$t" >> /etc/local-ai/secrets/hf.env; chgrp spark /etc/local-ai/secrets/hf.env'
  ```

## The Mac's key

Generate it on the Mac:

```bash
printf 'export SPARK_API_KEY=%s\n' "$(openssl rand -hex 32)" >> ~/.secrets
```

Send the same value to the Spark without displaying it:

```bash
( . ~/.secrets; printf 'LLAMASWAP_KEY_DAN_MAC=%s\n' "$SPARK_API_KEY" ) | ssh brightroar 'umask 077; cat > ~/.spark-key-in'
```

Then on the Spark — with the same `umask` and `chgrp` as the other commands, so `llama-swap.env`
comes out right whichever step creates it:

```bash
sudo bash -c 'umask 027 && cat >> /etc/local-ai/secrets/llama-swap.env && chgrp spark /etc/local-ai/secrets/llama-swap.env' < ~/.spark-key-in && rm ~/.spark-key-in
```

Your shell opens `~/.spark-key-in` and hands it over on standard input, so no home directory is
named, and every step is joined with `&&`: the file is deleted only after its line is in
`llama-swap.env`. If anything fails, the file stays and nothing is lost.

## Record it

Record each secret in the vault by reference only — which file, which variable name. Never the
value.

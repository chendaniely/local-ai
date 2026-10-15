---
title: "Secret files"
description: "Every secret file, Phase 1's and Phase 2a's, step by step, generated so no value is ever displayed."
---

You create the secrets the stack needs, one command per step. None of them displays a value.
Ubuntu's `sh` is dash, which has no `read -s`, so the commands use `bash -c`.

## Where secrets live

In `/etc/local-ai/secrets/`: one file per service, with `KEY=value` lines and no `export`, and from
Phase 2a a few files that hold one key each. The folder is `root:root 0700` and every file in it
`0600 root:root`: only root reads them. PID 1 reads them for a unit's `EnvironmentFile=` and
`LoadCredential=`, and root's Compose reads its `env_file`, so nothing reads them as `spark`, and
neither the engines nor the model pull can read a secret from disk. Your own account can't list
that folder, so nothing running as you reads a secret by accident. Bootstrap creates the folder,
and sets every regular file in it to `0600 root:root` at each run.

*(Changed 2026-10-14, Phase 2a's Task 27, from Dan's decision of 2026-10-07. Phase 1's rule was a
folder `root:spark 0750` and files `0640 root:spark`, and these steps wrote each file with
`umask 027` and `chgrp spark`. Phase 2a's bootstrap re-run (its Task 36) makes the folder and every
file in it root's alone, and the steps now write them that way: as root, with `umask 077`, and no
`chgrp`. Before that re-run, the box's files are still `0640 root:spark`.)*

**Run each step once.** Every step appends a line, so running one twice leaves a duplicate key.
The check in step 10 shows it if that happens. The Phase 2a steps refuse to run twice.

## On the Spark

Run steps 1–7 in your own terminal on the Spark. Each one asks for your sudo password.

**1. llama-swap's key for agent** (`LLAMASWAP_KEY_AGENT`, into `llama-swap.env`):

**On the Spark:**

```bash
sudo bash -c 'umask 077; printf "LLAMASWAP_KEY_AGENT=%s\n" "$(openssl rand -hex 32)" >> /etc/local-ai/secrets/llama-swap.env'
```

**2. llama-swap's key for Open WebUI** (`LLAMASWAP_KEY_OPENWEBUI`). Step 5 copies it, so it must
come first:

**On the Spark:**

```bash
sudo bash -c 'umask 077; printf "LLAMASWAP_KEY_OPENWEBUI=%s\n" "$(openssl rand -hex 32)" >> /etc/local-ai/secrets/llama-swap.env'
```

**3. llama-swap's key for the stack itself** (`LLAMASWAP_KEY_SPARK`):

**On the Spark:**

```bash
sudo bash -c 'umask 077; printf "LLAMASWAP_KEY_SPARK=%s\n" "$(openssl rand -hex 32)" >> /etc/local-ai/secrets/llama-swap.env'
```

**4. Open WebUI's own secret** (`WEBUI_SECRET_KEY`, into `open-webui.env`). Without it, recreating
the container logs everyone out:

**On the Spark:**

```bash
sudo bash -c 'umask 077; printf "WEBUI_SECRET_KEY=%s\n" "$(openssl rand -hex 32)" >> /etc/local-ai/secrets/open-webui.env'
```

**5. Open WebUI's three copies of its llama-swap key.** `OPENAI_API_KEYS`, `RAG_OPENAI_API_KEY` and
`AUDIO_STT_OPENAI_API_KEY` all hold `LLAMASWAP_KEY_OPENWEBUI`'s value. If step 2 hasn't run, this
refuses and writes nothing:

**On the Spark:**

```bash
sudo bash -c '. /etc/local-ai/secrets/llama-swap.env; [ -n "$LLAMASWAP_KEY_OPENWEBUI" ] || { echo "create LLAMASWAP_KEY_OPENWEBUI first" >&2; exit 1; }; umask 077; for k in OPENAI_API_KEYS RAG_OPENAI_API_KEY AUDIO_STT_OPENAI_API_KEY; do printf "%s=%s\n" "$k" "$LLAMASWAP_KEY_OPENWEBUI"; done >> /etc/local-ai/secrets/open-webui.env'
```

**6. SearXNG's secret** (`SEARXNG_SECRET`, into `searxng.env`):

**On the Spark:**

```bash
sudo bash -c 'umask 077; printf "SEARXNG_SECRET=%s\n" "$(openssl rand -hex 32)" >> /etc/local-ai/secrets/searxng.env'
```

**7. Your Hugging Face token** (`HF_TOKEN`, into `hf.env`). Copy a read token from
<https://huggingface.co/settings/tokens>, run this, and paste at the `HF token:` prompt. Nothing
shows as you paste:

**On the Spark:**

```bash
sudo bash -c 'read -rsp "HF token: " t; echo; umask 077; printf "HF_TOKEN=%s\n" "$t" >> /etc/local-ai/secrets/hf.env'
```

## The Mac's key

Your Mac gets its own llama-swap key (`LLAMASWAP_KEY_DAN_MAC`). It is generated on the Mac, kept in
the Mac's `~/.secrets` as `SPARK_API_KEY`, and copied to the Spark without being displayed.

**8. On the Mac, generate it.** If `SPARK_API_KEY` is already in your `~/.secrets`, skip this: a
second line would replace the first one's value. This counts the lines that set it:

```bash
grep -c '^export SPARK_API_KEY=' ~/.secrets
```

If it prints `0`, generate the key, still **on the Mac**; if it prints `1`, go on to step 9:

```bash
printf 'export SPARK_API_KEY=%s\n' "$(openssl rand -hex 32)" >> ~/.secrets
```

**9. Send it to the Spark and add it there.**

**On the Mac**, send it (nothing is displayed):

```bash
( . ~/.secrets; printf 'LLAMASWAP_KEY_DAN_MAC=%s\n' "$SPARK_API_KEY" ) | ssh brightroar 'umask 077; cat > ~/.spark-key-in'
```

Then, **on the Spark**, add it to `llama-swap.env` and delete the copy:

```bash
sudo bash -c 'umask 077 && cat >> /etc/local-ai/secrets/llama-swap.env' < ~/.spark-key-in && rm ~/.spark-key-in
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

From Phase 2a, `internal-keys.env` is listed too, with the three keys of step 11. Each key appears
once, and every file is `600 root:root` (`640 root:spark` until Phase 2a's bootstrap re-run). If a
key appears twice, its step ran twice. This keeps the last copy of each key, the one the services
actually use, without displaying anything. **On the Spark**, set `f` to the file that has the
duplicate:

```bash
sudo bash -c 'umask 077 && f=/etc/local-ai/secrets/llama-swap.env && tac "$f" | awk -F= "!seen[\$1]++" | tac > "$f.new" && mv "$f.new" "$f"'
```

If a file has lines without `=`, keep only its `KEY=value` lines, again without displaying
anything. **On the Spark**, set `f` to that file:

```bash
sudo bash -c 'umask 077 && f=/etc/local-ai/secrets/llama-swap.env && grep = "$f" > "$f.new" && mv "$f.new" "$f"'
```

**On the Spark**, names alone can't show one failure: a copy in `open-webui.env` that no longer
matches `LLAMASWAP_KEY_OPENWEBUI`, after that key changed. This compares each copy with it by hash
and prints only `match` or `MISMATCH`. It reads each key's last copy, the one the services use, so
a leftover duplicate doesn't turn every line into a mismatch:

```bash
sudo bash -c 'd=/etc/local-ai/secrets; a=$(sed -n "s/^LLAMASWAP_KEY_OPENWEBUI=//p" $d/llama-swap.env | tail -n 1); [ -n "$a" ] || { echo "no LLAMASWAP_KEY_OPENWEBUI" >&2; exit 1; }; h=$(printf %s "$a" | sha256sum); for k in OPENAI_API_KEYS RAG_OPENAI_API_KEY AUDIO_STT_OPENAI_API_KEY; do v=$(sed -n "s/^$k=//p" $d/open-webui.env | tail -n 1); [ "$(printf %s "$v" | sha256sum)" = "$h" ] && echo "$k: match" || echo "$k: MISMATCH"; done'
```

Expected: three `match` lines. For a `MISMATCH`, run step 5 again, then the command above that
keeps each key's last copy, with `f` set to `open-webui.env`.

Don't open the files in an editor: that puts the values on screen.

## Phase 2a's files

Phase 2a puts a front in front of llama-swap (127.0.0.1:9100), which checks the same client keys
but holds only their SHA-256 digests, and gives each caller of llama-swap, the front, the gate and
the brake, an internal key of its own. Run these steps after Phase 2a's bootstrap re-run, which
made the folder root's alone ([Bootstrap the Spark](bootstrap.md#phase-2as-re-run)), and before
the cutover. Steps 11 and 12 each refuse, and write nothing, if their files are already there.

| File | Holds | Read by |
|---|---|---|
| `internal-keys.env` | `LLAMASWAP_KEY_FRONT`, `LLAMASWAP_KEY_GATE`, `LLAMASWAP_KEY_BRAKE` | llama-swap's `EnvironmentFile=` |
| `front.key`, `gate.key`, `brake.key` | the same three keys, one in each, on one line | each caller's `LoadCredential=` |
| `client-keys.sha256` | one `<key name> <hex>` line for each client key: its name and its key's SHA-256 | the front's `LoadCredential=` |
| `llama-swap.env` | Phase 1's, kept as it is | nothing from the cutover; the rollback to Phase 1's layout |
| `ntfy-gate.header`, `ntfy-notify.header`, `ntfy-brake.header` | ntfy's tokens, Task 2's, in Phase 5 | the gate's, the notifier's and the brake's `LoadCredential=` |

Every file is `0600 root:root`. The private key list, `/etc/local-ai/keys.yaml`, sits beside the
folder, not in it: it holds names, not secrets.

**11. The three internal keys.** Each is generated once and written twice, to its own file and to
`internal-keys.env`, and never displayed. It refuses if any of the three key files is there, or if
`internal-keys.env` holds one of the three keys already:

**On the Spark:**

```bash
sudo bash -c 'd=/etc/local-ai/secrets; for n in front gate brake; do [ ! -e "$d/$n.key" ] || { echo "$d/$n.key is there already, so nothing was written" >&2; exit 1; }; done; if grep -qs "^LLAMASWAP_KEY_\(FRONT\|GATE\|BRAKE\)=" "$d/internal-keys.env"; then echo "internal-keys.env holds an internal key already, so nothing was written" >&2; exit 1; fi; umask 077; for n in front gate brake; do k=$(openssl rand -hex 32); [ ${#k} -eq 64 ] || { echo "openssl gave no key for $n" >&2; exit 1; }; printf "%s\n" "$k" > "$d/$n.key"; printf "LLAMASWAP_KEY_%s=%s\n" "${n^^}" "$k" >> "$d/internal-keys.env"; done'
```

`printf` is a shell builtin, so no key is ever on a command line. If it stops partway, which only a
failing `openssl` or a full disk would do, steps 16 and 17 show which file is short or doesn't
match: remove the four files and run it again.

**12. The client keys' digests** (`client-keys.sha256`), from the client keys in `llama-swap.env`:
every `LLAMASWAP_KEY_` line but `LLAMASWAP_KEY_SPARK`, which retires. For each one it asks for the
key's name, the name messages and the key list use for it: lowercase letters, digits and `-`. The
names are yours to choose, private but not secret, and they go in the key list (step 13), never in
this repo. It shows no key and no digest, and writes the file only once every key has a name:

**On the Spark:**

```bash
sudo bash -c 'd=/etc/local-ai/secrets; f=$d/client-keys.sha256; [ ! -e "$f" ] || { echo "$f is there already, so nothing was written" >&2; exit 1; }; vars=$(sed -n "s/^\(LLAMASWAP_KEY_[A-Z0-9_]*\)=.*/\1/p" "$d/llama-swap.env" | grep -vx LLAMASWAP_KEY_SPARK | sort -u); [ -n "$vars" ] || { echo "no client key in llama-swap.env" >&2; exit 1; }; umask 077; lines=(); for var in $vars; do read -rp "name for the key in $var: " name; case "$name" in ""|*[!a-z0-9-]*) echo "a name is lowercase letters, digits and -, so nothing was written" >&2; exit 1 ;; esac; for l in "${lines[@]}"; do [ "${l%% *}" != "$name" ] || { echo "$name is taken, so nothing was written" >&2; exit 1; }; done; k=$(sed -n "s/^$var=//p" "$d/llama-swap.env" | tail -n 1); lines+=("$name $(printf %s "$k" | sha256sum | cut -d" " -f1)"); done; printf "%s\n" "${lines[@]}" > "$f"'
```

It reads each key's last copy, as the services do. The Mac's key is `LLAMASWAP_KEY_DAN_MAC`, Open
WebUI's `LLAMASWAP_KEY_OPENWEBUI` and `agent`'s `LLAMASWAP_KEY_AGENT`.

**13. The private key list,** `/etc/local-ai/keys.yaml`: each key's name, its group (what it may
do: `dan` or `agent`, the registry's `key_groups`), its label (how messages name the asker) and,
for `agent`'s, its account. It is private, not secret, so you edit it in an editor. **On the Spark**,
from the clone, copy the example to your home:

```bash
cp stack/keys.example.yaml ~/keys.yaml
```

In `~/keys.yaml`, replace each `example-` name with the name step 12 gave that key: `example-mac`
the Mac's, `example-web-ui` Open WebUI's and `example-agent` `agent`'s. Keep the groups, labels and
account as they are. Then, **on the Spark**, install it as root's, for `spark-admin` to read, and
delete the copy:

```bash
sudo install -o root -g spark-admin -m 0640 ~/keys.yaml /etc/local-ai/keys.yaml && rm ~/keys.yaml
```

PID 1 reads it, as root, and hands it to the front and the gate through `LoadCredential=`. Your
account reads it as a member of `spark-admin`, and `agent` can't reach it.

**14. ntfy's files are Task 2's.** The three header files above, and the values file
`/etc/local-ai/values.env` with its `NTFY_*` lines, come with Task 2, in Phase 5:
[ntfy](ntfy.md) makes them. Nothing here makes them, and the stack starts and serves without them.

**15. Phase 1's `llama-swap.env` stays,** `0600 root:root`, for the rollback to Phase 1's layout,
until Phase 2a closes (its Task 51 removes it). From the cutover nothing reads it: llama-swap reads
`internal-keys.env`, and the client keys reach the front only as their digests.

## Check Phase 2a's files

**16. On the Spark, the names, sizes and permissions, and ntfy's state.** Names, sizes and counts
only, never a value:

```bash
sudo bash -c 'd=/etc/local-ai/secrets; stat -c "%a %U:%G %n" "$d" "$d"/*; for f in "$d"/*.env; do echo "$f"; sed -n "s/=.*//p" "$f" | sed "s/^/  /"; n=$(grep -vc = "$f"); [ "$n" -eq 0 ] || echo "  and $n line(s) without =, not shown"; done; for f in "$d"/*.key; do [ -e "$f" ] || continue; echo "$f: $(wc -l < "$f") line, $(wc -c < "$f") bytes"; done; [ -e "$d/client-keys.sha256" ] && echo "$d/client-keys.sha256: $(grep -c "^[a-z0-9-][a-z0-9-]* [0-9a-f]\{64\}$" "$d/client-keys.sha256") digest line(s), $(grep -vc "^[a-z0-9-][a-z0-9-]* [0-9a-f]\{64\}$" "$d/client-keys.sha256") other(s)"; have="" missing=""; for h in ntfy-gate.header ntfy-notify.header ntfy-brake.header; do if [ -e "$d/$h" ]; then have="$have $h"; else missing="$missing $h"; fi; done; if grep -qs "^NTFY_URL=" /etc/local-ai/values.env; then have="$have NTFY_URL"; else missing="$missing NTFY_URL"; fi; if [ -z "$have" ]; then echo "ntfy not set up (Task 2, Phase 5)"; elif [ -n "$missing" ]; then echo "ntfy half set up (Task 2): missing$missing"; fi'
```

Expected:

- `700 root:root` for the folder, and `600 root:root` for every file in it.
- `internal-keys.env` with `LLAMASWAP_KEY_FRONT`, `LLAMASWAP_KEY_GATE` and `LLAMASWAP_KEY_BRAKE`,
  and the Phase 1 files as step 10 lists them.
- Each `.key` file: `1 line, 65 bytes`, the key and its line break.
- `client-keys.sha256`: one digest line per client key, three today, and `0 other(s)`.
- Until Task 2: `ntfy not set up (Task 2, Phase 5)`. That is expected, never a failure. Once
  Task 2 has made all three headers and `NTFY_URL`, the check says nothing about ntfy. Some of them
  there and some not is a Task 2 left half done, and the line names each one missing, by name only.

**17. On the Spark, the hash checks.** Each key is compared with its copy by its SHA-256, inside
the shell, and only `match` or `MISMATCH` is printed: each internal key's file with its line in
`internal-keys.env`, Open WebUI's three copies of its key with the digests, and, while
`llama-swap.env` is there, each of its client keys with the digests:

```bash
sudo bash -c 'd=/etc/local-ai/secrets; h() { printf %s "$1" | sha256sum | cut -d" " -f1; }; digests=$(awk "!/^#/ && NF {print \$2}" "$d/client-keys.sha256"); known() { local x; for x in $digests; do [ "$x" = "$1" ] && return 0; done; return 1; }; for n in front gate brake; do v=$(sed -n "s/^LLAMASWAP_KEY_${n^^}=//p" "$d/internal-keys.env" | tail -n 1); k=$(head -n 1 "$d/$n.key"); if [ -n "$v" ] && [ "$(h "$k")" = "$(h "$v")" ]; then echo "$n.key: match"; else echo "$n.key: MISMATCH"; fi; done; for k in OPENAI_API_KEYS RAG_OPENAI_API_KEY AUDIO_STT_OPENAI_API_KEY; do v=$(sed -n "s/^$k=//p" "$d/open-webui.env" | tail -n 1); if [ -n "$v" ] && known "$(h "$v")"; then echo "$k: match"; else echo "$k: MISMATCH"; fi; done; [ -e "$d/llama-swap.env" ] || exit 0; for var in $(sed -n "s/^\(LLAMASWAP_KEY_[A-Z0-9_]*\)=.*/\1/p" "$d/llama-swap.env" | grep -vx LLAMASWAP_KEY_SPARK | sort -u); do v=$(sed -n "s/^$var=//p" "$d/llama-swap.env" | tail -n 1); if known "$(h "$v")"; then echo "$var: match"; else echo "$var: MISMATCH"; fi; done'
```

Expected: every line `match`, nine of them while `llama-swap.env` is there. A digest is compared
with `[`, a builtin, so neither a key nor a digest reaches a command line. For a `.key` that
doesn't match, or a client key missing from the digests, remove the step's files and run it again:
`sudo rm /etc/local-ai/secrets/client-keys.sha256` before step 12, or the four files of step 11
before it. For an Open WebUI copy that doesn't match, its key and the digests disagree: run step 5
again, then the duplicate fix above with `f` set to `open-webui.env`.

**18. On the Spark, the key list names every digest, and no other key.** It loads the key list as
the front and the gate will, against the clone's registry, so a group, a label or a name it would
refuse fails here too, then compares its names with the digests'. Names only. `sudo -v` asks for
your password first, if sudo needs it, so its prompt doesn't run into `diff`'s output:

```bash
sudo -v && diff <(uv run --frozen --project spark python -c 'from spark import paths; from spark.registry import load_keys, load_registry; print("\n".join(sorted(load_keys(paths.KEYS, load_registry("stack/models.yaml").key_groups))))') <(sudo awk '!/^#/ && NF {print $1}' /etc/local-ai/secrets/client-keys.sha256 | sort) && echo "the key list names every digest, and no other key"
```

Run it from the clone's root. `diff` shows only the names that differ: `<` for a name only the key
list has, `>` for one only the digests have.

## A new client key, after 2a

From the cutover the front checks the client keys, so a new client gets a key by its digest. This
changes both files, `client-keys.sha256` and the key list, and the front reads them only when it
starts: then restart it with `make restart-front`, which waits for a quiet minute as `make apply`
does. *(That target comes with Phase 2a's deploy work, its Task 31. Before the cutover no front
runs, and a new client key goes into `llama-swap.env`, as in steps 1 and 9.)* The name is yours,
lowercase letters, digits and `-`, and goes in both files.

**19. Generate it, and give it to its client.** Set `name` in the command to the new key's name, in
place of `example-new-key`.

For `agent`, whose key lives in its own `~/.secrets`: the key is generated as root, written into
`agent`'s file by `agent` itself, and only its digest is kept. **On the Spark:**

```bash
sudo bash -c 'name=example-new-key; d=/etc/local-ai/secrets; case "$name" in ""|*[!a-z0-9-]*) echo "a name is lowercase letters, digits and -" >&2; exit 1 ;; esac; if awk -v n="$name" "\$1 == n {found = 1} END {exit !found}" "$d/client-keys.sha256"; then echo "$name is in client-keys.sha256 already" >&2; exit 1; fi; k=$(openssl rand -hex 32); [ ${#k} -eq 64 ] || { echo "openssl gave no key" >&2; exit 1; }; printf "export SPARK_API_KEY=%s\n" "$k" | runuser -u agent -- sh -c "umask 077; cat > /home/agent/.secrets" && printf "%s %s\n" "$name" "$(printf %s "$k" | sha256sum | cut -d" " -f1)" >> "$d/client-keys.sha256"'
```

This replaces `agent`'s whole `~/.secrets`, as [pi](pi.md) does when it gives `agent` its key.
Root never writes in `agent`'s home: `runuser` runs `cat` as `agent`.

For another machine, the key is generated there, as the Mac's is in step 8, and sent as in step 9.
On the Spark it becomes a digest line instead of a line in `llama-swap.env`. Set `name` in the
command, as above. **On the Spark:**

```bash
sudo bash -c 'name=example-new-key; d=/etc/local-ai/secrets; case "$name" in ""|*[!a-z0-9-]*) echo "a name is lowercase letters, digits and -" >&2; exit 1 ;; esac; if awk -v n="$name" "\$1 == n {found = 1} END {exit !found}" "$d/client-keys.sha256"; then echo "$name is in client-keys.sha256 already" >&2; exit 1; fi; IFS== read -r _ k; [ -n "$k" ] || { echo "no key in the copy" >&2; exit 1; }; printf "%s %s\n" "$name" "$(printf %s "$k" | sha256sum | cut -d" " -f1)" >> "$d/client-keys.sha256"' < ~/.spark-key-in && rm ~/.spark-key-in
```

**20. Add it to the key list.** **On the Spark**, copy the list, add the new name with its group,
label and account in an editor, as in step 13, and install it again:

```bash
cp /etc/local-ai/keys.yaml ~/keys.yaml
```

```bash
sudo install -o root -g spark-admin -m 0640 ~/keys.yaml /etc/local-ai/keys.yaml && rm ~/keys.yaml
```

Run steps 17 and 18 again, then `make restart-front`.

To retire a key, delete its digest line and its entry in the key list, then `make restart-front`.
To replace one, retire it, then make the new one with the same name. **On the Spark**, with the
key's name in place of `example-new-key`:

```bash
sudo bash -c 'name=example-new-key; case "$name" in ""|*[!a-z0-9-]*) echo "a name is lowercase letters, digits and -" >&2; exit 1 ;; esac; sed -i "/^$name /d" /etc/local-ai/secrets/client-keys.sha256'
```

## Record it

**21. In the vault**, record each secret by reference only: which file, and which variable name.
Never the value. For the Mac's key, note that it lives in the Mac's `~/.secrets` as
`SPARK_API_KEY`. The key list's names are private: they go in the vault too, never in this repo.

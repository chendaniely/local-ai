---
title: "Qwen3.8-Flash-Next, by hand"
description: "The box's biggest model, served by hand with the stack's models stopped: a 76 GiB 3-bit file, an optional MTP speed-up, what to measure, and the restore."
---

*(Added 2026-10-13. Revised 2026-10-14 after its council review, Dan's decisions that day: the
3-bit `UD-IQ3_XXS` file instead of the 2-bit one, the shards' real folder, the memory flags the
registry gives every model, a memory budget and a stop rule, the brake as it really behaves, the
download before the stack stops, a restore for today's box, and MTP from a mainline release
instead of a fork. **Not yet run on this box.** It is the stopgap until the plan's Phase 4a puts
Flash-Next in the registry on Phase 2a's gate, and it is written for today's Phase 1 stack.)*

Qwen3.8-Flash-Next is 125B parameters with 6B active per token, plus 51B of n-gram embeddings and
a 4B MTP head. It is a vision model (image-text-to-text) with hybrid thinking. This is **not the
stack**: it lives in `~/scratch`, runs as you, and needs no new packages. The runs also measure
what Phase 4a needs ([What to record](#what-to-record)).

Run everything on the Spark, as you, inside tmux (`tmux new -As scratch`), unless a step says Mac.

## Why by hand, for now

Beside the always-loaded models it doesn't fit. Rule 9's room for an on-demand model is
min(117 − 43 − 24, 102 − 43) = 50 GiB, and this model needs about 80. On an empty box the room is
min(117 − 24, 102) = 93 GiB, but today's launch check allows only 78 there (`MemAvailable` capped
at the ceiling, less the reserve), so llama-swap would refuse it even alone. So it runs by hand,
with llama-swap stopped. Phase 4a (after Phase 2a) makes it a registry model that loads after
`spark make-room` clears the others, which only Dan can run; this page retires then.

## What fits

File sizes from the model's repository, 2026-10-14. The budget is 93 GiB with the stack stopped,
and the file is only part of it ([The budget](#the-budget)).

| Quant | File | On this box? |
|---|---:|---|
| UD-IQ1_S, UD-IQ1_M | 67.6 / 69.4 GiB | fits; lower quality |
| UD-Q2_K_XL | 73.5 GiB | fits; the fallback |
| **UD-IQ3_XXS** | **76.3 GiB** | **fits: the choice** |
| UD-Q3_K_XL | 83.8 GiB | leaves memory under the brake's warn line |
| UD-IQ4_XS, UD-Q4_K_XL | 87.3 / 103.7 GiB | no |
| Q8_0, BF16 | 175 / 330 GiB | never |

Unsloth's own measurements put `UD-IQ3_XXS` clearly ahead of `UD-Q2_K_XL` for 2.9 GiB more: mean
KL divergence 0.165 against 0.225, and the same top token 85.4% of the time against 82.7%.

## The budget

With llama-swap stopped, about 117 GiB is free, and the reserve keeps 24 of it: 93 GiB for the
model. What it takes, by arithmetic until the first run measures it:

| Part | GiB |
|---|---:|
| Weights, `UD-IQ3_XXS` | 76.3 |
| Vision projector | 0.8 |
| KV cache at 32K tokens (about 27 KiB a token) | 0.8 |
| Prompt cache (`--cache-ram 1024`) | 1.0 |
| Compute buffers and context checkpoints | a few; measured on the first run |
| **Total** | **about 80–83** |

So `free -g` should show about 34–37 GiB available once it's loaded. **Stop the server if
available falls under 28 GiB**, the brake's warn line. Each further 32K of context costs about
0.8 GiB more, so there's room to raise `--ctx-size` once the first run shows the real numbers.

## 1. Check the disk, then download

The stack keeps running while this downloads. **On the Spark:**

```bash
df -h /
```

It needs about 90 GB free. Then, **on the Spark:**

```bash
mkdir -p ~/scratch/models/qwen3.8-flash-next
uvx --from huggingface_hub hf download unsloth/Qwen3.8-Flash-Next-GGUF \
  --local-dir ~/scratch/models/qwen3.8-flash-next \
  --include "UD-IQ3_XXS/*" \
  --include "mmproj-F16.gguf" \
  --include "mtp-Qwen3.8-Flash-Next-Q8_0.gguf"
```

That is the model in three shards under `UD-IQ3_XXS/`, its vision projector, and the MTP head for
[step 5](#mtp-optional): 5 files, 87.0 GB (checked with `--dry-run`, `hf` 2.2.0, 2026-10-14). The
repository isn't gated, so no token is needed.

## 2. Stop the stack

**On the Spark:**

```bash
systemctl stop local-ai-llama-swap
free -g
```

This stops every model: Open WebUI, pi, opencode and `agent`'s models get no answers until
[the restore](#restore-the-stack). Available should be back near 117.

*(Added 2026-10-15, Phase 2a's Task 35: from Phase 2a's cutover, stopping llama-swap by hand isn't
enough, since any start of the front starts it again, through the front's `Wants=`: a crash's
restart, or a connection to 9100. Hold it down instead, and end the hold-down at the restore, as
[Deploy the stack](deploy.md#keeping-llama-swap-stopped) says; never stop the front's socket. Until
the cutover, this step is unchanged.)*

The download left the files in the page cache, and on GB10 reclaiming a full page cache during a
load has stalled other Sparks for minutes (the plan's open risk *Page cache and the launch check*).
Clear it before the load, and note `buff/cache` before and after. **On the Spark:**

```bash
free -g
sync && echo 1 | sudo tee /proc/sys/vm/drop_caches > /dev/null
free -g
```

While llama-swap is stopped, **the brake keeps running but can't unload this server**: it unloads
only through llama-swap. If available falls under 20 GiB it writes its hold, which blocks the
stack's loads after the restore until you release it. earlyoom is the last net, at 12 GiB, and
kills `llama-server` first.

## 3. Serve it

**On the Spark**, with the stack's pinned build, which already knows this architecture
(`qwen4exp`, read in its source; not yet run):

```bash
choom -n 1000 -- /opt/local-ai/bin/llama.cpp/b11146/llama-server \
  -m ~/scratch/models/qwen3.8-flash-next/UD-IQ3_XXS/Qwen3.8-Flash-Next-UD-IQ3_XXS-00001-of-00003.gguf \
  --mmproj ~/scratch/models/qwen3.8-flash-next/mmproj-F16.gguf \
  --alias qwen3.8-flash-next \
  --host 127.0.0.1 --port 8080 \
  --gpu-layers all --ctx-size 32768 --parallel 1 \
  --load-mode none --lazy-mode off --cache-ram 1024 --ctx-checkpoints 8 \
  --no-slots --jinja 2>&1 | tee ~/scratch/flash-next-$(date +%F-%H%M).log
```

- `-m` names the first shard; the server finds the other two beside it.
- `choom -n 1000` makes it the kernel's first choice if memory runs out, as `spark launch` does for
  the stack's on-demand models. If `choom` is missing, drop it: earlyoom picks `llama-server` first
  anyway. *(Corrected 2026-10-14, Phase 2a's Task 28: true only until Task 36's bootstrap takes
  `--prefer` out of earlyoom's arguments. After that, a `llama-server` started without `choom` runs
  at 0, and earlyoom takes the stack's engines, and any job of `agent`'s, before it. Keep `choom`:
  it's util-linux's, on every Ubuntu box.)*
- `--load-mode none` reads the weights into memory instead of mapping the files, as the registry
  does for every model: loads are faster here, and `free -g` then shows what the model really
  holds. `--lazy-mode off` keeps the 51B n-gram table in memory too. Its default reads that table
  from disk on demand, which saves memory but hides it in the page cache; upstream's DGX Spark run
  used it (`-lzm on`), so it's worth a second run once this one is measured.
- `--parallel 1`, `--cache-ram 1024` and `--ctx-checkpoints 8` cap what the server can grow into
  after the load; its defaults would let the prompt cache alone take 8 GiB.
- `--no-slots` keeps its `/slots` page off, as the stack does: it shows each request's prompt size
  to anyone on the box. `--host 127.0.0.1` keeps it to the Spark; the Mac gets in through the SSH
  tunnel.
- The log goes to `~/scratch`; it records the memory each part took ([What to record](#what-to-record)).

Leave it running in its tmux window (detach with Ctrl-b d). **On the Spark**, from a second window:

```bash
curl -s http://127.0.0.1:8080/v1/models | jq -r '.data[].id'
watch -n 2 free -g
```

The first prints `qwen3.8-flash-next`. Keep the second open while you use the model, and stop the
server (Ctrl-C in its window) if available goes under 28.

## 4. Connect from the Mac

**On the Mac**, open a tunnel and leave it running:

```bash
ssh -N -L 8080:127.0.0.1:8080 brightroar
```

Then chat in a browser at <http://localhost:8080> (llama-server's own page), or add an
OpenAI-compatible provider to any client, opencode and pi included: base URL
`http://localhost:8080/v1`, model `qwen3.8-flash-next`, any non-empty API key (this server checks
none).

**Sampling.** It's a hybrid thinking model, with thinking on by default. Unsloth's settings:

| | Thinking | Instruct |
|---|---:|---:|
| temperature | 1.0 | 0.7 |
| top_p | 0.95 | 0.8 |
| top_k | 20 | 20 |
| presence_penalty | 0.0 | 1.5 |

To start the server with thinking off, add `--chat-template-kwargs '{"enable_thinking":false}'`.

**Speed.** llama.cpp's own benchmark on a DGX Spark, with the 4-bit `UD-IQ4_XS` file and one
request at a time, measured about 28 tokens a second, and about 44 with MTP (PR #29761, reported,
not measured here). The log prints each request's speed (`eval time … tokens per second`).

## 5. MTP, optional {#mtp-optional}

MTP (multi-token prediction) drafts a few tokens at a time for the model to check. The pinned b11146
has no MTP for this model; mainline gained it in PR #29761, merged 2026-10-01, and the first
release with it is **b11330** (2026-10-02). It's built in `~/scratch`, not installed. **On the
Spark:**

```bash
mkdir -p ~/scratch && cd ~/scratch
git clone --depth 1 --branch b11330 https://github.com/ggml-org/llama.cpp llama.cpp-b11330
cd llama.cpp-b11330
cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=121a-real -DLLAMA_OPENSSL=OFF -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release -j "$(nproc)" --target llama-server
```

Then run step 3 again with `~/scratch/llama.cpp-b11330/build/bin/llama-server` in place of the
pinned one, and these three lines added before the `2>&1`:

```
  --spec-draft-model ~/scratch/models/qwen3.8-flash-next/mtp-Qwen3.8-Flash-Next-Q8_0.gguf \
  --spec-type draft-mtp --spec-draft-n-max 3 \
```

- The head is the self-contained 3.9 GiB one, downloaded in step 1. Mainline can't use the smaller
  "shared" heads, which borrow the model's own embeddings.
- `--spec-draft-n-max 3` is what PR #29761 measured with, and what the stack's coder uses.
- The head adds about 4 GiB, so available should settle near 30–33. If it goes under 28, use
  `UD-Q2_K_XL` for MTP runs, or a shorter context.

## What to record

Keep each run's log. Phase 4a's settings come from these numbers, measured here:

- `buff/cache` before and after the drop, and how long the load took.
- `free -g` available once loaded, and again after a long answer.
- From the log: the model, KV, compute and checkpoint buffer sizes.
- Tokens a second, with and without MTP.
- Whether the brake held: the restore's status check says.

## Restore the stack

**On the Spark**, stop the server (Ctrl-C in its tmux window, or `pkill -u "$USER" -x llama-server`),
then check the memory came back and the brake isn't holding:

```bash
free -g
/opt/local-ai/app/.venv/bin/spark status
```

Available should be back near 117. If the brake line says it's holding, release it once memory is
back, **on the Spark**: `/opt/local-ai/app/.venv/bin/spark brake --release`. (The deployed app,
not the clone: the clone is on `phase-2a`, whose `make status` misreads today's box.) Then, **on
the Spark:**

```bash
systemctl start local-ai-llama-swap
```

Nothing loads yet: today's stack loads each model on its first request, so `free -g` stays near
117 until something asks.

## Clean up

Keep the files if Phase 4a is close and the disk has room; otherwise:

**1. On the Spark, delete the model and the build:**

```bash
du -sh ~/scratch/models/qwen3.8-flash-next ~/scratch/llama.cpp-b11330 2>/dev/null
rm -rf ~/scratch/models/qwen3.8-flash-next ~/scratch/llama.cpp-b11330
```

The pinned `/opt/local-ai/bin/llama.cpp/b11146/` belongs to the stack: never delete it.

**2. On the Spark, delete what the download cached.** Both are download caches, so deleting them
is safe:

```bash
rm -rf ~/.cache/huggingface/xet
uv cache clean huggingface-hub
```

**3. Close the tunnel (Mac):** Ctrl-C where `ssh -N -L 8080…` runs, and remove the
`http://localhost:8080/v1` provider from any app you added it to.

**4. On the Spark, check the disk:** `df -h /`.

---
title: "Qwen3.8-Flash-Next, on scratch"
description: "The box's biggest model, served by hand with the stack's models stopped: a 79 GB 2-bit file, an optional MTP speed-up, and the restore when you're done."
---

*(Added 2026-10-13. Qwen3.8-Flash-Next is 125B parameters with 6B active per token, plus 51B of
n-gram embeddings and a 4B MTP head — and it doesn't fit beside the stack's resident models
([Why not in the registry](#why-not-in-the-registry)). So this is the scratch route: stop
llama-swap, serve it by hand, and restore the stack when you're done. The file sizes below are
measured from the model's repository on 2026-10-13. Unsloth's guide says to use the latest
llama.cpp, so the pinned b11146 is a try-first, not a guarantee.)*

This is **not the stack**. It lives in `~/scratch`, runs as you, and needs no sudo and no new
packages. It is a vision model (image-text-to-text) with hybrid thinking. Delete it when you're
done ([Clean up](#clean-up)).

Run everything on the Spark, as you, inside tmux (`tmux new -As scratch`), unless a step says Mac.

## Why not in the registry

The launch check admits an on-demand model only if it fits beside the always-loaded ones with the
reserve kept: 78.9 GB for the 2-bit file, 43 GiB of residents (32 + 8 + 3), and the 24 GiB reserve,
against the 102 GiB CUDA-allocatable ceiling — 146 GiB, 44 over. `spark render` refuses it, and at
runtime the gate never evicts a resident to make room, so a load with the residents loaded is
refused by design. It can only run while the stack's models are stopped: this page.

## What fits

Total memory needed, from unsloth's guide; file sizes measured on 2026-10-13. The box shows 121 GiB
of unified memory, and with llama-swap stopped about 117 GiB is free.

| Quant | File (measured) | Total with MTP | On the box? |
|---|---:|---:|---|
| UD-IQ1_S, UD-IQ1_M | 72.5 / 74.5 GB | 76 GB | yes, tight |
| **UD-Q2_K_XL** | **78.9 GB** | **80 GB** | **yes — the choice** |
| UD-IQ3_XXS, UD-Q3_K_XL | 81.9 / 90.0 GB | 91 GB | yes, short context only |
| UD-IQ4_XS, UD-Q4_K_XL | 93.7 / 112.3 GB | 97–115 GB | no |
| Q8_0, BF16 | 188.3 / ~375 GB | 200 GB+ | never |

Unsloth recommends at least the 2-bit dynamic quant, `UD-Q2_K_XL`, to balance size and accuracy.
Context costs memory on top of the file, and the model's native 262K (extensible to 1M) context is
irrelevant here: start at 32K.

## 1. Stop the stack

**On the Spark:**

```bash
systemctl stop local-ai-llama-swap
free -g    # available back near 117
```

This stops every model. While it's stopped, nothing is served, the brake has nothing to hold, and
earlyoom stays the only net — don't start anything else big.

## 2. Download the model

**On the Spark:**

```bash
mkdir -p ~/scratch/models/qwen3.8-flash-next
uvx --from huggingface_hub hf download unsloth/Qwen3.8-Flash-Next-GGUF \
  --local-dir ~/scratch/models/qwen3.8-flash-next \
  --include "*UD-Q2_K_XL*" \
  --include "*mmproj-F16*" \
  --include "*mtp-Qwen3.8-Flash-Next-shared-Q8_0.gguf*"
```

That is the 2-bit model in three shards (78.9 GB), its vision projector (0.9 GB), and the shared
MTP module (2.8 GB, for [MTP](#5-mtp-optional-13-to-17x-faster)) — about 83 GB on the 1 TB drive.
The repository isn't gated, so no token is needed.

## 3. Serve it

**On the Spark**, try the stack's pinned build first — it's already on the box:

```bash
/opt/local-ai/bin/llama.cpp/b11146/llama-server \
  -m ~/scratch/models/qwen3.8-flash-next/Qwen3.8-Flash-Next-UD-Q2_K_XL-00001-of-00003.gguf \
  --mmproj ~/scratch/models/qwen3.8-flash-next/mmproj-F16.gguf \
  --host 127.0.0.1 --port 8080 \
  -ngl 999 -c 32768 --jinja \
  --alias qwen3.8-flash-next
```

- `-m` names the first shard; the server loads the other two from the same folder.
- `--mmproj` enables image input. `--host 127.0.0.1` keeps it to the Spark; the Mac gets in
  through the SSH tunnel in step 4.
- If it fails with "unknown architecture" (this is a new architecture, and unsloth's guide says to
  use the latest llama.cpp), build master in `~/scratch` — [A scratch model to tinker with](scratch-model.md),
  step 1, about two minutes — and run `~/scratch/llama.cpp/build/bin/llama-server` instead.

Leave it running in its tmux window (detach with Ctrl-b d). **On the Spark**, from a second window:

```bash
curl -s http://127.0.0.1:8080/v1/models | jq -r '.data[].id'   # qwen3.8-flash-next, the alias
free -g                                                          # available down near 90
dcgmi dmon -e 203,204,155,150                                    # GPU: util %, clock, watts, °C
```

## 4. Connect from the Mac

**On the Mac**, open a tunnel and leave it running:

```bash
ssh -N -L 8080:127.0.0.1:8080 brightroar
```

Then chat in a browser at <http://localhost:8080> (llama-server's own page), or point any
OpenAI-compatible client at base URL `http://localhost:8080/v1`, model name `qwen3.8-flash-next`,
any non-empty API key.

**Sampling.** It's a hybrid thinking model; thinking is on by default. Unsloth's settings:

| | Thinking | Instruct |
|---|---:|---:|
| temperature | 1.0 | 0.7 |
| top_p | 0.95 | 0.8 |
| top_k | 20 | 20 |
| presence_penalty | 0.0 | 1.5 |

To turn thinking off from the command line: `--chat-template-kwargs '{"enable_thinking":false}'`.

**Speed.** Generation tracks the 6B active parameters, and GB10 is bound by memory bandwidth, so
expect the fast band for big models. Images decode through the vision projector.

## 5. MTP, optional: 1.3 to 1.7x faster

MTP (multi-token prediction) lets the model draft several tokens at once; unsloth measures 1.3–1.7x
on this model. It needs the `qwen4exp/mtp` branch of Daniel Hanchen's llama.cpp fork — the pinned
b11146 won't do it — and 1–2 GB of headroom.

**On the Spark, build the fork:**

```bash
mkdir -p ~/scratch && cd ~/scratch
git clone --branch qwen4exp/mtp https://github.com/danielhanchen/llama.cpp llama.cpp-mtp
cd llama.cpp-mtp
cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=121a-real -DLLAMA_OPENSSL=OFF -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release -j "$(nproc)" --target llama-server
```

Then restart the server (step 3) with this build, and add the shared MTP module — already
downloaded in step 2:

```
  -md ~/scratch/models/qwen3.8-flash-next/MTP/mtp-Qwen3.8-Flash-Next-shared-Q8_0.gguf \
  --spec-type draft-mtp --spec-draft-n-max 5 \
```

`--spec-draft-n-max` is how many tokens the draft proposes per step; the guide uses 5, and 1–6 can
be tried for the fastest on this box. The shared module reuses the main model's token embeddings,
which is why it's 2.8 GB instead of 4.1.

## Restore the stack

**On the Spark**, stop the server (Ctrl-C in its tmux window, or
`pkill -u "$USER" -x llama-server`), then:

```bash
systemctl start local-ai-llama-swap
make status
free -g    # available back near 74: 117 minus the 43 of residents
```

The residents load again on start, and the gate, brake and front come back with llama-swap.

## Clean up

**1. On the Spark, delete the model and the build:**

```bash
du -sh ~/scratch/models/qwen3.8-flash-next ~/scratch/llama.cpp-mtp ~/scratch/llama.cpp 2>/dev/null
rm -rf ~/scratch/models/qwen3.8-flash-next
rm -rf ~/scratch/llama.cpp-mtp ~/scratch/llama.cpp   # only the ones you built
```

The pinned `/opt/local-ai/bin/llama.cpp/b11146/` belongs to the stack: never delete it.

**2. On the Spark, delete what the downloads cached.** Both are download caches, so deleting them
is safe:

```bash
rm -rf ~/.cache/huggingface/xet
uv cache clean huggingface-hub
```

**3. Close the tunnel (Mac):** Ctrl-C where `ssh -N -L 8080…` runs, and remove the
`http://localhost:8080/v1` provider from any app you added it to.

**4. On the Spark, check the disk:** `df -h /`.

# Notes: `cosmicbboy/local-ai` (Niels Bantilan)

Reference notes on **[github.com/cosmicbboy/local-ai](https://github.com/cosmicbboy/local-ai)**,
read at commit `d32fab0` on 2026-09-23.

**Framing:** that repo documents a **2× DGX Spark** cluster. Every `vllm serve` in it is
`--tensor-parallel-size 2 --nnodes 2`; there is no single-node config to copy. What transfers to a
single Spark is the **GB10 hardware knowledge and operational hygiene** — which is most of the
value. Section H lists what's deliberately excluded.

Confidence labels:

- **[verified]** — measured by Niels on real GB10 hardware, quoted from the repo.
- **[adapted]** — translated to one node here. Reasonable, but untested by anyone.

---

## A. Four hardware facts to internalize first

**[verified]**

1. **`nvidia-smi` reports `[N/A]` for memory.** GB10's GPU allocates from the same LPDDR5X pool as
   the CPU, so `nvidia-smi` cannot see it.

   ```bash
   free -g                       # the only real memory view on this box
   ```

   Stated three separate times across his runbooks — a fair signal of what it cost to learn.

2. **~121 GiB unified memory, total, for everything.** Weights, KV cache, OS, and any other
   process draw from one pool. There is no separate VRAM to fall back on.

3. **Compute capability 12.1 → `sm_121`.** Anything compiled from source needs this, or it
   silently builds for the wrong target:

   ```bash
   -DCMAKE_CUDA_ARCHITECTURES=121        # cmake
   TORCH_CUDA_ARCH_LIST=12.1a            # torch
   FLASHINFER_CUDA_ARCH_LIST=12.1a       # flashinfer
   CUTE_DSL_ARCH=sm_121a                 # cutlass DSL
   ```

4. **Base platform** on his boxes: Ubuntu 24.04 LTS, driver 580.x, CUDA 13.0, kernel
   `6.17.0-1014-nvidia`.

---

## B. First hour

```bash
# inventory
uname -r
nvidia-smi --query-gpu=name,driver_version --format=csv,noheader
free -g
df -h ~
nvidia-smi -L                              # expect: GPU 0: NVIDIA GB10 (UUID: ...)

# docker without sudo
sudo usermod -aG docker $USER
sudo systemctl restart docker
# log out / back in, then:
docker run --rm --gpus all nvcr.io/nvidia/vllm:25.11-py3 nvidia-smi -L
```

**[verified] Kill the memory competition first.** His known-issue #2, and it bites a single box
*harder* than a pair — on one Spark there is nowhere else for the memory to come from:

```bash
systemctl --user list-unit-files | grep -iE 'lmstudio|ollama'
systemctl --user disable --now lmstudio     # if present
systemctl --user disable --now ollama       # if present
```

He found `lmstudio.service` enabled at boot loading two models into unified memory before his
server started. Its `ExecStart` was even failing (203/EXEC) — but the model-loading
`ExecStartPre`s still ran.

**Headless access.** He drives the box from a Mac over Tailscale; `ufw` is active on his node:

```bash
sudo tailscale up
sudo ufw allow in on tailscale0 to any port 8888 proto tcp   # tailnet-only, not 0.0.0.0
```

**For a service that survives reboot with no login:**

```bash
loginctl enable-linger $USER
```

---

## C. What actually fits in 121 GiB

**[verified]** His model inventory, annotated for one Spark. Keep **≥6 GiB `MemAvailable`** free,
so budget ~105–110 GiB for weights + KV cache combined.

| Model | Size | One Spark? |
|---|---|---|
| `deepseek-ai/DeepSeek-V4-Flash-0731` (FP8) | 155 GB | **No** — this is why he bought the pair |
| `unsloth/Qwen3.8-Flash-Next-GGUF` | 111.3 GB | **No** — he confirms it cannot fit one |
| `Qwen/Qwen3-Next-80B-A3B-Thinking-FP8` | 76 GB | Yes, tight — little KV headroom |
| `Qwen/Qwen3.6-35B-A3B` | 66 GB | Yes |
| `unsloth/gpt-oss-120b` | 60 GB | Yes |
| `unsloth/Qwen3-Next-80B-A3B-Instruct-bnb-4bit` | 39 GB | Yes, comfortable |
| `unsloth/Qwen3.6-35B-A3B-MTP-GGUF` | 22 GB | Yes |
| `unsloth/Qwen3.8-27B-NVFP4` | 21 GB | Yes — his smoke-test model |
| `stabilityai/stable-diffusion-xl-base-1.0` | 6 GB | Yes |

**[verified] KV cache arithmetic:** each `0.01` of `--gpu-memory-utilization` ≈ **1.2 GiB** ≈
**165K KV tokens**. He runs `0.80`, notes `0.78` as a practical floor; `0.85` hit an
`ibv_reg_mr` ENOMEM on his older image.

**[verified] Reassuring data point:** two-node TP=2 was *slower* than single-node on a 22 GB
model, and he calls that "the correct and expected result — cross-node TP is for models that
don't fit on one Spark, not for speed." For anything in the table that fits, **one Spark is the
right answer**, not a compromise.

### Throughput calibration **[verified]**

Set expectations from his measurements — this is a memory-bandwidth-bound box, not an H100:

| Setup | Model | Decode |
|---|---|---|
| llama.cpp, single-node-ish | Qwen3.6-35B-A3B Q4 (21 GB) | **48 tok/s** |
| llama.cpp RPC across 2 Sparks | Qwen3.8-Flash-Next Q4 (111 GB) | **23.6 tok/s** |
| vLLM TP=2 + spec decoding | DeepSeek-V4-Flash (155 GB) | **~75 tok/s** code, 35 tok/s prose |

Note the 75 tok/s figure needs **both** Sparks *and* DSpark speculative decoding (k=5, mean accept
length ~4.7). Tens of tok/s is the realistic band for one Spark on a large MoE.

---

## D. Serving it

### Option 1 — vLLM in the NGC container **[adapted]**

His image is `nvcr.io/nvidia/vllm:25.11-py3`. Two-node flags stripped:

```bash
docker run -d --name vllm \
  --gpus all --network host --ipc host --shm-size 16g \
  --ulimit memlock=-1 --ulimit stack=67108864 \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  -e PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
  -e TORCH_CUDA_ARCH_LIST=12.1a \
  nvcr.io/nvidia/vllm:25.11-py3 \
  vllm serve unsloth/Qwen3.8-27B-NVFP4 \
    --served-model-name qwen-27b \
    --tensor-parallel-size 1 \
    --gpu-memory-utilization 0.80 \
    --max-model-len 8192 \
    --enable-prefix-caching --enable-chunked-prefill \
    --host 0.0.0.0 --port 8888

docker logs -f vllm
```

Dropped vs. his: `--distributed-executor-backend ray`, `--nnodes`, `--node-rank`, `--master-addr`,
all `NCCL_*` env, `--device /dev/infiniband`. Start with the 21 GB model to prove plumbing before
reaching for a 76 GB one.

**[verified] First startup takes minutes** — CUDA graph compile for `sm_121`. Not a hang.

### Option 2 — llama.cpp from source **[verified build, adapted serve]**

```bash
cd ~ && git clone --depth 1 https://github.com/ggml-org/llama.cpp
cd llama.cpp
export PATH=/usr/local/cuda/bin:$PATH
cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=121 \
      -DLLAMA_CURL=OFF -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release -j "$(nproc)" --target llama-server llama-cli
```

**[verified] Budget 15–25 min** — CC template instantiations dominate. (Skip `-DGGML_RPC=ON` and
the `ggml-rpc-server` target; those are for splitting across two boxes.)

### Option 3 — LM Studio

**[verified]** LM Studio's bundled llama.cpp is only a dead end for **multi-node** — no
`rpc-server`, and its `llama-server` is a 19 KB loader stub. For a single Spark it's fine, and he
keeps using it as a client of OpenAI-compatible endpoints. Per §B, don't leave it holding memory
while something else is serving.

---

## E. Two tricks worth stealing verbatim

### E.1 The `drop_caches` sidecar **[verified]**

His best operational find, and it applies fully to one node. Loading a large checkpoint wedges:
progress freezes at e.g. `Loading safetensors checkpoint shards: 10/48` for ~20 minutes, no
errors, `py-spy` showing 100% CPU in `cuMemcpyHtoDAsync_v2`. His note: *"GB10 driver does not
reclaim page cache fast enough; MemFree pinned at 8 GB."*

Run during model load **[adapted to one host]**:

```bash
docker run -d --rm --privileged --name dropcaches \
  --entrypoint sh nvcr.io/nvidia/vllm:25.11-py3 \
  -c 'while true; do sync; echo 3 > /proc/sys/vm/drop_caches; sleep 5; done'

# ... start your server, wait for it to come up ...

docker rm -f dropcaches
```

He wraps this with `trap ... EXIT` so the sidecar always gets cleaned up. Worth copying.

### E.2 Hardlinked HF cache **[verified]**

If a checkpoint lives in `~/models/` but a tool insists on the HF hub cache layout, hardlink
rather than copy — zero extra disk on a 155 GB checkpoint:

```bash
REV=<commit-sha>
SRC=$HOME/models/<your-model-dir>
REPO=$HOME/.cache/huggingface/hub/models--<org>--<name>
SNAP=$REPO/snapshots/$REV
mkdir -p "$SNAP" "$REPO/refs"
echo -n "$REV" > "$REPO/refs/main"
for f in "$SRC"/*; do b=$(basename "$f"); [ -e "$SNAP/$b" ] || ln "$f" "$SNAP/$b"; done
```

**Caveat he flags:** the two paths now share inodes. Editing a file in place changes both, and
deleting the source is not safe in the way you'd expect.

---

## F. Making it always-on

**[verified]** The design lesson, which is harness-agnostic:

- `systemd --user` unit, `Type=oneshot` + `RemainAfterExit=yes`, container restart policy `no`
  (systemd owns lifecycle, not dockerd).
- `loginctl enable-linger` so it starts at boot with no login.
- **`active (exited)` does not mean healthy.** It only means startup succeeded. His service died
  mid-run on 2026-09-19 and the unit still read `active` while the API was gone for hours.
- **HTTP 200 does not mean healthy either.** A wedged server still answers `GET /v1/models`. His
  watchdog runs two probes: liveness every 60 s, plus a real **generation probe**
  (`POST /v1/completions`, `max_tokens=8`, hard deadline) every 10 min.
- Give-up policy: 3 consecutive failures → cold restart; 3 restarts in 3 h → stop restarting and
  only report. Resets after 1 h healthy. Prevents restart-looping a broken box for days.

`dgx-spark/deepseek-v4-vllm/scripts/deepseek-v4-flash-watchdog.sh` (219 lines) is worth reading
even if not run as-is.

---

## G. Gotchas cheat sheet

| Gotcha | Detail |
|---|---|
| **`pkill -f <name>` over ssh kills your own session** | `-f` matches the ssh wrapper's own command line. Use a script file on the remote host, or `pkill -x <name>` (bare name). He calls this "responsible for the most confusing failures in all three runbooks." |
| **llama.cpp readiness wording** | Grep for `model loaded\|listening on http://` — the string `server is listening` never appears and will hang a wait-loop forever. |
| **Reasoning models eat your `max_tokens`** | Qwen3.x / DeepSeek-V4 consume the budget in `reasoning_content`, leaving `content` empty. Pass `"chat_template_kwargs":{"enable_thinking":false}` or raise `max_tokens`. |
| **`docker compose restart` ignores env changes** | Always stop, then start. |
| **Persist JIT caches** | Mount `~/.cache/huggingface` into the container. FlashInfer autotune is ~2 min on first boot, cached after. |
| **No API key by default** | vLLM binds `0.0.0.0` with no auth — anyone on your LAN/tailnet can use it. Set `VLLM_API_KEY`. His own known-risk #4; don't inherit it. |
| **Benchmark script** | `scripts/bench-dspark.py` is 29 lines, streaming, reports TTFT and decode tok/s separately. Takes `--model`, so it works against any OpenAI-compatible endpoint. Worth grabbing. |

---

## H. Deliberately excluded — revisit only with a second Spark

None of this can affect a single node:

- The **ConnectX-7 power-throttle bug** (hot-plugging QSFP → silent 13 Gb/s cap; fix is rebooting
  with the cable already in). His best find, but it's a NIC-fabric issue.
- **RoCE / NCCL setup** — GID index 3, `NCCL_IB_HCA`, `ib_write_bw`, MTU 9000, and the "200 Gb is
  aggregate across both ports, ~100 Gb/s per port is healthy" finding.
- **The `ibv_reg_mr_iova2` ~200 memory-region ceiling** and the `NCCL_MAX_NCHANNELS=8` workaround.
  An RDMA registration limit, reachable only when NCCL opens cross-node communicators.
- **Ray head/worker, NFS-exported HF cache, llama.cpp RPC, SGLang multi-node** — pair-only.
- **The `deepseek-v4-vllm/` service itself** — 155 GB checkpoint, needs both boxes.

If a second Spark ever arrives, `dgx-spark/dual-dgx/README.md` §0.3 is the first thing to read,
*before plugging anything in*.

---

## What makes his repo unusually good

Two things most runbooks lack, worth imitating in this one:

- **A "Ruled out on this hardware (don't re-tread)" table** — MTU, cable, link negotiation, QP
  tuning, CPU governor, each with measurements showing they *weren't* the cause. Negative results,
  recorded. That's the expensive part to learn and the first thing usually lost.
- **`config/DEPLOYED.env.dspark`** — the literal live config, 606 lines, with a separate
  `env-deltas.md` diffing it against upstream's example in 9 places. Ground truth you can diff
  against.

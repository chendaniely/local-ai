---
title: "A scratch model to tinker with"
description: "Temporary: build llama.cpp in your home folder, serve one model, and use it from the Mac through an SSH tunnel — until Phase 1's stack replaces it."
---

This is **not the stack**. It is a model you run by hand to tinker with while Phase 1 is being
built. It lives in `~/scratch`, runs as you, and needs no sudo and no new packages. Delete it when
Phase 1 serves models ([Clean up](#clean-up)).

**Guardrails until Phase 1's brake exists:**

- **One model at a time, well under memory.** Keep it to about 60–70 GB. Overcommitting GB10's
  shared memory can freeze the box outright. earlyoom is the only net until Phase 1, and it kills
  model engines first.
- **Stop it before Phase 1 loads real models**, so the two never share memory.
- **Mind the disk.** Models share the 1 TB drive with everything else.

Run everything on the Spark, as you, inside tmux (`tmux new -As scratch`), unless a step says Mac.

Two ways to serve, and one at a time, never both:

- **A. llama.cpp** (steps 1–5): builds in two minutes, runs as you with no sudo, and takes only the
  memory the model needs. Start here.
- **B. vLLM from NVIDIA's NGC container** ([Option B](#option-b-vllm-from-nvidias-ngc-container)):
  NVIDIA's build for this hardware. Use it to try vLLM itself, many requests at once, or a model
  whose FP8/NVFP4 version only vLLM serves well. It needs `sudo docker`.

## 1. Build llama.cpp

```bash
mkdir -p ~/scratch && cd ~/scratch
git clone --depth 1 https://github.com/ggml-org/llama.cpp
cd llama.cpp
cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=121a-real -DLLAMA_OPENSSL=OFF -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release -j "$(nproc)" --target llama-server llama-cli
```

- `121a-real` is the GB10's architecture, as NVIDIA's own llama.cpp playbook builds it. Without the
  right one, the build silently targets the wrong GPU.
- `LLAMA_OPENSSL=OFF`: llama.cpp's built-in Hugging Face download needs OpenSSL's headers, which
  aren't installed. Step 2 downloads with Hugging Face's own tool instead, so nothing is missing.
- The build takes about two minutes on the Spark's 20 cores, mostly compiling CUDA kernels.
- Check that it sees the GPU: `build/bin/llama-server --list-devices` shows `CUDA0: NVIDIA GB10`.
  (Tested 2026-09-24 with llama.cpp `1ab7e5a`: built, found the GB10, served a model.)

## 2. Download a model

Hugging Face's CLI runs through uv, so nothing gets installed. Pick one:

| Model | File | Size | Good for |
|---|---|---|---|
| Qwen3-8B | `Qwen3-8B-Q4_K_M.gguf` from `Qwen/Qwen3-8B-GGUF` | ~5 GB | a quick first try |
| Gemma 4 26B-A4B | `gemma-4-26B-A4B-it-Q4_0.gguf` from `ggml-org/gemma-4-26B-A4B-it-GGUF` | ~14 GB | one of Phase 1's real models |

```bash
mkdir -p ~/scratch/models
uvx --from huggingface_hub hf download Qwen/Qwen3-8B-GGUF Qwen3-8B-Q4_K_M.gguf --local-dir ~/scratch/models
# or
uvx --from huggingface_hub hf download ggml-org/gemma-4-26B-A4B-it-GGUF gemma-4-26B-A4B-it-Q4_0.gguf --local-dir ~/scratch/models
```

Neither repository is gated, so no token is needed. Your Hugging Face token stays in the stack's
secret file.

## 3. Serve it

```bash
~/scratch/llama.cpp/build/bin/llama-server \
  -m ~/scratch/models/Qwen3-8B-Q4_K_M.gguf --alias qwen3-8b \
  --host 127.0.0.1 --port 8080 \
  -ngl 999 -c 32768 --jinja
```

- `--host 127.0.0.1`: only the Spark itself can reach it. The Mac gets in through the SSH tunnel in
  step 4, and nothing opens on the LAN or the tailnet.
- `-ngl 999` puts every layer on the GPU; `-c 32768` sets a 32K-token context window.
- `--alias` is the model name clients see and send. Without it, the name is the file's full path.
- `--jinja` uses the model's own chat template, which tool-calling clients such as pi need.

Leave it running in its tmux window (detach with Ctrl-b d). To check it from a second window:

```bash
curl -s http://127.0.0.1:8080/v1/models | jq -r '.data[].id'   # qwen3-8b, the alias
free -g                                                          # memory it's holding
```

## 4. Connect from the Mac

**On the Mac**, open a tunnel and leave it running:

```bash
ssh -N -L 8080:127.0.0.1:8080 brightroar
```

This uses the `brightroar` alias from [SSH from the Mac](ssh.md). It works at home and away, and
stops when you press Ctrl-C.

Then, on the Mac:

- **Chat in a browser:** <http://localhost:8080>. llama-server has its own chat page.
- **The API**, for anything that speaks OpenAI's API: base URL `http://localhost:8080/v1`, API
  key any non-empty string (the server doesn't check one), and the model name (the `--alias`).

## 5. Clients

- **pi, Open WebUI on the Mac, and other chat or agent apps:** add an "OpenAI-compatible"
  provider with the base URL and model name above. Most apps call it a custom OpenAI endpoint.
- **Ollama:** can't use this. Ollama is itself a model server, and its apps talk to their own
  server rather than to llama-server.
- **Claude Code and Claude Desktop: never.** Pointing them at the Spark would mean changing the
  Claude path (`ANTHROPIC_BASE_URL` or a proxy), and the repo's first rule is that the Claude path
  stays untouched.

## Option B: vLLM from NVIDIA's NGC container

*Not yet tested on this box.* The commands follow NVIDIA's DGX Spark playbooks; the image's tag and
its arm64 build were checked on 2026-09-25.

Stop any llama.cpp server first, so the two never share memory.

**B1. Pull the image** (about 11 GB compressed, public, no NGC login needed):

```bash
sudo docker pull nvcr.io/nvidia/vllm:26.08-py3
```

**B2. Save the compose file.** vLLM uses a model's original Hugging Face repo (safetensors), not
GGUF. Everything you're likely to change is in `x-settings` at the top:

```bash
mkdir -p ~/scratch/vllm && nano ~/scratch/vllm/compose.yaml
```

```yaml
# Scratch vLLM (not the stack). Edit the values in `x-settings`, then: sudo docker compose up
x-settings:
  model: &model Qwen/Qwen3-8B            # Hugging Face repo (safetensors, not GGUF)
  name: &name qwen3-8b                    # the name clients send
  memory: &memory "0.4"                   # share of GB10's memory vLLM reserves; 0.5 at most here
  context: &context "32768"               # max tokens per request
  tool_parser: &tool_parser hermes        # tool calling for Qwen3; other families differ

services:
  vllm:
    image: nvcr.io/nvidia/vllm:26.08-py3
    container_name: scratch-vllm
    command:
      - vllm
      - serve
      - *model
      - --served-model-name
      - *name
      - --gpu-memory-utilization
      - *memory
      - --max-model-len
      - *context
      - --enable-auto-tool-choice
      - --tool-call-parser
      - *tool_parser
    ports:
      - "127.0.0.1:8000:8000"             # the Spark only; the Mac uses the SSH tunnel
    ipc: host
    ulimits:
      memlock: -1
      stack: 67108864
    volumes:
      - ./hf:/root/.cache/huggingface     # downloads land in ~/scratch/vllm/hf (root-owned)
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
```

- **`memory` is the important one.** vLLM reserves that share of GPU memory at start, whatever the
  model needs. On GB10 that is the whole machine's memory, and vLLM's default of 0.9 would take about
  110 GB. 0.4 reserves about 50 GB. Raise it only as far as the scratch guardrail allows (0.5 at most).
- `127.0.0.1:8000` publishes the port on the Spark only; the Mac uses the tunnel.
- `name` is what clients send, like llama.cpp's `--alias`.
- `tool_parser` enables tool calling, which pi needs. `hermes` is right for Qwen3; other families
  use a different parser (see vLLM's docs).
- If the GPU request is refused, replace the whole `deploy:` block with
  `devices: ["nvidia.com/gpu=all"]`. The Spark has NVIDIA's device spec at `/var/run/cdi/nvidia.yaml`.

`sudo docker compose config` (in that folder) checks the file without starting anything.

**B3. Serve it:**

```bash
cd ~/scratch/vllm
sudo docker compose up            # stays in the foreground; Ctrl-C stops it
# or: sudo docker compose up -d && sudo docker compose logs -f
```

Startup takes a few minutes: download, load, then compile. It is ready when the log says
`Application startup complete`. Check it from another window:

```bash
curl -s http://127.0.0.1:8000/v1/models | jq -r '.data[].id'   # qwen3-8b
free -g
```

To switch models, edit `x-settings` and run `sudo docker compose up` again.

**B4. From the Mac:** the same as step 4, on port 8000:
`ssh -N -L 8000:127.0.0.1:8000 brightroar`, then base URL `http://localhost:8000/v1`. vLLM has no chat
page, so use a client.

**B5. Stop it:** Ctrl-C, or `sudo docker compose down` in `~/scratch/vllm`.

## What you can run

**One model per server.** The chat page and the API see only the model `llama-server` was started
with. To switch, restart it with another file ([Switch models](#switch-models)).

**From Hugging Face:**

- **GGUF files only.** Most popular models have GGUF versions, from their makers or from
  `ggml-org`, `unsloth` or `bartowski`. A model published only as safetensors needs converting first.
- **An architecture your build supports.** Mainstream families work: Llama, Qwen, Gemma, Mistral,
  DeepSeek, GLM, Phi. A brand-new one may need a newer llama.cpp. If loading fails with "unknown
  architecture", run `git pull` in `~/scratch/llama.cpp` and repeat step 1's two `cmake` lines.
- **Gated models** (some Llama, Gemma and Mistral repos) need their license accepted on the
  model's page, and your token for the download. Give the token for that one command only, rather
  than `hf auth login`, which saves it in a file:

  ```bash
  read -rsp "HF token: " HF_TOKEN; echo; export HF_TOKEN
  uvx --from huggingface_hub hf download <repo> <file> --local-dir ~/scratch/models
  unset HF_TOKEN
  ```

- **The license** on each model's page says what you may use it for.

**What fits.** A 4-bit file is about 0.6 GB per billion parameters, plus a few GB for context:

| Model size | 4-bit file | In scratch? |
|---|---|---|
| 8B | ~5 GB | easily |
| 32B | ~20 GB | yes |
| 70B | ~42 GB | yes, slowly |
| 120B-class mixture-of-experts | ~65 GB | at the limit (keep scratch under about 60–70 GB) |
| over ~110 GB | — | never: it can't fit, and trying risks freezing the box |

Context costs memory too: a long `-c` (128K tokens, say) can add many GB on top of the file.

**What llama.cpp does and doesn't do:**

| | |
|---|---|
| Text chat, coding, tool calling | yes |
| Embeddings, reranking | yes (`--embedding`, `--reranking`) |
| Images as input | yes, for vision models that ship an `mmproj` file |
| Speech-to-text | no: that's whisper.cpp (Phase 1) |
| Image generation | no: diffusion models need other tools (ComfyUI, in the plan's backlog) |
| Training or fine-tuning | no (it can load LoRA adapters, not train them) |
| Many users at once | weaker: it's built for one person; vLLM is the high-concurrency option |

**Speed and quality:**

- **Generation speed tracks active parameters,** because GB10 is bound by memory bandwidth.
  Mixture-of-experts models (Gemma 4 26B-A4B, with about 4B active) feel fast. A dense 70B runs
  at single-digit tokens per second. Tens of tokens per second is the realistic band for big models.
- **Quantization trades quality for size.** 4-bit (`Q4_K_M`, `Q4_0`) is the usual sweet spot,
  8-bit is near-lossless at twice the size, and quality drops noticeably below 4-bit.
- **Tool calling depends on the model's chat template.** Some community GGUF files have imperfect
  templates, and agents like pi then misbehave. Makers' own or `ggml-org` files are the safest.

## Switch models

Ctrl-C the server, download another file (step 2), and start step 3 again with the new `-m`. One
model at a time.

## Clean up

Do this when Phase 1's stack serves models, or any time you're done with scratch. Each step says
where it runs.

**1. Stop the server (Spark).** Press Ctrl-C in its tmux window. If you've lost track of it:

```bash
pkill -u "$USER" -x llama-server
pgrep -a llama-server || echo "no server running"
tmux kill-session -t scratch 2>/dev/null
```

**2. Check that the memory came back (Spark):**

```bash
free -g                                                               # available back near 118
nvidia-smi --query-compute-apps=pid,process_name --format=csv,noheader   # prints nothing
```

**3. Close the tunnel and forget the endpoint (Mac).** Press Ctrl-C where `ssh -N -L 8080…` runs,
or `pkill -f 'ssh -N -L 8080'`. Remove the custom `http://localhost:8080/v1` provider from any app
you added it to.

**4. Delete the build and the models (Spark):**

```bash
du -sh ~/scratch    # how much this frees
rm -rf ~/scratch
```

**5. Delete what the downloads cached (Spark).** Both are download caches, so deleting them is safe:

```bash
rm -rf ~/.cache/huggingface/xet      # Hugging Face's chunk cache from the downloads
uv cache clean huggingface-hub       # uv's cached copy of the Hugging Face CLI
```

Leave the rest of `~/.cache/huggingface/hub` unless you know what's in it. Other tools share it:
`du -sh ~/.cache/huggingface/hub/models--*` lists each model in it and its size, and you can delete
the ones you no longer want. If you gave a token with `HF_TOKEN` (see gated models), it was never
saved, so there's nothing to remove.

**If you used Option B (Spark):** its downloads are owned by root, and the image stays until removed:

```bash
(cd ~/scratch/vllm && sudo docker compose down)     # if still running
sudo rm -rf ~/scratch/vllm/hf                        # the models vLLM downloaded (root-owned)
sudo docker rmi nvcr.io/nvidia/vllm:26.08-py3        # the image, about 20 GB on disk
sudo docker ps -a && sudo docker images             # nothing scratch-related left
```

Do this before step 4, because `rm -rf ~/scratch` can't delete root-owned files.

**6. Check the disk (Spark):** `df -h /`.

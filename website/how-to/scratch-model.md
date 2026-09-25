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

## Switch models

Ctrl-C the server, download another file (step 2), and start step 3 again with the new `-m`. One
model at a time.

## Clean up

When Phase 1's stack serves models, stop the server (Ctrl-C), close the tunnel, and delete the
scratch folder, models included:

```bash
rm -rf ~/scratch
```

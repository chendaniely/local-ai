---
title: "The fast coder, by hand"
description: "Qwen3.8-27B on SGLang with NVFP4 and a DFlash2 drafter, run by hand in Docker: bounded memory, a key on its admin routes, what to measure, and how to stop it."
---

*(Added 2026-10-14, Dan's decision: test the fast coder now, before the plan's Phase 4a puts it in
the registry on Phase 2a's gate. **Not yet run on this box.** The setup is hasso5703's
[dgx-spark-qwen38](https://github.com/hasso5703/dgx-spark-qwen38), read at `3849269`: its
`install.sh` and its SGLang unit. The memory caps and the key handling were read in SGLang
v0.5.21's source. It differs from hasso5703's unit in five ways: memory capped by tokens rather
than by a share of free memory, a key on every route, no host network, no remote code, and no
restart. It is written for today's Phase 1 stack.)*

*(Added 2026-10-15: it ran on 2026-10-14, alone, with the stack stopped: 41.7 tokens a second
against the llama.cpp coder's 23.8, about 45 GiB, and about 3 minutes from start to ready
([Speed tests](../reference/speed-tests.md#the-fast-coder-against-todays-coder)). For a repeat run,
[Run it again](#run-it-again) has only the commands it needs.)*

This is **not the stack**: a container you start and stop yourself, beside it or instead of it. It
serves Qwen3.8-27B as RadixArk's NVFP4 checkpoint, with a DFlash2 drafter that proposes 16 tokens
at a time, on SGLang v0.5.21. hasso5703 reports 71.4 tokens a second (greedy median on its
`bench.sh`) on a Spark; the deployed llama.cpp coder is the baseline to beat
([What to record](#what-to-record)).

Run everything on the Spark, as you, inside tmux (`tmux new -As coder`), unless a step says Mac.

## Run it again {#run-it-again}

*(Added 2026-10-15, Dan's decision: the fast coder and Flash-Next stay usable by hand while Phases
2a and 4a are built, so each page starts with its repeat run. It needs steps 1–3 done once, as on
2026-10-14: the image, the model, the drafter and the key on the box, and the `spark-sglang`
provider in opencode ([step 6](#connect-from-the-mac)).)*

**Ask the Spark's build session first.** Most of Phase 2a's remaining tasks measure or drill the
box: the cutover, a cold boot, the memory drills, the soak and the brake's tuning. A container
holding 45 GiB during one of them spoils the measurement or trips the brake. The session's code and
docs work doesn't conflict.

**It runs beside the stack**, so Open WebUI, pi and `agent` keep their models. Alone it took about
45 GiB, and by arithmetic that fits beside the everyday models: their 43, its 45 and the 24 GiB
reserve make 112 of 117. The first repeat run is also the first measured beside them, so keep the
`free -g` readings for [Speed tests](../reference/speed-tests.md). Flash-Next can't run at the same
time.

**1. Make room, on the Spark:**

```bash
/opt/local-ai/app/.venv/bin/spark status
free -g
```

- **The llama.cpp coder, `qwen3.8-27b`, mustn't be loaded:** the two coders don't fit together. If
  `spark status` lists it, unload it. Today's stack unloads only by stopping llama-swap, which stops
  every model and cuts off any answer in progress, **on the Spark**:
  `systemctl stop local-ai-llama-swap && systemctl start local-ai-llama-swap`. From Phase 2a's
  cutover, `/opt/local-ai/app/.venv/bin/spark unload coder` unloads only the coder.
- **Then load Gemma:** ask Open WebUI one question, so the container's start sees Gemma's memory.
- **Start only if `free -g` shows at least 79 GiB available:** the container's 45, the 24 reserve,
  and about 10 the everyday models can still grow into.

**2. Start it, on the Spark**, in tmux or a terminal you'll keep open. It's step 5's command with
the image's digest filled in, hasso5703's, checked on the Spark on 2026-10-14:

```bash
sudo docker run --rm --name qwen38-coder --init \
  --gpus all --user "$(id -u):$(id -g)" \
  --cap-drop ALL --security-opt no-new-privileges \
  --read-only --tmpfs /tmp:rw,nosuid,nodev,mode=1777,size=2g --shm-size 16g \
  --oom-score-adj 1000 --log-driver none \
  -p 127.0.0.1:990:30000 \
  -v "$HOME/sglang/hf":/hf:ro \
  -v "$HOME/sglang/secrets/engine.yaml":/run/sglang/engine.yaml:ro \
  -v "$HOME/sglang/scratch":/scratch \
  -e HOME=/scratch -e USER=sglang -e XDG_CACHE_HOME=/scratch/.cache \
  -e TRITON_CACHE_DIR=/scratch/triton -e TORCHINDUCTOR_CACHE_DIR=/scratch/inductor \
  -e CUDA_CACHE_PATH=/scratch/nv -e PYTHONDONTWRITEBYTECODE=1 \
  -e HF_HOME=/hf -e HF_HUB_OFFLINE=1 -e HF_HUB_DISABLE_TELEMETRY=1 \
  lmsysorg/sglang@sha256:b1259f3ea3275f66237c498ea388919729018bc9f01c3d638391e06e2cf3f469 \
  python3 -m sglang.launch_server --config /run/sglang/engine.yaml \
    --model-path /hf/hub/models--RadixArk--Qwen3.8-27B-NVFP4/snapshots/52d1adc5f38aa5ebf099c29ed7025ba34cfbb854 \
    --speculative-draft-model-path /hf/hub/models--maurienne-ai--Qwen3.8-27B-DFlash2-NVFP4-RTNcal/snapshots/bd7a934213c47a9e7ef69eef36bb3325f47fd1f1 \
    --served-model-name qwen3.8-27b-sglang --tp-size 1 \
    --speculative-algorithm DFLASH --speculative-num-draft-tokens 16 \
    --speculative-draft-model-quantization modelopt_fp4 \
    --kv-cache-dtype fp8_e4m3 \
    --max-running-requests 1 --max-total-tokens 266240 --max-mamba-cache-size 16 \
    --mamba-ssm-dtype bfloat16 --mamba-radix-cache-strategy extra_buffer \
    --mem-fraction-static 0.55 --chunked-prefill-size 4096 \
    --cuda-graph-max-bs-decode 1 --disable-prefill-cuda-graph --disable-flashinfer-autotune \
    --attention-backend flashinfer \
    --reasoning-parser qwen3 --tool-call-parser qwen3_coder \
    --log-level warning --host 0.0.0.0 --port 30000 \
    2>&1 | tee ~/sglang/scratch/boot-$(date +%F-%H%M).log
```

If that terminal's SSH session drops, the container may keep running: look with
`sudo docker ps --filter name=qwen38-coder`, and stop it as in step 5 below.

**3. Check it, on the Spark**, from a second terminal. It takes about 3 minutes to answer:

```bash
until curl -sf http://127.0.0.1:990/health > /dev/null; do sleep 10; done; date +%T
free -g
```

If the first terminal shows an error and returns to its prompt, press Ctrl-C here: the loop waits
for an answer that won't come. **Stop it if available is under 28.**

**4. Connect from the Mac.** One tunnel serves both by-hand models; leave it running, **on the
Mac**:

```bash
ssh -N -L 8990:127.0.0.1:990 -L 8080:127.0.0.1:8080 brightroar
```

Then in opencode pick `qwen3.8-27b-sglang` with `/model`, under `spark-sglang`, and keep to one
opencode window at a time ([What to know first](#what-to-know-first)). `Error: Unauthorized` means
opencode's background service started without the key: [step 6](#connect-from-the-mac) has the fix.

**5. Stop it, on the Spark:**

```bash
sudo docker stop -t 20 qwen38-coder
free -g
/opt/local-ai/app/.venv/bin/spark status
```

Available comes back within seconds. The stack kept running, so there's nothing to restore; if
`spark status` says the brake is holding, release it as [Stop it](#stop-it) says. **On the Mac:**
Ctrl-C where the tunnel runs.

## What to know first

- **One request at a time.** Concurrent requests corrupt the drafter's state (sglang #36548 and
  #38009, both open on 2026-10-14), and the sign is an answer that turns into runs of `!`. So it
  runs with `--max-running-requests 1`: opencode's sub-agents queue rather than run side by side.
  If you see a run of `!`, restart the container.
- **It's outside the stack's checks.** llama-swap's launch check never sees it, and the brake can't
  stop it: the brake unloads only through llama-swap. You check memory before you start it, and
  you stop it ([The budget](#the-budget)).
- **It boots slowly.** hasso5703 measured 5–9 minutes from start to the first answer. *(Here,
  2026-10-14: about 3 minutes from start to ready, by its boot log, with the stack stopped:
  [Speed tests](../reference/speed-tests.md).)*

## The budget

The two caps, `--max-total-tokens` and `--max-mamba-cache-size`, fix its size whatever memory is
free when it starts; `--mem-fraction-static` then only decides whether it can start. By arithmetic
from SGLang's source, until the first run measures it:

| Part | GiB |
|---|---:|
| Weights | about 21 |
| Drafter | 1.4 |
| CUDA context and workspaces | 1.5–2.5 |
| KV cache: 266,240 tokens in FP8, 32 KiB each | 8.1 |
| Drafter's KV cache | 1.3 |
| Recurrent state: 16 slots | 3.6 |
| Decode graphs | 0.5–1 |
| Prefill working memory | 3–6 |
| **Total** | **about 40–46** |

So `free -g` available should fall by about 43 GiB once it's up. **Stop it (`sudo docker stop -t
20 qwen38-coder`) if available goes under 28 GiB**, the brake's warn line. Beside the always-loaded
models the room is 50 GiB (rule 9), so it fits there too, with little to spare.

## 1. Before you start

**On the Spark**, check the disk and Docker:

```bash
df -h /
sudo docker version --format '{{.Server.Version}}'
sudo docker info --format '{{.LoggingDriver}}'
```

- It needs about 45 GB free: 23.5 GB of weights and the image.
- Docker 28.0 or later: its release notes say earlier versions could let other machines on the
  LAN reach a port published on 127.0.0.1. If it's older, stop here and say so.
- The logging driver is only for the record; the run below sets its own.

## 2. Make a key

Without a key, SGLang's admin routes are open to every account on the box, `agent` included, and
one of them can turn on logging of every prompt. The key lives in your Mac's secrets, and reaches
the Spark through `ssh`'s input, never a command line.

**On the Mac**, add it to your secrets, load it, and send it to the Spark:

```bash
printf 'export SPARK_SGLANG_KEY=%s\n' "$(openssl rand -hex 32)" >> ~/.secrets
source ~/.secrets
printf 'api-key: "%s"\n' "$SPARK_SGLANG_KEY" | ssh brightroar 'umask 077; mkdir -p ~/sglang/secrets && cat > ~/sglang/secrets/engine.yaml'
```

That leaves `~/sglang/secrets/engine.yaml` on the Spark, readable by you alone, in the form
SGLang's `--config` file takes. (Tested on the Mac with a throwaway key: the folders come out
`0700`, the file `0600`, and it parses as one `api-key` entry.)

## 3. Get the image, the model and the drafter

**On the Spark**, pull the image and read its digest:

```bash
sudo docker pull lmsysorg/sglang:v0.5.21
sudo docker image inspect --format '{{json .RepoDigests}}' lmsysorg/sglang:v0.5.21
```

Note the `sha256:…` it prints: the run uses it, so the image can't change under you. hasso5703
pinned `sha256:b1259f3ea3275f66237c498ea388919729018bc9f01c3d638391e06e2cf3f469`; if yours
differs, say so before going on. v0.5.21 came out on 2026-10-02, past the seven-day rule.

Then, **on the Spark**, download the model and the drafter at hasso5703's pinned revisions, into a
Hugging Face cache of their own:

```bash
mkdir -p ~/sglang/hf ~/sglang/scratch
HF_HOME=~/sglang/hf uvx --from huggingface_hub hf download RadixArk/Qwen3.8-27B-NVFP4 \
  --revision 52d1adc5f38aa5ebf099c29ed7025ba34cfbb854
HF_HOME=~/sglang/hf uvx --from huggingface_hub hf download maurienne-ai/Qwen3.8-27B-DFlash2-NVFP4-RTNcal \
  --revision bd7a934213c47a9e7ef69eef36bb3325f47fd1f1
```

22 files (21.9 GB) and 6 files (1.6 GB), safetensors and JSON only, with no code to run (checked
with `--dry-run`, `hf` 2.2.0, 2026-10-14). Neither repository is gated.

## 4. Make room

**The first run: alone.** It measures the container's real size with nothing else at risk.
**On the Spark:**

```bash
systemctl stop local-ai-llama-swap
free -g
sync && echo 1 | sudo tee /proc/sys/vm/drop_caches > /dev/null
free -g
```

That stops every model the stack serves until you start it again ([Stop it](#stop-it)), and clears
the page cache the downloads left (the plan's open risk *Page cache and the launch check*).
Available should be back near 117. The brake keeps running: if available falls under 20 GiB it
writes its hold, which blocks the stack's loads until you release it.

*(Added 2026-10-15, Phase 2a's Task 35: from Phase 2a's cutover, stopping llama-swap by hand isn't
enough, since any start of the front starts it again, through the front's `Wants=`: a crash's
restart, or a connection to 9100. Hold it down instead, and end the hold-down where
[Stop it](#stop-it) starts the stack again, as [Deploy the
stack](deploy.md#keeping-llama-swap-stopped) says; never stop the front's socket. Until the
cutover, this step is unchanged.)*

**Later runs: beside the always-loaded models**, once the first run's numbers are in:

1. Load the always-loaded models first (a question in Open WebUI loads Gemma), so SGLang's start
   sees them.
2. Make sure the llama.cpp coder isn't loaded: `/opt/local-ai/app/.venv/bin/spark status`.
3. Start only if `free -g` shows at least 77 GiB available: the container's 43, the 24 reserve, and
   about 10 the always-loaded models can still grow into. *(Corrected 2026-10-15: 79 GiB, since the
   first run measured the container at about 45, not 43. [Run it again](#run-it-again) uses 79.)*

Anything llama-swap loads afterwards still goes through its launch check, which sees the
container's memory as used and refuses what doesn't fit. Nothing checks the container itself.

## 5. Start it

**On the Spark**, with the digest from step 3 in place of `<digest>`:

```bash
sudo docker run --rm --name qwen38-coder --init \
  --gpus all --user "$(id -u):$(id -g)" \
  --cap-drop ALL --security-opt no-new-privileges \
  --read-only --tmpfs /tmp:rw,nosuid,nodev,mode=1777,size=2g --shm-size 16g \
  --oom-score-adj 1000 --log-driver none \
  -p 127.0.0.1:990:30000 \
  -v "$HOME/sglang/hf":/hf:ro \
  -v "$HOME/sglang/secrets/engine.yaml":/run/sglang/engine.yaml:ro \
  -v "$HOME/sglang/scratch":/scratch \
  -e HOME=/scratch -e USER=sglang -e XDG_CACHE_HOME=/scratch/.cache \
  -e TRITON_CACHE_DIR=/scratch/triton -e TORCHINDUCTOR_CACHE_DIR=/scratch/inductor \
  -e CUDA_CACHE_PATH=/scratch/nv -e PYTHONDONTWRITEBYTECODE=1 \
  -e HF_HOME=/hf -e HF_HUB_OFFLINE=1 -e HF_HUB_DISABLE_TELEMETRY=1 \
  lmsysorg/sglang@sha256:<digest> \
  python3 -m sglang.launch_server --config /run/sglang/engine.yaml \
    --model-path /hf/hub/models--RadixArk--Qwen3.8-27B-NVFP4/snapshots/52d1adc5f38aa5ebf099c29ed7025ba34cfbb854 \
    --speculative-draft-model-path /hf/hub/models--maurienne-ai--Qwen3.8-27B-DFlash2-NVFP4-RTNcal/snapshots/bd7a934213c47a9e7ef69eef36bb3325f47fd1f1 \
    --served-model-name qwen3.8-27b-sglang --tp-size 1 \
    --speculative-algorithm DFLASH --speculative-num-draft-tokens 16 \
    --speculative-draft-model-quantization modelopt_fp4 \
    --kv-cache-dtype fp8_e4m3 \
    --max-running-requests 1 --max-total-tokens 266240 --max-mamba-cache-size 16 \
    --mamba-ssm-dtype bfloat16 --mamba-radix-cache-strategy extra_buffer \
    --mem-fraction-static 0.55 --chunked-prefill-size 4096 \
    --cuda-graph-max-bs-decode 1 --disable-prefill-cuda-graph --disable-flashinfer-autotune \
    --attention-backend flashinfer \
    --reasoning-parser qwen3 --tool-call-parser qwen3_coder \
    --log-level warning --host 0.0.0.0 --port 30000 \
    2>&1 | tee ~/sglang/scratch/boot-$(date +%F-%H%M).log
```

**Memory.**
- `--max-total-tokens 266240` is one 262,144-token request plus the drafter's tokens, with a little
  margin. `--max-mamba-cache-size 16` is the recurrent state: 5 slots for the running request, the
  rest for cached prefixes.
- `--mem-fraction-static 0.55` is enough to start beside the always-loaded models. If the log says
  `max_total_tokens … is larger than the profiled value`, stop and raise it to 0.60.
- `--chunked-prefill-size 4096`, decode graphs for one request only, no prefill graphs and no
  autotuning all keep down the memory SGLang uses outside its caps. hasso5703 uses 8192.

**Safety.**
- `--config` brings the key in from the file, so it's on no command line.
- `--log-level warning` keeps out the start-up line that prints every setting, the key included.
- `--user`, `--cap-drop ALL`, `no-new-privileges` and `--read-only`: the container can do no more
  than you can, and writes only to `~/sglang/scratch` and its own `/tmp`. The model files are
  mounted read-only, and `HF_HUB_OFFLINE` stops it reaching Hugging Face.
- `-p 127.0.0.1:990:30000` publishes one port on the Spark only, and below 1024, so only Docker can
  bind it, never `agent`. SGLang's other ports stay inside the container.
- `--log-driver none` keeps its output out of Docker's logs and the journal; it still reaches this
  window and the `tee` file. The tool-call parser's warnings carry arguments, and they go there only.
- `--oom-score-adj 1000`: the kernel's first choice if memory runs out. `--rm` and no `--restart`:
  a crash stays stopped, and nothing piles a restart onto memory that hasn't come back (sglang
  #40948 froze a Spark that way). `--name` makes a second start fail instead of loading it twice.

**If it fails:**
- `EROFS` (read-only file system): drop `--read-only` and note the path it wanted.
- Permission errors under `/root`: drop the `--user` line and keep the rest.
- No GPU: replace `--gpus all` with `--device nvidia.com/gpu=all`.
- Nothing printed at all: drop `--log-driver none`.

It's up when `/health` answers. **On the Spark**, from a second window:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:990/health
sudo -u agent curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:990/server_info
watch -n 2 free -g
```

- The first prints `200` once it's up; `/health` needs no key.
- The second checks the key: `agent`, with none, must get `401`. Anything else, stop the container.
- Keep the third open, and stop the container if available goes under 28.

## 6. Connect from the Mac

**On the Mac**, open a tunnel and leave it running:

```bash
ssh -N -L 8990:127.0.0.1:990 brightroar
```

It may print `setsockopt TCP_NODELAY: Invalid argument`: a harmless warning from macOS's ssh, and
the tunnel still works (seen 2026-10-14). **On the Mac**, in another terminal, check the tunnel and
the key together. It prints `200` when both are good, and `401` when the key on the Mac isn't the
one on the Spark:

```bash
printf 'Authorization: Bearer %s\n' "$SPARK_SGLANG_KEY" | curl -s -H @- -o /dev/null -w '%{http_code}\n' http://localhost:8990/v1/models
```

**opencode:** add a provider to `~/.config/opencode/opencode.json`, beside the `spark` one, inside
`"provider"`. It's the same form `spark clients opencode` writes for the stack, and the key comes
from your environment:

```json
"spark-sglang": {
  "npm": "@ai-sdk/openai-compatible",
  "options": {
    "apiKey": "{env:SPARK_SGLANG_KEY}",
    "baseURL": "http://localhost:8990/v1"
  },
  "models": {
    "qwen3.8-27b-sglang": {
      "variants": {
        "low": { "reasoningEffort": "low" },
        "medium": { "reasoningEffort": "medium" },
        "xhigh": { "reasoningEffort": "xhigh" }
      }
    }
  }
}
```

Then pick `spark-sglang/qwen3.8-27b-sglang` with `/model`. **pi** and other clients: an
OpenAI-compatible provider with base URL `http://localhost:8990/v1`, model `qwen3.8-27b-sglang` and
the key from `SPARK_SGLANG_KEY`. Keep to one session at a time: a request queued behind a long one
can pass opencode's 300-second wait for a reply and be sent again.

- **The efforts.** opencode's own list for this kind of provider is `low`, `medium` and `high`, and
  Qwen3.8's template refuses `high`: `Error: Unexpected reasoning effort high. Supported types are
  xhigh (default), medium, and low` (seen 2026-10-14). The `variants` replace that list with the
  three it takes. **Default** sends no effort, and the template then uses `xhigh`, the most
  thinking, so *default* and `xhigh` are the same. (Checked with opencode 2.0.25 against a stand-in
  server that recorded what each choice sent; `spark clients opencode` gives the stack's models the
  same list.)
- **`Error: Unauthorized` while the check above prints `200`:** opencode 2 runs one background
  service that every opencode window hands its requests to, and it keeps the environment it
  started with. If it started before you made the key, it sends an empty key, even from a new
  terminal. **On the Mac**, in a terminal opened after step 2, run `opencode service restart`, then
  start `opencode` again (seen 2026-10-14).

If tool calls fail in opencode or pi, hasso5703 has a chat template patched for agentic clients;
it comes in only if the stock one fails.

## What to record

Keep the boot log. Phase 4a's settings come from these numbers, measured here. *(The first
run's, 2026-10-14, are on [Speed tests](../reference/speed-tests.md), with the commands.)*

- `free -g` available before the start, once it's up, and after a long answer.
- From the log: the memory it found at the start, the KV pool's size in tokens, the recurrent state
  pool, and any warning about the caps. *(Corrected 2026-10-14, at the first run:
  `--log-level warning` keeps the KV pool's and the recurrent state's lines out of the log, which
  still shows the memory it found, its free memory at warm-up and any warning. The sizes come
  from the server instead, **on the Mac**, with the tunnel open. The filter keeps only the size
  and memory fields and drops any field named like a key, since this route also returns the
  server's settings:)*

  ```bash
  printf 'Authorization: Bearer %s\n' "$SPARK_SGLANG_KEY" | curl -s -H @- http://localhost:8990/server_info | jq '[paths(type != "object" and type != "array") as $p | select(any($p[]; tostring | test("max_total|max_running|mamba|mem_fraction|context_len|max_req|memory|capacity|version"))) | select(all($p[]; tostring | test("key|secret|password|auth"; "i") | not)) | {key: ($p | map(tostring) | join(".")), value: getpath($p)}] | from_entries'
  ```

- How long it took from start to `200`.
- Tokens a second. **On the Mac**, with the tunnel open, time one answer; the key goes to `curl` on
  its input, not its command line:

```bash
printf 'Authorization: Bearer %s\n' "$SPARK_SGLANG_KEY" | curl -s -H @- -H 'Content-Type: application/json' -o "$TMPDIR/sglang-test.json" -w '%{time_total} s\n' http://localhost:8990/v1/chat/completions -d '{"model":"qwen3.8-27b-sglang","messages":[{"role":"user","content":"Write a Python function that parses ISO 8601 dates, with tests."}],"max_tokens":1024,"temperature":0}'
jq '.usage' "$TMPDIR/sglang-test.json"
```

`completion_tokens` divided by the time is the speed, thinking included. For the comparison, ask
the deployed llama.cpp coder the same question once the container is stopped and the stack is
back ([Stop it](#stop-it)). **On the Mac**, with `make tunnel` running:

```bash
printf 'Authorization: Bearer %s\n' "$SPARK_API_KEY" | curl -s -H @- -H 'Content-Type: application/json' -o "$TMPDIR/gguf-test.json" -w '%{time_total} s\n' http://127.0.0.1:9100/v1/chat/completions -d '{"model":"qwen3.8-27b","messages":[{"role":"user","content":"Write a Python function that parses ISO 8601 dates, with tests."}],"max_tokens":1024,"temperature":0}'
jq '.usage' "$TMPDIR/gguf-test.json"
```

The first request also loads the coder, so time a second one.

## Stop it

**On the Spark:**

```bash
sudo docker stop -t 20 qwen38-coder
sudo docker ps --filter name=qwen38-coder
free -g
/opt/local-ai/app/.venv/bin/spark status
```

- `docker ps` lists nothing once it's gone; available comes back within seconds.
- If the status says the brake is holding, release it, **on the Spark**:
  `/opt/local-ai/app/.venv/bin/spark brake --release`. (The deployed app, not the clone: the clone
  is on `phase-2a`, whose `make status` misreads today's box.)
- If you stopped the stack for the first run, start it again, **on the Spark**:
  `systemctl start local-ai-llama-swap`. Today's stack loads each model on its first request.
  *(Added 2026-10-15, Phase 2a's Task 35's review: from Phase 2a's cutover, end the hold-down
  instead, as [Deploy the stack](deploy.md#keeping-llama-swap-stopped) says, and do it before you
  release the brake. While the drop-in stands, `systemctl start local-ai-llama-swap` exits 0 and
  starts nothing; the hold-down's undo starts llama-swap itself. Until the cutover, this step is
  unchanged.)*

**On the Mac:** Ctrl-C where the tunnel runs.

## Clean up

Keep the files if Phase 4a is close; it pins the same revisions. Otherwise, **on the Spark:**

```bash
du -sh ~/sglang
rm -rf ~/sglang
sudo docker image rm lmsysorg/sglang:v0.5.21
df -h /
```

**On the Mac**, remove the `spark-sglang` provider from opencode and the `SPARK_SGLANG_KEY` line
from `~/.secrets`, in your editor.

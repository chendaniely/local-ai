---
title: "Speed tests"
description: "Speeds and memory measured on this box, each with its setup and the exact commands, so a later run can be compared with it."
---

*(Started 2026-10-14, with the first by-hand run of the fast coder.)*

Every test here ran on `brightroar`, this stack's one Spark. Each one records what was running,
the commands as they were typed, and the numbers as measured, so a later run can repeat it and be
compared. Numbers from other people's boxes, such as hasso5703's 71.4 tok/s, stay on the pages that
cite them.

## 2026-10-15 — Flash-Next by hand, with and without MTP

**The question.** How fast is Qwen3.8-Flash-Next, the box's biggest model, on llama.cpp, with and
without MTP (the model drafting a few tokens ahead for itself)? How much memory does it take, and
how does it do in opencode?

**The answer, for this one prompt:** **31.7 tokens a second without MTP and 41.7 with it**, 1.3× as
fast, for **about 81 GiB** without MTP at a 32K context and **about 90 GiB** with it at 128K. With
MTP it writes as fast as the fast coder, for twice its memory and the whole box. In opencode it
worked, once opencode was told the model's context is 128K. Told 32K, each turn re-read about
20,000 tokens of prompt, 37–41 s, before writing anything, most likely because opencode trimmed its
history every turn to fit.

### Setup

| | Flash-Next | Flash-Next with MTP |
|---|---|---|
| Engine | llama.cpp b11146, the stack's pinned build in `/opt/local-ai/bin/llama.cpp/b11146`, run by hand | llama.cpp b11330, built in `~/scratch/llama.cpp-b11330` and not installed: the first release with MTP for this model |
| Weights | `unsloth/Qwen3.8-Flash-Next-GGUF`, `UD-IQ3_XXS`, in three shards (76.3 GiB by the repository); its revision wasn't recorded | the same |
| Vision projector | `mmproj-F16.gguf`, loaded | the same |
| Drafting | none | MTP, from `mtp-Qwen3.8-Flash-Next-Q8_0.gguf` (3.9 GiB), up to 3 tokens a step |
| Context | 32,768 tokens, then 131,072 for opencode, in llama.cpp's default KV cache | 131,072 tokens |
| Requests at once | 1 | 1 |
| How it ran | by hand, with llama-swap stopped: [Qwen3.8-Flash-Next, by hand](../how-to/qwen3.8-flash-next.md), step 3 | the same, with step 5's flags |

Nothing else was loaded: llama-swap was stopped first, and the page cache dropped, `buff/cache`
going from 13 GiB to 1 with 117 GiB available. Both builds ran with the flags in step 3:
`--gpu-layers all --parallel 1 --load-mode none --lazy-mode off --cache-ram 1024 --ctx-checkpoints
8 --no-slots --jinja`, and `choom -n 1000`. The files took 82 GiB on disk (`du`), downloaded the
day before.

### Commands

The runs used two SSH terminals on the Spark, without tmux, and a third on the Mac for the tunnel.
The 32K command below is checked against what Dan pasted; the others are the steps he was given,
checked against their output.

**Stop the stack and drop the page cache, on the Spark:**

```bash
systemctl stop local-ai-llama-swap
free -g
sync && echo 1 | sudo tee /proc/sys/vm/drop_caches > /dev/null
free -g
```

**Serve it at 32K, on the Spark**, in the first terminal:

```bash
date +%T
choom -n 1000 -- /opt/local-ai/bin/llama.cpp/b11146/llama-server \
  -m ~/scratch/models/qwen3.8-flash-next/UD-IQ3_XXS/Qwen3.8-Flash-Next-UD-IQ3_XXS-00001-of-00003.gguf \
  --mmproj ~/scratch/models/qwen3.8-flash-next/mmproj-F16.gguf \
  --alias qwen3.8-flash-next \
  --host 127.0.0.1 --port 8080 \
  --gpu-layers all --ctx-size 32768 --parallel 1 \
  --load-mode none --lazy-mode off --cache-ram 1024 --ctx-checkpoints 8 \
  --no-slots --jinja 2>&1 | tee ~/scratch/flash-next-$(date +%F-%H%M).log
```

**Wait until it answers, on the Spark**, in the second terminal:

```bash
until curl -sf http://127.0.0.1:8080/health > /dev/null; do sleep 5; done; date +%T
curl -s http://127.0.0.1:8080/v1/models | jq -r '.data[].id'
free -g
```

**Speed, on the Spark**, in the second terminal. The same question as on 2026-10-14, at temperature
0, capped at 1,024 tokens, but sent on the Spark itself, with no tunnel and no key (this server
checks none). It ran twice, and the second run counts:

```bash
for i in 1 2; do curl -s -H 'Content-Type: application/json' -o ~/scratch/flash-next-test-$i.json -w '%{time_total} s\n' http://127.0.0.1:8080/v1/chat/completions -d '{"model":"qwen3.8-flash-next","messages":[{"role":"user","content":"Write a Python function that parses ISO 8601 dates, with tests."}],"max_tokens":1024,"temperature":0}'; done
jq '{usage, timings}' ~/scratch/flash-next-test-2.json
free -g
```

`timings` is llama.cpp's own count, without the trip through `curl`. **What the log and the answer
held, on the Spark:**

```bash
f="$(ls -t ~/scratch/flash-next-*.log | head -1)"
grep -E 'buffer size|model size|n_ctx|load time' "$f" | head -40
grep -E '^ *[0-9.]+ [WE] ' "$f" | head -20
jq '.choices[0].message | {thinking_chars: (.reasoning_content // "" | length), answer_chars: (.content // "" | length)}' ~/scratch/flash-next-test-2.json
```

**The tunnel, on the Mac**, left running:

```bash
ssh -N -L 8080:127.0.0.1:8080 brightroar
```

**The efforts the chat template takes, on the Mac**, through the tunnel:

```bash
curl -s http://localhost:8080/v1/models | jq -r '.data[].id'
curl -s http://localhost:8080/props | jq -r '.chat_template' | grep -oE 'Supported types are[^.]*' | head -1
```

It printed `Supported types are xhigh (default), medium, and low`, the same as the 27B coder's
template, so opencode's provider got those three ([the by-hand page](../how-to/qwen3.8-flash-next.md),
step 4).

**opencode, on the Spark**, after a few of its turns: each turn's reading and writing, then how much
of each new prompt matched the one before:

```bash
grep -E 'prompt eval time|eval time' "$f" | tail -6
free -g
grep -iE 'forcing full prompt|checkpoint|n_past|similarity|erased' "$f" | tail -25
/opt/local-ai/bin/llama.cpp/b11146/llama-server --help 2>/dev/null | grep -iE 'checkpoint|cache-reuse|swa-full|reasoning-preserve'
```

**Stop it gently, on the Spark**, from the second terminal, then **serve it again at 128K** in the
first. `tee -i` keeps the log writing while the server stops:

```bash
pkill -INT -f 'alias qwen3.8-flash-next'
```

```bash
date +%T
choom -n 1000 -- /opt/local-ai/bin/llama.cpp/b11146/llama-server \
  -m ~/scratch/models/qwen3.8-flash-next/UD-IQ3_XXS/Qwen3.8-Flash-Next-UD-IQ3_XXS-00001-of-00003.gguf \
  --mmproj ~/scratch/models/qwen3.8-flash-next/mmproj-F16.gguf \
  --alias qwen3.8-flash-next \
  --host 127.0.0.1 --port 8080 \
  --gpu-layers all --ctx-size 131072 --parallel 1 \
  --load-mode none --lazy-mode off --cache-ram 1024 --ctx-checkpoints 8 \
  --no-slots --jinja 2>&1 | tee -i ~/scratch/flash-next-$(date +%F-%H%M).log
```

**opencode again, on the Mac**, after its limit for the model went to 131,072 tokens with 32,768
kept for the answer. The restart makes its background service read the new limit:

```bash
opencode service restart
opencode
```

Then a new session, with a task of several steps, and **on the Spark**, after four or five of its
turns:

```bash
grep -E 'prompt eval time' "$f" | tail -8
grep -E 'f_keep' "$f" | tail -8
```

**Build b11330, on the Spark**, with Flash-Next stopped, as the by-hand page's step 5 gives it:

```bash
mkdir -p ~/scratch && cd ~/scratch
git clone --depth 1 --branch b11330 https://github.com/ggml-org/llama.cpp llama.cpp-b11330
cd llama.cpp-b11330
cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=121a-real -DLLAMA_OPENSSL=OFF -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release -j "$(nproc)" --target llama-server
ls -la build/bin/llama-server
```

**Serve it with MTP, on the Spark**, in the first terminal:

```bash
date +%T
choom -n 1000 -- ~/scratch/llama.cpp-b11330/build/bin/llama-server \
  -m ~/scratch/models/qwen3.8-flash-next/UD-IQ3_XXS/Qwen3.8-Flash-Next-UD-IQ3_XXS-00001-of-00003.gguf \
  --mmproj ~/scratch/models/qwen3.8-flash-next/mmproj-F16.gguf \
  --alias qwen3.8-flash-next \
  --host 127.0.0.1 --port 8080 \
  --gpu-layers all --ctx-size 131072 --parallel 1 \
  --load-mode none --lazy-mode off --cache-ram 1024 --ctx-checkpoints 8 \
  --no-slots --jinja \
  --spec-draft-model ~/scratch/models/qwen3.8-flash-next/mtp-Qwen3.8-Flash-Next-Q8_0.gguf \
  --spec-type draft-mtp --spec-draft-n-max 3 2>&1 | tee -i ~/scratch/flash-next-mtp-$(date +%F-%H%M).log
```

**Speed with MTP, on the Spark**, in the second terminal, after the same wait as before with
`f="$(ls -t ~/scratch/flash-next-mtp-*.log | head -1)"`. Dan ran it twice, so four runs in all:

```bash
for i in 1 2; do curl -s -H 'Content-Type: application/json' -o ~/scratch/flash-next-mtp-test-$i.json -w '%{time_total} s\n' http://127.0.0.1:8080/v1/chat/completions -d '{"model":"qwen3.8-flash-next","messages":[{"role":"user","content":"Write a Python function that parses ISO 8601 dates, with tests."}],"max_tokens":1024,"temperature":0}'; done
jq '{usage, timings}' ~/scratch/flash-next-mtp-test-2.json
grep -iE 'draft|accept' "$f" | tail -4
free -g
```

### Results

| | Writing speed | Time for 1,024 tokens | Memory |
|---|---:|---:|---|
| Today's coder, Qwen3.8-27B GGUF (2026-10-14) | 23.8 tok/s | 43.0 s | 35 GiB, estimated, at 163,840 tokens |
| **Flash-Next** | **31.7 tok/s** | 32.4 s (32.6 the first time) | **about 81 GiB** at 32K: available went from 117 to 37 once loaded, and 36 after the long answers |
| **Flash-Next with MTP** | **41.7 tok/s** | 24.6–25.6 s, four runs | **about 90 GiB** at 128K: available 28 once loaded, and 27 after the runs |
| The fast coder, Qwen3.8-27B on SGLang (2026-10-14) | 41.7 tok/s | 24.6 s | about 45 GiB, at 266,240 tokens |

Memory here is how far available memory fell from 117 GiB, as for the fast coder. At 131,072
tokens without MTP, available was 34 once loaded, so the bigger context cost about 3 GiB, and the
MTP head about 6 more, against the by-hand page's estimate of 4.

- **Without MTP:** llama.cpp counted 31.69 tok/s for the writing; `curl`'s times give 31.6.
- **With MTP:** llama.cpp counted 41.47 and 41.66 tok/s for the two runs it reported. The model kept
  55% of the drafted tokens (637 of 1,155) and took 2.65 tokens a step on average; the first run
  kept 635 of 1,161.
- **All thinking.** Without MTP, the 1,024 tokens were all thinking, 3,238 characters of it, and no
  answer yet, as for both coders on 2026-10-14. The MTP runs' split wasn't checked.
- **Start:** from 15:54:08 to answering at 15:55:10, about a minute, from a dropped page cache. The
  128K restart took 47 s, from 16:24:56 to 16:25:43, with part of the files still cached; its log
  says the model had loaded at 44.7 s. The MTP server answered at 16:42:51. These times are the
  Spark's clock.
- **The build:** b11330's `llama-server` was built by 16:40, its file's time; how long the build
  took wasn't timed.

**In opencode**, through the tunnel:

| opencode's limit for the model | What each later turn read | Reading time | Writing |
|---|---|---:|---:|
| 32,768 tokens, 16,384 kept for the answer | the whole prompt again: 19,853, 21,913 and 20,908 tokens, at about 538 tok/s | 37–41 s | 28.3, 27.8 and 28.1 tok/s |
| 131,072 tokens, 32,768 kept for the answer | only what was new: 30 to 1,347 tokens, the bigger reads at about 450–500 tok/s | 0.2–2.7 s | — |

At 32K, each new prompt matched only 86.5–95.5% of the one before (the log's LCP similarity), and
one was shorter than the one before it, so opencode was changing earlier parts of the conversation
between turns. With 16,384 tokens kept for the answer, its limit left about 16,000 for a prompt
that was already 20,000, so it was most likely trimming its history to fit, and the server, which
reuses only a matching start, read everything again. At 128K the share of the old prompt the server
kept (`f_keep`) was 1.000 in 7 of the 8 turns shown, and the one turn at 0.951 still read only 1,347
tokens. The server's own context went from 32,768 to 131,072 at the same time, so this test doesn't
separate the two; but the prompts never came near 32,768 tokens, which points to opencode's limit.
Memory held at 36 GiB available through the 32K session.

**In use,** Flash-Next finished a coding task of about ten turns in opencode, writing five scripts,
running them and correcting its own wording. Once, mid-task, it thought it was still waiting for a
request, then carried on.

**The log** printed no buffer sizes at b11146's default log level (verbosity 3): the first `grep`
found only `n_ctx_slot = 32768`. Its warnings were the missing API key, the vision model's
image-token minimum, preserved reasoning being on by default, and a future change of the default
port. It printed no memory breakdown when stopped either, even with `tee -i` keeping the log open.

**The brake:** stopping the server gave back the memory, 117 GiB available. But `spark status`
showed the brake holding since 04:40:58 that day, with 19.9 GiB available then, well before this
run, which never went below 27. It came from the build session's own test runs (phase-2a's plan,
Task 47, records it). It was released with the deployed `spark brake --release`, and llama-swap
started again.

### What this doesn't tell

- **One prompt.** The plain runs agreed (32.6 and 32.4 s) and so did the four MTP runs, but it's
  one question.
- **Thinking only.** Like the coders' runs, the answer stopped before any code, so how often MTP's
  drafts land on code isn't known.
- **Quality isn't compared,** and the runs used temperature 0, not Unsloth's settings for thinking
  (temperature 1.0, top_p 0.95, top_k 20).
- **MTP at 128K leaves 27 GiB,** under the brake's 28 GiB warn line. Phase 4a needs MTP at a 64K
  context (not tried) or the 2-bit `UD-Q2_K_XL` file.
- **MTP wasn't tried in opencode.**
- **Where the memory goes isn't split.** b11146 printed no buffer sizes or breakdown at its default
  log level; a higher `-lv` may print them (not tried).
- **Reading long prompts:** about 450–540 tok/s here, against nothing yet for the two coders.

## 2026-10-14 — the fast coder against today's coder

**The question.** How much faster is Qwen3.8-27B on SGLang, with NVFP4 weights and a DFlash2
drafter (Phase 4a's fast coder), than the llama.cpp coder deployed today? And how much memory does
it take?

**The answer, for this one prompt:** **1.75× as fast**, 41.7 against 23.8 tokens a second, for
**about 45 GiB**, at the full 262K context. In opencode, Dan's sense of it was about 1.5×.

### Setup

| | Fast coder | Today's coder |
|---|---|---|
| Engine | SGLang v0.5.21 in Docker | llama.cpp b11146, under llama-swap v257 |
| Image | `lmsysorg/sglang@sha256:b1259f3ea3275f66237c498ea388919729018bc9f01c3d638391e06e2cf3f469`, hasso5703's digest (checked) | — |
| Weights | `RadixArk/Qwen3.8-27B-NVFP4` at `52d1adc` | `unsloth/Qwen3.8-27B-GGUF`, `UD-Q4_K_XL`, at `4ca7207` |
| Drafting | DFlash2 drafter, `maurienne-ai/Qwen3.8-27B-DFlash2-NVFP4-RTNcal` at `bd7a934`, 16 tokens a step | the model's own MTP layer (`--spec-type draft-mtp`), up to 3 tokens; whether it drafts isn't checked yet |
| Context | 266,240 tokens in an FP8 KV cache | 163,840 tokens, as deployed on 2026-10-08 (`main`'s full 262,144 isn't applied yet) |
| Requests at once | 1 | 1 |
| How it ran | by hand, with llama-swap stopped: [The fast coder, by hand](../how-to/qwen3.8-27b-sglang.md), step 5 | the deployed stack, started again after the fast coder stopped |

The fast coder ran with llama-swap stopped, so no other model was loaded. Today's coder ran on the
restarted stack, which loads each model on its first request. The fast coder's flags are the ones in
the by-hand page's step 5: `--max-total-tokens 266240`, `--max-mamba-cache-size 16`,
`--mem-fraction-static 0.55`, `--chunked-prefill-size 4096`, and decode graphs for one request.

### Commands

**Speed, on the Mac.** One request each, through an SSH tunnel, with the key going to `curl` on
its input, never its command line. The same question both times, at temperature 0, capped at 1,024
tokens. For the fast coder, the tunnel was `ssh -N -L 8990:127.0.0.1:990 brightroar`:

```bash
printf 'Authorization: Bearer %s\n' "$SPARK_SGLANG_KEY" | curl -s -H @- -H 'Content-Type: application/json' -o "$TMPDIR/sglang-test.json" -w '%{time_total} s\n' http://localhost:8990/v1/chat/completions -d '{"model":"qwen3.8-27b-sglang","messages":[{"role":"user","content":"Write a Python function that parses ISO 8601 dates, with tests."}],"max_tokens":1024,"temperature":0}'
jq '.usage' "$TMPDIR/sglang-test.json"
```

For today's coder, the tunnel was `make tunnel`, and the request ran twice, because the first one
also loads the model. The second one counts:

```bash
printf 'Authorization: Bearer %s\n' "$SPARK_API_KEY" | curl -s -H @- -H 'Content-Type: application/json' -o "$TMPDIR/gguf-test.json" -w '%{time_total} s\n' http://127.0.0.1:9100/v1/chat/completions -d '{"model":"qwen3.8-27b","messages":[{"role":"user","content":"Write a Python function that parses ISO 8601 dates, with tests."}],"max_tokens":1024,"temperature":0}'
jq '.usage' "$TMPDIR/gguf-test.json"
```

The speed is `completion_tokens` divided by the time `curl` printed. So it includes reading the
69-token prompt and the trip through the tunnel, which makes it a little under the pure generation
speed.

**Memory, on the Spark.** `free -g` with the fast coder up, right after its long answer, then again
once it had stopped, with nothing loaded:

```bash
free -g
sudo docker stop -t 20 qwen38-coder
free -g
```

**SGLang's own count, on the Mac**, through the same tunnel. The filter keeps only the size and
memory fields and drops any field named like a key, since this route also returns the server's
settings:

```bash
printf 'Authorization: Bearer %s\n' "$SPARK_SGLANG_KEY" | curl -s -H @- http://localhost:8990/server_info | jq '[paths(type != "object" and type != "array") as $p | select(any($p[]; tostring | test("max_total|max_running|mamba|mem_fraction|context_len|max_req|memory|capacity|version"))) | select(all($p[]; tostring | test("key|secret|password|auth"; "i") | not)) | {key: ($p | map(tostring) | join(".")), value: getpath($p)}] | from_entries'
```

**Start time, on the Spark**, from the timestamps in the boot log that step 5's `tee` writes:

```bash
grep -i -E "avail|memory|kv|max_total|mamba|warn" "$(ls -t ~/sglang/scratch/boot-*.log | head -1)" | tail -40
```

### Results

| | Fast coder | Today's coder |
|---|---|---|
| Time for 1,024 tokens | 24.6 s | 43.0 s (the first run, which loaded it, took 58.6 s) |
| **Speed** | **41.7 tok/s** | **23.8 tok/s** |
| Memory | **about 45 GiB**: available went from 117 GiB with nothing loaded to 72 with it up | 35 GiB in the deployed registry, at 163,840 tokens, estimated; its cold load took 30.6 GiB (2026-10-08) |
| Start | about 3 minutes, to ready | about 15 s to load: the first run's extra time |

**SGLang's own count** of its memory:

| | |
|---|---|
| Weights, model and drafter | 23.3 GB |
| KV cache | 8.1 GB, for 266,240 tokens |
| Warm-up graphs | 1.2 GB |
| Longest single request | 262,138 tokens |
| Recurrent-state slots | 16 |

SGLang sets aside its KV cache and its recurrent-state slots when it starts. So the 45 GiB already
holds the cache for the whole context, and the token cap, not `--mem-fraction-static`, is what
limits it. The rest of the 45 GiB, beyond SGLang's own count, is CUDA's working memory and the
recurrent state.

**Start**, by the boot log, in UTC: it found the memory at 00:08:27, set up the KV cache at 00:11:00
and finished its warm-up graphs at 00:11:28. That's about 3 minutes; hasso5703 measured 5–9 on its
own box.

**Disk:** the model and the drafter take 22 GiB in `~/sglang/hf` (`du -sh`), and the root
filesystem had 591 GiB free after (`df -h /`).

### What this doesn't tell

- **One prompt, and one run of the fast coder.** Variation between runs isn't known yet.
- **All 1,024 tokens were thinking.** SGLang counted `reasoning_tokens: 1024`, so the answer stopped
  before any code was written. A drafter's guesses may land more often on code than on thinking,
  so code may come faster; hasso5703's 71.4 tok/s was on its own bench, not this one.
- **Reading long prompts wasn't measured.** This prompt was 69 tokens. A long coding session sends
  tens of thousands, and then prompt reading counts for much more of the wait.
- **The memory isn't like for like.** The fast coder held a 266,240-token cache, and today's coder
  runs at 163,840 as deployed. On a 69-token prompt the cache's size barely changes the speed.
- **The fast coder ran alone.** Beside the always-loaded models, its memory and speed aren't
  measured yet. By the numbers it fits there: 43 GiB for them, 45 for it and the 24 GiB reserve
  make 112, within the 117 available.
- **Quality isn't compared.** SGLang also warned that the FP8 KV cache has no scaling factors, so
  it uses 1.0 for each.

Phase 4a's runs cover these before the fast coder replaces `qwen3.8-27b`.

---
title: "Speed tests"
description: "Speeds and memory measured on this box, each with its setup and the exact commands, so a later run can be compared with it."
---

*(Started 2026-10-14, with the first by-hand run of the fast coder.)*

Every test here ran on `brightroar`, this stack's one Spark. Each one records what was running,
the commands as they were typed, and the numbers as measured, so a later run can repeat it and be
compared. Numbers from other people's boxes, such as hasso5703's 71.4 tok/s, stay on the pages that
cite them.

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

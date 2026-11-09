---
title: "The speech service — design"
description: "Word timings for Qwen3-ASR on the Spark: a by-hand service today, behind the front and the gate after the soak, shaped so that diarization can join it."
date: 2026-11-09
---

# The speech service — design

*Status: written 2026-11-09, for Dan's review. Nothing here is built. Stage 1 is a by-hand exception to
"nothing loads around the gate", which Dan approved the same day, as he did for the by-hand fast coder.*

## The goal

Transcription leaves the Mac, because the Mac is busy with agents around the clock (GitHub issue #35). Whisper
already gives word timings for English, Japanese and Korean. For Mandarin and Cantonese, vid2sub's first choice is
Qwen3-ASR with its forced aligner, and nothing in the stack can run the aligner: llama.cpp's Qwen3-ASR returns text
with no timings.

**Done (Dan, 2026-11-09):** a real Mandarin or Cantonese episode runs through vid2sub on the Spark, start to finish,
with correct word timings, while he keeps coding on the by-hand SGLang coder, and with no change to vid2sub's parser.

## Constraints

1. **vid2sub's parser doesn't change.** It sends one `POST /v1/audio/transcriptions` per chunk, multipart, with
   `model`, `response_format=verbose_json`, the `language` and `prompt` when it has them, and
   `timestamp_granularities`, the audio being a FLAC of at most 180 s. Its `parse_openai` requires a `segments` list
   of objects with `start`, `end` and `text`, reads a top-level `words` list of `word`, `start` and `end`, and a
   `language`. It rejoins each segment's words to its text, restoring punctuation; a segment whose words don't spell
   its text keeps no word timings.
2. **It fits beside SGLang.** With the lines of 2026-11-09 (reserve 17, brake 14), about 11 GiB is left beside the
   always-loaded models and a 45 GiB SGLang.
3. **The original weights,** since Qwen's published accuracy, which puts the model first for these languages, is
   theirs and not a quantized copy's.
4. **Nothing disturbs Phase 2a's soak,** which measures the registry's models.
5. **In the long run nothing loads around the gate,** and diarization has to fit the same shape.

## The choice

One Python service that runs Qwen3-ASR and the aligner together and answers under one model name. A wrapper over
llama.cpp's model would need two loads per request and would transcribe with the Q8 copy. Two separate calls would
change vid2sub. vLLM can't start under llama-swap's sandbox (Phase 4).

Behind the gate it needs a new engine kind in the registry, the launcher and the gate's engine scan, and multi-file
downloads, which shouldn't land just before the soak. So it comes in two stages, with the same service code in both.

## Stage 1: by hand

Like `make fast-coder`: temporary, started by Dan, outside the registry, and stopped for Phase 2a's drills and soak.
The gate sees it only as *other processes*, and the brake can't unload it.

### The service

`stack/speech/serve.py`, with its dependencies in `stack/speech/pyproject.toml` and `uv.lock` (Python 3.12, wheels
only, `exclude-newer = "7 days"`). Starlette and uvicorn, as the gate uses.

| Piece | Pinned at |
|---|---|
| `qwen-asr-dgxspark` | 0.1.1, a third party's fork of `qwen-asr` for aarch64 and CUDA 13, Apache-2.0 |
| torch | the newest `+cu130` aarch64 wheel the seven-day rule allows |
| `Qwen/Qwen3-ASR-1.7B` | `7278e1e70fe206f11671096ffdd38061171dd6e5`, Apache-2.0 |
| `Qwen/Qwen3-ForcedAligner-0.6B` | `c7cbfc2048c462b0d63a45797104fc9db3ad62b7`, Apache-2.0 |

The weights, about 6.1 GiB, live in `~/speech/hf`, as SGLang's do in `~/sglang/hf`. They are fetched once, then the
service runs offline.

**Routes.**
- `POST /v1/audio/transcriptions`, the shape in constraint 1.
- `GET /v1/models`, naming one model, `qwen3-asr-1.7b`.
- `GET /health`.

**The answer.** `language`, as `zh`, `yue`, `en`, `ja` or `ko` where it is one of those; `text`; `duration`;
`segments`; `words`. `prompt` goes to the model as its context. The text is split into segments at sentence-ending
punctuation. Each aligner unit belongs to the sentence whose characters it spells, and a segment's `start` and `end`
are its first and last unit's. If the units can't be matched to the text, the whole chunk is one segment with no
`words`, so vid2sub still gets the text.

**Refusals**, as JSON with a plain message:

| Status | When |
|---|---|
| 400 | not a readable audio file |
| 404 | any other model name |
| 413 | audio over 300 s, the aligner's limit, or a body over 100 MiB |
| 429 | one is already running: one request at a time, none queued |
| 503 | `MemAvailable` under 17 GiB when a request starts |

Every request is logged with its length, time taken and status, never its audio or text.

### Isolation

`qwen-asr-dgxspark` is a small third-party package, so it never runs as Dan with his environment. The service runs
as SGLang's does: `sudo docker run` of a digest-pinned `ubuntu:24.04` with `--gpus all`, `--user` Dan's uid,
`--cap-drop ALL`, `--security-opt no-new-privileges`, `--read-only` with a small `/tmp` tmpfs, and
**`--network none`**. Its mounts are the virtual environment and uv's Python, read-only, the weights, read-only, and
the socket's folder. The virtual environment is built on the host with `uv sync --no-build`, so nothing from the
packages runs outside the container.

### Reaching it

It listens on a Unix socket, `/run/user/1000/local-ai-speech/speech.sock`, in a folder only Dan's account can open,
so no local account can reach it or take its place, and it needs no key. `make tunnel` gains
`-L 58991:/run/user/1000/local-ai-speech/speech.sock`, the 5-prefix port of Dan's decision of 2026-10-22. It forwards
only while the socket exists; otherwise a connection fails and the rest of the tunnel is unaffected.

vid2sub needs only a config entry, in its own repo: an endpoint at `http://127.0.0.1:58991/v1`, and a `spark-qwen`
model on it with `catalog = "qwen3-asr-1.7b"`, `model = "qwen3-asr-1.7b"` and `parser = "openai"`.

### The target

`make speech`, `make speech DO=wait` and `make speech DO=stop`, from `stack/host/by-hand-speech`, listed as temporary
in `test_by_hand.py` as the other by-hand targets are. It refuses to start under the memory the first run measures
plus the reserve of 17 GiB; 27 GiB, for an estimated 10, until then.

### Tests

In `spark/tests/`, loading `serve.py` by its path with a stand-in model, so they need neither torch nor a GPU: the
answer's shape against constraint 1, including a segment's words spelling its text after punctuation is restored;
the sentence split for Chinese and English; each refusal; one request at a time; `language` in both directions; a
transcript the aligner can't match. The container's flags are checked as `test_by_hand.py` checks SGLang's.

### The order, and what stops it

1. **The torch test,** after the next deploy, since it needs memory the current lines don't leave: in the container,
   `torch.cuda.is_available()`, a small computation, then one real Mandarin clip through both models, reading the
   memory and time.
2. If torch can't use this GPU: the aligner and the model on the CPU, measured. If that is too slow to use, **stop and
   ask Dan.**
3. The service, its tests and its review.
4. The done test.

## Stage 2: behind the front and the gate

After the soak, in Phase 3's speech work, which this pulls forward. The service stays; a registry entry and a new
engine kind replace `make speech`, so it starts with a ticket, counts in the gate's memory and goes through the
front with a client key. Whisper-style clients and the usage meter then see it like any other model. Diarization and
speaker embeddings for Dan's audio pipeline join the same service as further routes (the plan's *Speech*: pyannote
community-1). That design, and the pipeline's route and key from the homelab, come later.

## Not in Stage 1

The 0.6B model, which needs only another pair of weights once the 1.7B works; diarization; a queue, since vid2sub
sends one chunk at a time; a served aligner for clients other than vid2sub.

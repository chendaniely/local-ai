---
title: "The speech service — design"
description: "Word timings for Qwen3-ASR on the Spark: a by-hand service today, behind the front and the gate after the soak, shaped so that diarization can join it."
date: 2026-11-09
---

# The speech service — design

*Status: written 2026-11-09, revised the same day after a six-member council (five Claude reviewers and the local
Qwen3.6), and approved by Dan that day ("you can build"). Stage 1's service, target and tests are built on
`phase-2a`; [the build plan](speech-service-plan.md) has them. It hadn't yet run as a service on the box when this
was written; `changelog.md` records its first run. Stage 1 is a by-hand exception to "nothing loads around the gate",
which Dan approved the same day, as he did for the by-hand fast coder.*

## The goal

Transcription leaves the Mac, because the Mac is busy with agents around the clock (GitHub issue #35). Whisper
already gives word timings for English, Japanese and Korean. For Mandarin and Cantonese, vid2sub's first choice is
Qwen3-ASR with its forced aligner, and nothing in the stack can run the aligner: llama.cpp's Qwen3-ASR returns text
with no timings.

**Done (Dan, 2026-11-09):** a real Mandarin or Cantonese episode runs through vid2sub on the Spark, start to finish,
with correct word timings, while he keeps coding on the by-hand SGLang coder, and with no change to vid2sub's parser.
"Correct" means: no cue flagged `est`, the share of segments with words reported, three places spot-checked by ear,
and the times compared with the Mac's result if one is kept for that episode.

## Constraints

1. **vid2sub's parser doesn't change.** It sends one `POST /v1/audio/transcriptions` per chunk, multipart, with
   `model`, `response_format=verbose_json`, `language` and `prompt` when it has them, and `timestamp_granularities[]`
   twice, which this service ignores. The audio is a FLAC of at most 180 s. `parse_openai` requires a `segments` list
   of objects with `start`, `end` and `text`, reads a top-level `words` list of `word`, `start` and `end`, and a
   `language`. It rejoins each segment's words to its text. A segment whose words don't spell its text keeps no word
   timings.
2. **Memory.** The box has no room for it as things stand. Read on 2026-11-09 beside SGLang, Qwen3.6 pinned, the
   embeddings and whisper: 30 GiB available, 6 GiB of it owed to the loaded models' growth, so about 7 GiB free for a
   load even at the reserve of 17. The service takes about 12 GiB (step 3, below), and a load from there
   would set off the brake's watch on the rate of fall, which could only unload always-loaded models. So **Qwen3.6
   is unloaded for each transcription run** (Dan, 2026-11-09), which frees about 30 GiB, and the load starts from
   about 58 GiB free, far from the brake. ~~Requests for Qwen3.6, the web UI's background tasks included, are refused
   with the reason until it is loaded again.~~ *(Corrected 2026-11-09, at the build's review: a request loads it again
   if it fits, because the mark `spark unload` leaves stops only the gate's own reload. With SGLang up it doesn't fit at
   the reserve deployed today, so it's refused with the reason. From the next deploy, at the reserve of 17, it may fit,
   and the web UI's background tasks use it then, so a run shouldn't ask for it.)*
3. **The original weights,** since Qwen's published accuracy, which puts the model first for these languages, is
   theirs and not a quantized copy's.
4. **Nothing disturbs Phase 2a's measurements.** Tasks 41, 43, 44, 45 and 47 to 50 run with this service stopped.
5. **In the long run nothing loads around the gate,** and diarization has to fit the same shape.

## The choice

One Python service that runs Qwen3-ASR and the aligner together and answers under one model name. It is the only
option meeting constraints 1 and 3. A wrapper over llama.cpp's model transcribes with the Q8 copy. Two separate calls
change vid2sub. vLLM's and CrispASR's servers document no word timings on their transcription routes.

Behind the gate it needs an engine kind, a start route and multi-file downloads that shouldn't land just before the
soak. So it comes in two stages.

## Stage 1: by hand

Like `make fast-coder`: temporary, started by Dan, outside the registry. The gate sees it only as *other processes*,
and the brake can't unload it.

### The service

`stack/speech/serve.py`, with its dependencies in `stack/speech/pyproject.toml` and `uv.lock`: Python 3.12,
uv's own interpreters, `exclude-newer = "7 days"`, wheels only, no build system. Starlette and uvicorn, as the gate
uses, and `python-multipart`. Dependabot doesn't watch this folder.

| Piece | Pinned at |
|---|---|
| `qwen-asr-dgxspark` | 0.1.1, a third party's fork of `qwen-asr` for aarch64 and CUDA 13, Apache-2.0, its wheel's hash in the lock |
| transformers | 5.14.x, within what the fork's release says it supports |
| torch | 2.13.0+cu130, from an explicit PyTorch index used for torch only; `torchaudio` overridden out, since nothing imports it |
| `Qwen/Qwen3-ASR-1.7B` | `7278e1e70fe206f11671096ffdd38061171dd6e5`, Apache-2.0, safetensors |
| `Qwen/Qwen3-ForcedAligner-0.6B` | `c7cbfc2048c462b0d63a45797104fc9db3ad62b7`, Apache-2.0, safetensors |
| `ubuntu:24.04` | by digest |

The weights, about 6.1 GiB, live in `~/speech/hf`. The two snapshots hold no code: `make speech` refuses to start if
either holds a `*.py`, `*.bin`, `*.pt` or `*.pkl`.

**Loading.** Once, at startup: `Qwen3ASRModel.from_pretrained` on `cuda:0` in bfloat16, the aligner given its own
device and dtype, or it stays on the CPU, `max_inference_batch_size=1` and **`max_new_tokens=4096`**. The default of
512 can silently cut a fast 180 s chunk, and the aligner would then spread the partial text over the whole audio. An
answer that reaches the limit is logged. torch 2.13's native Triton kernels are de-registered at startup
(`torch._native.registry.deregister_op_overrides(disable_dsl_names="triton")`): they compile a C helper at run time,
and the container has no compiler. `/health` answers 200 only once both models are loaded.

**Routes.** `POST /v1/audio/transcriptions`; `GET /v1/models`, as `{"object":"list","data":[{"id":"qwen3-asr-1.7b"}]}`;
`GET /health`. The models run in one worker thread, so `/health`, `/v1/models` and a 429 answer at once during a
chunk: vid2sub's five-second probe would otherwise move on to another model.

**The answer.** `language`, `text`, `duration`, `segments`, `words`.

- **One segment per chunk,** the text whole, its `start` the first word's start and its `end` the last word's end.
  vid2sub's own Qwen parser takes one segment per chunk, and a sentence splitter loses a following sentence's words
  to a zero-length unit.
- **Each `word` is a literal span of the text,** not the aligner's unit. The aligner drops punctuation inside a
  token (`3.5` becomes `35`, `COVID-19` becomes `COVID19`), and one such unit costs the whole chunk its timings. The
  service walks the text with the aligner's kept-character test and cuts each word from the unit's first kept
  character to its last, with the unit's times. Whitespace runs become one ASCII space. Only if the walk can't consume
  every kept character does the chunk go back with no `words`, and the service logs it. *(Found while building it,
  2026-11-09: vid2sub's `attach_punctuation` rebuilds a gap between two words from at most one space, so `A - B` would
  come back as `A -B` and cost the chunk its timings. A word therefore takes in all but the last space of the gap after
  it, and the first word all but the first space of the gap before it: there `A -` is one word.)*
- **A zero-length word** is widened by a millisecond or two, so its midpoint lies inside its segment.
- **`language`** goes out lower-cased as `zh`, `yue`, `en`, `ja` or `ko` where it is one of those, the first of a
  comma list if the package returns several. Coming in, those five map to the package's names. Anything else is a 400.
- ~~**`prompt`,** at most 4 KiB, goes to the model as its context.~~ *(Corrected 2026-11-09, at the done test, Dan's
  decision: **the service ignores `prompt`.** vid2sub sends the end of the previous chunk's text there, with a
  glossary's terms when it has one. Passed as the model's context, it made 12 of a real episode's 32 chunks run to the
  4,096-token limit: about 80 % of its speech, and 28.7 of the run's 29.3 minutes. The model loops, the aligner spreads
  the repeated text over the audio, and those timings can't be right. The same happened on the 161 s clip from step 3:
  with no prompt, 489 words in 11 s; with its last 100 characters as the prompt, 502 words in 11.5 s and the text
  changed; with its first 100, 5,822 words in 132 s. So a prompt can change the text even when it doesn't loop. The cost
  is that a glossary's vocabulary hints don't reach the model, though vid2sub's stamp still records them, until a safe way to send them is found. Whether the Mac's
  engine, which passes the same context, loops too isn't known.)*
- **No text, or no kept characters** (music, silence): `segments: []` and `words: []`, a success.

**Refusals**, all as `{"error":{"code":"…","message":"…","retry_after_s":…}}`. vid2sub waits on a `no_fit` and a
429; a bare 503 would fail the input after six seconds.

| Status | Code | When |
|---|---|---|
| 400 | `bad_request` | not FLAC by its magic, over 48 kHz or 2 channels, an unsupported language, an unreadable file, ~~a prompt over 4 KiB,~~ (struck 2026-11-09, at the done test) a form with no audio |
| 403 | `forbidden` | an `Origin` header, or a `Host` other than the tunnel's (added at the build, 2026-11-09) |
| 404 | `model_not_found` | any other model name |
| 413 | `too_long` | decoded audio over 300 s, this service's own bound, or a body over 100 MiB, counted as it arrives |
| 429 | `busy` | one is already running, taken after the upload is read: one at a time, none queued |
| 503 | `no_fit` | `MemAvailable`, less the 3.5 GiB of request growth step 3 measured, under 15 GiB; or CUDA ran out of memory |
| 500 | `failed` | anything else that fails a chunk, which vid2sub retries twice; the log has the exception's type and place, never its message (added at the build, 2026-11-09) |

The audio is decoded with `soundfile`, read at most 300 s plus one frame, and passed as `(array, rate)`, never as a
string, since the package's loader fetches URLs and opens paths. *(Found at the build's review, 2026-11-09: FLAC written to a pipe, as vid2sub's ffmpeg does, has no sample count in its header, and `soundfile`'s seek after a read to the end fails on the length libsndfile then reports. So the audio is read forward only, and an empty read is a 400.)* A request with an `Origin` header, or a `Host`
other than `127.0.0.1:58991` or `localhost:58991`, is refused. Logs hold length, time taken and status, never the
audio, its file name, the text or the prompt, and uvicorn's access log is off.

### Guards while it runs

A watchdog thread reads `MemAvailable` every 0.25 s while the models are loaded and ends the process, with
`os._exit`, if it goes under 15 GiB, one above the brake's line of 14 after the next deploy. A process's exit frees
its CUDA memory. It doesn't wait on the main thread, so a hung CUDA call can't stop it, as with the ceiling script.

### Isolation

`qwen-asr-dgxspark` is a small third-party package, so it never runs as Dan with his environment. A review of the
wheel found only transformers-5 compatibility changes, and no new network, exec or pickle code. The service runs as
SGLang's does: `sudo docker run --rm --init --name local-ai-speech --gpus all --user 1000:1000 --cap-drop ALL
--security-opt no-new-privileges --read-only --network none --memory 16g --memory-swap 16g --pids-limit 1024
--oom-score-adj 1000 --log-driver none`, with `/tmp` a `noexec` tmpfs of 256 MiB. Step 1 sizes the memory cap and
reads whether it binds on CUDA's allocations at all. `HOME`, the caches, `NUMBA_CACHE_DIR` and `CUDA_CACHE_PATH` are
under `/tmp`, and Hugging Face is offline.

Mounted read-only: the virtual environment, uv's Python at its identical host path, since the environment's links
are absolute, `stack/speech/serve.py` alone and never the clone, and the weights. Writable: the socket's folder. The
environment is built on the host, at `~/speech/venv`, with `uv sync --frozen --no-build --no-install-project`~~, and
nothing on the host runs it~~. *(Corrected 2026-11-09, at the final review: only the container runs the service. `make test` runs this environment's Python on the host, with no `.pth` files, for soundfile, python-multipart and uvicorn, and never imports the third-party package.)*

What this leaves: `--gpus all` exposes the NVIDIA driver's device nodes, which every account already has, and a
denial of service to the GPU. The service sees all audio and transcripts, and with `--network none` its replies and
the socket's folder are its only ways out.

### Reaching it

On the Spark it listens on a Unix socket, `/run/user/1000/local-ai-speech/speech.sock`, in a folder made `0700` by
Dan at each start and emptied at start and stop, so no other account there can reach it or take its place. The
folder goes when Dan's last session ends, so it runs in tmux. The container can replace the socket with a link~~, which
would need a second actor on the Mac to use~~: accepted for Stage 1, and Stage 2's front must not connect through a
folder the container can write. *(Corrected 2026-11-09, at the final review: a malicious uploader is already on the Mac, so one process could do both halves, and a linked socket reaches whatever Unix sockets Dan can, the gate's control socket among them. A process running as Dan on the Mac already has his ssh, so what this adds is only for a sandboxed process or another account. It stays accepted for Stage 1.)*

On the Mac, `make tunnel` gains `-L 127.0.0.1:58991:/run/user/1000/local-ai-speech/speech.sock`, beside its existing
forward. Any process on the Mac can reach that port, which is why it needs the `Origin` and `Host` checks above. It
carries no key.

vid2sub needs no code change. Its entry lives in a TOML of its own, passed with `--config` only while the service
runs, since an unreachable entry stalls other runs. An endpoint at `http://127.0.0.1:58991/v1` and a `spark-qwen`
model on it, with `catalog = "qwen3-asr-1.7b"`, `model = "qwen3-asr-1.7b"`, `parser = "openai"` and `local = true`.
A run passes `--from zh` or `--from yue`, because vid2sub's auto-detection skips Mandarin on this kind of endpoint.

### The target

**On the Spark**, in the clone, in tmux: `make speech` runs it in the foreground, and `make speech DO=stop` stops it,
from `stack/host/by-hand-speech`. It refuses to start if `spark status --json`'s `free_for_a_load_gib` is under the 12
GiB step 3 measured, or `MemAvailable` is under 48 GiB, or the brake has paused loads or a make-room hold stands, or the
gate doesn't answer, or Dan's uid isn't 1000, or outside tmux, or if it's already running or a stopped container is left
behind, or if a snapshot isn't at its pinned revision. *(The 48 and the two holds were added at the build's review,
2026-11-09: a load from about 35 GiB available fired the brake twice and one from 67 stayed quiet, and 12 GiB free alone
admits about 29 to 35 at the reserve of 17 (36 to 42 at the 24 deployed today).)* It has its own test file and removal
list, since it goes at Stage 2, not with the other by-hand targets at Phase 4a. Its help says to stop it for Phase 2a's
drills. [Its runbook](../how-to/speech-by-hand.md) has the steps.

### Tests

`spark/tests/test_speech.py` loads `serve.py` by path with a stand-in model and parts already parsed: the answer
against a verbatim copy of vid2sub's `parse_openai` and `attach_punctuation` at `ac29e7b`, asserting words kept equals
units, for Mandarin, Cantonese, English, decimals, `COVID-19`, full-width punctuation and spaces; zero-length units;
empty text; each refusal; one request at a time; the worker thread and the watchdog. The multipart and FLAC paths
need `python-multipart` and `soundfile`, which `spark/`'s lock doesn't have, so they run from `stack/speech`'s
environment under the 4 GiB cap. Container flags are checked as the by-hand tests check SGLang's.

### The order, and what stops it

The service is written only after these pass. **Steps 1 to 4 were run on 2026-11-09, as a throwaway test in the
container with these pins, and passed.**

1. In the container: import the package, `torch.cuda.is_available()`, a small computation. *torch 2.13.0+cu130 saw
   the GB10 and ran on it from its sm_120 kernels.*
2. The aligner alone on a short clip. *It timed each character, in 0.7 s. **Loading it beside Qwen3.6 set off the
   brake twice,** from about 35 GiB available, on its rate of fall, which unloaded whisper and Qwen3.6 and paused new
   loads. So nothing of this service loads until Qwen3.6 is unloaded, even the aligner alone.*
3. Qwen3.6 unloaded, then both models on real speech: 161 s of fast Mandarin stand-up with audience laughter, starting
   from 67 GiB available. *12.2 s for the chunk, about 13 times real time, 373 tokens, so the limit wasn't near.
   `MemAvailable` fell 11.9 GiB; CUDA reserved 8.3 GiB at its peak, and the 16 GiB cgroup cap held 5.1 GiB of host
   memory. The brake stayed quiet.*
4. That answer through vid2sub's real parser, built as *The answer* says. *One segment, 489 of 489 words kept, the text
   exact. 54 units had zero length, and a few stretched across laughter, up to 6 s on one character: how those read in
   subtitles is checked at step 6.* Not yet measured: the chunk's time while SGLang is generating.
5. The service, its tests and its review.
6. The done test.

**Stop and ask Dan** if the brake fires, if step 3's peak doesn't fit beside SGLang with Qwen3.6 unloaded, or if a chunk
takes more than vid2sub's 900 s timeout. If torch can't use this GPU, the models go on the CPU and are measured
against the same 900 s; if that's too slow, stop and ask.

### What changes with Stage 1

The Architecture page's diagrams and scenario table; `changelog.md` and README's *Current state* when it first runs;
the plan's *Speech* section, with a link here, and its Revisions; dated notes on the plan's three sentences saying
that captions run the aligner themselves until Phase 3, and on Task 41's precondition. `spark doctor` needs nothing, by
SGLang's precedent.

## Stage 2: behind the front and the gate

After the soak, in Phase 3's speech work, which this pulls forward. Stage 2 keeps the routes, the answer and its tests.
*(2026-11-09, at the done test: Stage 2 also has to settle how to send vocabulary to the model. Forwarding vid2sub's
`prompt` as context made it loop (*The answer*), so it isn't safe to turn back on as it was.)*
Its start route, a TCP listener for llama-swap, the pull of multi-file repos, the engine kind and long audio get a
design of their own: `sudo docker` can't start under llama-swap's `NoNewPrivileges`, and dropping the container would
run the third-party package as `spark`. Diarization and speaker embeddings for Dan's audio pipeline join the same
service then, and that pipeline's route and key from the homelab come with that design.

## Not in Stage 1

The 0.6B model, which needs only another pair of weights once the 1.7B works; diarization; a queue, since vid2sub
sends one chunk at a time; a served aligner for clients other than vid2sub; vid2sub's `prompt`, which the service has
ignored since the done test (*The answer*).

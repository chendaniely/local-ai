---
title: "Word timings for Mandarin and Cantonese, by hand"
description: "Qwen3-ASR and its forced aligner on the Spark for vid2sub: start make speech, reach it from the Mac, run an episode, and stop it. Temporary, until the speech service's Stage 2."
---

*(Added 2026-11-09, Dan's decisions; [the design](../design/speech-service.md).)*

This is the speech service's first stage: a Python service in a locked-down container, started by
hand, that gives vid2sub word timings for Mandarin and Cantonese with no change to vid2sub's parser.
It stays outside the front and the gate until its Stage 2, after the soak, so `spark status` counts
it as *other processes* and the brake can't unload it.

- **It needs Qwen3.6 unloaded,** which frees about 30 GiB. Nothing keeps it out while the service
  runs: a request for it loads it again if it fits. With the by-hand SGLang coder up, it doesn't fit
  at today's reserve, and the request is refused with the reason. From the next deploy, at the lower
  reserve, it may fit, and the web UI's background tasks ask for it then. So don't use it during a
  run. The by-hand SGLang coder can keep running.
- **Stop it for Phase 2a's drills and measurements:** Tasks 41, 43, 44, 45 and 47 to 50.
- **It ignores vid2sub's `prompt`,** so a glossary's vocabulary hints don't reach the model. As context, the prompt can
  make the model loop.
- **Don't trust its captions on TV drama yet.** On the one episode tried, 12 of 32 chunks looped to the token limit and
  came back invented or nearly empty, with no prompt too, though every chunk still got word timings and no cue was
  flagged `est` ([the design](../design/speech-service.md#the-service) has the numbers). In its tmux window, a line
  `an answer reached the 4096-token limit` marks each such chunk. Spoken word with no music, a stand-up clip, was clean.
  The stack's Whisper, vid2sub's `spark-turbo`, got dialogue out of the same audio (2026-11-10).
- **It has no key.** Its socket's folder is yours alone on the Spark, and on the Mac the tunnel's
  port takes requests only with its own `Host` and no `Origin`, so a web page can't reach it.

## Start it

**On the Spark**, as yourself, open a tmux window, so its socket's folder stays when you log out:

```bash
tmux new -As speech
```

**On the Spark**, inside that tmux window, with the clone at `~/git/hub/local-ai` on the `phase-2a` branch. Paste
this as a block of its own: pasted with `tmux new`, its later lines can run in the outer shell when tmux exits.

```bash
cd ~/git/hub/local-ai
/opt/local-ai/app/.venv/bin/spark unload qwen3.6-35b-a3b
make speech
```

If `spark unload` says it isn't loaded, that's fine. `make speech` asks for your `sudo` password once.

It refuses to start if there's less than 12 GiB free for a load or less than 48 GiB available, which
keeps the load clear of the brake's watch on the rate of fall; while the brake has paused loads or a
make-room hold stands; if the gate isn't answering; if it is already running or a stopped container
is left behind; or if the weights in `~/speech/hf` aren't the pinned ones. It loads for about a
minute and prints `loaded: listening`, then one line per chunk: its length, the time taken and the
status, never the text.

To check it, **on the Spark**, in another terminal:

```bash
curl -s --unix-socket /run/user/1000/local-ai-speech/speech.sock http://127.0.0.1:58991/v1/models
```

It prints `{"object":"list","data":[{"id":"qwen3-asr-1.7b"}]}`.

## Run an episode from the Mac

**On the Mac**, in your clone of this repo on the `phase-2a` branch, close any tunnel already open first: one
opened before you pulled this branch has no forward to the service, and a second `make tunnel` can't bind 9100.
Then leave the tunnel open in a spare terminal. It also carries the model API, as before.

```bash
make tunnel
```

**On the Mac**, save this as `~/speech-spark.toml`. It is vid2sub's whole configuration for the run, passed
with `--config` only while the service is up, because a model that can't be reached stalls other
runs:

```toml
[endpoints.spark-speech]
base_url = "http://127.0.0.1:58991/v1"

[models.spark-qwen]
catalog = "qwen3-asr-1.7b"
endpoint = "spark-speech"
model = "qwen3-asr-1.7b"
parser = "openai"
local = true
```

**On the Mac**, where you run vid2sub, use a copy of the episode in a new, empty folder, because vid2sub reuses
a sidecar it finds beside the video and doesn't transcribe again. Give it the spoken language, `zh` or `yue`,
because its auto-detection skips Mandarin on this kind of endpoint. `--to none` makes captions in the spoken
language only, since this file names no translation model. In that folder, check with `--dry-run` first:

```bash
vid2sub run "Episode.mkv" --config ~/speech-spark.toml --model spark-qwen --from zh --to none --dry-run
```

If it says `transcribe: reuse the sidecar`, stop: the run wouldn't touch the Spark. If not, **on the Mac**, in the
same folder:

```bash
vid2sub run "Episode.mkv" --config ~/speech-spark.toml --model spark-qwen --from zh --to none
```

## What it answers

| Status | Code | When |
|---|---|---|
| 400 | `bad_request` | not FLAC, over 48 kHz or 2 channels, a language other than `zh`, `yue`, `en`, `ja` or `ko`, an unreadable file |
| 403 | `forbidden` | a request with an `Origin`, or with another `Host` |
| 404 | `model_not_found` | any model name but `qwen3-asr-1.7b` |
| 413 | `too_long` | over 300 s of audio, or a body over 100 MiB |
| 429 | `busy` | another chunk is running; none wait |
| 500 | `failed` | the chunk failed; the service's terminal says where |
| 503 | `no_fit` | too little memory for the chunk, or CUDA ran out; vid2sub waits and tries again |

If memory falls under 15 GiB available at any point, the service ends itself, and vid2sub pauses
with a connection error. Until the next deploy the brake's line is 20, above that 15, so the brake acts first.
`make speech` starts the service again.

## Stop it

Press Ctrl-C in its tmux window; during a load or a chunk it waits for that work to end. Or, **on the Spark**,
from the clone at `~/git/hub/local-ai`, this stops it within 20 s:

```bash
make speech DO=stop
```

Then, **on the Spark**, load Qwen3.6 again:

```bash
/opt/local-ai/app/.venv/bin/spark load qwen3.6-35b-a3b
```

The service's Stage 2 retires this page, the target and the second forward in `make tunnel`.

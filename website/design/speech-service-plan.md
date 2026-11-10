---
title: "The speech service, Stage 1 — build plan"
description: "Task by task: the service, make speech, the docs, the first run and the done test."
date: 2026-11-09
---

# The speech service, Stage 1 — build plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A real Mandarin or Cantonese episode runs through vid2sub on the Spark with correct word timings, while Dan
keeps coding on the by-hand SGLang coder, and with no change to vid2sub's parser.

**Architecture:** One Python service, in a locked-down container, runs the original Qwen3-ASR 1.7B and its forced
aligner and answers `POST /v1/audio/transcriptions` as OpenAI's `verbose_json`. It listens on a Unix socket in a folder
only Dan can enter, and `make tunnel` forwards that socket to the Mac's 127.0.0.1:58991. Dan starts it by hand with
`make speech`; it stays outside the front and the gate until the design's Stage 2.

**Tech Stack:** Python 3.12 from uv, Starlette 1.7.0, uvicorn 0.54.0 (h11), python-multipart, soundfile, torch
2.13.0+cu130, transformers 5.14.x, `qwen-asr-dgxspark` 0.1.1, Docker with `--gpus all`, bash, GNU make.

**Spec:** [`speech-service.md`](speech-service.md), approved by Dan on 2026-11-09 ("you can build"). Read it first.

**How the listings work.** Each file's full text is below, tested as written on the Spark on 2026-11-09, so it goes
into the tree with this function rather than being retyped. **On the Spark**, in bash, from the clone on `phase-2a`:

```bash
listing() { awk -v want="$1" '$0 == "<!-- listing: " want " -->" { armed = 1; next } armed && /^````/ { if (inside) exit; inside = 1; next } inside { print }' website/design/speech-service-plan.md > "${2:-$1}"; }
```

*(Corrected 2026-11-09, after Task 5's first attempt: the listing of `serve.py` passes vid2sub's `prompt` to the model, and the tree's ignores it, because as context it can make the model loop.)*

*(Corrected 2026-11-09, at the final review: the listings stay as first tested, and the tree has moved on from them again: its `stack/speech/serve.py`, `stack/speech/pyproject.toml`, `spark/tests/test_speech.py`, `spark/tests/speech_env_checks.py`, `stack/host/by-hand-speech` and `website/how-to/speech-by-hand.md` hold the final review's fixes.)*

## Global Constraints

Every task's requirements include these.

- **Where it runs.** Every task is on the Spark, in `~/git/hub/local-ai` on `phase-2a`, unless its label says otherwise:
  **[Spark]** a session as Dan, **[Dan]** Dan's own terminal (sudo, the interactive parts), **[Mac]** heartsbane.
- **Tests only through `make test`,** one run at a time: `make test ARGS="spark/tests/test_x.py -q"`. A bare pytest is
  refused on the Spark.
- **Nothing deploys.** No `make apply`, `make apply-now` or `make install-units` in this plan.
- **The third-party package never runs as Dan or on the host.** `uv sync` only unpacks wheels; ~~only the container runs
  the environment~~. *(Corrected 2026-11-09, at the final review: only the container runs the service. `make test` runs this environment's Python on the host, with no `.pth` files, for soundfile, python-multipart and uvicorn, and never imports the third-party package.)*
- **Nothing loads while Qwen3.6 is loaded.** Loading beside it set off the brake twice on 2026-11-09. `make speech`
  refuses under 12 GiB free for a load. *(Corrected 2026-11-09, at the final review: what `make speech` checks is the room, 12 GiB free and 48 GiB available and no pause or hold, by Task 2's review. With SGLang up that means Qwen3.6 is out.)*
- **Pins:** `qwen-asr-dgxspark` 0.1.1; transformers 5.14.x; torch 2.13.0+cu130; `Qwen/Qwen3-ASR-1.7B` at
  `7278e1e70fe206f11671096ffdd38061171dd6e5`; `Qwen/Qwen3-ForcedAligner-0.6B` at
  `c7cbfc2048c462b0d63a45797104fc9db3ad62b7`; `ubuntu:24.04` by digest; `exclude-newer = "7 days"`.
- **Logs hold lengths, times and statuses,** never the audio, its file name, the text or the prompt.
- **Public repo.** No tailnet or LAN addresses, no personal hostnames, and nothing about why Dan transcribes; public
  text says "Dan's own pipelines". Never run the secrets files or the denylist through anything.
- **Docs are part of the change,** every command in them names its machine in bold, and any `#` stays out of a shell
  block that runs on the Mac.
- **Stage files by explicit path; never `--no-verify`.** Commits are Conventional, with 🤖 after the prefix, and end with
  the exact line `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`, which overrides any attribution a harness suggests.
- **Don't merge `main` into `phase-2a` from the Spark.** Push only as Task 5 says.

## Review Focus

Each line is pinned by a test in the task that owns the code; a reviewer checks that test still means what it says.

1. **A client that goes away mid-chunk** (Ctrl-C on the Mac): the chunk stays counted until the worker is done, so
   nothing queues behind it (`test_a_cancelled_handler_keeps_the_chunk_counted_until_the_worker_is_done`, Task 1).
2. **Spaced punctuation in a transcript,** such as `A - B`: the chunk keeps its timings
   (`test_any_mix_of_words_and_punctuation_keeps_every_word`, Task 1).
3. **A damaged upload:** `fLaC` then garbage, a file cut short, a form with no audio, or a path sent as text: a refusal,
   never a 500 (`speech_env_checks.py`, Task 1). *(Corrected 2026-11-09, at Task 1's review: a file cut inside a frame's audio is refused. One cut on a frame boundary, or within the next frame's header, isn't, because what's left is valid FLAC: as long as a whole frame remains, it's answered with the audio before the cut, and its `duration` shows the shorter length.)*
4. **The model breaking or memory running out mid-chunk:** a refusal in vid2sub's shape, and the next chunk is served
   (`test_cuda_running_out_is_a_no_fit_and_the_next_chunk_is_served`, Task 1).
5. **A second `make speech` while one runs:** it refuses and leaves the running one's socket alone
   (`test_a_second_start_is_refused_and_leaves_the_running_ones_socket`, Task 2).

***

### Task 1 [Spark]: the service

*(Corrected 2026-11-09, at this task's review: the listing of `decode_flac` below refused every chunk vid2sub sends, because FLAC from a pipe has no sample count in its header. The listings below are as first tested; the tree's `stack/speech/serve.py` and `spark/tests/speech_env_checks.py` hold the fix and its checks, and Step 5's count grows with them. The steps' `-q` also hides the count line, since addopts already passes one: leave it out. And `stack/speech/uv.lock`'s two `nvidia-curand` lines carry `# leakcheck: allow`, because its version number reads as a private address to the leak check.)*

**Files:**

- Create: `stack/speech/pyproject.toml`, `stack/speech/uv.lock`, `stack/speech/serve.py`
- Test: `spark/tests/test_speech.py`, `spark/tests/speech_env_checks.py`, `spark/tests/vid2sub_parser.py`

**Interfaces:**

- Consumes: nothing from this plan. `~/speech/hf` holds the weights already.
- Produces, for Task 2: `stack/speech/serve.py`, which the container runs as `/venv/bin/python /app/serve.py`; it reads
  the weights at `/hf/Qwen3-ASR-1.7B` and `/hf/Qwen3-ForcedAligner-0.6B`, and listens on `/run/speech/speech.sock`. The
  environment is `~/speech/venv`.

- [ ] **Step 1: Put the tests in the tree.**

```bash
listing spark/tests/vid2sub_parser.py
listing spark/tests/speech_env_checks.py
listing spark/tests/test_speech.py
```

- [ ] **Step 2: See them fail.**

Run: `make test ARGS="spark/tests/test_speech.py -q -x --no-header"`
Expected: FAIL, with `FileNotFoundError` for `stack/speech/serve.py`.

- [ ] **Step 3: Add the environment, lock it, and build it.**

```bash
mkdir -p stack/speech
listing stack/speech/pyproject.toml
uv lock --project stack/speech
UV_PROJECT_ENVIRONMENT="$HOME/speech/venv" uv sync --frozen --no-build --no-install-project --project stack/speech
```

Expected in `stack/speech/uv.lock`: torch `2.13.0+cu130`, transformers `5.14.x`, `qwen-asr-dgxspark` `0.1.1`, starlette
`1.7.0`, uvicorn `0.54.0`. Stop and report if any differs.

- [ ] **Step 4: Add the service.**

```bash
listing stack/speech/serve.py
```

- [ ] **Step 5: See them pass.**

Run: `make test ARGS="spark/tests/test_speech.py -q --no-header"`
Expected: 401 passed, none skipped. A skip means `~/speech/venv` wasn't built, and the multipart and FLAC paths haven't
run.

- [ ] **Step 6: Commit.**

```bash
git add stack/speech/pyproject.toml stack/speech/uv.lock stack/speech/serve.py spark/tests/test_speech.py spark/tests/speech_env_checks.py spark/tests/vid2sub_parser.py
git commit -m "feat(stack): 🤖 the speech service: Qwen3-ASR with word timings in vid2sub's shape" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

<!-- listing: stack/speech/pyproject.toml -->

````toml
# The speech service's own environment (website/design/speech-service.md). `make speech` builds it on the Spark with
# `uv sync --frozen --no-build --no-install-project`, and only the container runs it: never as Dan, never on the host.
[project]
name = "local-ai-speech"
version = "0"
requires-python = "==3.12.*"
dependencies = [
  "qwen-asr-dgxspark==0.1.1",
  "torch==2.13.0",
  "transformers>=5.14,<5.15",
  "soundfile>=0.13,<1",
  "numpy>=2,<3",
  "starlette==1.7.0",
  "uvicorn==0.54.0",
  "python-multipart>=0.0.20,<1",
]

[tool.uv]
package = false
environments = ["sys_platform == 'linux' and platform_machine == 'aarch64'"]
python-preference = "only-managed"
exclude-newer = "7 days"
no-build = true
# The fork depends on torchaudio, and nothing imports it (a review of the wheel, 2026-11-09).
override-dependencies = ["torchaudio; sys_platform == 'never'"]

[tool.uv.sources]
torch = { index = "pytorch-cu130" }

[[tool.uv.index]]
name = "pytorch-cu130"
url = "https://download.pytorch.org/whl/cu130"
explicit = true
````

<!-- listing: stack/speech/serve.py -->

````python
#!/usr/bin/env python3
"""The speech service, Stage 1: Qwen3-ASR and its forced aligner behind one OpenAI-shaped route, so vid2sub gets word
timings for Mandarin and Cantonese with no change to its parser. The design is website/design/speech-service.md.

It runs only inside the container `make speech` starts, so the third-party package never runs as Dan or with his
environment. torch, qwen_asr and soundfile are imported where they are used, because spark/tests/test_speech.py loads
this file by path from an environment that has none of them.

**One at a time.** The models run in one worker thread, so `/health`, `/v1/models` and a 429 answer at once during a
chunk: vid2sub's five-second probe would otherwise move on to another model.

**What is logged.** Lengths, times and statuses. Never the audio, its file name, the text or the prompt, so a failure
logs an exception's type and where it was, not its message."""

from __future__ import annotations

import asyncio
import concurrent.futures
import io
import logging
import os
import sys
import threading
import time
import traceback
import unicodedata
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

log = logging.getLogger("speech")

MODEL_ID = "qwen3-asr-1.7b"
ASR_PATH = "/hf/Qwen3-ASR-1.7B"
ALIGNER_PATH = "/hf/Qwen3-ForcedAligner-0.6B"
SOCKET = "/run/speech/speech.sock"  # in the container; `make speech` mounts Dan's 0700 folder there

# The default of 512 can silently cut a fast 180 s chunk, and the aligner would then spread the partial text over the
# whole audio. An answer that reaches this is logged.
MAX_NEW_TOKENS = 4096
MAX_BODY_BYTES = 100 * 1024 * 1024
MAX_SECONDS = 300  # this service's own bound; vid2sub sends at most 180
MAX_RATE = 48_000
MAX_CHANNELS = 2
MAX_PROMPT_BYTES = 4096
MAX_FIELDS = 16

# One above the brake's line of 14 after the next deploy.
FLOOR_GIB = 15.0
# What one request took from MemAvailable beyond the load, in step 3 of the design's order: 58.6 GiB to 55.2.
REQUEST_GROWTH_GIB = 3.5
WATCH_EVERY_S = 0.25
WATCHDOG_EXIT = 3
KIB_PER_GIB = 1024 * 1024

BUSY_RETRY_S = 5
NO_FIT_RETRY_S = 30
# The tunnel's address on the Mac: any process there can reach it, and a web page's request carries an Origin.
ALLOWED_HOSTS = (b"127.0.0.1:58991", b"localhost:58991")
# Coming in, vid2sub's codes; going out, the package's names as codes, lower-cased (language_out).
LANGUAGES = {"zh": "Chinese", "yue": "Cantonese", "en": "English", "ja": "Japanese", "ko": "Korean"}
LANGUAGE_CODES = {name.lower(): code for code, name in LANGUAGES.items()}


class Refusal(Exception):
    """A request this service won't serve, in the shape vid2sub waits on: it pauses on a `no_fit` and on a 429, and a
    bare 503 would fail the input after six seconds. vid2sub reads a 429's wait from the Retry-After header and a
    `no_fit`'s from the body, so both carry it."""

    def __init__(self, status: int, code: str, message: str, retry_after_s: int | None = None):
        super().__init__(code)
        self.status, self.code, self.message, self.retry_after_s = status, code, message, retry_after_s

    def response(self) -> JSONResponse:
        body = {"error": {"code": self.code, "message": self.message, "retry_after_s": self.retry_after_s}}
        headers = {} if self.retry_after_s is None else {"Retry-After": str(self.retry_after_s)}
        return JSONResponse(body, status_code=self.status, headers=headers)


def bad_request(message: str) -> Refusal:
    return Refusal(400, "bad_request", message)


def too_long(message: str) -> Refusal:
    return Refusal(413, "too_long", message)


# The answer.

def is_kept_char(ch: str) -> bool:
    """The forced aligner's own test for a character it times (qwen_asr's Qwen3ForcedAligner.is_kept_char), copied
    here so the walk below agrees with it and the tests need no torch."""
    return ch == "'" or unicodedata.category(ch)[0] in ("L", "N")


def literal_spans(text: str, units: Sequence[Any]) -> list[tuple[int, int]] | None:
    """Each unit's span of `text`, from its first kept character to its last, or None if the units don't spell the
    text's kept characters exactly. The aligner drops punctuation inside a token (`3.5` becomes `35`, `COVID-19` becomes
    `COVID19`), and vid2sub's parser needs each word to be literal text: one that isn't costs the whole chunk its
    timings."""
    spans: list[tuple[int, int]] = []
    at = 0
    for unit in units:
        first = last = None
        for ch in unit.text:
            while at < len(text) and not is_kept_char(text[at]):
                at += 1
            if at == len(text) or text[at] != ch:
                return None
            if first is None:
                first = at
            last = at
            at += 1
        if first is not None:
            spans.append((first, last + 1))
    if any(is_kept_char(ch) for ch in text[at:]):
        return None
    return spans


def fit_the_gaps(text: str, spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """vid2sub's attach_punctuation rebuilds a gap between two words from at most one space, so `A - B`, whose gap holds
    two, would come back as `A -B` and lose the chunk's timings. A word therefore takes in all but the last space of the
    gap after it, and the first word all but the first space of the gap before it."""
    fitted = [list(span) for span in spans]
    for left, right in zip(fitted, fitted[1:]):
        first, last = text.find(" ", left[1], right[0]), text.rfind(" ", left[1], right[0])
        if first != last:
            left[1] = last
    if fitted:
        first, last = text.find(" ", 0, fitted[0][0]), text.rfind(" ", 0, fitted[0][0])
        if first != last:
            fitted[0][0] = first + 1
    return [(start, end) for start, end in fitted]


def words_of(text: str, units: Sequence[Any]) -> list[dict[str, Any]] | None:
    """The answer's `words`, each a literal span with its unit's times, or None if the walk fails. A zero-length unit is
    widened by two milliseconds, so its midpoint lies inside its segment."""
    timed = [unit for unit in units if unit.text]
    spans = literal_spans(text, timed)
    if not spans:
        return None
    words = []
    for (start, end), unit in zip(fit_the_gaps(text, spans), timed):
        begins, ends = float(unit.start_time), float(unit.end_time)
        if ends <= begins:
            ends = round(begins + 0.002, 3)
        words.append({"word": text[start:end], "start": begins, "end": ends})
    return words


def language_out(name: str | None) -> str:
    first = (name or "").split(",")[0].strip().lower()
    return LANGUAGE_CODES.get(first, first)


def answer_for(result: Any, duration: float) -> dict[str, Any]:
    """vid2sub's `verbose_json`: one segment per chunk, since a sentence splitter loses a following sentence's words to
    a zero-length unit, and a top-level `words`. Whitespace runs become one ASCII space, because the parser's rebuilt
    text must equal the segment's."""
    text = " ".join(result.text.split())
    answer: dict[str, Any] = {"language": language_out(result.language), "text": text, "duration": duration}
    if not any(is_kept_char(ch) for ch in text):
        return {**answer, "segments": [], "words": []}
    units = result.time_stamps or []
    words = words_of(text, units)
    if words is None:
        log.warning("the aligner's %d units don't spell this chunk's %d characters: it goes back with no word timings",
                    len(units), len(text))
        return {**answer, "segments": [{"id": 0, "start": 0.0, "end": duration, "text": text}], "words": []}
    segment = {"id": 0, "start": words[0]["start"], "end": words[-1]["end"], "text": text}
    return {**answer, "segments": [segment], "words": words}


# The request.

@dataclass(frozen=True)
class Upload:
    audio: bytes
    model: str | None
    language: str | None
    prompt: str | None


def text_field(form: Any, name: str) -> str | None:
    value = form.get(name)
    return value if isinstance(value, str) else None


async def read_upload(request: Request) -> Upload:
    """The multipart form, its body counted as it arrives so an oversized one is refused without being read whole."""
    length = request.headers.get("content-length", "")
    too_big = too_long(f"the body is over this service's {MAX_BODY_BYTES // (1024 * 1024)} MiB")
    if length.isdigit() and int(length) > MAX_BODY_BYTES:
        raise too_big
    if not request.headers.get("content-type", "").startswith("multipart/form-data"):
        raise bad_request("expected a multipart/form-data upload")

    async def counted() -> AsyncGenerator[bytes, None]:
        seen = 0
        async for chunk in request.stream():
            seen += len(chunk)
            if seen > MAX_BODY_BYTES:
                raise too_big
            yield chunk

    try:
        form = await MultiPartParser(request.headers, counted(), max_files=1, max_fields=MAX_FIELDS).parse()
    except MultiPartException:
        raise bad_request("the upload isn't a readable multipart form") from None
    file = form.get("file")
    if not isinstance(file, UploadFile):
        raise bad_request("the upload has no `file` part")
    audio = await file.read()
    await file.close()
    return Upload(audio, text_field(form, "model"), text_field(form, "language"), text_field(form, "prompt"))


def decode_flac(data: bytes) -> tuple[Any, int]:
    """(samples, rate). Passed on as that pair and never as a string, since the package's loader fetches URLs and opens
    paths. Read to at most 300 s and one frame, whatever length the file claims."""
    import soundfile

    try:
        with soundfile.SoundFile(io.BytesIO(data)) as flac:
            if flac.format != "FLAC":
                raise bad_request("the audio isn't FLAC")
            rate, channels = flac.samplerate, flac.channels
            if rate > MAX_RATE or channels > MAX_CHANNELS:
                raise bad_request(f"the audio is over {MAX_RATE} Hz or {MAX_CHANNELS} channels")
            audio = flac.read(frames=MAX_SECONDS * rate + 1, dtype="float32", always_2d=False)
    except soundfile.LibsndfileError:
        raise bad_request("the audio can't be read") from None
    if audio.shape[0] > MAX_SECONDS * rate:
        raise too_long(f"the audio is over this service's {MAX_SECONDS} s")
    return audio, rate


def read_mem_available(path: str = "/proc/meminfo") -> float:
    """MemAvailable in GiB: the whole box's, since a container's /proc/meminfo is the host's."""
    with open(path, encoding="ascii") as meminfo:
        for line in meminfo:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / KIB_PER_GIB
    raise ValueError("no MemAvailable line")


def language_in(code: str | None) -> str | None:
    """The package's name for vid2sub's code; no code leaves the package to detect it."""
    if not code:
        return None
    name = LANGUAGES.get(code.strip().lower())
    if name is None:
        raise bad_request(f"this service takes only these languages: {', '.join(LANGUAGES)}")
    return name


def out_of_memory(error: Exception) -> bool:
    """torch.cuda.OutOfMemoryError, recognised by name, as torch may not be importable where this runs."""
    return type(error).__name__ == "OutOfMemoryError" or "CUDA out of memory" in str(error)


def empty_cuda_cache() -> None:
    torch = sys.modules.get("torch")
    if torch is not None:
        torch.cuda.empty_cache()


def no_fit(message: str) -> Refusal:
    return Refusal(503, "no_fit", message, retry_after_s=NO_FIT_RETRY_S)


def check_room(mem_available: Callable[[], float]) -> None:
    try:
        left = mem_available() - REQUEST_GROWTH_GIB
    except (OSError, ValueError):
        raise no_fit("this service can't read MemAvailable") from None
    if left < FLOOR_GIB:
        raise no_fit(f"the chunk would leave {left:.1f} GiB available, under this service's {FLOOR_GIB:.0f}")


class Speech:
    """The routes, and the one chunk at a time. `_busy` is set on the event loop and cleared when the worker's future
    is done, not when the handler ends: a handler that is cancelled mustn't let a second chunk start behind the first."""

    def __init__(self, model: Any, worker: concurrent.futures.Executor, *,
                 upload: Callable[[Request], Awaitable[Upload]], decode: Callable[[bytes], tuple[Any, int]],
                 mem_available: Callable[[], float]):
        self._model, self._worker, self._upload, self._decode = model, worker, upload, decode
        self._mem_available = mem_available
        self._busy = False

    async def health(self, _request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    async def models(self, _request: Request) -> JSONResponse:
        return JSONResponse({"object": "list", "data": [{"id": MODEL_ID}]})

    def _free(self, _future: concurrent.futures.Future) -> None:
        self._busy = False

    async def transcriptions(self, request: Request) -> JSONResponse:
        try:
            upload = await self._upload(request)
            if upload.model != MODEL_ID:
                raise Refusal(404, "model_not_found", f"this service serves only {MODEL_ID}")
            prompt = upload.prompt or ""
            if len(prompt.encode("utf-8")) > MAX_PROMPT_BYTES:
                raise bad_request(f"the prompt is over {MAX_PROMPT_BYTES // 1024} KiB")
            language = language_in(upload.language)
            if not upload.audio.startswith(b"fLaC"):
                raise bad_request("the audio isn't FLAC")
            if self._busy:
                raise Refusal(429, "busy", "another chunk is running, and none wait", retry_after_s=BUSY_RETRY_S)
            self._busy = True
            future = self._worker.submit(self._run, upload.audio, language, prompt)
            future.add_done_callback(self._free)
            return JSONResponse(await asyncio.wrap_future(future))
        except Refusal as refusal:
            log.info("refused: status %d, %s", refusal.status, refusal.code)
            return refusal.response()

    def _run(self, data: bytes, language: str | None, prompt: str) -> dict[str, Any]:
        """On the worker thread: decode, check the room, transcribe."""
        started = time.monotonic()
        seconds, status = 0.0, 200
        try:
            audio, rate = self._decode(data)
            seconds = audio.shape[0] / rate
            check_room(self._mem_available)
            [result] = self._model.transcribe(audio=(audio, rate), context=prompt, language=language,
                                              return_time_stamps=True)
            return answer_for(result, round(seconds, 3))
        except Refusal as refusal:
            status = refusal.status
            raise
        except Exception as error:
            if not out_of_memory(error):
                status = 500
                log.error("a chunk failed: %s\n%s", type(error).__name__, "".join(traceback.format_tb(error.__traceback__)))
                raise Refusal(500, "failed", "this chunk failed; the service's terminal on the Spark says where") from None
            status = 503
        finally:
            log.info("%.1f s of audio, %.1f s taken, status %d", seconds, time.monotonic() - started, status)
        # Past the except, so the traceback's frames, and the tensors they hold, have gone before the cache is emptied.
        empty_cuda_cache()
        raise no_fit("CUDA ran out of memory on this chunk")


class OnlyThroughTheTunnel:
    """Refuses a request with an `Origin` header, or a `Host` other than the tunnel's address, on every route. Any
    process on the Mac can reach the forwarded port and it carries no key, so a web page must not be able to."""

    def __init__(self, app: Callable[..., Awaitable[None]]):
        self._app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            headers = scope["headers"]
            hosts = [value for name, value in headers if name == b"host"]
            if any(name == b"origin" for name, _ in headers) or len(hosts) != 1 or hosts[0] not in ALLOWED_HOSTS:
                refusal = Refusal(403, "forbidden", "this service answers only through its tunnel")
                await refusal.response()(scope, receive, send)
                return
        await self._app(scope, receive, send)


def create_app(model: Any, worker: concurrent.futures.Executor, *,
               upload: Callable[[Request], Awaitable[Upload]] = read_upload,
               decode: Callable[[bytes], tuple[Any, int]] = decode_flac,
               mem_available: Callable[[], float] = read_mem_available) -> OnlyThroughTheTunnel:
    speech = Speech(model, worker, upload=upload, decode=decode, mem_available=mem_available)
    return OnlyThroughTheTunnel(Starlette(routes=[
        Route("/health", speech.health),
        Route("/v1/models", speech.models),
        Route("/v1/audio/transcriptions", speech.transcriptions, methods=["POST"]),
    ]))


# Starting up.

def start_watchdog(mem_available: Callable[[], float], *, end: Callable[[int], None] = os._exit,
                   every_s: float = WATCH_EVERY_S) -> threading.Thread:
    """Ends the process if MemAvailable goes under the floor, or can't be read: a process's exit frees its CUDA memory.
    It waits on nothing, so a hung CUDA call can't stop it."""

    def watch() -> None:
        while True:
            try:
                available = mem_available()
            except (OSError, ValueError) as error:
                log.error("the watchdog can't read MemAvailable (%s), so it ends the service", type(error).__name__)
                end(WATCHDOG_EXIT)
                return
            if available < FLOOR_GIB:
                log.error("%.1f GiB available, under the %.0f floor: the watchdog ends the service", available, FLOOR_GIB)
                end(WATCHDOG_EXIT)
                return
            time.sleep(every_s)

    thread = threading.Thread(target=watch, name="speech-watchdog", daemon=True)
    thread.start()
    return thread


def log_when_the_limit_is_reached(inner: Any, limit: int = MAX_NEW_TOKENS) -> None:
    """`transcribe` decodes and discards the ids this needs, so the count is taken around `generate`."""
    original = inner.generate

    def counted(*args: Any, **kwargs: Any) -> Any:
        output = original(*args, **kwargs)
        if int(output.sequences.shape[1] - kwargs["input_ids"].shape[1]) >= limit:
            log.warning("an answer reached the %d-token limit, so its text may be cut short", limit)
        return output

    inner.generate = counted


def no_triton_kernels() -> None:
    """torch 2.13's native Triton kernels compile a C helper at run time, and this container has no compiler."""
    from torch._native.registry import deregister_op_overrides

    deregister_op_overrides(disable_dsl_names="triton")


def load_models() -> Any:
    import torch

    no_triton_kernels()
    from qwen_asr import Qwen3ASRModel

    # The aligner is given its own device and dtype, or it stays on the CPU.
    model = Qwen3ASRModel.from_pretrained(
        ASR_PATH, dtype=torch.bfloat16, device_map="cuda:0", forced_aligner=ALIGNER_PATH,
        forced_aligner_kwargs={"dtype": torch.bfloat16, "device_map": "cuda:0"},
        max_inference_batch_size=1, max_new_tokens=MAX_NEW_TOKENS)
    log_when_the_limit_is_reached(model.model)
    return model


def main(load: Callable[[], Any] = load_models, serve: Callable[..., None] = uvicorn.run,
         watchdog: Callable[..., Any] = start_watchdog) -> int:
    """The watchdog first, since the load is the steepest fall in memory. The socket appears only once both models are
    in, so `/health`'s 200 means they are, and a client before then gets a refused connection, which vid2sub waits on."""
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s speech: %(message)s")
    watchdog(read_mem_available)
    worker = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="speech-worker")
    log.info("loading the two models")
    model = worker.submit(load).result()
    log.info("loaded: listening on %s", SOCKET)
    serve(create_app(model, worker), uds=SOCKET, http="h11", ws="none", loop="asyncio", lifespan="off",
          log_level="warning", access_log=False, server_header=False, date_header=False, proxy_headers=False,
          timeout_graceful_shutdown=5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
````

<!-- listing: spark/tests/test_speech.py -->

````python
"""The speech service, stack/speech/serve.py (website/design/speech-service.md).

serve.py is loaded by path with a stand-in model and parts already parsed, so these need no torch. Its answer is run
through vid2sub_parser.py, a verbatim copy of vid2sub's parser, because the design's first constraint is that the
parser doesn't change. The multipart and FLAC paths need python-multipart and soundfile, which spark/'s lock doesn't
have: speech_env_checks.py runs them from stack/speech's environment, as a child of this run and so under its memory
cap, and they skip where that environment isn't built (the Mac, CI, any account but Dan's)."""

import asyncio
import concurrent.futures
import importlib.util
import logging
import random
import subprocess
import sys
import threading
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from vid2sub_parser import parse_openai

ROOT = Path(__file__).resolve().parents[2]
SERVE = ROOT / "stack" / "speech" / "serve.py"
CHECKS = Path(__file__).with_name("speech_env_checks.py")
# conftest.py gives each test a HOME of its own, so this is read at import, when HOME is still the account's.
SPEECH_PYTHON = Path.home() / "speech" / "venv" / "bin" / "python"
BASE = "http://127.0.0.1:58991"
ROUTE = "/v1/audio/transcriptions"
SECRET = "秘密的字幕文本"


@pytest.fixture(scope="module")
def serve():
    spec = importlib.util.spec_from_file_location("speech_serve", SERVE)
    module = importlib.util.module_from_spec(spec)
    sys.modules["speech_serve"] = module  # dataclass needs its module registered
    spec.loader.exec_module(module)
    yield module
    del sys.modules["speech_serve"]


@dataclass
class Unit:
    text: str
    start_time: float
    end_time: float


@dataclass
class Result:
    text: str
    language: str
    time_stamps: Any


def is_cjk(ch: str) -> bool:
    return 0x4E00 <= ord(ch) <= 0x9FFF


def aligner_tokens(text: str) -> list[str]:
    """What the aligner's tokenize_space_lang gives: each space-split piece's kept characters, each CJK one on its own."""
    tokens: list[str] = []
    for piece in text.split():
        kept = "".join(ch for ch in piece if ch == "'" or unicodedata.category(ch)[0] in "LN")
        run = ""
        for ch in kept:
            if is_cjk(ch):
                if run:
                    tokens.append(run)
                    run = ""
                tokens.append(ch)
            else:
                run += ch
        if run:
            tokens.append(run)
    return tokens


def units_for(tokens: list[str]) -> list[Unit]:
    return [Unit(token, round(i * 0.25, 3), round(i * 0.25 + 0.2, 3)) for i, token in enumerate(tokens)]


def collapsed(text: str) -> str:
    return " ".join(text.split())


# The answer, through vid2sub's parser.

@pytest.mark.parametrize(("text", "name", "code"), [
    ("你好，世界。今天天气很好！", "Chinese", "zh"),
    ("你食咗飯未呀？我哋一齊去啦。", "Cantonese", "yue"),
    ("Hello, world. It's a test.", "English", "en"),
    ("The rate is 3.5 percent, up from 2.75.", "English", "en"),
    ("COVID-19 came first. Then e-mail.", "English", "en"),
    ("我用 Python 3.12 写了 COVID-19 的报告。", "Chinese", "zh"),
    ("你好　世界！（测试）「引号」", "Chinese", "zh"),
    ("Tabs\tand  double   spaces\nand newlines.", "English", "en"),
    ("「こんにちは」世界。", "Japanese", "ja"),
    ("A - B and C — D ... E", "English", "en"),
    ("- - hello there", "English", "en"),
    ("(a) (b) [c]", "English", "en"),
], ids=["mandarin", "cantonese", "english", "decimals", "hyphens", "mixed", "full-width", "whitespace", "japanese",
        "spaced dashes", "leading dashes", "brackets"])
def test_the_answer_keeps_every_word_through_vid2subs_parser(serve, text, name, code):
    tokens = aligner_tokens(text)
    parsed = parse_openai(serve.answer_for(Result(text, name, units_for(tokens)), 90.0))
    assert parsed.language == code and len(parsed.segments) == 1
    segment = parsed.segments[0]
    assert len(segment.words) == len(tokens)
    assert "".join(word.text for word in segment.words) == collapsed(text) == segment.text


PIECES = ["你", "好", "世", "界", "hello", "World", "3.5", "COVID-19", "it's", "x", "2026", "A", "Ω", "ñandú"]
BETWEEN = ["", " ", "  ", "　", "\t", ",", "，", "。", ", ", " - ", " — ", "...", " (", ") ", "「", "」", "—", " , ", ": "]


@pytest.mark.parametrize("seed", range(300))
def test_any_mix_of_words_and_punctuation_keeps_every_word(serve, seed):
    rng = random.Random(seed)
    text = rng.choice(BETWEEN).lstrip() + "".join(rng.choice(PIECES) + rng.choice(BETWEEN) for _ in range(rng.randint(1, 30)))
    tokens = aligner_tokens(text)
    parsed = parse_openai(serve.answer_for(Result(text, "Chinese", units_for(tokens)), 90.0))
    [segment] = parsed.segments
    assert len(segment.words) == len(tokens)
    assert "".join(word.text for word in segment.words) == collapsed(text)


def test_a_word_is_the_texts_own_characters_from_its_first_kept_to_its_last(serve):
    answer = serve.answer_for(Result("rate 3.5, COVID-19.", "English", units_for(["rate", "35", "COVID19"])), 10.0)
    assert [word["word"] for word in answer["words"]] == ["rate", "3.5", "COVID-19"]


def test_a_gap_with_two_spaces_goes_into_the_word_before_it(serve):
    answer = serve.answer_for(Result("A - B", "English", units_for(["A", "B"])), 10.0)
    assert [word["word"] for word in answer["words"]] == ["A -", "B"]


def test_the_answer_has_one_segment_from_the_first_words_start_to_the_last_words_end(serve):
    units = [Unit("你", 1.5, 1.7), Unit("好", 1.7, 2.25)]
    answer = serve.answer_for(Result("你好", "Chinese", units), 10.0)
    assert answer["segments"] == [{"id": 0, "start": 1.5, "end": 2.25, "text": "你好"}]
    assert answer["language"] == "zh" and answer["text"] == "你好" and answer["duration"] == 10.0


def test_a_zero_length_unit_is_widened_and_keeps_its_midpoint_in_the_segment(serve):
    units = [Unit("你", 1.0, 1.0), Unit("好", 1.0, 1.0)]
    answer = serve.answer_for(Result("你好", "Chinese", units), 10.0)
    assert all(word["end"] - word["start"] == pytest.approx(0.002) for word in answer["words"])
    segment = answer["segments"][0]
    for word in answer["words"]:
        assert segment["start"] <= (word["start"] + word["end"]) / 2 < segment["end"]
    assert len(parse_openai(answer).segments[0].words) == 2


def test_a_unit_ending_before_it_starts_is_widened_too(serve):
    answer = serve.answer_for(Result("你", "Chinese", [Unit("你", 2.0, 1.5)]), 10.0)
    assert answer["words"][0]["end"] == 2.002


@pytest.mark.parametrize("text", ["", "   ", "。。。", "（♪♪♪）"], ids=["empty", "spaces", "punctuation", "music"])
def test_no_kept_characters_is_a_success_with_nothing_in_it(serve, text):
    answer = serve.answer_for(Result(text, "Chinese", []), 30.0)
    assert answer["segments"] == [] and answer["words"] == [] and answer["language"] == "zh"
    assert parse_openai(answer).segments == ()


@pytest.mark.parametrize(("text", "tokens"), [
    ("你好世界", ["你", "好"]),
    ("你好", ["你", "坏"]),
    ("你好", ["你", "好", "吗"]),
    ("hello", ["HELLO"]),
], ids=["units stop early", "a different character", "a unit too many", "other case"])
def test_units_that_dont_spell_the_text_send_it_back_with_no_words_and_log_no_text(serve, caplog, text, tokens):
    with caplog.at_level(logging.WARNING, logger="speech"):
        answer = serve.answer_for(Result(text, "Chinese", units_for(tokens)), 30.0)
    assert answer["words"] == []
    assert answer["segments"] == [{"id": 0, "start": 0.0, "end": 30.0, "text": text}]
    assert caplog.records and text not in caplog.text


def test_time_stamps_of_none_send_the_text_back_with_no_words(serve):
    answer = serve.answer_for(Result("你好", "Chinese", None), 30.0)
    assert answer["words"] == [] and answer["segments"][0]["text"] == "你好"


@pytest.mark.parametrize(("name", "code"), [
    ("Chinese", "zh"), ("Cantonese", "yue"), ("English", "en"), ("Japanese", "ja"), ("Korean", "ko"),
    ("Chinese,English", "zh"), ("Cantonese,Chinese", "yue"), ("German", "german"), ("", ""), (None, ""),
], ids=str)
def test_language_goes_out_as_a_code_where_it_is_one(serve, name, code):
    assert serve.language_out(name) == code


# The service, with a stand-in model.

class Model:
    def __init__(self, *, result: Any = None, error: Exception | None = None, hold: bool = False):
        self.result = result if result is not None else Result("你好", "Chinese", [Unit("你", 0.1, 0.2), Unit("好", 0.2, 0.3)])
        self.error, self.calls, self.threads = error, [], []
        self.started, self.release = threading.Event(), threading.Event()
        self.hold = hold

    def transcribe(self, **kwargs):
        self.calls.append(kwargs)
        self.threads.append(threading.current_thread().name)
        self.started.set()
        if self.hold:
            assert self.release.wait(10)
        if self.error is not None:
            raise self.error
        return [self.result]


class Samples:
    shape = (160_000,)


def fixture_app(serve, model, **overrides):
    worker = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="speech-worker")

    async def upload(_request):
        return overrides.get("parts") or serve.Upload(b"fLaC audio", serve.MODEL_ID, "zh", "词汇")

    options = {"upload": upload, "decode": lambda _data: (Samples(), 16000), "mem_available": lambda: 60.0}
    options.update({key: value for key, value in overrides.items() if key != "parts"})
    return serve.create_app(model, worker, **options)


def client_for(serve, model, **overrides) -> TestClient:
    return TestClient(fixture_app(serve, model, **overrides), base_url=BASE, raise_server_exceptions=True)


def test_health_and_models_answer_as_the_design_says(serve):
    client = client_for(serve, Model())
    assert client.get("/health").status_code == 200
    answer = client.get("/v1/models")
    assert answer.status_code == 200 and answer.json() == {"object": "list", "data": [{"id": "qwen3-asr-1.7b"}]}


def test_a_chunk_reaches_the_model_as_samples_with_its_context_and_language_name(serve):
    model = Model()
    answer = client_for(serve, model).post(ROUTE)
    assert answer.status_code == 200
    [call] = model.calls
    assert isinstance(call["audio"], tuple) and isinstance(call["audio"][0], Samples) and call["audio"][1] == 16000
    assert call["context"] == "词汇" and call["language"] == "Chinese" and call["return_time_stamps"] is True
    body = answer.json()
    assert body["duration"] == 10.0 and [word["word"] for word in body["words"]] == ["你", "好"]


def test_no_language_leaves_the_package_to_detect_it(serve):
    model = Model()
    client_for(serve, model, parts=serve.Upload(b"fLaC", serve.MODEL_ID, None, None)).post(ROUTE)
    assert model.calls[0]["language"] is None and model.calls[0]["context"] == ""


def assert_refused(answer, status, code, *, retry=None):
    assert answer.status_code == status, answer.text
    assert answer.json() == {"error": {"code": code, "message": answer.json()["error"]["message"], "retry_after_s": retry}}
    assert answer.json()["error"]["message"]
    if retry is not None:
        assert answer.headers["retry-after"] == str(retry)


@pytest.mark.parametrize(("audio", "model_name", "language", "prompt", "status", "code"), [
    (b"RIFF", "qwen3-asr-1.7b", "zh", "", 400, "bad_request"),
    (b"", "qwen3-asr-1.7b", "zh", "", 400, "bad_request"),
    (b"fLaC", "whisper-1", "zh", "", 404, "model_not_found"),
    (b"fLaC", None, "zh", "", 404, "model_not_found"),
    (b"fLaC", "qwen3-asr-1.7b", "de", "", 400, "bad_request"),
    (b"fLaC", "qwen3-asr-1.7b", "zh", "字" * 1366, 400, "bad_request"),
], ids=["not flac", "empty audio", "another model", "no model", "another language", "a long prompt"])
def test_each_early_refusal_never_reaches_the_model(serve, audio, model_name, language, prompt, status, code):
    model = Model()
    answer = client_for(serve, model, parts=serve.Upload(audio, model_name, language, prompt)).post(ROUTE)
    assert_refused(answer, status, code)
    assert model.calls == []


def test_a_prompt_of_exactly_4_kib_is_taken(serve):
    model = Model()
    answer = client_for(serve, model, parts=serve.Upload(b"fLaC", serve.MODEL_ID, "zh", "a" * 4096)).post(ROUTE)
    assert answer.status_code == 200


@pytest.mark.parametrize(("status", "code", "maker"), [
    (400, "bad_request", "bad_request"), (413, "too_long", "too_long"),
], ids=["unreadable", "too long"])
def test_a_refusal_from_the_decode_is_answered_as_it_was_made(serve, status, code, maker):
    model = Model()

    def decode(_data):
        raise getattr(serve, maker)("words")

    assert_refused(client_for(serve, model, decode=decode).post(ROUTE), status, code)
    assert model.calls == []


@pytest.mark.parametrize(("available", "served"), [(18.5, True), (18.49, False), (10.0, False)], ids=["18.5", "just under", "10"])
def test_a_chunk_runs_only_if_memory_less_its_growth_stays_above_15_gib(serve, available, served):
    model = Model()
    answer = client_for(serve, model, mem_available=lambda: available).post(ROUTE)
    if served:
        assert answer.status_code == 200
    else:
        assert_refused(answer, 503, "no_fit", retry=30)
    assert bool(model.calls) == served


def test_unreadable_memory_is_a_no_fit(serve):
    model = Model()

    def broken():
        raise OSError("no")

    assert_refused(client_for(serve, model, mem_available=broken).post(ROUTE), 503, "no_fit", retry=30)
    assert model.calls == []


class OutOfMemoryError(RuntimeError):
    pass


@pytest.mark.parametrize("error", [OutOfMemoryError("no"), RuntimeError("CUDA out of memory. Tried to allocate 2 GiB")],
                         ids=["the type", "the words"])
def test_cuda_running_out_is_a_no_fit_and_the_next_chunk_is_served(serve, error):
    model = Model(error=error)
    client = client_for(serve, model)
    assert_refused(client.post(ROUTE), 503, "no_fit", retry=30)
    model.error = None
    assert client.post(ROUTE).status_code == 200


def test_any_other_failure_is_a_refusal_that_logs_no_text(serve, caplog):
    model = Model(error=ValueError(SECRET))
    client = client_for(serve, model)
    with caplog.at_level(logging.INFO, logger="speech"):
        answer = client.post(ROUTE)
    assert_refused(answer, 500, "failed")
    assert "ValueError" in caplog.text and SECRET not in caplog.text and "词汇" not in caplog.text
    assert SECRET not in answer.text
    model.error = None
    assert client.post(ROUTE).status_code == 200


def test_the_log_holds_lengths_and_status_and_never_the_text(serve, caplog):
    model = Model(result=Result(SECRET, "Chinese", units_for(list(SECRET))))
    with caplog.at_level(logging.INFO, logger="speech"):
        assert client_for(serve, model).post(ROUTE).status_code == 200
    assert "10.0 s of audio" in caplog.text and "status 200" in caplog.text
    assert SECRET not in caplog.text and "词汇" not in caplog.text


def in_the_background(call):
    done: list[Any] = []
    thread = threading.Thread(target=lambda: done.append(call()), daemon=True)
    thread.start()
    return thread, done


def test_during_a_chunk_health_models_and_a_429_answer_at_once_and_nothing_queues(serve):
    model = Model(hold=True)
    with client_for(serve, model) as client:
        thread, done = in_the_background(lambda: client.post(ROUTE))
        assert model.started.wait(10)
        began = time.monotonic()
        assert client.get("/health").status_code == 200
        assert client.get("/v1/models").status_code == 200
        assert_refused(client.post(ROUTE), 429, "busy", retry=5)
        assert time.monotonic() - began < 5
        assert len(model.calls) == 1 and done == []
        model.release.set()
        thread.join(10)
        assert done[0].status_code == 200
        model.hold = False
        assert client.post(ROUTE).status_code == 200


def test_the_model_runs_on_one_worker_thread(serve):
    model = Model()
    client = client_for(serve, model)
    assert client.post(ROUTE).status_code == 200
    assert client.post(ROUTE).status_code == 200
    assert len(set(model.threads)) == 1 and model.threads[0].startswith("speech-worker")


def test_a_cancelled_handler_keeps_the_chunk_counted_until_the_worker_is_done(serve):
    """A client that goes away (Ctrl-C on the Mac) mustn't let a second chunk queue behind the first."""
    model = Model(hold=True)
    worker = concurrent.futures.ThreadPoolExecutor(max_workers=1)

    async def upload(_request):
        return serve.Upload(b"fLaC", serve.MODEL_ID, "zh", "")

    speech = serve.Speech(model, worker, upload=upload, decode=lambda _d: (Samples(), 16000), mem_available=lambda: 60.0)

    async def run():
        task = asyncio.create_task(speech.transcriptions(None))
        while not model.started.is_set():
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert speech._busy
        refused = await speech.transcriptions(None)
        assert refused.status_code == 429
        model.release.set()
        for _ in range(200):
            if not speech._busy:
                break
            await asyncio.sleep(0.01)
        assert not speech._busy

    asyncio.run(run())
    worker.shutdown(wait=True)


@pytest.mark.parametrize("path", ["/health", "/v1/models"])
@pytest.mark.parametrize(("headers", "allowed"), [
    ({}, True),
    ({"Host": "localhost:58991"}, True),
    ({"Host": "127.0.0.1:9100"}, False),
    ({"Host": "evil.example"}, False),
    ({"Origin": "https://evil.example"}, False),
    ({"Origin": "null"}, False),
], ids=["the tunnel", "localhost", "another port", "another name", "a page's origin", "a null origin"])
def test_only_the_tunnels_host_and_no_origin_get_an_answer(serve, path, headers, allowed):
    answer = client_for(serve, Model()).get(path, headers=headers)
    if allowed:
        assert answer.status_code == 200
    else:
        assert_refused(answer, 403, "forbidden")


def test_a_page_cant_reach_the_model(serve):
    model = Model()
    answer = client_for(serve, model).post(ROUTE, headers={"Origin": "https://evil.example"})
    assert_refused(answer, 403, "forbidden")
    assert model.calls == []


# Starting up, and the guards.

def run_watchdog(serve, readings):
    ended: list[int] = []
    finished = threading.Event()
    values = iter(readings)

    def read():
        try:
            value = next(values)
        except StopIteration:
            finished.set()
            threading.Event().wait()
        if isinstance(value, Exception):
            raise value
        return value

    def end(code):
        ended.append(code)
        finished.set()

    thread = serve.start_watchdog(read, end=end, every_s=0)
    assert finished.wait(10)
    assert thread.daemon
    return ended


def test_the_watchdog_ends_the_process_under_15_gib(serve):
    assert run_watchdog(serve, [40.0, 20.0, 14.99, 40.0]) == [serve.WATCHDOG_EXIT]


def test_the_watchdog_lets_15_gib_pass(serve):
    assert run_watchdog(serve, [15.0, 15.0]) == []


@pytest.mark.parametrize("error", [OSError("gone"), ValueError("no line")], ids=["unreadable", "no line"])
def test_the_watchdog_ends_the_process_when_it_cant_read_memory(serve, error):
    assert run_watchdog(serve, [40.0, error]) == [serve.WATCHDOG_EXIT]


def test_mem_available_reads_gib_from_meminfo(serve, tmp_path):
    path = tmp_path / "meminfo"
    path.write_text("MemTotal: 127533356 kB\nMemAvailable: 31457280 kB\n")
    assert serve.read_mem_available(str(path)) == 30.0
    path.write_text("MemTotal: 127533356 kB\n")
    with pytest.raises(ValueError):
        serve.read_mem_available(str(path))


class Sequences:
    def __init__(self, length):
        self.shape = (1, length)


class Output:
    def __init__(self, length):
        self.sequences = Sequences(length)


@pytest.mark.parametrize(("new", "logged"), [(4096, True), (4095, False)], ids=["at the limit", "under it"])
def test_an_answer_reaching_the_token_limit_is_logged(serve, caplog, new, logged):
    class Inner:
        def generate(self, **kwargs):
            return Output(10 + new)

    inner = Inner()
    serve.log_when_the_limit_is_reached(inner)
    with caplog.at_level(logging.WARNING, logger="speech"):
        output = inner.generate(input_ids=Sequences(10))
    assert output.sequences.shape == (1, 10 + new)
    assert ("4096-token limit" in caplog.text) == logged


def test_main_starts_the_watchdog_loads_on_the_worker_then_serves_the_socket(serve):
    order: list[str] = []
    seen: dict[str, Any] = {}

    def load():
        order.append("load")
        seen["thread"] = threading.current_thread().name
        return Model()

    def serve_it(app, **options):
        order.append("serve")
        seen["options"] = options
        seen["app"] = app

    assert serve.main(load=load, serve=serve_it, watchdog=lambda _read: order.append("watchdog")) == 0
    assert order == ["watchdog", "load", "serve"]
    assert seen["thread"].startswith("speech-worker")
    options = seen["options"]
    assert options["uds"] == "/run/speech/speech.sock" and options["http"] == "h11" and options["ws"] == "none"
    assert options["access_log"] is False and options["proxy_headers"] is False and options["server_header"] is False
    assert isinstance(seen["app"], serve.OnlyThroughTheTunnel)


def test_main_never_serves_if_the_load_fails(serve):
    served: list[Any] = []

    def load():
        raise RuntimeError("no GPU")

    with pytest.raises(RuntimeError):
        serve.main(load=load, serve=lambda *a, **k: served.append(a), watchdog=lambda _read: None)
    assert served == []


def test_the_models_are_loaded_with_the_designs_settings():
    # Read from the source: load_models needs torch and the weights, which run only on the box (the plan's first run).
    source = SERVE.read_text()
    for needle in ('device_map="cuda:0"', "dtype=torch.bfloat16", "forced_aligner=ALIGNER_PATH",
                   'forced_aligner_kwargs={"dtype": torch.bfloat16, "device_map": "cuda:0"}',
                   "max_inference_batch_size=1", "max_new_tokens=MAX_NEW_TOKENS", 'disable_dsl_names="triton"'):
        assert needle in source, needle
    assert "MAX_NEW_TOKENS = 4096" in source


# From stack/speech's environment.

speech_names = subprocess.run([sys.executable, "-IB", str(CHECKS), "--list"], capture_output=True, text=True).stdout.split()


@pytest.mark.skipif(not SPEECH_PYTHON.exists(), reason="stack/speech's environment isn't built here")
@pytest.mark.parametrize("name", speech_names)
def test_in_the_speech_environment(name):
    run = subprocess.run([str(SPEECH_PYTHON), "-IB", str(CHECKS), name, str(SERVE)], capture_output=True, text=True,
                         timeout=120, stdin=subprocess.DEVNULL)
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.strip() == f"ok {name}"
````

<!-- listing: spark/tests/speech_env_checks.py -->

````python
"""The speech service's multipart and FLAC paths, run from stack/speech's environment, which has python-multipart and
soundfile. test_speech.py starts this as a child of its run, once per check, so each is under make test's memory cap:

    speech_env_checks.py --list
    speech_env_checks.py <check> <path to serve.py>

Imports of the environment's packages stay inside the functions, because --list runs from any Python."""

import asyncio
import concurrent.futures
import http.client
import importlib.util
import io
import json
import socket
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

CHECKS = {}
BOUNDARY = "speechboundary"
ROUTE = "/v1/audio/transcriptions"
HOST = b"127.0.0.1:58991"


def check(function):
    CHECKS[function.__name__] = function
    return function


@dataclass
class Result:
    text: str
    language: str
    time_stamps: list


class Model:
    def __init__(self):
        self.calls = []

    def transcribe(self, **kwargs):
        self.calls.append(kwargs)
        return [Result("", "Chinese", [])]


def flac(seconds=1.0, rate=16000, channels=1):
    import numpy
    import soundfile

    frames = int(seconds * rate)
    samples = numpy.zeros((frames, channels) if channels > 1 else frames, dtype="float32")
    buffer = io.BytesIO()
    soundfile.write(buffer, samples, rate, format="FLAC", subtype="PCM_16")
    return buffer.getvalue()


def part(name, value):
    return f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()


def file_part(data, name="file"):
    head = f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="{name}"; filename="c.flac"\r\nContent-Type: audio/flac\r\n\r\n'
    return head.encode() + data + b"\r\n"


def form(audio, *, model="qwen3-asr-1.7b"):
    """As vid2sub's client sends a chunk: `timestamp_granularities[]` twice, which this service ignores."""
    return b"".join([file_part(audio), part("model", model), part("response_format", "verbose_json"), part("language", "zh"),
                     part("prompt", "词汇"), part("timestamp_granularities[]", "word"),
                     part("timestamp_granularities[]", "segment"), f"--{BOUNDARY}--\r\n".encode()])


CONTENT_TYPE = f"multipart/form-data; boundary={BOUNDARY}".encode()


def drive(app, chunks, *, content_type=CONTENT_TYPE, content_length=None):
    """The ASGI call uvicorn would make, returning (status, body, how many chunks were read)."""
    headers = [(b"host", HOST), (b"content-type", content_type)]
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode()))
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST", "path": ROUTE,
             "raw_path": ROUTE.encode(), "query_string": b"", "root_path": "", "scheme": "http", "headers": headers,
             "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 58991)}
    sent = []
    read = [0]
    pieces = iter(chunks)

    async def receive():
        try:
            chunk = next(pieces)
        except StopIteration:
            return {"type": "http.request", "body": b"", "more_body": False}
        read[0] += 1
        return {"type": "http.request", "body": chunk, "more_body": True}

    async def send(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    status = next(message["status"] for message in sent if message["type"] == "http.response.start")
    body = b"".join(message.get("body", b"") for message in sent if message["type"] == "http.response.body")
    return status, json.loads(body), read[0]


def load(path):
    spec = importlib.util.spec_from_file_location("speech_serve", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["speech_serve"] = module
    spec.loader.exec_module(module)
    return module


def app_of(serve, model):
    worker = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    return serve.create_app(model, worker, mem_available=lambda: 60.0)


def post(serve, body, model=None, **options):
    model = model if model is not None else Model()
    return (*drive(app_of(serve, model), [body], **options), model)


def code_of(answer):
    return answer["error"]["code"]


@check
def a_chunk_as_vid2sub_sends_it_is_decoded_and_transcribed(serve):
    status, body, _, model = post(serve, form(flac(seconds=2.0)))
    assert status == 200, body
    assert body["duration"] == 2.0 and body["segments"] == [] and body["words"] == [] and body["language"] == "zh"
    [call] = model.calls
    samples, rate = call["audio"]
    assert rate == 16000 and samples.shape == (32000,) and str(samples.dtype) == "float32"
    assert call["language"] == "Chinese" and call["context"] == "词汇"


@check
def forty_eight_khz_stereo_is_taken(serve):
    status, body, _, model = post(serve, form(flac(rate=48000, channels=2)))
    assert status == 200, body
    assert model.calls[0]["audio"][1] == 48000 and model.calls[0]["audio"][0].shape == (48000, 2)


@check
def over_48_khz_is_a_400(serve):
    status, body, _, model = post(serve, form(flac(rate=96000)))
    assert (status, code_of(body)) == (400, "bad_request") and model.calls == []


@check
def three_channels_is_a_400(serve):
    status, body, _, model = post(serve, form(flac(channels=3)))
    assert (status, code_of(body)) == (400, "bad_request") and model.calls == []


@check
def wav_audio_is_a_400(serve):
    import numpy
    import soundfile

    buffer = io.BytesIO()
    soundfile.write(buffer, numpy.zeros(1600, dtype="float32"), 16000, format="WAV")
    status, body, _, model = post(serve, form(buffer.getvalue()))
    assert (status, code_of(body)) == (400, "bad_request") and model.calls == []


@check
def garbage_after_the_magic_is_a_400(serve):
    status, body, _, model = post(serve, form(b"fLaC" + b"\x00" * 4096))
    assert (status, code_of(body)) == (400, "bad_request") and model.calls == []


@check
def a_flac_cut_short_is_answered_and_never_a_500(serve):
    data = flac(seconds=5.0)
    status, body, _, _ = post(serve, form(data[:len(data) // 2]))
    assert status in (200, 400), (status, body)


@check
def a_flac_holding_only_its_header_is_a_400(serve):
    status, body, _, model = post(serve, form(flac(seconds=5.0)[:42]))
    assert (status, code_of(body)) == (400, "bad_request") and model.calls == []


@check
def over_300_seconds_is_a_413(serve):
    status, body, _, model = post(serve, form(flac(seconds=300.5, rate=8000)))
    assert (status, code_of(body)) == (413, "too_long") and model.calls == []


@check
def exactly_300_seconds_is_taken(serve):
    status, body, _, model = post(serve, form(flac(seconds=300.0, rate=8000)))
    assert status == 200, body
    assert model.calls[0]["audio"][0].shape == (2_400_000,)


@check
def a_body_declared_over_100_mib_is_refused_before_any_of_it_is_read(serve):
    status, body, read = drive(app_of(serve, Model()), [b"x"], content_length=100 * 1024 * 1024 + 1)
    assert (status, code_of(body)) == (413, "too_long") and read == 0


@check
def a_body_over_100_mib_is_refused_as_it_arrives(serve):
    megabyte = b"x" * (1024 * 1024)
    chunks = [file_part(megabyte)[:-2]] + [megabyte] * 200
    status, body, read = drive(app_of(serve, Model()), chunks)
    assert (status, code_of(body)) == (413, "too_long")
    assert read <= 102, read


@check
def a_malformed_form_is_a_400(serve):
    status, body, _, model = post(serve, b"--" + BOUNDARY.encode() + b"\r\nnot a header\r\n\r\n")
    assert (status, code_of(body)) == (400, "bad_request") and model.calls == []


@check
def a_form_with_no_boundary_is_a_400(serve):
    status, body, _, _ = post(serve, form(flac()), content_type=b"multipart/form-data")
    assert (status, code_of(body)) == (400, "bad_request")


@check
def json_instead_of_a_form_is_a_400(serve):
    status, body, _, _ = post(serve, b"{}", content_type=b"application/json")
    assert (status, code_of(body)) == (400, "bad_request")


@check
def a_form_with_no_file_part_is_a_400(serve):
    status, body, _, _ = post(serve, part("model", "qwen3-asr-1.7b") + f"--{BOUNDARY}--\r\n".encode())
    assert (status, code_of(body)) == (400, "bad_request")


@check
def a_form_with_the_audio_as_text_is_a_400(serve):
    status, body, _, _ = post(serve, part("file", "/etc/passwd") + part("model", "qwen3-asr-1.7b") + f"--{BOUNDARY}--\r\n".encode())
    assert (status, code_of(body)) == (400, "bad_request")


@check
def two_files_are_a_400(serve):
    body_in = file_part(flac()) + file_part(flac(), name="other") + f"--{BOUNDARY}--\r\n".encode()
    status, body, _, _ = post(serve, body_in)
    assert (status, code_of(body)) == (400, "bad_request")


class UnixConnection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("127.0.0.1", 58991, timeout=30)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(30)
        self.sock.connect(self.path)


@check
def uvicorn_serves_it_on_a_unix_socket(serve):
    """The real server on a real socket, with the options main gives it, a Host header as the tunnel's client sends."""
    import uvicorn

    with tempfile.TemporaryDirectory() as folder:
        path = str(Path(folder) / "speech.sock")
        config = uvicorn.Config(app_of(serve, Model()), uds=path, http="h11", ws="none", loop="asyncio", lifespan="off",
                                log_level="warning", access_log=False, server_header=False, date_header=False,
                                proxy_headers=False, timeout_graceful_shutdown=5)
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 15
        while not Path(path).exists():
            assert time.monotonic() < deadline, "no socket"
            time.sleep(0.05)

        connection = UnixConnection(path)
        connection.request("GET", "/health")
        answer = connection.getresponse()
        assert answer.status == 200, answer.read()
        answer.read()

        connection.putrequest("POST", ROUTE, skip_accept_encoding=True)
        body = form(flac())
        connection.putheader("Content-Type", CONTENT_TYPE.decode())
        connection.putheader("Content-Length", str(len(body)))
        connection.endheaders(body)
        answer = connection.getresponse()
        text = answer.read()
        assert answer.status == 200, text
        assert json.loads(text)["language"] == "zh"

        other = UnixConnection(path)
        other.putrequest("GET", "/health", skip_host=True)
        other.putheader("Host", "evil.example")
        other.endheaders()
        answer = other.getresponse()
        assert answer.status == 403, answer.read()
        answer.read()
        server.should_exit = True
        thread.join(15)
        assert not thread.is_alive()


def main(argv):
    if argv[1:] == ["--list"]:
        print("\n".join(CHECKS))
        return 0
    CHECKS[argv[1]](load(argv[2]))
    print(f"ok {argv[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
````

<!-- listing: spark/tests/vid2sub_parser.py -->

````python
"""vid2sub's parser, for test_speech.py: the speech service's answer has to pass it unchanged (the design's constraint 1).

MIT License, Copyright (c) 2026 Daniel Chen. ms, fill_untimed, attach_punctuation, _segment_for, parse_openai and
NO_LINE_END are verbatim copies from vid2sub at commit ac29e7b (src/vid2sub/parse/words.py and openai.py, and
subs/text.py), and are not to be edited here: if vid2sub's parser changes, copy it again. What's above them stands in for
vid2sub's own types, with the fields and the check these functions use (Word's times are never negative)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any


class ParseError(ValueError):
    pass


@dataclass(frozen=True)
class Word:
    text: str
    start_ms: int
    end_ms: int
    prob: float | None = None
    timed: bool = True

    def __post_init__(self) -> None:
        if self.start_ms < 0 or self.end_ms < 0:
            raise ValueError("a time is negative")


@dataclass(frozen=True)
class Segment:
    text: str
    start_ms: int
    end_ms: int
    words: tuple[Word, ...] = ()
    avg_logprob: float | None = None
    no_speech_prob: float | None = None
    compression_ratio: float | None = None


@dataclass(frozen=True)
class ParsedChunk:
    segments: tuple[Segment, ...]
    language: str | None
    language_probs: dict[str, float]


class langs:
    """vid2sub's table of languages, as far as parse_openai reads it."""

    LANGS = ("en", "ja", "zh", "yue", "ko")
    _FULL = {"english": "en", "japanese": "ja", "chinese": "zh", "cantonese": "yue", "korean": "ko"}

    @staticmethod
    def from_whisper_full(name: str) -> str | None:
        return langs._FULL.get(name.strip().lower())


NO_LINE_END = frozenset("（「『【〔〈《〘〖〝‘“｟«([{")


def ms(seconds: Any) -> int | None:
    if seconds is None or isinstance(seconds, bool):
        return None
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return round(value * 1000)


def fill_untimed(raw: Sequence[Sequence[Any]], start_ms: int = 0) -> list[Word]:
    words: list[Word] = []
    prev_end = start_ms
    for text, start, end, prob in raw:
        if start is None or end is None or end < start:
            words.append(Word(text=text, start_ms=prev_end, end_ms=prev_end, prob=prob,
                              timed=False))
        else:
            words.append(Word(text=text, start_ms=start, end_ms=end, prob=prob))
            prev_end = end
    return words


def attach_punctuation(text: str, units: Sequence[tuple[str, int | None, int | None]], *,
                       start_ms: int = 0) -> list[Word] | None:
    lowered = text.lower()
    fold = len(lowered) == len(text)
    haystack = lowered if fold else text
    raw: list[list[Any]] = []
    pos = 0
    for utext, start, end in units:
        needle = utext.strip()
        if not needle:
            continue
        found = haystack.find(needle.lower() if fold else needle, pos)
        if found < 0:
            return None
        gap = text[pos:found]
        lead = ""
        if gap:
            space = next((i for i, ch in enumerate(gap) if ch.isspace()), None)
            if space is not None:
                closing, lead = gap[:space], " " + gap[space:].strip()
            else:
                k = len(gap)
                while k > 0 and gap[k - 1] in NO_LINE_END:
                    k -= 1
                closing, lead = gap[:k], gap[k:]
            if closing:
                if raw:
                    raw[-1][0] += closing
                else:
                    lead = closing + lead
        raw.append([lead + text[found:found + len(needle)], start, end, None])
        pos = found + len(needle)
    if not raw:
        return None
    raw[-1][0] += text[pos:].rstrip()
    return fill_untimed(raw, start_ms)


def _segment_for(mid: int, spans: list[tuple[int, int]]) -> int:
    best, best_gap = 0, None
    for i, (start, end) in enumerate(spans):
        if start <= mid < end:
            return i
        gap = start - mid if mid < start else mid - end
        if best_gap is None or gap < best_gap:
            best, best_gap = i, gap
    return best


def parse_openai(data: Any) -> ParsedChunk:
    if not isinstance(data, dict) or not isinstance(data.get("segments"), list):
        raise ParseError("not an OpenAI verbose_json response")
    raw_lang = str(data.get("language") or "")
    language = langs.from_whisper_full(raw_lang) or (raw_lang if raw_lang in langs.LANGS else None)
    words = [w for w in data.get("words") or [] if isinstance(w, dict)]
    segs = data["segments"]
    for seg in segs:
        if not isinstance(seg, dict):
            raise ParseError("an OpenAI segment is not an object")
    spans = []
    for seg in segs:
        s_start = ms(seg.get("start")) or 0
        spans.append((s_start, max(ms(seg.get("end")) or s_start, s_start)))
    mine: list[list[tuple[str, int, int]]] = [[] for _ in segs]
    if segs:
        for w in words:
            ws, we = ms(w.get("start")), ms(w.get("end"))
            if ws is None or we is None:
                continue
            mine[_segment_for((ws + we) // 2, spans)].append((str(w.get("word", "")), ws, we))
    segments = []
    for seg, (s_start, s_end), units in zip(segs, spans, mine, strict=True):
        text = str(seg.get("text", ""))
        aligned = attach_punctuation(text, units, start_ms=s_start) if units else None
        if aligned and "".join(w.text for w in aligned) == text.rstrip():
            text, words_out = "".join(w.text for w in aligned), tuple(aligned)
        else:
            words_out = ()
        segments.append(Segment(
            text=text, start_ms=s_start, end_ms=s_end, words=words_out,
            avg_logprob=seg.get("avg_logprob"), no_speech_prob=seg.get("no_speech_prob"),
            compression_ratio=seg.get("compression_ratio")))
    # The engine reports only the language, not a distribution: 1.0 means "the engine said so",
    # not a measured probability.
    return ParsedChunk(tuple(segments), language, {language: 1.0} if language else {})
````

***

### Task 2 [Spark]: `make speech` and the tunnel's second forward

*(Corrected 2026-11-09, at this task's review: `stack/host/by-hand-speech` and its tests below are as first tested. The tree's versions also refuse under 48 GiB available or while the brake has paused loads or a make-room hold stands, ask `docker ps` for the container's state so that a failed query can't remove a live socket's folder, and check that first. Step 4's test count grows with them.)*

**Files:**

- Create: `stack/host/by-hand-speech`
- Modify: `Makefile`, with `makefile-edits.py` below
- Test: `spark/tests/test_speech_target.py`

**Interfaces:**

- Consumes: `stack/speech/serve.py`, `stack/speech/uv.lock`, and `~/speech/hf`.
- Produces, for Tasks 4 and 5: `make speech`, `make speech DO=stop`, and `make tunnel` forwarding
  `127.0.0.1:58991` to `/run/user/1000/local-ai-speech/speech.sock`.

- [ ] **Step 1: Put the test in the tree.**

```bash
listing spark/tests/test_speech_target.py
```

- [ ] **Step 2: See it fail.**

Run: `make test ARGS="spark/tests/test_speech_target.py -q -x --no-header"`
Expected: FAIL: the script isn't there.

- [ ] **Step 3: Add the script and the Makefile's changes.**

```bash
listing stack/host/by-hand-speech
chmod 775 stack/host/by-hand-speech
EDITS="$(mktemp -d)/makefile-edits.py"
listing makefile-edits.py "$EDITS"
uv run --frozen --project spark python -I "$EDITS"
```

Expected: `applied 4 edits`.

- [ ] **Step 4: See it pass, and that the older targets still do.**

Run: `make test ARGS="spark/tests/test_speech_target.py spark/tests/test_makefile.py spark/tests/test_by_hand.py -q --no-header"`, then `make lint`.
Expected: all pass (40 in `test_speech_target.py`); shellcheck silent.

- [ ] **Step 5: Commit.**

```bash
git add stack/host/by-hand-speech Makefile spark/tests/test_speech_target.py
git commit -m "feat(stack): 🤖 make speech starts the speech service, and make tunnel reaches it" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

<!-- listing: stack/host/by-hand-speech -->

````bash
#!/usr/bin/env bash
# TEMPORARY: remove at the speech service's Stage 2, when it moves behind the front and the gate (Dan's decision,
# 2026-11-09; website/design/speech-service.md).
# Delete then: stack/host/by-hand-speech (this file), spark/tests/test_speech_target.py, the Makefile's speech target
# with its comment, .PHONY name and lint entry, make tunnel's second forward, and website/how-to/speech-by-hand.md.
#
# make speech: Qwen3-ASR with its forced aligner in a locked-down container, outside the gate. On the Spark only, as
# Dan, from the clone, in tmux. Stop it for Phase 2a's drills and measurements.
#   make speech           the checks, then the container in the foreground; Ctrl-C stops it
#   make speech DO=stop   stops it from another terminal, then shows free -g and the deployed spark status
#
# BY_HAND_OPT and BY_HAND_RUN_DIR are for the tests; each defaults to the box's own path.
set -euo pipefail

OPT="${BY_HAND_OPT:-/opt/local-ai}"
if [ ! -d "$OPT" ]; then
  echo "make speech: it runs on the Spark only, and this machine has no $OPT" >&2
  exit 1
fi
SPARK="$OPT/app/.venv/bin/spark"
SPARK_PYTHON="$OPT/app/.venv/bin/python"
REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
HF="$HOME/speech/hf"
VENV="$HOME/speech/venv"
SOCKET_DIR="${BY_HAND_RUN_DIR:-/run/user/1000}/local-ai-speech"
CONTAINER=local-ai-speech
IMAGE="ubuntu:24.04@sha256:fe81560990fae86dc4743202b2cb4d3df98d0bcf949173cecef15ce1127ef852"
ASR_REVISION=7278e1e70fe206f11671096ffdd38061171dd6e5
ALIGNER_REVISION=c7cbfc2048c462b0d63a45797104fc9db3ad62b7
# What a load and a chunk took from MemAvailable in step 3 of the design's order: 11.9 GiB (Dan's clip, 2026-11-09).
NEED_GIB=12

refuse() {
  echo "make speech: $1" >&2
  exit 1
}

check_snapshot() {
  local folder="$HF/$1" revision="$2" found recorded
  [ -f "$folder/config.json" ] || refuse "no weights at $folder: the design's pins say what to download"
  found="$(find "$folder" \( -name '*.py' -o -name '*.bin' -o -name '*.pt' -o -name '*.pkl' \) -print -quit)"
  [ -z "$found" ] || refuse "$found is in the weights: a snapshot holds only safetensors and JSON, so it's not started"
  recorded="$(head -n 1 "$folder/.cache/huggingface/download/config.json.metadata" 2>/dev/null || true)"
  [ "$recorded" = "$revision" ] || refuse "$folder isn't at revision $revision"
}

free_for_a_load() {
  "$SPARK" status --json | "$SPARK_PYTHON" -I -c '
import json, sys

free = json.load(sys.stdin).get("memory", {}).get("free_for_a_load_gib")
print("" if free is None else format(free, ".1f"))' 2> /dev/null
}

start() {
  [ "$(id -u)" = 1000 ] || refuse "the socket's folder is uid 1000's, Dan's, and this is uid $(id -u)"
  [ -n "${TMUX:-}" ] || refuse "run it inside tmux (tmux new -As speech): its socket's folder goes when your last session ends"
  check_snapshot Qwen3-ASR-1.7B "$ASR_REVISION"
  check_snapshot Qwen3-ForcedAligner-0.6B "$ALIGNER_REVISION"

  local free
  free="$(free_for_a_load)" || refuse "spark status --json gave something it can't read, so nothing says there's room"
  [ -n "$free" ] || refuse "the gate isn't answering, so nothing says there's room: start it first"
  if ! awk -v free="$free" -v need="$NEED_GIB" 'BEGIN { exit !(free >= need) }'; then
    refuse "$free GiB free for a load, under the $NEED_GIB it needs: unload Qwen3.6 ($SPARK unload qwen3.6-35b-a3b), then run make speech again"
  fi

  if sudo docker inspect "$CONTAINER" > /dev/null 2>&1; then
    refuse "it's already running: make speech DO=stop stops it"
  fi

  UV_PROJECT_ENVIRONMENT="$VENV" uv sync --quiet --frozen --no-build --no-install-project --project "$REPO/stack/speech"

  # Nothing here runs the environment's Python; these only read its links. uv makes it two links deep: the environment
  # points at the minor-version folder, and that folder is a link to the real one. pyvenv.cfg names the minor-version
  # folder, so the container has both paths, each holding the real folder.
  local py_link py_real py_link_dir py_real_dir
  py_link="$(readlink -- "$VENV/bin/python")"
  py_real="$(readlink -f -- "$VENV/bin/python")"
  case "$py_link" in
    /*) ;;
    *) refuse "expected an absolute link at $VENV/bin/python, found $py_link" ;;
  esac
  py_link_dir="$(dirname -- "$(dirname -- "$py_link")")"
  py_real_dir="$(dirname -- "$(dirname -- "$py_real")")"

  local mounts=(
    -v "$VENV:/venv:ro"
    -v "$py_real_dir:$py_real_dir:ro"
    -v "$REPO/stack/speech/serve.py:/app/serve.py:ro"
    -v "$HF:/hf:ro"
    --mount "type=bind,source=$SOCKET_DIR,target=/run/speech"
  )
  if [ "$py_link_dir" != "$py_real_dir" ]; then
    mounts+=(-v "$py_real_dir:$py_link_dir:ro")
  fi

  rm -rf -- "$SOCKET_DIR"
  mkdir -m 0700 -- "$SOCKET_DIR"
  trap 'rm -rf -- "$SOCKET_DIR"' EXIT

  echo "make speech: loading, about a minute; stop it for Phase 2a's drills and measurements (Ctrl-C, or make speech DO=stop)"
  local rc=0
  sudo docker run --rm --init --name "$CONTAINER" \
    --gpus all --user 1000:1000 \
    --cap-drop ALL --security-opt no-new-privileges \
    --read-only --tmpfs /tmp:rw,nosuid,nodev,noexec,mode=1777,size=256m \
    --network none --memory 16g --memory-swap 16g --pids-limit 1024 \
    --oom-score-adj 1000 --log-driver none \
    -e HOME=/tmp -e USER=speech -e XDG_CACHE_HOME=/tmp/.cache \
    -e NUMBA_CACHE_DIR=/tmp/numba -e CUDA_CACHE_PATH=/tmp/nv -e PYTHONDONTWRITEBYTECODE=1 \
    -e HF_HOME=/tmp/hf -e HF_HUB_OFFLINE=1 -e HF_HUB_DISABLE_TELEMETRY=1 \
    "${mounts[@]}" \
    "$IMAGE" \
    /venv/bin/python /app/serve.py || rc=$?
  return "$rc"
}

stop() {
  local rc=0
  sudo docker stop -t 20 "$CONTAINER" || rc=$?
  rm -rf -- "$SOCKET_DIR"
  free -g
  "$SPARK" status
  return "$rc"
}

case "${1:-}" in
  '') start ;;
  stop) stop ;;
  *)
    echo "usage: make speech [DO=stop]" >&2
    exit 2
    ;;
esac
````

<!-- listing: spark/tests/test_speech_target.py -->

````python
"""make speech's script, stack/host/by-hand-speech: the speech service in its locked-down container, by hand (Dan's
decision, 2026-11-09; website/design/speech-service.md).

TEMPORARY: remove at the speech service's Stage 2. PIECES, below, lists every piece and what of it goes.

The script uses sudo and starts a container, so it never runs here for real: every tool it calls that acts on the box is
a stand-in on PATH that logs its call and changes only the test's own files, and the box's paths are the test's folders
(BY_HAND_OPT, BY_HAND_RUN_DIR, HOME)."""

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "stack" / "host" / "by-hand-speech"
ASR = "7278e1e70fe206f11671096ffdd38061171dd6e5"
ALIGNER = "c7cbfc2048c462b0d63a45797104fc9db3ad62b7"
IMAGE = "ubuntu:24.04@sha256:fe81560990fae86dc4743202b2cb4d3df98d0bcf949173cecef15ce1127ef852"

# TEMPORARY: every piece of make speech, and what of it goes at Stage 2: a search for NAMES finds only these files.
PIECES = {
    "Makefile": "the target, its comment, its .PHONY name, its script in lint's list and make tunnel's second forward",
    "stack/host/by-hand-speech": "the file",
    "spark/tests/test_speech_target.py": "the file",
    "stack/speech/serve.py": "the words naming it, when Stage 2 changes what starts the service",
    "stack/speech/pyproject.toml": "the words naming it, when Stage 2 changes what builds the environment",
    "website/how-to/speech-by-hand.md": "the file",
    "website/architecture.qmd": "the by-hand speech service, when Stage 2 moves it behind the front and the gate",
    "website/design/plan.md": "nothing: its dated notes and Revisions lines stay",
    "website/design/phase-2a.md": "nothing: a dated note",
    "website/design/speech-service.md": "nothing: it records the design",
    "website/design/speech-service-plan.md": "nothing: it records the build",
    "README.md": "Current state's line, when Stage 2 replaces it",
    "changelog.md": "nothing: a dated log",
}
NAMES = ("make speech", "by-hand-speech", "speech-by-hand")

STANDINS = {
    "sudo": ('case "$1 $2" in\n'
             '  "docker inspect") exit "${INSPECT_EXIT:-1}" ;;\n'
             '  "docker stop") exit "${STOP_EXIT:-0}" ;;\n'
             '  "docker run")\n'
             '    ls -ld "$BY_HAND_RUN_DIR/local-ai-speech" | cut -c1-10 > "$BOX/mode-at-run"\n'
             '    ls -A "$BY_HAND_RUN_DIR/local-ai-speech" > "$BOX/listing-at-run"\n'
             '    echo "speech: the server says it is up"\n'
             '    exit "${RUN_EXIT:-0}" ;;\n'
             'esac'),
    "uv": 'echo "UV_PROJECT_ENVIRONMENT=$UV_PROJECT_ENVIRONMENT" >> "$CALLS"\nexit "${UV_EXIT:-0}"',
    "id": 'echo "${ID_U:-1000}"',
    "free": 'echo "Mem:             121          4        110           0           6        117"',
}


def status(free) -> str:
    return json.dumps({"memory": {"available_gib": 60.0, "free_for_a_load_gib": free}, "gate": "ok"})


class Box:
    def __init__(self, tmp_path: Path):
        self.root = tmp_path
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        for name, body in STANDINS.items():
            self._standin(self.bin / name, f'echo "{name}${{*:+ $*}}" >> "$CALLS"\n{body}')
        self.home = tmp_path / "home"
        self.hf = self.home / "speech" / "hf"
        self.snapshot("Qwen3-ASR-1.7B", ASR)
        self.snapshot("Qwen3-ForcedAligner-0.6B", ALIGNER)
        # As uv makes it: the environment's python links to the minor-version folder, itself a link to the real one.
        self.python_root = self.home / ".local" / "share" / "uv" / "python"
        self.real = self.python_root / "cpython-3.12.14-linux-aarch64-gnu"
        (self.real / "bin").mkdir(parents=True)
        (self.real / "bin" / "python3.12").write_text("")
        self.minor = self.python_root / "cpython-3.12-linux-aarch64-gnu"
        self.minor.symlink_to(self.real)
        self.venv = self.home / "speech" / "venv"
        (self.venv / "bin").mkdir(parents=True)
        (self.venv / "bin" / "python").symlink_to(self.minor / "bin" / "python3.12")
        self.opt = tmp_path / "opt"
        venv_bin = self.opt / "app" / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        self._standin(venv_bin / "spark", 'echo "spark $*" >> "$CALLS"\nif [ "$1" = status ] && [ "${2:-}" = --json ]; then cat "$BOX/status"; fi')
        self._standin(venv_bin / "python", f'exec "{sys.executable}" "$@"')
        self.run_dir = tmp_path / "run"
        self.run_dir.mkdir()
        self.socket_dir = self.run_dir / "local-ai-speech"
        self.set_status(status(58.0))

    @staticmethod
    def _standin(path: Path, body: str) -> None:
        path.write_text(f"#!/usr/bin/env bash\n{body}\n")
        path.chmod(0o755)

    def snapshot(self, name: str, revision: str) -> None:
        folder = self.hf / name
        (folder / ".cache" / "huggingface" / "download").mkdir(parents=True)
        (folder / "config.json").write_text("{}")
        (folder / "model.safetensors").write_text("")
        (folder / ".cache" / "huggingface" / "download" / "config.json.metadata").write_text(f"{revision}\nabc\n1\n")

    def set_status(self, text: str) -> None:
        (self.root / "status").write_text(text)

    def env(self, **extra: str) -> dict[str, str]:
        env = {"PATH": f"{self.bin}:{os.environ['PATH']}", "HOME": str(self.home), "CALLS": str(self.root / "calls"),
               "BOX": str(self.root), "BY_HAND_OPT": str(self.opt), "BY_HAND_RUN_DIR": str(self.run_dir),
               "TMUX": "/tmp/tmux-1000/default,1,0", **extra}
        for name in STANDINS:  # never the real tool
            assert shutil.which(name, path=env["PATH"]) == str(self.bin / name), name
        return env

    def run(self, *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
        env = self.env(**extra)
        env = {key: value for key, value in env.items() if value != ""}
        return subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=60)

    def calls(self) -> list[str]:
        path = self.root / "calls"
        return path.read_text().splitlines() if path.exists() else []

    def ran(self) -> bool:
        return any(call.startswith("sudo docker run") for call in self.calls())


def refused(result, box, words: str) -> None:
    assert result.returncode == 1, result.stderr
    assert not box.ran()
    assert result.stderr.count("\n") == 1 and words in result.stderr, result.stderr


def test_off_the_spark_it_refuses_and_runs_nothing(tmp_path):
    box = Box(tmp_path)
    result = box.run(BY_HAND_OPT=str(tmp_path / "absent"))
    assert result.returncode == 1 and box.calls() == [] and result.stdout == ""
    assert result.stderr.count("\n") == 1 and "runs on the Spark" in result.stderr


def test_another_uid_is_refused(tmp_path):
    box = Box(tmp_path)
    refused(box.run(ID_U="1001"), box, "this is uid 1001")


def test_outside_tmux_it_is_refused(tmp_path):
    box = Box(tmp_path)
    refused(box.run(TMUX=""), box, "inside tmux")


@pytest.mark.parametrize("name", ["Qwen3-ASR-1.7B", "Qwen3-ForcedAligner-0.6B"])
@pytest.mark.parametrize("planted", ["modeling.py", "pytorch_model.bin", "weights.pt", "extra.pkl", "sub/deep.py"])
def test_a_snapshot_holding_code_is_refused(tmp_path, name, planted):
    box = Box(tmp_path)
    path = box.hf / name / planted
    path.parent.mkdir(exist_ok=True)
    path.write_text("")
    refused(box.run(), box, planted)


@pytest.mark.parametrize(("name", "revision"), [("Qwen3-ASR-1.7B", ALIGNER), ("Qwen3-ForcedAligner-0.6B", ASR)])
def test_a_snapshot_at_another_revision_is_refused(tmp_path, name, revision):
    box = Box(tmp_path)
    (box.hf / name / ".cache" / "huggingface" / "download" / "config.json.metadata").write_text(f"{revision}\n")
    refused(box.run(), box, "isn't at revision")


def test_a_missing_snapshot_is_refused(tmp_path):
    box = Box(tmp_path)
    shutil.rmtree(box.hf / "Qwen3-ASR-1.7B")
    refused(box.run(), box, "no weights at")


@pytest.mark.parametrize(("free", "starts"), [(12.0, True), (11.95, False), (0.0, False), (58.0, True)], ids=str)
def test_it_starts_only_with_12_gib_free_for_a_load(tmp_path, free, starts):
    box = Box(tmp_path)
    box.set_status(status(free))
    result = box.run()
    if starts:
        assert result.returncode == 0, result.stderr
        assert box.ran()
    else:
        refused(result, box, "unload Qwen3.6")
        assert "under the 12 it needs" in result.stderr
        assert "spark unload qwen3.6-35b-a3b" in result.stderr


@pytest.mark.parametrize("shape", [
    json.dumps({"memory": {"total_gib": 121, "available_gib": 60, "brake_gib": 20}, "gate": "not answering"}),
    status(None),
], ids=["the gate is down", "the gate has no figure"])
def test_it_is_refused_when_the_gate_gives_no_figure(tmp_path, shape):
    box = Box(tmp_path)
    box.set_status(shape)
    refused(box.run(), box, "the gate isn't answering")


def test_it_is_refused_when_the_status_cant_be_read(tmp_path):
    box = Box(tmp_path)
    box.set_status("not json")
    refused(box.run(), box, "can't read")


def test_a_second_start_is_refused_and_leaves_the_running_ones_socket(tmp_path):
    box = Box(tmp_path)
    box.socket_dir.mkdir(mode=0o700)
    (box.socket_dir / "speech.sock").write_text("")
    refused(box.run(INSPECT_EXIT="0"), box, "already running")
    assert (box.socket_dir / "speech.sock").exists()


def test_it_starts_the_container_with_the_designs_flags_and_mounts_exactly(tmp_path):
    box = Box(tmp_path)
    result = box.run()
    assert result.returncode == 0, result.stderr
    assert "speech: the server says it is up" in result.stdout
    assert "Phase 2a's drills" in result.stdout
    [run] = [call for call in box.calls() if call.startswith("sudo docker run")]
    assert run.split(" ") == [
        "sudo", "docker", "run", "--rm", "--init", "--name", "local-ai-speech",
        "--gpus", "all", "--user", "1000:1000",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--read-only", "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,mode=1777,size=256m",
        "--network", "none", "--memory", "16g", "--memory-swap", "16g", "--pids-limit", "1024",
        "--oom-score-adj", "1000", "--log-driver", "none",
        "-e", "HOME=/tmp", "-e", "USER=speech", "-e", "XDG_CACHE_HOME=/tmp/.cache",
        "-e", "NUMBA_CACHE_DIR=/tmp/numba", "-e", "CUDA_CACHE_PATH=/tmp/nv", "-e", "PYTHONDONTWRITEBYTECODE=1",
        "-e", "HF_HOME=/tmp/hf", "-e", "HF_HUB_OFFLINE=1", "-e", "HF_HUB_DISABLE_TELEMETRY=1",
        "-v", f"{box.venv}:/venv:ro",
        "-v", f"{box.real}:{box.real}:ro",
        "-v", f"{ROOT}/stack/speech/serve.py:/app/serve.py:ro",
        "-v", f"{box.hf}:/hf:ro",
        "--mount", f"type=bind,source={box.socket_dir},target=/run/speech",
        "-v", f"{box.real}:{box.minor}:ro",
        IMAGE, "/venv/bin/python", "/app/serve.py"]


def test_it_builds_the_environment_from_the_lock_and_never_builds_a_package(tmp_path):
    box = Box(tmp_path)
    assert box.run().returncode == 0
    calls = box.calls()
    uv = calls.index(f"uv sync --quiet --frozen --no-build --no-install-project --project {ROOT}/stack/speech")
    assert calls[uv - 1] != "" and f"UV_PROJECT_ENVIRONMENT={box.venv}" in calls
    assert uv < next(i for i, call in enumerate(calls) if call.startswith("sudo docker run"))


def test_a_failed_build_starts_nothing(tmp_path):
    box = Box(tmp_path)
    result = box.run(UV_EXIT="3")
    assert result.returncode != 0 and not box.ran()


def test_the_socket_folder_is_made_0700_and_empty_and_goes_when_it_stops(tmp_path):
    box = Box(tmp_path)
    box.socket_dir.mkdir(mode=0o755)
    (box.socket_dir / "stale.sock").write_text("")
    assert box.run().returncode == 0
    assert (box.root / "mode-at-run").read_text().strip() == "drwx------"
    assert (box.root / "listing-at-run").read_text() == ""
    assert not box.socket_dir.exists()


def test_a_container_that_fails_gives_its_status_and_still_empties_the_folder(tmp_path):
    box = Box(tmp_path)
    assert box.run(RUN_EXIT="3").returncode == 3
    assert not box.socket_dir.exists()


def test_stop_stops_it_empties_the_folder_and_shows_the_memory(tmp_path):
    box = Box(tmp_path)
    box.socket_dir.mkdir(mode=0o700)
    result = box.run("stop")
    assert result.returncode == 0, result.stderr
    assert box.calls() == ["sudo docker stop -t 20 local-ai-speech", "free -g", "spark status"]
    assert not box.socket_dir.exists()


def test_stop_with_nothing_running_says_so_and_still_empties_the_folder(tmp_path):
    box = Box(tmp_path)
    box.socket_dir.mkdir(mode=0o700)
    assert box.run("stop", STOP_EXIT="1").returncode == 1
    assert not box.socket_dir.exists()


def test_it_says_its_usage_for_any_other_do(tmp_path):
    box = Box(tmp_path)
    result = box.run("wait")
    assert result.returncode == 2 and box.calls() == [] and result.stderr.startswith("usage: make speech")


def test_it_is_shellcheck_clean():
    if shutil.which("shellcheck") is None:
        pytest.skip("shellcheck not installed")
    subprocess.run(["shellcheck", str(SCRIPT)], check=True)


def test_its_file_is_executable_as_the_others_are():
    assert stat.S_IMODE(SCRIPT.stat().st_mode) & stat.S_IXUSR


# The Makefile.

def make(*args: str, **env: str) -> str:
    run = subprocess.run(["make", "-s", "-n", "-C", str(ROOT), *args], capture_output=True, text=True, check=True,
                         env={"PATH": os.environ["PATH"], "HOME": os.environ["HOME"], **env})
    return run.stdout


@pytest.mark.parametrize(("args", "line"), [
    (["speech"], "BY_HAND_OPT='/opt/local-ai' bash stack/host/by-hand-speech ''"),
    (["speech", "DO=stop"], "BY_HAND_OPT='/opt/local-ai' bash stack/host/by-hand-speech 'stop'"),
], ids=["start", "stop"])
def test_make_speech_runs_its_script_with_do(args, line):
    assert make(*args).strip() == line


def test_make_help_says_temporary_on_the_spark_and_to_stop_it_for_the_drills():
    help_text = subprocess.run(["make", "-s", "-C", str(ROOT), "help"], capture_output=True, text=True, check=True).stdout
    [line] = [each for each in help_text.splitlines() if each.strip().startswith("speech ")]
    assert "TEMPORARY" in line and "on the Spark" in line and "drills" in line


def test_make_tunnel_forwards_the_model_api_and_the_speech_socket():
    assert make("tunnel").strip() == ("ssh -N -L 9100:127.0.0.1:9100 -L 127.0.0.1:58991:/run/user/1000/local-ai-speech/"
                                      "speech.sock ${SPARK_SSH_HOST:-brightroar}")


def test_make_lint_shellchecks_it():
    assert "stack/host/by-hand-speech" in make("lint").split()


def test_every_file_naming_it_is_on_the_removal_list():
    listed = subprocess.run(["git", "-C", str(ROOT), "ls-files", "--cached", "--others", "--exclude-standard"],
                            capture_output=True, text=True, check=True).stdout.splitlines()
    found = set()
    for name in listed:
        path = ROOT / name
        if path.is_file() and not path.is_symlink():
            text = path.read_bytes().decode("utf-8", errors="replace")
            if any(each in text for each in NAMES):
                found.add(name)
    assert found <= set(PIECES), found - set(PIECES)
    assert {"Makefile", "stack/host/by-hand-speech", "spark/tests/test_speech_target.py"} <= found
````

<!-- listing: makefile-edits.py -->

````python
#!/usr/bin/env python3
"""Task 2's Makefile edits, each an exact (old text, new text); run from the clone's root."""
import pathlib
import sys

EDITS = [
    ("clients-posit doctor fast-coder flash-next\n", "clients-posit doctor fast-coder flash-next speech\n"),
    # Split so this plan isn't among the files test_by_hand.py's search for the other by-hand targets finds.
    ("stack/host/by-hand-" "fast-coder stack/host/by-hand-" "flash-next\n",
     "stack/host/by-hand-" "fast-coder stack/host/by-hand-" "flash-next stack/host/by-hand-speech\n"),
    ("tunnel: ## On the Mac: forward the Spark's model API, 127.0.0.1:9100 (Ctrl-C closes it)\n"
     "\tssh -N -L 9100:127.0.0.1:9100 $${SPARK_SSH_HOST:-brightroar}\n",
     "tunnel: ## On the Mac: forward the Spark's model API, 127.0.0.1:9100, and the by-hand speech service, 127.0.0.1:58991 (Ctrl-C closes it)\n"
     "\tssh -N -L 9100:127.0.0.1:9100 -L 127.0.0.1:58991:/run/user/1000/local-ai-speech/speech.sock $${SPARK_SSH_HOST:-brightroar}\n"),
    ("\t@BY_HAND_OPT='$(SPARK_OPT)' MTP='$(MTP)' bash stack/host/by-hand-" "flash-next '$(DO)'\n",
     "\t@BY_HAND_OPT='$(SPARK_OPT)' MTP='$(MTP)' bash stack/host/by-hand-" "flash-next '$(DO)'\n"
     "\n"
     "# TEMPORARY: remove at the speech service's Stage 2, when it moves behind the front and the gate (Dan's decision,\n"
     "# 2026-11-09; website/design/speech-service.md). Delete then: this target, this comment, its .PHONY name, its script in\n"
     "# lint's list and make tunnel's second forward; stack/host/by-hand-speech, spark/tests/test_speech_target.py and\n"
     "# website/how-to/speech-by-hand.md.\n"
     "speech: ## TEMPORARY, on the Spark in tmux: Qwen3-ASR with word timings for vid2sub, outside the gate; DO=stop stops it, as Phase 2a's drills need\n"
     "\t@BY_HAND_OPT='$(SPARK_OPT)' bash stack/host/by-hand-speech '$(DO)'\n"),
]


def main() -> int:
    path = pathlib.Path("Makefile")
    text = path.read_text(encoding="utf-8")
    for old, new in EDITS:
        if text.count(old) != 1:
            print(f"Makefile: expected one match, found {text.count(old)}: {old[:60]!r}", file=sys.stderr)
            return 1
        text = text.replace(old, new)
    path.write_text(text, encoding="utf-8")
    print(f"applied {len(EDITS)} edits")
    return 0


if __name__ == "__main__":
    sys.exit(main())
````

***

### Task 3 [Spark]: the docs that change with it

*(Corrected 2026-11-09: the runbook and `docs-edits.py` below are as first written. The tree's versions also list the refusals Task 2's review added, and correct two things this listing has wrong: a request for Qwen3.6 loads it again if it fits, and `--to none` is there because the run's file names no translation model.)*

**Files:**

- Create: `website/how-to/speech-by-hand.md`
- Modify: `website/architecture.qmd`, `website/design/plan.md`, `website/design/phase-2a.md`,
  `website/design/speech-service.md`, with `docs-edits.py` below

**Interfaces:**

- Consumes: Task 2's target and tunnel, which the runbook describes.
- Produces: the Architecture page's new part and path, for the reviewer to check against Tasks 1 and 2.

The overview, `website/_overview.qmd`, needs no change: it doesn't show the speech engines by name. `spark doctor` needs
nothing, by SGLang's precedent. `changelog.md` and README's *Current state* wait for the first run, in Task 4.

- [ ] **Step 1: Add the runbook, then apply the edits.**

```bash
listing website/how-to/speech-by-hand.md
EDITS="$(mktemp -d)/docs-edits.py"
listing docs-edits.py "$EDITS"
uv run --frozen --project spark python -I "$EDITS"
```

Expected: `applied 18 edits`. Any miss stops with the file and the text that didn't match.

- [ ] **Step 2: Check them.**

Run: `make test ARGS="spark/tests/test_speech_target.py spark/tests/test_scenarios.py spark/tests/test_by_hand.py -q --no-header"`
Expected: all pass, `test_every_file_naming_it_is_on_the_removal_list` among them.

Then re-read each edited sentence against Tasks 1 and 2's code, and every `{mermaid}` block you changed for an unbalanced
quote. Rendering waits for the Mac (`make docs`).

- [ ] **Step 3: Commit.**

```bash
git add website/how-to/speech-by-hand.md website/architecture.qmd website/design/plan.md website/design/phase-2a.md website/design/speech-service.md
git commit -m "docs(plan): 🤖 the speech service's runbook, its place on the Architecture page, and the plan's notes" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

<!-- listing: website/how-to/speech-by-hand.md -->

````markdown
---
title: "Word timings for Mandarin and Cantonese, by hand"
description: "Qwen3-ASR and its forced aligner on the Spark for vid2sub: start make speech, reach it from the Mac, run an episode, and stop it. Temporary, until the speech service's Stage 2."
---

*(Added 2026-11-09, Dan's decisions; [the design](../design/speech-service.md).)*

This is the speech service's first stage: a Python service in a locked-down container, started by
hand, that gives vid2sub word timings for Mandarin and Cantonese with no change to vid2sub's parser.
It stays outside the front and the gate until its Stage 2, after the soak, so `spark status` counts
it as *other processes* and the brake can't unload it.

- **It needs Qwen3.6 unloaded,** which frees about 30 GiB. While it's out, requests for it are
  refused with the reason, the web UI's background tasks included. The by-hand SGLang coder can keep
  running.
- **Stop it for Phase 2a's drills and measurements:** Tasks 41, 43, 44, 45 and 47 to 50.
- **It has no key.** Its socket's folder is yours alone on the Spark, and on the Mac the tunnel's
  port takes requests only with its own `Host` and no `Origin`, so a web page can't reach it.

## Start it

**On the Spark**, as yourself, in the clone at `~/git/hub/local-ai` on the `phase-2a` branch, in
tmux, so its socket's folder stays when you log out. It asks for your `sudo` password once.

```bash
tmux new -As speech
/opt/local-ai/app/.venv/bin/spark unload qwen3.6-35b-a3b
make speech
```

It refuses to start if there's less than 12 GiB free for a load, if the gate isn't answering, if it
is already running, or if the weights in `~/speech/hf` aren't the pinned ones. It loads for about a
minute and prints `loaded: listening`, then one line per chunk: its length, the time taken and the
status, never the text.

To check it, **on the Spark**, in another terminal:

```bash
curl -s --unix-socket /run/user/1000/local-ai-speech/speech.sock http://127.0.0.1:58991/v1/models
```

It prints `{"object":"list","data":[{"id":"qwen3-asr-1.7b"}]}`.

## Run an episode from the Mac

**On the Mac**, in your clone of this repo on the `phase-2a` branch, leave the tunnel open in a spare
terminal. It also carries the model API, as before.

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

**On the Mac**, where you run vid2sub, give it the spoken language, `zh` or `yue`, because its
auto-detection skips Mandarin on this kind of endpoint. `--to none` makes captions in the spoken
language only, since Qwen3.6, which would translate them, is unloaded:

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
with a connection error. `make speech` starts it again.

## Stop it

Press Ctrl-C in its tmux window. Or, **on the Spark**, from the clone at `~/git/hub/local-ai`:

```bash
make speech DO=stop
```

Then, **on the Spark**, load Qwen3.6 again:

```bash
/opt/local-ai/app/.venv/bin/spark load qwen3.6-35b-a3b
```

The service's Stage 2 retires this page, the target and the second forward in `make tunnel`.
````

<!-- listing: docs-edits.py -->

````python
#!/usr/bin/env python3
"""Task 4's documentation edits, each an exact (file, old text, new text); run from the clone's root."""
import pathlib
import sys

SERVICE_NODE = ('    flash["Flash-Next, llama.cpp<br/>loads after make-room · Phase 4a"]:::planned\n')
EDITS = [
    # The Architecture page: the big diagram, the speech diagram, its prose, and the scenario table.
    ("website/architecture.qmd",
     '    pyapps_mac["my Python apps<br/>openai, chatlas, querychat"]\n',
     '    pyapps_mac["my Python apps<br/>openai, chatlas, querychat"]\n    vid2sub_mac["vid2sub"]\n'),
    ("website/architecture.qmd", SERVICE_NODE,
     SERVICE_NODE + '    speech["the speech service, by hand · make speech<br/>Qwen3-ASR and its aligner in Docker<br/>a socket of Dan\'s, outside the gate"]\n'),
    ("website/architecture.qmd",
     '  pyapps_mac -- "same tunnel" --> front\n',
     '  pyapps_mac -- "same tunnel" --> front\n  vid2sub_mac -- "same tunnel" --> front\n'
     '  vid2sub_mac -- "same tunnel, port 58991, while it runs" --> speech\n'),
    ("website/architecture.qmd",
     '  swap -.-> batch["whisper, batch"]:::planned\n',
     '  swap -.-> batch["whisper, batch"]:::planned\n'
     '  vid2sub["vid2sub on the Mac"] -- "make tunnel, port 58991,<br/>no key" --> speechsvc["the speech service, by hand · make speech<br/>Qwen3-ASR 1.7B and its forced aligner, word timings<br/>outside the front and the gate until its Stage 2"]\n'),
    ("website/architecture.qmd",
     "returns text only: captions' timestamps need its aligner, which the stack doesn't serve yet, and\n"
     "Phase 3's speech comparison picks a served one.\n",
     "returns text only: captions' timestamps need its aligner, which the stack doesn't serve yet, and\n"
     "Phase 3's speech comparison picks a served one. *(2026-11-09, Dan's decisions: [the speech\n"
     "service](design/speech-service.md) gives Qwen3-ASR its word timings, by hand for now ([its\n"
     "runbook](how-to/speech-by-hand.md)), in a container of its own, on a Unix socket of Dan's that\n"
     "`make tunnel` forwards to 127.0.0.1:58991. It stays outside the front and the gate until its Stage\n"
     "2, after the soak, which takes this part of Phase 3 early. Qwen3.6 is unloaded for each run.)*\n"),
    ("website/architecture.qmd",
     "| [S26 · What it cost](scenarios/s26-what-it-cost.md) |",
     "| [Word timings by hand](how-to/speech-by-hand.md) (a runbook, no numbered scenario) | Apps and speech: the by-hand speech service, until its Stage 2 |\n"
     "| [S26 · What it cost](scenarios/s26-what-it-cost.md) |"),

    # The plan: Speech, Phase 3, the 10-23 Revisions line, and a new Revisions line.
    ("website/design/plan.md",
     "  Phase 3 speech comparison adds Qwen3-ASR and a served aligner (vLLM's or CrispASR's) to its\n"
     "  candidates. Whisper keeps the `stt` role and the web UI's speech-to-text.\n",
     "  Phase 3 speech comparison adds Qwen3-ASR and a served aligner (vLLM's or CrispASR's) to its\n"
     "  candidates. Whisper keeps the `stt` role and the web UI's speech-to-text. *(2026-11-09, Dan's\n"
     "  decisions: captions get their timings from the speech service, the bullet below, ahead of Phase 3's\n"
     "  comparison.)*\n"
     "- *(2026-11-09, Dan's decisions, GitHub issue #35; Revisions.)* **The speech service**\n"
     "  ([its design](speech-service.md)): the original Qwen3-ASR 1.7B and its forced aligner in one\n"
     "  Python service that answers `/v1/audio/transcriptions` as OpenAI's `verbose_json`, for vid2sub's\n"
     "  word timings in Mandarin and Cantonese. Stage 1 is by hand, like the by-hand SGLang coder:\n"
     "  `make speech`, in a locked-down container, on a Unix socket of Dan's forwarded by `make tunnel`,\n"
     "  outside the front and the gate ([its runbook](../how-to/speech-by-hand.md)). Stage 2 moves it behind\n"
     "  them after the soak, with diarization and speaker embeddings for Dan's own pipelines.\n"),
    ("website/design/plan.md",
     "  llama.cpp, with a served aligner for its timestamps, vLLM's or CrispASR's; *Speech*)* · the\n",
     "  llama.cpp, with a served aligner for its timestamps, vLLM's or CrispASR's; *Speech*)*\n"
     "  *(2026-11-09, Dan's decisions: the speech service's Stage 2 is that served aligner unless this\n"
     "  comparison finds a better one; its Stage 1 runs by hand until then; *Speech*)* · the\n"),
    ("website/design/plan.md",
     "  captions project runs the aligner itself until Phase 3's speech comparison picks a served one. Each\n",
     "  captions project runs the aligner itself until Phase 3's speech comparison picks a served one.\n"
     "  *(2026-11-09, Dan's decisions: until then it can use the by-hand speech service; *Speech*.)* Each\n"),
    ("website/design/plan.md",
     "\n## Sources\n",
     "\n- **2026-11-09** — The speech service's design, approved by Dan (\"you can build\"), and its Stage 1\n"
     "  built on `phase-2a` (GitHub issue #35; [the design](speech-service.md), [the build\n"
     "  plan](speech-service-plan.md)). vid2sub needs word timings for Mandarin and Cantonese, which\n"
     "  llama.cpp's Qwen3-ASR can't give. So one Python service runs the original 1.7B and its forced\n"
     "  aligner, in a locked-down container, started by hand with `make speech`, outside the front and the\n"
     "  gate, as the by-hand SGLang coder is, which Dan approved. *Speech* and Phase 3 gain dated notes,\n"
     "  the Architecture page shows it, and Tasks 41, 43, 44, 45 and 47 to 50 run with it stopped. Each\n"
     "  run unloads Qwen3.6. Its Stage 2 moves it behind the front and the gate after the soak.\n"
     "\n## Sources\n"),

    # Task 41's precondition.
    ("website/design/phase-2a.md",
     "### Task 41 [Spark]: the CUDA-allocatable ceiling, carefully\n\n",
     "### Task 41 [Spark]: the CUDA-allocatable ceiling, carefully\n\n"
     "*(2026-11-09, Dan's decisions; [the speech service's design](speech-service.md), constraint 4: this\n"
     "task runs with `make speech` stopped, as do Tasks 43, 44, 45 and 47 to 50.)*\n\n"),

    # The design: what the build settled.
    ("website/design/speech-service.md",
     "*Status: written 2026-11-09, revised the same day after a six-member council (five Claude reviewers and the local\n"
     "Qwen3.6), for Dan's review. Nothing here is built. Stage 1 is a by-hand exception to \"nothing loads around the gate\",\n"
     "which Dan approved the same day, as he did for the by-hand fast coder.*\n",
     "*Status: written 2026-11-09, revised the same day after a six-member council (five Claude reviewers and the local\n"
     "Qwen3.6), and approved by Dan that day (\"you can build\"). Stage 1's service, target and tests are built on\n"
     "`phase-2a`; [the build plan](speech-service-plan.md) has them. It hadn't yet run as a service on the box when this\n"
     "was written; `changelog.md` records its first run. Stage 1 is a by-hand exception to \"nothing loads around the gate\",\n"
     "which Dan approved the same day, as he did for the by-hand fast coder.*\n"),
    ("website/design/speech-service.md",
     "  character to its last, with the unit's times. Whitespace runs become one ASCII space. Only if the walk can't consume\n"
     "  every kept character does the chunk go back with no `words`, and the service logs it.\n",
     "  character to its last, with the unit's times. Whitespace runs become one ASCII space. Only if the walk can't consume\n"
     "  every kept character does the chunk go back with no `words`, and the service logs it. *(Found while building it,\n"
     "  2026-11-09: vid2sub's `attach_punctuation` rebuilds a gap between two words from at most one space, so `A - B` would\n"
     "  come back as `A -B` and cost the chunk its timings. A word therefore takes in all but the last space of the gap after\n"
     "  it, and the first word all but the first space of the gap before it: there `A -` is one word.)*\n"),
    ("website/design/speech-service.md",
     "| 400 | `bad_request` | not FLAC by its magic, over 48 kHz or 2 channels, an unsupported language, an unreadable file |\n",
     "| 400 | `bad_request` | not FLAC by its magic, over 48 kHz or 2 channels, an unsupported language, an unreadable file, a prompt over 4 KiB, a form with no audio |\n"
     "| 403 | `forbidden` | an `Origin` header, or a `Host` other than the tunnel's (added at the build, 2026-11-09) |\n"),
    ("website/design/speech-service.md",
     "| 503 | `no_fit` | `MemAvailable`, less the request growth step 3 measures, under 15 GiB; or CUDA ran out of memory |\n",
     "| 503 | `no_fit` | `MemAvailable`, less the 3.5 GiB of request growth step 3 measured, under 15 GiB; or CUDA ran out of memory |\n"
     "| 500 | `failed` | anything else that fails a chunk, which vid2sub retries twice; the log has the exception's type and place, never its message (added at the build, 2026-11-09) |\n"),
    ("website/design/speech-service.md",
     "SGLang's does: `sudo docker run --init --name local-ai-speech --gpus all --user 1000:1000 --cap-drop ALL\n",
     "SGLang's does: `sudo docker run --rm --init --name local-ai-speech --gpus all --user 1000:1000 --cap-drop ALL\n"),
    ("website/design/speech-service.md",
     "are absolute, `stack/speech/serve.py` alone and never the clone, and the weights. Writable: the socket's folder. The\n"
     "environment is built on the host with `uv sync --frozen --no-build --no-install-project`, and nothing on the host runs\n"
     "it.\n",
     "are absolute, `stack/speech/serve.py` alone and never the clone, and the weights. Writable: the socket's folder. The\n"
     "environment is built on the host, at `~/speech/venv`, with `uv sync --frozen --no-build --no-install-project`, and\n"
     "nothing on the host runs it.\n"),
    ("website/design/speech-service.md",
     "from `stack/host/by-hand-speech`. It refuses to start if `spark status --json`'s `free_for_a_load_gib` is under\n"
     "the peak step 3 measures, or the gate doesn't answer, or Dan's uid isn't 1000. It has its own test file and removal\n"
     "list, since it goes at Stage 2, not with the other by-hand targets at Phase 4a. Its help says to stop it for Phase\n"
     "2a's drills.\n",
     "from `stack/host/by-hand-speech`. It refuses to start if `spark status --json`'s `free_for_a_load_gib` is under\n"
     "the 12 GiB step 3 measured, or the gate doesn't answer, or Dan's uid isn't 1000, or outside tmux, or if it's already\n"
     "running, or if a snapshot isn't at its pinned revision. It has its own test file and removal list, since it goes at\n"
     "Stage 2, not with the other by-hand targets at Phase 4a. Its help says to stop it for Phase 2a's drills. [Its\n"
     "runbook](../how-to/speech-by-hand.md) has the steps.\n"),
]


def main() -> int:
    for name, old, new in EDITS:
        path = pathlib.Path(name)
        text = path.read_text(encoding="utf-8")
        if text.count(old) != 1:
            print(f"{name}: expected one match, found {text.count(old)}: {old[:60]!r}", file=sys.stderr)
            return 1
        path.write_text(text.replace(old, new), encoding="utf-8")
    print(f"applied {len(EDITS)} edits")
    return 0


if __name__ == "__main__":
    sys.exit(main())
````

***

### Task 4 [Dan], then [Spark]: the first run on the box

Nothing above ran the real models through the service. This does. **Stop and ask Dan** if the brake fires, if the peak
doesn't fit beside SGLang with Qwen3.6 unloaded, or if a chunk takes more than vid2sub's 900 s timeout.

- [ ] **Step 1 [Dan]: start it.** **On the Spark**, as Dan, open a tmux window:

```bash
tmux new -As speech
```

**On the Spark**, inside that tmux window, with the clone on `phase-2a`. Paste this as a block of its own: pasted
with `tmux new`, its later lines can run in the outer shell when tmux exits.

```bash
cd ~/git/hub/local-ai
/opt/local-ai/app/.venv/bin/spark unload qwen3.6-35b-a3b
free -g
make speech
```

If `spark unload` says it isn't loaded, that's fine. Expected: it asks for the `sudo` password once, then within about a minute prints `loaded: listening on /run/speech/speech.sock`.

- [ ] **Step 2 [Spark]: the session checks it,** through the socket, with the 161 s clip from the spike. Record
`MemAvailable` before and after, and the time.

```bash
SOCK=/run/user/1000/local-ai-speech/speech.sock
curl -s --unix-socket "$SOCK" http://127.0.0.1:58991/health
curl -s --unix-socket "$SOCK" http://127.0.0.1:58991/v1/models
grep MemAvailable /proc/meminfo
time curl -s --unix-socket "$SOCK" http://127.0.0.1:58991/v1/audio/transcriptions -F file=@"$HOME/speech/spike/clip.flac" -F model=qwen3-asr-1.7b -F language=zh -F response_format=verbose_json -o "$HOME/speech/spike/out/service.json" -w '%{http_code}\n'
grep MemAvailable /proc/meminfo
journalctl -u local-ai-brake.service -n 5 --no-pager
```

Expected: `{"status":"ok"}`; the model list; `200`, in about 12 s; `MemAvailable` falling by about 3.5 GiB for the
request; nothing new from the brake.

- [ ] **Step 3 [Spark]: compare it with vid2sub's real parser.** The session runs the service's answer, Step 2's
`service.json`, through `spark/tests/vid2sub_parser.py`, the lasting copy of vid2sub's parser from `ac29e7b`, from
`make test`'s environment (`uv run --frozen --project spark python -I`, with that file on the path), and gives no
other command for the comparison. Words kept must equal the words sent, and the text must match. Expected: the 489 of 489 words the spike reached, give or take any
change in a re-run of the model. Then send the refusals their real paths: a `.wav` (400) and `Origin:` (403).

- [ ] **Step 4 [Dan]: stop it,** Ctrl-C in its tmux window. Expected: back at the prompt, and the socket's folder gone.

- [ ] **Step 5 [Spark]: record it.** `changelog.md` gains a dated entry and README's *Current state* gains the new current
state (the speech service, by hand, off when not in use; `~/speech`), with this step's measured figures. The two brake
firings of 2026-11-09 go in the same entry, as the design's step 2 records them. If a measured figure differs from
`REQUEST_GROWTH_GIB` or `NEED_GIB`, say so and correct them, with the spec, in this commit; the `git add` line then also takes
`stack/speech/serve.py stack/host/by-hand-speech website/design/speech-service.md`.

```bash
git add changelog.md README.md
git commit -m "docs(changelog): 🤖 the speech service's first run on the box" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 5 [Mac] and [Dan]: the done test

*(2026-11-09, the first attempt: the episode ran through, with the brake quiet and memory never under 51.5 GiB, but 12
of its 32 chunks looped to the token limit because the service passed vid2sub's `prompt` to the model. Dan's decision:
the service ignores `prompt` ([the design](speech-service.md), *The answer*), and the run is repeated.)*

*(Corrected later that day: the rerun, with no prompt, looped on the same 12 chunks, so the "because" above is wrong.
The done test isn't met: the transcript is mostly missing or invented on those chunks. The design's *The answer* has what's
known. Steps 5 and 6 below stay open until the cause is settled.)*

This is the design's step 6. Dan's definition of done: a real Mandarin or Cantonese episode, start to finish, with
correct word timings, while he keeps coding on the by-hand SGLang coder, and no change to vid2sub's parser.

- [ ] **Step 1 [Spark]: push.** Dan's standing OK covers this. Run *Before every push* in `website/how-to/leak-guards.md`
first, then `git push origin phase-2a`.

- [ ] **Step 2 [Mac]: pull and open the tunnel.** **On the Mac**, in the clone on `phase-2a`, close any tunnel already
open first: one opened before this pull has no forward to the service, and a second `make tunnel` can't bind 9100.
Then:

```bash
git pull
make tunnel
```

Expected: it stays open, with no error. Then, in another terminal on the Mac, in the same clone,
`make test ARGS="spark/tests/test_speech_target.py spark/tests/test_speech.py -q"`, for the Mac's bash 3.2 and make 3.81.
`test_speech.py`'s environment checks skip on the Mac, as they should.

- [ ] **Step 3 [Dan]: start it** as in Task 4's Step 1, on the Spark, beside SGLang.

- [ ] **Step 4 [Mac]: the config and a dry run.** **On the Mac**, save the TOML from the runbook as `~/speech-spark.toml`,
and copy the episode into a new, empty folder, because vid2sub reuses a sidecar it finds beside the video and doesn't
transcribe again. Then, in that folder:

```bash
vid2sub run "Episode.mkv" --config ~/speech-spark.toml --model spark-qwen --from zh --to none --dry-run
```

Expected: it says it would use `spark-qwen`, and writes nothing. If it says `transcribe: reuse the sidecar`, stop: the
run wouldn't touch the Spark.

- [ ] **Step 5 [Dan]: the episode.** **On the Mac**, in that folder, with SGLang in real use:

```bash
vid2sub run "Episode.mkv" --config ~/speech-spark.toml --model spark-qwen --from zh --to none
```

While the episode runs, the session's job is a memory log. **On the Spark**, in a second terminal, started before
the episode, noting the time it started, and ended with Ctrl-C once the episode is over:

```bash
while sleep 5; do date +%T; grep MemAvailable /proc/meminfo; done >> ~/speech/spike/out/episode-mem.log
```

After the episode, **on the Spark**, the lowest figure, then the brake's journal since the start:

```bash
grep MemAvailable ~/speech/spike/out/episode-mem.log | sort -k2 -n | head -n 1
journalctl -u local-ai-brake.service --since "<the time it started>" --no-pager
```

Record both. If the brake fired, stop and ask Dan, as Task 4 says.

Also record: no cue flagged `est`, the share of segments with words, three places spot-checked by ear, and the times against
the Mac's result if one is kept for that episode. Also the slowest chunk's time while SGLang is generating, which the
design's step 4 left unmeasured, and how the longest stretched units read in subtitles.

- [ ] **Step 6 [Spark]: record the result** in the design's *The order*, step 6, and in `changelog.md`, without an
episode's name or its transcript. Stop the service (`make speech DO=stop`, **on the Spark**) and load Qwen3.6 again, so
Phase 2a's tasks find the box as they expect.

```bash
git add website/design/speech-service.md changelog.md
git commit -m "docs(plan): 🤖 the speech service's done test" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

## After this

Before Dan's first run, a whole-branch review of Tasks 1 to 3 on Opus, with security as one of its lenses, since this
adds a port on the Mac, a socket, and a third-party package on the GPU. Per task, a review and a backward and forward
pass; findings that change a later step go in this plan and the spec, with a Revisions line. Stage 2, and everything
under *Not in Stage 1*, is out of scope here.

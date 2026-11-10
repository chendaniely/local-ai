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
    return Upload(audio, text_field(form, "model"), text_field(form, "language"))


def decode_flac(data: bytes) -> tuple[Any, int]:
    """(samples, rate). Passed on as that pair and never as a string, since the package's loader fetches URLs and opens
    paths. Read to at most 300 s and one frame, whatever length the file claims."""
    import soundfile

    class ForwardOnly(soundfile.SoundFile):
        """FLAC written to a pipe, as vid2sub's ffmpeg does, has no sample count in its header. libsndfile then reports
        a length of 2^63-1, and the seek that `read` makes after reaching the end fails, so this reads forward only."""

        def seekable(self) -> bool:
            return False

    try:
        with ForwardOnly(io.BytesIO(data)) as flac:
            if flac.format != "FLAC":
                raise bad_request("the audio isn't FLAC")
            rate, channels = flac.samplerate, flac.channels
            if rate > MAX_RATE or channels > MAX_CHANNELS:
                raise bad_request(f"the audio is over {MAX_RATE} Hz or {MAX_CHANNELS} channels")
            audio = flac.read(frames=MAX_SECONDS * rate + 1, dtype="float32", always_2d=False)
    except soundfile.LibsndfileError:
        raise bad_request("the audio can't be read") from None
    # Read forward only, a file holding just its header gives no audio and no error.
    if audio.shape[0] == 0:
        raise bad_request("the audio holds no samples")
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
    """torch.cuda.OutOfMemoryError, recognised by name, as torch may not be importable where this runs, or torch's
    words for it, which also come as `CUDA error: out of memory`. The message is only tested here and never logged."""
    return type(error).__name__ == "OutOfMemoryError" or "out of memory" in str(error).lower()


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
            language = language_in(upload.language)
            if not upload.audio.startswith(b"fLaC"):
                raise bad_request("the audio isn't FLAC")
            if self._busy:
                raise Refusal(429, "busy", "another chunk is running, and none wait", retry_after_s=BUSY_RETRY_S)
            self._busy = True
            future = self._worker.submit(self._run, upload.audio, language)
            future.add_done_callback(self._free)
            return JSONResponse(await asyncio.wrap_future(future))
        except Refusal as refusal:
            log.info("refused: status %d, %s", refusal.status, refusal.code)
            return refusal.response()

    def _run(self, data: bytes, language: str | None) -> dict[str, Any]:
        """On the worker thread: decode, check the room, transcribe."""
        started = time.monotonic()
        seconds, status = 0.0, 200
        try:
            audio, rate = self._decode(data)
            seconds = audio.shape[0] / rate
            check_room(self._mem_available)
            # vid2sub's `prompt`, the end of the chunk before, is left out: passed as the context, it made the model loop
            # on 12 of a real episode's 32 chunks (2026-11-09; the design's *The answer*).
            [result] = self._model.transcribe(audio=(audio, rate), context="", language=language,
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
            except Exception as error:  # only this thread bounds CUDA memory, so nothing may end it silently
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

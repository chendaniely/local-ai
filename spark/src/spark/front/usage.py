"""The front's usage scanner (the usage meter's counter, Task 44a): the numbers of one request, read from its response
as the chunks pass, holding a fixed head and tail of each line and never more.

A stream is read per data line, a body that isn't streamed as one line; of each line it keeps the first HEAD bytes and
the last TAIL, so a multi-megabyte line costs the same memory as a short one. At a line's end it takes the last
`"usage"` and the last `"timings"` object from the tail (the head, for a usage that sits before an embeddings body's
vectors), each matched by exactly OBJECT after its key, which takes linear time on any input, and parses only that
object. llama-server b11146 puts them at the end of the last chunk, and at the end of a whole body (server-task.cpp).
Nothing from a client or from the model's text becomes a counted field: the numbers are checked ints, and the
response's id passes `safe_text`."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from spark.front import parse


HEAD = 4096
TAIL = 16384
LIMIT = 2**53  # a counted value is a non-negative int below this

ROUTE_NAMES = {
    "/v1/chat/completions": "chat",
    "/v1/completions": "completions",
    "/v1/responses": "responses",
    "/v1/messages": "messages",
    "/v1/embeddings": "embeddings",
    "/v1/audio/transcriptions": "transcriptions",
}
if set(ROUTE_NAMES) != set(parse.INFERENCE_PATHS):  # a new route gets a name here
    raise ImportError("usage.ROUTE_NAMES doesn't name exactly parse.INFERENCE_PATHS")
ROUTES_COUNTED = ("/v1/chat/completions", "/v1/completions", "/v1/embeddings")

# One object after its key, one level of nesting. The two alternatives start with different bytes, so the match never
# backtracks into a choice: linear time (a `[^{}]+` inside the loop is exponential on an unclosed object).
OBJECT = rb"\{(?:[^{}]|\{[^{}]*\})*\}"
_OBJECT = re.compile(OBJECT)
_USAGE_KEY = re.compile(rb'"usage"\s*:\s*')
_TIMINGS_KEY = re.compile(rb'"timings"\s*:\s*')
_TRIES = 4  # key copies tried from the last backward, so unclosed copies cost a bounded number of scans
_THINK = re.compile(rb'"reasoning_content"\s*:\s*"')
_TEXT = re.compile(rb'"content"\s*:\s*"')
_ID = re.compile(rb'"id"\s*:\s*"([^"\\]{1,200})"')
_EOL = re.compile(rb"\r\n|\r|\n")
_SAFE = re.compile(r"[A-Za-z0-9._:-]{1,64}", re.ASCII)

Source = Literal["usage", "timings", "none"]


@dataclass(frozen=True)
class Counts:
    p: int | None = None  # prompt tokens, cached ones included
    c: int | None = None  # cached prompt tokens; None when not reported, never 0 for that
    o: int | None = None  # output tokens
    source: Source = "none"
    ev_think: int | None = None  # events with reasoning text (streams)
    ev_text: int | None = None  # events with answer text (streams)
    prompt_ms: int | None = None
    gen_ms: int | None = None
    resp_id: str | None = None


def safe_text(value: object) -> str | None:
    """`value` if it is text of `[A-Za-z0-9._:-]{1,64}` only, else None."""
    if isinstance(value, str) and _SAFE.fullmatch(value):
        return value
    return None


def _count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < LIMIT:
        return None
    return value


def _millis(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not 0 <= value < LIMIT:  # also false for nan
        return None
    return int(round(value))


def _last_object(key: re.Pattern[bytes], *parts: bytes) -> dict | None:
    """The last JSON object after `key` in the first of `parts` that has one."""
    for part in parts:
        starts = [found.end() for found in key.finditer(part)]
        for start in starts[-1:-_TRIES - 1:-1]:
            found = _OBJECT.match(part, start)
            if found:
                try:
                    obj = json.loads(found.group())
                except ValueError:
                    continue
                if isinstance(obj, dict):
                    return obj
    return None


def _field(obj: object, name: str) -> int | None:
    return _count(obj.get(name)) if isinstance(obj, dict) else None


class UsageScan:
    """Scans one response. `feed(chunk)` as its bytes pass, `finish()` once for its Counts (again, the same ones)."""

    def __init__(self, path: str, *, streamed: bool):
        self.path = path
        self.streamed = streamed
        self._head = bytearray()
        self._tail = bytearray()
        self._cr = False
        self._usage: dict | None = None
        self._timings: dict | None = None
        self._think = 0
        self._text = 0
        self._id: str | None = None
        self._id_done = False
        self._counts: Counts | None = None

    def held_bytes(self) -> int:
        return len(self._head) + len(self._tail)

    def feed(self, chunk: bytes) -> None:
        if self._counts is not None:
            return
        if not self.streamed:
            self._take(chunk, 0, len(chunk))
            return
        at = 0
        if self._cr:
            self._cr = False
            if chunk[:1] == b"\n":
                at = 1
        for end in _EOL.finditer(chunk, at):
            self._take(chunk, at, end.start())
            self._line_ends()
            at = end.end()
            if end.group() == b"\r" and at == len(chunk):
                self._cr = True
        self._take(chunk, at, len(chunk))

    def _take(self, chunk: bytes, start: int, stop: int) -> None:
        room = HEAD - len(self._head)
        if room > 0:
            self._head += chunk[start:min(stop, start + room)]
        if stop - start >= TAIL:
            self._tail[:] = chunk[stop - TAIL:stop]
        else:
            self._tail += chunk[start:stop]
            if len(self._tail) > TAIL:
                del self._tail[:len(self._tail) - TAIL]

    def _line_ends(self) -> None:
        head, tail = bytes(self._head), bytes(self._tail)
        self._head.clear()
        self._tail.clear()
        if not head.startswith(b"data:"):  # an event name, a ping, a blank line
            return
        if _THINK.search(head):
            self._think += 1
        if _TEXT.search(head):
            self._text += 1
        if not self._id_done:
            self._id_done = True
            # The last id: llama-server puts the top-level one after `choices`, whose tool calls carry the model's own.
            found = list(_ID.finditer(tail)) or list(_ID.finditer(head))
            self._id = safe_text(found[-1].group(1).decode("ascii", "replace")) if found else None
        self._look(head, tail)

    def _look(self, head: bytes, tail: bytes) -> None:
        usage = _last_object(_USAGE_KEY, tail, head)
        if usage is not None:
            self._usage = usage
        timings = _last_object(_TIMINGS_KEY, tail, head)
        if timings is not None:
            self._timings = timings

    def finish(self) -> Counts:
        if self._counts is not None:
            return self._counts
        if self.streamed:
            if self._head:
                self._line_ends()
        else:
            head, tail = bytes(self._head), bytes(self._tail)
            self._look(head, tail)
            self._id = self._body_id(tail)
        self._head.clear()
        self._tail.clear()
        self._counts = self._result()
        return self._counts

    @staticmethod
    def _body_id(tail: bytes) -> str | None:
        """The body's top-level id: the last one in the tail, and, when a tool call is there, only one after the
        last `"tool_calls"` and the `"usage"` that follows the choices."""
        floor = 0
        if b'"tool_calls"' in tail:
            usage = [found.start() for found in _USAGE_KEY.finditer(tail)]
            if not usage or usage[-1] < tail.rfind(b'"tool_calls"'):
                return None
            floor = usage[-1]
        found = [m for m in _ID.finditer(tail) if m.start() >= floor]
        return safe_text(found[-1].group(1).decode("ascii", "replace")) if found else None

    def _result(self) -> Counts:
        usage, timings = self._usage, self._timings
        prompt_ms = gen_ms = None
        if timings is not None:
            prompt_ms = _millis(timings.get("prompt_ms"))
            gen_ms = _millis(timings.get("predicted_ms"))
        p = c = o = None
        source: Source = "none"
        if usage is not None:
            p, o = _field(usage, "prompt_tokens"), _field(usage, "completion_tokens")
            c = _field(usage.get("prompt_tokens_details"), "cached_tokens")
            if c is None:
                c = _field(timings, "cache_n")
            if self.path == "/v1/embeddings" and p is not None:
                o = 0
            source = "usage"
        elif timings is not None:
            cache = _field(timings, "cache_n")
            prompt = _field(timings, "prompt_n")
            if prompt is not None:
                p = _count(prompt + (cache or 0))
            c, o = cache, _field(timings, "predicted_n")
            source = "timings"
        if p is None and c is None and o is None:
            source, prompt_ms, gen_ms = "none", None, None
        streamed = self.streamed
        return Counts(p, c, o, source, self._think if streamed else None, self._text if streamed else None,
                      prompt_ms, gen_ms, self._id)


def scan_for(path: str, status: int, headers: Iterable[tuple[bytes, bytes]]) -> UsageScan | None:
    """A UsageScan for a 2xx answer on a counted route, or None. `streamed` reads the media type as
    upstream.StreamEnd.watching does; an encoded body can't be scanned, so it gets None."""
    if path not in ROUTES_COUNTED or not 200 <= status < 300:
        return None
    found = {name.lower(): value for name, value in headers}
    if found.get(b"content-encoding", b"identity").strip().lower() != b"identity":
        return None
    kind = found.get(b"content-type", b"").split(b";", 1)[0].strip().lower()
    return UsageScan(path, streamed=kind == b"text/event-stream")

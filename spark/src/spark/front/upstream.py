"""The front's way to llama-swap, and its count of what is in flight (Phase 2a; plan.md, *The front and the gate*).

- **Upstream** forwards a request to llama-swap with the front's own internal key, `Authorization: Bearer <key>`, the
  only credential it sends, over an httpx.AsyncClient of the asking key's own, so no upstream connection ever carries
  two keys' requests. Each client takes no proxy from the environment, which would get the internal key even for
  127.0.0.1, follows no redirect, since whatever answers on the port could send the key anywhere, and has no read
  timeout, so a long prefill that sends nothing for minutes is never cut off. It asks for the body as it is
  (`Accept-Encoding: identity`), so the front reads the events of a stream it passes on. Its errors hold nothing of
  the request: Unreached, no answer at all; Broken, a body that broke off. Each is raised from None, so a traceback
  never shows httpx's own, whose request holds the internal key.
- **Upstream.ready_models** reads llama-swap's `GET /running` with the same key, over a client of the front's own:
  the models the front serves while the gate is down (rule 8; Task 22's GateLink).
- **InFlight** counts the requests in flight per model, each from when the front forwards it until its response
  ends, however it ends, with each one's key and start, the oldest start and the last end: the counts the gate's
  drains and `make apply`'s quiet wait read (rule 4), and `spark status` shows, so a count left behind would show. Its
  `on_change` hook hears each change in the step that made it, so the gate link answers a drain at 0 (Task 22).
- **StreamEnd** tells how an event stream ended, as it passes: with its route's last event, with the engine's own
  error event, or cut, without either, which the front passes on as a cut (the controller's ruling, at Task 12's
  review).

What it relies on, read in llama-swap v257's source (github.com/mostlygeek/llama-swap, tag v257, commit f00d375) and
llama.cpp b11146's (github.com/ggml-org/llama.cpp, tag b11146, commit 7fe450e, tools/server):
- v257's answer to a model's eleventh request is a 429 whose JSON's `error.code` is `concurrency_limit`, with
  `Retry-After: 1` (internal/router/scheduler/fifo.go:308-312, internal/swaputil/httperror.go:112-151).
- v257's answer to a request whose model's start ended with its command exited, as `spark launch` exits when it
  refuses a start without the gate's ticket, is a 500 whose message holds `upstream command exited prematurely`
  (internal/process/process_command.go:556-559, then internal/swaputil/http.go:185-186).
- v257's `GET /running` lists each model that isn't stopped with its state, `ready` among them
  (internal/server/api.go:348-367, internal/router/base.go:366-376).
- v257 ends a stream whose engine was killed cleanly, only early: it recovers the reverse proxy's abort
  (internal/process/process_command.go:492-507). So a cut stream can end like a whole one, and only its last event
  tells them apart.
- llama-server's streams (server-context.cpp, the stream loop in handle_completions_impl, and server-task.cpp's last
  events): `/v1/chat/completions` and `/v1/completions` end with `data: [DONE]`; `/v1/messages` with an
  `event: message_stop`; `/v1/responses` with an `event: response.completed`. An error mid-stream ends it with the
  route's error event instead, `data: {"error": …}`, or `event: error` on `/v1/messages`, and nothing after it. A
  quiet stream gets a comment ping, `:` and a blank line, which is no event."""

from __future__ import annotations

import json
import re
import time
import urllib.parse
from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import dataclass
from typing import Literal

import httpx

from spark.gateproto import InflightSnapshot, ModelInflight

# The llama-swap and the llama.cpp whose answers this module reads. test_tested_against.py fails once
# stack/versions.yaml moves on without them: check the lines cited above against the new release, then move them.
TESTED_AGAINST = {"llama-swap": "v257", "llama.cpp": "b11146"}
# No read timeout: a prefill may send nothing for minutes. A connection is made, or a pooled one free, within 5 s,
# and each write of a body to llama-swap within 60 s.
TIMEOUT = httpx.Timeout(connect=5, read=None, write=60, pool=5)
# RFC 9110's hop-by-hop headers, which never come back to the client, with the ones the connection header names.
_HOP_BY_HOP = frozenset({b"connection", b"keep-alive", b"proxy-connection", b"te", b"trailer", b"transfer-encoding",
                         b"upgrade", b"proxy-authenticate"})
# What never goes upstream from the client's headers, though parse.clean_headers has already dropped them: credentials,
# and the encoding the front asks for itself.
_NOT_SENT = frozenset({b"authorization", b"x-api-key", b"cookie", b"proxy-authorization", b"accept-encoding"})
RUNNING_TIMEOUT = httpx.Timeout(5)  # `GET /running`, which answers at once (api.go:348-367)
EXITED_PREMATURELY = b"upstream command exited prematurely"
# httpx's errors for a connection that failed: no answer before the head, or a body cut off after it. A local
# protocol error, an unsupported scheme or a proxy's error is the front's own, and isn't one of them.
_FAILED = (httpx.NetworkError, httpx.TimeoutException, httpx.RemoteProtocolError)


class Unreached(Exception):
    """llama-swap gave no answer: no connection, or one that failed before the response's head (refused, reset, or
    closed by a llama-swap that crashed or restarted). Its text holds nothing of the request."""


class Broken(Exception):
    """The response's body broke off: the connection failed or closed before the body ended."""


def _url_ok(url: str) -> bool:
    try:
        parts = urllib.parse.urlsplit(url)
        parts.port  # a ValueError unless the port is a number from 0 to 65535
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.hostname)


def response_headers(raw: Iterable[tuple[bytes, bytes]]) -> list[tuple[bytes, bytes]]:
    """The response's headers as the client gets them, names in lower case: all but the hop-by-hop ones and those the
    connection header names. Content-Length stays: the body goes on as its bytes came, never decoded."""
    pairs = [(name.lower(), value) for name, value in raw]
    named = {token.strip().lower() for name, value in pairs if name == b"connection" for token in value.split(b",")}
    return [(name, value) for name, value in pairs if name not in _HOP_BY_HOP and name not in named]


class UpstreamResponse:
    """llama-swap's answer, its head read, its body not yet: `status`, `headers` (response_headers'), the body as its
    bytes come (`aiter_raw`, raising Broken if it breaks off), and `aclose()`, which closes its connection unless the
    body was read to its end."""

    def __init__(self, response: httpx.Response):
        self._response = response
        self.status = response.status_code
        self.headers = response_headers(response.headers.raw)

    async def aiter_raw(self) -> AsyncIterator[bytes]:
        try:
            async for chunk in self._response.aiter_raw():
                yield chunk
        except _FAILED:
            raise Broken("llama-swap's answer broke off before its end") from None

    async def aclose(self) -> None:
        await self._response.aclose()


class Upstream:
    """llama-swap at `base_url`, reached with `internal_key`: an httpx.AsyncClient per client key, made at its first
    request. Refuses, at once, an address that isn't http(s)://host:port, and a key that is empty or isn't printable
    ASCII without spaces, never quoting it. Close it with `aclose()`."""

    def __init__(self, base_url: str, internal_key: str):
        if not _url_ok(base_url):
            raise ValueError(f"llama-swap's address isn't a URL the front can use: {base_url!r} "
                             "(want http://host:port)")
        if not (internal_key and internal_key.isascii() and all(0x21 <= ord(char) <= 0x7E for char in internal_key)):
            # Never the value, nor any part of it. The likely cause: a stray CR or LF in the credential's file.
            raise ValueError("the front's key for llama-swap is empty or isn't printable ASCII without spaces")
        self.base_url = base_url.rstrip("/")
        self._authorization = (b"authorization", b"Bearer " + internal_key.encode("ascii"))
        self._clients: dict[str, httpx.AsyncClient] = {}
        self._own: httpx.AsyncClient | None = None  # the front's own reads, which carry no client's request

    def client(self, key: str) -> httpx.AsyncClient:
        """The client for the client key named `key`: its own connections, used by no other key."""
        client = self._clients.get(key)
        if client is None:
            client = self._clients[key] = httpx.AsyncClient(base_url=self.base_url, timeout=TIMEOUT, trust_env=False,
                                                            follow_redirects=False)
        return client

    async def forward(self, method: str, path: str, headers: list[tuple[bytes, bytes]],
                      body: bytes | AsyncIterator[bytes] | None, key: str) -> UpstreamResponse:
        """`method path` sent to llama-swap with `headers` (parse.clean_headers'), the internal key added, and `body`:
        bytes (a JSON body, with its length), an async iterator of bytes (a form, sent chunked) or None. Its answer
        once its head has come (UpstreamResponse); Unreached if none comes. The body's iterator is closed either way,
        so a spooled file it reads is never left open."""
        sent = [(name, value) for name, value in headers if name.lower() not in _NOT_SENT]
        sent += [self._authorization, (b"accept-encoding", b"identity")]
        client = self.client(key)
        try:
            request = client.build_request(method, path, headers=sent, content=body)
            response = await client.send(request, stream=True)
        except _FAILED:
            raise Unreached("llama-swap gave no answer") from None
        finally:
            closing = getattr(body, "aclose", None)
            if closing is not None:
                await closing()
        return UpstreamResponse(response)

    async def ready_models(self) -> set[str]:
        """The models llama-swap's `GET /running` shows ready, read with the internal key over a client of the
        front's own, no client key's. Unreached when llama-swap gives no answer, or one that isn't that list."""
        if self._own is None:
            self._own = httpx.AsyncClient(base_url=self.base_url, timeout=RUNNING_TIMEOUT, trust_env=False,
                                          follow_redirects=False)
        try:
            response = await self._own.get("/running", headers=[self._authorization])
            listed = response.json()["running"] if response.status_code == 200 else None
        except _FAILED:
            raise Unreached("llama-swap gave no answer") from None
        except (ValueError, KeyError, TypeError):
            listed = None
        if not isinstance(listed, list):
            raise Unreached("llama-swap's /running answer isn't its list of models")
        return {entry["model"] for entry in listed if isinstance(entry, dict) and entry.get("state") == "ready"
                and isinstance(entry.get("model"), str)}

    async def aclose(self) -> None:
        clients, self._clients = list(self._clients.values()), {}
        if self._own is not None:
            clients.append(self._own)
            self._own = None
        for client in clients:
            await client.aclose()


def concurrency_limited(status: int, body: bytes) -> bool:
    """Whether an answer is v257's 429 for a model's eleventh request: its JSON's `error.code` is
    `concurrency_limit`."""
    if status != 429:
        return False
    try:
        parsed = json.loads(body)
    except ValueError:
        return False
    error = parsed.get("error") if isinstance(parsed, dict) else None
    return isinstance(error, dict) and error.get("code") == "concurrency_limit"


def exited_prematurely(status: int, body: bytes) -> bool:
    """Whether an answer is v257's 500 for a start whose command exited: as `spark launch` exits when it refuses a
    start without the gate's ticket, so the front's view of what is loaded was stale, or the model crashed."""
    return status == 500 and EXITED_PREMATURELY in body


def retry_after(headers: Iterable[tuple[bytes, bytes]]) -> int | None:
    """The Retry-After header's whole seconds, or None when it has none, or one that isn't a whole number."""
    values = [value.strip() for name, value in headers if name.lower() == b"retry-after"]
    if len(values) != 1 or not values[0].isdigit() or len(values[0]) > 6:
        return None
    return int(values[0])


# In flight.

@dataclass(eq=False)
class InFlightRequest:
    """One request in flight, as InFlight.enter gives it, for its leave."""

    model: str
    key: str
    started_at: float
    left: bool = False


class InFlight:
    """The requests in flight, per model: each request's key and start, from when the front forwards it, the oldest
    start, and the last end. `enter` when a request is forwarded; `leave`, in a `finally`, on every way out; a request
    left twice counts once. One event loop's: nothing here awaits, so a change and its check are one step, and
    `on_change`, when set, is called with the model in that step (Task 22's GateLink: a drain answered at 0, and a
    snapshot posted on each change). Times are Unix seconds, by `clock`, as the gate's are."""

    def __init__(self, clock: Callable[[], float] = time.time):
        self.clock = clock
        self.started_at = clock()
        self.on_change: Callable[[str], None] | None = None
        self._open: dict[str, list[InFlightRequest]] = {}
        self._last_end: dict[str, float] = {}
        self._seq = 0

    def enter(self, model: str, key: str) -> InFlightRequest:
        request = InFlightRequest(model, key, self.clock())
        self._open.setdefault(model, []).append(request)
        if self.on_change is not None:
            self.on_change(model)
        return request

    def leave(self, request: InFlightRequest) -> None:
        if request.left:
            return
        request.left = True
        self._open[request.model].remove(request)
        self._last_end[request.model] = self.clock()
        if self.on_change is not None:
            self.on_change(request.model)

    def count(self, model: str) -> int:
        return len(self._open.get(model, ()))

    def snapshot(self, *, models: Iterable[str] = (), draining: Iterable[str] = ()) -> InflightSnapshot:
        """The whole count, never a delta, in `/v1/front/inflight`'s shape (gateproto.InflightSnapshot): every model
        in `models` and every model a request has been forwarded to since the front started, each with its count, its
        oldest request's start, its last end and each request's key and start; and `draining`, the models the front
        holds as draining (Task 22's GateLink gives them). Each snapshot takes the next `seq`."""
        self._seq += 1
        names = dict.fromkeys([*models, *self._open, *self._last_end])
        counts: dict[str, ModelInflight] = {}
        for name in names:
            open_ = self._open.get(name, [])
            counts[name] = {"count": len(open_),
                            "oldest_started_at": min((each.started_at for each in open_), default=None),
                            "last_end_at": self._last_end.get(name),
                            "requests": [{"key": each.key, "started_at": each.started_at} for each in open_]}
        return {"seq": self._seq, "front_started_at": self.started_at, "models": counts, "draining": list(draining)}


# A stream's end.

Outcome = Literal["done", "error", "cut"]
# Each streamed route's last event, as llama.cpp b11146 writes it: the field that carries it and its value.
LAST_EVENTS: dict[str, tuple[bytes, bytes]] = {
    "/v1/chat/completions": (b"data", b"[DONE]"),
    "/v1/completions": (b"data", b"[DONE]"),
    "/v1/messages": (b"event", b"message_stop"),
    "/v1/responses": (b"event", b"response.completed"),
}
_EOL = re.compile(rb"\r\n|\r|\n")  # an event stream's line ends (the HTML standard's server-sent events)
_ERROR_DATA = re.compile(rb'[ \t]*\{[ \t]*"error"[ \t]*:')  # `data: {"error": …}`, the engine's own error event


class StreamEnd:
    """How an event stream ended, read as its bytes pass, keeping only the first HEAD bytes of each line, so a long
    line is never held whole. Events are parsed as the HTML standard's server-sent events: lines end in CRLF, LF or
    CR; a line that starts with `:` is a comment (a ping), no event; an event is dispatched at its blank line, and
    only if it has a data line; one still open when the stream ends is dropped. `outcome()`: "done" when the last
    event dispatched is the route's last event (LAST_EVENTS), "error" when it is the engine's own error event (named
    `error`, or data that is a JSON object whose first key is `error`), and otherwise "cut". A name or a data line
    past HEAD is never taken for a last event, nor a name past it for `error`; an error's data, whose message can be
    long, is known by its start."""

    HEAD = 256

    def __init__(self, last: tuple[bytes, bytes]):
        self._last = last
        self._line = bytearray()  # the line so far, its first HEAD bytes
        self._long = False  # the line has passed HEAD
        self._cr = False  # the bytes so far end in a CR, whose LF may come first in the next chunk
        self._event, self._event_long = b"", False
        self._data: bytearray | None = None  # the event's data so far, its first HEAD bytes; None until a data line
        self._data_long = False
        self._dispatched: tuple[bytes, bool, bytes, bool] | None = None  # the last event: its name, its data

    @classmethod
    def watching(cls, path: str, status: int, headers: Iterable[tuple[bytes, bytes]]) -> StreamEnd | None:
        """A StreamEnd for a response worth watching: a 2xx event stream (`text/event-stream`), not encoded, on a
        route with a last event. None for any other, which is judged by its connection alone."""
        if path not in LAST_EVENTS or not 200 <= status < 300:
            return None
        found = {name.lower(): value for name, value in headers}
        kind = found.get(b"content-type", b"").split(b";", 1)[0].strip().lower()
        encoding = found.get(b"content-encoding", b"identity").strip().lower()
        if kind != b"text/event-stream" or encoding != b"identity":
            return None
        return cls(LAST_EVENTS[path])

    def feed(self, chunk: bytes) -> None:
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
        room = self.HEAD - len(self._line)
        if room > 0:
            self._line += chunk[start:min(stop, start + room)]
        if stop - start > room:
            self._long = True

    def _line_ends(self) -> None:
        line, long = bytes(self._line), self._long
        self._line.clear()
        self._long = False
        if not line and not long:  # a blank line: the event, if it has data, is dispatched
            if self._data is not None:
                self._dispatched = (self._event, self._event_long, bytes(self._data), self._data_long)
            self._event, self._event_long, self._data, self._data_long = b"", False, None, False
            return
        if line.startswith(b":"):
            return  # a comment: a ping
        name, colon, value = line.partition(b":")
        if not colon and long:
            return  # a field name past HEAD: no field this reads
        if value.startswith(b" "):
            value = value[1:]
        if name == b"event":
            self._event, self._event_long = value, long
        elif name == b"data":
            if self._data is None:
                self._data, self._data_long = bytearray(value), long
            else:
                added = b"\n" + value
                room = self.HEAD - len(self._data)
                self._data += added[:max(room, 0)]
                self._data_long = self._data_long or long or len(added) > room

    def outcome(self) -> Outcome:
        if self._dispatched is None:
            return "cut"
        event, event_long, data, data_long = self._dispatched
        field, value = self._last
        if (field == b"event" and event == value and not event_long) or \
                (field == b"data" and data == value and not data_long):
            return "done"
        if (event == b"error" and not event_long) or _ERROR_DATA.match(data):
            return "error"
        return "cut"

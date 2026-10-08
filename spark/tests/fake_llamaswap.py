"""A stand-in for llama-swap v257, for the async tests (Phase 2a, Task 12): an ASGI app that a real uvicorn serves on
127.0.0.1 (`serve_fake`), answering as v257's own source does, with a controller that scripts what its engines do and
keeps what it was sent (`requests`).

Each behaviour cites the line of llama-swap's source it copies, as `path:line`, relative to the root of
github.com/mostlygeek/llama-swap at tag v257 (commit f00d375), in the configuration render writes for 2a: one group
that swaps out nothing, apiKeys, no ttl, no concurrencyLimit and no sendLoadingState. What it leaves out: aliases,
peers, profiles, ignorePaths, the model routes but five (and their multipart ones), /v1/models, /api/events, the
other /logs routes, and Go's 405 for a method a route doesn't take (a 404 here). A test that needs one adds it,
citing its line. Its engines are stand-ins too: `/health` answers 200 once ready, and an inference route streams what
`stream` scripted."""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import json
import socket
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import parse_qs

import uvicorn

from spark import render
from spark.serve import QuietServer

# The llama-swap whose behaviour this copies. test_tested_against.py fails once stack/versions.yaml moves on without
# it: check each cited line against the new release, then move it.
TESTED_AGAINST = {"llama-swap": "v257"}

LIMIT = 10  # internal/router/scheduler/fifo.go:17: per model, waiting or served, when concurrencyLimit isn't set
GROUP = "group"  # the router's name, in its errors: internal/router/group.go:32
STATES = ("starting", "ready", "stopping", "stopped")  # internal/process/process.go:14-17
# The model routes with a model in a JSON body that the stack serves (internal/server/server.go:132-138).
JSON_ROUTES = ("/v1/chat/completions", "/v1/responses", "/v1/completions", "/v1/messages", "/v1/embeddings")
DEFAULT_CHUNKS = (b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n', b"data: [DONE]\n\n")
TEXT = b"text/plain; charset=utf-8"  # what Go's net/http sniffs for a plain-text body written with no Content-Type

Outcome = Literal["ready", "exited", "timeout"]
ASGIReceive = Callable[[], Awaitable[dict[str, Any]]]
ASGISend = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass(frozen=True)
class Seen:
    """A request the stand-in was sent: its method, path and query, and its headers, names in lower case, a repeated
    header's values joined with ", "."""

    method: str
    path: str
    query: str
    headers: dict[str, str]


@dataclass
class _Engine:
    """A model's process (internal/process/process_command.go) and its share of the scheduler
    (internal/router/scheduler/fifo.go)."""

    state: str = "stopped"
    script: tuple[float, Outcome] = (0.0, "ready")
    stop_s: float = 0.0
    chunks: tuple[bytes, ...] = DEFAULT_CHUNKS
    delay_s: float = 0.0
    starting: asyncio.Task | None = None  # the start under way (fifo.go:43's active swap)
    waiters: set[asyncio.Future] = field(default_factory=set)  # requests waiting on it
    reserved: int = 0  # requests admitted, waiting or served (fifo.go:44)
    served: set[asyncio.Event] = field(default_factory=set)  # requests being served; set once the engine is gone
    idle: asyncio.Event = field(default_factory=asyncio.Event)  # set while no stop is under way
    history: bytearray = field(default_factory=bytearray)  # its log so far
    tails: set[asyncio.Queue] = field(default_factory=set)  # its log streams open

    def __post_init__(self) -> None:
        self.idle.set()


def _go_duration(seconds: int) -> str:
    """A whole number of seconds as Go's time.Duration writes it: 180 → 3m0s."""
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h{minutes}m{secs}s"
    return f"{minutes}m{secs}s" if minutes else f"{secs}s"


def _go_json(value: Any) -> bytes:
    """`value` as Go's encoding/json writes it: compact, UTF-8, and <, >, &, U+2028 and U+2029 escaped."""
    text = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    for char, escaped in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"), ("\u2028", "\\u2028"),
                          ("\u2029", "\\u2029")):
        text = text.replace(char, escaped)
    return text.encode()


def _envelope(status: int, message: str, code: str = "") -> bytes:
    """internal/swaputil/httperror.go:45-97: every error's JSON, its type and code from its status."""
    kinds = {400: ("invalid_request_error", "bad_request"), 401: ("authentication_error", "unauthorized"),
             403: ("authentication_error", "forbidden"), 404: ("invalid_request_error", "not_found"),
             409: ("invalid_request_error", "conflict"), 429: ("rate_limit_error", "rate_limit_exceeded"),
             502: ("server_error", "bad_gateway"), 503: ("server_error", "service_unavailable"),
             504: ("server_error", "gateway_timeout")}
    kind, default = kinds.get(status, ("invalid_request_error", "invalid_request") if 400 <= status < 500
                              else ("server_error", "internal_error"))
    return _go_json({"src": "llama-swap", "error": {"message": message, "type": kind, "param": None,
                                                    "code": code or default}})


def _key(headers: dict[str, str]) -> str:
    """internal/swaputil/http.go:585-611: Basic's password first, then a Bearer token, then x-api-key."""
    bearer = basic = ""
    scheme, sep, credentials = headers.get("authorization", "").partition(" ")
    if sep and scheme.lower() == "bearer":
        bearer = credentials
    elif sep and scheme.lower() == "basic":
        with contextlib.suppress(binascii.Error, ValueError):
            user_password = base64.b64decode(credentials, validate=True).decode("utf-8", "surrogateescape")
            if ":" in user_password:
                basic = user_password.split(":", 1)[1]
    return basic or bearer or headers.get("x-api-key", "")


async def _answer(send: ASGISend, status: int, body: bytes, content_type: bytes | None = None,
                  headers: list[tuple[bytes, bytes]] | None = None) -> None:
    head = [(b"content-type", content_type)] if content_type else []
    await send({"type": "http.response.start", "status": status, "headers": head + (headers or [])})
    await send({"type": "http.response.body", "body": body})


async def _redirect(send: ASGISend, method: str, status: int, location: str) -> None:
    """Go's http.Redirect, as v257 calls it: a short HTML body for a GET, none otherwise."""
    if method != "GET":
        return await _answer(send, status, b"", None, [(b"location", location.encode())])
    reason = {301: "Moved Permanently", 302: "Found", 308: "Permanent Redirect"}[status]
    await _answer(send, status, f'<a href="{location}">{reason}</a>.\n\n'.encode(), b"text/html; charset=utf-8",
                  [(b"location", location.encode())])


class FakeLlamaSwap:
    """llama-swap v257 with `keys` as its apiKeys and `models` as its models, all stopped. The controller:
    `script_start`, `script_stop`, `set_state`, `stream`, `log` and `canned`; `requests`, what it was sent; and
    `reserved` and `tails`, a model's admitted requests and open log streams."""

    def __init__(self, keys: set[str], models: list[str]):
        self.keys = set(keys)
        self.models = list(models)
        self.requests: list[Seen] = []
        self._engines = {model: _Engine() for model in models}
        self._canned: dict[tuple[str, str], tuple[int, bytes, list[tuple[bytes, bytes]]]] = {}
        self._closing = asyncio.Event()

    # The controller.

    def script_start(self, model: str, delay_s: float = 0.0, outcome: Outcome = "ready") -> None:
        """Every start of `model` from now on takes `delay_s`, then ends ready, with its command exited, or past its
        deadline (for "timeout", `delay_s` stands for the wait and the kill together)."""
        self._engines[model].script = (delay_s, outcome)

    def script_stop(self, model: str, delay_s: float = 0.0) -> None:
        """Every stop of `model` from now on takes `delay_s`: its engine's exit, or its kill."""
        self._engines[model].stop_s = delay_s

    def set_state(self, model: str, state: str) -> None:
        """`model`'s process state, as /running shows it, set as it is: no start or stop runs."""
        assert state in STATES, state
        self._engines[model].state = state

    def stream(self, model: str, chunks: list[bytes], delay_s: float = 0.0) -> None:
        """What `model`'s engine sends each inference request from now on: `chunks`, the first at once and each of the
        rest `delay_s` after the one before."""
        self._engines[model].chunks = tuple(chunks)
        self._engines[model].delay_s = delay_s

    def log(self, model: str, lines: list[str]) -> None:
        """`lines` written to `model`'s log, each ending in a newline: kept in its history, and sent on every stream
        of it open (internal/server/log.go:123-145)."""
        data = "".join(f"{line}\n" for line in lines).encode()
        engine = self._engines[model]
        engine.history += data
        for tail in engine.tails:
            tail.put_nowait(data)

    def canned(self, method: str, path: str, status: int, body: bytes = b"",
               headers: dict[str, str] | None = None) -> None:
        """From now on `method path` answers exactly this, before any key is checked: a test's hook for answers v257
        never gives (a redirect elsewhere, a body of another shape), as from whatever else might answer on its
        port."""
        listed = [(name.encode("latin-1"), value.encode("latin-1")) for name, value in (headers or {}).items()]
        self._canned[(method, path)] = (status, body, listed)

    def reserved(self, model: str) -> int:
        return self._engines[model].reserved

    def tails(self, model: str) -> int:
        return len(self._engines[model].tails)

    def close(self) -> None:
        """serve_fake's end, as llama-swap's shutdown goes (llama-swap.go:481-518) but without its drain: a log stream
        ends at once (internal/server/server.go:503-508, log.go:139-140); what waits for a start gets 500 `group is
        shutting down` (internal/router/base.go:598-602), and an inference stream ends, where v257 would first give
        both up to 30 s (llama-swap.go:42, 487-492)."""
        self._closing.set()

    # The app.

    async def __call__(self, scope: dict[str, Any], receive: ASGIReceive, send: ASGISend) -> None:
        assert scope["type"] == "http", scope["type"]  # lifespan is off, and nothing here speaks WebSocket
        method, path = scope["method"], scope["path"]
        headers: dict[str, str] = {}
        for raw_name, raw_value in scope["headers"]:
            name, value = raw_name.decode("latin-1").lower(), raw_value.decode("latin-1")
            headers[name] = f"{headers[name]}, {value}" if name in headers else value
        query = scope["query_string"].decode("latin-1")
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        self.requests.append(Seen(method, path, query, headers))
        left = asyncio.Event()  # the client went
        watching = asyncio.create_task(self._watch(receive, left))
        try:
            await self._route(method, path, query, headers, bytes(body), send, left)
        finally:
            watching.cancel()

    @staticmethod
    async def _watch(receive: ASGIReceive, left: asyncio.Event) -> None:
        while (await receive())["type"] != "http.disconnect":
            pass
        left.set()

    async def _race(self, awaitable: Awaitable[Any], *events: asyncio.Event) -> tuple[bool, Any]:
        """(True, what `awaitable` gave) once it is done; (False, None) if one of `events`, or the stand-in's closing,
        comes first, and then `awaitable` is cancelled."""
        main = asyncio.ensure_future(awaitable)
        others = [asyncio.ensure_future(event.wait()) for event in (*events, self._closing)]
        try:
            await asyncio.wait([main, *others], return_when=asyncio.FIRST_COMPLETED)
        finally:
            finished = main.done() and not main.cancelled()
            for task in (main, *others):
                if not task.done():
                    task.cancel()
        return (True, main.result()) if finished else (False, None)

    async def _error(self, send: ASGISend, headers: dict[str, str], status: int, message: str,
                     extra: list[tuple[bytes, bytes]] | None = None) -> None:
        """internal/swaputil/http.go:193-221: an error in the form the client's Accept asks for, JSON by default."""
        accept = headers.get("accept", "")
        if "text/plain" in accept:
            return await _answer(send, status, f"llama-swap: {message}".encode(), b"text/plain", extra)
        if "text/html" in accept:
            escaped = message
            for char, entity in (("&", "&amp;"), ("'", "&#39;"), ("<", "&lt;"), (">", "&gt;"), ('"', "&#34;")):
                escaped = escaped.replace(char, entity)  # Go's html.EscapeString
            page = f"<html><body><h1>llama-swap</h1><p>{escaped}</p></body></html>"
            return await _answer(send, status, page.encode(), b"text/html", extra)
        await _answer(send, status, _envelope(status, message), b"application/json", extra)

    async def _route(self, method: str, path: str, query: str, headers: dict[str, str], body: bytes,
                     send: ASGISend, left: asyncio.Event) -> None:
        if (method, path) in self._canned:
            status, data, extra = self._canned[(method, path)]
            return await _answer(send, status, data, None, extra)
        if method == "GET" and path == "/health":  # internal/server/server.go:357, api.go:432-435: no key
            return await _answer(send, 200, b"OK", TEXT)
        if method == "GET" and path == "/upstream":  # server.go:379, api.go:441-443: no key
            return await _redirect(send, method, 302, "/ui/models")
        if self.keys and _key(headers) not in self.keys:  # internal/server/auth.go:15-40
            return await self._error(send, headers, 401, "unauthorized: invalid or missing API key",
                                     [(b"www-authenticate", b'Basic realm="llama-swap"')])
        if method == "GET" and path == "/running":
            return await self._running(send)
        if method == "POST" and path.startswith("/api/models/unload/"):  # server.go:390
            return await self._unload(path.removeprefix("/api/models/unload/"), send, headers)
        if method == "GET" and path.startswith("/logs/stream/"):  # server.go:355
            return await self._tail(path.removeprefix("/logs/stream/"), query, headers, send, left)
        if path.startswith("/upstream/"):  # server.go:380, any method
            return await self._upstream(method, path, query, headers, body, send, left)
        if method == "POST" and path in JSON_ROUTES:  # server.go:340-342
            return await self._inference(path, headers, body, send, left)
        await _answer(send, 404, b"404 page not found\n", TEXT, [(b"x-content-type-options", b"nosniff")])

    async def _running(self, send: ASGISend) -> None:
        """internal/server/api.go:348-367: every model that isn't stopped (internal/router/base.go:366-376), sorted by
        model, each with its config's metadata."""
        listed = [{"model": model, "state": engine.state, "cmd": f"spark launch {model} -- engine",
                   "proxy": "http://127.0.0.1:${PORT}", "ttl": 0, "name": "", "description": ""}
                  for model, engine in sorted(self._engines.items()) if engine.state != "stopped"]
        await _answer(send, 200, _go_json({"running": listed}) + b"\n", b"application/json")

    async def _unload(self, model: str, send: ASGISend, headers: dict[str, str]) -> None:
        """internal/server/apigroup.go:149-164: `OK` once the engine has stopped; synchronous, through the scheduler's
        OnUnload (internal/router/scheduler/fifo.go:222-267)."""
        if model not in self._engines:
            return await self._error(send, headers, 404, "model not found")
        engine = self._engines[model]
        # fifo.go:230-242: the requests waiting on a start under way fail, and the start is dropped.
        self._grant(engine, f"{GROUP}: model unloaded")
        if engine.starting is not None or engine.state == "starting":
            # The stop reaches the start mid-way, cancels it and kills its command; the state stays starting until
            # the kill is done (internal/process/process_command.go:384-393, 552-555).
            starting, engine.starting = engine.starting, None
            engine.idle.clear()
            if starting is not None:
                starting.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await starting
            await asyncio.sleep(engine.stop_s)
            engine.state = "stopped"
            engine.idle.set()
        elif engine.state == "ready":  # process_command.go:421-446
            engine.state = "stopping"
            engine.idle.clear()
            await asyncio.sleep(engine.stop_s)
            for gone in engine.served:  # the engine is gone, and so is every request it was serving
                gone.set()
            engine.state = "stopped"
            engine.idle.set()
        elif engine.state == "stopping":  # the router's run loop takes one unload at a time (base.go:430-440)
            await engine.idle.wait()
        # Stopped already: a stop is a no-op (process_command.go:432-434).
        await _answer(send, 200, b"OK", TEXT)

    async def _tail(self, model: str, query: str, headers: dict[str, str], send: ASGISend,
                    left: asyncio.Event) -> None:
        """internal/server/log.go:90-146: the model's log so far, then each new line, until the client goes."""
        if model not in self._engines:  # log.go:79-85, 103-106
            return await self._error(send, headers, 400, "invalid logger. Use 'proxy', 'upstream' or a model's ID")
        engine = self._engines[model]
        await send({"type": "http.response.start", "status": 200, "headers": [
            (b"content-type", b"text/plain"), (b"x-content-type-options", b"nosniff"), (b"x-accel-buffering", b"no")]})
        if "no-history" not in parse_qs(query, keep_blank_values=True) and engine.history:
            await send({"type": "http.response.body", "body": bytes(engine.history), "more_body": True})
        tail: asyncio.Queue = asyncio.Queue()
        engine.tails.add(tail)
        try:
            while True:
                got, data = await self._race(tail.get(), left)
                if not got:
                    break
                await send({"type": "http.response.body", "body": data, "more_body": True})
            await send({"type": "http.response.body", "body": b""})
        finally:
            engine.tails.discard(tail)

    async def _upstream(self, method: str, path: str, query: str, headers: dict[str, str], body: bytes,
                        send: ASGISend, left: asyncio.Event) -> None:
        """internal/server/api.go:447-509: a request passed to the model's engine, through the scheduler, so it starts
        the model or joins its start; `GET /upstream/<model>/health` is how the gate loads one."""
        model, slash, rest = path.removeprefix("/upstream/").partition("/")
        if model not in self._engines:  # api.go:450-453
            return await self._error(send, headers, 404, "model not found")
        if not slash:  # api.go:456-469: to the same path with a slash, keeping the method past a GET or HEAD
            location = f"/upstream/{model}/" + (f"?{query}" if query else "")
            return await _redirect(send, method, 301 if method in ("GET", "HEAD") else 308, location)
        await self._scheduled(model, headers, send, left, lambda engine: self._engine_answers(
            engine, method, f"/{rest}", send, left))

    async def _inference(self, path: str, headers: dict[str, str], body: bytes, send: ASGISend,
                         left: asyncio.Event) -> None:
        """A model route (internal/server/server.go:275-295): the model from the body, then the scheduler."""
        try:
            model = json.loads(body).get("model")
        except (ValueError, AttributeError):
            model = None
        if not isinstance(model, str) or not model:  # internal/server/auth.go:45-58, swaputil/http.go:177-178
            return await self._error(send, headers, 404, "no model id could be identified")
        if model not in self._engines:  # server.go:292-293, swaputil/http.go:183-184
            return await self._error(send, headers, 404, "no router for requested model")
        await self._scheduled(model, headers, send, left, lambda engine: self._engine_answers(
            engine, "POST", path, send, left))

    async def _scheduled(self, model: str, headers: dict[str, str], send: ASGISend, left: asyncio.Event,
                         serve: Callable[[_Engine], Awaitable[None]]) -> None:
        """A request through the scheduler (internal/router/base.go:487-610, scheduler/fifo.go:94-141)."""
        engine = self._engines[model]
        if engine.reserved >= LIMIT:  # fifo.go:308-312, the 429 written as it is (swaputil/http.go:166-173)
            body = _envelope(429, "Too many requests", "concurrency_limit")  # swaputil/httperror.go:112-151
            return await _answer(send, 429, body, b"application/json", [(b"retry-after", b"1")])
        engine.reserved += 1  # fifo.go:316, held until it has been served, or is cancelled or failed (fifo.go:148-217,
        # 298-303)
        try:
            if engine.state != "ready" or engine.starting is not None:
                if engine.starting is None:  # fifo.go:138-140: a new start (base.go:243-276)
                    engine.starting = asyncio.create_task(self._start(engine))
                waiter = asyncio.get_running_loop().create_future()  # fifo.go:107-112: join the start's waiters
                engine.waiters.add(waiter)
                try:
                    got, failed = await self._race(waiter, left)
                finally:
                    engine.waiters.discard(waiter)
                if not got:
                    if self._closing.is_set() and not left.is_set():
                        await self._error(send, headers, 500, f"unspecific error: {GROUP} is shutting down")
                    return  # a client that went gets nothing, and the start goes on (base.go:586-597)
                if failed is not None:  # fifo.go:196-202, then swaputil/http.go:185-186
                    return await self._error(send, headers, 500, f"unspecific error: {failed}")
            await serve(engine)
        finally:
            engine.reserved -= 1

    async def _start(self, engine: _Engine) -> None:
        """internal/process/process_command.go:450-616: the command, then health checks until it is ready, its
        command exits, or the deadline passes; then every waiter learns how it ended (fifo.go:189-205)."""
        await engine.idle.wait()  # a stop under way finishes first: the process takes one request at a time
        engine.state = "starting"
        delay_s, outcome = engine.script
        await asyncio.sleep(delay_s)
        if outcome == "ready":
            engine.state = "ready"  # process_command.go:338-339
            failed = None
        else:
            engine.state = "stopped"  # process_command.go:373-375
            # process_command.go:556-559, or 587-589 with render's healthCheckTimeout
            failed = ("upstream command exited prematurely" if outcome == "exited"
                      else f"health check timed out after {_go_duration(render.HEALTH_CHECK_TIMEOUT_S)}")
        engine.starting = None
        self._grant(engine, failed)

    @staticmethod
    def _grant(engine: _Engine, failed: str | None) -> None:
        for waiter in engine.waiters:
            if not waiter.done():
                waiter.set_result(failed)

    async def _engine_answers(self, engine: _Engine, method: str, path: str, send: ASGISend,
                              left: asyncio.Event) -> None:
        """The engine's stand-in, behind llama-swap's reverse proxy: /health, and the inference routes' stream."""
        if method == "GET" and path == "/health":
            return await _answer(send, 200, b'{"status":"ok"}', b"application/json")
        if method != "POST" or path not in JSON_ROUTES:
            return await _answer(send, 404, b"", None)
        gone = asyncio.Event()
        engine.served.add(gone)
        try:
            # process_command.go:487-489: an event stream gets X-Accel-Buffering: no.
            await send({"type": "http.response.start", "status": 200, "headers": [
                (b"content-type", b"text/event-stream"), (b"x-accel-buffering", b"no")]})
            for i, chunk in enumerate(engine.chunks):
                if i:
                    got, _ = await self._race(asyncio.sleep(engine.delay_s), gone, left)
                    if not got:
                        break
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
            # Ended cleanly even when the engine died mid-stream: v257 recovers the reverse proxy's abort
            # (process_command.go:492-507), so the response is finished, only early.
            await send({"type": "http.response.body", "body": b""})
        finally:
            engine.served.discard(gone)


@contextlib.asynccontextmanager
async def serve_fake(fake: FakeLlamaSwap) -> AsyncIterator[str]:
    """`fake` on a real uvicorn at 127.0.0.1 and a port of its own, in the running event loop, yielding its URL. At
    the end, llama-swap's shutdown (FakeLlamaSwap.close), then uvicorn's."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    config = uvicorn.Config(fake, lifespan="off", log_config=None, access_log=False, ws="none", server_header=False,
                            date_header=False, timeout_graceful_shutdown=2)
    server = QuietServer(config)  # leaves SIGINT and SIGTERM to pytest
    serving = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        while not server.started:
            if serving.done():
                serving.result()  # it failed to start: raise why
                raise RuntimeError("uvicorn stopped before it started")
            await asyncio.sleep(0.01)
        yield f"http://127.0.0.1:{port}"
    finally:
        fake.close()
        server.should_exit = True
        await serving
        listener.close()

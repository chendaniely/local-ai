"""A stand-in for the gate's status socket, as the front calls it (Phase 2a, Task 22): Task 10's routes for the front,
over a short Unix socket, scripted by the test and keeping what it was sent. Its HTTP is a few lines of its own on
asyncio's streams, not uvicorn, so a test can break a connection mid-call, as a gate that dies does.

- `GET /v1/front/events`: `hello`, then `state`, as the test set them when the call came, then each event the test
  `send`s, and a `{"op": "ping"}` whenever nothing else has gone for `ping_every_s`, as the gate's own stream does
  (gate.app's _send_events); `drop()` ends every stream with its last chunk, and the front calls again. `send_now`
  writes an event on every stream in the caller's own step, so a test can land it in a chosen turn of the loop.
- `POST /v1/admit`: kept in `admits`; held while `hold` is set and not yet released; then answered with `answer` (a
  dict, or a function of the request's body), `{"ok": true}` by default. One whose call closes while it is held is
  kept in `closed_admits`, by its request id. While `admit_breaks` is true, each admit's connection is broken once it
  is kept, as a gate whose new connections fail. `answer_now()` answers each held admit in the caller's own step,
  with `connection: close`, and the stand-in reads nothing more from that connection.
- `POST /v1/front/inflight`, `/v1/front/drained`, `/v1/front/busy`, `/v1/front/refused`: kept in `snapshots`,
  `drained`, `busy` and `refused`, each answered 200; but the first `fail_posts[path]` posts to a path are answered
  500 and kept in `failed_posts` instead.
- Every body the front posts is checked as the gate checks it (gate.app's _snapshot, _text and _number, and the
  refused route's checks). One that fails is answered 400, as the gate answers it, and kept in `bad`, so a shape the
  front drifts from shows.

Its listener is held from its making to `close()`, as systemd holds the gate's socket: while it isn't serving
(`stop()`), a connection waits in the listener's backlog, never accepted, and `start()` serves those first. `stop()`
is the gate's stop, as `spark gate`'s is (Task 19): each events stream ends with its last chunk, and each held admit is
answered 503 GATE_STOPPING, its connection closed; or, with `how="crash"`, every connection broken. `refuse()` closes
the listener, so a connection is refused."""

from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import socket
import time
from pathlib import Path
from typing import Any

from spark.gate import app as gate_app
from spark.gate.app import GATE_STOPPING
from spark.gateproto import FRONT_REFUSAL_CODES, MAX_REQUEST_BYTES

_END = object()  # a stream's end, with its last chunk
_REASONS = {200: "OK", 400: "Bad Request", 404: "Not Found", 500: "Internal Server Error", 503: "Service Unavailable"}


def _refused_body(body: dict) -> None:
    """`/v1/front/refused`'s checks, as the gate's route makes them (gate.app's _Routes.refused), but the key list,
    which the stand-in doesn't have."""
    gate_app._number(body, "at")
    if body.get("code") not in FRONT_REFUSAL_CODES:
        raise gate_app._Bad("code isn't one of the front's refusals")
    if body.get("model") is not None and not isinstance(body.get("model"), str):
        raise gate_app._Bad("model isn't text or null")
    if not isinstance(body.get("key"), str):
        raise gate_app._Bad("key isn't text")
    gate_app._text(body, "message", most=MAX_REQUEST_BYTES)


def _drain_report(body: dict) -> None:
    gate_app._text(body, "model")
    gate_app._text(body, "drain_id")


def _admit_body(body: dict) -> None:
    gate_app._text(body, "model")
    gate_app._text(body, "request_id")
    gate_app._number(body, "deadline")
    if not isinstance(body.get("key"), str):
        raise gate_app._Bad("key isn't text")


# Each route's body, checked as the gate checks it.
_CHECKS = {"/v1/admit": _admit_body, "/v1/front/inflight": gate_app._snapshot, "/v1/front/drained": _drain_report,
           "/v1/front/busy": _drain_report, "/v1/front/refused": _refused_body}


class GateStandIn:
    def __init__(self, folder: Path, *, models: list[dict], ready=(), applying: bool = False, lapse_s: float = 60.0,
                 started_at: float = 1000.0, ping_every_s: float = 0.2):
        self.path = folder / "gate-status.sock"
        self.hello: dict[str, Any] = {"op": "hello", "gate_started_at": started_at, "applying": applying,
                                      "lapse_s": lapse_s, "models": copy.deepcopy(models)}
        self.state: dict[str, Any] = {"op": "state", "ready": sorted(ready), "starting": [], "draining": [],
                                      "models": copy.deepcopy(models)}
        self.admits: list[dict] = []
        self.closed_admits: list[str] = []
        self.answer: Any = None
        self.hold: asyncio.Event | None = None
        self.snapshots: list[dict] = []
        self.drained: list[dict] = []
        self.busy: list[dict] = []
        self.refused: list[dict] = []
        self.admit_breaks = False
        self.fail_posts: dict[str, int] = {}
        self.failed_posts: list[tuple[str, dict]] = []
        self.bad: list[tuple[str, str]] = []
        self.ping_every_s = ping_every_s
        self.pings = 0
        self.events_calls = 0
        self._streams: set[asyncio.Queue] = set()
        self._event_writers: set[asyncio.StreamWriter] = set()  # each events stream's, until its last chunk
        self._held: dict[asyncio.StreamWriter, dict] = {}  # each held admit's connection, and its body
        self._writers: set[asyncio.StreamWriter] = set()
        self._stopping = asyncio.Event()
        self._server: asyncio.Server | None = None
        self.listener: socket.socket | None = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(str(self.path))
        self.listener.listen(64)

    # The controller.

    async def start(self) -> None:
        """Serving: the connections waiting in the listener's backlog first."""
        assert self._server is None and self.listener is not None
        self._stopping = asyncio.Event()
        self._server = await asyncio.start_unix_server(self._serve, sock=self.listener.dup())

    async def stop(self, how: str = "stopping") -> None:
        """No longer serving, the listener still held. `stopping`: each events stream ends with its last chunk, each
        held admit is answered 503 GATE_STOPPING and its connection closed, and every other connection closes once its
        exchange is done. `crash`: every connection broken at once."""
        server, self._server = self._server, None
        assert server is not None
        server.close()  # no more accepts; the listener's dup closes, the listener stays
        if how == "crash":
            for writer in list(self._writers):
                writer.transport.abort()
        else:
            assert how == "stopping", how
            self._stopping.set()
        await asyncio.wait_for(server.wait_closed(), 10)

    def refuse(self) -> None:
        """Nothing listens from now on: a connection is refused."""
        if self.listener is not None:
            self.listener.close()
            self.listener = None

    async def close(self) -> None:
        if self._server is not None:
            await self.stop("crash")
        self.refuse()

    def send(self, event: dict) -> None:
        """`event` on every events stream open now."""
        for queue in self._streams:
            queue.put_nowait(copy.deepcopy(event))

    def drop(self) -> None:
        """Every events stream ends, with its last chunk, while the stand-in goes on serving."""
        for queue in self._streams:
            queue.put_nowait(_END)

    def send_now(self, event: dict) -> None:
        """`event` written on every events stream open now, in this step: its bytes are on the front's socket before
        the step ends, where `send` queues it for the stream's own task."""
        data = json.dumps(event).encode() + b"\n"
        for writer in self._event_writers:
            writer.write(b"%x\r\n%s\r\n" % (len(data), data))

    def answer_now(self) -> None:
        """Each held admit answered in this step, as `answer` gives it, its bytes on the front's socket before the step
        ends. Answered with `connection: close`: the stand-in reads nothing more from that connection."""
        for writer, body in list(self._held.items()):
            del self._held[writer]
            writer.write(_response(200, self._answer(body), close=True))

    # The socket.

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._writers.add(writer)
        stopping = self._stopping
        try:
            while not stopping.is_set():
                head = await _first(reader.readuntil(b"\r\n\r\n"), stopping)
                if head is None:
                    break
                lines = head.decode("latin-1").split("\r\n")
                method, path, _ = lines[0].split(" ", 2)
                length = next((int(line.split(":", 1)[1]) for line in lines[1:]
                               if line.lower().startswith("content-length:")), 0)
                body = json.loads(await reader.readexactly(length)) if length else None
                if not await self._route(method, path, body, reader, writer, stopping):
                    break
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            self._writers.discard(writer)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _route(self, method: str, path: str, body: Any, reader: asyncio.StreamReader,
                     writer: asyncio.StreamWriter, stopping: asyncio.Event) -> bool:
        """Answers one request; whether the connection carries another."""
        if (method, path) == ("GET", "/v1/front/events"):
            return await self._events(writer, stopping)
        if method == "POST" and path in _CHECKS:
            try:
                if not isinstance(body, dict):
                    raise gate_app._Bad("the body isn't a JSON object")
                _CHECKS[path](body)
            except gate_app._Bad as err:
                self.bad.append((path, str(err)))
                await _respond(writer, 400, {"message": str(err), "code": None})
                return True
        if (method, path) == ("POST", "/v1/admit"):
            return await self._admit(body, reader, writer, stopping)
        kept = {"/v1/front/inflight": self.snapshots, "/v1/front/drained": self.drained,
                "/v1/front/busy": self.busy, "/v1/front/refused": self.refused}.get(path)
        if method == "POST" and kept is not None:
            if self.fail_posts.get(path, 0) > 0:
                self.fail_posts[path] -= 1
                self.failed_posts.append((path, body))
                await _respond(writer, 500, {"message": "The gate couldn't answer: it hit an error of its own.",
                                             "code": None})
                return True
            kept.append(body)
            await _respond(writer, 200, {})
            return True
        await _respond(writer, 404, {"message": "The gate has no such route.", "code": None})
        return True

    async def _events(self, writer: asyncio.StreamWriter, stopping: asyncio.Event) -> bool:
        self.events_calls += 1
        queue: asyncio.Queue = asyncio.Queue()
        for event in (self.hello, self.state):
            queue.put_nowait(copy.deepcopy(event))
        self._streams.add(queue)
        try:
            writer.write(b"HTTP/1.1 200 OK\r\ncontent-type: application/x-ndjson\r\ntransfer-encoding: chunked\r\n\r\n")
            self._event_writers.add(writer)
            while True:
                event = await _first(self._next_event(queue), stopping)
                if event is None or event is _END:
                    break
                data = json.dumps(event).encode() + b"\n"
                writer.write(b"%x\r\n%s\r\n" % (len(data), data))
                await writer.drain()
            self._event_writers.discard(writer)  # before its last chunk: nothing written after it
            writer.write(b"0\r\n\r\n")
            await writer.drain()
            return False  # its connection closes once the stream has ended, as the gate's does
        finally:
            self._event_writers.discard(writer)
            self._streams.discard(queue)

    async def _next_event(self, queue: asyncio.Queue) -> Any:
        """The next event the test sent, or a ping once none has come for `ping_every_s`."""
        try:
            return await asyncio.wait_for(queue.get(), self.ping_every_s)
        except TimeoutError:
            self.pings += 1
            return {"op": "ping", "at": time.time()}

    async def _admit(self, body: dict, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                     stopping: asyncio.Event) -> bool:
        self.admits.append(body)
        if self.admit_breaks:
            writer.transport.abort()
            return False
        self._held[writer] = body
        closed = asyncio.ensure_future(reader.read(1))  # b"": the front closed its call
        waits = [closed, asyncio.ensure_future(stopping.wait()),
                 asyncio.ensure_future(self.hold.wait() if self.hold is not None else asyncio.sleep(0))]
        done, pending = await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.wait(waits)
        if self._held.pop(writer, None) is None:  # answer_now answered it, with connection: close
            if not closed.cancelled():
                closed.exception()  # a reset, taken as the close it is
            return False
        if closed in done:
            closed.exception()  # a reset, taken as the close it is
            self.closed_admits.append(body["request_id"])
            return False
        if stopping.is_set():
            await _respond(writer, 503, {"message": GATE_STOPPING, "code": None}, close=True)
            return False
        await _respond(writer, 200, self._answer(body))
        return True

    def _answer(self, body: dict) -> dict:
        answer = self.answer(body) if callable(self.answer) else self.answer
        return answer if answer is not None else {"ok": True, "model": body["model"], "load_s": None}


async def _first(work, stopping: asyncio.Event):
    """`work`'s result, or None once `stopping` is set first."""
    task = asyncio.ensure_future(work)
    stop = asyncio.ensure_future(stopping.wait())
    done, _ = await asyncio.wait((task, stop), return_when=asyncio.FIRST_COMPLETED)
    stop.cancel()
    if task in done:
        return task.result()
    task.cancel()
    await asyncio.wait((task,))
    return None


def _response(status: int, payload: dict, *, close: bool = False) -> bytes:
    data = json.dumps(payload).encode()
    head = (f"HTTP/1.1 {status} {_REASONS.get(status, 'Error')}\r\ncontent-type: application/json\r\n"
            f"content-length: {len(data)}\r\n" + ("connection: close\r\n" if close else "") + "\r\n")
    return head.encode() + data


async def _respond(writer: asyncio.StreamWriter, status: int, payload: dict, *, close: bool = False) -> None:
    writer.write(_response(status, payload, close=close))
    await writer.drain()

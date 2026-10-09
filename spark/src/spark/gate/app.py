"""The gate's two sockets (Phase 2a; plan.md, *The front and the gate* and *Users, access and security*): the status
socket, for group spark-users, and the control socket, for spark-admin (gateproto.SOCKET_GROUPS), each a Starlette
app serving its routes of Task 10's table (gateproto.ROUTES) on Task 18's core. Each caller is known by its uid, which
Task 9's protocol reads from the socket (SO_PEERCRED) into the request's scope.

**Who may call** (gateproto.CALLERS), decided on the uid alone, before the first receive(), so a refused caller is
answered at once and its unread body falls under Task 9's deadline (a body the app is still reading has no limit, so
reading before authorizing would give that hold back):
- `front`: spark-front's uid only, root not among them;
- `users`: anyone the socket lets in, its group already limiting who connects;
- `owner`: anyone the socket lets in, a session's own uid renewing and ending it and an admin uid ending any
  (Task 16's Sessions decides, by the session's id in the path); `spark` and `spark-front` hold none, refused before
  the body;
- `admin`: a uid `admin_uids()` gives (spark-admin's members), or root.
A request with no `peer_cred`, which comes only from a connection that isn't over the gate's sockets, is refused. Each
refusal is a 403 whose sentence says who may.

**The body.** At most MAX_REQUEST_BYTES, by its Content-Length before any of it is read and by its count as it is
read: past it, a 413 in words, never parsed. A non-empty body is a JSON object, else 400. An answer sent before the
body was read whole closes the connection, so what is left of it is never read as a request.

**Time.** Every request is bounded by REQUEST_TIMEOUT_S, its body's read included: a body that hasn't come by then gets
a 408 and the connection closes; an answer the gate hasn't made by then is a 503, and its work goes on to its end, so
a change the gate began is never cut halfway (apply's end, its /running read slow, say). The routes that wait are
bounded otherwise, their bodies still read within REQUEST_TIMEOUT_S: `/v1/admit` by its key's wait (the deadline the
front gives; a load that started is waited for to its own bound, as every load is); `/v1/front/events`, kept open;
`/v1/load` and `/v1/pin` by LOAD_CALL_TIMEOUT_S, the deadline Room.load and Pins.pin give their admission; and
`/v1/unload` and `/v1/make-room` by their drains, which wait for their requests in flight however long.

**Streams.** `/v1/load`, `/v1/unload`, `/v1/make-room` and a `/v1/pin` that loads send their head at once, before any
line, so a gate queued behind another load never reads to the CLI as one that is down; then NDJSON, each progress line
with its `kind`, then the result, or a refusal line (GateRefusalBody), then the last chunk. A caller that goes ends its
stream, never the work: a load that started goes on, and so do a drain and its unload. A pin on a model already loaded
answers once, as does a pin refused before it would load.

**The gate's stop.** `spark gate` sets `stopping` (each app's `state.stopping`, one event for both) as its stop begins
(serve.run_servers' `stopping`), and the answers that wait end then, rather than hold the stop for GRACEFUL_S: the
events stream ends; a held admit leaves the queue and is answered 503, `GATE_STOPPING`, and the front asks the
restarted gate again with its original deadline (Task 22); a stream ends without its result, which the CLI reads as a
gate that stopped partway (Task 30), and each drain, saved, resumes at the restarted gate (Task 18).

**Errors never quote a request.** Every answer that isn't 2xx is a GateRefusalBody, `message` and `code`, None for the
gate's own (`/v1/admit` alone answers a refusal 200, with `ok: false`). A route that breaks is logged by its route and
where it broke, never its exception's text, which code can build from a request, and answered 500 in words, or, partway
through a stream, with a refusal line."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import math
import pwd
import secrets
import time
import traceback
from collections.abc import AsyncIterator, Awaitable, Callable
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route, compile_path

from spark import gateproto, messages, protocols
from spark.budget import ALL
from spark.gate.admission import AdmitRequest, Admitted
from spark.gate.policy import HOLDS_NO_SESSION, Refusal
from spark.gate.state import STATE_FILE, RecentRecord, RefusalRecord
from spark.gateproto import FRONT_REFUSAL_CODES, MAX_REQUEST_BYTES, PING_EVERY_S, SESSION_TTL_S
from spark.llamaswap import LlamaSwapError

_log = logging.getLogger(__name__)

REQUEST_TIMEOUT_S = gateproto.REQUEST_TIMEOUT_S  # read at each request, so a test may shorten it
NDJSON = b"application/x-ndjson"
JSON = b"application/json"
LOGS_MAX = 1000  # `/v1/logs`' n: the last lines it may ask for
TEXT_MAX = 2000  # a front refusal's words, as kept, and a session's label: as many as gateclient shows (MESSAGE_MAX)
NAME_MAX = 256  # a model's name, a request's id, a drain's id or a needle, as a request gives it
FILE_MAX = 1 << 20  # the canary reads at most this much of each of launch's files

# The gate's own words, each a GateRefusalBody's `message`.
NO_PEER = "The gate answers only over its own sockets, where it knows each caller's account."
ONLY_THE_FRONT = "Only the front, which runs as spark-front, can use this route."
ONLY_ADMIN = "Only spark-admin's members and root can use the gate's control socket: on the Spark, run it as Dan."
TOO_BIG = f"The request is over {MAX_REQUEST_BYTES // 1024} KiB, the most the gate reads, so it wasn't read."
NOT_AN_OBJECT = "The request's body isn't a JSON object."
BROKE = "The gate couldn't answer: it hit an error of its own. On the Spark, `make logs s=gate` shows why."
GATE_STOPPING = "The gate is stopping, so it can't answer this now; ask it again once it's back."
NO_SUCH_ROUTE = "The gate has no such route."
WRONG_METHOD = "That route of the gate's doesn't take this method."
ON_THE_CONTROL_SOCKET = "That route is on the gate's control socket, which only spark-admin's members and root can use."
ON_THE_STATUS_SOCKET = "That route is on the gate's status socket."


def _slow_body() -> str:
    return (f"The request didn't arrive whole within {messages.duration(REQUEST_TIMEOUT_S)}, so the gate closed the "
            "connection.")


def _slow_answer() -> str:
    return (f"The gate couldn't answer within {messages.duration(REQUEST_TIMEOUT_S)}. On the Spark, `make logs s=gate` "
            "shows why.")


def _bad(field: str, what: str) -> str:
    return f"The request's {field} isn't {what}."


class _Done(Exception):
    """The exchange has ended: answered, or its client gone."""


class _Bad(Exception):
    """A request the gate can't take: 400, in words."""


def _account(uid: int) -> str | None:
    """The uid's account name, from the user database; None for a uid with none."""
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return None


def _plain(value: Any) -> Any:
    """What json can't write: a Decimal as a float, a dataclass as its fields."""
    if isinstance(value, Decimal):
        return float(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    raise TypeError(f"{type(value).__name__} isn't JSON")


def _encode(payload: Any) -> bytes:
    return json.dumps(payload, default=_plain).encode()


def _where(err: BaseException) -> str:
    """Where `err` was raised, by file, line and function: never its text, which can quote a request."""
    frames = traceback.extract_tb(err.__traceback__)
    if not frames:
        return "an unknown place"
    last = frames[-1]
    return f"{Path(last.filename).name}:{last.lineno} in {last.name}"


def _broke(route: gateproto.Route, err: BaseException) -> None:
    _log.error("the gate's %s %s broke: %s at %s", route.method, route.path, type(err).__name__, _where(err))


class _Exchange:
    """One request and its answer, as uvicorn's h11 protocol gives them: whether the body was read whole, whether the
    head is sent, and whether the client has gone."""

    def __init__(self, route: gateproto.Route, scope: dict, receive: Callable[[], Awaitable[dict]],
                 send: Callable[[dict], Awaitable[None]]):
        self.route, self.scope, self._receive, self._send = route, scope, receive, send
        self.body_read = False
        self.started = False
        self.gone = False

    async def answer(self, status: int, payload: Any, *, close: bool | None = None) -> None:
        """One answer, JSON. It closes the connection when the body wasn't read whole, or when `close` says so."""
        data = _encode(payload)
        headers = [(b"content-type", JSON), (b"content-length", str(len(data)).encode())]
        if (not self.body_read) if close is None else close:
            headers.append((b"connection", b"close"))
        await self._send({"type": "http.response.start", "status": status, "headers": headers})
        await self._send({"type": "http.response.body", "body": data})
        self.started = True

    async def refuse(self, status: int, message: str, code: str | None = None, *, close: bool | None = None) -> None:
        await self.answer(status, {"message": message, "code": code}, close=close)

    async def head(self) -> None:
        """A stream's head, sent at once: NDJSON, chunked."""
        await self._send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", NDJSON)]})
        self.started = True

    async def line(self, payload: Any) -> None:
        await self._send({"type": "http.response.body", "body": _encode(payload) + b"\n", "more_body": True})

    async def end(self) -> None:
        if not self.gone:
            await self._send({"type": "http.response.body", "body": b""})

    async def read_body(self, deadline: float) -> dict:
        """The body, read within the cap by `deadline` (the loop's clock), as a JSON object; an empty one is {}. Too
        big: 413; too slow: 408; not an object: 400; the client gone: nothing. Each ends the exchange (_Done)."""
        length = next((value for name, value in self.scope.get("headers", []) if name == b"content-length"), None)
        if length is not None and length.isdigit() and int(length) > MAX_REQUEST_BYTES:
            await self.refuse(413, TOO_BIG)
            raise _Done
        data = bytearray()
        try:
            async with asyncio.timeout_at(deadline):
                while True:
                    message = await self._receive()
                    if message["type"] == "http.disconnect":
                        self.gone = True
                        raise _Done
                    data += message.get("body", b"")
                    if len(data) > MAX_REQUEST_BYTES:
                        await self.refuse(413, TOO_BIG)
                        raise _Done
                    if not message.get("more_body"):
                        break
        except TimeoutError:
            await self.refuse(408, _slow_body())
            raise _Done from None
        self.body_read = True
        if not data.strip():
            return {}
        try:
            body = json.loads(data)
        except (ValueError, RecursionError):
            body = None
        if not isinstance(body, dict):
            await self.refuse(400, NOT_AN_OBJECT)
            raise _Done
        return body

    async def wait_gone(self) -> None:
        """Until the client goes. Called once the body is read: what else comes is the disconnect."""
        while True:
            message = await self._receive()
            if message["type"] == "http.disconnect":
                self.gone = True
                return

    async def stream(self, lines: AsyncIterator[dict], stopping: asyncio.Event) -> None:
        """`lines` as NDJSON: the head at once, each line as it comes, then the last chunk. A client that goes, or the
        gate's stop, ends the iteration (the work it waits on goes on); a line that breaks it ends the stream with a
        refusal line in words."""
        await self.head()

        async def pump() -> None:
            try:
                async for line in lines:
                    await self.line(line)
            except asyncio.CancelledError:
                raise
            except Exception as err:
                _broke(self.route, err)
                await self.line({"message": BROKE, "code": None})
            finally:
                try:
                    await lines.aclose()  # its finally runs now, not when it is collected (make-room's settling)
                except Exception as err:
                    _broke(self.route, err)

        await _until_first([pump(), self.wait_gone(), stopping.wait()])
        await self.end()


async def _until_first(coros: list[Awaitable[Any]]) -> asyncio.Task:
    """Each as a task, until the first ends; the others cancelled and awaited. The first."""
    tasks = [asyncio.ensure_future(coro) for coro in coros]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    return next(task for task in tasks if task in done)


def _text(body: dict, field: str, *, empty: bool = False, line: bool = True, most: int = NAME_MAX) -> str:
    value = body.get(field)
    if (not isinstance(value, str) or len(value) > most or (not value and not empty)
            or (line and not all(char.isprintable() for char in value))):
        raise _Bad(_bad(field, f"text on one line, of at most {most} characters"))
    return value


def _number(body: dict, field: str, *, none: bool = False) -> float | None:
    value = body.get(field)
    if value is None and none:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise _Bad(_bad(field, "a number" + (" or null" if none else "")))
    return float(value)


def _flag(body: dict, field: str) -> bool:
    value = body.get(field)
    if not isinstance(value, bool):
        raise _Bad(_bad(field, "true or false"))
    return value


def _resolve(registry: Any, name: str) -> str:
    """A model's name, or one of its roles, as `spark load`'s argument is resolved: the name first, as llama-swap
    looks a name up among the models before the roles; one it doesn't know, as it is."""
    if name in registry.models:
        return name
    return next((model for model, known in registry.models.items() if name in known.roles), name)


@dataclasses.dataclass
class _Gate:
    """What both apps share: the core, who may call, and the stop that ends the answers that wait."""

    core: Any
    front_uid: int
    admin_uids: Callable[[], set[int]]
    stopping: asyncio.Event

    def is_admin(self, uid: int) -> bool:
        return uid == 0 or uid in self.admin_uids()

    def refused(self, callers: str, uid: int) -> str | None:
        """Why `uid` may not call a route of `callers`, in words, or None."""
        if callers == "front" and uid != self.front_uid:
            return ONLY_THE_FRONT
        if callers == "admin" and not self.is_admin(uid):
            return ONLY_ADMIN
        return None


Bounded = Callable[[int, dict, dict, dict], Awaitable[tuple[int, Any]]]
Waiting = Callable[[_Exchange, int, dict, dict], Awaitable[None]]


class _Endpoint:
    """One route's ASGI app: who may call it, on the uid; the body, within the cap and REQUEST_TIMEOUT_S; then its
    handler, a bounded one (its answer within REQUEST_TIMEOUT_S, its work shielded) or one that waits."""

    def __init__(self, gate: _Gate, route: gateproto.Route, *, bounded: Bounded | None = None,
                 waiting: Waiting | None = None, before_body: Callable[[int], str | None] | None = None):
        self.gate, self.route = gate, route
        self.bounded, self.waiting, self.before_body = bounded, waiting, before_body

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        exchange = _Exchange(self.route, scope, receive, send)
        began = asyncio.get_running_loop().time()
        try:
            cred = protocols.peer(scope)
            if cred is None:
                return await exchange.refuse(403, NO_PEER)
            refused = self.gate.refused(self.route.callers, cred.uid)
            if refused is None and self.before_body is not None:
                refused = self.before_body(cred.uid)
            if refused is not None:
                return await exchange.refuse(403, refused)
            body = await exchange.read_body(began + REQUEST_TIMEOUT_S)
            params = dict(scope.get("path_params") or {})
            query = parse_qs(scope.get("query_string", b"").decode("latin-1"), keep_blank_values=True)
            if self.waiting is not None:
                await self.waiting(exchange, cred.uid, body, params)
            else:
                await self._bounded(exchange, cred.uid, body, params, query, began + REQUEST_TIMEOUT_S)
        except _Done:
            return
        except _Bad as bad:
            if not exchange.started:
                await exchange.refuse(400, str(bad))
        except Exception as err:
            _broke(self.route, err)
            if not exchange.started:
                await exchange.refuse(500, BROKE)
            elif not exchange.gone:
                await exchange.line({"message": BROKE, "code": None})
                await exchange.end()

    async def _bounded(self, exchange: _Exchange, uid: int, body: dict, params: dict, query: dict,
                       deadline: float) -> None:
        work = asyncio.ensure_future(self.bounded(uid, body, params, query))
        try:
            async with asyncio.timeout_at(deadline):
                status, payload = await asyncio.shield(work)
        except TimeoutError:
            _log.warning("the gate's %s %s took longer than %s: answered 503, and its work goes on to its end",
                         self.route.method, self.route.path, messages.duration(REQUEST_TIMEOUT_S))
            work.add_done_callback(self._late)
            return await exchange.refuse(503, _slow_answer())
        await exchange.answer(status, payload)

    def _late(self, work: asyncio.Future) -> None:
        if not work.cancelled() and work.exception() is not None and not isinstance(work.exception(), _Bad):
            _broke(self.route, work.exception())


# The routes.

def _answer_of(result: Any) -> tuple[int, Any]:
    """A policy's answer: its Refusal's status and body, or 200 and what it gave."""
    if isinstance(result, Refusal):
        return result.status, result.body()
    return 200, result


class _Routes:
    """Every route's handler, on the core."""

    def __init__(self, gate: _Gate, status_state: Any):
        self.gate, self.core = gate, gate.core
        self.status_state = status_state

    # The front's.

    async def admit(self, exchange: _Exchange, uid: int, body: dict, params: dict) -> None:
        """The front's question for a model not loaded, held until it loads or is refused (its key's wait). A call
        dropped is the cancel: the request leaves the queue at once, and nothing loads for it; a load already
        started goes on. At the gate's stop, it leaves the queue too, and the answer is 503."""
        core = self.core
        model, request_id = _text(body, "model"), _text(body, "request_id")
        if model not in core.registry.models:
            raise _Bad(_bad("model", "a model the gate's registry has"))
        key = core.keys.get(body.get("key"))
        if key is None:
            raise _Bad(_bad("key", "a key the gate's key list has"))
        deadline = _number(body, "deadline")
        request = AdmitRequest(request_id, model, "key", key, deadline)
        work = asyncio.ensure_future(core.admitter.admit(request))
        await _until_first([_settled(work), exchange.wait_gone(), self.gate.stopping.wait()])
        if work.done() and not work.cancelled():
            result = work.result()
            if isinstance(result, Admitted):
                return await exchange.answer(200, {"ok": True, "model": result.model})
            return await exchange.answer(200, {"ok": False, "code": result.code, "status": result.status,
                                               "message": result.message, "retry_after_s": result.retry_after_s})
        core.admitter.cancel(request_id)
        work.cancel()
        await asyncio.gather(work, return_exceptions=True)
        if not exchange.gone:  # the gate's stop: the front asks the restarted gate, on a new connection
            await exchange.refuse(503, GATE_STOPPING, close=True)

    async def events(self, exchange: _Exchange, uid: int, body: dict, params: dict) -> None:
        """The front's events call, kept open: `hello` and `state` at its head, every event the core sends as it
        comes, and a ping every PING_EVERY_S. It ends when the front goes, when a newer call replaces it, or at the
        gate's stop; the words a drain is owed and didn't get go to the next call (FrontChannel.close)."""
        front = self.core.front
        queue = self.core.open_front_events()
        replaced = front.dropped()
        try:
            await exchange.head()
            await _until_first([self._send_events(exchange, queue), exchange.wait_gone(), replaced.wait(),
                                self.gate.stopping.wait()])
        finally:
            front.close(queue)
        await exchange.end()

    async def _send_events(self, exchange: _Exchange, queue: asyncio.Queue) -> None:
        while True:
            try:
                async with asyncio.timeout(self.status_state.ping_every_s):
                    event = await queue.get()
            except TimeoutError:
                event = {"op": "ping", "at": time.time()}
            await exchange.line(event)

    async def inflight(self, uid: int, body: dict, params: dict, query: dict) -> tuple[int, Any]:
        self.core.inflight(_snapshot(body))
        return 200, {}

    async def drained(self, uid: int, body: dict, params: dict, query: dict) -> tuple[int, Any]:
        self.core.front.drained(_text(body, "model"), _text(body, "drain_id"))
        return 200, {}

    async def busy(self, uid: int, body: dict, params: dict, query: dict) -> tuple[int, Any]:
        self.core.front.busy(_text(body, "model"), _text(body, "drain_id"))
        return 200, {}

    async def refused(self, uid: int, body: dict, params: dict, query: dict) -> tuple[int, Any]:
        """A refusal of the front's own: in the refusal history, for *recent*, and one `refused` notification, which
        the gate words from what it knows (the controller's ruling at Task 7): the key's label and its group's words,
        wait and naming of processes; the model's label; for model_not_found, the name asked for and the registry's
        models; for draining, the drain's why and the model's requests in flight. A name the registry doesn't have is
        kept only in the front's words, never as a model. Those words hold what the client asked for, however long,
        so they are kept to TEXT_MAX characters, an ellipsis ending one that was cut."""
        core = self.core
        at = _number(body, "at")
        code = body.get("code")
        if code not in FRONT_REFUSAL_CODES:
            raise _Bad(_bad("code", f"one of the front's refusals: {', '.join(FRONT_REFUSAL_CODES)}"))
        model = body.get("model")  # as the client asked for it, any length: the body's cap bounds it
        if model is not None and not isinstance(model, str):
            raise _Bad(_bad("model", "text or null"))
        key = core.keys.get(body.get("key"))
        if key is None:
            raise _Bad(_bad("key", "a key the gate's key list has"))
        group = core.registry.key_groups.get(key.group)
        if group is None:
            raise _Bad(_bad("key", "one whose group the registry has"))
        message = _text(body, "message", most=MAX_REQUEST_BYTES)
        if len(message) > TEXT_MAX:
            message = message[:TEXT_MAX - 1] + "…"
        known = core.registry.models.get(model) if model else None
        moment = messages.Moment(
            key_label=key.label, words=group.words, names_processes=group.names_processes, wait_s=group.wait_s,
            model_label=known.label if known else "", model_command=(known.roles or (model,))[0] if known else "")
        if code == "model_not_found":
            moment = dataclasses.replace(moment, asked_name=model or "", models=[
                (each.label, name) for name, each in core.registry.models.items()])
        elif code == "draining":
            record = core.state.models.get(model) if model else None
            counts = ((core.front.snapshot or {}).get("models") or {}).get(model) or {}
            moment = dataclasses.replace(moment, drain_for=(record.drain_why if record else None) or "unknown",
                                         inflight=int(counts.get("count") or 0))
        core.state.refusals.append(RefusalRecord(at=at, model=model if known else None, key=key.name, uid=None,
                                                 code=code, message=message))
        try:
            core.save()
        except (OSError, ValueError):  # the core shows it under health; the record goes with the next save
            pass
        kind, event = messages.refusal_notification(code, request_id=f"front-{secrets.token_hex(8)}")
        try:
            core.notifier.emit(kind, event, code=code, moment=moment)
        except ValueError as err:
            _log.error("the %s notification for the front's %s couldn't be made: %s", kind, code, err)
        return 200, {}

    # Everyone's.

    async def status_for(self, uid: int, body: dict, params: dict, query: dict) -> tuple[int, Any]:
        """The status socket's view: the refusals of the caller's own keys (by their account) and its own uid's, its
        own events and the system's (empty uids), and free for a load as its group sees it."""
        core = self.core
        account = _account(uid)
        owned = [key for key in core.keys.values() if account is not None and key.account == account]

        def sees(item: RefusalRecord | RecentRecord) -> bool:
            if isinstance(item, RecentRecord):
                return not item.uids or uid in item.uids
            key = core.keys.get(item.key) if item.key else None
            return ((key is not None and account is not None and key.account == account)
                    or (item.uid is not None and item.uid == uid))

        if self.gate.is_admin(uid):
            uses_hold = None  # Dan's own view: the dan group's
        else:
            groups = [core.registry.key_groups.get(key.group) for key in owned]
            uses_hold = bool(groups) and all(group is not None and group.uses_hold for group in groups)
        return 200, core.status_view(sees=sees, uses_hold=uses_hold)

    async def session_begin(self, uid: int, body: dict, params: dict, query: dict) -> tuple[int, Any]:
        pid = body.get("pid")
        if isinstance(pid, bool) or not isinstance(pid, int):
            raise _Bad(_bad("pid", "a process id"))
        label = _text(body, "label", line=False, most=TEXT_MAX)  # Sessions puts it on one line
        result = self.core.sessions.register(uid, pid, _text(body, "model"), label)
        if isinstance(result, Refusal):
            return _answer_of(result)
        return 200, {"id": result.id, "expires_at": result.renewed_at + SESSION_TTL_S}

    async def session_renew(self, uid: int, body: dict, params: dict, query: dict) -> tuple[int, Any]:
        result = self.core.sessions.renew(params["id"], uid)
        if isinstance(result, Refusal):
            return _answer_of(result)
        return 200, {"id": result.id, "expires_at": result.renewed_at + SESSION_TTL_S}

    async def session_end(self, uid: int, body: dict, params: dict, query: dict) -> tuple[int, Any]:
        result = self.core.sessions.end(params["id"], uid, admin=self.gate.is_admin(uid))
        return _answer_of(result) if isinstance(result, Refusal) else (200, {})

    def holds_sessions(self, uid: int) -> str | None:
        """Before `/v1/sessions` reads its body: the stack's own accounts, spark and spark-front, hold none."""
        if uid == self.gate.front_uid or not self.core.sessions.may_hold(uid):
            return HOLDS_NO_SESSION
        return None

    # spark-admin's.

    async def status_full(self, uid: int, body: dict, params: dict, query: dict) -> tuple[int, Any]:
        return 200, self.core.status_view()

    def _model(self, body: dict) -> str:
        return _resolve(self.core.registry, _text(body, "model"))

    async def load(self, exchange: _Exchange, uid: int, body: dict, params: dict) -> None:
        name = self._model(body)
        await exchange.stream(self.core.room.load(name, uid), self.gate.stopping)

    async def unload(self, exchange: _Exchange, uid: int, body: dict, params: dict) -> None:
        name = self._model(body)
        await exchange.stream(self.core.room.unload(name, uid), self.gate.stopping)

    async def pin(self, exchange: _Exchange, uid: int, body: dict, params: dict) -> None:
        """A pin on a model already loaded, or one refused before it would load, answers once: PinConfirmation, or a
        409. A pin that loads first streams as `/v1/load` does."""
        core = self.core
        name = self._model(body)
        until = _number(body, "until", none=True)
        record = core.state.models.get(name)
        loaded = record is not None and record.state == "ready" and record.unload_requested_at is None
        if loaded or name not in core.registry.models:  # Pins.pin awaits nothing on these ways
            result = [line async for line in core.pins.pin(name, until, uid)][-1]
            return await exchange.answer(409 if "message" in result else 200, result)
        await exchange.stream(core.pins.pin(name, until, uid), self.gate.stopping)

    async def unpin(self, uid: int, body: dict, params: dict, query: dict) -> tuple[int, Any]:
        result = self.core.pins.unpin(_resolve(self.core.registry, params["model"]), uid)
        return _answer_of(result)

    async def room_plan(self, uid: int, body: dict, params: dict, query: dict) -> tuple[int, Any]:
        if set(body) == {"all"} and body["all"] is True:
            target: Any = ALL
        elif set(body) == {"size_gib"}:
            target = Decimal(str(_number(body, "size_gib")))
        else:
            raise _Bad("make-room's request is {size_gib: <GiB>} or {all: true}.")
        try:
            plan_id, plan = self.core.room.plan(target, uid)
        except ValueError as err:  # a size that isn't above 0: Room's own words, which quote nothing of the request
            raise _Bad(f"{err}.") from None
        return 200, {"plan_id": plan_id, "plan": dataclasses.asdict(plan)}

    async def room(self, exchange: _Exchange, uid: int, body: dict, params: dict) -> None:
        plan_id = _text(body, "plan_id")
        for_s = _number(body, "for_s", none=True)
        await exchange.stream(self.core.room.execute(plan_id, for_s, uid), self.gate.stopping)

    async def release(self, uid: int, body: dict, params: dict, query: dict) -> tuple[int, Any]:
        result = await self.core.room.release(uid, room=_flag(body, "room"), brake=_flag(body, "brake"))
        return 200, dataclasses.asdict(result)

    async def quiet(self, uid: int, body: dict, params: dict, query: dict) -> tuple[int, Any]:
        return 200, self.core.quiet()

    async def drain_all(self, uid: int, body: dict, params: dict, query: dict) -> tuple[int, Any]:
        return 200, self.core.drain_all(uid)

    async def undrain_all(self, uid: int, body: dict, params: dict, query: dict) -> tuple[int, Any]:
        return 200, await self.core.undrain_all()

    async def apply_renew(self, uid: int, body: dict, params: dict, query: dict) -> tuple[int, Any]:
        return 200, self.core.apply_renew(_number(body, "since"), uid)

    async def apply_begin(self, uid: int, body: dict, params: dict, query: dict) -> tuple[int, Any]:
        restarting = body.get("restarting")
        if not isinstance(restarting, list) or not all(isinstance(unit, str) and unit for unit in restarting):
            raise _Bad(_bad("restarting", "a list of units"))
        return 200, self.core.apply_begin(restarting)

    async def apply_end(self, uid: int, body: dict, params: dict, query: dict) -> tuple[int, Any]:
        return 200, await self.core.apply_end()

    async def logs(self, uid: int, body: dict, params: dict, query: dict) -> tuple[int, Any]:
        """The engine's last lines, read with the gate's own key; `{model}` a name or one of its roles."""
        core = self.core
        asked = params["model"]
        name = _resolve(core.registry, asked)
        given = query.get("n", ["20"])
        if len(given) != 1 or not given[0].isascii() or not given[0].isdigit() or not 1 <= int(given[0]) <= LOGS_MAX:
            raise _Bad(_bad("n", f"a whole number from 1 to {LOGS_MAX}"))
        known = core.registry.models.get(name)
        if known is None:
            return 409, {"message": messages.no_such_model(asked, "read"), "code": None}
        try:
            lines = await core.llamaswap.last_lines(name, int(given[0]))
        except LlamaSwapError:
            return 503, {"message": f"llama-swap didn't send {known.label}'s log. On the Spark, `make logs "
                                    "s=llama-swap` shows why.", "code": None}
        return 200, {"lines": lines}

    async def canary(self, uid: int, body: dict, params: dict, query: dict) -> tuple[int, Any]:
        """Whether the needle is in the gate's state, its refusal history or launch's records: where, never what
        surrounds it (doctor's `--full`)."""
        needle = _text(body, "needle")
        core = self.core
        found = []
        if any(needle in text for text in _strings(list(core.state.refusals))):
            found.append("refusals")
        rest = [getattr(core.state, field.name) for field in dataclasses.fields(core.state) if field.name != "refusals"]
        if (any(needle in text for text in _strings(rest))
                or await asyncio.to_thread(_in_files, core.state_dir, needle, {STATE_FILE})):
            found.append("state")
        if await asyncio.to_thread(_in_files, core.launch, needle, set()):
            found.append("launch")
        return 200, {"found": found}


async def _settled(work: asyncio.Future) -> None:
    """Until `work` is done, whatever it came to."""
    await asyncio.gather(asyncio.shield(work), return_exceptions=True)


def _snapshot(body: dict) -> dict:
    """The front's whole in-flight report, checked as InflightSnapshot shapes it, so the core reads only what it
    expects."""
    def stamp(value: Any, field: str) -> float | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise _Bad(_bad(field, "a time or null"))
        return value

    seq = body.get("seq")
    if isinstance(seq, bool) or not isinstance(seq, int):
        raise _Bad(_bad("seq", "a whole number"))
    _number(body, "front_started_at")
    models = body.get("models")
    if not isinstance(models, dict):
        raise _Bad(_bad("models", "an object"))
    for name, counts in models.items():
        if not isinstance(counts, dict) or len(name) > NAME_MAX:
            raise _Bad(_bad("models", "an object of each model's counts"))
        count = counts.get("count")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise _Bad(_bad("count", "a whole number, 0 or more"))
        stamp(counts.get("oldest_started_at"), "oldest_started_at")
        stamp(counts.get("last_end_at"), "last_end_at")
        requests = counts.get("requests")
        if not isinstance(requests, list) or not all(
                isinstance(each, dict) and isinstance(each.get("key"), str)
                and stamp(each.get("started_at"), "started_at") is not None for each in requests):
            raise _Bad(_bad("requests", "a list of each request's key and start"))
    draining = body.get("draining")
    if not isinstance(draining, list) or not all(isinstance(name, str) for name in draining):
        raise _Bad(_bad("draining", "a list of model names"))
    return body


def _strings(value: Any) -> list[str]:
    """Every text inside `value`: its strings, its mappings' keys and values, its records' fields."""
    found: list[str] = []
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            found.append(item)
        elif isinstance(item, bytes):
            found.append(item.decode("utf-8", "replace"))
        elif isinstance(item, dict):
            stack.extend(item.keys())
            stack.extend(item.values())
        elif isinstance(item, (list, tuple, set, frozenset)) or type(item).__name__ == "deque":
            stack.extend(item)
        elif dataclasses.is_dataclass(item) and not isinstance(item, type):
            stack.extend(getattr(item, field.name) for field in dataclasses.fields(item))
    return found


def _in_files(folder: Path, needle: str, skip: set[str]) -> bool:
    """Whether a regular file under `folder` holds the needle: as it is, or as a JSON writer escapes it. A file that
    can't be read is logged, by its path, and passed over."""
    forms = {needle.encode(), json.dumps(needle)[1:-1].encode(), json.dumps(needle, ensure_ascii=False)[1:-1].encode()}
    try:
        files = sorted(Path(folder).rglob("*"))
    except OSError as err:
        _log.warning("the canary couldn't list %s (%s)", folder, err.strerror)
        return False
    for path in files:
        if path.name in skip or path.is_symlink() or not path.is_file():
            continue
        try:
            with path.open("rb") as file:
                data = file.read(FILE_MAX)
        except OSError as err:
            _log.warning("the canary couldn't read %s (%s)", path, err.strerror)
            continue
        if any(form in data for form in forms):
            return True
    return False


# The apps.

def _refusals(socket_name: str) -> Callable:
    """Starlette's own refusals, a path no route has and a method a route doesn't take, as GateRefusalBody; a route
    of the other socket's says where it is."""
    other = [compile_path(route.path)[0] for route in gateproto.ROUTES if route.socket != socket_name]
    elsewhere = ON_THE_CONTROL_SOCKET if socket_name == "status" else ON_THE_STATUS_SOCKET

    async def refuse(request: Request, exc: HTTPException) -> JSONResponse:
        if exc.status_code == 404:
            path = request.scope.get("path", "")
            message = elsewhere if any(regex.match(path) for regex in other) else NO_SUCH_ROUTE
        elif exc.status_code == 405:
            message = WRONG_METHOD
        else:
            message = NO_SUCH_ROUTE
        headers = {"connection": "close"} | ({"allow": exc.headers["Allow"]} if "Allow" in (exc.headers or {}) else {})
        return JSONResponse({"message": message, "code": None}, status_code=exc.status_code, headers=headers)

    return refuse


async def _broken(request: Request, exc: Exception) -> JSONResponse:
    """What escaped a route (none should: each catches its own): 500 in words, logged without its text."""
    _log.error("the gate's app broke outside a route: %s at %s", type(exc).__name__, _where(exc))
    return JSONResponse({"message": BROKE, "code": None}, status_code=500, headers={"connection": "close"})


def build_apps(core: Any, *, front_uid: int, admin_uids: Callable[[], set[int]]) -> tuple[Starlette, Starlette]:
    """The status app and the control app, on `core` (Task 18's GateCore, started in the servers' loop by `spark
    gate`). `front_uid` is spark-front's uid; `admin_uids()` gives spark-admin's members' uids, asked at each request
    that needs it, so a member added is known without a restart. Each app's `state.stopping`, one event for both, ends
    the answers that wait once it is set (the module's docstring); `state.ping_every_s` is the events stream's ping."""
    gate = _Gate(core, front_uid, admin_uids, asyncio.Event())
    status_app = Starlette(exception_handlers={HTTPException: _refusals("status"), Exception: _broken})
    control_app = Starlette(exception_handlers={HTTPException: _refusals("control"), Exception: _broken})
    for app in (status_app, control_app):
        app.state.stopping = gate.stopping
        app.state.ping_every_s = PING_EVERY_S
        app.router.redirect_slashes = False  # a redirect isn't one of the gate's answers
    routes = _Routes(gate, status_app.state)
    handlers: dict[tuple[str, str, str], tuple[str, Any]] = {
        ("status", "POST", "/v1/admit"): ("waiting", routes.admit),
        ("status", "GET", "/v1/front/events"): ("waiting", routes.events),
        ("status", "POST", "/v1/front/inflight"): ("bounded", routes.inflight),
        ("status", "POST", "/v1/front/drained"): ("bounded", routes.drained),
        ("status", "POST", "/v1/front/busy"): ("bounded", routes.busy),
        ("status", "POST", "/v1/front/refused"): ("bounded", routes.refused),
        ("status", "GET", "/v1/status"): ("bounded", routes.status_for),
        ("status", "POST", "/v1/sessions"): ("bounded", routes.session_begin),
        ("status", "POST", "/v1/sessions/{id}/renew"): ("bounded", routes.session_renew),
        ("status", "DELETE", "/v1/sessions/{id}"): ("bounded", routes.session_end),
        ("control", "GET", "/v1/status"): ("bounded", routes.status_full),
        ("control", "POST", "/v1/load"): ("waiting", routes.load),
        ("control", "POST", "/v1/unload"): ("waiting", routes.unload),
        ("control", "POST", "/v1/pin"): ("waiting", routes.pin),
        ("control", "DELETE", "/v1/pin/{model}"): ("bounded", routes.unpin),
        ("control", "POST", "/v1/make-room/plan"): ("bounded", routes.room_plan),
        ("control", "POST", "/v1/make-room"): ("waiting", routes.room),
        ("control", "POST", "/v1/release"): ("bounded", routes.release),
        ("control", "GET", "/v1/quiet"): ("bounded", routes.quiet),
        ("control", "POST", "/v1/drain-all"): ("bounded", routes.drain_all),
        ("control", "POST", "/v1/undrain-all"): ("bounded", routes.undrain_all),
        ("control", "POST", "/v1/apply/renew"): ("bounded", routes.apply_renew),
        ("control", "POST", "/v1/apply/begin"): ("bounded", routes.apply_begin),
        ("control", "POST", "/v1/apply/end"): ("bounded", routes.apply_end),
        ("control", "GET", "/v1/logs/{model}"): ("bounded", routes.logs),
        ("control", "POST", "/v1/canary"): ("bounded", routes.canary),
    }
    if set(handlers) != {(r.socket, r.method, r.path) for r in gateproto.ROUTES}:
        raise RuntimeError("the gate's routes and gateproto.ROUTES disagree")
    before = {("status", "POST", "/v1/sessions"): routes.holds_sessions}
    for route in gateproto.ROUTES:
        kind, handler = handlers[(route.socket, route.method, route.path)]
        endpoint = _Endpoint(gate, route, before_body=before.get((route.socket, route.method, route.path)),
                             **{kind: handler})
        app = status_app if route.socket == "status" else control_app
        app.router.routes.append(Route(route.path, endpoint, methods=[route.method]))
    return status_app, control_app

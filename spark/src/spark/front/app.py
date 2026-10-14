"""The front's ASGI app (Phase 2a; plan.md, *The front and the gate*): raw ASGI on uvicorn, no framework, served on
127.0.0.1:9100 through Task 9's protocol, which closes a connection whose request head, or whose body nobody reads,
doesn't come in time.

**A request's way**, each step before the next:
1. `GET /health` is the front's own: 200, `{"status": "ok"}`, with no key needed and no journal line.
2. **The key**, from the headers alone (parse.authenticate), before the first receive(): no key, or one no digest
   matches, is answered 401 at once, its body unread, so it falls under Task 9's deadline (a body the app is still
   reading has no limit, so reading before authorizing would give that hold back). The key comes first, so a caller
   the front doesn't know learns only that, and each refusal after it has a key to name.
3. **The route**: only parse.ROUTES go on; any other gets 404 `route_not_served`, so llama-swap's admin routes are
   never reached through the front.
4. Both Content-Length and Transfer-Encoding: 400.
5. **The key's open cap** (parse.KeyCaps' `max_open`): past it, 429 `too_many_requests`, the body unread.
6. **The body**, by its Content-Length before any is read, then as it comes: on `/v1/audio/transcriptions` a multipart
   form, its file parts spooled to disk past parse.SPOOL_ABOVE for the form, at most MAX_BODY_BYTES; anywhere else
   JSON, read in memory, at most MAX_JSON_BYTES and parse.MAX_JSON_PARTS parts. 413 past a cap, 400 for what can't be
   parsed. A spooled file is deleted when the request ends, however it ends.
   - **Memory** (the controller's rulings, at Task 20's review). Every body held in memory is charged to one budget
     for the whole front, BODY_BUDGET_BYTES (parse.BodyBudget). A JSON body is charged by its Content-Length before
     any of it is read, or as it comes, and then as the bytes it goes on as. A form is charged parse.FORM_IN_MEMORY,
     what it may hold in memory. A request that would pass the budget gets 429 `front_busy`, and gives back what it
     holds when it ends, however it ends.
   - **What that bounds, measured on the Spark at Task 20's review.** Parsing is synchronous, so at most one body is
     parsed at a time.
     - The worst parse measured grew the process by 192 MiB at its peak, the bytes the budget counts for it included:
       a 16 MiB string of ASCII with one emoji, which Python then holds at 4 bytes a character.
     - A million parts took at most 150 MiB (500,000 objects, each with a key of its own), and a body past the cap
       48 MiB, to refuse it.
     - The process held 33 MiB before any request.
     So the front stays under about 420 MiB (33 + 192 + 192): within the unit's MemoryMax=512M, though not "well
     inside" it, as this first said.
7. **The model**, by its name or one of its roles, in the current model list (parse.ModelList): 404
   `model_not_found` naming the models, on-demand first, then the residents, each in the list's order.
8. **An upload** (a form) goes on only while fewer than UPLOADS_AT_ONCE are forwarded or waiting for a load,
   front-wide; past that, 429 `uploads_busy`, never forwarded. It takes its slot here, before the gate is asked, so
   one the cap refuses never waits for a load first (the controller's ruling, at Task 21's review). llama-swap v257
   reads every POST body whole into memory before its per-model limit applies (internal/swaputil/http.go:542), so
   each 200 MiB upload forwarded costs it about 200 MiB of the unified pool (the controller's ruling, at Task 20's
   review). A JSON body needs no such cap: the front holds each one it forwards, within its body budget, until its
   request ends, so llama-swap's copies are within that budget too.
9. **A model not loaded** waits for the gate's admission, within the key's waiting cap (429 past it), its deadline
   when the front received the request plus the key's wait; the gate's refusal goes to the client as the gate words
   it, with its status and headers.
10. **Forwarded, rebuilt** (upstream.Upstream): to the route's own path, without the query; the client's headers less
   its credentials, its framing, the hop-by-hop ones and the addresses it may claim (parse.clean_headers), with the
   front's own Content-Type for the body it rebuilt: the JSON re-serialized, the form re-encoded, at each send, so a
   resend (Task 22's) encodes the form again. Upstream adds the internal key, over a connection of the asking key's
   own, with no read timeout. A request for a model is counted in flight (upstream.InFlight) from here until its
   response ends, however it ends: the counts the gate's drains read (rule 4).
11. **The answer goes back as it comes**, chunk by chunk, while receive() is watched beside it for the client's
   disconnect, which cancels the upstream call, so llama-swap and the engine stop too. llama-swap's own 429
   (`concurrency_limit`) comes back worded, with its Retry-After (Task 6's words). An event stream on a route with a
   last event (upstream.StreamEnd) that ends without it, or any answer whose connection breaks off, is a **cut**: the
   front ends the client's response without its last chunk, so uvicorn closes the connection and the client's HTTP
   library reports a body that never ended, and it injects nothing (plan.md, *Nothing is injected into a reply
   stream*; the controller's ruling, at Task 12's review). An event stream the front watches so goes to the client
   chunked, its Content-Length dropped, so a cut shows in the client's framing. A stream whose last event is the
   engine's own error event ends as the engine ended it. llama-swap giving no answer at all is a 502 of the front's
   own, `llama_swap_unreached` (Task 22 asks the gate instead).

**At start**, `spark front` (Task 21) reads the private key list and the key digests from their credentials
(read_keys), and FrontApp refuses to start, naming the key, when the two disagree (check_keys).

Every refusal of the front's own is in the asking key's words (refusal()): its label, and its group's `words`, so
`model_not_found`'s next step is the Mac's `make clients` for Dan's pi and agent's own command for agent's (Task 6).
Nothing reaches a client as a traceback: an error of the front's own is logged by where it broke, never its text,
which code can build from a request, and answered 500 in words, with `x-should-retry: false`. Each request but
`/health` leaves one journal line (parse.journal_line): its key's name, its model's, and its status, or how it ended
without one whole: `gone` (its client went first), `cut` (its answer broke off, or was cut short by the front's own
stop) or `error` (its stream ended with the engine's error event); and its duration.

The calls the app makes, on Task 21's Upstream and Task 22's GateLink, or the tests' stand-ins:
- `upstream`: `await forward(method, path, headers, body, key)`, `body` None, bytes or an async iterator of bytes,
  giving a response with `status`, `headers`, `aiter_raw()` and `await aclose()`; upstream.Unreached for no answer,
  and upstream.Broken from `aiter_raw()` for a body that broke off;
- `gate`: `await loaded(model) -> bool`, and `await admit(model, key, deadline, request_id)`, giving gateproto's
  AdmitAnswer."""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import logging
import os
import secrets
import time
import traceback
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from spark import messages
from spark.credentials import parse_digests, read_credential_text
from spark.front import parse
from spark.front.upstream import (Broken, InFlight, StreamEnd, Unreached, concurrency_limited,
                                  retry_after)
from spark.registry import parse_keys

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from spark.gateproto import ModelEntry
    from spark.registry import ClientKey, KeyGroup

KEYS_CREDENTIAL = "keys"  # the private key list, /etc/local-ai/keys.yaml, by LoadCredential=
DIGESTS_CREDENTIAL = "client-keys"  # the client keys' digests, `<key name> <sha256 hex>` lines
MAX_BODY_BYTES = 200 * 2**20  # a form's body, spooled: the ~170 MB uploads *Speech* allows
MAX_JSON_BYTES = 16 * 2**20  # a JSON body, parsed in memory
BODY_BUDGET_BYTES = 192 * 2**20  # the bodies held in memory, all requests together (the controller's ruling)
SPOOL_DIR = Path("/tmp")  # the unit's PrivateTmp=, on disk, not tmpfs
UNKNOWN_KEY_CODE = "invalid_api_key"
BROKE_CODE = "front_error"
# The front's own error, by the asking key's group's words: only Dan reads the front's journal.
BROKE = {"dan": "The front couldn't answer: it hit an error of its own. On the Spark, `make logs s=front` shows why.",
         "agent": "The front couldn't answer: it hit an error of its own. Dan can see why with `make logs s=front` on "
                  "the Spark."}
JSON = (b"content-type", b"application/json")
# The 500's: OpenAI's SDKs retry any 5xx unless told, so a deterministic error of the front's would be sent again (the
# controller's ruling, at Task 20's review). pi's own retry still matches the status.
NO_RETRY = (b"x-should-retry", b"false")
# The uploads forwarded at once, or waiting for a load, front-wide. llama-swap v257 reads each forwarded upload whole
# into memory (internal/swaputil/http.go:542), up to MAX_BODY_BYTES, so they hold at most about 800 MiB of the unified
# pool in llama-swap. v257 passes them on to whisper-server, up to its per-model limit; what whisper-server holds of the
# ones it hasn't reached yet isn't measured (the controller's ruling, at Task 21's review).
UPLOADS_AT_ONCE = 4
UPLOADS_BUSY_CODE = "uploads_busy"
UPLOADS_RETRY_AFTER_S = 30
# llama-swap giving no answer: no connection, or one that failed before its head. A 502, which pi and OpenAI's SDKs
# retry, as is sensible for a llama-swap that is restarting. Task 22 asks the gate instead.
UNREACHED_CODE = "llama_swap_unreached"
UNREACHED = {"dan": "The front couldn't reach the model service on the Spark. On the Spark, `make logs s=llama-swap` "
                    "shows why.",
             "agent": "The front couldn't reach the model service on the Spark. Dan can see why with "
                      "`make logs s=llama-swap` on the Spark."}
ANSWER_MAX = 64 << 10  # a 429's body read to tell llama-swap's own, far more than its few hundred bytes
_log = logging.getLogger(__name__)


def uploads_busy(at_once: int) -> parse.Refused:
    """429: the front is already forwarding `at_once` uploads, the most it sends llama-swap at once."""
    return parse.Refused(429, UPLOADS_BUSY_CODE, f"The front is already sending {at_once} uploads on to the model "
                         f"service, the most it sends at once; try again in {UPLOADS_RETRY_AFTER_S} seconds.",
                         UPLOADS_RETRY_AFTER_S)


class KeyListError(ValueError):
    """The key digests and the private key list disagree. Its text names the key, never a digest."""


def check_keys(keys: dict[str, ClientKey], digests: dict[str, bytes]) -> None:
    """Every digest's name is a key in the list, and every key in the list has a digest; refused otherwise, naming the
    keys."""
    unlisted = sorted(set(digests) - set(keys))
    if unlisted:
        raise KeyListError(f"the key digests (client-keys) have a digest for {', '.join(unlisted)}, which the key list "
                           "(keys) doesn't name: each key needs its line in the list, with its group and label")
    missing = sorted(set(keys) - set(digests))
    if missing:
        raise KeyListError(f"the key list (keys) names {', '.join(missing)}, with no digest in the key digests "
                           "(client-keys)")


def read_keys(groups: dict[str, KeyGroup],
              env: Mapping[str, str] = os.environ) -> tuple[dict[str, ClientKey], dict[str, bytes]]:
    """The private key list and the client keys' digests, each a credential LoadCredential= gives (`keys` and
    `client-keys`), read whole (credentials.read_credential_text) and parsed: the list against the registry's key
    groups (registry.parse_keys, which refuses, naming the key, a label pi's retry list matches), the digests by
    credentials.parse_digests; then checked against each other (check_keys). `spark front` reads them once, at its
    start (Task 21). Each refusal names a credential, a key or a line, never a key or a digest: a CredentialError, a
    RegistryError or a KeyListError."""
    keys = parse_keys(read_credential_text(KEYS_CREDENTIAL, env), groups)
    digests = parse_digests(read_credential_text(DIGESTS_CREDENTIAL, env))
    check_keys(keys, digests)
    return keys, digests


def _where(err: BaseException) -> str:
    """Where `err` was raised, by file, line and function: never its text, which can quote a request."""
    frames = traceback.extract_tb(err.__traceback__)
    if not frames:
        return "an unknown place"
    last = frames[-1]
    return f"{Path(last.filename).name}:{last.lineno} in {last.name}"


@dataclass
class _Request:
    """One request as the front has it so far, for its answer and its journal line."""

    started: float  # when the front received it, by its clock: Unix seconds, as the gate's deadlines are
    held: parse.Held  # its share of the body budget, given back when it ends
    route: tuple[str, str] | None = None  # set once it is one of parse.ROUTES
    key: str | None = None  # the key's name, once known
    model: str | None = None  # the model's registry name, once resolved
    entry: ModelEntry | None = None  # the model's entry in the model list, once resolved
    status: int | None = None  # the status sent, once its head is
    complete: bool = False  # its answer was sent whole
    ended: str | None = None  # how it ended without its answer whole, or with an error event: gone, cut, error
    json: bytes | None = None  # its JSON body as it goes on, held until it ends, so it can go again
    form: parse.Form | None = None  # its spooled form, deleted when it ends

    def outcome(self) -> int | str:
        """Its journal line's status: how it ended, else its status, `gone` when it ended before any answer (its
        client went first), and `cut` when its answer began and never ended whole (cancelled, as by the front's own
        stop, or broken by an error of the front's own)."""
        if self.ended is not None:
            return self.ended
        if self.status is None:
            return "gone"
        return self.status if self.complete else "cut"


class FrontApp:
    """The front's ASGI app. Refuses to start, naming the key and never a digest, when the key digests and the private
    key list (Task 4) disagree."""

    def __init__(self, models: parse.ModelList, keys: dict[str, ClientKey], groups: dict[str, KeyGroup],
                 digests: dict[str, bytes], *, upstream: Any, gate: Any, caps: parse.KeyCaps,
                 log: Callable[[str], None], clock: Callable[[], float] = time.time, spool_dir: Path = SPOOL_DIR,
                 budget: parse.BodyBudget | None = None, inflight: InFlight | None = None,
                 uploads_at_once: int = UPLOADS_AT_ONCE):
        check_keys(keys, digests)
        self.models = models
        self.keys, self.groups, self.digests = keys, groups, digests
        self.upstream, self.gate, self.caps = upstream, gate, caps
        self.log, self.clock, self.spool_dir = log, clock, spool_dir
        self.budget = parse.BodyBudget(BODY_BUDGET_BYTES) if budget is None else budget
        self.inflight = InFlight(clock) if inflight is None else inflight
        self.uploads_at_once = uploads_at_once
        self.uploading = 0  # the uploads forwarded now, front-wide

    def refusal(self, code: str, key: str, **fields: Any) -> messages.Refusal:
        """The front's own refusal `code` for `key`, in its words: its label, and its group's `words`,
        `names_processes` and wait, with the code's other fields (messages.Moment's)."""
        client = self.keys[key]
        group = self.groups[client.group]
        return messages.refusal(code, messages.Moment(words=group.words, key_label=client.label,
                                                      names_processes=group.names_processes, wait_s=group.wait_s,
                                                      **fields))

    async def __call__(self, scope: dict, receive: Callable[[], Awaitable[dict]],
                       send: Callable[[dict], Awaitable[None]]) -> None:
        if scope["type"] != "http":
            return  # lifespan is off, and a WebSocket never reaches the app (serve's ws="none")
        if (scope["method"], scope["path"]) == parse.HEALTH:
            await _answer(send, 200, {"status": "ok"})
            return
        request = _Request(started=self.clock(), held=parse.Held(self.budget))
        try:
            await self._serve(scope, receive, send, request)
        except Exception as err:
            route = " ".join(request.route) if request.route else "request"  # a path it serves, never a client's
            _log.error("the front's %s broke: %s at %s", route, type(err).__name__, _where(err))
            if request.status is None:
                with contextlib.suppress(Exception):  # a client already gone
                    await self._send(send, request, 500, {"error": {"message": BROKE[self._words(request)],
                                                                    "code": BROKE_CODE}}, [JSON, NO_RETRY])
        finally:
            request.held.release()
            if request.form is not None:
                request.form.close()
            self.log(parse.journal_line(request.key or "-", request.model or "-", request.outcome(),
                                        self.clock() - request.started))

    async def _serve(self, scope: dict, receive, send, request: _Request) -> None:
        headers = scope["headers"]
        key = parse.authenticate(headers, self.digests)
        if key is None:
            await self._send(send, request, 401, {"error": {"message": messages.UNKNOWN_KEY,
                                                            "code": UNKNOWN_KEY_CODE}})
            return
        request.key = key
        route = (scope["method"], scope["path"])
        if route not in parse.ROUTES:
            await self._refuse(send, request, self.refusal("route_not_served", key))
            return
        request.route = route
        if parse.both_lengths(headers):
            await self._refuse(send, request, parse.Refused(400, parse.BAD_REQUEST, parse.BOTH_LENGTHS))
            return
        if not self.caps.enter_open(key):
            await self._refuse(send, request, self.refusal("too_many_requests", key))
            return
        try:
            await self._open(headers, receive, send, request)
        finally:
            self.caps.leave_open(key)

    async def _open(self, headers: parse.Headers, receive, send, request: _Request) -> None:
        method, path = request.route
        sent_headers = parse.clean_headers(headers)
        if method == "POST":
            try:
                name = await self._read(path, headers, receive, request)
            except parse.Refused as refused:
                await self._refuse(send, request, refused)
                return
            except parse.ClientGone:
                return
            entry = parse.resolve(name, self.models)
            if entry is None:
                await self._refuse(send, request, self.refusal("model_not_found", request.key, asked_name=name,
                                                               models=self._listed()))
                return
            request.model, request.entry = entry["name"], entry
        if request.form is None:
            await self._admitted(method, path, sent_headers, receive, send, request)
            return
        # An upload takes its slot before the gate is asked, so one the cap refuses never waits for a load, or starts
        # one, first; one waiting for its load holds its slot (the controller's ruling, at Task 21's review).
        if self.uploading >= self.uploads_at_once:
            await self._refuse(send, request, uploads_busy(self.uploads_at_once))
            return
        self.uploading += 1  # nothing awaits between the check and the take: one event loop
        try:
            await self._admitted(method, path, sent_headers, receive, send, request)
        finally:
            self.uploading -= 1

    async def _admitted(self, method: str, path: str, headers: parse.Headers, receive, send,
                        request: _Request) -> None:
        """A request for a model the gate says isn't loaded waits for its admission (_wait_for_load), then is
        forwarded; one for a loaded model, or with no model (`GET /v1/models`), is forwarded at once."""
        if request.model is not None and not await self.gate.loaded(request.model):
            refused = await self._wait_for_load(request.model, request)
            if refused is not None:
                await self._refuse(send, request, refused)
                return
        await self._forward(method, path, headers, receive, send, request)

    async def _forward(self, method: str, path: str, headers: parse.Headers, receive, send,
                       request: _Request) -> None:
        """The request forwarded and its answer relayed (_relay), counted in flight for its model from here until the
        relay has ended, however it ends; receive() watched beside it, and the relay, with its upstream call,
        cancelled when the client goes first. An error of the relay's is raised once it has ended, or, when this
        task was cancelled meanwhile, logged as one plain line."""
        counted = None if request.model is None else self.inflight.enter(request.model, request.key)
        try:
            relay = asyncio.ensure_future(self._relay(method, path, headers, send, request))
            watch = asyncio.ensure_future(_disconnected(receive))
            cancelled = False
            try:
                await asyncio.wait((relay, watch), return_when=asyncio.FIRST_COMPLETED)
                # uvicorn also gives http.disconnect once the answer is whole, and a relay that has ended its answer
                # (whole, cut or with the engine's error) is only closing: neither is a client gone first, and the
                # close is never cut short (the controller's ruling, at Task 21's review).
                if not relay.done() and not request.complete and request.ended is None:
                    request.ended = "gone"
                    relay.cancel()
            except BaseException:  # this task cancelled, as by the front's own stop: the relay with it
                cancelled = True
                relay.cancel()
                raise
            finally:
                watch.cancel()
                await asyncio.wait((relay, watch))  # the relay's own cleanup closes the upstream answer
                # Each task's end taken, so asyncio never reports one later as an exception never retrieved, with its
                # traceback (the controller's ruling, at Task 21's review).
                if not watch.cancelled():
                    watch.exception()
                broke = None if relay.cancelled() else relay.exception()
                if cancelled and broke is not None:
                    route = " ".join(request.route)
                    _log.error("the front's %s broke as it was cancelled: %s at %s", route, type(broke).__name__,
                               _where(broke))
            if broke is not None:
                raise broke
        finally:
            if counted is not None:
                self.inflight.leave(counted)

    async def _relay(self, method: str, path: str, headers: parse.Headers, send, request: _Request) -> None:
        """One send to llama-swap, its body made for it (_body), and its answer back to the client as it comes."""
        content_type, body = self._body(request)
        sent = headers if content_type is None else [*headers, (b"content-type", content_type)]
        try:
            response = await self.upstream.forward(method, path, sent, body, request.key)
        except Unreached:
            await self._send(send, request, 502, {"error": {"message": UNREACHED[self._words(request)],
                                                            "code": UNREACHED_CODE}})
            return
        chunks = response.aiter_raw()
        try:
            head, broke = b"", False
            if response.status == 429 and request.entry is not None:
                head, broke = await _upto(chunks, ANSWER_MAX)
                if not broke and concurrency_limited(response.status, head):
                    worded = self.refusal("concurrency_limit", request.key, model_label=request.entry["label"])
                    await self._refuse(send, request, dataclasses.replace(
                        worded, retry_after_s=retry_after(response.headers)))
                    return
            end = StreamEnd.watching(path, response.status, response.headers)
            sent_back = response.headers
            if end is not None:  # chunked to the client, so a cut shows in its framing, which a length would hide
                sent_back = [(name, value) for name, value in sent_back if name != b"content-length"]
            await send({"type": "http.response.start", "status": response.status, "headers": sent_back})
            request.status = response.status
            if head:
                await send({"type": "http.response.body", "body": head, "more_body": True})
            try:
                if broke:
                    raise Broken
                async for chunk in chunks:
                    if chunk:
                        if end is not None:
                            end.feed(chunk)
                        await send({"type": "http.response.body", "body": chunk, "more_body": True})
            except Broken:
                request.ended = "cut"
                return  # without the last chunk: uvicorn closes the connection, and the client sees a cut
            if end is not None and end.outcome() != "done":
                request.ended = end.outcome()
                if request.ended == "cut":
                    return  # an event stream without its last event: a cut, passed on as one
            await send({"type": "http.response.body", "body": b""})
            request.complete = True
        finally:
            closing = getattr(chunks, "aclose", None)
            if closing is not None:
                await closing()
            await response.aclose()

    @staticmethod
    def _body(request: _Request) -> tuple[bytes | None, bytes | AsyncIterator[bytes] | None]:
        """(its Content-Type, the body to send on), made at each send: a form encoded again, from its spooled files,
        which live until the request ends; a JSON body's bytes, held; none for a GET."""
        if request.form is not None:
            kind, body = parse.encode_form(request.form)
            return kind.encode(), body
        if request.json is not None:
            return JSON[1], request.json
        return None, None

    def _words(self, request: _Request) -> str:
        """Whose words the request's answers of the front's own are in: its key's group's, Dan's for no key."""
        return self.groups[self.keys[request.key].group].words if request.key else "dan"

    async def _read(self, path: str, headers: parse.Headers, receive, request: _Request) -> str:
        """The body, kept in `request` (its form, or its JSON as it goes on); the model it names. Refused by its
        Content-Length before any of it is read, by its cap or the body budget, then as parse reads it."""
        content_type = next((value for name, value in headers if name == b"content-type"), b"").decode("latin-1")
        is_form = path in parse.FORM_PATHS
        limit = MAX_BODY_BYTES if is_form else MAX_JSON_BYTES
        length = parse.declared_length(headers)
        if length is not None and length > limit:
            raise parse.too_big(limit)
        if is_form:
            request.held.take(parse.FORM_IN_MEMORY)
            request.form = await parse.read_form(receive, content_type, spool_dir=self.spool_dir, max_bytes=limit)
            return request.form.model
        if content_type.split(";", 1)[0].strip().lower().startswith("multipart/"):
            raise parse.Refused(400, parse.BAD_REQUEST, parse.FORM_NOT_TAKEN)
        if length is not None:
            request.held.take(length)  # before any of it is read
        parsed, request.json = await parse.read_json(receive, max_bytes=limit, held=request.held)
        return parsed["model"]

    async def _wait_for_load(self, model: str, request: _Request) -> messages.Refusal | None:
        """The gate's answer for a model not loaded: None once admitted, else its refusal. The request waits within
        its key's waiting cap, until the deadline its key's wait gives."""
        key = request.key
        if not self.caps.enter_waiting(key):
            return self.refusal("too_many_requests", key)
        try:
            deadline = request.started + self.groups[self.keys[key].group].wait_s
            answer = await self.gate.admit(model, key, deadline, secrets.token_hex(8))
        finally:
            self.caps.leave_waiting(key)
        if answer.get("ok") is True:
            return None
        return messages.Refusal(answer["code"], answer["status"], answer["message"], answer.get("retry_after_s"))

    def _listed(self) -> list[tuple[str, str]]:
        """The models as model_not_found names them, (label, name): on-demand first, then the residents, each in the
        list's order (Task 6)."""
        return [(entry["label"], entry["name"]) for entry in sorted(self.models, key=lambda entry: entry["resident"])]

    async def _refuse(self, send, request: _Request, refused: messages.Refusal | parse.Refused) -> None:
        await self._send(send, request, refused.status, refused.body(), refused.headers())

    async def _send(self, send, request: _Request, status: int, payload: dict,
                    headers: parse.Headers | None = None) -> None:
        await _answer(send, status, payload, headers, request)


async def _answer(send, status: int, payload: dict, headers: parse.Headers | None = None,
                  request: _Request | None = None) -> None:
    """One answer of the front's own, JSON, whole."""
    data = json.dumps(payload).encode()
    head = [*(headers or [JSON]), (b"content-length", str(len(data)).encode())]
    await send({"type": "http.response.start", "status": status, "headers": head})
    if request is not None:
        request.status = status
    await send({"type": "http.response.body", "body": data})
    if request is not None:
        request.complete = True


async def _disconnected(receive) -> None:
    """Until receive() gives http.disconnect: the client gone, or, from uvicorn, the answer whole."""
    while (await receive())["type"] != "http.disconnect":
        pass


async def _upto(chunks: AsyncIterator[bytes], limit: int) -> tuple[bytes, bool]:
    """The body's bytes from `chunks` until it ends or passes `limit`, and whether it broke off meanwhile; what is
    left of it stays in `chunks`."""
    data = bytearray()
    try:
        while len(data) <= limit:
            data += await anext(chunks)
    except StopAsyncIteration:
        pass
    except Broken:
        return bytes(data), True
    return bytes(data), False

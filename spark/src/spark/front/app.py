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
5a. **The model list** (`GET /v1/models`) is the front's own answer, from its copy of the gate's model list
   (_model_list), never sent upstream and never held (the controller's ruling, at Task 22's review). It has llama-swap
   v257's shape (internal/server/api.go, handleListModels) for the registry's models, so pi's and the web UI's saved
   model ids keep working: `{"object": "list", "data": [...]}`, one entry a model, sorted by name, each with its `id`
   (the model's name: render sets no `includeAliasesInList`, so v257 lists no role as an id), `object` "model",
   `created` (now), `owned_by` "llama-swap", `meta.llamaswap` (`type` "model", and its roles as `aliases` when it
   has any) and `status.value` ("loaded" while the gate's last `state` lists it, ready, starting or draining, as
   v257 counts a running model; else "unloaded"). What v257 discovers by probing a ready engine (`context_length`,
   `capabilities` and the like) isn't listed: the front probes nothing.
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
9. **Cleared by the gate** (Task 22's GateLink.clear), with nothing sent to the client meanwhile: a loaded model's
   request at once; one for a model not loaded, a model being drained, or any while apply's hold stands, waits within
   the key's waiting cap (429 past it), its deadline when the front received the request plus the key's wait. The
   client's disconnect, watched meanwhile, drops the wait, and with it the gate's admit call. The gate's refusal goes
   to the client as the gate words it, with its status and headers; the front's own (`gate_down`, `draining`,
   `restarting`) in the asking key's words. A request is counted in flight (upstream.InFlight) from the step the gate
   link clears it in until its response ends, however it ends: the counts the gate's drains read (rule 4).
10. **Forwarded, rebuilt** (upstream.Upstream): to the route's own path, without the query; the client's headers less
   its credentials, its framing, the hop-by-hop ones and the addresses it may claim (parse.clean_headers), with the
   front's own Content-Type for the body it rebuilt: the JSON re-serialized, the form re-encoded, at each send, so a
   resend encodes the form again from its spooled files. Upstream adds the internal key, over a connection of the
   asking key's own, with no read timeout.
11. **The answer goes back as it comes**, chunk by chunk, while receive() is watched beside it for the client's
   disconnect, which cancels the upstream call, so llama-swap and the engine stop too. llama-swap's own 429
   (`concurrency_limit`) comes back worded, with its Retry-After (Task 6's words). An event stream on a route with a
   last event (upstream.StreamEnd) that ends without it, or any answer whose connection breaks off, is a **cut**: the
   front ends the client's response without its last chunk, so uvicorn closes the connection and the client's HTTP
   library reports a body that never ended, and it injects nothing (plan.md, *Nothing is injected into a reply
   stream*; the controller's ruling, at Task 12's review). An event stream the front watches so goes to the client
   chunked, its Content-Length dropped, so a cut shows in the client's framing. A stream whose last event is the
   engine's own error event ends as the engine ended it.
12. **Sent again, before any of an answer has gone** (Task 22). llama-swap's 500 `upstream command exited prematurely`
   (`spark launch` refused the start for want of a ticket, so the front's view was stale, or the model crashed) goes
   through admission once, then is sent again from the body the front holds; a second one reaches the client as 409
   `load_failed`. No answer at all (a refused connection, or one reset before its head: llama-swap crashed or
   restarting) goes through admission too, with its original deadline, so the gate answers it (`restarting`,
   `llama_swap_down`, or a load); while the gate admits it and llama-swap still doesn't answer, it goes through
   admission and is sent again RESEND_AFTER_S apart, and gets the front's own `llama_swap_down` at its deadline. Its
   key's waiting slot is held only while it waits on the gate link. Every request forwarded names a model: the
   model list is the front's own answer (5a), so no request has a 502 of the front's own for llama-swap giving no
   answer (Task 21's `llama_swap_unreached`, retired at Task 22's review).

**At start**, `spark front` (Task 21) reads the private key list and the key digests from their credentials
(read_keys), and FrontApp refuses to start, naming the key, when the two disagree (check_keys).

Every refusal of the front's own is in the asking key's words (refusal()): its label, and its group's `words`, so
`model_not_found`'s next step is the Mac's `make clients` for Dan's pi and agent's own command for agent's (Task 6),
and `draining`'s is make-room's for Dan and Dan's for agent. The front's own `model_not_found`, `too_many_requests`,
`route_not_served` and `draining` reach the gate too, while its call is open (GateLink.refused). Nothing reaches a
client as a traceback: an error of the front's own is logged by where it broke, never its text, which code can build
from a request, and answered 500 in words, with `x-should-retry: false`. Each request but `/health` leaves one journal
line (parse.journal_line): its key's name, its model's, and its status, or how it ended without one whole: `gone` (its
client went first), `cut` (its answer broke off, or was cut short by the front's own stop) or `error` (its stream
ended with the engine's error event); its duration; and why, where the gate gives it: a refusal's code when it came
from the gate, or after a wait for a load, a drain, apply's hold or llama-swap; and, for a cut, the gate link's
CUT_WHY when the gate's events show the model gone from llama-swap's `/running` (the brake, a crash, a kill). A cut's
line waits for those events (GateLink.why_cut); the client's response never does.

The calls the app makes, on Task 21's Upstream and Task 22's GateLink, or the tests' stand-ins:
- `upstream`: `await forward(method, path, headers, body, key)`, `body` None, bytes or an async iterator of bytes,
  giving a response with `status`, `headers`, `aiter_raw()` and `await aclose()`; upstream.Unreached for no answer,
  and upstream.Broken from `aiter_raw()` for a body that broke off;
- `gate`: `await clear(model, key, deadline, request_id, enter=…, admit=…, waits=…)`, giving what `enter()` gave (the
  request counted in flight) or a gatelink.Refused; `refused(code, model, key, message)`; `await why_cut(model,
  since)`, giving the words for a cut's journal line, or None; and `running_models()`, the models the model list
  shows loaded."""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import functools
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
from spark.front.gatelink import Refused
from spark.front.upstream import (Broken, InFlight, InFlightRequest, StreamEnd, Unreached, concurrency_limited,
                                  exited_prematurely, retry_after)
from spark.gateproto import FRONT_REFUSAL_CODES
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
ANSWER_MAX = 64 << 10  # a 429's or a 500's body read to tell llama-swap's own, far more than its few hundred bytes
# A request llama-swap gave no answer, while the gate admits it, is sent again this long after (Task 22): the gate
# admits a model it counts ready at once, so without a pause the two would go back and forth until the deadline.
RESEND_AFTER_S = 1.0
EXITED, UNANSWERED = "exited", "unanswered"  # how a send ended that goes through admission again
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
    asked: str | None = None  # the model as the client named it, once read
    model: str | None = None  # the model's registry name, once resolved
    entry: ModelEntry | None = None  # the model's entry in the model list, once resolved
    status: int | None = None  # the status sent, once its head is
    complete: bool = False  # its answer was sent whole
    ended: str | None = None  # how it ended without its answer whole, or with an error event: gone, cut, error
    json: bytes | None = None  # its JSON body as it goes on, held until it ends, so it can go again
    form: parse.Form | None = None  # its spooled form, deleted when it ends
    forwarded_at: float | None = None  # when it was last counted in flight, by the front's clock
    why: str | None = None  # why it was refused, for its journal line, where the gate gives it

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
                 uploads_at_once: int = UPLOADS_AT_ONCE, resend_after_s: float = RESEND_AFTER_S):
        check_keys(keys, digests)
        self.models = models
        self.keys, self.groups, self.digests = keys, groups, digests
        self.upstream, self.gate, self.caps = upstream, gate, caps
        self.log, self.clock, self.spool_dir = log, clock, spool_dir
        self.budget = parse.BodyBudget(BODY_BUDGET_BYTES) if budget is None else budget
        self.inflight = InFlight(clock) if inflight is None else inflight
        if getattr(gate, "inflight", self.inflight) is not self.inflight:
            # The gate link answers drains from the count it holds: it must be the one the app counts in.
            raise ValueError("the front's gate link and its app count requests in flight in two places")
        self.uploads_at_once = uploads_at_once
        self.uploading = 0  # the uploads forwarded now, front-wide
        self.resend_after_s = resend_after_s
        self._lines: set[asyncio.Task] = set()  # cuts' journal lines, each waiting for why

    async def aclose(self) -> None:
        """The cuts' journal lines still waiting for why are written now, without it."""
        lines = list(self._lines)
        for task in lines:
            task.cancel()
        await asyncio.gather(*lines, return_exceptions=True)

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
            seconds = self.clock() - request.started
            if request.outcome() == "cut" and request.model is not None and request.forwarded_at is not None:
                self._cut_line(request, seconds)
            else:
                self.log(parse.journal_line(request.key or "-", request.model or "-", request.outcome(), seconds,
                                            why=request.why))

    def _cut_line(self, request: _Request, seconds: float) -> None:
        """A cut's journal line, once the gate link has said why, or that it can't (GateLink.why_cut), in a task of its
        own: the client's response has already ended."""
        async def line() -> None:
            why = None
            try:
                why = await self.gate.why_cut(request.model, request.forwarded_at)
            except Exception as err:
                _log.error("the front couldn't tell why a stream was cut: %s at %s", type(err).__name__, _where(err))
            finally:
                self.log(parse.journal_line(request.key, request.model, "cut", seconds, why=why))

        task = asyncio.ensure_future(line())
        self._lines.add(task)
        task.add_done_callback(self._lines.discard)

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
        if request.route == parse.MODEL_LIST:
            await self._send(send, request, 200, self._model_list())
            return
        method, path = request.route  # each route left is a POST that names its model
        sent_headers = parse.clean_headers(headers)
        try:
            name = await self._read(path, headers, receive, request)
        except parse.Refused as refused:
            await self._refuse(send, request, refused)
            return
        except parse.ClientGone:
            return
        request.asked = name
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
        """The request cleared by the gate (_cleared), then forwarded; sent again through admission when llama-swap
        answers that its start exited, once, or gives no answer, until its deadline (step 12)."""
        deadline = request.started + self.groups[self.keys[request.key].group].wait_s
        admit, exits = False, 0
        while True:
            cleared = await self._cleared(request, receive, deadline, admit)
            if cleared is None:
                return  # the client went while it waited
            if isinstance(cleared, Refused):
                await self._refuse_cleared(send, request, cleared)
                return
            way = await self._forward(method, path, headers, receive, send, request, cleared)
            if way is None:
                return
            if way == EXITED:
                exits += 1
                if exits > 1:
                    await self._refuse_cleared(send, request, Refused("load_failed"))
                    return
            elif self.clock() >= deadline:
                await self._refuse_cleared(send, request, Refused("llama_swap_down"))
                return
            elif admit:
                await asyncio.sleep(self.resend_after_s)
            admit = True

    async def _cleared(self, request: _Request, receive, deadline: float,
                       admit: bool) -> InFlightRequest | Refused | None:
        """The gate link's answer for the request (GateLink.clear): counted in flight, or its refusal; None when the
        client went first, which drops the wait, and with it any admit call. While it waits, it holds one of its key's
        waiting slots.

        Its bookkeeping awaits nothing, so no cancel can skip it (the controller's ruling, at Task 22's review): the
        waiting slot is given back by a done-callback on the gate link's work, however that ends; and a count the work
        cleared that the request won't forward, its client gone or its own task cancelled, is left there too, or at
        once when the work had already ended (InFlight.leave counts a request once, however often it is left)."""
        key, model = request.key, request.model
        waiting = abandoned = False

        def waits() -> bool:
            nonlocal waiting
            waiting = self.caps.enter_waiting(key)
            return waiting

        def leave_unused(work: asyncio.Future) -> None:
            if not work.cancelled() and work.exception() is None and not isinstance(work.result(), Refused):
                self.inflight.leave(work.result())

        def settle(work: asyncio.Future) -> None:
            if waiting:
                self.caps.leave_waiting(key)
            if abandoned:
                leave_unused(work)
            elif not work.cancelled():
                work.exception()  # taken: an error is raised from the result below

        work = asyncio.ensure_future(self.gate.clear(model, key, deadline, secrets.token_hex(8), admit=admit,
                                                     waits=waits, enter=functools.partial(self.inflight.enter,
                                                                                          model, key)))
        work.add_done_callback(settle)
        watch = asyncio.ensure_future(_disconnected(receive))
        watch.add_done_callback(lambda done: done.cancelled() or done.exception())
        try:
            await asyncio.wait((work, watch), return_when=asyncio.FIRST_COMPLETED)
        except BaseException:  # this task cancelled, as by the front's own stop: the wait with it, and its count
            abandoned = True
            watch.cancel()
            if work.done():
                leave_unused(work)
            else:
                work.cancel()
            raise
        watch.cancel()
        if not work.done():  # the client went first
            abandoned = True
            work.cancel()
            return None
        cleared = work.result()
        if not isinstance(cleared, Refused):
            request.forwarded_at = cleared.started_at
        return cleared

    async def _refuse_cleared(self, send, request: _Request, refused: Refused) -> None:
        """A refusal the gate link gave, or one after a send: the gate's as the gate words it; the front's own in the
        asking key's words, with the model's label and command where the code takes them. Its code goes in the journal
        line, but too_many_requests', the key's cap, which waits for nothing."""
        if refused.answer is not None:
            answer = refused.answer
            worded = messages.Refusal(answer["code"], answer["status"], answer["message"], answer.get("retry_after_s"))
        else:
            entry = request.entry
            fields = dict(refused.fields)
            if refused.code in ("draining", "load_failed"):
                fields["model_label"] = entry["label"]
            if refused.code == "load_failed":
                fields.update(model_command=(entry["roles"] or [entry["name"]])[0], start_ended=True)
            worded = self.refusal(refused.code, request.key, **fields)
        if worded.code != "too_many_requests":
            request.why = worded.code
        await self._refuse(send, request, worded)

    async def _forward(self, method: str, path: str, headers: parse.Headers, receive, send,
                       request: _Request, counted: InFlightRequest) -> str | None:
        """The request forwarded and its answer relayed (_relay), counted in flight (`counted`, as the gate link
        cleared it) until the relay has ended, however it ends; receive() watched beside it, and the relay, with its
        upstream call, cancelled when the client goes first. EXITED or UNANSWERED when the send ended so, nothing of an
        answer sent, else None. An error of the relay's is raised once it has ended, or, when this task was cancelled
        meanwhile, logged as one plain line."""
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
            return None if relay.cancelled() else relay.result()
        finally:
            self.inflight.leave(counted)

    async def _relay(self, method: str, path: str, headers: parse.Headers, send, request: _Request) -> str | None:
        """One send to llama-swap, its body made for it (_body), and its answer back to the client as it comes; or
        EXITED or UNANSWERED instead, nothing sent (step 12)."""
        content_type, body = self._body(request)
        sent = [*headers, (b"content-type", content_type)]
        try:
            response = await self.upstream.forward(method, path, sent, body, request.key)
        except Unreached:
            return UNANSWERED
        chunks = response.aiter_raw()
        try:
            head, broke = b"", False
            if response.status in (429, 500):
                head, broke = await _upto(chunks, ANSWER_MAX)
                if not broke and exited_prematurely(response.status, head):
                    return EXITED
                if not broke and concurrency_limited(response.status, head):
                    worded = self.refusal("concurrency_limit", request.key, model_label=request.entry["label"])
                    await self._refuse(send, request, dataclasses.replace(
                        worded, retry_after_s=retry_after(response.headers)))
                    return None
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
                return None  # without the last chunk: uvicorn closes the connection, and the client sees a cut
            if end is not None and end.outcome() != "done":
                request.ended = end.outcome()
                if request.ended == "cut":
                    return None  # an event stream without its last event: a cut, passed on as one
            await send({"type": "http.response.body", "body": b""})
            request.complete = True
            return None
        finally:
            closing = getattr(chunks, "aclose", None)
            if closing is not None:
                await closing()
            await response.aclose()

    @staticmethod
    def _body(request: _Request) -> tuple[bytes, bytes | AsyncIterator[bytes]]:
        """(its Content-Type, the body to send on), made at each send: a form encoded again, from its spooled files,
        which live until the request ends; a JSON body's bytes, held."""
        if request.form is not None:
            kind, body = parse.encode_form(request.form)
            return kind.encode(), body
        return JSON[1], request.json

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

    def _model_list(self) -> dict:
        """`GET /v1/models`' answer, in llama-swap v257's shape for the registry's models (step 5a)."""
        created, running = int(self.clock()), self.gate.running_models()
        data = []
        for entry in sorted(self.models, key=lambda entry: entry["name"]):
            meta: dict[str, Any] = {"type": "model"}
            if entry["roles"]:
                meta["aliases"] = list(entry["roles"])
            data.append({"id": entry["name"], "object": "model", "created": created, "owned_by": "llama-swap",
                         "meta": {"llamaswap": meta},
                         "status": {"value": "loaded" if entry["name"] in running else "unloaded"}})
        return {"object": "list", "data": data}

    def _listed(self) -> list[tuple[str, str]]:
        """The models as model_not_found names them, (label, name): on-demand first, then the residents, each in the
        list's order (Task 6)."""
        return [(entry["label"], entry["name"]) for entry in sorted(self.models, key=lambda entry: entry["resident"])]

    async def _refuse(self, send, request: _Request, refused: messages.Refusal | parse.Refused) -> None:
        """A refusal sent; one of the front's own that the gate keeps (FRONT_REFUSAL_CODES) told to it too: the model
        as the client named it for model_not_found, else its registry name."""
        if refused.code in FRONT_REFUSAL_CODES and request.key is not None:
            model = request.asked if refused.code == "model_not_found" else request.model
            self.gate.refused(refused.code, model, request.key, refused.message)
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

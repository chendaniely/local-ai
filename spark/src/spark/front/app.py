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
   form, its files spooled to disk past parse.SPOOL_ABOVE, at most MAX_BODY_BYTES; anywhere else JSON, read in memory,
   at most MAX_JSON_BYTES, so a few at once stay well inside the unit's MemoryMax=512M. 413 past the cap, 400 for what
   can't be parsed. A spooled file is deleted when the request ends, however it ends.
7. **The model**, by its name or one of its roles, in the current model list (parse.ModelList): 404
   `model_not_found` naming the models, on-demand first, then the residents, each in the list's order.
8. **A model not loaded** waits for the gate's admission, within the key's waiting cap (429 past it), its deadline
   when the front received the request plus the key's wait; the gate's refusal goes to the client as the gate words
   it, with its status and headers.
9. **Forwarded, rebuilt**: to the route's own path, without the query; the client's headers less its credentials,
   its framing and the hop-by-hop ones (parse.clean_headers), with the front's own Content-Type for the body it
   rebuilt: the JSON re-serialized, the form re-encoded. Task 21's Upstream adds the internal key.

**At start**, `spark front` (Task 21) reads the private key list and the key digests from their credentials
(read_keys), and FrontApp refuses to start, naming the key, when the two disagree (check_keys).

Every refusal of the front's own is in the asking key's words (refusal()): its label, and its group's `words`, so
`model_not_found`'s next step is the Mac's `make clients` for Dan's pi and agent's own command for agent's (Task 6).
Nothing reaches a client as a traceback: an error of the front's own is logged by where it broke, never its text,
which code can build from a request, and answered 500 in words. Each request but `/health` leaves one journal line
(parse.journal_line): its key's name, its model's, its status and its duration.

The stand-ins this task's tests give, and the calls the app makes on them:
- `upstream` (Task 21's Upstream): `await forward(method, path, headers, body, key)`, `body` None, bytes or an async
  iterator of bytes, giving a response with `status`, `headers`, `aiter_raw()` and `await aclose()`;
- `gate` (Task 22's GateLink): `await loaded(model) -> bool`, and `await admit(model, key, deadline, request_id)`,
  giving gateproto's AdmitAnswer."""

from __future__ import annotations

import contextlib
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
from spark.registry import parse_keys

if TYPE_CHECKING:
    from spark.registry import ClientKey, KeyGroup

KEYS_CREDENTIAL = "keys"  # the private key list, /etc/local-ai/keys.yaml, by LoadCredential=
DIGESTS_CREDENTIAL = "client-keys"  # the client keys' digests, `<key name> <sha256 hex>` lines
MAX_BODY_BYTES = 200 * 2**20  # a form's body, spooled: the ~170 MB uploads *Speech* allows
MAX_JSON_BYTES = 16 * 2**20  # a JSON body, parsed in memory
SPOOL_DIR = Path("/tmp")  # the unit's PrivateTmp=, on disk, not tmpfs
UNKNOWN_KEY_CODE = "invalid_api_key"
BROKE_CODE = "front_error"
# The front's own error, by the asking key's group's words: only Dan reads the front's journal.
BROKE = {"dan": "The front couldn't answer: it hit an error of its own. On the Spark, `make logs s=front` shows why.",
         "agent": "The front couldn't answer: it hit an error of its own. Dan can see why with `make logs s=front` on "
                  "the Spark."}
JSON = (b"content-type", b"application/json")
_log = logging.getLogger(__name__)


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
    route: tuple[str, str] | None = None  # set once it is one of parse.ROUTES
    key: str | None = None  # the key's name, once known
    model: str | None = None  # the model's registry name, once resolved
    status: int | None = None  # the status sent, once its head is
    form: parse.Form | None = None  # its spooled form, deleted when it ends


class FrontApp:
    """The front's ASGI app. Refuses to start, naming the key and never a digest, when the key digests and the private
    key list (Task 4) disagree."""

    def __init__(self, models: parse.ModelList, keys: dict[str, ClientKey], groups: dict[str, KeyGroup],
                 digests: dict[str, bytes], *, upstream: Any, gate: Any, caps: parse.KeyCaps,
                 log: Callable[[str], None], clock: Callable[[], float] = time.time, spool_dir: Path = SPOOL_DIR):
        check_keys(keys, digests)
        self.models = models
        self.keys, self.groups, self.digests = keys, groups, digests
        self.upstream, self.gate, self.caps = upstream, gate, caps
        self.log, self.clock, self.spool_dir = log, clock, spool_dir

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
        request = _Request(started=self.clock())
        try:
            await self._serve(scope, receive, send, request)
        except Exception as err:
            route = " ".join(request.route) if request.route else "request"  # a path it serves, never a client's
            _log.error("the front's %s broke: %s at %s", route, type(err).__name__, _where(err))
            if request.status is None:
                words = self.groups[self.keys[request.key].group].words if request.key else "dan"
                with contextlib.suppress(Exception):  # a client already gone
                    await self._send(send, request, 500, {"error": {"message": BROKE[words], "code": BROKE_CODE}})
        finally:
            if request.form is not None:
                request.form.close()
            self.log(parse.journal_line(request.key or "-", request.model or "-",
                                        "gone" if request.status is None else request.status,
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
        body = None
        if method == "POST":
            try:
                name, body, content_type = await self._read(path, headers, receive, request)
            except parse.Refused as refused:
                await self._refuse(send, request, refused)
                return
            except parse.ClientGone:
                return
            sent_headers.append((b"content-type", content_type))
            entry = parse.resolve(name, self.models)
            if entry is None:
                await self._refuse(send, request, self.refusal("model_not_found", request.key, asked_name=name,
                                                               models=self._listed()))
                return
            request.model = entry["name"]
            if not await self.gate.loaded(entry["name"]):
                refused = await self._wait_for_load(entry["name"], request)
                if refused is not None:
                    await self._refuse(send, request, refused)
                    return
        response = await self.upstream.forward(method, path, sent_headers, body, request.key)
        try:
            await send({"type": "http.response.start", "status": response.status, "headers": response.headers})
            request.status = response.status
            async for chunk in response.aiter_raw():
                if chunk:
                    await send({"type": "http.response.body", "body": chunk, "more_body": True})
            await send({"type": "http.response.body", "body": b""})
        finally:
            await response.aclose()

    async def _read(self, path: str, headers: parse.Headers, receive, request: _Request):
        """The body: (the model it names, the body to send on, its Content-Type). Refused by its Content-Length
        before any of it is read, then as parse reads it."""
        content_type = next((value for name, value in headers if name == b"content-type"), b"").decode("latin-1")
        is_form = path in parse.FORM_PATHS
        limit = MAX_BODY_BYTES if is_form else MAX_JSON_BYTES
        length = parse.declared_length(headers)
        if length is not None and length > limit:
            raise parse.too_big(limit)
        if is_form:
            request.form = await parse.read_form(receive, content_type, spool_dir=self.spool_dir, max_bytes=limit)
            kind, body = parse.encode_form(request.form)
            return request.form.model, body, kind.encode()
        if content_type.split(";", 1)[0].strip().lower().startswith("multipart/"):
            raise parse.Refused(400, parse.BAD_REQUEST, parse.FORM_NOT_TAKEN)
        parsed, sent = await parse.read_json(receive, max_bytes=limit)
        return parsed["model"], sent, JSON[1]

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

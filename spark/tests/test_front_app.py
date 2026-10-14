"""The front's ASGI app (website/design/phase-2a.md, Task 20): only the inference routes reach llama-swap; client keys
held as digests and checked before any of a body is read; bodies parsed within their caps, uploads spooled to disk,
and every request rebuilt; per-key caps; the front's own refusals in the asking key's words; one journal line a
request, naming the key, the model, the status and the duration only.

The app is called as uvicorn calls it, with a recording stand-in for llama-swap (Task 21's Upstream, as the app calls
it) and one for the gate (Task 22's GateLink); two tests serve it on a real uvicorn with Task 9's protocol, on
127.0.0.1 (uvicorn_helpers.py). The stand-ins are front_helpers.py's."""

import asyncio
import dataclasses
import hashlib
import itertools
import json
import logging
import re

import anyio
import pytest

from front_helpers import (BOUNDARY, DIGESTS, GROUPS, KEYS, KEYS_TEXT, MIB, MODELS, field, file_part,
                           form_body)
from spark import messages
from spark.credentials import CredentialError
from spark.front import app as front_app
from spark.front import gatelink, parse
from spark.protocols import make_protocol
from spark.registry import RegistryError
from uvicorn_helpers import (HEADER_TIMEOUT_S, connect, read_response, response, serving, tcp_listener,
                             trickle_until_closed)

BOUND_S = 10
NAMES = [entry["name"] for entry in MODELS]
CODER, GEMMA, EMBED, WHISPER = "qwen3.8-27b", "gemma-4-26b-a4b", "qwen3-embedding-0.6b", "whisper-large-v3-turbo"
FORM_TYPE = f"multipart/form-data; boundary={BOUNDARY}".encode()


@pytest.fixture
def anyio_backend():
    return "asyncio"


@dataclasses.dataclass
class Forwarded:
    method: str
    path: str
    headers: list[tuple[bytes, bytes]]
    body: bytes | None
    key: str


class Response:
    """What Task 21's Upstream.forward returns: the status, the headers and the body's chunks."""

    def __init__(self, status: int = 200, chunks: tuple[bytes, ...] = (b'{"ok":true}',)):
        self.status = status
        self.headers = [(b"content-type", b"application/json")]
        self._chunks = chunks
        self.closed = False

    async def aiter_raw(self):
        for chunk in self._chunks:
            yield chunk

    async def aclose(self):
        self.closed = True


class Upstream:
    """llama-swap's stand-in: it records each request, its body read whole, and answers 200. `during` is called with
    each request once its body is read, before the answer; `hold`, when set, holds every answer until it is."""

    def __init__(self):
        self.seen: list[Forwarded] = []
        self.responses: list[Response] = []
        self.during = None
        self.hold: asyncio.Event | None = None
        self.raises: Exception | None = None

    async def forward(self, method, path, headers, body, key):
        if body is not None and not isinstance(body, bytes):
            body = b"".join([chunk async for chunk in body])
        seen = Forwarded(method, path, list(headers), body, key)
        self.seen.append(seen)
        if self.during is not None:
            self.during(seen)
        if self.raises is not None:
            raise self.raises
        if self.hold is not None:
            await self.hold.wait()
        response = Response()
        self.responses.append(response)
        return response


class Gate:
    """The gate's stand-in, as the app calls Task 22's GateLink: a model in `ready` is cleared at once, counted as it is
    (`enter`); any other takes a waiting slot (`waits`), too_many_requests without one, and asks for an admit, which
    it records and answers with `answer`, held while `hold` is set and not yet released. The front's own refusals it
    is told of are kept in `refusals`."""

    def __init__(self, ready=NAMES):
        self.ready = set(ready)
        self.admits: list[tuple[str, str, float, str]] = []
        self.hold: asyncio.Event | None = None
        self.answer: dict = {"ok": True}
        self.refusals: list[tuple[str, str | None, str, str]] = []

    async def clear(self, model, key, deadline, request_id, *, enter, admit=False, waits=lambda: True):
        if not admit and model in self.ready:
            return enter()
        if not waits():
            return gatelink.Refused("too_many_requests")
        self.admits.append((model, key, deadline, request_id))
        if self.hold is not None:
            await self.hold.wait()
        return enter() if self.answer.get("ok") else gatelink.Refused(self.answer["code"], answer=self.answer)

    def refused(self, code: str, model: str | None, key: str, message: str) -> None:
        self.refusals.append((code, model, key, message))

    async def why_cut(self, model: str, since: float) -> None:
        return None


@dataclasses.dataclass
class Front:
    app: front_app.FrontApp
    upstream: Upstream
    gate: Gate
    lines: list[str]
    spool: object
    now: list[float]


def make_front(tmp_path, *, ready=NAMES, keys=KEYS, digests=DIGESTS, budget: int | None = None) -> Front:
    spool = tmp_path / "spool"
    spool.mkdir(exist_ok=True)
    upstream, gate, lines, now = Upstream(), Gate(ready), [], [1000.0]
    app = front_app.FrontApp(parse.ModelList(MODELS), keys, GROUPS, digests, upstream=upstream, gate=gate,
                             caps=parse.KeyCaps(keys, GROUPS), log=lines.append, clock=lambda: now[0],
                             spool_dir=spool, budget=None if budget is None else parse.BodyBudget(budget))
    return Front(app, upstream, gate, lines, spool, now)


@pytest.fixture
def front(tmp_path):
    return make_front(tmp_path)


@dataclasses.dataclass
class Answer:
    status: int
    headers: dict[str, str]
    body: bytes
    received: bool  # whether the app called receive() before it answered

    def json(self):
        return json.loads(self.body)


def _with_more(chunks):
    """Each chunk with whether another follows."""
    iterator = iter(chunks)
    current = next(iterator, None)
    while current is not None:
        following = next(iterator, None)
        yield current, following is not None
        current = following


async def call(app, method: str, path: str, *, key: str | None = "k1", body: bytes | None = None, chunks=None,
               content_type: bytes | None = b"application/json", length: int | bool | None = None,
               headers: list[tuple[bytes, bytes]] = (), query: bytes = b"") -> Answer:
    """One request, as uvicorn's h11 protocol makes it: the key as a bearer key, the body in one message or in
    `chunks`, its Content-Length counted unless `length` gives one or is False (a chunked body); then nothing until
    the answer ends, when the client goes."""
    if chunks is None:
        chunks = [] if body is None else [body]
    raw = [(b"host", b"spark")]
    if key is not None:
        raw.append((b"authorization", b"Bearer " + key.encode()))
    if content_type is not None and (body is not None or chunks):
        raw.append((b"content-type", content_type))
    if length is None:
        length = sum(len(chunk) for chunk in chunks)
    if length is not False:
        raw.append((b"content-length", str(length).encode()))
    raw += list(headers)
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
             "method": method, "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": query,
             "root_path": "", "headers": raw, "client": ("127.0.0.1", 50000), "server": ("127.0.0.1", 9100),
             "extensions": {}, "state": {}}
    pending = _with_more(chunks)
    done = asyncio.Event()
    calls = 0
    sent: list[dict] = []
    received = False

    async def receive():
        nonlocal calls
        calls += 1
        step = next(pending, None)
        if step is not None:
            return {"type": "http.request", "body": step[0], "more_body": step[1]}
        if calls == 1:
            return {"type": "http.request", "body": b"", "more_body": False}
        await done.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        nonlocal received
        if message["type"] == "http.response.start":
            received = calls > 0
        sent.append(message)
        if message["type"] == "http.response.body" and not message.get("more_body"):
            done.set()

    with anyio.fail_after(BOUND_S):
        await app(scope, receive, send)
    start = sent[0]
    names = {name.decode().lower(): value.decode() for name, value in start.get("headers", [])}
    return Answer(start["status"], names, b"".join(m.get("body", b"") for m in sent[1:]), received)


def chat(model: str = "coder", **more) -> bytes:
    return json.dumps({"model": model, "messages": [{"role": "user", "content": "hi"}], **more}).encode()


def refusal_body(code: str, **moment) -> dict:
    return messages.refusal(code, messages.Moment(**moment)).body()


async def until(condition):
    while not condition():
        await asyncio.sleep(0.005)


UNKNOWN_KEY_BODY = {"error": {"message": "That API key isn't one the Spark knows. Check SPARK_API_KEY on this machine.",
                              "code": "invalid_api_key"}}


# Routes.

@pytest.mark.anyio
@pytest.mark.parametrize(("method", "path"), [
    ("GET", "/unload"), ("POST", "/api/models/unload/x"), ("POST", "/api/inflight/1/cancel"), ("GET", "/logs/stream"),
    ("GET", "/upstream/x/health"), ("GET", "/running"), ("GET", "/ui"), ("GET", "/metrics"),
    ("GET", "/v1/chat/completions"), ("POST", "/v1/models"),
    # and a few near misses
    ("POST", "/health"), ("HEAD", "/v1/models"), ("GET", "/v1/models/"), ("POST", "/v1/chat/completions/"),
    ("POST", "/v1/audio/translations"), ("GET", "/"),
    # uvicorn decodes the path but never normalizes it: no traversal, doubled slash or case reaches a route, and a
    # %2F decodes to the same unmatched path (the controller's ruling, at Task 20's review)
    ("GET", "//logs"), ("GET", "/v1/../logs"), ("GET", "/v1/models/.."), ("GET", "/v1/models/../../running"),
    ("POST", "/v1/chat/completions/../../api/models/unload/x"), ("GET", "/V1/MODELS"), ("get", "/v1/models"),
    ("post", "/v1/chat/completions"), ("get", "/health"), ("GET", "/v1/x/../models"), ("POST", "/v1//chat/completions"),
])
async def test_only_the_listed_routes_go_through_and_the_rest_get_route_not_served(front, method, path):
    answer = await call(front.app, method, path, body=chat() if method == "POST" else None)
    assert answer.status == 404
    assert answer.json() == {"error": {"message": messages.refusal("route_not_served", messages.Moment()).message,
                                       "code": "route_not_served"}}
    assert "x-should-retry" not in answer.headers and "retry-after" not in answer.headers
    assert not answer.received  # answered before any of the body was read
    assert front.upstream.seen == [] and front.gate.admits == []
    assert front.lines == ["front: dan-mac - 404 0.0s"]


@pytest.mark.anyio
async def test_a_route_not_served_without_a_key_gets_401(front):
    # The key is checked first: a caller the front doesn't know learns nothing but that.
    answer = await call(front.app, "POST", "/api/models/unload/x", key=None)
    assert (answer.status, answer.json()) == (401, UNKNOWN_KEY_BODY)


@pytest.mark.anyio
async def test_the_path_sent_on_is_the_routes_own_with_no_query(front):
    await call(front.app, "POST", "/v1/chat/completions", body=chat(), query=b"x=1")
    await call(front.app, "GET", "/v1/models", query=b"y=2")
    assert [(seen.method, seen.path) for seen in front.upstream.seen] == [("POST", "/v1/chat/completions"),
                                                                          ("GET", "/v1/models")]


@pytest.mark.anyio
async def test_the_model_list_route_is_forwarded_without_a_body_or_the_gate(front):
    front.gate.ready = set()
    answer = await call(front.app, "GET", "/v1/models", key="k2")
    assert (answer.status, answer.body) == (200, b'{"ok":true}')
    [seen] = front.upstream.seen
    assert (seen.body, seen.key) == (None, "agent")
    assert front.gate.admits == []
    assert front.lines == ["front: agent - 200 0.0s"]


@pytest.mark.anyio
async def test_health_is_answered_by_the_front_itself(front):
    answer = await call(front.app, "GET", "/health", key=None)
    assert (answer.status, answer.json()) == (200, {"status": "ok"})
    assert front.upstream.seen == [] and front.lines == []


# Keys.

@pytest.mark.anyio
async def test_an_unknown_key_is_refused_and_never_logged(front, caplog):
    caplog.set_level(logging.DEBUG)
    answer = await call(front.app, "POST", "/v1/chat/completions", key="nope", body=chat())
    assert (answer.status, answer.json()) == (401, UNKNOWN_KEY_BODY)
    assert answer.headers["content-type"] == "application/json"
    assert not answer.received
    assert front.upstream.seen == []
    logged = "\n".join(front.lines) + caplog.text
    assert front.lines == ["front: - - 401 0.0s"]
    assert "nope" not in logged and hashlib.sha256(b"nope").hexdigest() not in logged


@pytest.mark.anyio
async def test_no_key_is_refused(front):
    answer = await call(front.app, "POST", "/v1/chat/completions", key=None, body=chat())
    assert (answer.status, answer.json()) == (401, UNKNOWN_KEY_BODY)
    assert not answer.received and front.upstream.seen == []


@pytest.mark.anyio
async def test_bearer_and_x_api_key_both_authenticate(front):
    assert (await call(front.app, "POST", "/v1/chat/completions", body=chat())).status == 200
    answer = await call(front.app, "POST", "/v1/chat/completions", key=None, body=chat(), headers=[(b"x-api-key",
                                                                                                    b"k2")])
    assert answer.status == 200
    assert [seen.key for seen in front.upstream.seen] == ["dan-mac", "agent"]


@pytest.mark.anyio
async def test_the_digests_must_match_the_key_list(tmp_path):
    robot = {**DIGESTS, "robot": hashlib.sha256(b"k9").digest()}
    with pytest.raises(front_app.KeyListError) as excinfo:
        make_front(tmp_path, digests=robot)
    assert "robot" in str(excinfo.value)
    no_agent = {name: digest for name, digest in DIGESTS.items() if name != "agent"}
    with pytest.raises(front_app.KeyListError) as excinfo2:
        make_front(tmp_path, digests=no_agent)
    assert "agent" in str(excinfo2.value)
    for err in (excinfo.value, excinfo2.value):
        assert not re.search(r"[0-9a-f]{16}", str(err))  # never a digest
        assert isinstance(err, ValueError)  # so `spark front` refuses it as its other startup errors (Task 21)


def credentials(tmp_path, keys_text: str = KEYS_TEXT, digests: dict[str, bytes] = DIGESTS) -> dict[str, str]:
    """The front's two credentials as LoadCredential= gives them, stand-ins in a folder of the test's own: the
    environment that names it."""
    folder = tmp_path / "credentials"
    folder.mkdir(exist_ok=True)
    (folder / "keys").write_text(keys_text)
    (folder / "client-keys").write_text("# name, then sha256\n"
                                        + "".join(f"{name} {digest.hex()}\n" for name, digest in digests.items()))
    return {"CREDENTIALS_DIRECTORY": str(folder)}


def test_the_key_list_and_its_digests_are_read_from_their_credentials(tmp_path):
    env = credentials(tmp_path)
    assert front_app.read_keys(GROUPS, env) == (KEYS, DIGESTS)
    (tmp_path / "credentials" / "client-keys").unlink()
    with pytest.raises(CredentialError, match="client-keys"):
        front_app.read_keys(GROUPS, env)
    (tmp_path / "credentials" / "keys").write_bytes(b"keys:\n  agent: {group: agent, label: \xff}\n")
    with pytest.raises(CredentialError) as excinfo:  # read_credential_text's refusal, which never quotes the file
        front_app.read_keys(GROUPS, env)
    assert str(excinfo.value) == "the credential keys isn't UTF-8 text"
    # The two must agree: a digest the list lacks refuses the start, by its key's name.
    env = credentials(tmp_path, digests={**DIGESTS, "robot": hashlib.sha256(b"k9").digest()})
    with pytest.raises(front_app.KeyListError, match="robot"):
        front_app.read_keys(GROUPS, env)


def test_a_key_label_pi_would_retry_refuses_the_start(tmp_path):
    # A key's label goes into its refusals, and pi retries every refusal whose text its list matches (Task 6's
    # deferred note, done by registry.parse_keys at Task 19).
    text = KEYS_TEXT.replace("agent: {group: agent, label: agent,", "agent: {group: agent, label: agent timeout,")
    assert text != KEYS_TEXT
    with pytest.raises(RegistryError) as excinfo:
        front_app.read_keys(GROUPS, credentials(tmp_path, keys_text=text))
    assert str(excinfo.value).startswith("keys: agent: its label holds 'timeout'")
    assert not re.search(r"[0-9a-f]{16}", str(excinfo.value))


# Bodies.

@pytest.mark.anyio
async def test_the_model_is_read_from_json_or_a_form_and_roles_resolve(front):
    front.gate.ready = set()  # each goes through the gate, which sees the model's own name
    assert (await call(front.app, "POST", "/v1/chat/completions", body=chat("coder"))).status == 200
    form = form_body([field("model", "stt"), file_part(b"RIFF")])
    assert (await call(front.app, "POST", "/v1/audio/transcriptions", body=form, content_type=FORM_TYPE)).status == 200
    assert (await call(front.app, "POST", "/v1/chat/completions", body=chat("gemma-4-26b-a4b"))).status == 200
    assert [admit[0] for admit in front.gate.admits] == [CODER, WHISPER, GEMMA]
    assert [line.split()[2] for line in front.lines] == [CODER, WHISPER, GEMMA]
    # What goes on names the model as the client did: llama-swap resolves the roles itself.
    assert json.loads(front.upstream.seen[0].body)["model"] == "coder"


@pytest.mark.anyio
async def test_a_form_with_two_model_fields_is_refused(front):
    form = form_body([field("model", "stt"), field("model", "coder"), file_part(b"RIFF")])
    answer = await call(front.app, "POST", "/v1/audio/transcriptions", body=form, content_type=FORM_TYPE)
    assert (answer.status, answer.json()["error"]) == (400, {"message": parse.TWO_MODELS, "code": "invalid_request"})
    assert front.upstream.seen == []


@pytest.mark.anyio
async def test_a_json_body_goes_on_reserialized(front):
    raw = b'{"model":"coder",  "x" : 1}'
    await call(front.app, "POST", "/v1/chat/completions", body=raw)
    [seen] = front.upstream.seen
    assert seen.body == json.dumps(json.loads(raw), separators=(",", ":")).encode()
    assert (b"content-type", b"application/json") in seen.headers


@pytest.mark.anyio
async def test_a_form_goes_on_rebuilt(front):
    form = form_body([field("model", "stt"), field("language", "en"), file_part(b"RIFF-data")])
    await call(front.app, "POST", "/v1/audio/transcriptions", body=form, content_type=FORM_TYPE)
    [seen] = front.upstream.seen
    [content_type] = [value for name, value in seen.headers if name == b"content-type"]
    assert re.fullmatch(rb"multipart/form-data; boundary=spark-[0-9a-f]{32}", content_type)
    boundary = content_type.split(b"=", 1)[1]
    assert BOUNDARY.encode() not in seen.body  # rebuilt, never the client's bytes passed on
    assert seen.body.endswith(b"--" + boundary + b"--\r\n")
    assert b'name="language"\r\n\r\nen\r\n' in seen.body and b"RIFF-data" in seen.body


@pytest.mark.anyio
async def test_a_body_of_the_wrong_kind_for_its_route_is_400(front):
    form = form_body([field("model", "stt")])
    answer = await call(front.app, "POST", "/v1/chat/completions", body=form, content_type=FORM_TYPE)
    assert (answer.status, answer.json()["error"]["message"]) == (400, parse.FORM_NOT_TAKEN)
    answer = await call(front.app, "POST", "/v1/audio/transcriptions", body=chat("stt"))
    assert (answer.status, answer.json()["error"]["message"]) == (400, parse.NOT_A_FORM)
    assert front.upstream.seen == []


@pytest.mark.anyio
async def test_content_length_with_transfer_encoding_is_refused_as_asgi(front):
    answer = await call(front.app, "POST", "/v1/chat/completions", body=chat(),
                        headers=[(b"transfer-encoding", b"chunked")])
    assert (answer.status, answer.json()["error"]["message"]) == (400, parse.BOTH_LENGTHS)
    assert not answer.received and front.upstream.seen == []


def test_content_length_with_transfer_encoding_is_refused(tmp_path):
    # h11 0.16 takes such a request, framing it by Transfer-Encoding, so the 400 is the front's.
    body = chat()
    request = (b"POST /v1/chat/completions HTTP/1.1\r\nHost: spark\r\nAuthorization: Bearer k1\r\n"
               b"Content-Type: application/json\r\nContent-Length: 5\r\nTransfer-Encoding: chunked\r\n\r\n"
               + f"{len(body):x}\r\n".encode() + body + b"\r\n0\r\n\r\n")
    front = make_front(tmp_path)
    with tcp_listener() as listener, \
            serving(front.app, listener, make_protocol(peer_cred=False, header_timeout_s=10)):
        with connect(listener) as client:
            client.sendall(request)
            lines, answer = response(client)
    assert lines[0] == b"HTTP/1.1 400 Bad Request"
    assert json.loads(answer)["error"]["message"] == parse.BOTH_LENGTHS
    assert front.upstream.seen == []


@pytest.mark.anyio
async def test_auth_cookie_and_proxy_auth_headers_never_go_upstream(front):
    await call(front.app, "POST", "/v1/chat/completions", body=chat(),
               headers=[(b"x-api-key", b"k1"), (b"cookie", b"session=s1"), (b"proxy-authorization", b"Basic eA=="),
                        (b"connection", b"keep-alive"), (b"x-custom", b"1"), (b"x-forwarded-for", b"203.0.113.9"),
                        (b"x-real-ip", b"203.0.113.9"), (b"forwarded", b"for=203.0.113.9")])
    [seen] = front.upstream.seen
    assert seen.headers == [(b"x-custom", b"1"), (b"content-type", b"application/json")]


@pytest.mark.anyio
async def test_an_upload_is_spooled_to_disk_and_deleted_when_the_request_ends(front):
    big = bytes(range(256)) * (3 * 4096)  # 3 MiB
    during = []
    front.upstream.during = lambda seen: during.append([path.read_bytes() for path in front.spool.iterdir()])
    form = form_body([field("model", "stt"), file_part(big)])
    chunks = [form[i:i + 65536] for i in range(0, len(form), 65536)]
    answer = await call(front.app, "POST", "/v1/audio/transcriptions", chunks=chunks, content_type=FORM_TYPE)
    assert answer.status == 200
    assert during == [[big]]  # while it ran, one file of its bytes
    assert list(front.spool.iterdir()) == []  # after its answer, nothing
    assert big in front.upstream.seen[0].body

    small = form_body([field("model", "stt"), file_part(b"s" * (100 << 10))])
    assert (await call(front.app, "POST", "/v1/audio/transcriptions", body=small, content_type=FORM_TYPE)).status == 200
    assert during[1] == []  # a 100 KiB part writes no file


@pytest.mark.anyio
async def test_an_uploads_file_goes_however_its_request_ends(front):
    # Task 33's doctor doesn't search the front's private /tmp: this pins that nothing is left there.
    form = form_body([field("model", "stt"), file_part(b"q" * (2 * MIB))])
    front.upstream.raises = RuntimeError("upstream broke")  # an error of the front's own: a 500
    answer = await call(front.app, "POST", "/v1/audio/transcriptions", body=form, content_type=FORM_TYPE)
    assert answer.status == 500
    assert list(front.spool.iterdir()) == []
    front.upstream.raises = None

    front.upstream.hold = asyncio.Event()  # cancelled while upstream answers, as at a shutdown
    task = asyncio.create_task(call(front.app, "POST", "/v1/audio/transcriptions", body=form, content_type=FORM_TYPE))
    with anyio.fail_after(BOUND_S):
        await until(lambda: len(front.upstream.seen) == 2)
    assert len(list(front.spool.iterdir())) == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert list(front.spool.iterdir()) == []
    assert front.lines[-1] == "front: dan-mac whisper-large-v3-turbo gone 0.0s"
    assert front.app.caps.open("dan-mac") == 0


@pytest.mark.anyio
async def test_an_upload_refused_after_it_is_read_leaves_no_spool_file(front):
    # model_not_found comes once the form is read: its spooled file goes too.
    form = form_body([field("model", "nonesuch"), file_part(b"q" * (2 * MIB))])
    answer = await call(front.app, "POST", "/v1/audio/transcriptions", body=form, content_type=FORM_TYPE)
    assert answer.status == 404
    assert list(front.spool.iterdir()) == []


@pytest.mark.anyio
async def test_a_body_over_200_mib_is_refused(front):
    head = form_body([field("model", "stt")])[:-len(f"--{BOUNDARY}--\r\n")]
    head += f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="file"; filename="a.wav"\r\n\r\n'.encode()
    answer = await call(front.app, "POST", "/v1/audio/transcriptions", chunks=[head], content_type=FORM_TYPE,
                        length=200 * MIB + 1)
    assert (answer.status, answer.json()["error"]["code"]) == (413, "request_too_large")
    assert answer.json()["error"]["message"] == parse.too_big(200 * MIB).message
    assert not answer.received  # refused by its Content-Length, before reading

    seen_files = []
    stream = itertools.chain([head], (b"z" * MIB for _ in range(201)))  # chunked: no Content-Length

    def chunks():
        for chunk in stream:
            seen_files.append(len(list(front.spool.iterdir())))
            yield chunk

    answer = await call(front.app, "POST", "/v1/audio/transcriptions", chunks=chunks(), content_type=FORM_TYPE,
                        length=False)
    assert (answer.status, answer.json()["error"]["code"]) == (413, "request_too_large")
    assert answer.json()["error"]["message"] == parse.too_big(200 * MIB, part_way=True).message
    assert max(seen_files) == 1  # it was spooled as it came
    assert list(front.spool.iterdir()) == []  # and its file is gone
    assert front.upstream.seen == []


@pytest.mark.anyio
async def test_a_json_body_over_16_mib_is_refused(front):
    answer = await call(front.app, "POST", "/v1/chat/completions", chunks=[b"{"], length=16 * MIB + 1)
    assert (answer.status, answer.json()["error"]["message"]) == (413, parse.too_big(16 * MIB).message)
    assert not answer.received

    over = [b'{"model":"coder","x":"'] + [b"a" * MIB] * 16 + [b'"}']
    answer = await call(front.app, "POST", "/v1/chat/completions", chunks=over, length=False)
    assert answer.status == 413

    fits = b'{"model":"coder","x":"' + b"a" * (15 * MIB) + b'"}'
    assert (await call(front.app, "POST", "/v1/chat/completions", body=fits)).status == 200
    assert front.upstream.seen[0].body == fits
    # The upload's cap is the form's alone: a JSON route never takes 200 MiB.
    answer = await call(front.app, "POST", "/v1/embeddings", chunks=[b"{"], length=100 * MIB)
    assert answer.status == 413


@pytest.mark.anyio
async def test_an_unparseable_request_gets_400_never_a_crash(front, caplog):
    no_boundary = (b"multipart/form-data", form_body([field("model", "stt")]))
    for path, content_type, body in [("/v1/chat/completions", b"application/json", b"{"),
                                     ("/v1/chat/completions", b"application/json", b"[]"),
                                     ("/v1/chat/completions", b"application/json", b'{"x": 1}'),
                                     ("/v1/audio/transcriptions", *no_boundary)]:
        answer = await call(front.app, "POST", path, body=body, content_type=content_type)
        assert answer.status == 400, body
        assert answer.json()["error"]["code"] == "invalid_request"
        assert set(answer.json()) == {"error"} and set(answer.json()["error"]) == {"message", "code"}
        assert (await call(front.app, "POST", "/v1/chat/completions", body=chat())).status == 200  # the next served
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


# The front's own refusals.

DAN_MODEL_NOT_FOUND = (
    "There's no model called qwen3.6-35b-a3b here. The models are the coder (qwen3.8-27b), Gemma (gemma-4-26b-a4b), "
    "the embeddings (qwen3-embedding-0.6b) and whisper (whisper-large-v3-turbo). On the Mac, `make clients` updates "
    "pi's list.")
AGENT_MODEL_NOT_FOUND = (
    "There's no model called qwen3.6-35b-a3b here. The models are the coder (qwen3.8-27b), Gemma (gemma-4-26b-a4b), "
    "the embeddings (qwen3-embedding-0.6b) and whisper (whisper-large-v3-turbo). On the Spark, as `agent`, "
    "`/opt/local-ai/app/.venv/bin/spark clients pi --write --registry /opt/local-ai/etc/models.yaml` updates pi's "
    "list.")


@pytest.mark.anyio
async def test_model_not_found_names_the_current_models(front):
    for key, words in (("k1", DAN_MODEL_NOT_FOUND), ("k2", AGENT_MODEL_NOT_FOUND)):
        answer = await call(front.app, "POST", "/v1/chat/completions", key=key, body=chat("qwen3.6-35b-a3b"))
        assert answer.status == 404
        assert answer.json() == {"error": {"message": words, "code": "model_not_found"}}
        assert "x-should-retry" not in answer.headers and "retry-after" not in answer.headers
    # The web UI's key isn't a pi's: no step to update a list.
    answer = await call(front.app, "POST", "/v1/chat/completions", key="k3", body=chat("qwen3.6-35b-a3b"))
    assert answer.json()["error"]["message"] == DAN_MODEL_NOT_FOUND.removesuffix(
        " On the Mac, `make clients` updates pi's list.")
    # An empty name reads as no name, never an error.
    answer = await call(front.app, "POST", "/v1/chat/completions", body=chat(""))
    assert answer.status == 404
    assert answer.json()["error"]["message"].startswith("There's no model by that name here. The models are the coder")
    # The current list: a model the gate's list drops is no longer served, and is not listed.
    front.app.models.replace([entry for entry in MODELS if entry["name"] != EMBED])
    answer = await call(front.app, "POST", "/v1/embeddings", body=chat("embed"))
    assert answer.status == 404 and "embeddings" not in answer.json()["error"]["message"]
    assert front.upstream.seen == [] and front.gate.admits == []


@pytest.mark.anyio
async def test_the_fronts_own_refusals_are_in_the_asking_keys_words(front):
    # Every refusal the front builds goes through app.refusal, the asking key's label and its group's words with it:
    # model_not_found here, and draining, which Task 22's GateLink gives at a draining model's deadline.
    for key, words, label in (("dan-mac", "dan", "pi on the Mac"), ("agent", "agent", "agent")):
        group = GROUPS[KEYS[key].group]
        for code, fields in (("draining", {"model_label": "the coder", "inflight": 1, "drain_for": "make-room"}),
                             ("too_many_requests", {})):
            expected = messages.refusal(code, messages.Moment(words=words, key_label=label, wait_s=group.wait_s,
                                                              names_processes=group.names_processes, **fields))
            assert front.app.refusal(code, key, **fields) == expected
    assert "Dan runs" in front.app.refusal("draining", "agent", model_label="the coder", inflight=1,
                                           drain_for="make-room").message


@pytest.mark.anyio
async def test_a_model_not_loaded_waits_for_the_gate_with_its_keys_deadline(front):
    front.gate.ready = {GEMMA}
    await call(front.app, "POST", "/v1/chat/completions", key="k1", body=chat("coder"))
    await call(front.app, "POST", "/v1/chat/completions", key="k2", body=chat("coder"))
    await call(front.app, "POST", "/v1/chat/completions", key="k2", body=chat("small"))  # loaded: no admit
    assert [admit[:3] for admit in front.gate.admits] == [(CODER, "dan-mac", 1030.0), (CODER, "agent", 1600.0)]
    assert len({admit[3] for admit in front.gate.admits}) == 2  # a request id each
    # The gate's refusal reaches the client as the gate words it, with its status and headers.
    front.gate.answer = {"ok": False, "code": "no_fit", "status": 409, "message": "M", "retry_after_s": 30}
    answer = await call(front.app, "POST", "/v1/chat/completions", body=chat("coder"))
    assert (answer.status, answer.json()) == (409, {"error": {"message": "M", "code": "no_fit"}, "retry_after_s": 30})
    assert answer.headers["x-should-retry"] == "false" and answer.headers["retry-after"] == "30"
    assert len(front.upstream.seen) == 3


@pytest.mark.anyio
async def test_too_many_requests_when_a_keys_caps_are_reached(front):
    agent = KEYS["agent"]
    too_many = refusal_body("too_many_requests", key_label=agent.label, words="agent")
    front.gate.ready = {GEMMA}
    front.gate.hold = asyncio.Event()
    answers = []
    async with anyio.create_task_group() as tg:
        for _ in range(4):
            tg.start_soon(lambda: _keep(answers, call(front.app, "POST", "/v1/chat/completions", key="k2",
                                                      body=chat("coder"))))
        await until(lambda: len(front.gate.admits) == 4)
        fifth = await call(front.app, "POST", "/v1/chat/completions", key="k2", body=chat("coder"))
        assert (fifth.status, fifth.json()) == (429, too_many)
        assert fifth.headers["retry-after"] == "10" and "x-should-retry" not in fifth.headers
        # Another key's room is its own.
        assert (await call(front.app, "POST", "/v1/chat/completions", key="k1", body=chat("small"))).status == 200
        front.gate.hold.set()
    assert [answer.status for answer in answers] == [200] * 4
    assert front.app.caps.waiting("agent") == 0 and front.app.caps.open("agent") == 0

    front.upstream.hold = asyncio.Event()
    answers.clear()
    async with anyio.create_task_group() as tg:
        for _ in range(32):
            tg.start_soon(lambda: _keep(answers, call(front.app, "POST", "/v1/chat/completions", key="k1",
                                                      body=chat("small"))))
        await until(lambda: front.app.caps.open("dan-mac") == 32)
        thirty_third = await call(front.app, "POST", "/v1/chat/completions", key="k1", body=chat("small"))
        assert thirty_third.status == 429
        assert not thirty_third.received  # refused before its body was read
        front.upstream.hold.set()
    assert [answer.status for answer in answers] == [200] * 32
    assert front.app.caps.open("dan-mac") == 0


async def _keep(answers: list, request) -> None:
    answers.append(await request)


# The body budget (the controller's ruling, at Task 20's review).

@pytest.mark.anyio
async def test_a_body_past_the_fronts_budget_is_refused_busy(tmp_path):
    front = make_front(tmp_path, budget=MIB)
    busy = {"error": {"message": "The front is busy holding other large requests; try again in a moment.",
                      "code": "front_busy"}, "retry_after_s": 10}
    # By its Content-Length, before any of it is read.
    answer = await call(front.app, "POST", "/v1/chat/completions", chunks=[b"{"], length=2 * MIB)
    assert (answer.status, answer.json()) == (429, busy)
    assert answer.headers["retry-after"] == "10" and "x-should-retry" not in answer.headers
    assert not answer.received
    # Chunked, as it comes.
    big = b'{"model":"coder","x":"' + b"a" * (2 * MIB) + b'"}'
    answer = await call(front.app, "POST", "/v1/chat/completions", chunks=[big[i:i + 65536] for i in
                                                                           range(0, len(big), 65536)], length=False)
    assert (answer.status, answer.json()) == (429, busy)
    assert front.upstream.seen == [] and front.app.budget.held == 0
    assert front.lines == ["front: dan-mac - 429 0.0s"] * 2
    # A form reserves what it may hold in memory; past the budget, it is refused before it is read.
    tight = make_front(tmp_path, budget=parse.FORM_IN_MEMORY - 1)
    answer = await call(tight.app, "POST", "/v1/audio/transcriptions", body=form_body([field("model", "stt")]),
                        content_type=FORM_TYPE)
    assert (answer.status, answer.received) == (429, False)
    assert messages.pi_retry_match(busy["error"]["message"]) is None


@pytest.mark.anyio
async def test_the_budget_is_shared_by_every_request_and_released_however_each_ends(tmp_path):
    front = make_front(tmp_path, budget=4 * MIB)
    front.upstream.hold = asyncio.Event()
    body = b'{"model":"coder","x":"' + b"a" * (3 * MIB) + b'"}'
    first = asyncio.create_task(call(front.app, "POST", "/v1/chat/completions", body=body))
    with anyio.fail_after(BOUND_S):
        await until(lambda: len(front.upstream.seen) == 1)
    assert front.app.budget.held == len(front.upstream.seen[0].body)  # held while it waits on its answer
    # Another key's body that doesn't fit beside it is refused busy.
    answer = await call(front.app, "POST", "/v1/chat/completions", key="k2", body=body)
    assert (answer.status, answer.json()["error"]["code"]) == (429, "front_busy")
    front.upstream.hold.set()
    assert (await first).status == 200
    assert front.app.budget.held == 0

    # Refused after it is read, cancelled while it waits on its answer, or a form: each gives its bytes back.
    assert (await call(front.app, "POST", "/v1/chat/completions", body=chat("nonesuch"))).status == 404
    assert front.app.budget.held == 0
    front.upstream.hold = asyncio.Event()
    for request in (call(front.app, "POST", "/v1/chat/completions", body=body),
                    call(front.app, "POST", "/v1/audio/transcriptions", content_type=FORM_TYPE,
                         body=form_body([field("model", "stt"), file_part(b"RIFF")]))):
        task = asyncio.create_task(request)
        seen = len(front.upstream.seen)
        with anyio.fail_after(BOUND_S):
            await until(lambda: len(front.upstream.seen) == seen + 1)
        assert front.app.budget.held > 0
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert front.app.budget.held == 0
    assert front.lines[-2:] == ["front: dan-mac qwen3.8-27b gone 0.0s",
                                "front: dan-mac whisper-large-v3-turbo gone 0.0s"]


# The journal.

@pytest.mark.anyio
async def test_journal_lines_carry_key_name_model_status_and_duration_only(front, caplog):
    caplog.set_level(logging.DEBUG)
    front.upstream.during = lambda seen: front.now.__setitem__(0, front.now[0] + 1.2)
    marker_headers = [(b"x-marker", b"header-marker"), (b"user-agent", b"agent-marker")]
    await call(front.app, "POST", "/v1/chat/completions", key="k2", body=chat("coder", body="body-marker"),
               headers=marker_headers)
    assert front.lines == ["front: agent qwen3.8-27b 200 1.2s"]
    form = form_body([field("model", "stt"), field("prompt", "field-marker"), file_part(b"x", filename="name.wav")])
    await call(front.app, "POST", "/v1/audio/transcriptions", key="k1", body=form, content_type=FORM_TYPE,
               headers=marker_headers)
    assert front.lines[1] == "front: dan-mac whisper-large-v3-turbo 200 1.2s"
    logged = "\n".join(front.lines) + caplog.text
    for marker in ("header-marker", "agent-marker", "body-marker", "field-marker", "name.wav", "k1", "k2",
                   "Bearer"):
        assert marker not in logged


@pytest.mark.anyio
async def test_an_error_of_the_fronts_own_is_a_500_in_words_never_a_traceback(front, caplog):
    front.upstream.raises = RuntimeError("raised-marker k1")
    for key, words in (("k1", front_app.BROKE["dan"]), ("k2", front_app.BROKE["agent"])):
        answer = await call(front.app, "POST", "/v1/chat/completions", key=key, body=chat())
        assert (answer.status, answer.json()) == (500, {"error": {"message": words, "code": "front_error"}})
        assert answer.headers["x-should-retry"] == "false"  # OpenAI's SDKs retry a 5xx unless told
    assert "raised-marker" not in caplog.text and "Traceback" not in caplog.text
    [record, _] = [r for r in caplog.records if r.name == "spark.front.app"]
    assert record.getMessage().startswith("the front's POST /v1/chat/completions broke: RuntimeError at ")
    assert front.lines[0] == "front: dan-mac qwen3.8-27b 500 0.0s"
    caplog.clear()
    front.upstream.raises = None
    assert (await call(front.app, "POST", "/v1/chat/completions", body=chat())).status == 200  # it serves on
    for words in front_app.BROKE.values():
        assert messages.pi_retry_match(json.dumps({"message": words, "code": "front_error"})) is None


@pytest.mark.anyio
async def test_a_client_gone_while_its_body_comes_is_logged_gone(front):
    scope_chunks = [b'{"model":']

    async def receive_then_go():
        if scope_chunks:
            return {"type": "http.request", "body": scope_chunks.pop(), "more_body": True}
        return {"type": "http.disconnect"}

    sent = []

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": "/v1/chat/completions", "query_string": b"",
             "headers": [(b"authorization", b"Bearer k2"), (b"content-type", b"application/json")]}
    with anyio.fail_after(BOUND_S):
        await front.app(scope, receive_then_go, send)
    assert sent == [] and front.upstream.seen == []
    assert front.lines == ["front: agent - gone 0.0s"]
    assert front.app.caps.open("agent") == 0


# On a real uvicorn.

def test_an_unknown_key_with_a_trickled_body_is_refused_at_once(tmp_path):
    # The key is checked on the headers, before the first receive(): the 401 comes before any of the body is sent,
    # and the body nobody reads then falls under Task 9's deadline, however slowly it trickles in.
    front = make_front(tmp_path)
    with tcp_listener() as listener, \
            serving(front.app, listener, make_protocol(peer_cred=False, header_timeout_s=HEADER_TIMEOUT_S),
                    keep_alive_s=2):
        with connect(listener) as client:
            client.sendall(b"POST /v1/chat/completions HTTP/1.1\r\nHost: spark\r\nAuthorization: Bearer nope\r\n"
                           b"Content-Type: application/json\r\nContent-Length: 1000\r\n\r\n")
            status, body = read_response(client)
            assert status == b"HTTP/1.1 401 Unauthorized"
            assert json.loads(body) == UNKNOWN_KEY_BODY
            took = trickle_until_closed(client, b"x", limit_s=3)
    assert HEADER_TIMEOUT_S - 0.1 <= took <= HEADER_TIMEOUT_S + 0.5
    assert front.lines == ["front: - - 401 0.0s"]

"""The front's forwarding (website/design/phase-2a.md, Task 21): each request goes to llama-swap with the front's own
internal key, over a connection of its key's own, and its answer comes back chunk by chunk, with no read timeout; every
request is counted in flight from its forwarding until its response ends, however it ends; a stream cut upstream
reaches the client as a cut, never as a finish, with nothing injected; llama-swap's 429 comes back in the front's
words; the uploads forwarded at once are capped; and `spark front` itself, as systemd starts it.

The front runs on a real uvicorn, with Task 9's protocol, in the test's own event loop, against Task 12's v257 stand-in
(fake_llamaswap.py), and a client reaches it with httpx, as pi's SDK would. Every key, digest and name here is a
stand-in; none was read from the box."""

import asyncio
import contextlib
import dataclasses
import gc
import json
import logging
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

import anyio
import httpx
import pytest

import spark.front
from fake_llamaswap import FakeLlamaSwap, serve_fake
from front_helpers import DIGESTS, GROUPS, KEYS, KEYS_TEXT, MODELS, field, file_part, form_body
from gate_standin import GateStandIn
from spark import messages, serve
from spark.front import app as front_app
from spark.front import gatelink, parse, upstream, usage
from spark.protocols import make_protocol
from spark.rowfile import RefusedLimit
from test_protocols import LINUX_ONLY
from test_sockets import LAUNCHER

BOUND_S = 20
INTERNAL = "internal-key-standin"  # the front's own key for llama-swap, a stand-in
NAMES = [entry["name"] for entry in MODELS]
CODER, GEMMA, EMBED, WHISPER = "qwen3.8-27b", "gemma-4-26b-a4b", "qwen3-embedding-0.6b", "whisper-large-v3-turbo"
T = 1000.0
ROOT = Path(__file__).resolve().parents[2]
STACK_REGISTRY = ROOT / "stack" / "models.yaml"
CLIENT_KEYS = {"dan-mac": "k1", "agent": "k2", "open-webui": "k3"}  # front_helpers.DIGESTS' stand-in keys


@pytest.fixture
def anyio_backend():
    return "asyncio"


def sse(data: str, event: str | None = None) -> bytes:
    return (f"event: {event}\n" if event else "").encode() + f"data: {data}\n\n".encode()


DELTA = sse('{"choices":[{"delta":{"content":"ok"}}]}')
DONE = b"data: [DONE]\n\n"
PING = b":\n\n"  # llama-server's SSE ping (server-context.cpp, b11146)
OAI_ERROR = sse('{"error":{"code":500,"message":"an engine stand-in error","type":"server_error"}}')
# Each streamed route: a chunk of its own, its last event, and the engine's error event, as llama.cpp b11146 writes
# them (tools/server/server-context.cpp's stream loop and format_error, server-task.cpp's last events,
# server-common.cpp's format_oai_sse, format_oai_resp_sse and format_anthropic_sse).
STREAMS = {
    "/v1/chat/completions": (DELTA, DONE, OAI_ERROR),
    "/v1/completions": (sse('{"choices":[{"text":"ok"}]}'), DONE, OAI_ERROR),
    "/v1/messages": (sse('{"type":"content_block_delta"}', "content_block_delta"),
                     sse('{"type":"message_stop"}', "message_stop"),
                     sse('{"code":500,"message":"an engine stand-in error","type":"server_error"}', "error")),
    "/v1/responses": (sse('{"type":"response.output_text.delta"}', "response.output_text.delta"),
                      sse('{"type":"response.completed"}', "response.completed"), OAI_ERROR),
}


def chat(model: str = "qwen3.8-27b", **more) -> dict:
    """A request's body, naming its model by name: the v257 stand-in has no aliases, where llama-swap resolves the
    roles render gives it."""
    return {"model": model, "messages": [{"role": "user", "content": "hi"}], "stream": True, **more}


def auth(key: str = "k1") -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


async def until(condition, bound_s: float = BOUND_S) -> None:
    with anyio.fail_after(bound_s):
        while not condition():
            await asyncio.sleep(0.005)


class Loaded:
    """The gate's stand-in, as the app calls Task 22's GateLink: every model loaded, so each request is cleared at once,
    counted as it is (`enter`), and nothing is admitted. A resend, which goes through admission, is refused with
    `refusal`: the gate's llama_swap_down, by default. Every model runs, by the model list."""

    def __init__(self, refusal: dict | None = None):
        self.refusal = refusal or {"ok": False, "code": "llama_swap_down", "status": 409, "message": "M",
                                   "retry_after_s": 30}
        self.admits = 0

    async def clear(self, model, key, deadline, request_id, *, enter, admit=False, waits=lambda: True,
                    loaded=None):
        if not admit:
            return enter()
        self.admits += 1
        return gatelink.Refused(self.refusal["code"], answer=self.refusal)

    def refused(self, code, model, key, message):
        pass

    async def why_cut(self, model, since):
        return None

    def running_models(self):
        return frozenset(NAMES)


class Rows:
    """The row file's stand-in, as the app calls rowfile.RowFile: each row put kept in `rows`; with `raises`, each put
    raises it instead."""

    def __init__(self, raises: Exception | None = None):
        self.rows: list[dict] = []
        self.raises = raises

    def put(self, row, at):
        if self.raises is not None:
            raise self.raises
        self.rows.append(row)


class Tasks:
    """The front's app, wrapped to keep each request's task, as uvicorn runs it, so a test can cancel one."""

    def __init__(self, app):
        self.app = app
        self.tasks: list[asyncio.Task] = []

    async def __call__(self, scope, receive, send):
        self.tasks.append(asyncio.current_task())
        await self.app(scope, receive, send)


@contextlib.asynccontextmanager
async def serve_app(app):
    """`app` on a real uvicorn at 127.0.0.1, a port of its own, with Task 9's protocol and the services' settings
    (serve's), in the running event loop; its URL."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    config = serve._config(app, make_protocol(peer_cred=False, header_timeout_s=10), graceful_s=1)
    server = serve.QuietServer(config)
    task = asyncio.create_task(server.serve(sockets=[listener]))
    with anyio.fail_after(BOUND_S):
        while not server.started:
            if task.done():
                task.result()
            await asyncio.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{listener.getsockname()[1]}"
    finally:
        server.should_exit = True
        await task
        listener.close()


@dataclasses.dataclass
class Rig:
    app: front_app.FrontApp
    tasks: Tasks
    fake: FakeLlamaSwap | None
    swap_url: str
    url: str
    lines: list[str]
    now: list[float]
    client: httpx.AsyncClient
    rows: Rows
    gate: object

    def count(self, model: str = CODER) -> int:
        return self.app.inflight.count(model)

    def stream(self, path: str = "/v1/chat/completions", body: dict | None = None, key: str = "k1"):
        return self.client.stream("POST", path, json=chat() if body is None else body, headers=auth(key))


@contextlib.asynccontextmanager
async def rig(tmp_path, *, fake: FakeLlamaSwap | None = None, swap_url: str | None = None, gate=None,
              rows: Rows | None = None, **app_options):
    """The front on a real uvicorn, its Upstream reaching `swap_url`, or the v257 stand-in `fake` (one with every model
    ready, by default), its gate `gate` (Loaded, by default) and its row file `rows` (a Rows, by default), and an httpx
    client for it with a read timeout well past any silence a test makes."""
    gate = Loaded() if gate is None else gate
    rows = Rows() if rows is None else rows
    if swap_url is None:
        fake = fake or FakeLlamaSwap({INTERNAL}, NAMES)
        for name in NAMES:
            fake.set_state(name, "ready")
        context = serve_fake(fake)
    else:
        context = contextlib.nullcontext(swap_url)
    spool = tmp_path / "spool"
    spool.mkdir(exist_ok=True)
    lines, now = [], [T]
    async with context as url_of_swap:
        forwarder = upstream.Upstream(url_of_swap, INTERNAL)
        app = front_app.FrontApp(parse.ModelList(MODELS), KEYS, GROUPS, DIGESTS, upstream=forwarder, gate=gate,
                                 caps=parse.KeyCaps(KEYS, GROUPS), log=lines.append, clock=lambda: now[0],
                                 spool_dir=spool, rows=rows, **app_options)
        tasks = Tasks(app)
        try:
            async with serve_app(tasks) as url, httpx.AsyncClient(base_url=url, trust_env=False,
                                                                  timeout=httpx.Timeout(15)) as client:
                yield Rig(app, tasks, fake, url_of_swap, url, lines, now, client, rows, gate)
        finally:
            await forwarder.aclose()


def nothing_listening() -> str:
    """A URL on 127.0.0.1 where nothing listens: a port the system gave this test, then closed. Never one of the
    stack's own (900, 9100, 800 and up, 5800 and up, 3000, 8888), so no test can reach the live llama-swap."""
    with socket.socket() as free:
        free.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{free.getsockname()[1]}"


@contextlib.asynccontextmanager
async def front_with(tmp_path, forwarder, *, gate=None, **app_options):
    """The front on a real uvicorn, its upstream the stand-in `forwarder`, called as the app calls Upstream; yields
    (the app, its request tasks, an httpx client for it, its journal lines)."""
    lines: list[str] = []
    app = front_app.FrontApp(parse.ModelList(MODELS), KEYS, GROUPS, DIGESTS, upstream=forwarder,
                             gate=Loaded() if gate is None else gate, caps=parse.KeyCaps(KEYS, GROUPS),
                             log=lines.append, clock=lambda: T, spool_dir=tmp_path, **app_options)
    tasks = Tasks(app)
    async with serve_app(tasks) as url, httpx.AsyncClient(base_url=url, trust_env=False,
                                                          timeout=httpx.Timeout(15)) as client:
        yield app, tasks, client, lines


class Scripted:
    """llama-swap's stand-in for the app as Task 20's tests call it: a 200 event stream whose first chunk comes at once
    and then, 0.05 s later, as `then` says: "break", the body broken off (upstream.Broken), or "wait", nothing more,
    ever. Its close takes `close_s`, then raises if `close_raises`, else counts itself in `closed`. `reached` is set
    when it gets to `then`."""

    def __init__(self, then: str = "wait", *, close_s: float = 0.0, close_raises: bool = False):
        self.then, self.close_s, self.close_raises = then, close_s, close_raises
        self.reached = asyncio.Event()
        self.closed = 0

    async def forward(self, method, path, headers, body, key):
        if body is not None and not isinstance(body, bytes):
            async for _ in body:  # sent, as httpx sends it
                pass
        script = self

        class Response:
            status = 200
            headers = [(b"content-type", b"text/event-stream")]

            async def aiter_raw(self):
                yield DELTA
                await asyncio.sleep(0.05)
                script.reached.set()
                if script.then == "break":
                    raise upstream.Broken("llama-swap's answer broke off before its end")
                await asyncio.Event().wait()

            async def aclose(self):
                await asyncio.sleep(script.close_s)
                if script.close_raises:
                    raise RuntimeError("close-marker")
                script.closed += 1

        return Response()


async def first_chunk(response: httpx.Response) -> tuple[bytes, object]:
    """The answer's first chunk, and the iterator for the rest. Keep the iterator while the answer should stay open:
    one dropped is finalized at once, and httpx then closes the response."""
    chunks = response.aiter_raw()
    return await anext(chunks), chunks


# Forwarding.

@pytest.mark.anyio
async def test_a_stream_is_forwarded_chunk_by_chunk(tmp_path):
    async with rig(tmp_path) as r:
        chunks = [sse(f'{{"choices":[{{"delta":{{"content":"{n}"}}}}]}}') for n in range(4)] + [DONE]
        r.fake.stream(CODER, chunks, delay_s=0.1)
        with anyio.fail_after(BOUND_S):
            async with r.stream() as response:
                assert response.status_code == 200
                assert response.headers["content-type"] == "text/event-stream"
                received, rest = await first_chunk(response)
                got_first = time.monotonic()
                async for chunk in rest:
                    received += chunk
                ended = time.monotonic()
        # The last chunk was sent 0.4 s after the first: the client had the first well before it.
        assert ended - got_first >= 0.3
        assert received == b"".join(chunks)
        await until(lambda: r.lines == [f"front: dan-mac {CODER} 200 0.0s"])


@pytest.mark.anyio
async def test_the_count_goes_down_when_a_response_ends_a_client_goes_or_upstream_fails(tmp_path):
    async with rig(tmp_path) as r:
        with anyio.fail_after(BOUND_S):
            # A full response.
            r.fake.stream(CODER, [DELTA, DELTA, DONE], delay_s=0.2)
            async with r.stream() as response:
                _, rest = await first_chunk(response)
                assert r.count() == 1
                async for _ in rest:
                    pass
            await until(lambda: r.count() == 0)

            # The client closing mid-stream.
            r.fake.stream(CODER, [DELTA, DELTA, DONE], delay_s=5)
            async with r.stream() as response:
                await first_chunk(response)
                assert r.count() == 1
            await until(lambda: r.count() == 0)

            # The stand-in dropping the connection mid-stream.
            r.fake.stream(CODER, [DELTA, DELTA, DONE], delay_s=0.2, drop_after=2)
            with pytest.raises(httpx.RemoteProtocolError):
                async with r.stream() as response:
                    _, rest = await first_chunk(response)
                    assert r.count() == 1
                    async for _ in rest:
                        pass
            await until(lambda: r.count() == 0)

            # The handler's task cancelled, as uvicorn's shutdown cancels what outlasts its graceful time.
            r.fake.stream(CODER, [DELTA, DELTA, DONE], delay_s=5)
            with pytest.raises(httpx.RemoteProtocolError):
                async with r.stream() as response:
                    _, rest = await first_chunk(response)
                    assert r.count() == 1
                    r.tasks.tasks[-1].cancel()
                    async for _ in rest:
                        pass
            await until(lambda: r.count() == 0)
        await until(lambda: len(r.lines) == 4)
        assert r.lines == [f"front: dan-mac {CODER} {end} 0.0s" for end in (200, "gone", "cut", "cut")]
        assert r.app.caps.open("dan-mac") == 0


@pytest.mark.anyio
@pytest.mark.parametrize("path", list(STREAMS))
async def test_a_stream_cut_upstream_reaches_the_client_as_a_cut(tmp_path, path):
    chunk, last, error = STREAMS[path]
    async with rig(tmp_path) as r:
        with anyio.fail_after(BOUND_S):
            # The stand-in dropping the connection mid-stream.
            sent = [chunk, chunk, chunk, last]
            r.fake.stream(CODER, sent, delay_s=0.05, drop_after=2)
            received = b""
            with pytest.raises(httpx.RemoteProtocolError):
                async with r.stream(path) as response:
                    async for each in response.aiter_raw():
                        received += each
            assert received == chunk * 2  # what came, and nothing injected
            await until(lambda: len(r.lines) == 1)

            # An unload mid-stream: v257 ends the response cleanly, only early, without the route's last event.
            r.fake.stream(CODER, sent, delay_s=0.3)
            received = b""
            with pytest.raises(httpx.RemoteProtocolError):
                async with r.stream(path) as response:
                    received, rest = await first_chunk(response)
                    unloaded = await r.client.post(f"{r.swap_url}/api/models/unload/{CODER}",
                                                   headers=auth(INTERNAL))
                    assert unloaded.text == "OK"
                    async for each in rest:
                        received += each
            assert received in {b"".join(sent[:n]) for n in range(1, len(sent))}
            await until(lambda: len(r.lines) == 2)
            r.fake.set_state(CODER, "ready")

            # A full stream, its last event sent, ends cleanly; and so does one whose last event is the engine's own
            # error, pings before it.
            r.fake.stream(CODER, [chunk, last])
            async with r.stream(path) as response:
                assert await response.aread() == chunk + last
            r.fake.stream(CODER, [chunk, PING, PING, error])
            async with r.stream(path) as response:
                assert await response.aread() == chunk + PING * 2 + error
            await until(lambda: len(r.lines) == 4)
        assert r.lines == [f"front: dan-mac {CODER} {end} 0.0s" for end in ("cut", "cut", 200, "error")]
        assert r.count() == 0


@pytest.mark.anyio
async def test_a_client_gone_mid_stream_cancels_the_upstream_call(tmp_path):
    async with rig(tmp_path) as r:
        r.fake.stream(CODER, [DELTA, DELTA, DONE], delay_s=30)
        with anyio.fail_after(BOUND_S):
            async with r.stream() as response:
                await first_chunk(response)
                assert r.fake.reserved(CODER) == 1
            gone = time.monotonic()
            await until(lambda: r.fake.reserved(CODER) == 0, bound_s=1)
        assert time.monotonic() - gone < 1
        await until(lambda: r.lines == [f"front: dan-mac {CODER} gone 0.0s"])


@pytest.mark.anyio
async def test_a_silent_long_prefill_is_never_timed_out(tmp_path):
    async with rig(tmp_path) as r:
        with anyio.fail_after(BOUND_S):
            # Its headers, then nothing for 3 s, then a chunk (an empty body message sends nothing on the wire).
            r.fake.stream(CODER, [b"", DELTA + DONE], delay_s=3)
            async with r.stream() as response:
                started = time.monotonic()
                assert await response.aread() == DELTA + DONE
            assert time.monotonic() - started >= 2.9
            # Nothing at all for 3 s, its headers included, as llama-server sends none until its prompt is read.
            r.fake.set_state(CODER, "stopped")
            r.fake.script_start(CODER, 3.0)
            r.fake.stream(CODER, [DELTA, DONE])
            started = time.monotonic()
            async with r.stream() as response:
                assert await response.aread() == DELTA + DONE
            assert time.monotonic() - started >= 2.9
        # No read timeout at all, on any key's connection.
        for key in KEYS:
            assert r.app.upstream.client(key).timeout == httpx.Timeout(connect=5, read=None, write=60, pool=5)


@pytest.mark.anyio
async def test_upstream_gets_only_the_internal_key(tmp_path):
    async with rig(tmp_path) as r:
        r.fake.stream(GEMMA, [DELTA, DONE])
        with anyio.fail_after(BOUND_S):
            for headers in (auth("k1"), {"x-api-key": "k2"}, {**auth("k3"), "x-api-key": "k3", "Cookie": "c=k3",
                                                              "Proxy-Authorization": "Basic k3",
                                                              "Accept-Encoding": "gzip"}):
                answer = await r.client.post("/v1/chat/completions", json=chat(GEMMA), headers=headers)
                assert answer.status_code == 200
        assert len(r.fake.requests) == 3
        for seen in r.fake.requests:
            assert seen.headers["authorization"] == f"Bearer {INTERNAL}"
            assert seen.headers["accept-encoding"] == "identity"  # the body as it is, so the front reads its events
            assert not {"x-api-key", "cookie", "proxy-authorization"} & set(seen.headers)
            assert not any(key in value for value in seen.headers.values() for key in CLIENT_KEYS.values())


class Ported(FakeLlamaSwap):
    """The v257 stand-in, keeping the client port each request came from."""

    def __init__(self, *args):
        super().__init__(*args)
        self.ports: list[int] = []

    async def __call__(self, scope, receive, send):
        self.ports.append(scope["client"][1])
        await super().__call__(scope, receive, send)


@pytest.mark.anyio
async def test_no_upstream_connection_is_shared_between_keys(tmp_path):
    async with rig(tmp_path, fake=Ported({INTERNAL}, NAMES)) as r:
        r.fake.stream(GEMMA, [DELTA, DONE])
        with anyio.fail_after(BOUND_S):
            for key in ("k1", "k2", "k1", "k2"):  # one after another, so a shared pool would reuse one connection
                answer = await r.client.post("/v1/chat/completions", json=chat(GEMMA), headers=auth(key))
                assert answer.status_code == 200
        dan, agent, dan_again, agent_again = r.fake.ports
        assert dan != agent
        assert (dan_again, agent_again) == (dan, agent)  # each key's own connection, kept alive


@pytest.mark.anyio
async def test_llama_swaps_429_is_passed_on_worded_with_its_retry_after(tmp_path):
    async with rig(tmp_path) as r:
        r.fake.stream(CODER, [DELTA, DONE], delay_s=30)
        with anyio.fail_after(BOUND_S):
            async with contextlib.AsyncExitStack() as held:
                kept = []
                for _ in range(10):  # llama-swap's limit: 10 a model, waiting or served
                    response = await held.enter_async_context(r.stream())
                    kept.append(await first_chunk(response))
                assert r.fake.reserved(CODER) == 10 and r.count() == 10
                eleventh = await r.client.post("/v1/chat/completions", json=chat(), headers=auth())
            await until(lambda: r.count() == 0)
        assert eleventh.status_code == 429
        assert eleventh.headers["retry-after"] == "1"
        assert "x-should-retry" not in eleventh.headers
        worded = "Too many requests for the coder at once; try again in a moment."
        assert eleventh.json() == {"error": {"message": worded, "code": "concurrency_limit"}, "retry_after_s": 1}
        assert eleventh.json()["error"]["message"] == messages.refusal(
            "concurrency_limit", messages.Moment(model_label="the coder")).message
        await until(lambda: len(r.lines) == 11)
        assert r.lines[0] == f"front: dan-mac {CODER} 429 0.0s"


class Answers:
    """An upstream that answers each request with what `answer` gives it; a 429 that isn't llama-swap's, by default."""

    def __init__(self, status: int, body: bytes, headers: list[tuple[bytes, bytes]]):
        self.status, self.body, self.headers = status, body, headers

    async def __call__(self, scope, receive, send):
        while (await receive()).get("more_body"):
            pass
        await send({"type": "http.response.start", "status": self.status, "headers": self.headers})
        await send({"type": "http.response.body", "body": self.body})


@pytest.mark.anyio
async def test_another_429_goes_on_as_it_came(tmp_path):
    # Only llama-swap's concurrency_limit is worded; any other 429 reaches the client as it is.
    body = b'{"error":{"message":"slow down","code":"something_else"}}'
    async with serve_app(Answers(429, body, [(b"content-type", b"application/json"), (b"retry-after", b"7")])) as url:
        async with rig(tmp_path, swap_url=url) as r:
            answer = await r.client.post("/v1/chat/completions", json=chat(), headers=auth())
            assert (answer.status_code, answer.content, answer.headers["retry-after"]) == (429, body, "7")
            await until(lambda: r.count() == 0)


@pytest.mark.anyio
async def test_a_stream_with_a_length_cut_short_is_still_a_cut(tmp_path):
    # An event stream sent with a Content-Length, ending without its last event: the front sends it on without the
    # length, so the client's framing shows the cut, where the length alone would read as a whole answer.
    headers = [(b"content-type", b"text/event-stream"), (b"content-length", str(len(DELTA)).encode())]
    async with serve_app(Answers(200, DELTA, headers)) as url, rig(tmp_path, swap_url=url) as r:
        received = b""
        with pytest.raises(httpx.RemoteProtocolError):
            async with r.stream() as response:
                assert "content-length" not in response.headers
                async for each in response.aiter_raw():
                    received += each
        assert received == DELTA
        await until(lambda: r.lines == [f"front: dan-mac {CODER} cut 0.0s"])


# Counting.

@pytest.mark.anyio
async def test_each_models_oldest_request_is_tracked(tmp_path):
    async with rig(tmp_path) as r:
        r.fake.stream(CODER, [DELTA, DONE], delay_s=30)
        with anyio.fail_after(BOUND_S):
            async with r.stream() as first:
                kept = [await first_chunk(first)]
                r.now[0] = T + 5
                async with r.stream() as second:
                    kept.append(await first_chunk(second))
                    counts = r.app.inflight.snapshot()["models"][CODER]
                    assert (counts["count"], counts["oldest_started_at"], counts["last_end_at"]) == (2, T, None)
                    await first.aclose()
                    await until(lambda: r.count() == 1)
                    counts = r.app.inflight.snapshot()["models"][CODER]
                    assert (counts["count"], counts["oldest_started_at"], counts["last_end_at"]) == (1, T + 5, T + 5)
            await until(lambda: r.count() == 0)
        counts = r.app.inflight.snapshot()["models"][CODER]
        assert (counts["count"], counts["oldest_started_at"]) == (0, None)


@pytest.mark.anyio
async def test_the_snapshot_names_each_requests_key(tmp_path):
    async with rig(tmp_path) as r:
        r.fake.stream(CODER, [DELTA, DONE], delay_s=30)
        with anyio.fail_after(BOUND_S):
            async with r.stream(key="k1") as dans:
                kept = [await first_chunk(dans)]
                r.now[0] = T + 5
                async with r.stream(key="k2") as agents:
                    kept.append(await first_chunk(agents))
                    snapshot = r.app.inflight.snapshot(models=NAMES)
        assert set(snapshot) == {"seq", "front_started_at", "models", "draining", "holding", "at"}  # Task 32's review
        assert snapshot["front_started_at"] == T and snapshot["draining"] == []
        assert snapshot["models"][CODER]["requests"] == [{"key": "dan-mac", "started_at": T},
                                                         {"key": "agent", "started_at": T + 5}]
        # Every model named, with its count, as the gate reads a whole snapshot, never a delta.
        assert set(snapshot["models"]) == set(NAMES)
        assert snapshot["models"][GEMMA] == {"count": 0, "oldest_started_at": None, "last_end_at": None,
                                             "requests": []}
        assert r.app.inflight.snapshot()["seq"] == snapshot["seq"] + 1


def test_in_flight_counts_each_request_once_however_often_it_is_left():
    now = [T]
    inflight = upstream.InFlight(clock=lambda: now[0])
    first = inflight.enter(CODER, "dan-mac")
    now[0] = T + 1
    second = inflight.enter(CODER, "agent")
    now[0] = T + 2
    inflight.leave(first)
    inflight.leave(first)  # every way out leaves it, and only once counts
    assert inflight.count(CODER) == 1
    assert inflight.snapshot()["models"][CODER] == {"count": 1, "oldest_started_at": T + 1, "last_end_at": T + 2,
                                                    "requests": [{"key": "agent", "started_at": T + 1}]}
    inflight.leave(second)
    assert inflight.count(CODER) == 0 and inflight.count(GEMMA) == 0


# Errors before an answer.

@pytest.mark.anyio
async def test_llama_swap_not_answering_leaves_the_model_list_answered_and_sends_a_model_through_admission(tmp_path):
    # No connection, or one closed before the head. The model list is the front's own answer, from its copy of the
    # gate's list, so it is answered whatever llama-swap does, and nothing goes upstream for it (the controller's
    # ruling, at Task 22's review). A request for a model goes through admission (Task 22), and the gate's answer
    # reaches the client.
    nothing = nothing_listening()
    reached: list[bytes] = []

    async def hang_up(reader, writer):  # takes the request, then closes before any answer: a crash mid-request
        reached.append(await reader.readuntil(b"\r\n\r\n"))
        writer.close()

    server = await asyncio.start_server(hang_up, "127.0.0.1", 0)
    hangs_up = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    try:
        for url in (nothing, hangs_up):
            reached.clear()
            gate = Loaded()
            async with rig(tmp_path, swap_url=url, gate=gate) as r:
                for key in ("k1", "k2"):
                    answer = await r.client.get("/v1/models", headers=auth(key))
                    assert answer.status_code == 200
                    assert [each["id"] for each in answer.json()["data"]] == sorted(NAMES)
                assert reached == []  # nothing upstream for the list
                answer = await r.client.post("/v1/chat/completions", json=chat(), headers=auth())
                refused = {"error": {"message": "M", "code": "llama_swap_down"}, "retry_after_s": 30}
                assert (answer.status_code, answer.json()) == (409, refused)
                await until(lambda: r.count() == 0)
                await until(lambda: len(r.lines) == 3)
                assert r.lines == ["front: dan-mac - 200 0.0s", "front: agent - 200 0.0s",
                                   f"front: dan-mac {CODER} 409 0.0s (llama_swap_down)"]
                assert gate.admits == 1
            assert len(reached) == (0 if url == nothing else 1)  # only the model's request went upstream
    finally:
        server.close()
        await server.wait_closed()


class Raising:
    """llama-swap's stand-in for the app as Task 20's tests call it: its forward raises."""

    async def forward(self, method, path, headers, body, key):
        raise RuntimeError("raised-marker")


@pytest.mark.anyio
async def test_an_error_of_the_fronts_own_while_forwarding_leaves_the_count(tmp_path):
    app = front_app.FrontApp(parse.ModelList(MODELS), KEYS, GROUPS, DIGESTS, upstream=Raising(), gate=Loaded(),
                             caps=parse.KeyCaps(KEYS, GROUPS), log=[].append, spool_dir=tmp_path)
    async with serve_app(app) as url, httpx.AsyncClient(base_url=url, trust_env=False) as client:
        answer = await client.post("/v1/chat/completions", json=chat(), headers=auth())
    assert answer.status_code == 500 and answer.json()["error"]["code"] == "front_error"
    assert app.inflight.count(CODER) == 0


class SlowToClose:
    """llama-swap's stand-in for the app as Task 20's tests call it: a whole answer whose close takes a moment, as a
    connection's return to its pool can. Meanwhile uvicorn tells the app the client is gone, since the answer is
    whole."""

    def __init__(self):
        self.closed = 0

    async def forward(self, method, path, headers, body, key):
        stand_in = self

        class Response:
            status = 200
            headers = [(b"content-type", b"text/event-stream")]

            async def aiter_raw(self):
                yield DELTA + DONE

            async def aclose(self):
                await asyncio.sleep(0.05)
                stand_in.closed += 1

        return Response()


@pytest.mark.anyio
async def test_an_answer_sent_whole_is_never_taken_for_a_client_gone(tmp_path):
    lines: list[str] = []
    forwarder = SlowToClose()
    app = front_app.FrontApp(parse.ModelList(MODELS), KEYS, GROUPS, DIGESTS, upstream=forwarder, gate=Loaded(),
                             caps=parse.KeyCaps(KEYS, GROUPS), log=lines.append, clock=lambda: T,
                             spool_dir=tmp_path)
    async with serve_app(app) as url, httpx.AsyncClient(base_url=url, trust_env=False) as client:
        with anyio.fail_after(BOUND_S):
            answer = await client.post("/v1/chat/completions", json=chat(), headers=auth())
            assert answer.content == DELTA + DONE
            await until(lambda: lines == [f"front: dan-mac {CODER} 200 0.0s"])
    assert forwarder.closed == 1  # its close ran to its end, never cancelled
    assert app.inflight.count(CODER) == 0


@pytest.mark.anyio
async def test_a_redirect_is_passed_on_never_followed(tmp_path):
    fake = FakeLlamaSwap({INTERNAL}, NAMES)
    fake.canned("POST", "/v1/chat/completions", 302, b"", {"location": "http://127.0.0.1:9/elsewhere"})
    async with rig(tmp_path, fake=fake) as r:
        answer = await r.client.post("/v1/chat/completions", json=chat(), headers=auth())
    # Followed, it would have met nothing at port 9, and the client a 502.
    assert (answer.status_code, answer.headers["location"]) == (302, "http://127.0.0.1:9/elsewhere")
    assert [seen.path for seen in fake.requests] == ["/v1/chat/completions"]


@pytest.mark.anyio
async def test_a_proxy_in_the_environment_is_never_used(tmp_path, monkeypatch):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    async with rig(tmp_path) as r:
        r.fake.stream(GEMMA, [DELTA, DONE])
        answer = await r.client.post("/v1/chat/completions", json=chat(GEMMA), headers=auth())
    assert answer.status_code == 200


def test_each_key_has_a_client_of_its_own_and_none_follows_a_redirect():
    forwarder = upstream.Upstream("http://127.0.0.1:9", INTERNAL)  # never sent to: Upstream is lazy
    dan = forwarder.client("dan-mac")
    assert forwarder.client("dan-mac") is dan and forwarder.client("agent") is not dan
    assert dan.follow_redirects is False and dan.trust_env is False
    assert dan.timeout == httpx.Timeout(connect=5, read=None, write=60, pool=5)


def test_a_bad_address_or_key_is_refused_at_start_without_quoting_the_key():
    with pytest.raises(ValueError, match="URL"):
        upstream.Upstream("ftp://127.0.0.1:9", INTERNAL)
    for bad in ("", "key with spaces-marker", "key-marker\r"):
        with pytest.raises(ValueError) as refused:
            upstream.Upstream("http://127.0.0.1:9", bad)
        assert "marker" not in str(refused.value)


def test_hop_by_hop_headers_never_come_back():
    raw = [(b"Connection", b"close, X-Hop"), (b"X-Hop", b"1"), (b"Transfer-Encoding", b"chunked"),
           (b"Keep-Alive", b"timeout=5"), (b"Content-Type", b"text/event-stream"), (b"X-Accel-Buffering", b"no"),
           (b"Trailer", b"x"), (b"Upgrade", b"h2c"), (b"Content-Length", b"12")]
    assert upstream.response_headers(raw) == [(b"content-type", b"text/event-stream"),
                                              (b"x-accel-buffering", b"no"), (b"content-length", b"12")]


# A stream's end, as llama.cpp b11146 writes each route's.

@pytest.mark.parametrize("path", list(STREAMS))
def test_each_routes_last_event_ends_its_stream_and_nothing_else_does(path):
    chunk, last, error = STREAMS[path]

    def outcome(*chunks: bytes) -> str:
        end = upstream.StreamEnd.watching(path, 200, [(b"content-type", b"text/event-stream")])
        for each in chunks:
            end.feed(each)
        return end.outcome()

    assert outcome(chunk, last) == "done"
    assert outcome(chunk, last, PING) == "done"  # a ping is no event
    assert outcome(chunk, PING, error) == "error"
    assert outcome(chunk, chunk) == "cut"
    assert outcome() == "cut" and outcome(PING) == "cut"
    assert outcome(chunk, error, chunk) == "cut"  # only the last event ends it
    assert outcome(chunk, last[:-1]) == "cut"  # an event isn't one until its blank line comes
    # Any route's last event but its own is no end.
    others = {each[1] for name, each in STREAMS.items() if each[1] != last}
    for other in others:
        assert outcome(chunk, other) == "cut"
    # Split anywhere, in CRLF or CR line ends, the same.
    for ending in (b"\r\n", b"\r"):
        text = (chunk + PING + last).replace(b"\n", ending)
        assert outcome(*[text[i:i + 1] for i in range(len(text))]) == "done"
        assert outcome(text) == "done"


def test_a_long_line_is_never_held_whole():
    end = upstream.StreamEnd.watching("/v1/chat/completions", 200, [(b"content-type", b"text/event-stream")])
    piece = b"x" * 65536
    end.feed(b'data: {"choices":[{"delta":{"content":"')
    for _ in range(160):  # 10 MiB of one line
        end.feed(piece)
        assert len(end._line) <= upstream.StreamEnd.HEAD
    end.feed(b'"}}]}\n\n')
    assert end.outcome() == "cut"
    end.feed(DONE)
    assert end.outcome() == "done"
    # A long event name, or data past the head, is never taken for a last event.
    long_name = upstream.StreamEnd.watching("/v1/messages", 200, [(b"content-type", b"text/event-stream")])
    long_name.feed(b"event: message_stop" + b"x" * 1000 + b"\ndata: {}\n\n")
    assert long_name.outcome() == "cut"
    long_data = upstream.StreamEnd.watching("/v1/chat/completions", 200, [(b"content-type", b"text/event-stream")])
    long_data.feed(b"data: [DONE]" + b" " * 1000 + b"\n\n")
    assert long_data.outcome() == "cut"
    # An engine's error with a long message is still its error.
    long_error = upstream.StreamEnd.watching("/v1/chat/completions", 200, [(b"content-type", b"text/event-stream")])
    long_error.feed(b'data: {"error":{"code":500,"message":"' + b"m" * 1000 + b'","type":"server_error"}}\n\n')
    assert long_error.outcome() == "error"


def test_only_an_event_stream_on_a_streamed_route_is_watched():
    stream = [(b"content-type", b"text/event-stream; charset=utf-8")]
    assert upstream.StreamEnd.watching("/v1/chat/completions", 200, stream) is not None
    assert upstream.StreamEnd.watching("/v1/embeddings", 200, stream) is None
    assert upstream.StreamEnd.watching("/v1/models", 200, stream) is None
    assert upstream.StreamEnd.watching("/v1/chat/completions", 200, [(b"content-type", b"application/json")]) is None
    assert upstream.StreamEnd.watching("/v1/chat/completions", 500, stream) is None
    # Encoded bytes can't be read for events: such a stream is judged by its connection alone.
    assert upstream.StreamEnd.watching("/v1/chat/completions", 200, [*stream, (b"content-encoding", b"gzip")]) is None


# Uploads.

class Holding:
    """An upstream that reads each request whole, keeps it, and holds every answer until `release` is set."""

    def __init__(self):
        self.seen: list[tuple[str, dict[bytes, bytes], bytes]] = []
        self.release = asyncio.Event()

    async def __call__(self, scope, receive, send):
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        self.seen.append((scope["path"], dict(scope["headers"]), body))
        await self.release.wait()
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": b'{"text":"ok"}'})


def upload(data: bytes = b"RIFF-standin-audio") -> tuple[bytes, str]:
    boundary = "spark-test-boundary"
    return (form_body([field("model", "stt"), file_part(data)], boundary),
            f"multipart/form-data; boundary={boundary}")


@pytest.mark.anyio
async def test_the_uploads_forwarded_at_once_are_capped(tmp_path):
    holding = Holding()
    async with serve_app(holding) as url, rig(tmp_path, swap_url=url) as r:
        assert r.app.uploads_at_once == front_app.UPLOADS_AT_ONCE == 4

        async def post_upload(key: str = "k1"):
            body, kind = upload()
            return await r.client.post("/v1/audio/transcriptions", content=body,
                                       headers={**auth(key), "content-type": kind})

        with anyio.fail_after(BOUND_S):
            held = [asyncio.create_task(post_upload()) for _ in range(4)]
            await until(lambda: len(holding.seen) == 4)
            fifth = await post_upload("k2")  # another key's too: the cap is the front's, not a key's
            assert fifth.status_code == 429
            assert fifth.json() == {"error": {"message": front_app.uploads_busy(4).message,
                                              "code": front_app.UPLOADS_BUSY_CODE},
                                    "retry_after_s": front_app.UPLOADS_RETRY_AFTER_S}
            assert fifth.headers["retry-after"] == str(front_app.UPLOADS_RETRY_AFTER_S) == "30"
            assert fifth.json()["error"]["message"].endswith("; try again in 30 seconds.")
            assert len(holding.seen) == 4  # never forwarded
            # A JSON body isn't an upload: the body budget bounds those.
            chat_answer = asyncio.create_task(r.client.post("/v1/chat/completions", json=chat(), headers=auth()))
            await until(lambda: len(holding.seen) == 5)
            holding.release.set()
            assert [answer.status_code for answer in await asyncio.gather(*held, chat_answer)] == [200] * 5
            await until(lambda: r.app.uploading == 0)
            assert (await post_upload()).status_code == 200  # each slot given back
        # The form went on whole, rebuilt by the front, with its model and its file.
        path, headers, body = holding.seen[0]
        assert path == "/v1/audio/transcriptions"
        assert headers[b"content-type"].startswith(b"multipart/form-data; boundary=spark-")
        assert b'name="model"\r\n\r\nstt\r\n' in body and b"RIFF-standin-audio" in body
        assert r.lines[0] == f"front: agent {WHISPER} 429 0.0s"  # the refused one ended first
    assert messages.pi_retry_match(json.dumps({"message": front_app.uploads_busy(4).message,
                                               "code": front_app.UPLOADS_BUSY_CODE})) is None


class Waits:
    """The gate's stand-in: no model loaded, and each admit held until `admitted` is set, then answered ok."""

    def __init__(self):
        self.admits: list[str] = []
        self.admitted = asyncio.Event()

    async def clear(self, model, key, deadline, request_id, *, enter, admit=False, waits=lambda: True,
                    loaded=None):
        if not waits():
            return gatelink.Refused("too_many_requests")
        self.admits.append(model)
        await self.admitted.wait()
        return enter()

    def refused(self, code, model, key, message):
        pass

    async def why_cut(self, model, since):
        return None

    def running_models(self):
        return frozenset()


@pytest.mark.anyio
async def test_an_upload_past_the_cap_is_refused_before_it_waits_for_a_load(tmp_path):
    # The slot is taken before the gate is asked, so an upload the cap would refuse never waits for a load, or starts
    # one, first (the controller's ruling, at Task 21's review). An upload waiting for its load holds its slot.
    holding, gate = Holding(), Waits()
    async with serve_app(holding) as url, rig(tmp_path, swap_url=url, gate=gate, uploads_at_once=1) as r:
        body, kind = upload()
        headers = {**auth(), "content-type": kind}
        with anyio.fail_after(BOUND_S):
            first = asyncio.create_task(r.client.post("/v1/audio/transcriptions", content=body, headers=headers))
            await until(lambda: len(gate.admits) == 1)
            assert r.app.uploading == 1
            second = await r.client.post("/v1/audio/transcriptions", content=body, headers=headers)
            assert (second.status_code, second.json()["error"]["code"]) == (429, front_app.UPLOADS_BUSY_CODE)
            assert gate.admits == [WHISPER]  # the second never asked the gate
            gate.admitted.set()
            await until(lambda: len(holding.seen) == 1)
            holding.release.set()
            assert (await first).status_code == 200
            await until(lambda: r.app.uploading == 0)


@pytest.mark.anyio
@pytest.mark.parametrize("way", ["gone", "unreached", "error", "cut", "cancelled"])
async def test_an_uploads_slot_is_given_back_however_it_ends(tmp_path, way):
    body, kind = upload()
    headers = {**auth(), "content-type": kind}
    holding = Holding()
    async with contextlib.AsyncExitStack() as stack:
        if way == "gone":
            url = await stack.enter_async_context(serve_app(holding))
            forwarder = upstream.Upstream(url, INTERNAL)
        elif way == "unreached":
            forwarder = upstream.Upstream(nothing_listening(), INTERNAL)
        elif way == "error":
            forwarder = Raising()
        else:
            forwarder = Scripted("break" if way == "cut" else "wait")
        app, tasks, client, lines = await stack.enter_async_context(front_with(tmp_path, forwarder,
                                                                                uploads_at_once=1))
        with anyio.fail_after(BOUND_S):
            if way == "gone":  # the client gone while its upload is forwarded, its answer held upstream
                gone = asyncio.create_task(client.post("/v1/audio/transcriptions", content=body, headers=headers))
                await until(lambda: len(holding.seen) == 1)
                assert app.uploading == 1
                gone.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await gone
            elif way in ("unreached", "error"):  # unreached: through admission, which the stand-in refuses
                answer = await client.post("/v1/audio/transcriptions", content=body, headers=headers)
                assert answer.status_code == {"unreached": 409, "error": 500}[way]
            else:  # cut upstream, or the handler's task cancelled as by the front's own stop
                with pytest.raises(httpx.RemoteProtocolError):
                    async with client.stream("POST", "/v1/audio/transcriptions", content=body,
                                             headers=headers) as response:
                        _, rest = await first_chunk(response)
                        assert app.uploading == 1
                        if way == "cancelled":
                            tasks.tasks[-1].cancel()
                        async for _ in rest:
                            pass
            ended = {"gone": "gone 0.0s", "unreached": "409 0.0s (llama_swap_down)", "error": "500 0.0s",
                     "cut": "cut 0.0s", "cancelled": "cut 0.0s"}[way]
            await until(lambda: lines == [f"front: dan-mac {WHISPER} {ended}"])
            assert app.uploading == 0
            holding.release.set()
        if isinstance(forwarder, upstream.Upstream):
            await forwarder.aclose()


# Ways out that end late.

@pytest.mark.anyio
async def test_an_error_while_a_cancelled_relay_closes_is_one_plain_line(tmp_path, caplog):
    # The handler's task cancelled while it streams, and the upstream answer's close then raises: the relay's error is
    # taken and logged as one plain line, by where it broke, so asyncio never logs it later as a task exception never
    # retrieved, with its traceback.
    caplog.set_level(logging.DEBUG)
    forwarder = Scripted("wait", close_raises=True)
    async with front_with(tmp_path, forwarder) as (app, tasks, client, lines):
        with anyio.fail_after(BOUND_S):
            with pytest.raises(httpx.RemoteProtocolError):
                async with client.stream("POST", "/v1/chat/completions", json=chat(), headers=auth()) as response:
                    _, rest = await first_chunk(response)
                    await forwarder.reached.wait()
                    tasks.tasks[-1].cancel()
                    async for _ in rest:
                        pass
            await until(lambda: lines == [f"front: dan-mac {CODER} cut 0.0s"])
    gc.collect()
    await asyncio.sleep(0)
    assert not [record for record in caplog.records if "never retrieved" in record.getMessage()]
    [plain] = [record for record in caplog.records if record.name == "spark.front.app"]
    assert plain.getMessage().startswith("the front's POST /v1/chat/completions broke as it was cancelled: "
                                         "RuntimeError at ")
    assert plain.exc_info is None and "close-marker" not in plain.getMessage()
    assert app.inflight.count(CODER) == 0


@pytest.mark.anyio
async def test_a_cut_stays_a_cut_when_the_client_goes_while_it_closes(tmp_path):
    # The answer broke off upstream, so the relay has stopped and is closing; a client that goes meanwhile neither
    # turns the cut into `gone` nor cuts the close short.
    forwarder = Scripted("break", close_s=0.3)
    async with front_with(tmp_path, forwarder) as (app, tasks, client, lines):
        with anyio.fail_after(BOUND_S):
            async with client.stream("POST", "/v1/chat/completions", json=chat(), headers=auth()) as response:
                kept = await first_chunk(response)  # kept: a dropped iterator would close the response at once
                await forwarder.reached.wait()
                await asyncio.sleep(0.05)  # the relay is in its close, which takes 0.3 s
                del kept
            await until(lambda: lines == [f"front: dan-mac {CODER} cut 0.0s"])
    assert forwarder.closed == 1  # its close ran to its end
    assert app.inflight.count(CODER) == 0


# A row per request (the usage meter's counter, Task 44d), in llama-server b11146's shapes (server-task.cpp): a chunk's
# fields in the order choices, created, id, model, system_fingerprint, object[, usage], timings.

TIMINGS = (',"timings":{"cache_n":90,"prompt_n":10,"prompt_ms":12.5,"prompt_per_token_ms":1.0,"prompt_per_second":9.0,'
           '"predicted_n":5,"predicted_ms":80.25,"predicted_per_token_ms":1.0,"predicted_per_second":9.0}')


def llama_chunk(delta: dict, tail: str = "") -> bytes:
    choices = json.dumps([{"finish_reason": None, "index": 0, "delta": delta}], separators=(",", ":"))
    return sse(f'{{"choices":{choices},"created":1790000000,"id":"chatcmpl-standin1","model":"m",'
               f'"system_fingerprint":"b11146","object":"chat.completion.chunk"{tail}}}')


# A stream without usage, as llama-server sends one a client didn't ask usage of: counted from its last timings.
COUNTED = [llama_chunk({"role": "assistant", "content": None}), llama_chunk({"reasoning_content": "hm"}),
           llama_chunk({"content": "hi"}), llama_chunk({}, TIMINGS), DONE]
# A whole answer, not streamed: its usage, then its id, then its timings.
ANSWER = ('{"choices":[{"finish_reason":"stop","index":0,"message":{"role":"assistant","content":"ok"}}],'
          '"created":1790000000,"model":"m","system_fingerprint":"b11146","object":"chat.completion",'
          '"usage":{"completion_tokens":7,"prompt_tokens":200,"total_tokens":207},"id":"chatcmpl-standin2"'
          f'{TIMINGS}}}').encode()


class Refuses(Loaded):
    """The gate's stand-in: every request refused, as the gate words `refusal`."""

    async def clear(self, model, key, deadline, request_id, *, enter, admit=False, waits=lambda: True,
                    loaded=None):
        return gatelink.Refused(self.refusal["code"], answer=self.refusal)


@pytest.mark.anyio
async def test_a_stream_leaves_one_row_with_its_counts(tmp_path):
    async with rig(tmp_path) as r:
        r.fake.stream(CODER, COUNTED)
        with anyio.fail_after(BOUND_S):
            async with r.stream() as response:
                assert await response.aread() == b"".join(COUNTED)
            await until(lambda: r.rows.rows)
    [row] = r.rows.rows
    fields = ("v", "key", "route", "model", "end", "status", "p", "c", "o", "from")
    assert {name: row.get(name) for name in fields} == {
        "v": 1, "key": "dan-mac", "route": "chat", "model": CODER, "end": "done", "status": 200, "p": 100, "c": 90,
        "o": 5, "from": "timings"}
    assert re.fullmatch(r"[0-9a-f]{16}", row["id"])
    assert row["t1"] >= row["t0"] and row["attempts"] == 1
    assert (row["ev_think"], row["ev_text"], row["resp_id"], row["stream"]) == (1, 1, "chatcmpl-standin1", True)
    assert (row["prompt_ms"], row["gen_ms"]) == (12, 80)
    assert row["wait_s"] >= 0 and row["first_s"] >= 0
    assert None not in row.values()


@pytest.mark.anyio
@pytest.mark.parametrize("ending", ["cut", "error", "refused by the gate", "refused by the front", "whole"])
async def test_each_ending_leaves_its_row(tmp_path, ending):
    fake = FakeLlamaSwap({INTERNAL}, NAMES)
    options: dict = {}
    if ending == "refused by the gate":
        options["gate"] = Refuses({"ok": False, "code": "no_fit", "status": 409, "message": "M", "retry_after_s": 30})
    elif ending == "refused by the front":
        options["budget"] = parse.BodyBudget(16)  # less than any body: front_busy, by its Content-Length
    elif ending == "whole":
        fake.canned("POST", "/v1/chat/completions", 200, ANSWER, {"content-type": "application/json"})
    async with rig(tmp_path, fake=fake, **options) as r:
        with anyio.fail_after(BOUND_S):
            if ending == "cut":  # the connection dropped after a chunk with timings
                r.fake.stream(CODER, [llama_chunk({"content": "hi"}, TIMINGS), DELTA, DONE], delay_s=0.05,
                              drop_after=1)
                with pytest.raises(httpx.RemoteProtocolError):
                    async with r.stream() as response:
                        await response.aread()
            elif ending == "error":  # the engine's own error event
                r.fake.stream(CODER, [DELTA, OAI_ERROR])
                assert (await r.client.post("/v1/chat/completions", json=chat(), headers=auth())).status_code == 200
            else:
                await r.client.post("/v1/chat/completions", json=chat(stream=False), headers=auth())
            await until(lambda: r.rows.rows)
    [row] = r.rows.rows
    expected = {
        "cut": {"end": "cut", "status": 200, "from": "timings", "p": 100, "c": 90, "o": 5, "attempts": 1},
        "error": {"end": "error", "status": 200, "code": None, "attempts": 1},
        "refused by the gate": {"end": "refused", "status": 409, "code": "no_fit", "model": CODER, "attempts": 0,
                                "from": "none"},
        "refused by the front": {"end": "refused", "status": 429, "code": "front_busy", "model": None,
                                 "route": "chat", "attempts": 0},
        "whole": {"end": "done", "status": 200, "from": "usage", "p": 200, "c": 90, "o": 7,
                  "resp_id": "chatcmpl-standin2", "stream": False, "attempts": 1},
    }[ending]
    assert {name: row.get(name) for name in expected} == expected
    assert row["key"] == "dan-mac" and None not in row.values()


@pytest.mark.anyio
async def test_no_client_string_reaches_a_row_even_one_that_passes_the_pattern(tmp_path):
    # Each passes the row's text pattern, [A-Za-z0-9._:-]{1,64}: only where a value comes from decides.
    async with rig(tmp_path) as r:
        r.fake.stream(CODER, [DELTA, DONE])
        with anyio.fail_after(BOUND_S):
            missing = await r.client.post("/v1/chat/completions", json=chat("secretproj-q3"), headers=auth())
            assert missing.status_code == 404
            await until(lambda: len(r.rows.rows) == 1)
            effort = await r.client.post("/v1/chat/completions", json=chat(reasoning_effort="secret-effort"),
                                         headers=auth())
            assert effort.status_code == 200
            await until(lambda: len(r.rows.rows) == 2)
            route = await r.client.post("/v1/secret-route", json=chat(), headers=auth())
            assert route.status_code == 404
            await until(lambda: len(r.rows.rows) == 3)
    assert "secret" not in json.dumps(r.rows.rows)
    missing_row, effort_row, route_row = r.rows.rows
    assert missing_row["asked_len"] == len("secretproj-q3") and "model" not in missing_row
    assert missing_row["code"] == "model_not_found"
    assert effort_row["model"] == CODER and "effort" not in effort_row and "asked_len" not in effort_row
    assert route_row["code"] == "route_not_served" and "route" not in route_row


@pytest.mark.anyio
async def test_no_row_for_a_401_health_or_the_model_list(tmp_path):
    async with rig(tmp_path) as r:
        r.fake.stream(CODER, [DELTA, DONE])
        with anyio.fail_after(BOUND_S):
            assert (await r.client.get("/health")).status_code == 200
            assert (await r.client.post("/v1/chat/completions", json=chat(),
                                        headers=auth("not-a-key"))).status_code == 401
            assert (await r.client.get("/v1/models", headers=auth())).status_code == 200
            assert (await r.client.post("/v1/chat/completions", json=chat(), headers=auth())).status_code == 200
            await until(lambda: len(r.lines) == 3)  # the 401's, the model list's and the chat's: its row is put next
    [row] = r.rows.rows
    assert (row["key"], row["model"], row["end"]) == ("dan-mac", CODER, "done")


class BreakingScan(usage.UsageScan):
    """A usage scan that breaks where `where` says: its feed, on its second call; or its finish."""

    def __init__(self, where: str):
        super().__init__("/v1/chat/completions", streamed=True)
        self.where, self.fed = where, 0

    def feed(self, chunk: bytes) -> None:
        self.fed += 1
        if self.where == "feed" and self.fed == 2:
            raise RuntimeError("scan-marker")
        super().feed(chunk)

    def finish(self) -> usage.Counts:
        if self.where == "finish":
            raise RuntimeError("scan-marker")
        return super().finish()


@pytest.mark.anyio
@pytest.mark.parametrize("where", ["feed", "scan_for", "finish"])
async def test_a_scanner_that_raises_never_cuts_the_stream(tmp_path, monkeypatch, caplog, where):
    def scan_for(path, status, headers):
        if where == "scan_for":
            raise RuntimeError("scan-marker")
        return BreakingScan(where)

    monkeypatch.setattr(usage, "scan_for", scan_for)
    caplog.set_level(logging.ERROR)
    async with rig(tmp_path) as r:
        r.fake.stream(CODER, COUNTED, delay_s=0.05)  # apart, so each comes to the front as a chunk of its own
        with anyio.fail_after(BOUND_S):
            async with r.stream() as response:
                assert await response.aread() == b"".join(COUNTED)  # every byte
            await until(lambda: r.rows.rows)
    assert r.lines == [f"front: dan-mac {CODER} 200 0.0s"]
    [row] = r.rows.rows
    assert (row["end"], row["status"], row["from"]) == ("done", 200, "none")  # the timings came after the fault
    assert not {"p", "c", "o", "ev_think", "ev_text", "resp_id"} & set(row)
    [broke] = [record for record in caplog.records if record.name == "spark.front.app"]  # once, for the request
    assert "RuntimeError" in broke.getMessage() and "scan-marker" not in broke.getMessage()
    assert broke.exc_info is None


@pytest.mark.anyio
async def test_a_rows_writer_that_raises_never_touches_the_request(tmp_path, caplog):
    caplog.set_level(logging.ERROR)
    async with rig(tmp_path, rows=Rows(raises=RuntimeError("rows-marker"))) as r:
        r.fake.stream(CODER, [DELTA, DONE])
        with anyio.fail_after(BOUND_S):
            first = await r.client.post("/v1/chat/completions", json=chat(), headers=auth())
            assert (first.status_code, first.content) == (200, DELTA + DONE)
            await until(lambda: len(r.lines) == 1)
            [broke] = [record for record in caplog.records if record.name == "spark.front.app"]
            assert "RuntimeError" in broke.getMessage() and "rows-marker" not in broke.getMessage()
            assert broke.exc_info is None
            second = await r.client.post("/v1/chat/completions", json=chat(), headers=auth())
            assert (second.status_code, second.content) == (200, DELTA + DONE)
            await until(lambda: len(r.lines) == 2)
    assert r.lines == [f"front: dan-mac {CODER} 200 0.0s"] * 2
    assert r.app.inflight.count(CODER) == 0 and r.app.caps.open("dan-mac") == 0


@pytest.mark.anyio
async def test_a_stream_cut_at_the_fronts_stop_has_put_its_row_once_aclose_returns(tmp_path):
    # uvicorn's stop cancels a stream that outlasts its graceful time and returns at once; the request ends only once
    # its relay has closed its upstream answer, here 0.2 s later. aclose waits for it, so `spark front`'s shutdown,
    # which closes the row file after aclose, never closes it before the row is put.
    forwarder, rows, lines = Scripted("wait", close_s=0.2), Rows(), []
    app = front_app.FrontApp(parse.ModelList(MODELS), KEYS, GROUPS, DIGESTS, upstream=forwarder, gate=Loaded(),
                             caps=parse.KeyCaps(KEYS, GROUPS), log=lines.append, clock=lambda: T, spool_dir=tmp_path,
                             rows=rows)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    server = serve.QuietServer(serve._config(app, make_protocol(peer_cred=False, header_timeout_s=10),
                                             graceful_s=0.3))
    serving = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        with anyio.fail_after(BOUND_S):
            while not server.started:
                await asyncio.sleep(0.01)
            url = f"http://127.0.0.1:{listener.getsockname()[1]}"
            async with httpx.AsyncClient(base_url=url, trust_env=False, timeout=httpx.Timeout(15)) as client:
                with contextlib.suppress(httpx.HTTPError):  # the cut, as the client sees it
                    async with client.stream("POST", "/v1/chat/completions", json=chat(), headers=auth()) as response:
                        kept = await first_chunk(response)
                        await forwarder.reached.wait()
                        server.should_exit = True
                        await serving  # its graceful time spent, the stream's task cancelled: still closing
                        assert forwarder.closed == 0 and rows.rows == []
                        await app.aclose()
                        del kept
    finally:
        server.should_exit = True
        await asyncio.gather(serving, return_exceptions=True)
        listener.close()
    assert forwarder.closed == 1
    [row] = rows.rows
    assert (row["key"], row["model"], row["end"], row["status"]) == ("dan-mac", CODER, "cut", 200)
    assert lines == [f"front: dan-mac {CODER} cut 0.0s"]


@pytest.mark.anyio
async def test_refused_rows_past_the_limit_are_folded_into_refused_n(tmp_path):
    async with rig(tmp_path, refused_limit=RefusedLimit(per_minute=2)) as r:
        r.fake.stream(CODER, [DELTA, DONE])
        with anyio.fail_after(BOUND_S):
            for _ in range(5):
                answer = await r.client.post("/v1/chat/completions", json=chat("no-such-model"), headers=auth())
                assert answer.status_code == 404
            # Another key's refusals are its own.
            answer = await r.client.post("/v1/chat/completions", json=chat("no-such-model"), headers=auth("k2"))
            assert answer.status_code == 404
            assert (await r.client.post("/v1/chat/completions", json=chat(), headers=auth())).status_code == 200
            await until(lambda: len(r.lines) == 7)
    assert [(row["key"], row["end"], row.get("refused_n")) for row in r.rows.rows] == [
        ("dan-mac", "refused", None), ("dan-mac", "refused", None), ("agent", "refused", None),
        ("dan-mac", "done", 3)]


# `spark front`, as systemd starts it.

SPARK_FRONT = "import sys\nfrom spark import cli\nsys.exit(cli.main(['front']))\n"


@pytest.fixture
def sockdir():
    path = Path(tempfile.mkdtemp(prefix="fs", dir="/tmp"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


def _received(receiver: socket.socket, timeout: float) -> bytes:
    receiver.settimeout(timeout)
    return receiver.recv(4096)


def _credentials(folder: Path) -> Path:
    folder.mkdir()
    (folder / "llamaswap-key").write_text(f"{INTERNAL}\n")
    (folder / "keys").write_text(KEYS_TEXT)
    digests = "".join(f"{name} {digest.hex()}\n" for name, digest in DIGESTS.items())
    (folder / "client-keys").write_text(digests)
    return folder


def _environment(**more: str) -> dict[str, str]:
    env = {name: os.environ[name] for name in ("PATH", "LANG", "HOME") if name in os.environ}
    return env | more


@LINUX_ONLY
@pytest.mark.anyio
async def test_spark_front_takes_9100_from_systemd(tmp_path, sockdir):
    # 127.0.0.1:0 stands in for 9100, which local-ai-front.socket holds, and a short socket under /tmp for the gate's
    # status socket (Task 22's GateLink), where the gate stand-in shows Gemma ready.
    credentials = _credentials(tmp_path / "credentials")
    registry = tmp_path / "models.yaml"
    registry.write_text(STACK_REGISTRY.read_text())
    from spark.registry import load_registry

    loaded = load_registry(registry)
    names = list(loaded.models)
    coder = next(name for name, model in loaded.models.items() if "coder" in model.roles)  # the registry's own
    fake = FakeLlamaSwap({INTERNAL}, names)
    for name in names:
        fake.set_state(name, "ready")
    fake.stream("gemma-4-26b-a4b", [DELTA, DONE])
    fake.stream(coder, [DELTA, DONE])
    gate = GateStandIn(sockdir, models=list(parse.ModelList.from_registry(loaded)), ready=[GEMMA])
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    url = f"http://127.0.0.1:{listener.getsockname()[1]}"
    with anyio.fail_after(60), socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as receiver:
        receiver.bind(str(sockdir / "notify"))
        await gate.start()
        try:
            async with serve_fake(fake) as swap_url:
                env = _environment(LISTEN_FDS="1", LISTEN_FDNAMES="front", NOTIFY_SOCKET=str(sockdir / "notify"),
                                   CREDENTIALS_DIRECTORY=str(credentials), SPARK_REGISTRY=str(registry),
                                   SPARK_LLAMASWAP_URL=swap_url, SPARK_GATE_STATUS=str(gate.path),
                                   SPARK_USAGE_DIR=str(tmp_path / "usage"))
                child = subprocess.Popen([sys.executable, "-c", LAUNCHER, str(listener.fileno()), SPARK_FRONT],
                                         pass_fds=[listener.fileno()], env=env, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, text=True)
                listener.close()  # the front's now, as systemd's would be
                try:
                    assert await asyncio.to_thread(_received, receiver, 30) == b"READY=1"
                    await until(lambda: gate.events_calls == 1 and gate.snapshots)  # it follows the gate
                    async with httpx.AsyncClient(base_url=url, trust_env=False, timeout=10) as client:
                        health = await client.get("/health")
                        assert (health.status_code, health.json()) == (200, {"status": "ok"})
                        await until(lambda: any(each["models"] for each in gate.snapshots))
                        answer = await client.post("/v1/chat/completions", json=chat(GEMMA), headers=auth("k2"))
                        assert (answer.status_code, answer.content) == (200, DELTA + DONE)
                        assert gate.admits == []  # loaded: no admit
                        answer = await client.post("/v1/chat/completions", json=chat(coder), headers=auth("k2"))
                        assert (answer.status_code, answer.content) == (200, DELTA + DONE)
                        assert [(admit["model"], admit["key"]) for admit in gate.admits] == [(coder, "agent")]
                    child.send_signal(signal.SIGTERM)
                    _, err = await asyncio.to_thread(child.communicate, timeout=30)
                finally:
                    if child.poll() is None:
                        child.kill()
                        await asyncio.to_thread(child.communicate, timeout=10)
                assert child.returncode == 0, err
                assert await asyncio.to_thread(_received, receiver, 5) == b"STOPPING=1"
        finally:
            await gate.close()
    assert [seen.headers["authorization"] for seen in fake.requests] == [f"Bearer {INTERNAL}"] * 2
    assert "front: agent gemma-4-26b-a4b 200" in err  # its journal line
    assert "Traceback" not in err and "k2" not in err


# `spark front` with uvicorn's graceful time cut to 1 s from gateproto.GRACEFUL_S's 20, so a stream held through its stop
# is cut within the test's bound: the same stop, sooner.
SPARK_FRONT_QUICK_STOP = ("import sys\nfrom spark import cli\nfrom spark.front import main\nmain.GRACEFUL_S = 1\n"
                          "sys.exit(cli.main(['front']))\n")


@LINUX_ONLY
@pytest.mark.anyio
@pytest.mark.parametrize("folder", ["missing", "a file", "read-only", "writable"])
async def test_spark_front_serves_whatever_its_usage_folder_and_a_stream_cut_at_its_stop_leaves_its_row(
        tmp_path, sockdir, folder):
    # No state of the usage folder keeps the front from starting or serving (the usage meter's counter, Task 44d). A
    # stream still open when uvicorn's graceful time ends is cut, and its row is put before the row file closes.
    usage_dir = tmp_path / "usage"
    if folder == "a file":
        usage_dir.write_text("")
    elif folder != "missing":
        usage_dir.mkdir(mode=0o500 if folder == "read-only" else 0o700)
    credentials = _credentials(tmp_path / "credentials")
    registry = tmp_path / "models.yaml"
    registry.write_text(STACK_REGISTRY.read_text())
    from spark.registry import load_registry

    loaded = load_registry(registry)
    names = list(loaded.models)
    fake = FakeLlamaSwap({INTERNAL}, names)
    for name in names:
        fake.set_state(name, "ready")
    gate = GateStandIn(sockdir, models=list(parse.ModelList.from_registry(loaded)), ready=[GEMMA])
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    url = f"http://127.0.0.1:{listener.getsockname()[1]}"
    with anyio.fail_after(60), socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as receiver:
        receiver.bind(str(sockdir / "notify"))
        await gate.start()
        try:
            async with serve_fake(fake) as swap_url:
                env = _environment(LISTEN_FDS="1", LISTEN_FDNAMES="front", NOTIFY_SOCKET=str(sockdir / "notify"),
                                   CREDENTIALS_DIRECTORY=str(credentials), SPARK_REGISTRY=str(registry),
                                   SPARK_LLAMASWAP_URL=swap_url, SPARK_GATE_STATUS=str(gate.path),
                                   SPARK_USAGE_DIR=str(usage_dir))
                child = subprocess.Popen([sys.executable, "-c", LAUNCHER, str(listener.fileno()),
                                          SPARK_FRONT_QUICK_STOP], pass_fds=[listener.fileno()], env=env,
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                listener.close()
                err = ""
                try:
                    assert await asyncio.to_thread(_received, receiver, 30) == b"READY=1"
                    await until(lambda: any(each["models"] for each in gate.snapshots))
                    async with httpx.AsyncClient(base_url=url, trust_env=False, timeout=10) as client:
                        health = await client.get("/health")
                        assert (health.status_code, health.json()) == (200, {"status": "ok"})
                        fake.stream(GEMMA, [DELTA, DONE])
                        answer = await client.post("/v1/chat/completions", json=chat(GEMMA), headers=auth("k2"))
                        assert (answer.status_code, answer.content) == (200, DELTA + DONE)
                        fake.stream(GEMMA, [DELTA, DELTA, DONE], delay_s=30)
                        with contextlib.suppress(httpx.HTTPError):  # the stream's cut, as its client sees it
                            async with client.stream("POST", "/v1/chat/completions", json=chat(GEMMA),
                                                     headers=auth("k2")) as held:
                                kept = await first_chunk(held)
                                child.send_signal(signal.SIGTERM)
                                _, err = await asyncio.to_thread(child.communicate, timeout=30)
                                del kept
                finally:
                    if child.poll() is None:
                        child.kill()
                        _, err = await asyncio.to_thread(child.communicate, timeout=10)
                assert child.returncode == 0, err
        finally:
            await gate.close()
    assert "Traceback" not in err and "k2" not in err
    assert "front: agent gemma-4-26b-a4b cut" in err  # its journal line
    if folder == "writable":
        [month] = usage_dir.glob("usage-*.jsonl")
        assert month.stat().st_mode & 0o777 == 0o640
        rows = [json.loads(line) for line in month.read_text().splitlines()]
        assert [(row["key"], row["model"], row["end"]) for row in rows] == [("agent", GEMMA, "done"),
                                                                            ("agent", GEMMA, "cut")]
    elif os.geteuid() != 0:  # root writes a read-only folder anyway
        assert "counting off" in err
        assert not usage_dir.is_dir() or list(usage_dir.iterdir()) == []


@LINUX_ONLY
def test_spark_front_refuses_to_start_without_its_socket_or_with_keys_that_disagree(tmp_path):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    registry = tmp_path / "models.yaml"
    registry.write_text(STACK_REGISTRY.read_text())
    credentials = _credentials(tmp_path / "credentials")
    # llama-swap's address on a closed port, so a refusal that stopped refusing could reach nothing live.
    # The gate's socket, a path nothing listens on, so a refusal that stopped refusing could reach no gate either.
    common = {"CREDENTIALS_DIRECTORY": str(credentials), "SPARK_REGISTRY": str(registry),
              "SPARK_LLAMASWAP_URL": nothing_listening(), "SPARK_GATE_STATUS": str(tmp_path / "no-gate.sock"),
              "SPARK_USAGE_DIR": str(tmp_path / "usage")}
    wrong = subprocess.run([sys.executable, "-c", LAUNCHER, str(listener.fileno()), SPARK_FRONT],
                           pass_fds=[listener.fileno()], env=_environment(LISTEN_FDS="1", LISTEN_FDNAMES="status",
                                                                          **common),
                           capture_output=True, text=True, timeout=30, check=False)
    assert wrong.returncode == 1 and "front" in wrong.stderr and "Traceback" not in wrong.stderr
    (credentials / "client-keys").write_text(f"robot {'ab' * 32}\n" + (credentials / "client-keys").read_text())
    disagree = subprocess.run([sys.executable, "-c", LAUNCHER, str(listener.fileno()), SPARK_FRONT],
                              pass_fds=[listener.fileno()], env=_environment(LISTEN_FDS="1", LISTEN_FDNAMES="front",
                                                                             **common),
                              capture_output=True, text=True, timeout=30, check=False)
    listener.close()
    assert disagree.returncode == 1 and "robot" in disagree.stderr and "Traceback" not in disagree.stderr
    assert "ab" * 32 not in disagree.stderr and INTERNAL not in disagree.stderr


# A raising request, through run_servers with `spark front`'s own logging: its 500, and none of the request on stderr.
RAISING = textwrap.dedent(
    """
    import socket, sys
    from spark import protocols, serve
    from spark.front import main

    main.configure_logging()

    async def app(scope, receive, send):
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        raise ValueError(f"a request broke on {body!r}, given {scope['headers']!r}")

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    print(listener.getsockname()[1], flush=True)
    serve.run_servers([(app, [listener])], protocol=protocols.make_protocol(peer_cred=False, header_timeout_s=10),
                      graceful_s=0.5)
    """
)


def test_a_raising_request_puts_no_request_content_on_stderr():
    # Three distinct stand-ins: the body's, a header's, and one sent as the client's key.
    markers = {"body": "BODY-MARKER-STANDIN", "header": "HEADER-MARKER-STANDIN", "key": "KEY-MARKER-STANDIN"}
    child = subprocess.Popen([sys.executable, "-c", RAISING], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, env=_environment())
    try:
        port = int(child.stdout.readline())
        body = json.dumps({"model": "coder", "prompt": markers["body"]}).encode()
        with socket.create_connection(("127.0.0.1", port), timeout=5) as client:
            client.sendall(b"POST /v1/chat/completions HTTP/1.1\r\nHost: spark\r\nX-Marker: %s\r\n"
                           b"Authorization: Bearer %s\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s"
                           % (markers["header"].encode(), markers["key"].encode(), len(body), body))
            answer = b""
            while chunk := client.recv(65536):
                answer += chunk
        assert answer.startswith(b"HTTP/1.1 500 ")
        child.send_signal(signal.SIGTERM)
        _, err = child.communicate(timeout=10)
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=10)
    assert child.returncode == 0, err
    assert "Exception in ASGI application" in err  # uvicorn's line came, through the front's logging
    for marker in markers.values():
        assert marker not in err
    assert "Traceback" not in err and "ValueError" not in err


def test_the_fronts_logging_setup_quiets_httpx_and_httpcore_and_filters_uvicorn():
    script = textwrap.dedent(
        """
        import logging
        from spark.front import main
        main.configure_logging()
        main.configure_logging()  # a second call adds nothing
        print(logging.getLogger("httpx").level, logging.getLogger("httpcore").level, len(logging.getLogger().handlers))
        seen = []  # another handler, ahead of the stderr one: what uvicorn.error's own filter leaves it
        other = logging.Handler()
        other.emit = lambda record: seen.append(record.exc_info is not None)  # as it was when this handler met it
        logging.getLogger().handlers.insert(0, other)
        try:
            raise ValueError("EXCEPTION-MARKER-STANDIN")
        except ValueError as err:
            logging.getLogger("uvicorn.error").error("Exception in ASGI application\\n", exc_info=err)
        logging.getLogger().removeHandler(other)
        print(sum(seen))
        logging.getLogger("uvicorn.error").warning("Unsupported upgrade request.")
        logging.getLogger("uvicorn.error").warning("No supported WebSocket library detected. Please use ...")
        logging.getLogger("uvicorn.error").info("Started server process")
        logging.getLogger("uvicorn.error").error("ASGI callable returned without completing response.")
        logging.getLogger("uvicorn.error").error("ASGI callable returned without starting response.")
        # A uvicorn logger below uvicorn.error, which only the handler's filter meets.
        try:
            raise ValueError("EXCEPTION-MARKER-STANDIN")
        except ValueError as err:
            logging.getLogger("uvicorn.error.below").error("Exception in ASGI application\\n", exc_info=err)
        """
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30, check=False,
                            env=_environment())
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == [str(30), str(30), "1", "0"]  # WARNING, WARNING, one handler; no traceback
    assert "upgrade" not in result.stderr and "WebSocket" not in result.stderr
    assert "Started server process" in result.stderr
    assert "Exception in ASGI application" in result.stderr
    assert "EXCEPTION-MARKER-STANDIN" not in result.stderr and "Traceback" not in result.stderr
    # The front ends a response unfinished on purpose, to pass a cut on, and its own line says `cut`: uvicorn's line
    # for it is dropped. One that never started is still a bug, and stays (the controller's ruling, Task 21's review).
    assert "without completing response" not in result.stderr
    assert "ASGI callable returned without starting response." in result.stderr


def test_the_gates_logging_setup_keeps_uvicorns_incomplete_response_line():
    # The gate never means to leave a response unfinished, so there the line flags a bug.
    script = textwrap.dedent(
        """
        import logging
        from spark.gate import main
        main.configure_logging()
        logging.getLogger("uvicorn.error").error("ASGI callable returned without completing response.")
        """
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30, check=False,
                            env=_environment())
    assert result.returncode == 0, result.stderr
    assert "ASGI callable returned without completing response." in result.stderr


# Its modules.

MODULES = textwrap.dedent(
    """
    import json, sys
    before = set(sys.modules)
    import {module}
    loaded = sorted(set(sys.modules) - before)
    print(json.dumps([name for name in loaded if name.split(".")[0] not in sys.stdlib_module_names]))
    """
)


def _loaded(module: str) -> list[str]:
    result = subprocess.run([sys.executable, "-c", MODULES.format(module=module)], capture_output=True, text=True,
                            timeout=60, check=False, env=_environment())
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_the_front_imports_only_its_listed_modules():
    assert isinstance(spark.front.FRONT_MODULES, frozenset)
    assert "spark.front.gatelink" in spark.front.FRONT_MODULES  # Task 22's link to the gate
    # Task 31's review: the unit runs `spark front`, so the front starts in spark.cli, and what spark.cli imports at
    # module level is the front's too. build_parser's other commands' modules aren't: see FRONT_MODULES.
    loaded = _loaded("spark.front.main") + _loaded("spark.cli")
    assert {name for name in loaded if name == "spark" or name.startswith("spark.")} == spark.front.FRONT_MODULES
    # spark.front alone loads no third-party module, so `spark apply` reads FRONT_MODULES without uvicorn.
    assert _loaded("spark.front") == ["spark", "spark.front"]

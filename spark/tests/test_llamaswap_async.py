"""Task 12: the gate's async llama-swap client (llamaswap_async), tested against a stand-in for llama-swap v257
(fake_llamaswap) that a real uvicorn serves on 127.0.0.1, never httpx's ASGITransport, which collects whole answers.
The stand-in answers as v257's own source does; its file cites the line each behaviour copies. Nothing here reaches the
box's own llama-swap."""

import asyncio
import base64
import contextlib
import socket
import time

import anyio
import httpx
import pytest

from fake_llamaswap import FakeLlamaSwap, serve_fake
from spark import gateproto, render
from spark.llamaswap import LlamaSwapAnswered, LlamaSwapError, LlamaSwapUnreachable, Running
from spark.llamaswap_async import AsyncLlamaSwap, LoadOutcome, load_timeout_for

KEY = "INTERNAL-KEY-STANDIN"  # stands for the gate's internal key, wherever one might travel
GEMMA = "gemma-4-26b-a4b"
CODER = "qwen3.6-35b-a3b"  # v257 lists /running sorted by model (internal/server/api.go:363), so after Gemma
EMBED = "qwen3-embedding-0.6b"
MODELS = [GEMMA, CODER, EMBED]
BOUND_S = 10  # every test ends within this, or fails


@pytest.fixture
def anyio_backend():
    return "asyncio"  # the gate runs on asyncio's loop, under uvicorn


@pytest.fixture
def fake():
    return FakeLlamaSwap({KEY}, MODELS)


@contextlib.asynccontextmanager
async def stand_in(reply: bytes | None = b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"):
    """A server that records each connection's bytes, up to the end of its head: where a redirect or a proxy would
    send the key. It answers `reply`, or with None drops the call unanswered."""
    seen: list[bytes] = []

    async def handle(reader, writer):
        data = b""
        try:
            while b"\r\n\r\n" not in data:
                chunk = await reader.read(65536)
                if not chunk:
                    break
                data += chunk
        finally:
            seen.append(data)
            if reply is not None:
                writer.write(reply)
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    try:
        yield f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}", seen
    finally:
        server.close()
        await server.wait_closed()


async def eventually(check, within_s: float = 2.0):
    """Polls `check` (a coroutine function returning a bool) until it holds, or fails after `within_s`."""
    deadline = time.monotonic() + within_s
    while not await check():
        assert time.monotonic() < deadline, "never held"
        await asyncio.sleep(0.02)


def test_the_load_timeout_is_the_deadline_plus_20():
    # The load's deadline, llama-swap's healthCheckTimeout, plus the 5 s it takes to kill a stuck start and a margin.
    assert load_timeout_for(180) == 200 == gateproto.LOAD_CALL_TIMEOUT_S
    assert load_timeout_for(render.HEALTH_CHECK_TIMEOUT_S) == gateproto.LOAD_CALL_TIMEOUT_S
    assert load_timeout_for(600) == 620


@pytest.mark.anyio
async def test_running_reads_v257s_shape(fake):
    with anyio.fail_after(BOUND_S):
        async with serve_fake(fake) as url, AsyncLlamaSwap(url, KEY, load_timeout_s=5) as llamaswap:
            assert await llamaswap.running() == []
            fake.set_state(CODER, "starting")
            fake.set_state(GEMMA, "ready")
            assert await llamaswap.running() == [Running(GEMMA, "ready"), Running(CODER, "starting")]

            # Anything but v257's shape must never read as nothing running: the gate would load over a loaded model.
            for body in (b'{"models": []}', b'{"running": {}}', b'{"running": [{"model": 1, "state": "ready"}]}',
                         b'{"running": [{"model": "m"}]}', b'{"running": ["m"]}', b"[]", b"OK", b"\xff"):
                fake.canned("GET", "/running", 200, body)
                with pytest.raises(LlamaSwapAnswered, match="^llama-swap GET /running: an answer it can't read"):
                    await llamaswap.running()
            fake.canned("GET", "/running", 500, b"")
            with pytest.raises(LlamaSwapAnswered, match="^llama-swap GET /running: HTTP 500$"):
                await llamaswap.running()


@pytest.mark.anyio
async def test_load_waits_past_every_other_timeout(fake):
    with anyio.fail_after(BOUND_S):
        async with serve_fake(fake) as url, \
                AsyncLlamaSwap(url, KEY, load_timeout_s=3, call_timeout_s=0.5) as llamaswap:
            fake.script_start(CODER, delay_s=1.0)
            began = time.monotonic()
            assert await llamaswap.load(CODER) == LoadOutcome.READY
            assert time.monotonic() - began >= 1.0
            assert await llamaswap.running() == [Running(CODER, "ready")]
            # A model already loaded answers at once.
            assert await llamaswap.load(CODER) == LoadOutcome.READY


@pytest.mark.anyio
async def test_a_load_call_that_times_out_reads_as_unknown_not_failed(fake):
    with anyio.fail_after(BOUND_S):
        async with serve_fake(fake) as url, AsyncLlamaSwap(url, KEY, load_timeout_s=0.2) as llamaswap:
            fake.script_start(CODER, delay_s=1.0)
            began = time.monotonic()
            assert await llamaswap.load(CODER) == LoadOutcome.UNKNOWN
            assert 0.2 <= time.monotonic() - began < 0.9
            # Which is the truth: v257's start goes on without its caller, so the caller keeps it counted as starting.
            assert await llamaswap.running() == [Running(CODER, "starting")]

            async def ready():
                return await llamaswap.running() == [Running(CODER, "ready")]

            await eventually(ready)


@pytest.mark.anyio
async def test_a_failed_start_reads_as_failed_with_its_text(fake):
    with anyio.fail_after(BOUND_S):
        async with serve_fake(fake) as url, AsyncLlamaSwap(url, KEY, load_timeout_s=5) as llamaswap:
            fake.script_start(CODER, outcome="exited")
            outcome = await llamaswap.load(CODER)
            assert outcome == LoadOutcome.failed(500, outcome.text)
            assert "upstream command exited prematurely" in outcome.text
            assert await llamaswap.running() == []

            fake.script_start(CODER, delay_s=0.1, outcome="timeout")
            outcome = await llamaswap.load(CODER)
            assert outcome.kind == "failed" and outcome.status == 500
            assert "health check timed out after 3m0s" in outcome.text  # Go's way of writing 180 s
            assert await llamaswap.running() == []

            # The answer's text arrives as one line, whatever it holds.
            fake.canned("GET", f"/upstream/{CODER}/health", 502, b"bad\x1b[2Jgateway\nnext")
            assert await llamaswap.load(CODER) == LoadOutcome.failed(502, "bad\\x1b[2Jgateway\\nnext")


@pytest.mark.anyio
async def test_unload_returns_once_the_engine_is_gone(fake):
    with anyio.fail_after(BOUND_S):
        async with serve_fake(fake) as url, AsyncLlamaSwap(url, KEY, load_timeout_s=5) as llamaswap:
            fake.set_state(CODER, "ready")
            fake.set_state(GEMMA, "ready")
            fake.script_stop(CODER, delay_s=0.3)
            began = time.monotonic()
            unloading = asyncio.create_task(llamaswap.unload(CODER))
            await asyncio.sleep(0.1)
            assert await llamaswap.running() == [Running(GEMMA, "ready"), Running(CODER, "stopping")]
            assert await unloading is None
            assert time.monotonic() - began >= 0.3
            assert await llamaswap.running() == [Running(GEMMA, "ready")]

            with pytest.raises(LlamaSwapAnswered, match="^llama-swap POST /api/models/unload/nonesuch: HTTP 404$"):
                await llamaswap.unload("nonesuch")


@pytest.mark.anyio
async def test_last_lines_reads_a_bounded_tail_and_closes(fake):
    with anyio.fail_after(BOUND_S):
        async with serve_fake(fake) as url, AsyncLlamaSwap(url, KEY, load_timeout_s=5) as llamaswap:
            fake.log(CODER, [f"line {i}" for i in range(49)] + ["colour \x1b[31mred\x1b[0m\ttab\u2028separator"])
            began = time.monotonic()
            lines = await llamaswap.last_lines(CODER, read_s=0.3)
            assert 0.3 <= time.monotonic() - began < 0.6
            assert lines == [f"line {i}" for i in range(30, 49)] + ["colour \\x1b[31mred\\x1b[0m\\ttab\\u2028separator"]

            async def closed():
                return fake.tails(CODER) == 0

            await eventually(closed, within_s=0.5)  # the stream left open is closed by the client, not by llama-swap

            # What arrives while it reads counts too, and n bounds the tail.
            asyncio.get_running_loop().call_later(0.1, fake.log, CODER, ["arrived while reading"])
            assert await llamaswap.last_lines(CODER, n=2, read_s=0.3) == [
                "colour \\x1b[31mred\\x1b[0m\\ttab\\u2028separator", "arrived while reading"]
            await eventually(closed, within_s=0.5)

            with pytest.raises(LlamaSwapAnswered, match="^llama-swap GET /logs/stream/nonesuch: HTTP 400$"):
                await llamaswap.last_lines("nonesuch", read_s=0.1)


@pytest.mark.anyio
async def test_no_redirect_is_followed_with_the_key(fake):
    with anyio.fail_after(BOUND_S):
        async with serve_fake(fake) as url, stand_in() as (elsewhere, seen), \
                AsyncLlamaSwap(url, KEY, load_timeout_s=5) as llamaswap:
            fake.canned("GET", "/running", 302, b"", {"location": f"{elsewhere}/running"})
            with pytest.raises(LlamaSwapAnswered, match="^llama-swap GET /running: HTTP 302$"):
                await llamaswap.running()
            fake.canned("POST", f"/api/models/unload/{CODER}", 307, b"", {"location": f"{elsewhere}/unload"})
            with pytest.raises(LlamaSwapAnswered, match=f"^llama-swap POST /api/models/unload/{CODER}: HTTP 307$"):
                await llamaswap.unload(CODER)
            fake.canned("GET", f"/logs/stream/{CODER}", 302, b"", {"location": f"{elsewhere}/logs"})
            with pytest.raises(LlamaSwapAnswered, match=f"^llama-swap GET /logs/stream/{CODER}: HTTP 302$"):
                await llamaswap.last_lines(CODER, read_s=0.1)
            fake.canned("GET", f"/upstream/{CODER}/health", 302, b"", {"location": f"{elsewhere}/health"})
            assert await llamaswap.load(CODER) == LoadOutcome.failed(302, "")
            assert seen == []


@pytest.mark.anyio
async def test_an_environment_proxy_is_ignored(fake, monkeypatch):
    with anyio.fail_after(BOUND_S):
        async with serve_fake(fake) as url, stand_in() as (proxy, seen):
            for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
                monkeypatch.setenv(name, proxy)
            async with AsyncLlamaSwap(url, KEY, load_timeout_s=5) as llamaswap:
                assert await llamaswap.load(GEMMA) == LoadOutcome.READY
                assert await llamaswap.running() == [Running(GEMMA, "ready")]
                await llamaswap.unload(GEMMA)
                assert await llamaswap.last_lines(GEMMA, read_s=0.1) == []
            assert seen == []
            assert [(r.method, r.path) for r in fake.requests] == [
                ("GET", f"/upstream/{GEMMA}/health"), ("GET", "/running"), ("POST", f"/api/models/unload/{GEMMA}"),
                ("GET", f"/logs/stream/{GEMMA}")]


@pytest.mark.anyio
async def test_the_key_goes_only_in_its_header(fake):
    with anyio.fail_after(BOUND_S):
        async with serve_fake(fake) as url:
            async with AsyncLlamaSwap(url, KEY, load_timeout_s=5) as llamaswap:
                await llamaswap.running()
                await llamaswap.load(GEMMA)
                await llamaswap.unload(GEMMA)
                await llamaswap.last_lines(GEMMA, read_s=0.1)
            assert len(fake.requests) == 4
            for seen in fake.requests:
                assert seen.headers["authorization"] == f"Bearer {KEY}"
                assert KEY not in seen.path + seen.query
                assert not [name for name, value in seen.headers.items() if KEY in value and name != "authorization"]

            # A key that is empty, or that a header can't carry, is refused before anything is sent, and never shown.
            for bad in ("", "with\r\nnewline", "with\ttab", "nicht-ascii-ä"):
                with pytest.raises(LlamaSwapError) as caught:
                    AsyncLlamaSwap(url, bad, load_timeout_s=5)
                assert not bad or bad not in str(caught.value)
            assert len(fake.requests) == 4


@pytest.mark.anyio
async def test_a_call_nothing_answers_is_unreachable_and_a_load_is_unknown():
    with anyio.fail_after(BOUND_S):
        with socket.socket() as probe:  # a port with nothing listening on it
            probe.bind(("127.0.0.1", 0))
            closed = f"http://127.0.0.1:{probe.getsockname()[1]}"
        async with AsyncLlamaSwap(closed, KEY, load_timeout_s=5) as llamaswap:
            for call in (llamaswap.running(), llamaswap.unload(CODER), llamaswap.last_lines(CODER, read_s=0.1)):
                with pytest.raises(LlamaSwapUnreachable, match=f"^llama-swap unreachable at {closed}: "):
                    await call
            # plan.md: on any timeout or error the gate keeps the load counted as starting, never freeing the one-load
            # slot early; so a load is UNKNOWN even when nothing was sent.
            assert await llamaswap.load(CODER) == LoadOutcome.UNKNOWN

        # Sent, then dropped unanswered: the load may be under way.
        async with stand_in(reply=None) as (dropping, seen), \
                AsyncLlamaSwap(dropping, KEY, load_timeout_s=5) as llamaswap:
            assert await llamaswap.load(CODER) == LoadOutcome.UNKNOWN
            with pytest.raises(LlamaSwapUnreachable):
                await llamaswap.running()
            assert len(seen) == 2


@pytest.mark.anyio
async def test_the_stand_in_answers_as_v257_does(fake):
    with anyio.fail_after(BOUND_S):
        async with serve_fake(fake) as url, httpx.AsyncClient(base_url=url, trust_env=False) as http:
            keyed = {"Authorization": f"Bearer {KEY}"}

            # A key it knows, by Bearer, Basic's password or x-api-key; anything else a 401 (internal/server/auth.go).
            answer = await http.get("/running")
            assert answer.status_code == 401
            assert answer.headers["www-authenticate"] == 'Basic realm="llama-swap"'
            assert answer.content == (b'{"src":"llama-swap","error":{"message":"unauthorized: invalid or missing API '
                                      b'key","type":"authentication_error","param":null,"code":"unauthorized"}}')
            assert (await http.get("/running", headers={"Authorization": "Bearer wrong"})).status_code == 401
            assert (await http.get("/running", headers={"x-api-key": KEY})).status_code == 200
            basic = base64.b64encode(f"anyone:{KEY}".encode()).decode()
            assert (await http.get("/running", headers={"Authorization": f"Basic {basic}"})).status_code == 200
            assert (await http.get("/health")).content == b"OK"  # unkeyed

            # Ten requests per model, waiting or served; the eleventh refused at once (fifo.go, httperror.go).
            fake.script_start(CODER, delay_s=30)
            waiting = [asyncio.create_task(http.get(f"/upstream/{CODER}/health", headers=keyed)) for _ in range(10)]

            async def ten():
                return fake.reserved(CODER) == 10

            await eventually(ten)
            eleventh = await http.post("/v1/chat/completions", headers=keyed, json={"model": CODER})
            assert eleventh.status_code == 429
            assert eleventh.headers["retry-after"] == "1"
            assert eleventh.json()["error"] == {"message": "Too many requests", "type": "rate_limit_error",
                                                "param": None, "code": "concurrency_limit"}

            # An unload fails every request waiting for the start, and aborts it.
            unloaded = await http.post(f"/api/models/unload/{CODER}", headers=keyed)
            assert (unloaded.status_code, unloaded.content) == (200, b"OK")
            for answer in await asyncio.gather(*waiting):
                assert answer.status_code == 500
                assert answer.json()["error"]["message"] == "unspecific error: group: model unloaded"

            async def none():
                return fake.reserved(CODER) == 0

            await eventually(none)
            assert (await http.get("/running", headers=keyed)).json() == {"running": []}

            # An inference request streams SSE, and an unload while it streams ends the stream early, cleanly.
            fake.set_state(GEMMA, "ready")
            chunks = [b'data: {"n": %d}\n\n' % i for i in range(10)] + [b"data: [DONE]\n\n"]
            fake.stream(GEMMA, chunks, delay_s=0.1)
            got = []
            async with http.stream("POST", "/v1/chat/completions", headers=keyed,
                                   json={"model": GEMMA, "stream": True}) as streaming:
                assert streaming.status_code == 200
                assert streaming.headers["content-type"] == "text/event-stream"
                async for chunk in streaming.aiter_raw():
                    got.append(chunk)
                    if len(got) == 1:
                        unloading = asyncio.create_task(http.post(f"/api/models/unload/{GEMMA}", headers=keyed))
            assert (await unloading).content == b"OK"
            assert 1 <= len(got) < len(chunks) and b"[DONE]" not in b"".join(got)

            # And what it saw, with the headers it was sent.
            assert fake.requests[0].headers.get("authorization") is None
            assert fake.requests[-1].headers["authorization"] == f"Bearer {KEY}"

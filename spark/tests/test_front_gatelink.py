"""The front with the gate (website/design/phase-2a.md, Task 22): GateLink asks the gate for each load and follows its
events. A model not loaded waits for the gate's answer, a drained model is held, apply's hold holds every new request,
a gate that restarts is asked again with each request's original deadline, and while the gate is down the front serves
what llama-swap shows loaded and refuses new loads with `gate_down`.

The front runs on a real uvicorn in the test's own event loop. GateLink reaches a scripted stand-in for the gate's
status socket, on a short Unix socket under /tmp (gate_standin.py), and the front's Upstream a stand-in for llama-swap
on 127.0.0.1 (Swap). Every wait of the gate link's and the app's reads one injected clock, which the tests move, so a
key's 30 s or 10 minutes pass at once. Every key, digest and name here is a stand-in; none was read from the box."""

import asyncio
import contextlib
import dataclasses
import json
import shutil
import tempfile
from pathlib import Path

import anyio
import httpx
import pytest

from front_helpers import BOUNDARY, DIGESTS, GROUPS, KEYS, MODELS, field, file_part, form_body
from gate_standin import GateStandIn
from spark import messages
from spark.front import app as front_app
from spark.front import gatelink, parse, upstream
from spark.gate import app as gate_app
from spark.gateproto import GATE_DOWN_AFTER_S
from test_front_forward import DELTA, DONE, INTERNAL, auth, chat, nothing_listening, serve_app, until

BOUND_S = 20
T = 1000.0
CODER, GEMMA, EMBED, WHISPER = "qwen3.8-27b", "gemma-4-26b-a4b", "qwen3-embedding-0.6b", "whisper-large-v3-turbo"
NAMES = [entry["name"] for entry in MODELS]
DAN_WAIT = GROUPS[KEYS["dan-mac"].group].wait_s
AGENT_WAIT = GROUPS[KEYS["agent"].group].wait_s
FORM_TYPE = f"multipart/form-data; boundary={BOUNDARY}"
# v257's answer to a request whose model's start ended with its command exited (internal/process/process_command.go:
# 556-559, then internal/swaputil/http.go:185-186), as Task 12's stand-in writes it.
EXITED = (b'{"src":"llama-swap","error":{"message":"unspecific error: upstream command exited prematurely",'
          b'"type":"server_error","param":null,"code":"internal_error"}}')


@pytest.fixture
def anyio_backend():
    return "asyncio"


def drain(model: str, drain_id: str, why: str) -> dict:
    return {"op": "drain", "model": model, "drain_id": drain_id, "why": why}


def undrain(model: str, drain_id: str | None) -> dict:
    return {"op": "undrain", "model": model, "drain_id": drain_id}


def unloaded(model: str) -> dict:
    return {"op": "unloaded", "model": model}


async def _whole(send, status: int, body: bytes) -> None:
    await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"application/json")]})
    await send({"type": "http.response.body", "body": body})


class Swap:
    """llama-swap's stand-in: `/running` shows each model in `ready` ready; each inference request is kept, its path
    and its body whole (`seen`), then answered with v257's 500 `upstream command exited prematurely` while `exited` is
    above 0, counting it down; else with an event stream, DELTA then DONE, held between the two until its hold is
    released (the first of `holds` left, one a request, else `hold`, when set), or cut after DELTA while `cut` is
    true."""

    def __init__(self, ready=()):
        self.ready = set(ready)
        self.seen: list[tuple[str, bytes]] = []
        self.exited = 0
        self.hold: asyncio.Event | None = None
        self.holds: list[asyncio.Event] = []
        self.cut = False

    async def __call__(self, scope, receive, send):
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        if scope["path"] == "/running":
            running = [{"model": name, "state": "ready"} for name in sorted(self.ready)]
            return await _whole(send, 200, json.dumps({"running": running}).encode())
        self.seen.append((scope["path"], body))
        hold = self.holds.pop(0) if self.holds else self.hold
        if self.exited:
            self.exited -= 1
            return await _whole(send, 500, EXITED)
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/event-stream")]})
        await send({"type": "http.response.body", "body": DELTA, "more_body": True})
        if self.cut:
            return  # unfinished: uvicorn closes the connection
        if hold is not None:
            await hold.wait()
        await send({"type": "http.response.body", "body": DONE})


@dataclasses.dataclass
class Rig:
    gate: GateStandIn
    swap: Swap
    link: gatelink.GateLink
    app: front_app.FrontApp
    client: httpx.AsyncClient
    now: list[float]
    lines: list[str]

    def post(self, model: str = CODER, key: str = "k1", path: str = "/v1/chat/completions"):
        return self.client.post(path, json=chat(model), headers=auth(key))

    def ask(self, model: str = CODER, key: str = "k1", path: str = "/v1/chat/completions") -> asyncio.Task:
        """A request for `model` with `key`, as a task of its own."""
        return asyncio.create_task(self.post(model, key, path))

    def count(self, model: str = CODER) -> int:
        return self.link.inflight.count(model)

    async def heard(self, events: int) -> None:
        """Until the gate link has read `events` more events than it had."""
        await until(lambda: self.link.events_read >= events)


@contextlib.asynccontextmanager
async def rig(*, ready=(), swap_ready=None, models=MODELS, serving=True, applying=False, lapse_s=60.0,
              swap_url=None, cut_why_s=5.0, **app_options):
    """The front on a real uvicorn, its GateLink on the gate stand-in's socket (serving unless `serving` is false, when
    its listener only holds connections), llama-swap's stand-in at `swap_url` or a Swap of its own showing `swap_ready`
    (by default, the gate's `ready`), and an httpx client for the front. With the gate serving, yielded once the gate
    link has read its first `hello` and `state`."""
    folder = Path(tempfile.mkdtemp(prefix="gl", dir="/tmp"))
    gate = GateStandIn(folder, models=models, ready=ready, applying=applying, lapse_s=lapse_s, started_at=T)
    swap = Swap(ready if swap_ready is None else swap_ready)
    now: list[float] = [T]
    lines: list[str] = []
    try:
        if serving:
            await gate.start()
        async with contextlib.AsyncExitStack() as stack:
            url_of_swap = swap_url or await stack.enter_async_context(serve_app(swap))
            forwarder = upstream.Upstream(url_of_swap, INTERNAL)
            stack.push_async_callback(forwarder.aclose)
            inflight = upstream.InFlight(lambda: now[0])
            listed = parse.ModelList(models)
            link = gatelink.GateLink(gate.path, lambda: now[0], inflight=inflight, models=listed,
                                     running=forwarder.ready_models, poll_s=0.01, retry_s=0.01, cut_why_s=cut_why_s)
            app = front_app.FrontApp(listed, KEYS, GROUPS, DIGESTS, upstream=forwarder, gate=link,
                                     caps=parse.KeyCaps(KEYS, GROUPS), log=lines.append, clock=lambda: now[0],
                                     spool_dir=folder, inflight=inflight, resend_after_s=0.01, **app_options)
            await link.start()
            stack.push_async_callback(link.aclose)
            stack.push_async_callback(app.aclose)
            url = await stack.enter_async_context(serve_app(app))
            client = await stack.enter_async_context(httpx.AsyncClient(base_url=url, trust_env=False,
                                                                       timeout=httpx.Timeout(15)))
            if serving:
                with anyio.fail_after(BOUND_S):
                    await until(lambda: link.connected and link.events_read >= 2)
            yield Rig(gate, swap, link, app, client, now, lines)
    finally:
        await gate.close()
        shutil.rmtree(folder, ignore_errors=True)


async def settled(task: asyncio.Task) -> None:
    """`task` cancelled, if it is still running, and its end taken."""
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


def upload(audio: bytes = b"RIFF-standin-audio") -> tuple[bytes, dict[str, str]]:
    return form_body([field("model", "stt"), file_part(audio)]), {**auth(), "content-type": FORM_TYPE}


# A request's way.

@pytest.mark.anyio
async def test_a_loaded_model_is_forwarded_without_asking_the_gate():
    async with rig(ready=[CODER]) as r:
        with anyio.fail_after(BOUND_S):
            answer = await r.post()
        assert (answer.status_code, answer.content) == (200, DELTA + DONE)
        assert r.gate.admits == [] and [path for path, _ in r.swap.seen] == ["/v1/chat/completions"]


@pytest.mark.anyio
async def test_a_model_not_loaded_waits_for_the_gates_answer():
    async with rig(ready=[GEMMA]) as r:
        r.gate.hold = asyncio.Event()
        with anyio.fail_after(BOUND_S):
            asked = r.ask()
            await until(lambda: len(r.gate.admits) == 1)
            await asyncio.sleep(1)  # the stand-in holds it a second
            assert not asked.done() and r.swap.seen == []
            r.gate.hold.set()
            answer = await asked
        assert (answer.status_code, answer.content) == (200, DELTA + DONE)
        assert len(r.swap.seen) == 1
        [admit] = r.gate.admits
        assert DAN_WAIT == 30
        assert {name: admit[name] for name in ("model", "key", "deadline")} == {
            "model": CODER, "key": "dan-mac", "deadline": T + DAN_WAIT}
        assert isinstance(admit["request_id"], str) and admit["request_id"]


@pytest.mark.anyio
async def test_a_refusal_reaches_the_client_as_its_status_with_its_words_and_headers():
    async with rig() as r:
        for code, status, retry in (("no_fit", 409, 30), ("loading", 409, None), ("restarting", 409, 60),
                                    ("gate_down", 503, 30)):
            r.gate.answer = {"ok": False, "code": code, "status": status, "message": f"M, {code}",
                             "retry_after_s": retry}
            with anyio.fail_after(BOUND_S):
                answer = await r.post()
            expected: dict = {"error": {"message": f"M, {code}", "code": code}}
            if retry is not None:
                expected["retry_after_s"] = retry
            assert (answer.status_code, answer.json()) == (status, expected)
            assert answer.headers["x-should-retry"] == "false"
            assert answer.headers.get("retry-after") == (None if retry is None else str(retry))
        assert r.swap.seen == []


# The gate down.

@pytest.mark.anyio
async def test_the_gate_counts_as_down_only_after_5s_without_an_answer():
    async with rig() as r:
        with anyio.fail_after(BOUND_S):
            await r.gate.stop("crash")  # the events call drops at T; a new one waits in the held socket, never accepted
            await until(lambda: not r.link.connected)
            r.now[0] = T + 3
            assert r.link.gate_up
            waiting = r.ask()
            await asyncio.sleep(0.2)
            assert not waiting.done()  # a request at T + 3 for a model not loaded still waits
            r.now[0] = T + GATE_DOWN_AFTER_S
            assert GATE_DOWN_AFTER_S == 5 and not r.link.gate_up
            down = await r.post(key="k2")  # at T + 5, a new one gets gate_down at once
            assert (down.status_code, down.json()) == (503, r.app.refusal("gate_down", "agent").body())
            assert down.headers["retry-after"] == "30" and down.headers["x-should-retry"] == "false"
            assert not waiting.done()  # the one already waiting keeps its key's wait
            r.now[0] = T + 3 + DAN_WAIT
            late = await waiting
        assert (late.status_code, late.json()) == (503, r.app.refusal("gate_down", "dan-mac").body())
        assert r.gate.admits == [] and r.swap.seen == []


@pytest.mark.anyio
async def test_while_the_gate_is_down_loaded_models_answer_and_loads_get_gate_down():
    async with rig(serving=False, swap_ready=[GEMMA]) as r:
        r.now[0] = T + GATE_DOWN_AFTER_S  # no answer since the front started at T
        with anyio.fail_after(BOUND_S):
            gemma = await r.post(GEMMA)
            coder = await r.post(CODER)
        assert (gemma.status_code, gemma.content) == (200, DELTA + DONE)
        words = messages.refusal("gate_down", messages.Moment(words="dan")).message  # Task 6's text
        assert (coder.status_code, coder.json()) == (503, {"error": {"message": words, "code": "gate_down"},
                                                           "retry_after_s": 30})
        assert coder.headers["retry-after"] == "30" and coder.headers["x-should-retry"] == "false"
        assert [path for path, _ in r.swap.seen] == ["/v1/chat/completions"] and r.gate.admits == []


@pytest.mark.anyio
@pytest.mark.parametrize("how", ["crash", "stopping"])
async def test_a_gate_restart_reasks_with_the_original_deadline(how):
    # `stopping` is spark gate's own stop: the held admit answered 503 GATE_STOPPING (Task 19); `crash`, its
    # connection broken. The front takes either as the gate restarting.
    async with rig() as r:
        r.gate.hold = asyncio.Event()
        with anyio.fail_after(BOUND_S):
            asked = r.ask()
            await until(lambda: len(r.gate.admits) == 1)
            r.now[0] = T + 10
            await r.gate.stop(how)
            await until(lambda: not r.link.connected)
            await asyncio.sleep(0.1)
            assert not asked.done()
            await r.gate.start()
            await until(lambda: len(r.gate.admits) == 2)
            r.gate.hold.set()
            answer = await asked
        assert (answer.status_code, answer.content) == (200, DELTA + DONE)
        first, again = r.gate.admits
        assert again == first and again["deadline"] == T + DAN_WAIT  # the same request id, the original deadline
        assert len(r.swap.seen) == 1


@pytest.mark.anyio
async def test_a_refused_connection_goes_through_admission():
    async with rig(ready=[CODER], swap_url=nothing_listening()) as r:
        with anyio.fail_after(BOUND_S):
            for code in ("llama_swap_down", "restarting"):
                worded = messages.refusal(code, messages.Moment(words="dan", wait_s=DAN_WAIT))
                r.gate.answer = {"ok": False, "code": code, "status": worded.status, "message": worded.message,
                                 "retry_after_s": worded.retry_after_s}
                answer = await r.post()
                assert (answer.status_code, answer.json()) == (409, worded.body())
            assert [admit["deadline"] for admit in r.gate.admits] == [T + DAN_WAIT] * 2
            # The gate admitting it each time while llama-swap still doesn't answer: asked again after a pause, then
            # the front's own llama_swap_down at the deadline.
            r.gate.answer = None
            asked = r.ask()
            await until(lambda: len(r.gate.admits) >= 5)
            r.now[0] = T + DAN_WAIT
            answer = await asked
        assert (answer.status_code, answer.json()) == (409, r.app.refusal("llama_swap_down", "dan-mac").body())
        assert answer.headers["x-should-retry"] == "false"


@pytest.mark.anyio
async def test_exited_prematurely_is_asked_again_and_forwarded_from_the_held_body():
    async with rig(ready=[CODER, WHISPER]) as r:
        body = chat(CODER, marker="held-body-marker")
        r.swap.exited = 1
        with anyio.fail_after(BOUND_S):
            answer = await r.client.post("/v1/chat/completions", json=body, headers=auth())
            assert (answer.status_code, answer.content) == (200, DELTA + DONE)  # the second send's answer
            assert [admit["deadline"] for admit in r.gate.admits] == [T + DAN_WAIT]
            (path, first), (path_again, again) = r.swap.seen
            assert (path, first) == (path_again, again)
            assert first == json.dumps(body, separators=(",", ":")).encode()  # the same body bytes
            # Twice in a row: load_failed.
            r.swap.exited = 2
            answer = await r.client.post("/v1/chat/completions", json=body, headers=auth())
            failed = r.app.refusal("load_failed", "dan-mac", model_label="the coder", model_command="coder",
                                   start_ended=True)
            assert (answer.status_code, answer.json()) == (409, failed.body())
            assert answer.headers["x-should-retry"] == "false"
            assert len(r.gate.admits) == 2 and len(r.swap.seen) == 4
            # A form goes again whole, encoded anew from its spooled file (parse.encode_form called again).
            audio = b"RIFF" + b"a" * (2 << 20)  # past the form's 1 MiB in memory: spooled
            form, headers = upload(audio)
            r.swap.exited = 1
            answer = await r.client.post("/v1/audio/transcriptions", content=form, headers=headers)
        assert answer.status_code == 200
        (_, first), (_, again) = r.swap.seen[4:]
        assert audio in first and audio in again and b'name="model"\r\n\r\nstt\r\n' in again
        assert first.split(b"\r\n", 1)[0] != again.split(b"\r\n", 1)[0]  # a boundary of its own: encoded again


# Draining.

@pytest.mark.anyio
async def test_drain_marks_draining_and_answers_drained_at_zero_under_one_lock():
    async with rig(ready=[CODER]) as r:
        first_done, second_done = asyncio.Event(), asyncio.Event()
        r.swap.holds = [first_done, second_done]
        with anyio.fail_after(BOUND_S):
            first, second = r.ask(), r.ask()
            await until(lambda: r.count() == 2)
            r.gate.send(drain(CODER, "d1", "unload"))
            await until(lambda: CODER in r.link.draining)
            third = r.ask()
            await asyncio.sleep(0.2)
            assert not third.done() and len(r.swap.seen) == 2 and r.gate.drained == []  # it waits
            first_done.set()
            assert (await first).status_code == 200
            await until(lambda: r.count() == 1)
            await asyncio.sleep(0.2)
            assert r.gate.drained == []  # one still in flight
            second_done.set()
            assert (await second).status_code == 200
            await until(lambda: r.gate.drained != [])
            await asyncio.sleep(0.2)
            assert r.gate.drained == [{"model": CODER, "drain_id": "d1"}]  # one, at 0
            assert not third.done() and len(r.swap.seen) == 2
            await settled(third)
        assert all(counts["count"] >= 0 for each in r.gate.snapshots for counts in each["models"].values())
        assert any(each["draining"] == [CODER] for each in r.gate.snapshots)


@pytest.mark.anyio
async def test_a_request_for_a_draining_model_waits_then_gets_draining():
    async with rig(ready=[CODER]) as r:
        with anyio.fail_after(BOUND_S):
            r.gate.send(drain(CODER, "d1", "unload"))
            await until(lambda: r.gate.drained == [{"model": CODER, "drain_id": "d1"}])
            asked = r.ask()
            await asyncio.sleep(0.2)
            assert not asked.done()
            r.now[0] = T + DAN_WAIT  # never unloaded, and Dan's 30 s pass
            answer = await asked
            worded = r.app.refusal("draining", "dan-mac", model_label="the coder", drain_for="unload", inflight=0)
            assert (answer.status_code, answer.json()) == (409, worded.body())
            assert answer.headers["retry-after"] == "60" and answer.headers["x-should-retry"] == "false"
            # The front's own refusal, so it reaches the gate too.
            await until(lambda: len(r.gate.refused) == 1)
        assert r.gate.refused == [{"at": T + DAN_WAIT, "code": "draining", "model": CODER, "key": "dan-mac",
                                   "message": worded.message}]
        assert r.swap.seen == [] and r.gate.admits == []


@pytest.mark.anyio
async def test_unloaded_sends_waiting_requests_through_admission():
    async with rig(ready=[CODER]) as r:
        r.gate.hold = asyncio.Event()
        with anyio.fail_after(BOUND_S):
            r.gate.send(drain(CODER, "d1", "idle"))  # idle, with nothing in flight: drained
            await until(lambda: r.gate.drained != [])
            asked = r.ask()
            await asyncio.sleep(0.1)
            assert r.gate.admits == [] and not asked.done()
            r.gate.send(unloaded(CODER))
            await until(lambda: len(r.gate.admits) == 1)
            assert r.gate.admits[0]["deadline"] == T + DAN_WAIT
            r.gate.hold.set()
            answer = await asked
        assert answer.status_code == 200 and len(r.swap.seen) == 1


@pytest.mark.anyio
async def test_undrain_returns_the_model_to_serving():
    async with rig(ready=[CODER]) as r:
        with anyio.fail_after(BOUND_S):
            r.gate.send(drain(CODER, "d1", "make-room"))
            await until(lambda: r.gate.drained != [])
            asked = r.ask()
            await asyncio.sleep(0.1)
            assert not asked.done()
            r.gate.send(undrain(CODER, "d1"))
            answer = await asked
        assert answer.status_code == 200 and len(r.swap.seen) == 1 and r.gate.admits == []


@pytest.mark.anyio
async def test_a_drained_model_stays_held_while_the_gate_is_gone():
    async with rig(ready=[CODER]) as r:
        with anyio.fail_after(BOUND_S):
            r.gate.send(drain(CODER, "d1", "unload"))
            await until(lambda: r.gate.drained != [])
            await r.gate.stop("crash")
            await until(lambda: not r.link.connected)
            assert CODER in r.link.draining  # no undrain of the front's own
            waiting = r.ask()
            await asyncio.sleep(0.2)
            assert not waiting.done()
            r.now[0] = T + DAN_WAIT
            answer = await waiting
            assert (answer.status_code, answer.json()) == (503, r.app.refusal("gate_down", "dan-mac").body())
            assert r.swap.seen == []
            # The gate back, its unloaded: the next request goes through admission.
            r.gate.state["ready"] = []
            await r.gate.start()
            await until(lambda: r.link.connected)
            r.gate.send(unloaded(CODER))
            await until(lambda: CODER not in r.link.draining)
            answer = await r.post()
        assert answer.status_code == 200 and [admit["model"] for admit in r.gate.admits] == [CODER]

    # A drain not yet drained when the call drops: the model served again at once.
    async with rig(ready=[CODER]) as r:
        r.swap.hold = asyncio.Event()
        with anyio.fail_after(BOUND_S):
            first = r.ask()
            await until(lambda: r.count() == 1)
            r.gate.send(drain(CODER, "d2", "unload"))
            await until(lambda: CODER in r.link.draining)
            await r.gate.stop("crash")
            await until(lambda: not r.link.connected)
            assert CODER not in r.link.draining
            second = r.ask()
            await until(lambda: len(r.swap.seen) == 2)
            r.swap.hold.set()
            assert [answer.status_code for answer in await asyncio.gather(first, second)] == [200, 200]
        assert r.gate.drained == [] and r.gate.admits == []


@pytest.mark.anyio
async def test_an_idle_drain_with_a_request_in_flight_answers_busy():
    async with rig(ready=[CODER]) as r:
        r.swap.hold = asyncio.Event()
        with anyio.fail_after(BOUND_S):
            first = r.ask()
            await until(lambda: r.count() == 1)
            r.gate.send(drain(CODER, "d1", "idle"))
            await until(lambda: r.gate.busy == [{"model": CODER, "drain_id": "d1"}])
            assert CODER not in r.link.draining
            second = r.ask()
            await until(lambda: len(r.swap.seen) == 2)  # forwarded at once
            r.swap.hold.set()
            assert [answer.status_code for answer in await asyncio.gather(first, second)] == [200, 200]
            await asyncio.sleep(0.1)
        assert r.gate.drained == []


@pytest.mark.anyio
async def test_draining_is_worded_by_its_reason():
    async with rig(ready=[CODER]) as r:
        for n, why in enumerate(("unload", "make-room")):
            with anyio.fail_after(BOUND_S):
                r.gate.send(drain(CODER, f"d{n}", why))
                await until(lambda: CODER in r.link.draining)
                began = r.now[0]
                dan, agent = r.ask(key="k1"), r.ask(key="k2")
                await asyncio.sleep(0.1)
                r.now[0] = began + DAN_WAIT
                dan_answer = await dan
                assert not agent.done()
                r.now[0] = began + AGENT_WAIT
                agent_answer = await agent
                for answer, name in ((dan_answer, "dan-mac"), (agent_answer, "agent")):
                    worded = r.app.refusal("draining", name, model_label="the coder", drain_for=why, inflight=0)
                    assert (answer.status_code, answer.json()) == (409, worded.body())
                r.gate.send(undrain(CODER, f"d{n}"))
                await until(lambda: CODER not in r.link.draining)
            if why == "unload":  # Task 6's neutral sentence
                assert dan_answer.json()["error"]["message"] == ("The coder is being unloaded, and your 30 s ran "
                                                                 "out. Try again in a minute.")
            else:  # make-room's, in each key's words
                assert "for make-room" in dan_answer.json()["error"]["message"]
                assert "into the room make-room holds for you" in dan_answer.json()["error"]["message"]
                assert "Dan runs `spark make-room --done`" in agent_answer.json()["error"]["message"]


@pytest.mark.anyio
async def test_a_drained_model_held_across_the_gates_restart_is_settled_under_its_saved_id():
    # The restarted gate resumes the drain under its saved id, or undrains it under that id (Task 18).
    async with rig(ready=[CODER]) as r:
        with anyio.fail_after(BOUND_S):
            r.gate.send(drain(CODER, "d1", "unload"))
            await until(lambda: r.gate.drained != [])
            await r.gate.stop("crash")
            await until(lambda: not r.link.connected)
            await r.gate.start()
            await until(lambda: r.link.connected)
            assert CODER in r.link.draining  # held across the restart
            r.gate.send(drain(CODER, "d1", "unload"))  # a resumed drain under its id: answered drained again
            await until(lambda: len(r.gate.drained) == 2)
            assert r.gate.drained == [{"model": CODER, "drain_id": "d1"}] * 2
            waiting = r.ask()
            r.gate.send(undrain(CODER, "not-held"))  # an undrain under an id it doesn't hold changes nothing
            await asyncio.sleep(0.2)
            assert not waiting.done() and CODER in r.link.draining
            r.gate.send(undrain(CODER, "d1"))  # the undrain under its saved id releases it
            answer = await waiting
        assert answer.status_code == 200 and r.gate.admits == []


@pytest.mark.anyio
async def test_a_drain_the_front_doesnt_hold_is_adopted():
    # As after the front's own restart, when it holds nothing: the gate sends a drain under the id it saved. The front
    # marks the model drained, lets its requests in flight finish, and answers drained with that id.
    async with rig(ready=[CODER]) as r:
        r.swap.hold = asyncio.Event()
        with anyio.fail_after(BOUND_S):
            first = r.ask()
            await until(lambda: r.count() == 1)
            r.gate.send(drain(CODER, "saved-id", "unknown"))
            await until(lambda: CODER in r.link.draining)
            waiting = r.ask()
            await asyncio.sleep(0.1)
            assert not waiting.done() and r.gate.drained == []
            r.swap.hold.set()
            assert (await first).status_code == 200
            await until(lambda: r.gate.drained == [{"model": CODER, "drain_id": "saved-id"}])
            r.gate.send(undrain(CODER, None))  # no id: whatever drain it holds for the model
            answer = await waiting
        assert answer.status_code == 200 and len(r.swap.seen) == 2


@pytest.mark.anyio
async def test_a_held_model_is_released_by_unloaded_after_the_gates_damaged_start():
    # A gate started fresh from a damaged state file lost each drain's id. The front's first snapshot says what it
    # holds, and the gate finishes each as a drain with why `unknown` (Task 18): one /running shows ready drained under
    # a new id, which the front adopts; one gone, unloaded at once.
    async with rig(ready=[CODER, GEMMA]) as r:
        with anyio.fail_after(BOUND_S):
            r.gate.send(drain(CODER, "d1", "unload"))
            r.gate.send(drain(GEMMA, "d2", "make-room"))
            await until(lambda: len(r.gate.drained) == 2)
            await r.gate.stop("crash")
            await until(lambda: not r.link.connected)
            before = len(r.gate.snapshots)
            await r.gate.start()
            await until(lambda: len(r.gate.snapshots) > before)
            assert sorted(r.gate.snapshots[before]["draining"]) == sorted([CODER, GEMMA])
            r.gate.send(drain(GEMMA, "new-id", "unknown"))
            await until(lambda: {"model": GEMMA, "drain_id": "new-id"} in r.gate.drained)
            r.gate.send(unloaded(CODER))
            await until(lambda: CODER not in r.link.draining)
            assert GEMMA in r.link.draining
            coder = await r.post(CODER)
            r.gate.send(unloaded(GEMMA))
            await until(lambda: GEMMA not in r.link.draining)
            gemma = await r.post(GEMMA)
        assert (coder.status_code, gemma.status_code) == (200, 200)
        assert [admit["model"] for admit in r.gate.admits] == [CODER, GEMMA]  # each through admission


# Apply's hold.

@pytest.mark.anyio
async def test_hold_all_holds_every_new_request_until_release_all():
    async with rig(ready=[GEMMA]) as r:
        with anyio.fail_after(BOUND_S):
            r.gate.send({"op": "hold_all", "lapse_s": 60})
            await until(lambda: r.link.holding)
            held = r.ask(GEMMA)
            await asyncio.sleep(0.2)
            assert not held.done() and r.swap.seen == [] and r.gate.admits == []  # held, though loaded
            r.gate.send({"op": "release_all"})
            answer = await held
            assert answer.status_code == 200
            assert [admit["model"] for admit in r.gate.admits] == [GEMMA]  # through admission
            r.gate.send({"op": "hold_all", "lapse_s": 60})
            await until(lambda: r.link.holding)
            late = r.ask(GEMMA)
            await asyncio.sleep(0.1)
            r.now[0] = T + DAN_WAIT
            answer = await late
        assert (answer.status_code, answer.json()) == (409, r.app.refusal("restarting", "dan-mac").body())
        assert answer.headers["retry-after"] == "60" and len(r.swap.seen) == 1


@pytest.mark.anyio
async def test_a_restarted_front_holds_again_from_hello():
    async with rig(ready=[GEMMA], applying=True) as r:
        with anyio.fail_after(BOUND_S):
            assert r.link.holding  # its first hello says applying
            held = r.ask(GEMMA)
            await asyncio.sleep(0.1)
            await r.gate.stop("crash")
            await until(lambda: not r.link.connected)
            r.now[0] = T + 10  # the gate's channel gone 10 s
            await asyncio.sleep(0.1)
            assert not held.done()
            r.now[0] = T + DAN_WAIT
            answer = await held
            assert (answer.status_code, answer.json()) == (409, r.app.refusal("restarting", "dan-mac").body())
            again = r.ask(GEMMA)  # still held: gone 30 s, within the hold's 60
            await asyncio.sleep(0.1)
            assert not again.done()
            r.gate.hello["applying"] = False
            await r.gate.start()
            answer = await again
        assert answer.status_code == 200
        assert [admit["model"] for admit in r.gate.admits] == [GEMMA] and len(r.swap.seen) == 1


@pytest.mark.anyio
async def test_a_held_front_drops_the_hold_when_the_gate_stays_gone():
    async with rig(ready=[GEMMA], swap_ready=[GEMMA], applying=True, lapse_s=60) as r:
        with anyio.fail_after(BOUND_S):
            assert r.link.holding
            await r.gate.stop("crash")
            r.gate.refuse()  # the gate failing at start: its channel refused
            await until(lambda: not r.link.connected)
            r.now[0] = T + 60
            assert r.link.holding  # not yet longer than its lapse_s
            r.now[0] = T + 61
            gemma = await r.post(GEMMA)
            coder = await r.post(CODER, key="k2")
        assert not r.link.holding
        assert (gemma.status_code, gemma.content) == (200, DELTA + DONE)
        assert (coder.status_code, coder.json()) == (503, r.app.refusal("gate_down", "agent").body())


# What the front tells the gate.

@pytest.mark.anyio
async def test_the_fronts_own_refusals_reach_the_gate():
    async with rig() as r:
        with anyio.fail_after(BOUND_S):
            answer = await r.post("qwen3.6-35b-a3b", key="k2")
            assert answer.status_code == 404
            words = answer.json()["error"]["message"]
            assert "as `agent`" in words  # in the asking key's words
            await until(lambda: len(r.gate.refused) == 1)
            assert r.gate.refused == [{"at": T, "code": "model_not_found", "model": "qwen3.6-35b-a3b", "key": "agent",
                                       "message": words}]
            route = await r.client.get("/v1/elsewhere", headers=auth())
            assert route.status_code == 404
            await until(lambda: len(r.gate.refused) == 2)
            assert {name: r.gate.refused[1][name] for name in ("code", "model", "key")} == {
                "code": "route_not_served", "model": None, "key": "dan-mac"}
            # A client's long name is cut to what the gate keeps, so the post stays inside the gate's 64 KiB.
            assert gatelink.REFUSED_TEXT_MAX == gate_app.TEXT_MAX == 2000
            long_name = "m" * 100_000
            assert (await r.post(long_name)).status_code == 404
            await until(lambda: len(r.gate.refused) == 3)
            cut = r.gate.refused.pop()
            assert len(cut["model"]) == len(cut["message"]) == 2000
            assert cut["model"] == "m" * 1999 + "…" and cut["message"].endswith("…")
            # With the gate down, none: not even one left waiting in its socket for it to come back.
            await r.gate.stop("crash")
            await until(lambda: not r.link.connected)
            assert (await r.post("qwen3.6-35b-a3b")).status_code == 404
            await r.gate.start()
            await until(lambda: r.link.connected)
            await asyncio.sleep(0.2)
        assert len(r.gate.refused) == 2


@pytest.mark.anyio
async def test_a_model_the_gate_adds_is_served_without_a_restart():
    without = [entry for entry in MODELS if entry["name"] != CODER]
    async with rig(models=without, swap_ready=NAMES) as r:
        with anyio.fail_after(BOUND_S):
            before = await r.post(CODER)
            assert before.status_code == 404 and f"the coder ({CODER})" not in before.json()["error"]["message"]
            read = r.link.events_read
            r.gate.send({**r.gate.state, "models": MODELS})
            await r.heard(read + 1)
            answer = await r.post(CODER)
            assert answer.status_code == 200 and [admit["model"] for admit in r.gate.admits] == [CODER]
            listed = (await r.post("nonesuch")).json()["error"]["message"]
            assert f"the coder ({CODER})" in listed
            # The gate then down: the last list kept.
            await r.gate.stop("crash")
            await until(lambda: not r.link.connected)
            r.now[0] = T + GATE_DOWN_AFTER_S
            kept = (await r.post("nonesuch")).json()["error"]["message"]
        assert kept == listed


@pytest.mark.anyio
async def test_in_flight_snapshots_are_whole_never_deltas():
    async with rig(ready=[CODER]) as r:
        r.swap.hold = asyncio.Event()
        loop = asyncio.get_running_loop()
        with anyio.fail_after(BOUND_S):
            await until(lambda: len(r.gate.snapshots) >= 1)  # one as the call opens
            quiet = len(r.gate.snapshots)
            await asyncio.sleep(1.5)
            assert len(r.gate.snapshots) > quiet  # at least one a second, with nothing changing
            asked = r.ask()
            began = loop.time()
            await until(lambda: any(each["models"][CODER]["count"] == 1 for each in r.gate.snapshots))
            assert loop.time() - began < 0.5  # one on the change, not at the next second
            r.swap.hold.set()
            await asked
            ended = loop.time()
            await until(lambda: r.gate.snapshots[-1]["models"][CODER]["count"] == 0)
            assert loop.time() - ended < 0.5
        for each in r.gate.snapshots:
            assert set(each["models"]) == set(NAMES)  # every model, with its count
            assert all(counts["count"] >= 0 for counts in each["models"].values())
        seqs = [each["seq"] for each in r.gate.snapshots]
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)


# The client.

@pytest.mark.anyio
async def test_nothing_is_written_to_a_waiting_clients_stream():
    async with rig() as r:
        r.gate.hold = asyncio.Event()
        body = json.dumps(chat()).encode()
        with anyio.fail_after(BOUND_S):
            reader, writer = await asyncio.open_connection("127.0.0.1", r.client.base_url.port)
            try:
                writer.write(b"POST /v1/chat/completions HTTP/1.1\r\nHost: spark\r\nAuthorization: Bearer k1\r\n"
                             b"Content-Type: application/json\r\nContent-Length: %d\r\n\r\n%s" % (len(body), body))
                await writer.drain()
                await until(lambda: len(r.gate.admits) == 1)
                with pytest.raises(TimeoutError):  # no byte, headers included, while it waits
                    await asyncio.wait_for(reader.read(1), 1)
                r.gate.hold.set()
                head = await reader.readuntil(b"\r\n\r\n")
            finally:
                writer.close()
        assert head.startswith(b"HTTP/1.1 200 ")


@pytest.mark.anyio
async def test_a_client_gone_drops_its_admit_call():
    async with rig() as r:
        r.gate.hold = asyncio.Event()
        with anyio.fail_after(BOUND_S):
            asked = r.ask()
            await until(lambda: len(r.gate.admits) == 1)
            await settled(asked)  # the client disconnects while it waits
            await until(lambda: r.gate.closed_admits == [r.gate.admits[0]["request_id"]])
            await until(lambda: r.lines == [f"front: dan-mac {CODER} gone 0.0s"])
        assert r.swap.seen == [] and r.app.caps.waiting("dan-mac") == 0


@pytest.mark.anyio
async def test_an_upload_past_the_cap_never_asks_the_gate():
    # The upload cap comes before admission (the controller's ruling, at Task 21's review): an upload the cap refuses
    # never asks the gate, and one waiting for its load holds its slot.
    async with rig(uploads_at_once=1) as r:
        r.gate.hold = asyncio.Event()
        form, headers = upload()
        with anyio.fail_after(BOUND_S):
            first = asyncio.create_task(r.client.post("/v1/audio/transcriptions", content=form, headers=headers))
            await until(lambda: len(r.gate.admits) == 1)
            second = await r.client.post("/v1/audio/transcriptions", content=form, headers=headers)
            assert (second.status_code, second.json()["error"]["code"]) == (429, front_app.UPLOADS_BUSY_CODE)
            assert len(r.gate.admits) == 1
            r.gate.hold.set()
            assert (await first).status_code == 200


@pytest.mark.anyio
async def test_the_journal_line_says_why_a_request_was_cut_or_refused():
    async with rig(ready=[CODER], cut_why_s=2.0) as r:
        with anyio.fail_after(BOUND_S):
            # Refused by the gate: its code.
            r.gate.answer = {"ok": False, "code": "no_fit", "status": 409, "message": "M", "retry_after_s": 30}
            assert (await r.post(GEMMA)).status_code == 409
            await until(lambda: len(r.lines) == 1)
            assert r.lines[0] == f"front: dan-mac {GEMMA} 409 0.0s (no_fit)"
            # Cut while the gate's events show the model leave llama-swap.
            r.swap.cut = True
            with pytest.raises(httpx.RemoteProtocolError):
                async with r.client.stream("POST", "/v1/chat/completions", json=chat(), headers=auth()) as response:
                    async for _ in response.aiter_raw():
                        pass
            r.gate.send({**r.gate.state, "ready": []})
            await until(lambda: len(r.lines) == 2)
            assert r.lines[1] == f"front: dan-mac {CODER} cut 0.0s ({gatelink.CUT_WHY})"
            # Cut with no such event since it was forwarded: `cut`, no more.
            read = r.link.events_read
            r.gate.send(r.gate.state)
            await r.heard(read + 1)
            r.now[0] = T + 1  # forwarded after the coder last left
            with pytest.raises(httpx.RemoteProtocolError):
                async with r.client.stream("POST", "/v1/chat/completions", json=chat(), headers=auth()) as response:
                    async for _ in response.aiter_raw():
                        pass
            await until(lambda: len(r.lines) == 3)
            assert r.lines[2] == f"front: dan-mac {CODER} cut 0.0s"
            # The gate down: gate_down.
            await r.gate.stop("crash")
            await until(lambda: not r.link.connected)
            r.now[0] = T + 1 + GATE_DOWN_AFTER_S
            assert (await r.post(EMBED)).status_code == 503
            await until(lambda: len(r.lines) == 4)
        assert r.lines[3] == f"front: dan-mac {EMBED} 503 0.0s (gate_down)"

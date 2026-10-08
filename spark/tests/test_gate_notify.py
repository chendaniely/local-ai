"""The gate's notifications (website/design/phase-2a.md, Task 14): one per event, keyed by its type and its event key,
at the registry's priorities; a burst of one refusal goes at once, then as one with its count; ntfy out of reach
never stalls the gate; the token travels only in a header; the brake's events become brake_fired and its follow-ups;
memory_warning once per fall."""

import asyncio
import contextlib
import logging
import re
import socket
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import anyio
import pytest

from spark import messages
from spark.brakeevents import EVENTS_FILE, BrakeEvent, append_event
from spark.gate.notify import (MemoryWarning, Notifier, NtfyError, NtfyPublisher, ingest_brake_events,
                               quiet_http_loggers)
from spark.gate.state import BrakeMark, GateState, ModelRecord, load_state, notified_key, save_state
from spark.gateproto import FIRED_ALONE_S, NTFY_LATE_KEEP_S, NTFY_LATE_RETRY_S, NTFY_QUEUE_MAX, RELEASE_AFTER_S
from spark.hold import Hold, write_hold
from spark.messages import Moment, Notification
from spark.registry import NOTIFICATION_TYPES, load_registry

STACK_REGISTRY = Path(__file__).resolve().parents[2] / "stack" / "models.yaml"
REGISTRY = load_registry(STACK_REGISTRY)
# The tests' own time zone, seven hours behind UTC, as test_messages.py has it.
TZ = "PDT+7"
ZONE = timezone(timedelta(hours=-7))
BOUND_S = 5  # every async test's bound
# Stand-ins, never a real address, topic or token (CLAUDE.md: the repo is public).
TOPIC = "stand-in-topic-q7"
TOKEN = "stand-in-token-k3"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def local_zone(monkeypatch):
    """The clock read in ZONE, on a box called brightroar, as the notifications' words read them."""
    monkeypatch.setenv("TZ", TZ)
    time.tzset()
    monkeypatch.setattr(socket, "gethostname", lambda: "brightroar.local")
    yield
    monkeypatch.undo()
    time.tzset()


def at(hour: int, minute: int, second: float = 0) -> float:
    """A moment on the tests' day, in Unix seconds, as the gate keeps time."""
    return datetime(2026, 10, 8, hour, minute, tzinfo=ZONE).timestamp() + second


def name_of(label: str) -> str:
    return next(name for name, model in REGISTRY.models.items() if model.label == label)


CODER, GEMMA, EMBED = name_of("the coder"), name_of("Gemma"), name_of("the embeddings")


class Clock:
    """The injected clock: Unix seconds, moved by hand."""

    def __init__(self, now: float):
        self.now = now

    def __call__(self) -> float:
        return self.now


class Published:
    """A recording publish: each Notification it was given, in order. `hang`: it never returns, as an ntfy out of
    reach would not."""

    def __init__(self):
        self.sent: list[Notification] = []
        self.hang = False
        self.calls = 0

    async def __call__(self, n: Notification) -> None:
        self.calls += 1
        if self.hang:
            await asyncio.Event().wait()
        self.sent.append(n)

    @property
    def texts(self) -> list[str]:
        return [n.message for n in self.sent]


@contextlib.asynccontextmanager
async def serving(notifier: Notifier):
    """The notifier's own task, running for the block."""
    async with anyio.create_task_group() as group:
        group.start_soon(notifier.run)
        try:
            yield
        finally:
            group.cancel_scope.cancel()


def text(kind: str, registry=REGISTRY, **fields) -> str:
    return messages.notification(kind, registry, **fields).message


# What each type the gate sends takes (phase-2a.md, Task 7's fields): the failure notifier sends gate_down, front_down
# and brake_down; llama_swap_down is the gate's too, when llama-swap stops answering with its unit up.
NO_FIT_AGENT = Moment(needed_gib=41, free_gib=12, available_gib=36, reserve_gib=24, owed_gib=0, holders=[],
                      model_label="the coder", model_command="coder", key_label="agent", words="agent",
                      names_processes=False, wait_s=600)
FIRED = {"at": at(3, 12), "available_gib": 19.6, "line_gib": 20, "unloaded": [("the coder", "starting")],
         "release_waits_for_dan": False, "release_after_s": RELEASE_AFTER_S}
SUSPECT = {"model_label": "the coder", "key_label": "agent", "fired_at": at(3, 12), "command": "coder"}
FAILED = {"model_label": "the coder", "command": "coder", "engine_said": "failed to load model"}
SENT_BY_THE_GATE = {
    "brake_fired": FIRED,
    "brake_needs_release": {"fired_at": at(2, 58)},
    "llama_swap_down": {"at": at(9, 14)},
    "back_up": {"unit": "front", "down_s": 12},
    "refused": {"code": "no_fit", "moment": NO_FIT_AGENT},
    "footprint_suspect": SUSPECT,
    "load_failed": FAILED,
    "brake_released": {"at": at(3, 40), "available_gib": 64, "reloaded": ["Gemma"], "loading_label": "the coder"},
    "room_hold_ended": {"why": "--done", "unused_gib": 40, "total_gib": 40, "reloading": [], "next_label": None},
    "resident_waiting": {"label": "Gemma", "needed_gib": 32, "free_gib": 9, "after": "hold"},
    "apply_restarted": {"at": at(14, 2), "reloaded": ["Gemma"], "on_demand": ["the coder"]},
    "load_started": {"label": "the coder", "key_label": "pi on the Mac", "last_s": 24},
    "loaded": {"label": "the coder", "seconds": 24},
    "unloaded": {"label": "the coder", "why": "idle", "idle_min": 60},
    "waiting": {"label": "the coder", "key_label": "pi on the Mac", "why": "memory", "wait_s": 30, "needed_gib": 41,
                "free_gib": 18},
    "pin_ended": {"label": "the coder", "at": at(18, 0), "idle_min": 60},
    "memory_warning": {"available_gib": 27.4, "warn_gib": 28, "brake_gib": 20},
}
# Every type at a priority the stack's registry doesn't give it, so a priority the notifier fixed itself shows.
_OTHER = {"high": "low", "default": "high", "low": "default"}
ROTATED = replace(REGISTRY, notifications={kind: _OTHER[REGISTRY.notifications[kind]] for kind in NOTIFICATION_TYPES})


@pytest.mark.anyio
async def test_every_type_goes_at_its_registry_priority():
    with anyio.fail_after(BOUND_S):
        published = Published()
        notifier = Notifier(published, ROTATED, GateState(), Clock(at(9, 0)))
        async with serving(notifier):
            for kind, fields in SENT_BY_THE_GATE.items():
                notifier.emit(kind, f"event:{kind}", **fields)
            await notifier.drained()
        # Each once; the high alerts first (the queue's order, the controller's ruling at Task 14's review).
        assert sorted(n.type for n in published.sent) == sorted(SENT_BY_THE_GATE)
        highs = [n.type for n in published.sent if n.priority == "high"]
        assert [n.type for n in published.sent][:len(highs)] == highs
        for n in published.sent:
            assert n.priority == ROTATED.notifications[n.type] != REGISTRY.notifications[n.type]
            assert n.message == text(n.type, ROTATED, **SENT_BY_THE_GATE[n.type])


@pytest.mark.anyio
async def test_an_off_type_sends_nothing():
    with anyio.fail_after(BOUND_S):
        published = Published()
        registry = replace(REGISTRY, notifications={**REGISTRY.notifications, "loaded": "off"})
        state = GateState()
        notifier = Notifier(published, registry, state, Clock(at(9, 0)))
        async with serving(notifier):
            notifier.emit("loaded", "load:t1", label="the coder", seconds=24)
            notifier.emit("load_started", "load:t2", label="Gemma", key_label=None, last_s=None)
            await notifier.drained()
        assert [n.type for n in published.sent] == ["load_started"]
        # A field the type doesn't take is still refused while it is off: the caller's bug shows in its tests.
        with pytest.raises(ValueError, match="^loaded's words take no idle_min"):
            notifier.emit("loaded", "load:t3", label="the coder", seconds=24, idle_min=60)


@pytest.mark.anyio
async def test_one_notification_per_event_even_across_a_restart(tmp_path):
    with anyio.fail_after(BOUND_S):
        published = Published()
        state = GateState()
        changed = []
        notifier = Notifier(published, REGISTRY, state, Clock(at(3, 40)), on_change=lambda: changed.append(1))
        released = SENT_BY_THE_GATE["brake_released"]
        async with serving(notifier):
            notifier.emit("brake_released", "release:3", **released)
            notifier.emit("brake_released", "release:3", **released)
            await notifier.drained()
        assert len(published.sent) == 1
        assert state.notified == {notified_key("brake_released", "release:3"): at(3, 40)}
        assert changed  # the core saves on every change
        save_state(tmp_path, state, now=at(3, 41))
        again, problem = load_state(tmp_path)
        assert problem is None
        restarted = Notifier(published, REGISTRY, again, Clock(at(3, 42)))
        async with serving(restarted):
            restarted.emit("brake_released", "release:3", **released)
            await restarted.drained()
        assert len(published.sent) == 1


@pytest.mark.anyio
async def test_a_burst_of_identical_refusals_collapses_with_a_count():
    with anyio.fail_after(BOUND_S):
        published = Published()
        clock = Clock(at(9, 12))
        notifier = Notifier(published, REGISTRY, GateState(), clock)
        suspect = {**SUSPECT, "fired_at": at(3, 12)}
        async with serving(notifier):
            notifier.emit("footprint_suspect", "refusal:r1", **suspect)
            await notifier.drained()
            assert published.texts == [text("footprint_suspect", **suspect)]  # the first goes at once
            for minute, request in ((14, "r2"), (16, "r3"), (18, "r4"), (20, "r5")):
                clock.now = at(9, minute)
                notifier.emit("footprint_suspect", f"refusal:{request}", **suspect)
                if minute == 14:  # a different code, for the same model and key, goes at once
                    clock.now = at(9, 15)
                    notifier.emit("refused", "refusal:n1", code="no_fit", moment=NO_FIT_AGENT)
            await notifier.drained()
            assert len(published.sent) == 2
            assert published.sent[1].type == "refused"
            clock.now = at(9, 21, 59)
            notifier.tick()
            await notifier.drained()
            assert len(published.sent) == 2  # the window is still open
            clock.now = at(9, 22)
            notifier.tick()
            await notifier.drained()
            assert published.texts[2] == "Didn't load the coder for agent 4 more times since 09:12: same reason."
            # The burst keeps its type and that type's priority.
            assert (published.sent[2].type, published.sent[2].priority) == ("footprint_suspect", "default")
            clock.now = at(9, 30)  # no_fit's window closes with nothing counted: nothing more
            notifier.tick()
            await notifier.drained()
            assert len(published.sent) == 3

            # A burst of load_failed collapses the same way: one per failed load, per model, whoever asked.
            clock.now = at(10, 0)
            notifier.emit("load_failed", "load-failed:t1", **FAILED)
            for minute, ticket in ((3, "t2"), (5, "t3")):
                clock.now = at(10, minute)
                notifier.emit("load_failed", f"load-failed:{ticket}", **{**FAILED, "engine_said": "CUDA error"})
            await notifier.drained()
            assert len(published.sent) == 4
            clock.now = at(10, 10)
            notifier.tick()
            await notifier.drained()
        assert published.texts[3] == text("load_failed", **FAILED)
        assert published.texts[4] == "The coder failed to load 2 more times since 10:00."
        assert published.sent[4].type == "load_failed"


@pytest.mark.anyio
async def test_front_refusals_burst_on_the_model_the_words_show():
    # A front refusal's burst passes the model it named: the registry's label for one the gate knows, the name the
    # client asked for when it reads as a model's (model_not_found), and nothing for route_not_served. Keyed on the
    # model the words show, a stream of names that don't read as one collapses (the deferred notes, at Task 7's
    # re-review).
    with anyio.fail_after(BOUND_S):
        published = Published()
        clock = Clock(at(9, 0))
        notifier = Notifier(published, REGISTRY, GateState(), clock)
        listed = [("the coder", CODER)]

        def not_found(name: str) -> Moment:
            return Moment(asked_name=name, models=listed, key_label="agent", words="agent")

        async with serving(notifier):
            for n, name in enumerate(("what is this model?", "http://x.example/m", "ask me\nanything")):
                notifier.emit("refused", f"refusal:a{n}", code="model_not_found", moment=not_found(name))
            notifier.emit("refused", "refusal:b1", code="model_not_found", moment=not_found("qwen-a"))
            notifier.emit("refused", "refusal:b2", code="model_not_found", moment=not_found("qwen-b"))
            notifier.emit("refused", "refusal:b3", code="model_not_found", moment=not_found("qwen-a"))
            for n in range(3):
                notifier.emit("refused", f"refusal:c{n}", code="route_not_served",
                              moment=Moment(key_label="agent", words="agent"))
            await notifier.drained()
            # The first of each goes at once: no name shown, qwen-a, qwen-b, and the address.
            assert len(published.sent) == 4
            clock.now = at(9, 10)
            notifier.tick()
            await notifier.drained()
        assert published.texts[4:] == [
            "Refused a request from agent 2 more times since 09:00: same reason.",
            "Refused qwen-a for agent 1 more time since 09:00: same reason.",
            "Refused a request from agent 2 more times since 09:00: same reason.",
        ]


@pytest.mark.anyio
async def test_a_refusal_with_no_key_goes_each_time():
    # One of Dan's own commands has no key, and a burst's words need one: each goes at once, never dropped.
    with anyio.fail_after(BOUND_S):
        published = Published()
        notifier = Notifier(published, REGISTRY, GateState(), Clock(at(9, 0)))
        command = replace(NO_FIT_AGENT, key_label="", words="dan", names_processes=True, wait_s=30)
        async with serving(notifier):
            for n in range(3):
                notifier.emit("refused", f"refusal:d{n}", code="no_fit", moment=command)
            await notifier.drained()
        assert len(published.sent) == 3


@pytest.mark.anyio
async def test_one_model_loading_twice_sends_two_loaded():
    with anyio.fail_after(BOUND_S):
        published = Published()
        notifier = Notifier(published, REGISTRY, GateState(), Clock(at(9, 0)))
        async with serving(notifier):
            notifier.emit("loaded", "load:t1", label="the coder", seconds=24)
            notifier.emit("loaded", "load:t2", label="the coder", seconds=22)
            notifier.emit("loaded", "load:t1", label="the coder", seconds=24)
            await notifier.drained()
        assert published.texts == ["Loaded the coder in 24 s.", "Loaded the coder in 22 s."]


@pytest.mark.anyio
async def test_two_types_never_share_a_key():
    # load_started and loaded both send load:<ticket id> for one load: keyed by the event key alone, loaded would be
    # dropped as a repeat (the controller's ruling after Task 7's fix round).
    with anyio.fail_after(BOUND_S):
        published = Published()
        state = GateState()
        notifier = Notifier(published, REGISTRY, state, Clock(at(9, 0)))
        async with serving(notifier):
            for _ in range(2):
                notifier.emit("load_started", "load:t1", label="the coder", key_label="pi on the Mac", last_s=24)
                notifier.emit("loaded", "load:t1", label="the coder", seconds=24)
            await notifier.drained()
        assert [n.type for n in published.sent] == ["load_started", "loaded"]
        assert set(state.notified) == {notified_key("load_started", "load:t1"), notified_key("loaded", "load:t1")}


@pytest.mark.anyio
async def test_one_failed_load_sends_one_alert():
    # Two requests joined one start that failed: each emits load_failed with the load's own key, load-failed:t1. One
    # publish, and inside an open burst window the load counts once, not once per request (Task 7's re-review).
    with anyio.fail_after(BOUND_S):
        published = Published()
        clock = Clock(at(10, 0))
        notifier = Notifier(published, REGISTRY, GateState(), clock)
        async with serving(notifier):
            notifier.emit("load_failed", "load-failed:t1", **FAILED)
            notifier.emit("load_failed", "load-failed:t1", **FAILED)
            await notifier.drained()
            assert len(published.sent) == 1
            clock.now = at(10, 10)
            notifier.tick()
            await notifier.drained()
            assert len(published.sent) == 1  # nothing was counted, so no burst

            clock.now = at(11, 0)
            notifier.emit("load_failed", "load-failed:t0", **FAILED)  # opens a window
            clock.now = at(11, 2)
            notifier.emit("load_failed", "load-failed:t2", **FAILED)
            notifier.emit("load_failed", "load-failed:t2", **FAILED)  # the second request of the same load
            clock.now = at(11, 10)
            notifier.tick()
            await notifier.drained()
        assert published.texts[1:] == [text("load_failed", **FAILED),
                                       "The coder failed to load 1 more time since 11:00."]


@pytest.mark.anyio
async def test_an_ntfy_out_of_reach_stalls_nothing_and_shows_failing_since(caplog):
    with anyio.fail_after(BOUND_S):
        published = Published()
        published.hang = True
        clock = Clock(at(8, 52))
        state = GateState()
        notifier = Notifier(published, REGISTRY, state, clock, timeout_s=0.05)
        async with serving(notifier):
            began = time.perf_counter()
            notifier.emit("loaded", "load:t1", label="the coder", seconds=24)
            assert time.perf_counter() - began < 0.010
            await notifier.drained()
            assert state.notify_failing_since == at(8, 52)  # the first failure's time
            clock.now = at(8, 53)
            notifier.emit("loaded", "load:t2", label="the coder", seconds=24)
            await notifier.drained()
            assert state.notify_failing_since == at(8, 52)  # still the first failure's
            published.hang = False
            clock.now = at(8, 54)
            notifier.emit("loaded", "load:t3", label="the coder", seconds=24)
            await notifier.drained()
        assert state.notify_failing_since is None  # the next success clears it
        assert published.calls == 3 and published.texts == ["Loaded the coder in 24 s."]
        assert "loaded" in caplog.text and "didn't answer" in caplog.text


@pytest.mark.anyio
async def test_a_publish_that_fails_counts_as_failing_and_never_stops_the_notifier():
    with anyio.fail_after(BOUND_S):
        clock = Clock(at(8, 52))
        state = GateState()
        sent = []

        async def refuses(n: Notification) -> None:
            if not sent:
                sent.append(None)
                raise NtfyError("ntfy answered HTTP 403")
            sent.append(n)

        notifier = Notifier(refuses, REGISTRY, state, clock)
        async with serving(notifier):
            notifier.emit("loaded", "load:t1", label="the coder", seconds=24)
            await notifier.drained()
            assert state.notify_failing_since == at(8, 52)
            notifier.emit("loaded", "load:t2", label="the coder", seconds=24)
            await notifier.drained()
        assert state.notify_failing_since is None and len(sent) == 2


async def _stand_in_ntfy(answer: bytes, seen: list):
    """A recording stand-in for ntfy on 127.0.0.1: each request's head and body, then `answer`."""
    async def handle(reader, writer):
        head = await reader.readuntil(b"\r\n\r\n")
        length = re.search(rb"(?im)^content-length: *(\d+)", head)
        body = await reader.readexactly(int(length.group(1))) if length else b""
        seen.append((head, body))
        writer.write(answer)
        await writer.drain()
        writer.close()

    return await asyncio.start_server(handle, "127.0.0.1", 0)


@pytest.fixture
def http_loggers():
    """httpx's and httpcore's levels, put back after the test."""
    kept = {name: logging.getLogger(name).level for name in ("httpx", "httpcore")}
    yield
    for name, level in kept.items():
        logging.getLogger(name).setLevel(level)


def test_the_publisher_leaves_the_loggers_to_the_gates_logging_setup(http_loggers):
    # The gate's logging setup quiets httpx and httpcore (Task 19), whether or not ntfy is set up; the publisher
    # changes nothing (the controller's ruling at Task 14's review).
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.NOTSET)
    NtfyPublisher("http://standin-nas.invalid", TOPIC, ("Authorization", f"Bearer {TOKEN}"))
    assert [logging.getLogger(name).level for name in ("httpx", "httpcore")] == [logging.NOTSET] * 2
    quiet_http_loggers()
    assert [logging.getLogger(name).level for name in ("httpx", "httpcore")] == [logging.WARNING] * 2


@pytest.mark.anyio
async def test_the_token_travels_only_in_a_header(caplog, http_loggers):
    caplog.set_level(logging.DEBUG)
    quiet_http_loggers()  # as the gate's logging setup does
    with anyio.fail_after(BOUND_S):
        seen: list = []
        server = await _stand_in_ntfy(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}", seen)
        port = server.sockets[0].getsockname()[1]
        url = f"http://127.0.0.1:{port}"
        publisher = NtfyPublisher(url + "/", TOPIC, ("Authorization", f"Bearer {TOKEN}"))
        n = Notification("brake_fired", "high", text("brake_fired", **FIRED))
        await publisher(n)

        refusing: list = []
        refuses = await _stand_in_ntfy(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
                                       refusing)
        refused_by = NtfyPublisher(f"http://127.0.0.1:{refuses.sockets[0].getsockname()[1]}", TOPIC,
                                   ("Authorization", f"Bearer {TOKEN}"))
        with pytest.raises(NtfyError) as answered:
            await refused_by(n)
        with socket.socket() as free:  # a port nothing listens on
            free.bind(("127.0.0.1", 0))
            gone = NtfyPublisher(f"http://127.0.0.1:{free.getsockname()[1]}", TOPIC,
                                 ("Authorization", f"Bearer {TOKEN}"))
        with pytest.raises(NtfyError) as unreachable:
            await gone(n)
        server.close()
        refuses.close()
        for each in (publisher, refused_by, gone):
            await each.aclose()

    (head, body), = seen
    lines = head.decode().split("\r\n")
    assert lines[0] == f"POST /{TOPIC} HTTP/1.1"  # the text goes to <url>/<topic>
    assert [line for line in lines if TOKEN in line] == [f"Authorization: Bearer {TOKEN}"]  # the header, only there
    assert any(line.lower() == "priority: high" for line in lines)
    assert body == n.message.encode()  # the text as the body
    assert TOKEN.encode() not in body
    assert str(answered.value) == "ntfy answered HTTP 403"
    for err in (answered.value, unreachable.value):  # no error names where ntfy is, its topic or its token
        assert err.__cause__ is None and (err.__context__ is None or err.__suppress_context__)
        for secret in (TOPIC, TOKEN, "127.0.0.1"):
            assert secret not in str(err) and secret not in repr(err)
    for secret in (TOPIC, TOKEN, url):  # and nothing logged them: not httpx's request lines either
        assert secret not in caplog.text
        assert secret not in repr(publisher)


def test_ntfy_settings_that_arent_right_are_refused_without_their_values():
    nas = "http://standin-nas.invalid"
    for url, topic, header, words in (("ftp://standin-nas.invalid", TOPIC, ("Authorization", "Bearer x"), "http"),
                                      (nas, "a/b", ("Authorization", "Bearer x"), "topic"),
                                      (nas, TOPIC, ("Bad Name", "Bearer x"), "header"),
                                      (nas, TOPIC, ("Authorization", "Bearer x\r\nX: y"), "header")):
        with pytest.raises(ValueError, match=words) as refused:
            NtfyPublisher(url, topic, header)
        assert "standin-nas" not in str(refused.value) and "Bearer" not in str(refused.value)


# The brake's events (Task 13's brakeevents), as the brake writes them (Task 23).
BOOT = "boot-1"


def event(kind: str, when: float, *, episode: int = 1, model: str | None = None, state: str | None = None,
          available: float = 19.6, line: float = 20, by_brake: bool = False, boot: str = BOOT) -> BrakeEvent:
    return BrakeEvent(seq=1, at=when, boot_id=boot, episode=episode, kind=kind, model=model, state=state,
                      available_gib=available, line_gib=line, sent_by_brake=by_brake)


def write(folder: Path, *events: BrakeEvent) -> list[BrakeEvent]:
    return [append_event(folder / EVENTS_FILE, e) for e in events]


def texts(found: list[tuple[str, str, dict]]) -> list[str]:
    return [text(kind, **fields) for kind, _, fields in found]


def plans_episode(folder: Path, *, by_brake: bool = False) -> list[BrakeEvent]:
    """Task 7's example: fired at 03:12 at 19.6; the coder unloaded while starting; at 03:13 Gemma and the embeddings,
    both idle."""
    return write(folder, event("fired", at(3, 12), by_brake=by_brake),
                 event("unload", at(3, 12, 1), model=CODER, state="starting", available=19.9, by_brake=by_brake),
                 event("unload", at(3, 13), model=GEMMA, state="idle", available=24.1, by_brake=by_brake),
                 event("unload", at(3, 13, 1), model=EMBED, state="idle", available=40.2, by_brake=by_brake))


def test_brake_events_become_brake_fired_and_its_follow_ups(tmp_path):
    written = plans_episode(tmp_path)
    state = GateState()
    found = ingest_brake_events(tmp_path / EVENTS_FILE, state, REGISTRY, boot_id=BOOT, now=at(3, 13, 2),
                                starts={CODER: 45.9})
    assert texts(found) == [
        "Brake on brightroar at 03:12: 19.6 GiB available, under the 20 GiB line. Unloaded the coder, which was "
        "loading; new loads are paused. They resume by themselves after 5 min above 28 GiB available.",
        "Brake, 03:13: also unloaded Gemma and the embeddings, both idle.",
    ]
    assert [(kind, key) for kind, key, _ in found] == [("brake_fired", f"brake:{BOOT}:1:{written[0].seq}"),
                                                       ("brake_fired", f"brake:{BOOT}:1:{written[2].seq}")]
    assert found[1][2]["follow_up"] is True
    # The coder was starting: it doesn't reload by itself (rule 5), marked with what it was seen using, its load's
    # fall from where it started (45.9) to the brake's reading as it unloaded it (19.9).
    mark = state.brake_marks[CODER]
    assert (mark.model, mark.at) == (CODER, at(3, 12)) and mark.seen_gib == pytest.approx(26.0)
    assert set(state.brake_marks) == {CODER}
    last = written[-1]
    assert state.brake_events_after == (BOOT, last.seq, last.at)
    # Each is read once.
    assert ingest_brake_events(tmp_path / EVENTS_FILE, state, REGISTRY, boot_id=BOOT, now=at(3, 14)) == []


def test_a_mark_with_no_reading_of_its_start_sees_nothing(tmp_path):
    # With no reading of where the load started, what it was seen using isn't known: None, never a guess (the
    # controller's ruling, at Task 14).
    plans_episode(tmp_path)
    state = GateState()
    ingest_brake_events(tmp_path / EVENTS_FILE, state, REGISTRY, boot_id=BOOT, now=at(3, 13, 2))
    assert state.brake_marks[CODER] == BrakeMark(CODER, at(3, 12), None)


def test_what_the_brake_sent_itself_is_not_sent_again(tmp_path):
    written = plans_episode(tmp_path, by_brake=True)
    state = GateState()
    assert ingest_brake_events(tmp_path / EVENTS_FILE, state, REGISTRY, boot_id=BOOT, now=at(3, 13, 2)) == []
    assert state.brake_events_after == (BOOT, written[-1].seq, written[-1].at)
    assert CODER in state.brake_marks  # the mark is the gate's state, whoever sent the alert


def test_a_drill_lines_event_is_worded_with_its_own_line(tmp_path):
    write(tmp_path, event("fired", at(3, 12), available=52.3, line=56),
          event("unload", at(3, 12, 1), model=GEMMA, state="idle", available=52.0, line=56))
    found = ingest_brake_events(tmp_path / EVENTS_FILE, GateState(), REGISTRY, boot_id=BOOT, now=at(3, 12, 2))
    (words,) = texts(found)
    assert "52.3 GiB available, under the 56 GiB line." in words and "20 GiB" not in words


def test_two_episodes_send_two_brake_fired(tmp_path):
    path = tmp_path / EVENTS_FILE
    state = GateState()
    write(tmp_path, event("fired", at(3, 12)), event("unload", at(3, 12, 1), model=CODER, state="idle"))
    first = ingest_brake_events(path, state, REGISTRY, boot_id=BOOT, now=at(3, 12, 2))
    state.last_auto_release_at = at(3, 40)  # the gate's automatic release between the two
    write(tmp_path, event("fired", at(3, 50), episode=2), event("unload", at(3, 50, 1), episode=2, model=GEMMA,
                                                                state="idle"))
    second = ingest_brake_events(path, state, REGISTRY, boot_id=BOOT, now=at(3, 50, 2))
    assert [kind for kind, _, _ in first + second] == ["brake_fired", "brake_fired"]
    assert not any(fields.get("follow_up") for _, _, fields in first + second)
    assert first[0][1] != second[0][1]
    # Within the hour after an automatic release, its own alert says the hold waits for Dan (rule 5).
    assert texts(second)[0].endswith("It fired within an hour of the automatic release at 03:40, so they stay paused "
                                     "until you release them: on the Spark, `make brake-release`.")


def test_after_a_damaged_start_a_brake_never_names_an_automatic_release(tmp_path):
    state = GateState(last_auto_release_at=at(3, 0), release_assumed=True)
    write(tmp_path, event("fired", at(3, 12)), event("unload", at(3, 12, 1), model=CODER, state="idle"))
    (words,) = texts(ingest_brake_events(tmp_path / EVENTS_FILE, state, REGISTRY, boot_id=BOOT, now=at(3, 12, 2)))
    assert words.endswith("New loads stay paused until you release them: the gate's saved state was damaged, so it "
                          "can't tell when the last automatic release was. On the Spark, `make brake-release` resumes "
                          "them.")


def test_a_lone_fired_event_waits_for_its_unload(tmp_path):
    # The brake writes fired, then its first unload, as two appends: a read between them waits for the unload, up to
    # FIRED_ALONE_S (the controller's ruling, at Task 14).
    path = tmp_path / EVENTS_FILE
    state = GateState()
    write(tmp_path, event("fired", at(3, 12)))
    assert ingest_brake_events(path, state, REGISTRY, boot_id=BOOT, now=at(3, 12, FIRED_ALONE_S - 0.5)) == []
    assert state.brake_events_after is None  # left to read again
    write(tmp_path, event("unload", at(3, 12, 3), model=CODER, state="starting"))
    found = ingest_brake_events(path, state, REGISTRY, boot_id=BOOT, now=at(3, 12, 4))
    assert texts(found) == [text("brake_fired", **FIRED)]


def test_a_brake_with_nothing_loaded_says_so_once_its_wait_is_over(tmp_path):
    write(tmp_path, event("fired", at(3, 12)))
    found = ingest_brake_events(tmp_path / EVENTS_FILE, GateState(), REGISTRY, boot_id=BOOT,
                                now=at(3, 12, FIRED_ALONE_S))
    assert texts(found) == [
        "Brake on brightroar at 03:12: 19.6 GiB available, under the 20 GiB line. Nothing of the stack's was loaded, "
        "so there was nothing to unload; new loads are paused. They resume by themselves after 5 min above 28 GiB "
        "available."]


def test_a_brake_whose_unload_went_unanswered_says_so_then_follows_up(tmp_path):
    path = tmp_path / EVENTS_FILE
    state = GateState(models={GEMMA: ModelRecord(name=GEMMA, footprint_gib=32, loaded_at=at(1, 0), load_fall_gib=27,
                                                 fall_flagged=False, rss_anon_at_load_gib=None, last_use=at(3, 0),
                                                 state="ready")})
    write(tmp_path, event("fired", at(3, 12)), event("warn", at(3, 12, 1)))  # a warn is no unload
    found = ingest_brake_events(path, state, REGISTRY, boot_id=BOOT, now=at(3, 12, 6))
    assert texts(found) == [
        "Brake on brightroar at 03:12: 19.6 GiB available, under the 20 GiB line; new loads are paused. It hasn't "
        "unloaded anything yet: llama-swap hasn't confirmed its unload. On the Spark, `make logs s=brake` shows why. "
        "They resume by themselves after 5 min above 28 GiB available."]
    write(tmp_path, event("unload", at(3, 13), model=GEMMA, state="idle"))
    assert texts(ingest_brake_events(path, state, REGISTRY, boot_id=BOOT, now=at(3, 13, 1))) == [
        "Brake, 03:13: also unloaded Gemma, which was idle."]


def test_memory_warning_once_per_fall():
    warning = MemoryWarning()
    assert [warning.check(reading, 28) for reading in (30, 27.4, 27, 26, 29, 27)] == [
        False, True, False, False, False, True]



# A brake from before a reboot (the controller's ruling at Task 14's review): the box froze, or was power-cycled,
# before the gate read the brake's events. The hold from that boot waits for Dan after the reboot (rule 5).
LATER_BOOT = "boot-2"


def gemma_loaded() -> dict:
    return {GEMMA: ModelRecord(name=GEMMA, footprint_gib=32, loaded_at=at(4, 0), load_fall_gib=27, fall_flagged=False,
                               rss_anon_at_load_gib=None, last_use=at(4, 0), state="ready")}


def test_a_brake_from_before_a_reboot_says_the_pause_still_stands(tmp_path):
    path = tmp_path / EVENTS_FILE
    written = write(tmp_path, event("fired", at(3, 12)), event("unload", at(3, 12, 1), model=CODER, state="starting"))
    write_hold(tmp_path, Hold("2026-10-08T03:12:00", "19.6 GiB available", (CODER,), BOOT, 1, CODER, 19.6))
    state = GateState(models=gemma_loaded(), boot_id=LATER_BOOT)  # restored on the new boot, Gemma reloaded
    found = ingest_brake_events(path, state, REGISTRY, boot_id=LATER_BOOT, now=at(4, 1))
    assert [(kind, key) for kind, key, _ in found] == [("brake_needs_release", f"brake-needs:{BOOT}:1")]
    assert texts(found) == ["After the reboot, new loads are still paused from the brake at 03:12 (19.6 GiB "
                            "available; it unloaded the coder). On the Spark, `make brake-release` resumes them."]
    # The stale fired is marked notified, never sent: no "resume by themselves", no unload unconfirmed.
    assert state.notified == {notified_key("brake_fired", f"brake:{BOOT}:1:{written[0].seq}"): at(4, 1)}
    assert CODER in state.brake_marks
    assert state.brake_events_after == (BOOT, written[-1].seq, written[-1].at)


def test_a_lone_fired_from_before_a_reboot_isnt_held_and_says_only_what_is_known(tmp_path):
    write(tmp_path, event("fired", at(3, 12)))
    write_hold(tmp_path, Hold("2026-10-08T03:12:00", "19.6 GiB available", (), BOOT, 1, None, 19.6))
    found = ingest_brake_events(tmp_path / EVENTS_FILE, GateState(models=gemma_loaded()), REGISTRY,
                                boot_id=LATER_BOOT, now=at(3, 12, 1))  # 1 s after it, but its boot has gone
    assert texts(found) == ["After the reboot, new loads are still paused from the brake at 03:12 (19.6 GiB "
                            "available). On the Spark, `make brake-release` resumes them."]


def test_a_brake_from_before_a_reboot_with_no_hold_left_sends_nothing(tmp_path):
    # Nothing is paused, so "still paused" would be untrue: the fired is marked, and nothing goes.
    written = write(tmp_path, event("fired", at(3, 12)), event("unload", at(3, 12, 1), model=GEMMA, state="idle"))
    state = GateState()
    assert ingest_brake_events(tmp_path / EVENTS_FILE, state, REGISTRY, boot_id=LATER_BOOT, now=at(4, 1)) == []
    assert notified_key("brake_fired", f"brake:{BOOT}:1:{written[0].seq}") in state.notified


@pytest.mark.anyio
async def test_the_reboots_alert_and_the_holds_are_one(tmp_path):
    # Task 17 sends brake_needs_release for the hold it finds after a reboot under the same key, so one goes.
    write(tmp_path, event("fired", at(3, 12)))
    write_hold(tmp_path, Hold("2026-10-08T03:12:00", "19.6 GiB available", (), BOOT, 1, None, 19.6))
    state = GateState(boot_id=LATER_BOOT)
    with anyio.fail_after(BOUND_S):
        published = Published()
        notifier = Notifier(published, REGISTRY, state, Clock(at(4, 1)))
        async with serving(notifier):
            for kind, key, fields in ingest_brake_events(tmp_path / EVENTS_FILE, state, REGISTRY,
                                                         boot_id=LATER_BOOT, now=at(4, 1)):
                notifier.emit(kind, key, **fields)
            notifier.emit("brake_needs_release", f"brake-needs:{BOOT}:1", fired_at=at(3, 12))
            await notifier.drained()
    assert [n.type for n in published.sent] == ["brake_needs_release"]


def test_an_episode_its_own_hold_dates_is_judged_by_that_hold(tmp_path):
    # rule 5 for the episode's own hold, read from the events file's folder: fired at 02:30 by the hold, 30 min after
    # the automatic release at 02:00, so it waits for Dan; another episode's hold is passed over for the event's time.
    path = tmp_path / EVENTS_FILE
    write(tmp_path, event("fired", at(3, 12)), event("unload", at(3, 12, 1), model=GEMMA, state="idle"))
    state = GateState(last_auto_release_at=at(2, 0))
    write_hold(tmp_path, Hold("2026-10-08T02:30:00", "19.6 GiB available", (GEMMA,), BOOT, 1, None, 19.6))
    (own,) = ingest_brake_events(path, state, REGISTRY, boot_id=BOOT, now=at(3, 12, 2))
    assert own[2]["release_waits_for_dan"] is True and own[2]["released_at"] == at(2, 0)
    write_hold(tmp_path, Hold("2026-10-08T02:30:00", "19.6 GiB available", (GEMMA,), BOOT, 7, None, 19.6))
    state = GateState(last_auto_release_at=at(2, 0))
    (other,) = ingest_brake_events(path, state, REGISTRY, boot_id=BOOT, now=at(3, 12, 2))
    assert other[2]["release_waits_for_dan"] is False  # 03:12 is 72 min after 02:00


# The publish queue (the controller's ruling at Task 14's review): at most NTFY_QUEUE_MAX; a high alert goes ahead of
# default and low and is never dropped; when it is full, the oldest low goes first, then the oldest default.
def low(n: int) -> tuple[str, str, dict]:
    return "loaded", f"load:l{n}", {"label": "the coder", "seconds": n}


def default(n: int) -> tuple[str, str, dict]:
    return "resident_waiting", f"resident:d{n}", {"label": "Gemma", "needed_gib": n, "free_gib": 1, "after": "hold"}


def high(n: int) -> tuple[str, str, dict]:
    return "brake_needs_release", f"brake-needs:h{n}", {"fired_at": at(2, 0, 60 * n)}


@pytest.mark.anyio
async def test_the_queue_is_bounded_and_high_alerts_go_first_and_are_never_dropped(caplog):
    with anyio.fail_after(BOUND_S):
        published = Published()
        notifier = Notifier(published, REGISTRY, GateState(), Clock(at(9, 0)))
        emits = [low(n) for n in range(1, 61)] + [default(n) for n in range(1, 51)] + [high(n) for n in range(1, 4)]
        for kind, key, fields in emits:  # nothing serves the queue yet: ntfy as good as out of reach
            notifier.emit(kind, key, **fields)
        assert notifier.dropped == 60 + 50 + 3 - NTFY_QUEUE_MAX  # the 13 oldest lows
        notifier.emit(*high(4)[:2], **high(4)[2])
        assert notifier.dropped == 14
        async with serving(notifier):
            await notifier.drained()
    sent = published.sent
    assert len(sent) == NTFY_QUEUE_MAX
    assert [n.type for n in sent[:4]] == ["brake_needs_release"] * 4  # the highs first, in their order
    assert [n.message for n in sent[:4]] == [text(*high(n)[:1], **high(n)[2]) for n in range(1, 5)]
    rest = [n.message for n in sent[4:]]
    assert rest == [text("loaded", **low(n)[2]) for n in range(15, 61)] + [text("resident_waiting", **default(n)[2])
                                                                            for n in range(1, 51)]
    assert "loaded notification was dropped" in caplog.text


def test_when_full_with_no_low_the_oldest_default_goes_and_highs_stay():
    notifier = Notifier(Published(), REGISTRY, GateState(), Clock(at(9, 0)))
    for n in range(1, NTFY_QUEUE_MAX + 1):
        notifier.emit(*default(n)[:2], **default(n)[2])
    notifier.emit(*low(1)[:2], **low(1)[2])  # the only low, and the queue full: it goes, before any default
    assert notifier.dropped == 1 and notifier.queued == NTFY_QUEUE_MAX
    notifier.emit(*default(101)[:2], **default(101)[2])  # the oldest default goes
    assert notifier.dropped == 2
    for n in range(1, NTFY_QUEUE_MAX + 2):
        notifier.emit(*high(n)[:2], **high(n)[2])
    assert notifier.dropped == 2 + NTFY_QUEUE_MAX  # every default and low went first
    assert notifier.queued == NTFY_QUEUE_MAX + 1  # and a high alert is never dropped, even past the bound


@pytest.mark.anyio
async def test_a_clean_stop_flushes_the_open_bursts_and_never_waits_on_a_hung_ntfy():
    with anyio.fail_after(BOUND_S):
        published = Published()
        clock = Clock(at(9, 12))
        notifier = Notifier(published, REGISTRY, GateState(), clock)
        async with serving(notifier):
            for n in range(3):
                notifier.emit("footprint_suspect", f"refusal:r{n}", **SUSPECT)
            await notifier.drained()
            assert len(published.sent) == 1
            clock.now = at(9, 15)  # the window is still open
            notifier.flush()
            assert await notifier.drained()
        assert published.texts[1] == "Didn't load the coder for agent 2 more times since 09:12: same reason."

        hung = Published()
        hung.hang = True
        stuck = Notifier(hung, REGISTRY, GateState(), clock, timeout_s=60)
        async with serving(stuck):
            stuck.emit("loaded", "load:t1", label="the coder", seconds=24)
            began = time.perf_counter()
            assert await stuck.drained(timeout_s=0.05) is False  # bounded: a stop goes on
            assert time.perf_counter() - began < 0.5


# A high alert ntfy didn't take (the controller's ruling at Task 14's review, under Dan's "err on more notifications"):
# kept, and sent once ntfy answers again while it is under NTFY_LATE_KEEP_S old, saying so. Default and low aren't.
@pytest.mark.anyio
async def test_a_high_alert_ntfy_missed_is_sent_late_once_it_answers():
    with anyio.fail_after(BOUND_S):
        published = Published()
        published.hang = True
        clock = Clock(at(3, 12))
        state = GateState()
        notifier = Notifier(published, REGISTRY, state, clock, timeout_s=0.05)
        fired = {**FIRED, "at": at(3, 12)}
        async with serving(notifier):
            notifier.emit("brake_fired", "brake:b:1:1", **fired)
            notifier.emit("loaded", "load:t1", label="the coder", seconds=24)
            await notifier.drained()
            assert published.sent == [] and state.notify_failing_since == at(3, 12)
            published.hang = False
            clock.now = at(3, 12, 30)  # before its own retry is due
            notifier.emit("loaded", "load:t2", label="the coder", seconds=22)  # ntfy answers again
            await notifier.drained()
            notifier.tick()
            await notifier.drained()
        assert published.texts == [
            "Loaded the coder in 22 s.",
            f"{text('brake_fired', **fired)} (sent late: ntfy was out of reach at 03:12)",
        ]
        assert published.sent[1].priority == "high"


@pytest.mark.anyio
async def test_a_missed_high_alert_is_retried_on_its_own_and_dropped_past_its_age():
    with anyio.fail_after(BOUND_S):
        published = Published()
        published.hang = True
        clock = Clock(at(3, 12))
        notifier = Notifier(published, REGISTRY, GateState(), clock, timeout_s=0.05)
        async with serving(notifier):
            notifier.emit(*high(1)[:2], **high(1)[2])
            await notifier.drained()
            published.hang = False
            clock.now = at(3, 12) + NTFY_LATE_RETRY_S - 1
            notifier.tick()
            await notifier.drained()
            assert published.sent == []  # not yet time to try again
            clock.now = at(3, 12) + NTFY_LATE_RETRY_S
            notifier.tick()  # nothing else is sent: it is tried again on its own
            await notifier.drained()
            assert published.texts == [f"{text(*high(1)[:1], **high(1)[2])} (sent late: ntfy was out of reach at "
                                       "03:12)"]

            published.hang = True
            clock.now = at(5, 0)
            notifier.emit(*high(2)[:2], **high(2)[2])
            await notifier.drained()
            published.hang = False
            clock.now = at(5, 0) + NTFY_LATE_KEEP_S  # 6 h on: too old to be news
            notifier.tick()
            notifier.emit(*low(1)[:2], **low(1)[2])
            await notifier.drained()
        assert published.texts[1:] == [text("loaded", **low(1)[2])]

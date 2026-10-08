"""The gate's notifications (Phase 2a; plan.md, *Visibility and notifications* and *What you see in Phase 2a*): every
event a gate module emits goes to Dan's phone through ntfy, once, at the registry's priority, in messages' words. This
module adds no words of its own.

- **One notification per event.** Each is keyed by its type and its event key together (gate.state.notified_key), so
  `loaded` is never taken for a repeat of `load_started`, which sends `load:<ticket id>` for the same load. The key
  goes into `GateState.notified` as it is accepted, so a restart, once the state is saved, never sends it again.
- **A burst collapses.** Each type that carries a refusal (`refused`, `footprint_suspect`, `load_failed`) collapses per
  model, key and code: the first goes at once; the repeats within BURST_WINDOW_S are counted and go as one, on the
  refusal's own type, when the window closes. A different code goes at once. A front refusal's model is the one its
  words show (messages.shown_model), so a stream of names that don't read as a model's collapses into one;
  `load_failed` collapses per model, whoever asked. A refusal with no key (one of Dan's own commands) never collapses,
  since a burst's words need one: each goes at once. The windows live in memory, so a restart within one loses its
  count, never its first.
- **ntfy out of reach never stalls the gate.** `emit` builds the text and returns at once; a queue, served by `run`,
  publishes each, bounded by NTFY_TIMEOUT_S. A failure sets `GateState.notify_failing_since` to the first failure's
  time, which `spark status` shows (Task 29), and the next success clears it. A notification that fails isn't sent again.
- **The token travels only in a header** (NtfyPublisher): never in the URL, a log line or an error, and neither do
  ntfy's address and the topic, which are private (`/etc/local-ai/values.env`).

It also turns the brake's events into `brake_fired` and its follow-ups (ingest_brake_events), and says when memory
falls under the warn line, once per fall (MemoryWarning)."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from spark import messages
from spark.brakeevents import BrakeEvent, read_events
from spark.credentials import HEADER_NAME
from spark.gate.state import BrakeMark, GateState, notified_key
from spark.gateproto import BURST_WINDOW_S, FIRED_ALONE_S, NTFY_TIMEOUT_S, RELEASE_AFTER_S
from spark.hold import Hold, read_hold, release_waits_for_dan
from spark.llamaswap import _url_ok
from spark.messages import Moment, Notification
from spark.registry import Registry

_log = logging.getLogger(__name__)
# The types that carry a refusal: a burst of one refusal collapses into one notification with its count.
BURST_TYPES = ("refused", "footprint_suspect", "load_failed")
TICK_S = 1.0  # the publishing task closes the burst windows that are due at least this often
NTFY_TOPIC = re.compile(r"[-_A-Za-z0-9]{1,64}")  # ntfy's own rule for a topic's name


@dataclass
class _Window:
    """A burst's window: opened by its first, which went at once; the repeats it counts go as one when it closes."""

    type: str
    model_label: str
    key_label: str
    code: str
    since: float  # the first's time
    count: int = 0


class Notifier:
    """Emit, for every gate module: `emit(type, event_key, **fields)`. `publish` sends one Notification (an
    NtfyPublisher; a recording stand-in in the tests); `clock` gives Unix seconds. The core runs `run()` as a task of
    its own, and saves the state when `on_change` says the notifier changed it (`notified`, `notify_failing_since`).
    `timeout_s` bounds each publish (NTFY_TIMEOUT_S). `registry` may be replaced when the registry is read again.

    Everything here runs on the event loop's thread: `emit` and `tick` never await, so they are never interleaved."""

    def __init__(self, publish: Callable[[Notification], Awaitable[None]], registry: Registry, state: GateState,
                 clock: Callable[[], float] = time.time, *, timeout_s: float = NTFY_TIMEOUT_S,
                 on_change: Callable[[], None] | None = None):
        self._publish = publish
        self.registry = registry
        self.state = state
        self._clock = clock
        self.timeout_s = timeout_s
        self._on_change = on_change or (lambda: None)
        self._queue: deque[Notification] = deque()
        self._windows: dict[tuple[str, str, str, str], _Window] = {}
        self._wake: asyncio.Event | None = None
        self._sending = False

    def emit(self, type: str, event_key: str, /, **fields: Any) -> None:  # noqa: A002 (Emit's name)
        """Queue the notification `type` for the event `event_key`, in messages' words, and return at once. A type the
        registry has `off` is dropped, and so is an event already notified. A field the type doesn't take, or one its
        words need left out, is messages' ValueError, even for a type that is off: the caller's bug, which its tests
        show."""
        built = messages.notification(type, self.registry, **fields)
        self.tick()
        if built is None:
            return
        key = notified_key(type, event_key)
        if key in self.state.notified:
            return
        now = self._clock()
        self.state.notified[key] = now
        self._on_change()
        burst = _burst_key(type, fields)
        if burst is not None:
            window = self._windows.get(burst)
            if window is not None:  # tick() has closed any that was due
                window.count += 1
                return
            self._windows[burst] = _Window(type, burst[1], burst[2], burst[3], since=now)
        self._put(built)

    def tick(self) -> None:
        """Close each burst window that is due, queueing its repeats as one notification on its own type. `emit` and
        `run` call it; the core may too."""
        now = self._clock()
        for key, window in list(self._windows.items()):
            if now - window.since < BURST_WINDOW_S:
                continue
            del self._windows[key]
            if window.count < 1:
                continue
            fields: dict[str, Any] = {"model_label": window.model_label, "count": window.count, "since": window.since,
                                      "code": window.code}
            if window.type != "load_failed":  # a failed load's burst is per model, whoever asked
                fields["key_label"] = window.key_label
            try:
                built = messages.notification(window.type, self.registry, **fields)
            except ValueError as err:  # never stop the publishing task over one burst
                _log.error("notify: a %s burst couldn't be worded: %s", window.type, err)
                continue
            if built is not None:
                self._put(built)

    def _put(self, n: Notification) -> None:
        self._queue.append(n)
        if self._wake is not None:
            self._wake.set()

    async def run(self) -> None:
        """Publish what is queued, one at a time, each bounded by `timeout_s`, and close the burst windows when they
        are due; until cancelled."""
        self._wake = asyncio.Event()
        while True:
            self.tick()
            while self._queue:
                self._sending = True
                try:
                    await self._send(self._queue.popleft())
                finally:
                    self._sending = False
                self.tick()
            self._wake.clear()
            if self._queue:
                continue
            try:
                async with asyncio.timeout(self._until_next()):
                    await self._wake.wait()
            except TimeoutError:
                pass

    def _until_next(self) -> float:
        """How long `run` may sleep: until the next window closes, and at most TICK_S."""
        if not self._windows:
            return TICK_S
        due = min(window.since for window in self._windows.values()) + BURST_WINDOW_S - self._clock()
        return min(TICK_S, max(0.0, due))

    async def drained(self) -> None:
        """Return once nothing is queued and nothing is being sent: for a clean stop, and for the tests."""
        while self._queue or self._sending:
            await asyncio.sleep(0.001)

    async def _send(self, n: Notification) -> None:
        try:
            async with asyncio.timeout(self.timeout_s):
                await self._publish(n)
        except TimeoutError:
            self._failed(n, f"ntfy didn't answer within {self.timeout_s:g} s")
        except Exception as err:  # noqa: BLE001 (ntfy's trouble never stops the gate)
            self._failed(n, str(err) if isinstance(err, NtfyError) else type(err).__name__)
        else:
            if self.state.notify_failing_since is not None:
                self.state.notify_failing_since = None
                self._on_change()
                _log.info("notify: ntfy takes notifications again")

    def _failed(self, n: Notification, why: str) -> None:
        """A notification ntfy didn't take: logged by its type alone, never its text, the address or the topic."""
        _log.warning("notify: a %s notification wasn't sent: %s", n.type, why)
        if self.state.notify_failing_since is None:
            self.state.notify_failing_since = self._clock()
            self._on_change()


def _burst_key(kind: str, fields: dict[str, Any]) -> tuple[str, str, str, str] | None:
    """What a burst of `kind` collapses on: (type, model, key, code), or None for a type that doesn't collapse, a burst
    itself (with `count`), and a refusal with no key."""
    if kind not in BURST_TYPES or fields.get("count") is not None:
        return None
    if kind == "load_failed":  # per model: one failed load is one event, whoever was waiting on it
        return kind, messages.shown_model("load_failed", fields["model_label"]), "", "load_failed"
    if kind == "footprint_suspect":
        return kind, messages.shown_model(kind, fields["model_label"]), fields["key_label"], kind
    code, moment = fields["code"], fields["moment"]
    if not moment.key_label:
        return None
    return kind, _refused_model(code, moment), moment.key_label, code


def _refused_model(code: str, moment: Moment) -> str:
    """The model a refusal's burst names (the controller's ruling at Task 7's review): the registry's label for one the
    gate knows; for model_not_found, the name the client asked for, only when it reads as a model's; nothing for
    route_not_served."""
    if code == "route_not_served":
        return ""
    if code == "model_not_found":
        return messages.shown_model(code, moment.asked_name)
    return messages.shown_model(code, moment.model_label)


# ---------------------------------------------------------------------------------------------------------------------
# ntfy


class NtfyError(Exception):
    """ntfy didn't take a notification. Its text never holds ntfy's address, the topic or the token."""


def _quiet_httpx() -> None:
    """httpx logs every request's URL, the topic in its path, at INFO, and httpcore the host at DEBUG: the gate's
    journal never gets either. This quiets the gate's llama-swap client's request lines too, a line a second."""
    for name in ("httpx", "httpcore"):
        logger = logging.getLogger(name)
        if logger.level < logging.WARNING:
            logger.setLevel(logging.WARNING)


class NtfyPublisher:
    """Publishes a Notification to ntfy: `POST <url>/<topic>`, its text as the body, `Priority: high | default | low`,
    and the token as the header `read_header_credential("ntfy-token")` gives (`Authorization: Bearer …`), never in the
    URL. httpx with `trust_env=False`, so no proxy from the environment gets the token, and no redirect followed. It
    never logs the URL, the topic or the header, and its errors (NtfyError) hold none of them. The gate builds it from
    the values file's NTFY_URL and NTFY_TOPIC_GATE and its credential; close it with `aclose()`."""

    def __init__(self, url: str, topic: str, header: tuple[str, str]):
        if not _url_ok(url):
            raise ValueError("NTFY_URL isn't an http(s) URL with a host")  # never the value: it is private
        if not NTFY_TOPIC.fullmatch(topic):
            raise ValueError("the ntfy topic isn't 1 to 64 letters, digits, - or _")
        name, value = header
        if not (HEADER_NAME.fullmatch(name) and value and all(0x20 <= ord(c) <= 0x7E for c in value)):
            raise ValueError("the ntfy token's header isn't one 'Name: value' line in printable ASCII")
        self._target = f"{url.rstrip('/')}/{topic}"
        self._header = (name, value)
        _quiet_httpx()
        self._client = httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=NTFY_TIMEOUT_S)

    def __repr__(self) -> str:
        return "NtfyPublisher(<the values file's ntfy>)"

    async def __call__(self, n: Notification) -> None:
        name, value = self._header
        try:
            response = await self._client.post(self._target, content=n.message.encode("utf-8"),
                                               headers={"Priority": n.priority, name: value})
        except (httpx.HTTPError, httpx.InvalidURL) as err:
            raise NtfyError(f"ntfy couldn't be reached ({type(err).__name__})") from None
        if not response.is_success:  # a redirect included: it is the answer, never followed
            raise NtfyError(f"ntfy answered HTTP {response.status_code}")

    async def aclose(self) -> None:
        await self._client.aclose()


# ---------------------------------------------------------------------------------------------------------------------
# The brake's events


@dataclass
class _Group:
    """One notification's worth of the brake's events: an episode's first alert (`fired`, and the unloads that go with
    it), or a follow-up's unloads."""

    episode: tuple[str, int]
    fired: BrakeEvent | None  # the first alert's fired event; None for a follow-up, or a first alert from its unload
    first: bool
    unloads: list[BrakeEvent] = field(default_factory=list)
    sent: bool = False  # the brake sent it itself (sent_by_brake): the gate doesn't send it again


def _minute(at: float) -> str:
    """The local minute an event shows as, so one notification never names two."""
    return datetime.fromtimestamp(at).strftime("%Y-%m-%d %H:%M")


def _episode(e: BrakeEvent) -> tuple[str, int]:
    return e.boot_id, e.episode


def _new_from(events: list[BrakeEvent], after: tuple[str, int, float] | None) -> int:
    """Where the events after `after` begin, as brakeevents.read_events finds them: by place, and all of them when no
    event has that key (a file removed, trimmed or started again)."""
    if after is None:
        return 0
    for place in range(len(events) - 1, -1, -1):
        found = events[place]
        if (found.boot_id, found.seq, found.at)[:len(after)] == tuple(after):
            return place + 1
    return 0


def _held_back(events: list[BrakeEvent], now: float) -> int:
    """How many of `events` are left for the next read: from a last `fired` with nothing after it but `warn` events,
    while it is younger than FIRED_ALONE_S, since its first unload may be a moment away."""
    for place in range(len(events) - 1, -1, -1):
        e = events[place]
        if e.kind == "warn":
            continue
        if e.kind == "fired" and abs(now - e.at) < FIRED_ALONE_S:
            return len(events) - place
        return 0
    return 0


def ingest_brake_events(path: Path, state: GateState, registry: Registry, *, now: float | None = None,
                        starts: Mapping[str, float] | None = None) -> list[tuple[str, str, dict[str, Any]]]:
    """The brake's events since `state.brake_events_after` (brake/events.jsonl, Task 13's), as the notifications they
    send, each (type, event key, fields) for Notifier.emit; `state.brake_events_after` advances past what was read,
    and `state.brake_marks` gains the marks. `now` is the gate's clock (time.time() by default); `starts`, each
    starting model's MemAvailable when its load began, from its ticket, as the core has it.

    - An episode's first alert is `brake_fired`, worded with its `fired` event's own reading and line (a drill's raised
      line as itself), naming the unloads that came with it, each by the registry's label and in its state. A `fired`
      with no unload after it yet is left for the next read, up to FIRED_ALONE_S; then it is sent naming none: *Nothing
      of the stack's was loaded* when the gate holds no model, else that llama-swap didn't answer the unload (the
      controller's ruling, at Task 14).
    - Each later unload of the episode is a follow-up, *Brake, 03:13: also unloaded …*. The unloads of one read at the
      same local minute go together.
    - A line the brake sent itself (`sent_by_brake`) isn't sent again. A `warn` sends nothing: memory_warning is the
      gate's own (MemoryWarning).
    - An unload of a model that was `starting` marks it (rule 5: it doesn't reload by itself), with when the brake
      fired and what it was seen using: its fall from `starts` to the brake's reading, or None without a reading.
    - A first alert's pause sentence follows rule 5: `release_waits_for_dan` is hold.release_waits_for_dan for the
      episode's hold (or the `fired` event's moment, when the hold standing is another), judged on the episode's own
      boot, with the last automatic release as `released_at`, and `release_assumed` after a damaged state.

    The keys: `brake:<boot id>:<episode>:<seq>`, the seq of the `fired` event for a first alert (of its first unload
    when the file holds no `fired` for the episode) and of the first unload for a follow-up."""
    now = time.time() if now is None else now
    events = read_events(path, None)
    start = _new_from(events, state.brake_events_after)
    new = events[start:]
    taken = new[:len(new) - _held_back(new, now)]
    if not taken:
        return []
    begun = {_episode(e) for e in events[:start]}  # an episode read before: its first alert went then
    fired_at = {_episode(e): e.at for e in events if e.kind == "fired"}
    groups: list[_Group] = []
    group: _Group | None = None
    for e in taken:
        episode = _episode(e)
        if e.kind == "warn":
            continue
        if e.kind == "fired":
            group = _Group(episode, e, first=True, sent=e.sent_by_brake)
            groups.append(group)
            begun.add(episode)
            continue
        if not e.model:  # an unload names its model (Task 23): one that doesn't has nothing to say
            _log.warning("notify: the brake's event %s:%s is an unload with no model; passed over", e.boot_id, e.seq)
            continue
        if e.state == "starting":
            seen = None if not starts or e.model not in starts else max(0.0, starts[e.model] - e.available_gib)
            state.brake_marks[e.model] = BrakeMark(e.model, fired_at.get(episode, e.at), seen)
        if _joins(group, e):
            group.unloads.append(e)
            continue
        # The file holds no fired for an episode not yet begun: its first unload is its first alert.
        group = _Group(episode, None, first=episode not in begun, unloads=[e], sent=e.sent_by_brake)
        groups.append(group)
        begun.add(episode)
    last = taken[-1]
    state.brake_events_after = (last.boot_id, last.seq, last.at)
    return [_notification(group, path, state, registry, now) for group in groups if not group.sent]


def _joins(group: _Group | None, e: BrakeEvent) -> bool:
    """Whether the unload `e` goes in `group`'s notification: the same episode, a group the gate sends, and the same
    local minute as the group's first unload (a first alert takes its first unload whenever it comes). An unload the
    brake sent itself never joins a follow-up of the gate's: Dan has had it already."""
    if group is None or group.episode != _episode(e) or group.sent:
        return False
    if not group.first and e.sent_by_brake:
        return False
    return not group.unloads or _minute(group.unloads[0].at) == _minute(e.at)


def _label(registry: Registry, name: str) -> str:
    """A model by the registry's label; one the registry doesn't know, by its name."""
    model = registry.models.get(name)
    return model.label if model is not None else name


def _notification(group: _Group, path: Path, state: GateState, registry: Registry,
                  now: float) -> tuple[str, str, dict[str, Any]]:
    boot, episode = group.episode
    unloaded = [(_label(registry, e.model or ""), e.state) for e in group.unloads]
    if not group.first:
        first = group.unloads[0]
        return ("brake_fired", f"brake:{boot}:{episode}:{first.seq}",
                {"at": first.at, "unloaded": unloaded, "follow_up": True})
    reading = group.fired or group.unloads[0]
    waits = _waits_for_dan(reading, path, state, now)
    fields: dict[str, Any] = {"at": reading.at, "available_gib": reading.available_gib, "line_gib": reading.line_gib,
                              "unloaded": unloaded, "release_waits_for_dan": waits,
                              "release_after_s": RELEASE_AFTER_S, "release_assumed": state.release_assumed}
    if waits and not state.release_assumed:
        fields["released_at"] = state.last_auto_release_at
    if not unloaded and state.models:  # something is loaded, and no unload came by FIRED_ALONE_S
        fields["unload_unanswered"] = True
    return "brake_fired", f"brake:{boot}:{episode}:{reading.seq}", fields


def _waits_for_dan(fired: BrakeEvent, path: Path, state: GateState, now: float) -> bool:
    """Rule 5, as hold.release_waits_for_dan has it, for the episode's hold: the one standing when it is this
    episode's, else one dated by the `fired` event itself. It is judged on the episode's own boot, so the alert says
    what that brake's pause waits for; a hold found after a reboot is Task 17's brake_needs_release."""
    hold = read_hold(path.parent)
    if hold is None or (hold.boot_id, hold.episode) != (fired.boot_id, fired.episode):
        since = datetime.fromtimestamp(fired.at).isoformat(timespec="seconds")  # local, as the brake writes `since`
        hold = Hold(since, "", (), fired.boot_id, fired.episode)
    return release_waits_for_dan(hold, boot_id=fired.boot_id, last_auto_release_at=state.last_auto_release_at,
                                 now=now)


# ---------------------------------------------------------------------------------------------------------------------
# Memory


class MemoryWarning:
    """Rule 5's warning: `check` is true once per fall under the warn line, and re-armed only above it."""

    def __init__(self) -> None:
        self._armed = True

    def check(self, available_gib: float, warn_gib: float) -> bool:
        if available_gib < warn_gib:
            fell, self._armed = self._armed, False
            return fell
        if available_gib > warn_gib:
            self._armed = True
        return False

"""Every refusal in Dan's words (website/design/plan.md, *What you see in Phase 2a*), with its HTTP status and its
headers: what pi and the web UI show when the gate or the front says no. Each says what happened, then the numbers,
then the next step, naming where to act and the command when there is one.

The front imports this module (Task 21's FRONT_MODULES), so at module level it may import spark.registry and nothing
else of spark: were the budget pulled in, a change there would restart the front.

The words never work out admission's arithmetic: free for a load is the gate's own figure (Moment.free_gib), and the
other memory numbers only make up the breakdown the plan's text shows. Each size is read exactly, a float as its repr
reads, as the budget's are, then rounded as the plan says.

Text from outside the registry and the key list (a process's name from /proc, the engine's line, the name a client
asked for) goes in only on one line, and only when pi's retry list doesn't match it: otherwise pi would retry the
refusal because of it."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal, localcontext
from typing import Literal

# Every refusal the gate or the front gives after a key's wait, or that no retry soon changes: pi shows a 409 at once
# and never retries it (phase-2a.md, *Before Task 1*). Dan's decision, 2026-10-07, after the forward-and-back council,
# added the last three.
CODES_409 = ("no_fit", "loading", "held_by_brake", "footprint_suspect", "load_failed", "not_downloaded", "restarting",
             "llama_swap_down", "draining")
CODES_503 = ("gate_down",)  # the front's answer at once while the gate is down, which pi retries: the gate restarts
FRONT_CODES = {"model_not_found": 404, "too_many_requests": 429, "route_not_served": 404, "concurrency_limit": 429}
# The controller's ruling, 2026-10-07: each code's retry-after, with its Retry-After header; none for the rest.
RETRY_AFTER_S = {"no_fit": 30, "held_by_brake": 300, "gate_down": 30, "restarting": 60, "draining": 60,
                 "llama_swap_down": 30, "too_many_requests": 10}
UNKNOWN_KEY = "That API key isn't one the Spark knows. Check SPARK_API_KEY on this machine."  # the front's 401

# pi-ai 0.85.1's RETRYABLE_PROVIDER_ERROR_PATTERN (dist/utils/retry.js), word for word, then 0.87.1's two additions.
# pi joins them with '|' and matches its error's text case-insensitively: a match makes pi retry the whole turn, up to
# three times, whatever the status and the headers say. pi's text is `<status>: <the body's error object as JSON>`.
PI_RETRY_PATTERNS: tuple[str, ...] = (
    "overloaded",
    "rate.?limit",
    "too many requests",
    "429",
    "500",
    "502",
    "503",
    "504",
    "524",
    "service.?unavailable",
    "server.?error",
    "internal.?error",
    "provider.?returned.?error",
    "exceeded request buffer limit while retrying upstream",
    "network.?error",
    "connection.?error",
    "connection.?refused",
    "connection.?lost",
    "other side closed",
    "fetch failed",
    "getaddrinfo",
    "ENOTFOUND",
    "EAI_AGAIN",
    "upstream.?connect",
    "reset before headers",
    "socket hang up",
    "socket connection was closed",
    "timed? out",
    "timeout",
    "terminated",
    "websocket.?closed",
    "websocket.?error",
    "ended without",
    "stream ended before message_stop",
    "stream ended before a terminal response event",
    "http2 request did not get a response",
    "retry delay",
    "you can retry your request",
    "try your request again",
    "please retry your request",
    "ResourceExhausted",
    "currently experiencing high demand",
    "520",
)
_PI_RETRIES = re.compile("|".join(PI_RETRY_PATTERNS), re.IGNORECASE)
_STATUS = {**dict.fromkeys(CODES_409, 409), **dict.fromkeys(CODES_503, 503), **FRONT_CODES}
# The paths the front serves (plan.md, *The front and the gate*), in the order route_not_served's words list them.
_SERVED = ("/v1/models", "/v1/chat/completions", "/v1/completions", "/v1/responses", "/v1/messages", "/v1/embeddings",
           "/v1/audio/transcriptions")
_DANS_KEYS = "pi on the Mac or the web UI"  # Dan's keys, by their labels in the private key list, as plan.md names them
_TENTH = Decimal("0.1")
# Decimal's default 28 digits can't hold an absurd size to a tenth (budget.DIGITS says why); 400 hold any float's.
_DIGITS = 400


@dataclass(frozen=True)
class Holder:
    """A memory holder as messages name it (rule 7): a loaded model by its label, any other process by its short name
    and its user, with what it holds now. `dans`: a process of Dan's, which a key group without names_processes sees
    only as *a process of Dan's*."""

    name: str
    gib: float
    dans: bool


@dataclass(frozen=True)
class Moment:
    """What a refusal's words need, as the gate or the front saw it. Every field has a default, since the front builds
    its own refusals (draining, model_not_found, restarting, concurrency_limit, gate_down) with none of the memory
    numbers; a key's fields default to the least they promise, agent's wording with no process of Dan's named. Sizes
    are GiB as measured, a float, an int or a Decimal: the words round them. no_fit needs `free_gib`."""

    needed_gib: float = 0  # the model's footprint
    free_gib: float | None = None  # no_fit: free for a load, the gate's own figure (budget.free_for_a_load's)
    # The breakdown no_fit's words show after the figure, never used to work it out.
    available_gib: float = 0  # MemAvailable
    reserve_gib: float = 0
    owed_gib: float = 0  # the growth the loaded models are still owed
    held_gib: float = 0  # make-room's hold
    hold_counted: bool = False  # the asker's group may not load into the hold (no uses_hold), so it is taken
    holders: list[Holder] = field(default_factory=list)  # using memory now, in the order to name them
    wait_s: float = 0  # the asking key's wait
    model_label: str = ""  # "the coder"
    model_command: str = ""  # the name `spark load` takes: the model's first role, else its name
    key_label: str = ""  # "pi on the Mac", "agent"
    words: Literal["dan", "agent"] = "agent"  # the asking key's group's: whose wording
    names_processes: bool = False  # the asking key's group's: whether Dan's processes are named
    loading_label: str = ""  # loading: the model that holds the one-load slot
    brake_at: datetime | None = None  # held_by_brake, footprint_suspect: when the brake fired, an aware datetime
    brake_available_gib: float | None = None  # held_by_brake: MemAvailable when it fired
    release_waits_for_dan: bool = False  # held_by_brake: the hold lifts only when Dan releases it
    warn_gib: float = 28  # held_by_brake: the brake's warn line, the registry's brake.warn_gib
    release_after_s: float = 300  # held_by_brake: memory stays above the warn line this long, then new loads resume
    engine_said: str | None = None  # load_failed: the engine's last line; None when the load passed its deadline
    deadline_s: float | None = None  # load_failed: the deadline the load passed
    download_gib: float | None = None  # not_downloaded: the model's files
    inflight: int = 0  # draining: the model's requests in flight
    drain_for: Literal["make-room", "unload", "idle"] = "unload"  # draining: why the model is being unloaded
    asked_name: str = ""  # model_not_found: the name the request asked for
    models: list[tuple[str, str]] = field(default_factory=list)  # model_not_found: each (label, name), in list order


@dataclass(frozen=True)
class Refusal:
    code: str
    status: int
    message: str
    retry_after_s: int | None

    def body(self) -> dict:
        """OpenAI's error shape, with only `message` and `code` in `error` and never a `detail` (phase-2a.md, *Before
        Task 1*): pi shows every key of `error`, and the web UI shows a `detail` in place of the message."""
        body: dict = {"error": {"message": self.message, "code": self.code}}
        if self.retry_after_s is not None:
            body["retry_after_s"] = self.retry_after_s
        return body

    def headers(self) -> list[tuple[bytes, bytes]]:
        """A 409 and a 503 say not to retry, since OpenAI's SDKs retry both unless told; they and a 429 carry
        Retry-After when the code has one; a 404, neither."""
        headers = [(b"content-type", b"application/json")]
        if self.status in (409, 503):
            headers.append((b"x-should-retry", b"false"))
        if self.retry_after_s is not None and self.status in (409, 429, 503):
            headers.append((b"retry-after", str(self.retry_after_s).encode()))
        return headers


def refusal(code: str, m: Moment) -> Refusal:
    """The refusal `code` at moment `m`, in the words of the asking key's group."""
    if code not in _STATUS:
        raise ValueError(f"{code!r} isn't a refusal with words; the codes are {', '.join(_STATUS)}")
    with localcontext(prec=_DIGITS):
        message = _WORDS[code](m)
    return Refusal(code, _STATUS[code], message, RETRY_AFTER_S.get(code))


def _gib(value: float | Decimal) -> Decimal:
    """`value` exactly: a Decimal as it is, a float or an int as its repr reads."""
    return value if isinstance(value, Decimal) else Decimal(repr(value))


def _up(value: float | Decimal) -> int:
    """A need, in whole GiB, rounded up: with a room rounded down, the two never read as a fit."""
    return int(_gib(value).to_integral_value(ROUND_CEILING))


def _down(value: float | Decimal) -> int:
    """*Available* and *free for a load*, in whole GiB, rounded down."""
    return int(_gib(value).to_integral_value(ROUND_FLOOR))


def _near(value: float | Decimal) -> int:
    """Any other size, in whole GiB, to the nearest, a half up."""
    return int(_gib(value).to_integral_value(ROUND_HALF_UP))


def _reading(value: float | Decimal) -> str:
    """A reading near a line (the brake's, the warn line's), to one decimal, rounded down as available is: one just
    under the line never reads as at it."""
    return f"{_gib(value).quantize(_TENTH, ROUND_FLOOR):f}"


def _line(value: float | Decimal) -> str:
    """A line as the registry gives it: 28, or 28.5."""
    return f"{_gib(value).normalize():f}"


def _wait(seconds: float | Decimal) -> str:
    """A wait: `<n> s` under a minute, else whole minutes."""
    if _near(seconds) < 60:
        return f"{_near(seconds)} s"
    minutes = _near(_gib(seconds) / 60)
    return "1 minute" if minutes == 1 else f"{minutes} minutes"


def _clock(at: datetime) -> str:
    """A moment as the local 24-hour clock shows it."""
    return at.astimezone().strftime("%H:%M")


def _start(label: str) -> str:
    """A label at a sentence's start: "the coder" → "The coder"; "Gemma", "whisper" and "agent" as they are."""
    return "The " + label[4:] if label.startswith("the ") else label


def _and(items: list[str]) -> str:
    """"A", "A and B", "A, B and C"."""
    return f"{', '.join(items[:-1])} and {items[-1]}" if len(items) > 1 else "".join(items)


def _one_line(text: str) -> str:
    """`text` on one line: anything that doesn't print (a line break, a tab, a control character) becomes a space, and
    each run of spaces one."""
    return " ".join("".join(c if c.isprintable() else " " for c in text).split())


def _pi_would_retry(text: str) -> bool:
    """Whether pi would retry a refusal whose words hold `text`: as written, which is how pi shows it, or as a JSON
    writer that keeps to ASCII writes it, Python's json.dumps among them: a character outside ASCII becomes \\uXXXX,
    whose digits can hold a 503."""
    return bool(_PI_RETRIES.search(text) or _PI_RETRIES.search(json.dumps(text)))


def _outside(text: str | None) -> str | None:
    """Text from outside the registry and the key list, as the words may hold it: on one line, or None when it is empty
    or pi's list matches it. Every piece it goes into is fixed text that matches nothing (the tests show), and no
    pattern spans the quote or the ", " around a piece, so a piece that passes alone passes in its sentence."""
    line = _one_line(text or "")
    return line if line and not _pi_would_retry(line) else None


def _holder(h: Holder, names_processes: bool) -> str:
    """A holder, named unless it is Dan's and the group doesn't name his processes. Its name goes through _outside,
    since a process's comes from /proc; a model's is its label, which passes."""
    name = _outside(h.name) if names_processes or not h.dans else None
    if name is not None:
        return f"{name} {_near(h.gib)} GiB"
    return f"a process of Dan's, {_near(h.gib)} GiB" if h.dans else f"a process, {_near(h.gib)} GiB"


def _no_fit(m: Moment) -> str:
    if m.free_gib is None:
        raise ValueError("no_fit's words need free_gib, the gate's own figure for free for a load")
    need, free = _up(m.needed_gib), _down(m.free_gib)
    held, reserve, owed = _near(m.held_gib) if m.hold_counted else 0, _near(m.reserve_gib), _near(m.owed_gib)
    less = [f"the {reserve} GiB reserve"] if reserve else []
    if owed:
        less.append(f"the {owed} GiB the loaded models may still grow into")
    if free >= 1:
        room = f"{free} GiB is free for a load"
        if held:
            less.append(f"the {held} GiB make-room holds for Dan")
    elif held:
        room = f"nothing is free for a load while make-room holds {held} GiB for Dan"
    else:
        room = "nothing is free for a load"
    taken = f", less {_and(less)}" if less else ""
    words = [f"{_start(m.model_label)} didn't load: it needs {need} GiB, and {room} "
             f"({_down(m.available_gib)} GiB available{taken})."]
    if m.holders:
        words.append(f"Using memory now: {', '.join(_holder(h, m.names_processes) for h in m.holders)}.")
    if held:
        words.append("On the Spark, `spark make-room --done` ends the hold.")
    elif m.words == "dan":
        words.append(f"Free space with `spark make-room {need}G` on the Spark, then try again.")
    else:  # make-room is Dan's alone, on the control socket
        words.append(f"Dan can free space with `spark make-room {need}G` on the Spark; then try again.")
    return " ".join(words)


def _loading(m: Moment) -> str:
    return (f"{_start(m.model_label)} didn't start in time: it was waiting its turn while {m.loading_label} loads, "
            f"since one model loads at a time, and your {_wait(m.wait_s)} ran out. Try again in a minute.")


def _at(at: datetime | None) -> str:
    return f" at {_clock(at)}" if at is not None else ""


def _held_by_brake(m: Moment) -> str:
    reading = f" ({_reading(m.brake_available_gib)} GiB available)" if m.brake_available_gib is not None else ""
    low = f"Not loading {m.model_label} now: memory ran low{_at(m.brake_at)}{reading}, and new loads"
    if m.release_waits_for_dan:
        who = "you release them" if m.words == "dan" else "Dan releases them"  # the release is Dan's alone
        return f"{low} stay paused until {who}: on the Spark, `make brake-release`."
    return (f"{low} are paused. They resume by themselves after {_wait(m.release_after_s)} above "
            f"{_line(m.warn_gib)} GiB available, if what would reload fits; on the Spark, `make brake-release` "
            "resumes them now.")


def _gate_down(m: Moment) -> str:
    return ("No new model can load: the gate on the Spark isn't running. Models already loaded still answer. Your "
            "phone has the alert; on the Spark, `make doctor` shows what's wrong.")


def _load_failed(m: Moment) -> str:
    began = f"{_start(m.model_label)} started loading but"
    after = "On the Spark, `spark status` shows the engine's last lines."
    if _one_line(m.engine_said or ""):
        said = _outside(m.engine_said)  # an engine's "timeout" or "terminated" would make pi repeat a whole load
        if said is not None:
            return f'{began} failed: the engine stopped with "{said}". {after}'
    elif m.deadline_s is not None:
        return f"{began} didn't finish within {_near(m.deadline_s)} s. {after}"
    return f"{began} failed: the engine stopped. {after}"


def _not_downloaded(m: Moment) -> str:
    size = f" ({_near(m.download_gib)} GiB)" if m.download_gib is not None and _near(m.download_gib) else ""
    return f"{_start(m.model_label)} isn't downloaded yet. On the Spark, `make pull` fetches it{size}."


def _restarting(m: Moment) -> str:
    return (f"The model service on the Spark is restarting for a configuration change, and your {_wait(m.wait_s)} ran "
            "out. Try again in a minute.")


def _llama_swap_down(m: Moment) -> str:
    return (f"The model service on the Spark isn't answering, and your {_wait(m.wait_s)} ran out. Your phone has the "
            "alert; on the Spark, `make doctor` shows what's wrong.")


def _draining(m: Moment) -> str:
    for_room = m.drain_for == "make-room"
    once = ("" if m.inflight < 1 else f" once its {m.inflight} request in flight finishes" if m.inflight == 1
            else f" once its {m.inflight} requests in flight finish")
    ran_out = (f"{_start(m.model_label)} is being unloaded{' for make-room' if for_room else ''}{once}, and your "
               f"{_wait(m.wait_s)} ran out.")
    if not for_room:
        return f"{ran_out} Try again in a minute."
    if m.words == "dan":  # make-room's hold is Dan's to load into (rule 4)
        return (f"{ran_out} Try again in a minute: your request can load it again, into the room make-room holds for "
                "you, if it fits.")
    return f"{ran_out} It won't load for {m.key_label} while make-room's hold stands."


def _footprint_suspect(m: Moment) -> str:
    return (f"Not loading {m.model_label} for {m.key_label}: it was loading when the brake fired{_at(m.brake_at)}, so "
            f"only Dan can load it again: `spark load {m.model_command}` on the Spark, or a request of his from "
            f"{_DANS_KEYS}, which loads it if it fits.")


def _model_not_found(m: Moment) -> str:
    asked = _outside(m.asked_name)  # the client's own text
    none = f"There's no model called {asked} here." if asked is not None else "There's no model by that name here."
    listed = _and([f"{label} ({name})" for label, name in m.models])
    if m.words == "dan":
        update = "On the Mac, `make clients` updates pi's list."
    else:  # agent's pi is on the Spark (website/how-to/pi.md): the controller's ruling, 2026-10-07, at Task 6
        update = "On the Spark, as `agent`: pull its clone and run `spark clients pi --write` to update pi's list."
    return f"{none} The models are {listed}. {update}"


def _too_many_requests(m: Moment) -> str:
    return (f"{_start(m.key_label)} already has as many requests waiting or open as its key allows; this one wasn't "
            "queued. Try again when one finishes.")


def _route_not_served(m: Moment) -> str:
    return f"This address isn't served here: the Spark's model API answers only {_and(list(_SERVED))}."


def _concurrency_limit(m: Moment) -> str:
    return f"Too many requests for {m.model_label} at once; try again in a moment."


_WORDS: dict[str, Callable[[Moment], str]] = {
    "no_fit": _no_fit, "loading": _loading, "held_by_brake": _held_by_brake, "footprint_suspect": _footprint_suspect,
    "load_failed": _load_failed, "not_downloaded": _not_downloaded, "restarting": _restarting,
    "llama_swap_down": _llama_swap_down, "draining": _draining, "gate_down": _gate_down,
    "model_not_found": _model_not_found, "too_many_requests": _too_many_requests,
    "route_not_served": _route_not_served, "concurrency_limit": _concurrency_limit,
}

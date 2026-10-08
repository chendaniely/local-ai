"""Every refusal, notification and confirmation in Dan's words (website/design/plan.md, *What you see in Phase 2a*).
A refusal, with its HTTP status and its headers, is what pi and the web UI show when the gate or the front says no; a
notification is what Dan's phone shows when the gate, the brake or the failure notifier acts; a confirmation is what a
command prints in the terminal. Each says what happened, then the numbers, then the next step, naming where to act and
the command when there is one. In `agent`'s words, a step only Dan can take says it is Dan's, and `agent` is never told
to run a command only Dan can run. A notification is always in Dan's words, since only Dan's phone gets one.

The front imports this module (Task 21's FRONT_MODULES), so at module level it may import spark.registry and nothing
else of spark: were the budget pulled in, a change there would restart the front.

The words never work out admission's arithmetic: free for a load is the gate's own figure (Moment.free_gib), and the
breakdown after it is the term of rule 9 that gave it, as the gate passes it. Each size is read exactly, a float as its
repr reads, as the budget's are, then rounded as the plan says.

No refusal may make pi retry it (phase-2a.md, *Before Task 1*):
- Text from outside the registry and the key list goes in only on one line, and only when pi's retry list doesn't
  match it. That is a process's name from /proc, the engine's line, or the name a client asked for.
- A duration reads in minutes and seconds from a minute on, so none prints as a bare 5xx.
- A size over 400 GiB reads *more than 400 GiB*, true and matched by nothing in pi's list. None can occur
  on this box, since render keeps every footprint under the ceiling. No number a message shows is ever altered.
- render refuses registry text pi's list matches (render.check_words)."""

from __future__ import annotations

import json
import re
import socket
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal, localcontext
from typing import TYPE_CHECKING, Any, Literal

from spark.registry import NOTIFICATION_TYPES

if TYPE_CHECKING:  # never at run time: the front imports this module, and must not pull in the budget
    from spark.budget import Candidate
    from spark.registry import Registry

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
# Where to look when a service is down: the phone's alert and `make doctor`, both Dan's.
_ALERT = {"dan": "Your phone has the alert; on the Spark, `make doctor` shows what's wrong.",
          "agent": "Dan's phone has the alert, and `make doctor` on the Spark shows Dan what's wrong."}
# The command agent's pi list is updated with after the cutover (phase-2a.md, Task 34): the deployed CLI against the
# deployed registry. Without --http-idle-timeout-ms it leaves pi's settings.json as it is.
_AGENTS_CLIENTS = "/opt/local-ai/app/.venv/bin/spark clients pi --write --registry /opt/local-ai/etc/models.yaml"
_TENTH = Decimal("0.1")
# Past this size a message says "more than 400 GiB", never the number: pi's list holds 429, 500, 502-504, 520 and
# 524, and no size up to 400 can show one of them (a decimal point breaks a reading's digits apart).
_TOO_BIG_GIB = 400
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
    numbers. Each code's words refuse a Moment that lacks a field they use, naming the code and the field (a
    ValueError), so a caller that forgets one fails in its tests, never in a sentence with a hole in it. Sizes are GiB
    as measured, a float, an int or a Decimal: the words round them."""

    needed_gib: float | None = None  # no_fit: the model's footprint
    free_gib: float | None = None  # no_fit: free for a load, the gate's own figure (budget.free_for_a_load's)
    # no_fit's breakdown, the term of rule 9's min() that was the smaller: MemAvailable less the reserve and the growth
    # owed, or, with ceiling_gib set (only when it was the smaller), the CUDA ceiling less what the loaded models are
    # committed to; then a model still starting, then the hold when it is counted. Never used to work out free_gib.
    available_gib: float | None = None  # MemAvailable
    reserve_gib: float | None = None
    owed_gib: float = 0  # the growth the loaded models are still owed
    ceiling_gib: float | None = None  # the CUDA-allocatable ceiling, set only when ceiling − committed was the smaller
    committed_gib: float = 0  # with ceiling_gib: the loaded models' footprints
    starting_gib: float = 0  # the footprint of a model still starting
    held_gib: float = 0  # make-room's hold
    hold_counted: bool = False  # the asker's group may not load into the hold (no uses_hold), so it is taken
    holders: list[Holder] = field(default_factory=list)  # using memory now, in the order to name them
    wait_s: float | None = None  # the asking key's wait
    model_label: str = ""  # "the coder"
    model_command: str = ""  # the name `spark load` takes: the model's first role, else its name
    key_label: str = ""  # "pi on the Mac", "agent"
    words: Literal["dan", "agent"] | None = None  # the asking key's group's: whose wording
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
    # draining: why the model is being unloaded (gateproto.DrainWhy); only make-room's changes the words
    drain_for: Literal["make-room", "unload", "idle", "late_start", "unknown"] | None = None
    asked_name: str | None = None  # model_not_found: the name the request asked for, as the client sent it
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


def pi_retry_match(text: str) -> str | None:
    """What pi's retry list matches in `text`, or None. It checks the text as written, which is how pi shows it, and
    as a JSON writer that keeps to ASCII writes it, Python's json.dumps among them: a character outside ASCII becomes
    \\uXXXX, whose digits can hold a 503."""
    found = _PI_RETRIES.search(text) or _PI_RETRIES.search(json.dumps(text))
    return found.group(0) if found else None


def duration(seconds: float | Decimal) -> str:
    """A duration as the words say it: `<n> s` under a minute; from a minute, minutes and seconds (`1 minute 30 s`);
    from an hour, hours too. No number in it passes 59 but the hours, so a deadline never reads as a bare 5xx (the
    controller's ruling, Task 6's fix round 1)."""
    total = int(_gib(seconds).to_integral_value(ROUND_HALF_UP))
    if total < 60:
        return f"{total} s"
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    parts = ["1 hour" if hours == 1 else f"{hours} hours"] if hours else []
    if minutes:
        parts.append("1 minute" if minutes == 1 else f"{minutes} minutes")
    if secs:
        parts.append(f"{secs} s")
    return " ".join(parts)


def _need(code: str, m: Moment, *names: str) -> None:
    """Refuse a Moment that lacks a field `code`'s words use: None, an empty text or an empty list."""
    for name in names:
        if getattr(m, name) in (None, "", []):
            raise ValueError(f"{code}'s words need {name}")


def _gib(value: float | Decimal) -> Decimal:
    """`value` exactly: a Decimal as it is, a float or an int as its repr reads."""
    return value if isinstance(value, Decimal) else Decimal(repr(value))


def _up(value: float | Decimal) -> int:
    """A need, in whole GiB, rounded up: with a room rounded down, the two never read as a fit."""
    return int(_gib(value).to_integral_value(ROUND_CEILING))


def _down(value: float | Decimal) -> int:
    """*Available*, *free for a load* and the ceiling, in whole GiB, rounded down."""
    return int(_gib(value).to_integral_value(ROUND_FLOOR))


def _near(value: float | Decimal) -> int:
    """Any other size, in whole GiB, to the nearest, a half up."""
    return int(_gib(value).to_integral_value(ROUND_HALF_UP))


def _size(shown: int | Decimal) -> str:
    """A size as a message shows it, `<n> GiB`, but *more than 400 GiB* past 400 (_TOO_BIG_GIB)."""
    if shown > _TOO_BIG_GIB:
        return "more than 400 GiB"
    return f"{shown:f} GiB" if isinstance(shown, Decimal) else f"{shown} GiB"


def _reading(value: float | Decimal) -> str:
    """A reading near a line (the brake's, the warn line's), to one decimal, rounded down as available is: one just
    under the line never reads as at it."""
    return _size(_gib(value).quantize(_TENTH, ROUND_FLOOR))


def _line(value: float | Decimal) -> str:
    """A line as the registry gives it: 28 GiB, or 28.5 GiB."""
    return _size(_gib(value).normalize())


def _clock(at: datetime | float) -> str:
    """A moment as the local 24-hour clock shows it: an aware datetime, or Unix seconds, as the gate keeps time."""
    return _local(at).strftime("%H:%M")


def _local(at: datetime | float) -> datetime:
    return datetime.fromtimestamp(at).astimezone() if isinstance(at, (int, float)) else at.astimezone()


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


def _outside(text: str | None) -> str | None:
    """Text from outside the registry and the key list, as the words may hold it: on one line, or None when it is empty
    or pi's list matches it. Every piece it goes into is fixed text that matches nothing (the tests show), and no
    pattern spans the quote or the ", " around a piece, so a piece that passes alone passes in its sentence."""
    line = _one_line(text or "")
    return line if line and pi_retry_match(line) is None else None


def _holder(h: Holder, names_processes: bool) -> str:
    """A holder, named unless it is Dan's and the group doesn't name Dan's processes. Its name goes through _outside,
    since a process's comes from /proc; a model's is its label, which passes."""
    name = _outside(h.name) if names_processes or not h.dans else None
    if name is not None:
        return f"{name} {_size(_near(h.gib))}"
    return f"a process of Dan's, {_size(_near(h.gib))}" if h.dans else f"a process, {_size(_near(h.gib))}"


def _no_fit(m: Moment) -> str:
    _need("no_fit", m, "model_label", "needed_gib", "free_gib", "words")
    if m.ceiling_gib is not None:  # ceiling − committed was the smaller term
        start, start_words = m.ceiling_gib, "the {} the GPU can allocate"
        items = [(m.committed_gib, "the {} the loaded models may grow to")]
    else:
        _need("no_fit", m, "available_gib", "reserve_gib")
        start, start_words = m.available_gib, "{} available"
        items = [(m.reserve_gib, "the {} reserve"), (m.owed_gib, "the {} the loaded models may still grow into")]
    items.append((m.starting_gib, "the {} the model still starting may take"))
    need, free = _up(m.needed_gib), _down(m.free_gib)
    whole, shown, held = _breakdown(start, items, m.held_gib if m.hold_counted else 0, free)
    less = [words.format(_size(value)) for value, words in shown if value]
    if free >= 1:
        room = f"{_size(free)} is free for a load"
        if held:
            less.append(f"the {_size(held)} make-room holds for Dan")
    elif held:
        room = f"nothing is free for a load while make-room holds {_size(held)} for Dan"
    else:
        room = "nothing is free for a load"
    taken = f", less {_and(less)}" if less else ""
    words = [f"{_start(m.model_label)} didn't load: it needs {_size(need)}, and {room} "
             f"({start_words.format(_size(whole))}{taken})."]
    if m.holders:
        words.append(f"Using memory now: {', '.join(_holder(h, m.names_processes) for h in m.holders)}.")
    if need > _TOO_BIG_GIB:  # more than the box has: no command can make that much room
        words.append("The Spark can never free that much.")
    elif held and _hold_is_the_way(m) and m.words == "dan":
        words.append("On the Spark, `spark make-room --done` ends the hold.")
    elif held and _hold_is_the_way(m):  # the hold is Dan's to end (the controller's ruling, Task 6's fix round 2)
        words.append("The hold ends when Dan runs `spark make-room --done` on the Spark.")
    elif m.words == "dan":
        words.append(f"Free space with `spark make-room {need}G` on the Spark, then try again.")
    else:  # make-room is Dan's alone, and its hold would bar agent anyway (rule 4)
        words.append("Only Dan can free memory for it, on the Spark; try again after that.")
    return " ".join(words)


def _hold_is_the_way(m: Moment) -> bool:
    """Whether ending make-room's hold would let the load fit: free for a load with the hold counted, and the hold. Only
    then is the hold's end the step (the controller's ruling on Task 7's review); with Dan's job in the hold, ending it
    can still leave too little."""
    return _gib(m.free_gib) + _gib(m.held_gib) >= _gib(m.needed_gib)


def _breakdown(start: float | Decimal, items: list[tuple[float | Decimal, str]], held: float | Decimal,
               free: int) -> tuple[Decimal, list[tuple[Decimal, str]], Decimal]:
    """The breakdown's terms as the words show them, so that they add up to the figure shown, `free` (free for a load,
    whole and rounded down, or nothing below 1). Whole GiB when the whole terms add up to it, else one decimal, else
    two, the first that does (the controller's ruling, Task 6's fix round 3: a breakdown adds up as shown). The start
    rounds down, as *available* does, the rest to the nearest. No number is altered: only how many places show."""
    def shown_as(total: Decimal) -> int | None:
        figure = int(total.to_integral_value(ROUND_FLOOR))
        return figure if figure >= 1 else None

    for places in (0, 1, 2):
        step = Decimal((0, (1,), -places))
        at = (_gib(start).quantize(step, ROUND_FLOOR), [(_gib(v).quantize(step, ROUND_HALF_UP), w) for v, w in items],
              _gib(held).quantize(step, ROUND_HALF_UP))
        if shown_as(at[0] - sum((v for v, _ in at[1]), Decimal(0)) - at[2]) == shown_as(Decimal(free)):
            break
    return at[0].normalize(), [(v.normalize(), w) for v, w in at[1]], at[2].normalize()


def _loading(m: Moment) -> str:
    _need("loading", m, "model_label", "loading_label", "wait_s")
    return (f"{_start(m.model_label)} didn't start in time: it was waiting its turn while {m.loading_label} loads, "
            f"since one model loads at a time, and your {duration(m.wait_s)} ran out. Try again in a minute.")


def _held_by_brake(m: Moment) -> str:
    _need("held_by_brake", m, "model_label", "brake_at", "brake_available_gib", "words")
    low = (f"Not loading {m.model_label} now: memory ran low at {_clock(m.brake_at)} "
           f"({_reading(m.brake_available_gib)} available), and new loads")
    if m.release_waits_for_dan:
        who = "you release them" if m.words == "dan" else "Dan releases them"  # the release is Dan's alone
        return f"{low} stay paused until {who}: on the Spark, `make brake-release`."
    resume = (f"They resume by themselves after {duration(m.release_after_s)} above {_line(m.warn_gib)} "
              "available, if what would reload fits")
    if m.words == "dan":
        return f"{low} are paused. {resume}; on the Spark, `make brake-release` resumes them now."
    return f"{low} are paused. {resume}, or when Dan runs `make brake-release` on the Spark."


def _gate_down(m: Moment) -> str:
    _need("gate_down", m, "words")
    return ("No new model can load: the gate on the Spark isn't running. Models already loaded still answer. "
            f"{_ALERT[m.words]}")


def _load_failed(m: Moment) -> str:
    _need("load_failed", m, "model_label", "model_command", "words")
    began = f"{_start(m.model_label)} started loading but"
    # The engine's lines are Task 30's `spark logs <model>`, on the control socket, which agent can't use.
    after = (f"On the Spark, `spark logs {m.model_command}` shows the engine's last lines." if m.words == "dan"
             else f"Dan can see why with `spark logs {m.model_command}` on the Spark.")
    if _one_line(m.engine_said or ""):
        said = _outside(m.engine_said)  # an engine's "timeout" or "terminated" would make pi repeat a whole load
        if said is not None:
            return f'{began} failed: the engine stopped with "{said}". {after}'
    elif m.deadline_s is not None:
        return f"{began} didn't finish within {duration(m.deadline_s)}. {after}"
    return f"{began} failed: the engine stopped. {after}"


def _not_downloaded(m: Moment) -> str:
    _need("not_downloaded", m, "model_label", "words")
    size = f" ({_size(_near(m.download_gib))})" if m.download_gib is not None and _near(m.download_gib) else ""
    fetch = (f"On the Spark, `make pull` fetches it{size}." if m.words == "dan"
             else f"Dan can fetch it with `make pull` on the Spark{size}.")
    return f"{_start(m.model_label)} isn't downloaded yet. {fetch}"


def _restarting(m: Moment) -> str:
    _need("restarting", m, "wait_s")
    return (f"The model service on the Spark is restarting for a configuration change, and your {duration(m.wait_s)} "
            "ran out. Try again in a minute.")


def _llama_swap_down(m: Moment) -> str:
    _need("llama_swap_down", m, "wait_s", "words")
    return f"The model service on the Spark isn't answering, and your {duration(m.wait_s)} ran out. {_ALERT[m.words]}"


def _draining(m: Moment) -> str:
    _need("draining", m, "model_label", "wait_s", "drain_for", "words")
    for_room = m.drain_for == "make-room"
    once = ("" if m.inflight < 1 else f" once its {m.inflight} request in flight finishes" if m.inflight == 1
            else f" once its {m.inflight} requests in flight finish")
    ran_out = (f"{_start(m.model_label)} is being unloaded{' for make-room' if for_room else ''}{once}, and your "
               f"{duration(m.wait_s)} ran out.")
    if not for_room:
        return f"{ran_out} Try again in a minute."
    if m.words == "dan":  # make-room's hold is Dan's to load into (rule 4)
        return (f"{ran_out} Try again in a minute: your request can load it again, into the room make-room holds for "
                "you, if it fits.")
    _need("draining", m, "key_label")
    return (f"{ran_out} It won't load for {m.key_label} while make-room's hold stands. The hold ends when Dan runs "
            "`spark make-room --done` on the Spark.")


def _footprint_suspect(m: Moment) -> str:
    _need("footprint_suspect", m, "model_label", "key_label", "model_command", "brake_at")
    return (f"Not loading {m.model_label} for {m.key_label}: it was loading when the brake fired at "
            f"{_clock(m.brake_at)}, so only Dan can load it again: `spark load {m.model_command}` on the Spark, or "
            f"one of Dan's requests from {_DANS_KEYS}, which loads it if it fits.")


def _model_not_found(m: Moment) -> str:
    if m.asked_name is None:  # "" is a name a client can send: it reads as "that name", below
        raise ValueError("model_not_found's words need asked_name")
    _need("model_not_found", m, "models", "words")
    asked = _outside(m.asked_name)  # the client's own text
    none = f"There's no model called {asked} here." if asked is not None else "There's no model by that name here."
    listed = _and([f"{label} ({name})" for label, name in m.models])
    update = _pi_step(m, "updates pi's list")
    return f"{none} The models are {listed}.{update}"


def _pi_step(m: Moment, does: str) -> str:
    """The step that updates a pi's model list, with a space before it: the Mac's for a pi key of Dan's (its label's
    first word is "pi"), agent's own for agent; none for another key, such as the web UI's, whose list isn't pi's (the
    controller's ruling on Task 7's review)."""
    if m.words == "dan":
        return f" On the Mac, `make clients` {does}." if m.key_label.split()[:1] == ["pi"] else ""
    return f" On the Spark, as `agent`, `{_AGENTS_CLIENTS}` {does}."


def _too_many_requests(m: Moment) -> str:
    _need("too_many_requests", m, "key_label")
    return (f"{_start(m.key_label)} already has as many requests waiting or open as its key allows; this one wasn't "
            "queued. Try again when one finishes.")


def _route_not_served(m: Moment) -> str:
    return f"This address isn't served here: the Spark's model API answers only {_and(list(_SERVED))}."


def _concurrency_limit(m: Moment) -> str:
    _need("concurrency_limit", m, "model_label")
    return f"Too many requests for {m.model_label} at once; try again in a moment."


_WORDS: dict[str, Callable[[Moment], str]] = {
    "no_fit": _no_fit, "loading": _loading, "held_by_brake": _held_by_brake, "footprint_suspect": _footprint_suspect,
    "load_failed": _load_failed, "not_downloaded": _not_downloaded, "restarting": _restarting,
    "llama_swap_down": _llama_swap_down, "draining": _draining, "gate_down": _gate_down,
    "model_not_found": _model_not_found, "too_many_requests": _too_many_requests,
    "route_not_served": _route_not_served, "concurrency_limit": _concurrency_limit,
}


# ---------------------------------------------------------------------------------------------------------------------
# Notifications, on Dan's phone (plan.md, *Notifications, on the phone*). One per event; each type's priority, or off,
# is the registry's. A notification names the stack's models by their labels and anything else by its short process
# name and user (rule 7). pi never sees one, so text from outside the registry is shown as it is, on one line, but for
# a client's model name, shown only when it reads as one, since the lock screen shows it.


@dataclass(frozen=True)
class Notification:
    type: str
    priority: str  # high, default or low: the registry's for the type
    message: str


# Each refusal sends exactly one notification (plan.md, 2026-10-07): these two codes their own type, every other code
# `refused`. gate_down sends none (the failure notifier's alert says it), nor does the front's 401 (its journal has it).
REFUSAL_NOTIFICATION = {"footprint_suspect": "footprint_suspect", "load_failed": "load_failed"}
_NOT_SENT = ("gate_down",)
# A burst's fields, by type: a refusal's, per model, key and code; a failed load's, per model, since one failed load is
# one event, whoever was waiting on it (the controller's ruling on Task 7's review).
_BURST = {"refused": ("model_label", "key_label", "count", "since", "code"),
          "footprint_suspect": ("model_label", "key_label", "count", "since", "code"),
          "load_failed": ("model_label", "count", "since", "code")}

# Each type's *When* and *Example*, from plan.md's table, for the notifications page (`spark docs notifications`). The
# tests hold each example to what `notification` builds from the plan's moment.
NOTIFICATION_DOC: dict[str, tuple[str, str]] = {
    "brake_fired": (
        "the brake fired: it names what it unloaded, and each further unload in the same episode sends a short "
        "follow-up; while the gate is down, the brake sends it itself, and the gate, once back, skips what the brake "
        "already sent",
        "*Brake on brightroar at 03:12: 19.6 GiB available, under the 20 GiB line. Unloaded the coder, which was "
        "loading; new loads are paused. They resume by themselves after 5 min above 28 GiB available.* Then, if it "
        "must unload more: *Brake, 03:13: also unloaded Gemma and the embeddings, both idle.* Sent by the brake while "
        "the gate is down, it ends *They resume once the gate is back and memory has stayed above 28 GiB available "
        "for 5 min.* Within the hour after an automatic release, the hold waits for Dan, and it ends *It fired within "
        "an hour of the automatic release at 03:40, so they stay paused until you release them: on the Spark, "
        "`make brake-release`.*"),
    "brake_needs_release": (
        "a hold found after a reboot",
        "*After the reboot, new loads are still paused from the brake at 02:58. On the Spark, `make brake-release` "
        "resumes them.*"),
    "gate_down": (
        "the failure notifier: the gate stopped",
        "*The gate on brightroar stopped at 09:14 (it crashed; it is restarting). Loaded models still answer; new "
        "loads are refused until it's back. On the Spark, `make doctor` shows what's wrong.*"),
    "front_down": (
        "the failure notifier: the front stopped",
        "*The front on brightroar stopped at 09:14 (it crashed; it is restarting). Requests wait for it, and any in "
        "flight were cut off. On the Spark, `make doctor` shows what's wrong.*"),
    "llama_swap_down": (
        "the failure notifier, or the gate when it stops answering",
        "*The model service on brightroar stopped at 09:14. No model answers until it's back; requests wait, then are "
        "refused. On the Spark, `make doctor` shows what's wrong.*"),
    "brake_down": (
        "the failure notifier: the brake stopped",
        "*The memory brake on brightroar stopped at 09:14 (it crashed; it is restarting within 2 s). earlyoom stays "
        "the backstop. On the Spark, `make doctor` shows what's wrong.*"),
    "back_up": (
        "the gate, once it, the front, llama-swap or the brake has run again for 60 s after a crash, so a crash loop "
        "doesn't alternate it with the `*_down` alerts",
        "*The gate on brightroar has been running again for a minute, after 12 s down. New loads work again.*"),
    "refused": (
        "a request was refused, whoever asked",
        "*Refused the coder for pi on the Mac: needs 41 GiB, 18 free for a load; python3 (chendaniely) holds 32. Free "
        "space with `spark make-room 41G` on the Spark, then try again.* A burst of the same refusal (model, key and "
        "code) goes as one: *Didn't load the coder for agent 4 more times since 09:12: same reason.*"),
    "footprint_suspect": (
        "an `agent` request for the model that was loading when the brake fired",
        "*Didn't load the coder for agent: it was loading when the brake fired at 03:12. On the Spark, `spark load "
        "coder` allows it again; your own requests load it if it fits.*"),
    "load_failed": (
        "a start failed or passed its deadline",
        "*The coder failed to load: the engine stopped with \"failed to load model\". On the Spark, `spark logs coder` "
        "shows the engine's last lines.*"),
    "brake_released": (
        "the gate lifted the brake's hold",
        "*Brake released at 03:40, 64 GiB available. Reloaded Gemma and the embeddings. The coder was loading when it "
        "fired, so it loads again only when you ask.*"),
    "room_hold_ended": (
        "make-room's hold ended: `--done`, its time, a reboot, or used up by Dan's own loads",
        "*make-room's hold for you ended (`--done`), all 40 GiB of it unused. Nothing to reload: the coder loads on "
        "its next request.* When it had unloaded always-loaded models: *make-room's hold for you ended (`--done`), "
        "all 40 GiB of it unused. Reloading Gemma.*"),
    "resident_waiting": (
        "an always-loaded model didn't fit when it was to reload: after a hold ended, at boot, after the brake's "
        "release, after `make apply`'s restart or the model service's, or after it stopped outside the gate",
        "*Gemma didn't fit after the hold ended: it needs 32 GiB, and 9 GiB is free for a load. It loads by itself "
        "once there's room.*"),
    "apply_restarted": (
        "`make apply` restarted the model service",
        "*make apply restarted the model service at 14:02. Gemma, the embeddings and whisper reloaded; the coder loads "
        "on its next request.*"),
    "load_started": ("a cold load started", "*Loading the coder for pi on the Mac (24 s last time)…*"),
    "loaded": ("a load finished", "*Loaded the coder in 24 s.*"),
    "unloaded": ("an idle unload, make-room or `spark unload`; or the gate's own: a start past its deadline, or an "
                 "unload it resumed with no why saved", "*Unloaded the coder after 60 min idle.*"),
    "waiting": (
        "a request started waiting for memory, the brake, the load slot, or Dan (`footprint_suspect`)",
        "*Waiting for memory: the coder for pi on the Mac, up to 30 s. It needs 41 GiB, and 18 GiB is free for a "
        "load.*"),
    "pin_ended": ("a pin's time ran out", "*The pin on the coder ended at 18:00; it unloads after 60 min idle.*"),
    "memory_warning": (
        "available memory fell under the warn line, 28 GiB (rule 5's warning), once per fall",
        "*Memory is getting low on brightroar: 27.4 GiB available, under the 28 GiB warning line. The brake acts at "
        "20.*"),
}


def refusal_notification(code: str, *, request_id: str, ticket_id: str | None = None) -> tuple[str, str] | None:
    """The notification a refusal sends, as (type, event key), or None for gate_down, whose alert is the failure
    notifier's. Each refusal is its request's own (`refusal:<request id>`), but load_failed: one failed load is one
    event, whoever was waiting on it, so it is keyed by the load's ticket, `load-failed:<ticket id>`, and the requests
    that joined that load share it (the controller's ruling on Task 7's review). Its prefix is its own, since
    `load_started` sends `load:<ticket id>` for the same load."""
    if code not in _STATUS:
        raise ValueError(f"{code!r} isn't a refusal with words; the codes are {', '.join(_STATUS)}")
    if code in _NOT_SENT:
        return None
    kind = REFUSAL_NOTIFICATION.get(code, "refused")
    if kind == "load_failed":
        if not ticket_id:
            raise ValueError("load_failed's notification is its load's: it needs ticket_id")
        return kind, f"load-failed:{ticket_id}"
    return kind, f"refusal:{request_id}"


def notification(type: str, registry: Registry, **fields: Any) -> Notification | None:  # noqa: A002 (the plan's name)
    """The notification `type`, in Dan's words, from `fields` (phase-2a.md, Task 7's *The notifications' fields*), at
    the registry's priority; None when the registry has the type `off`. Its words are built either way, so a field
    left out, or one the type doesn't take, is a ValueError naming it, even while the type is off."""
    if type not in _NOTIFY:
        raise ValueError(f"{type!r} isn't a notification type; the types are {', '.join(NOTIFICATION_TYPES)}")
    given = _fields(type, fields)
    with localcontext(prec=_DIGITS):
        message = _NOTIFY[type](registry, given)
    priority = registry.notifications[type]
    return None if priority == "off" else Notification(type, priority, message)


def _fields(kind: str, given: dict[str, Any]) -> dict[str, Any]:
    """`given`, with each field `kind` takes and wasn't given set to None (or its default); a field it doesn't take
    is refused. A refusal's type with `count` is its burst, which takes the burst's fields only."""
    if kind in _BURST and "count" in given:
        takes: dict[str, Any] = dict.fromkeys(_BURST[kind])
    else:
        takes = dict(_TAKES[kind])
    for name in given:
        if name not in takes:
            raise ValueError(f"{kind}'s words take no {name}; they take {', '.join(takes)}")
    return {**takes, **given}


def _needs(kind: str, f: dict[str, Any], *names: str) -> None:
    """Refuse a field `kind`'s words use that is missing: None, an empty text or an empty list."""
    for name in names:
        if f.get(name) is None or f[name] == "" or f[name] == []:
            raise ValueError(f"{kind}'s words need {name}")


def _one_of(kind: str, name: str, value: Any, allowed: tuple[str, ...] | dict[str, Any]) -> None:
    if value not in allowed:
        raise ValueError(f"{kind}'s {name} must be one of {', '.join(allowed)}, not {value!r}")


def _host() -> str:
    """The box's short name, read when a notification is built: brightroar."""
    return socket.gethostname().split(".")[0]


def _brief(seconds: float | Decimal) -> str:
    """A duration as a notification says it: `24 s`, `5 min`, `1 min 30 s`, `2 h 5 min`; never below 0."""
    total = max(0, int(_gib(seconds).to_integral_value(ROUND_HALF_UP)))
    if total < 60:
        return f"{total} s"
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    parts = [f"{hours} h"] if hours else []
    if minutes:
        parts.append(f"{minutes} min")
    if secs and not hours:
        parts.append(f"{secs} s")
    return " ".join(parts)


def _minutes(minutes: float | Decimal) -> str:
    return f"{max(0, _near(minutes))} min"


def _bare(shown: int) -> str:
    """A whole GiB with no unit, as the phone's short forms show one: 18, or *more than 400*."""
    return "more than 400" if shown > _TOO_BIG_GIB else str(shown)


def _number(value: float | Decimal) -> str:
    """A line as the registry gives it, with no unit: 20, or 20.5."""
    return f"{_gib(value).normalize():f}"


def _room_words(free: float | Decimal) -> str:
    """Free for a load, whole and rounded down: *18 GiB is free for a load*, or *nothing is*, never 0 or below."""
    shown = _down(free)
    return f"{_size(shown)} is free for a load" if shown >= 1 else "nothing is free for a load"


def _times(count: int) -> str:
    return "1 more time" if count == 1 else f"{count} more times"


# The brake's unload events name each model's state (Task 13's BrakeEvent.state): the words for each.
_BRAKE_STATES = {"starting": "loading", "idle": "idle", "answering": "answering"}


def _brake_unloaded(unloaded: list[tuple[str, str | None]]) -> str:
    """What the brake unloaded, each in its state: *the coder, which was loading*; *Gemma and the embeddings, both
    idle*; *the coder (loading) and Gemma (idle)*. A state of None (the gate's record was missing) isn't named."""
    for _, state in unloaded:
        if state is not None:
            _one_of("brake_fired", "state", state, _BRAKE_STATES)
    items = [(label, None if state is None else _BRAKE_STATES[state]) for label, state in unloaded]
    states = {state for _, state in items}
    if len(items) == 1:
        label, state = items[0]
        return f"{label}, which was {state}" if state else label
    if len(states) == 1 and None not in states:
        return f"{_and([label for label, _ in items])}, {'both' if len(items) == 2 else 'all'} {states.pop()}"
    return _and([f"{label} ({state})" if state else label for label, state in items])


def _brake_fired(registry: Registry, f: dict[str, Any]) -> str:
    _needs("brake_fired", f, "at")
    if f["follow_up"]:  # always an unload: an empty list is a field left out
        _needs("brake_fired", f, "unloaded")
        return f"Brake, {_clock(f['at'])}: also unloaded {_brake_unloaded(f['unloaded'])}."
    if f["unloaded"] is None:
        raise ValueError("brake_fired's words need unloaded")
    # An empty list: the brake paused new loads with nothing of the stack's loaded, the memory taken by something
    # outside it (the controller's ruling at Task 7's review, worded at Task 13).
    unloaded = (f"Unloaded {_brake_unloaded(f['unloaded'])}" if f["unloaded"]
                else "Nothing of the stack's was loaded, so there was nothing to unload")
    _needs("brake_fired", f, "available_gib", "line_gib", "release_waits_for_dan")
    if f["release_waits_for_dan"]:  # a brake within the hour after an automatic release (rule 5): it says so itself,
        _needs("brake_fired", f, "released_at")  # and why, since the last one resumed by itself (the review's I-2)
        resume = (f"It fired within an hour of the automatic release at {_clock(f['released_at'])}, so they stay "
                  "paused until you release them: on the Spark, `make brake-release`.")
    else:
        _needs("brake_fired", f, "release_after_s")
        warn, after = _line(registry.brake.warn_gib), _brief(f["release_after_s"])
        if f["by_brake"]:  # nothing resumes without the gate
            resume = f"They resume once the gate is back and memory has stayed above {warn} available for {after}."
        else:
            resume = f"They resume by themselves after {after} above {warn} available."
    return (f"Brake on {_host()} at {_clock(f['at'])}: {_reading(f['available_gib'])} available, under the "
            f"{_line(f['line_gib'])} line. {unloaded}; new loads are paused. {resume}")


def _brake_needs_release(registry: Registry, f: dict[str, Any]) -> str:
    """A hold found after a reboot, which waits for Dan. A brake within the hour after an automatic release says the
    same in its own brake_fired (release_waits_for_dan), so it sends no second alert (the controller's ruling,
    2026-10-07)."""
    _needs("brake_needs_release", f, "fired_at")
    return (f"After the reboot, new loads are still paused from the brake at {_clock(f['fired_at'])}. On the Spark, "
            "`make brake-release` resumes them.")


# The four the failure notifier sends: what stopped, what that means, and how soon systemd starts it again (the brake's
# 2 s matters most: nothing watches memory meanwhile). result_words is the notifier's result in words (Task 26), or
# None for llama-swap that stopped answering with its unit still up, which nothing restarts.
_DOWN = {
    "gate_down": ("The gate", "Loaded models still answer; new loads are refused until it's back.", ""),
    "front_down": ("The front", "Requests wait for it, and any in flight were cut off.", ""),
    "llama_swap_down": ("The model service", "No model answers until it's back; requests wait, then are refused.", ""),
    "brake_down": ("The memory brake", "earlyoom stays the backstop.", " within 2 s"),
}


def _down_alert(kind: str) -> Callable[[Registry, dict[str, Any]], str]:
    name, effect, within = _DOWN[kind]

    def words(registry: Registry, f: dict[str, Any]) -> str:
        _needs(kind, f, "at")
        result = _one_line(f["result_words"] or "")
        why = f" ({result}; it is restarting{within})" if result else ""
        return (f"{name} on {_host()} stopped at {_clock(f['at'])}{why}. {effect} On the Spark, `make doctor` shows "
                "what's wrong.")

    return words


_UNITS = {"gate": ("The gate", "New loads work again."), "front": ("The front", "Requests go through again."),
          "llama-swap": ("The model service", "Models answer again."),
          "brake": ("The memory brake", "Memory is watched again.")}


def _back_up(registry: Registry, f: dict[str, Any]) -> str:
    _needs("back_up", f, "unit", "down_s")
    _one_of("back_up", "unit", f["unit"], _UNITS)
    name, again = _UNITS[f["unit"]]
    return f"{name} on {_host()} has been running again for a minute, after {_brief(f['down_s'])} down. {again}"


def _burst(kind: str, f: dict[str, Any]) -> str:
    """The repeats within 10 minutes, as one (Task 14): of one refusal, the same model, key and code; of a failed load,
    the same model. The model passes the single form's gate: on one line, and a client's name only when it reads as
    a model's (the review's I-1), else, or with none, the burst names the request."""
    _needs(kind, f, *(name for name in _BURST[kind] if name != "model_label"))
    _sent_as(kind, f["code"])
    if not isinstance(f["count"], int) or f["count"] < 1:
        raise ValueError(f"{kind}'s words need count to be 1 or more")
    since = f"{_times(f['count'])} since {_clock(f['since'])}"
    model = _shown_model(f["code"], f["model_label"])
    if kind == "load_failed":  # each a load of its own: the engine's line may differ, so no "same reason"
        _needs(kind, f, "model_label")
        return f"{_start(_one_line(f['model_label']))} failed to load {since}."
    if not model:
        return f"Refused a request from {f['key_label']} {since}: same reason."
    # A refusal that never got as far as a load (FRONT_CODES: no such model, a key's cap, an address) wasn't one.
    did = "Didn't load" if f["code"] in CODES_409 else "Refused"
    return f"{did} {model} for {f['key_label']} {since}: same reason."


def _shown_model(code: str, text: str | None) -> str:
    """A model as the phone may show it: on one line; for model_not_found, the name a client asked for, only when it
    reads as a model's name, else nothing."""
    line = _one_line(text or "")
    if code == "model_not_found" and not _is_model_name(line):
        return ""
    return line


def _sent_as(kind: str, code: str) -> None:
    """Refuse a refusal code that `kind` doesn't word: each refusal sends exactly one type, gate_down none."""
    if code not in _STATUS:
        raise ValueError(f"{code!r} isn't a refusal with words; the codes are {', '.join(_STATUS)}")
    sent = None if code in _NOT_SENT else REFUSAL_NOTIFICATION.get(code, "refused")
    if sent == kind:
        return
    if kind == "refused":
        goes = f"it goes as {sent}" if sent else "the failure notifier's alert says it"
        raise ValueError(f"refused doesn't word {code}: {goes}")
    raise ValueError(f"{kind}'s burst is for {kind}, not {code}")


def _refused(registry: Registry, f: dict[str, Any]) -> str:
    if f.get("count") is not None:
        return _burst("refused", f)
    _needs("refused", f, "code", "moment")
    code, m = f["code"], f["moment"]
    _sent_as("refused", code)
    refusal(code, m)  # the moment holds every field the refusal's own words need, or this names the one missing
    return _PHONE[code](registry, m)


def _lead(m: Moment, model: str | None = None) -> str:
    """*Refused the coder for pi on the Mac: *, or without whichever of the two the refusal has none of."""
    model = m.model_label if model is None else model
    if model and m.key_label:
        return f"Refused {model} for {m.key_label}: "
    if model:
        return f"Refused {model}: "
    return f"Refused a request from {m.key_label}: " if m.key_label else "Refused a request: "


def _ran_out(m: Moment) -> str:
    return f"the {_brief(m.wait_s)} wait ran out"


def _again(m: Moment, step: str) -> str:
    """A step to try again: Dan's own request's, never one for agent's, which Dan didn't make."""
    return f" {step}" if m.words == "dan" else ""


def _phone_no_fit(registry: Registry, m: Moment) -> str:
    need, free = _up(m.needed_gib), _down(m.free_gib)
    held = _near(m.held_gib) if m.hold_counted else 0
    room = f"{_bare(free)} free for a load" if free >= 1 else "nothing free for a load"
    if held:
        room += f" while make-room holds {_size(held)} for you"
    labels = {model.label for model in registry.models.values()}
    outside = [h for h in m.holders if h.name not in labels]  # the loaded models are no news; anything else is
    holds = "; " + _and([f"{_one_line(h.name)} holds {_bare(_near(h.gib))}" for h in outside]) if outside else ""
    if need > _TOO_BIG_GIB:
        step = "The Spark can never free that much."
    elif held and _hold_is_the_way(m):
        step = "On the Spark, `spark make-room --done` ends the hold."
    elif m.words == "dan":
        step = f"Free space with `spark make-room {need}G` on the Spark, then try again."
    else:  # make-room's hold would bar agent's load (rule 4), so no command of Dan's makes room for it
        step = "On the Spark, `spark status` shows what's using memory."
    return f"{_lead(m)}needs {_size(need)}, {room}{holds}. {step}"


def _phone_loading(registry: Registry, m: Moment) -> str:
    return (f"{_lead(m)}{_ran_out(m)} while {m.loading_label} loads, since one model loads at a time."
            f"{_again(m, 'Try again in a minute.')}")


def _phone_held_by_brake(registry: Registry, m: Moment) -> str:
    paused = (f"{_lead(m)}new loads are paused since memory ran low at {_clock(m.brake_at)} "
              f"({_reading(m.brake_available_gib)} available)")
    if m.release_waits_for_dan:
        return f"{paused}, until you release them: on the Spark, `make brake-release`."
    return (f"{paused}. They resume by themselves after {_brief(m.release_after_s)} above {_line(m.warn_gib)} "
            "available, if what would reload fits; on the Spark, `make brake-release` resumes them now.")


def _phone_not_downloaded(registry: Registry, m: Moment) -> str:
    size = f" ({_size(_near(m.download_gib))})" if m.download_gib is not None and _near(m.download_gib) else ""
    return f"{_lead(m)}it isn't downloaded yet. On the Spark, `make pull` fetches it{size}."


def _phone_restarting(registry: Registry, m: Moment) -> str:
    return (f"{_lead(m)}{_ran_out(m)} while the model service restarts for a configuration change."
            f"{_again(m, 'Try again in a minute.')}")


def _phone_llama_swap_down(registry: Registry, m: Moment) -> str:
    return f"{_lead(m)}the model service isn't answering. On the Spark, `make doctor` shows what's wrong."


def _phone_draining(registry: Registry, m: Moment) -> str:
    for_room = m.drain_for == "make-room"
    base = f"{_lead(m)}it is being unloaded{' for make-room' if for_room else ''}, and {_ran_out(m)}."
    if for_room and m.words == "dan":  # make-room's hold is Dan's to load into (rule 4)
        return (f"{base} Try again in a minute: your request can load it into the room make-room holds for you, if "
                "it fits.")
    if for_room:
        return (f"{base} It won't load for {m.key_label} while your hold stands; on the Spark, "
                "`spark make-room --done` ends it.")
    return f"{base}{_again(m, 'Try again in a minute.')}"


# A client's model name reaches the lock screen only when it reads as one: never a sentence a client wrote, and never
# a web address, which a phone may make a link (the review's minor 4). Real ids pass: Qwen/Qwen3-8B, qwen3:8b.
_MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,79}")


def _is_model_name(text: str) -> bool:
    return _MODEL_NAME.fullmatch(text) is not None and "://" not in text


def _phone_model_not_found(registry: Registry, m: Moment) -> str:
    asked = _shown_model("model_not_found", m.asked_name)
    if asked:
        said = f"{_lead(m, asked)}there's no model by that name."
    else:
        said = f"{_lead(m, '')}it named no model the Spark has."
    return said + _pi_step(m, "updates pi's list" if m.words == "dan" else "updates agent's pi list")


def _phone_too_many_requests(registry: Registry, m: Moment) -> str:
    return (f"{_lead(m)}its key already has as many requests waiting or open as it allows."
            f"{_again(m, 'Try again when one finishes.')}")


def _phone_route_not_served(registry: Registry, m: Moment) -> str:
    return f"{_lead(m, '')}the Spark's model API doesn't serve that address."


def _phone_concurrency_limit(registry: Registry, m: Moment) -> str:
    return f"{_lead(m)}too many requests for it at once.{_again(m, 'Try again in a moment.')}"


_PHONE: dict[str, Callable[[Registry, Moment], str]] = {
    "no_fit": _phone_no_fit, "loading": _phone_loading, "held_by_brake": _phone_held_by_brake,
    "not_downloaded": _phone_not_downloaded, "restarting": _phone_restarting,
    "llama_swap_down": _phone_llama_swap_down, "draining": _phone_draining, "model_not_found": _phone_model_not_found,
    "too_many_requests": _phone_too_many_requests, "route_not_served": _phone_route_not_served,
    "concurrency_limit": _phone_concurrency_limit,
}


def _footprint_suspect(registry: Registry, f: dict[str, Any]) -> str:
    if f.get("count") is not None:
        return _burst("footprint_suspect", f)
    _needs("footprint_suspect", f, "model_label", "key_label", "fired_at", "command")
    return (f"Didn't load {f['model_label']} for {f['key_label']}: it was loading when the brake fired at "
            f"{_clock(f['fired_at'])}. On the Spark, `spark load {f['command']}` allows it again; your own requests "
            "load it if it fits.")


def _load_failed(registry: Registry, f: dict[str, Any]) -> str:
    if f.get("count") is not None:
        return _burst("load_failed", f)
    _needs("load_failed", f, "model_label", "command")
    began = f"{_start(f['model_label'])} failed to load"
    after = f"On the Spark, `spark logs {f['command']}` shows the engine's last lines."
    said = _one_line(f["engine_said"] or "")  # quoted either way: pi never sees a notification
    if said:
        return f'{began}: the engine stopped with "{said}". {after}'
    if f["deadline_s"] is not None:
        return f"{began}: it didn't finish within {_brief(f['deadline_s'])}. {after}"
    return f"{began}: the engine stopped. {after}"


def _brake_released(registry: Registry, f: dict[str, Any]) -> str:
    _needs("brake_released", f, "at", "available_gib")
    words = [f"Brake released at {_clock(f['at'])}, {_size(_down(f['available_gib']))} available."]
    words.append(f"Reloaded {_and(f['reloaded'])}." if f["reloaded"] else "Nothing to reload.")
    if f["loading_label"]:
        words.append(f"{_start(f['loading_label'])} was loading when it fired, so it loads again only when you ask.")
    return " ".join(words)


def _hold_usage(unused: float | Decimal, total: float | Decimal) -> str:
    """How much of make-room's hold went unused: *all 40 GiB of it*, *9 of its 41 GiB*, or *all of it used*."""
    left, whole = _near(unused), _near(total)
    if left >= whole:
        return f"all {_size(whole)} of it unused"
    if left < 1:
        return "all of it used"
    return f"{left} of its {_size(whole)} unused"


def _reloads(reloading: list[str], next_label: str | None) -> str:
    """What reloads now, one at a time, and the on-demand model that loads when asked."""
    if reloading:
        then = f"; {next_label} loads on its next request" if next_label else ""
        return f"Reloading {', then '.join(reloading)}{then}."
    if next_label:
        return f"Nothing to reload: {next_label} loads on its next request."
    return "Nothing to reload."


_HOLD_ENDED = {"--done": " (`--done`)", "time": " (its time ran out)", "reboot": " (the Spark rebooted)",
               "used": ": your own loads used it up"}


def _room_hold_ended(registry: Registry, f: dict[str, Any]) -> str:
    _needs("room_hold_ended", f, "why", "unused_gib", "total_gib")
    _one_of("room_hold_ended", "why", f["why"], _HOLD_ENDED)
    usage = "" if f["why"] == "used" else f", {_hold_usage(f['unused_gib'], f['total_gib'])}"
    return (f"make-room's hold for you ended{_HOLD_ENDED[f['why']]}{usage}. "
            f"{_reloads(f['reloading'], f['next_label'])}")


# When an always-loaded model reloads (Tasks 15 and 17), and so when it can find no room.
_AFTER = {"hold": "after the hold ended", "brake": "after the brake's release", "apply": "after make apply's restart",
          "boot": "at boot", "restart": "after the model service restarted",
          "crash": "after it stopped outside the gate"}


def _resident_waiting(registry: Registry, f: dict[str, Any]) -> str:
    _needs("resident_waiting", f, "label", "needed_gib", "free_gib", "after")
    _one_of("resident_waiting", "after", f["after"], _AFTER)
    return (f"{_start(f['label'])} didn't fit {_AFTER[f['after']]}: it needs {_size(_up(f['needed_gib']))}, and "
            f"{_room_words(f['free_gib'])}. It loads by itself once there's room.")


def _apply_restarted(registry: Registry, f: dict[str, Any]) -> str:
    _needs("apply_restarted", f, "at")
    parts = []
    if f["reloaded"]:
        parts.append(f"{_and(f['reloaded'])} reloaded")
    if f["on_demand"]:
        its = "loads on its" if len(f["on_demand"]) == 1 else "load on their"
        parts.append(f"{_and(f['on_demand'])} {its} next request")
    done = f"make apply restarted the model service at {_clock(f['at'])}."
    return f"{done} {_start('; '.join(parts))}." if parts else done


def _load_started(registry: Registry, f: dict[str, Any]) -> str:
    _needs("load_started", f, "label")
    asker = f" for {f['key_label']}" if f["key_label"] else ""  # a resident's reload, or Dan's own command, has none
    last = f" ({_brief(f['last_s'])} last time)" if f["last_s"] is not None else ""
    return f"Loading {f['label']}{asker}{last}…"


def _loaded(registry: Registry, f: dict[str, Any]) -> str:
    _needs("loaded", f, "label", "seconds")
    return f"Loaded {f['label']} in {_brief(f['seconds'])}."


def _unloaded(registry: Registry, f: dict[str, Any]) -> str:
    _needs("unloaded", f, "label", "why")
    _one_of("unloaded", "why", f["why"], ("idle", "make-room", "unload", "late_start", "unknown"))
    if f["why"] == "idle":
        _needs("unloaded", f, "idle_min")
        return f"Unloaded {f['label']} after {_minutes(f['idle_min'])} idle."
    if f["why"] == "make-room":
        return f"Unloaded {f['label']} for make-room."
    if f["why"] == "late_start":  # Task 15's abort of a start past its deadline
        return f"Unloaded {f['label']}: its start ran past its deadline."
    if f["why"] == "unknown":  # resumed with no why saved: no command is named for it (Task 13's review)
        return f"Unloaded {f['label']}."
    return f"Unloaded {f['label']}, as `spark unload` asked."


def _waiting(registry: Registry, f: dict[str, Any]) -> str:
    _needs("waiting", f, "label", "key_label", "why", "wait_s")
    _one_of("waiting", "why", f["why"], ("memory", "brake", "slot", "dan"))
    who = f"{f['label']} for {f['key_label']}, up to {_brief(f['wait_s'])}"
    if f["why"] == "memory":
        _needs("waiting", f, "needed_gib", "free_gib")
        return (f"Waiting for memory: {who}. It needs {_size(_up(f['needed_gib']))}, and "
                f"{_room_words(f['free_gib'])}.")
    if f["why"] == "brake":  # as held_by_brake says it: by itself, unless the pause waits for Dan
        _needs("waiting", f, "release_waits_for_dan")
        if f["release_waits_for_dan"]:
            return (f"Waiting while new loads are paused: {who}. They stay paused until you release them: on the "
                    "Spark, `make brake-release`.")
        _needs("waiting", f, "release_after_s")
        return (f"Waiting while new loads are paused: {who}. They resume by themselves after "
                f"{_brief(f['release_after_s'])} above {_line(registry.brake.warn_gib)} available, if what would "
                "reload fits; on the Spark, `make brake-release` resumes them now.")
    if f["why"] == "slot":
        _needs("waiting", f, "loading_label")
        return (f"Waiting its turn to load: {who}. {_start(f['loading_label'])} is loading, and one model loads at a "
                "time.")
    _needs("waiting", f, "command")
    return (f"Waiting for you: {who}. It was loading when the brake fired; on the Spark, `spark load {f['command']}` "
            "allows it again.")


def _pin_ended(registry: Registry, f: dict[str, Any]) -> str:
    _needs("pin_ended", f, "label", "at")
    return f"The pin on {f['label']} ended at {_clock(f['at'])}; {_after_pin(f['idle_min'])}"


def _after_pin(idle_min: float | None) -> str:
    """What a model does once its pin ends: an on-demand one unloads when idle; an always-loaded one stays."""
    if idle_min is None:
        return "it stays loaded, since it's always loaded."
    return f"it unloads after {_minutes(idle_min)} idle."


def _memory_warning(registry: Registry, f: dict[str, Any]) -> str:
    _needs("memory_warning", f, "available_gib", "warn_gib", "brake_gib")
    return (f"Memory is getting low on {_host()}: {_reading(f['available_gib'])} available, under the "
            f"{_line(f['warn_gib'])} warning line. The brake acts at {_number(f['brake_gib'])}.")


# What each type takes, each field's default None but where given (phase-2a.md, Task 7's fields table, with the
# controller's additions of 2026-10-07). A refusal's type also takes the burst's fields, _BURST, with `count`.
_TAKES: dict[str, dict[str, Any]] = {
    "brake_fired": {"at": None, "available_gib": None, "line_gib": None, "unloaded": None, "follow_up": False,
                    "by_brake": False, "release_waits_for_dan": None, "released_at": None, "release_after_s": None},
    "brake_needs_release": {"fired_at": None},
    **{kind: dict.fromkeys(("at", "result_words")) for kind in _DOWN},
    "back_up": dict.fromkeys(("unit", "down_s")),
    "refused": dict.fromkeys(("code", "moment")),
    "footprint_suspect": dict.fromkeys(("model_label", "key_label", "fired_at", "command")),
    "load_failed": dict.fromkeys(("model_label", "command", "engine_said", "deadline_s")),
    "brake_released": {"at": None, "available_gib": None, "reloaded": [], "loading_label": None},
    "room_hold_ended": {"why": None, "unused_gib": None, "total_gib": None, "reloading": [], "next_label": None},
    "resident_waiting": dict.fromkeys(("label", "needed_gib", "free_gib", "after")),
    "apply_restarted": {"at": None, "reloaded": [], "on_demand": []},
    "load_started": dict.fromkeys(("label", "key_label", "last_s")),
    "loaded": dict.fromkeys(("label", "seconds")),
    "unloaded": dict.fromkeys(("label", "why", "idle_min")),
    "waiting": dict.fromkeys(("label", "key_label", "why", "wait_s", "needed_gib", "free_gib", "command",
                              "loading_label", "release_waits_for_dan", "release_after_s")),
    "pin_ended": dict.fromkeys(("label", "at", "idle_min")),
    "memory_warning": dict.fromkeys(("available_gib", "warn_gib", "brake_gib")),
}
_NOTIFY: dict[str, Callable[[Registry, dict[str, Any]], str]] = {
    "brake_fired": _brake_fired, "brake_needs_release": _brake_needs_release,
    **{kind: _down_alert(kind) for kind in _DOWN}, "back_up": _back_up, "refused": _refused,
    "footprint_suspect": _footprint_suspect, "load_failed": _load_failed, "brake_released": _brake_released,
    "room_hold_ended": _room_hold_ended, "resident_waiting": _resident_waiting, "apply_restarted": _apply_restarted,
    "load_started": _load_started, "loaded": _loaded, "unloaded": _unloaded, "waiting": _waiting,
    "pin_ended": _pin_ended, "memory_warning": _memory_warning,
}


# ---------------------------------------------------------------------------------------------------------------------
# Confirmations: what each command prints, on the Spark (plan.md, *Each command says what it did, and how to undo it*).
# Each command's question, `[y/N]`, is part of its line.

BRAKE_RELEASED_WITHOUT_GATE = ("The gate isn't answering, so the hold file was removed directly; nothing reloads until "
                               "the gate is back.")  # make brake-release, falling back to Phase 1's release (Task 23)


def _until(at: datetime | float, now: datetime | None) -> str:
    """When a pin or a hold ends: 18:00 within the next day, else its day too, *Fri 9 Oct, 18:00*."""
    end = _local(at)
    now = (now or datetime.now()).astimezone()
    if timedelta(minutes=-1) <= end - now < timedelta(days=1):
        return f"{end:%H:%M}"
    return f"{end:%a} {end.day} {end:%b}, {end:%H:%M}"


def loaded(label: str, seconds: float | None, idle_min: float | None, command: str) -> str:
    """`spark load`: seconds None when the model was loaded already; idle_min None for an always-loaded model."""
    done = f"Loaded {label} in {_brief(seconds)}." if seconds is not None else f"{_start(label)} is already loaded."
    if idle_min is None:
        return f"{done} It stays loaded, since it's always loaded."
    return f"{done} It unloads after {_minutes(idle_min)} idle; `spark pin {command}` keeps it."


def unloading(label: str, inflight: int) -> str:
    """`spark unload`'s first line, at once."""
    if inflight < 1:
        return f"Unloading {label}…"
    if inflight == 1:
        return f"Unloading {label} once its 1 request in flight finishes…"
    return f"Unloading {label} once its {inflight} requests in flight finish…"


def unloaded(label: str) -> str:
    """`spark unload`'s second line, once the model is gone."""
    return f"Unloaded {label}."


def pinned(label: str, until: datetime | float | None, loaded_s: float | None, command: str, *,
           now: datetime | None = None) -> str:
    """`spark pin`: until None for a pin with no end; loaded_s when the pin loaded the model first."""
    first = f" (loaded it first, {_brief(loaded_s)})" if loaded_s is not None else ""
    stays = f"stays loaded until {_until(until, now)}" if until is not None else "stays loaded, with no end set"
    return f"{_start(label)} {stays}{first}. `spark unpin {command}` ends the pin."


def unpinned(label: str, idle_min: float | None) -> str:
    """`spark unpin`: idle_min None for an always-loaded model."""
    return f"The pin on {label} ended; {_after_pin(idle_min)}"


def room_list(target_gib: float | Decimal | None, free_now_gib: float | Decimal, candidates: list[Candidate],
              unload: list[str], free_after_gib: float | Decimal, *, registry: Registry) -> str:
    """`spark make-room`'s list and its question (plan.md, rule 4): target_gib None for `--all`. A numbered row per
    candidate: its label, its name in brackets for a chat model only, its size, *always loaded* or *loads when asked*,
    then what uses a resident, a pin, a session, its requests in flight and how long the oldest has run, or, for an
    on-demand model with none, how long it has been idle. When unloading every one can't reach the target, the list
    ends at its rows, and room_too_much asks the question; with nothing loaded, it says so."""
    now = _down(free_now_gib)
    if not candidates and (target_gib is None or _gib(free_now_gib) < _gib(target_gib)):
        return "Nothing is loaded, so there is nothing to unload."
    if target_gib is None:
        now_words = f"{_size(now)} free for a load now" if now >= 1 else "nothing free for a load now"
        lines = [f"To unload everything ({now_words}):"]
    else:
        now_words = f"{_size(now)} now" if now >= 1 else "nothing now"
        lines = [f"To make {_size(_up(target_gib))} free for a load ({now_words}):"]
    names = [_candidate_name(c, registry) for c in candidates]
    sizes = [_size(_near(c.gib)) for c in candidates]
    name_width = max(map(len, names), default=0) + 6  # the plan's example: six spaces after the longest name
    size_width = max(map(len, sizes), default=0) + 2
    for number, (c, name, size) in enumerate(zip(candidates, names, sizes, strict=True), 1):
        marks = " · ".join(_candidate_marks(c, registry))
        lines.append(f"{number:>3}  {name:<{name_width}}{size:<{size_width}}{marks}")
    numbers = {c.model: str(number) for number, c in enumerate(candidates, 1)}
    for model in unload:
        if model not in numbers:
            raise ValueError(f"{model!r} isn't on make-room's list")
    if not unload:
        if target_gib is not None:
            lines.append(f"Nothing needs to unload: {_size(now)} is free for a load now. "
                         f"Hold {_size(_up(target_gib))} of it for you? [y/N]")
    elif target_gib is None or _gib(free_after_gib) >= _gib(target_gib):
        which = _and([numbers[model] for model in unload])
        lines.append(f"Unloading {which} leaves {_size(_down(free_after_gib))} free for a load. Unload {which}? [y/N]")
    return "\n".join(lines)


def _candidate_name(c: Candidate, registry: Registry) -> str:
    model = registry.models.get(c.model)
    return f"{c.label} ({c.model})" if model is not None and model.capability == "chat" else c.label


def _candidate_marks(c: Candidate, registry: Registry) -> list[str]:
    marks = ["always loaded" if c.resident else "loads when asked"]
    model = registry.models.get(c.model)
    if c.resident and model is not None and model.used_by:
        marks.append(model.used_by)
    if c.pinned:
        marks.append("pinned")
    if c.session:
        marks.append(f"session: {c.session}")
    if c.inflight:
        age = "" if c.oldest_s is None else f" for {_brief(c.oldest_s)}"
        marks.append(f"1 request in flight{age}" if c.inflight == 1
                     else f"{c.inflight} requests in flight{', the oldest' + age if age else ''}")
    elif not c.resident and c.idle_min is not None:
        marks.append(f"idle {_minutes(c.idle_min)}")
    return marks


def room_held(unloaded: list[str], free_gib: float | Decimal, held_gib: float | Decimal,
              until: datetime | float | None, *, now: datetime | None = None) -> str:
    """`spark make-room <size>`, done: until None for a hold with no time set (`--for` sets one)."""
    first = f"Unloaded {_and(unloaded)}." if unloaded else "Nothing needed to unload."
    ends = f"until {_until(until, now)}, `spark make-room --done` or a reboot" if until is not None else (
        "until `spark make-room --done` or a reboot")
    return (f"{first} {_size(_down(free_gib))} is free for a load, and {_size(_near(held_gib))} of it is held for you "
            f"{ends}; your own requests can load into it, agent's and automatic reloads can't.")


def room_all() -> str:
    """`spark make-room --all`, done."""
    return ("Unloaded everything. The whole box is held for you until `spark make-room --done` or a reboot; your own "
            "requests can load into it.")


def room_too_much(target_gib: float | Decimal, most_gib: float | Decimal) -> str:
    """`spark make-room <size>` asked for more than unloading everything frees: the most it can, and the question."""
    target, most = _up(target_gib), _down(most_gib)
    if most < 1:
        return f"Unloading everything leaves nothing free for a load, not {_bare(target)}."
    return (f"Unloading everything leaves {_size(most)} free for a load, not {_bare(target)}. Free {_bare(most)} and "
            "hold it? [y/N]")


def room_done(unused_gib: float | Decimal, total_gib: float | Decimal, reloading: list[str],
              next_label: str | None) -> str:
    """`spark make-room --done`."""
    return f"Hold ended, {_hold_usage(unused_gib, total_gib)}. {_reloads(reloading, next_label)}"


def brake_released_by_dan(reloading: list[str]) -> str:
    """`make brake-release`, through the gate."""
    if not reloading:
        return "New loads resume. Nothing to reload."
    return f"New loads resume. Reloading {', then '.join(reloading)}…"


def apply_waiting(model_label: str | None, quiet_for_s: float, needed_s: float) -> str:
    """`make apply`, waiting for a quiet moment: model_label the model that answered last, None when none has."""
    quiet, needed = max(0, round(quiet_for_s)), round(needed_s)
    if model_label is None:
        last = "a request is in flight now" if quiet < 1 else f"the last request finished {quiet} s ago"
    else:
        last = f"{model_label} is answering now" if quiet < 1 else f"{model_label} answered {quiet} s ago"
    return (f"Waiting for a quiet moment: {last}, and it needs {needed} s with nothing in flight. Ctrl-C leaves "
            "everything as it was; `make apply-now` restarts now.")


def apply_no_quiet(inflight: int, deadline_s: float = 900) -> str:
    """`make apply`, at its deadline (Task 32's `--deadline-s` shortens it for a drill)."""
    if inflight < 1:
        holding = "holding new requests"
    elif inflight == 1:
        holding = "holding new requests while the 1 in flight finishes"
    else:
        holding = f"holding new requests while the {inflight} in flight finish"
    return f"No quiet minute in {duration(deadline_s)}. Drain now, {holding}? [y/N]"


def apply_now_confirm(inflight: list[str]) -> str:
    """`make apply-now`: inflight the askers' key labels, one per request in flight."""
    if not inflight:
        return "This restarts the model service now; nothing is in flight. Continue? [y/N]"
    count = "the 1 request" if len(inflight) == 1 else f"the {len(inflight)} requests"
    askers = {label: inflight.count(label) for label in inflight}  # each once, in order, with its count past one
    named = ", ".join(label if n == 1 else f"{label} ×{n}" for label, n in askers.items())
    return f"This restarts the model service now and cuts off {count} in flight ({named}). Continue? [y/N]"

"""Dan's commands to the gate (Phase 2a; plan.md, *Each command says what it did, and how to undo it*): `spark load`,
`unload`, `pin`, `unpin`, `make-room` and `logs` on the gate's control socket, which only spark-admin's members and
root reach, and `spark session`, for 2b's hooks, on its status socket. None needs a key: the socket's group says who
may, and the gate knows each caller by its uid. Each prints Task 7's words for what the gate did, or the refusal's
message (cli.main, exit 1). A name is a model's or one of its roles, sent as it is: the gate resolves it as `spark
load` does, and a name in a route is quoted whole.

**How long each waits** (website/design/phase-2a.md, Task 30). Every call's connection and the answer's head within
REQUEST_TIMEOUT_S, so a gate that is down shows within 5 s. `load`, and `pin`, which may load first, then wait up to
the load call's 200 s and 10 more for each line; `unload` and make-room's unloads as long as their drains take, since a
drain waits for its requests in flight however long; make-room's plan, `--done`, `unpin`, `logs` and the sessions
within REQUEST_TIMEOUT_S.

**Streams** (gateproto) are read a line at a time, each progress line told apart by its `kind`, never by its keys, and
printed as it comes. A stream ends at its result line, or at a refusal line, in GateRefusalBody's shape, and its
connection closes there, so a cut after the result never reads as an error after a success. One that ends without
either is the gate stopping partway: GateUnavailable, never a silent success. Ctrl-C ends the wait and the command,
saying what the gate goes on with (exit 130), never the work it has begun: a load already started, a drain, an unload
llama-swap took.

**make-room** asks once, and only on a terminal: without one it exits 2, unless `--yes` answers. Its list starts from
the gate's own figure for free for a load (rule 9's, with its ceiling and starting terms), never MemAvailable less the
reserve; with nothing loaded it stops there.

Only the standard library, gateclient, gateproto and messages at import: build_parser imports this module for every
`spark` command, `spark status --json` and `spark launch` among them (test_tested_against.py); what a handler needs
beyond them it imports itself."""

from __future__ import annotations

import argparse
import math
import os
import re
import sys
import time
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote

from spark import gateclient, gateproto, messages, paths
from spark.gateclient import GateClient, GateError, GateRefused, GateUnavailable

REQUEST_TIMEOUT_S = gateproto.REQUEST_TIMEOUT_S  # every call's connection and head, and a bounded call's whole answer
LOAD_THEN_S = gateproto.LOAD_CALL_TIMEOUT_S + 10  # each line of a load's stream, and a pin's
SESSION_HOLD_RENEW_S = gateproto.SESSION_HOLD_RENEW_S  # `spark session hold` renews its session this often
LOGS_SHOWN = 20  # `spark logs`' lines without -n, as the gate's own default
DURATION_MAX_S = 365 * 86400  # a pin's or a hold's time: a year at most, so its end is a time the clock can show
INTERRUPTED = 130  # Ctrl-C's exit, as a shell gives it
_SIZE = re.compile(r"(\d+(?:\.\d+)?)(?:G|GiB)?", re.ASCII | re.IGNORECASE)
_DURATION = re.compile(r"(\d+)([smhd])", re.ASCII)
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_NUMBER = (int, float)
_NO_REGISTRY = SimpleNamespace(models={})  # make-room's list without the registry: labels only, true all the same


def _now() -> float:
    return time.time()


# Sizes and durations.

def parse_size(text: str) -> Decimal:
    """make-room's size in GiB: `41G`, `41GiB` or `41` → 41, and a decimal one, `40.5G`; anything else, 0 among
    them, is a ValueError naming it."""
    found = _SIZE.fullmatch(text)
    if found is None:
        raise ValueError(f"{text!r} isn't a size: give it in GiB, as 41G, 41GiB or 41")
    size = Decimal(found.group(1))
    if size <= 0:
        raise ValueError(f"{text!r} isn't a size above 0 GiB")
    return size


def parse_duration(text: str) -> int:
    """A pin's or a hold's time, in seconds: `8h` → 28800, `90m` → 5400, `2d` → 172800, `45s` → 45; a year at most.
    Anything else is a ValueError naming it."""
    found = _DURATION.fullmatch(text)
    if found is None:
        raise ValueError(f"{text!r} isn't a duration: give a whole number and a unit, as 90m, 8h or 2d")
    seconds = int(found.group(1)) * _UNITS[found.group(2)]
    if not 0 < seconds <= DURATION_MAX_S:
        raise ValueError(f"{text!r} isn't a duration from 1 s to 365 days")
    return seconds


def _argument(parse):
    """`parse` as an argparse type: its ValueError's own words in the usage error, never argparse's *invalid value*."""
    def convert(text: str):
        try:
            return parse(text)
        except ValueError as err:
            raise argparse.ArgumentTypeError(str(err)) from None

    convert.__name__ = parse.__name__
    return convert


# Saying it.

def _shown(text: str) -> str:
    """`text` with every character a terminal would act on written out (ESC as \\x1b, a newline as \\n): labels come
    from the registry, the key list and callers, and an engine's lines from the engine."""
    return "".join(c if c.isprintable() else c.encode("unicode_escape").decode("ascii") for c in text)


def _say(text: str, *, err: bool = False, end: str = "\n") -> None:
    """Each line of `text`, escaped, at once: flushed, so a stream's progress shows as it comes over SSH's pipe too."""
    print("\n".join(_shown(line) for line in text.split("\n")), end=end, file=sys.stderr if err else sys.stdout,
          flush=True)


def _interrupted(asked: str, label: str | None) -> int:
    _say(messages.stopped_waiting(asked, label), err=True)
    return INTERRUPTED


# The registry, for the labels a line doesn't carry and make-room's list.

def _registry() -> Any | None:
    import yaml

    from spark.registry import load_registry

    try:
        return load_registry(paths.REGISTRY)
    except (OSError, ValueError, yaml.YAMLError, RecursionError):
        return None


def _label(registry: Any | None, name: str) -> str:
    """The model's label, its name or a role resolved as the gate resolves it (gate.app's _resolve: the name first);
    the name as given when the registry won't load or doesn't have it."""
    if registry is None:
        return name
    model = registry.models.get(name) or next((m for m in registry.models.values() if name in m.roles), None)
    return model.label if model is not None else name


def _warn_gib(registry: Any | None) -> float:
    """The brake's warn line, which its waiting words name: the registry's, or the plan's when it won't load."""
    if registry is not None:
        return registry.brake.warn_gib
    from spark.brake import FALLBACK

    return FALLBACK.warn_gib


# The gate's answers.

def _unreadable(what: str) -> GateError:
    return GateError(f"The gate's answer couldn't be read: {what}. {gateclient._step()}")


def _field(answer: dict, name: str, kinds: type | tuple[type, ...], *, none: bool = False) -> Any:
    """`answer[name]`, checked against the shape the gate sends: a GateError in words when it isn't."""
    value = answer.get(name)
    if value is None and none:
        return None
    kinds = kinds if isinstance(kinds, tuple) else (kinds,)
    if (not isinstance(value, kinds) or (isinstance(value, bool) and bool not in kinds)
            or (isinstance(value, float) and not math.isfinite(value))):
        raise _unreadable(f"its {name} is missing, or isn't what it should be")
    return value


def _strings(answer: dict, name: str) -> list[str]:
    value = answer.get(name)
    if not isinstance(value, list) or not all(isinstance(each, str) for each in value):
        raise _unreadable(f"its {name} isn't a list of words")
    return value


def _is_refusal(line: dict) -> bool:
    """A refusal sent as a stream's line: GateRefusalBody's shape, `message` and `code`, with no kind."""
    return line.get("kind") is None and isinstance(line.get("message"), str) and "code" in line


def progress_words(line: dict, label: str, warn_gib: float) -> str | None:
    """A progress line's words, by its `kind` alone (the controller's ruling at Task 12's second re-review): `waiting`
    by command_waiting, `loading` by load_started, `unloading` (UnloadCount, MakeRoomProgress) by unloading,
    `stopping` by still_stopping, `after_load` by unload_after_load. `label` is for a line that carries none, as
    UnloadCount doesn't. A kind added later says nothing (None); a wait whose reason can't be read says only that it
    waits."""
    kind = line.get("kind")
    given = line.get("label")
    label = given if isinstance(given, str) and given else label
    if kind == "waiting":
        try:
            return messages.command_waiting(
                label, line.get("why"), needed_gib=line.get("needed_gib"), free_gib=line.get("free_gib"),
                loading_label=line.get("loading_label"), release_waits_for_dan=line.get("release_waits_for_dan"),
                release_after_s=line.get("release_after_s"), warn_gib=warn_gib)
        except (ValueError, TypeError, ArithmeticError):
            return messages.command_waiting(label, None)
    if kind == "loading":
        last = line.get("last_s")
        number = isinstance(last, _NUMBER) and not isinstance(last, bool) and math.isfinite(last)
        return messages.load_started(label, last if number else None)
    if kind == "unloading":
        inflight = line.get("inflight")
        count = inflight if isinstance(inflight, int) and not isinstance(inflight, bool) and inflight > 0 else 0
        return messages.unloading(label, count)
    if kind == "stopping":
        return messages.still_stopping(label)
    if kind == "after_load":
        return messages.unload_after_load(label)
    return None


def _result(stream: Any, label: str, doing: str, warn_gib: float) -> dict:
    """The stream's result line, each progress line before it said as it comes. A refusal line is GateRefused; a
    stream the gate ended without either, GateUnavailable, saying it stopped partway through `doing`. Leaving the
    `with` closes the connection at once, whatever comes after."""
    with stream:
        for line in stream:
            if line.get("kind") is None:
                if _is_refusal(line):
                    raise GateRefused(409, line)
                return line
            words = progress_words(line, label, warn_gib)
            if words is not None:
                _say(words)
    raise GateUnavailable(messages.stopped_partway(doing, gateclient._words()), sent=True)


def _control() -> GateClient:
    return GateClient(paths.GATE_CONTROL_SOCKET, REQUEST_TIMEOUT_S)


def _status() -> GateClient:
    return GateClient(paths.GATE_STATUS_SOCKET, REQUEST_TIMEOUT_S)


def _clock() -> datetime:
    return datetime.fromtimestamp(_now())


# load, unload, pin, unpin.

def _load(args: argparse.Namespace) -> int:
    registry = _registry()
    label = _label(registry, args.model)
    try:
        lines = _control().stream("/v1/load", {"model": args.model}, then_s=LOAD_THEN_S)
        result = _result(lines, label, f"loading {label}", _warn_gib(registry))
        _say(messages.loaded(_field(result, "label", str), _field(result, "seconds", _NUMBER, none=True),
                             _field(result, "idle_min", _NUMBER, none=True), _field(result, "command", str)))
    except KeyboardInterrupt:
        return _interrupted("load", label)
    return 0


def _unload(args: argparse.Namespace) -> int:
    registry = _registry()
    label = _label(registry, args.model)
    try:
        lines = _control().stream("/v1/unload", {"model": args.model}, then_s=None)
        result = _result(lines, label, f"unloading {label}", _warn_gib(registry))
        if result.get("unloaded") is not True:
            raise _unreadable("its unloaded isn't true")
        _say(messages.unloaded(label, pin_ended=result.get("pin_ended") is True))
    except KeyboardInterrupt:
        return _interrupted("unload", label)
    return 0


def _pin(args: argparse.Namespace) -> int:
    registry = _registry()
    label = _label(registry, args.model)
    until = None if args.duration is None else _now() + args.duration
    try:
        lines = _control().stream("/v1/pin", {"model": args.model, "until": until}, then_s=LOAD_THEN_S)
        result = _result(lines, label, f"pinning {label}", _warn_gib(registry))
        _say(messages.pinned(_field(result, "label", str), _field(result, "until", _NUMBER, none=True),
                             _field(result, "loaded_s", _NUMBER, none=True), _field(result, "command", str),
                             now=_clock()))
    except KeyboardInterrupt:
        return _interrupted("pin", label)
    return 0


def _unpin(args: argparse.Namespace) -> int:
    try:
        answer = _control().delete(f"/v1/pin/{quote(args.model, safe='')}")
        _say(messages.unpinned(_field(answer, "label", str), _field(answer, "idle_min", _NUMBER, none=True)))
    except KeyboardInterrupt:
        return _interrupted("asked", None)
    return 0


# make-room.

def _plan(answer: dict) -> tuple[str, list, list[str], Decimal, bool, Decimal]:
    """/v1/make-room/plan's answer (gateproto.RoomPlanAnswer): the plan's id, its candidates as budget's Candidate,
    what it unloads, free for a load after, whether that reaches the target, and the most it can free."""
    from spark.budget import Candidate

    plan_id = _field(answer, "plan_id", str)
    plan = _field(answer, "plan", dict)
    candidates = []
    for each in _field(plan, "candidates", list):
        if not isinstance(each, dict):
            raise _unreadable("a candidate isn't an object")
        candidates.append(Candidate(
            model=_field(each, "model", str), label=_field(each, "label", str), gib=_field(each, "gib", _NUMBER),
            resident=_field(each, "resident", bool), pinned=_field(each, "pinned", bool),
            session=_field(each, "session", str, none=True), inflight=_field(each, "inflight", int),
            oldest_s=_field(each, "oldest_s", _NUMBER, none=True),
            idle_min=_field(each, "idle_min", _NUMBER, none=True)))
    return (plan_id, candidates, _strings(plan, "unload"), _exact(_field(plan, "free_after_gib", _NUMBER)),
            _field(plan, "enough", bool), _exact(_field(plan, "most_gib", _NUMBER)))


def _exact(value: float) -> Decimal:
    """A number of the gate's, exactly as its JSON reads (a float's repr), as budget reads its sizes."""
    return Decimal(repr(value))


def _ask(question: str, yes: bool) -> bool | None:
    """The question's answer: True for y or yes; None when there is no terminal to ask on and no `--yes`. `--yes`
    shows the question with its answer."""
    if yes:
        _say(f"{question} y")
        return True
    stdin = sys.stdin
    if stdin is None or not stdin.isatty():
        return None
    _say(f"{question} ", end="")
    reply = stdin.readline()
    if not reply:  # end of input, Ctrl-D: the next line starts on a line of its own
        print(flush=True)
    return reply.strip().lower() in ("y", "yes")


def _make_room(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if args.for_ is not None and args.size is None:
        parser.error("--for goes with a size, as `spark make-room 40G --for 8h`: --all and --done take no time")
    client = _control()
    if args.done:
        return _room_done(client)
    registry = _registry()
    target = None if args.all else args.size
    phase = "plan"  # then "question", then "run": what a Ctrl-C ends
    try:
        plan_id, candidates, unload, free_after, enough, most = _plan(
            client.post("/v1/make-room/plan", {"all": True} if args.all else {"size_gib": float(args.size)}))
        # Free for a load now, the gate's own figure, from its plan: what unloading every candidate frees, less them.
        free_now = most - sum((_exact(c.gib) for c in candidates), Decimal(0))
        lines = messages.room_list(target, free_now, candidates, unload, free_after,
                                   registry=registry or _NO_REGISTRY).split("\n")
        question = lines.pop() if lines[-1].endswith("[y/N]") else None
        if question is None and target is not None and candidates and not enough:
            question = messages.room_too_much(target, most)  # the list ends at its rows, and this asks
        _say("\n".join(lines))
        if question is None or not question.endswith("[y/N]"):
            if question is not None:
                _say(question)
            return 1  # nothing loaded, or nothing at all to free: nothing asked, nothing done
        phase = "question"
        answer = _ask(question, args.yes)
        if answer is None:
            _say(messages.room_needs_a_terminal(), err=True)
            return 2
        if not answer:
            _say(messages.room_declined())
            return 1
        phase = "run"
        for_s = None if args.for_ is None else float(args.for_)
        result = _result(client.stream("/v1/make-room", {"plan_id": plan_id, "for_s": for_s}, then_s=None),
                         "a model", "make-room", _warn_gib(registry))
        ended = _strings(result, "ended") if "ended" in result else []
        if args.all:
            _say(messages.room_all())
        else:
            _say(messages.room_held(_strings(result, "unloaded"), _field(result, "free_gib", _NUMBER),
                                    _field(result, "hold_gib", _NUMBER), _field(result, "until", _NUMBER, none=True),
                                    now=_clock()))
        if ended:
            _say(messages.room_ended_words(ended))
    except KeyboardInterrupt:
        if phase == "question":  # a no, and nothing was sent
            print(flush=True)
            _say(messages.room_declined())
            return INTERRUPTED
        return _interrupted("make-room" if phase == "run" else "read", None)
    return 0


def _room_done(client: GateClient) -> int:
    """`spark make-room --done`: the gate's release of make-room's hold alone, never the brake's pause."""
    try:
        answer = client.post("/v1/release", {"room": True, "brake": False})
    except KeyboardInterrupt:
        return _interrupted("asked", None)
    room = answer.get("room")
    if room is None:
        _say(messages.room_no_hold())
        return 0
    if not isinstance(room, dict):
        raise _unreadable("its room isn't an object")
    _say(messages.room_done(_field(room, "unused_gib", _NUMBER), _field(room, "total_gib", _NUMBER),
                            _strings(answer, "reloading"), _field(room, "next_label", str, none=True)))
    return 0


# logs.

def _logs(args: argparse.Namespace) -> int:
    try:
        answer = _control().get(f"/v1/logs/{quote(args.model, safe='')}?n={args.n}")
    except KeyboardInterrupt:
        return _interrupted("read", None)
    for line in _strings(answer, "lines"):
        print(_shown(line), flush=True)
    return 0


# Sessions, on the status socket.

def _session_start(args: argparse.Namespace) -> int:
    try:
        granted = _status().post("/v1/sessions", {"model": args.model, "pid": args.pid, "label": args.label})
    except KeyboardInterrupt:
        return _interrupted("asked", None)
    _say(_field(granted, "id", str))
    return 0


def _session_renew(args: argparse.Namespace) -> int:
    try:
        _status().post(f"/v1/sessions/{quote(args.id, safe='')}/renew", {})
    except KeyboardInterrupt:
        return _interrupted("asked", None)
    return 0


def _session_end(args: argparse.Namespace) -> int:
    try:
        _status().delete(f"/v1/sessions/{quote(args.id, safe='')}")
    except KeyboardInterrupt:
        return _interrupted("asked", None)
    return 0


class _Ended(Exception):
    """SIGTERM or SIGHUP, to `spark session hold`: its session ends."""


def _session_hold(args: argparse.Namespace) -> int:
    """A session for a process that isn't on the Spark, such as Orca's pi on the Mac (Dan's decision, 2026-10-07): it
    registers its own pid, renews every SESSION_HOLD_RENEW_S, and ends the session when its stdin closes, or on
    SIGTERM, SIGHUP or Ctrl-C, exit 0. A renewal the gate refuses means the session has ended already: said, exit 1.
    One that doesn't reach the gate is said, and tried again at the next renewal: the gate keeps a session for
    SESSION_TTL_S without one."""
    import select
    import signal

    client = _status()
    try:
        granted = client.post("/v1/sessions", {"model": args.model, "pid": os.getpid(), "label": args.label})
    except KeyboardInterrupt:  # a session begun all the same ends with this process, which the gate checks
        return _interrupted("asked", None)
    route = f"/v1/sessions/{quote(_field(granted, 'id', str), safe='')}"

    def ended(signum, frame):
        raise _Ended

    signals = (signal.SIGTERM, signal.SIGHUP)
    before = {signal.SIGINT: signal.getsignal(signal.SIGINT), **{number: signal.signal(number, ended)
                                                                 for number in signals}}
    code, gone = 0, False
    try:
        stdin = sys.stdin.fileno() if sys.stdin is not None else None
        due = time.monotonic() + SESSION_HOLD_RENEW_S
        while stdin is not None:  # no stdin at all is one already closed
            readable, _, _ = select.select([stdin], [], [], max(0.0, due - time.monotonic()))
            if readable:
                if not os.read(stdin, 65536):
                    break  # stdin closed: the process it stands for is gone
                continue
            due = time.monotonic() + SESSION_HOLD_RENEW_S
            try:
                client.post(f"{route}/renew", {})
            except GateRefused as err:
                _say(str(err), err=True)
                code, gone = 1, True
                break
            except GateError as err:
                _say(f"The session wasn't renewed this time: {err} It tries again in "
                     f"{messages.duration(SESSION_HOLD_RENEW_S)}.", err=True)
    except (_Ended, KeyboardInterrupt):
        pass
    finally:
        # Nothing interrupts the end itself: a second signal waits until the session is ended.
        for number in (*signals, signal.SIGINT):
            signal.signal(number, signal.SIG_IGN)
        try:
            if not gone:
                client.delete(route)
        except GateError as err:
            _say(str(err), err=True)
            code = 1
        finally:
            for number, handler in before.items():
                signal.signal(number, handler)
    return code


# The commands.

def register(subparsers) -> None:
    name_help = "a model's name in the registry, or one of its roles, as coder"

    p = subparsers.add_parser("load", help="load a model through the gate, and say how long it took (spark-admin)")
    p.add_argument("model", help=name_help)
    p.set_defaults(func=_load)

    p = subparsers.add_parser("unload", help="unload a model once its requests in flight finish (spark-admin)")
    p.add_argument("model", help=name_help)
    p.set_defaults(func=_unload)

    p = subparsers.add_parser("pin", help="keep a model loaded, for a time or with no end; loads it first if need be "
                                          "(spark-admin)")
    p.add_argument("model", help=name_help)
    p.add_argument("duration", nargs="?", type=_argument(parse_duration),
                   help="how long, as 90m, 8h or 2d (a year at most); none: no end")
    p.set_defaults(func=_pin)

    p = subparsers.add_parser("unpin", help="end a model's pin (spark-admin)")
    p.add_argument("model", help=name_help)
    p.set_defaults(func=_unpin)

    p = subparsers.add_parser("make-room", help="unload what you confirm so a size is free for a load, and hold it "
                                                "for you (spark-admin)")
    which = p.add_mutually_exclusive_group(required=True)
    which.add_argument("size", nargs="?", type=_argument(parse_size), help="in GiB, as 41G, 41GiB or 41")
    which.add_argument("--all", action="store_true", help="unload everything, and hold the whole box")
    which.add_argument("--done", action="store_true", help="end the hold; what it unloaded that is always loaded "
                                                           "reloads")
    p.add_argument("--for", dest="for_", type=_argument(parse_duration), metavar="DURATION",
                   help="end the hold after this long, as 8h (with a size)")
    p.add_argument("--yes", action="store_true", help="answer the question yes, as a script needs")
    p.set_defaults(func=lambda args, parser=p: _make_room(args, parser))

    p = subparsers.add_parser("logs", help="a model's engine's last lines, from llama-swap (spark-admin)")
    p.add_argument("model", help=name_help)
    p.add_argument("-n", type=int, default=LOGS_SHOWN, help=f"how many, 1 to 1000 (default {LOGS_SHOWN})")
    p.set_defaults(func=_logs)

    session = subparsers.add_parser("session", help="a session that keeps a model loaded while its process runs "
                                                    "(spark-users; for hooks)")
    sessions = session.add_subparsers(dest="session", metavar="<start|renew|end|hold>", required=True)
    p = sessions.add_parser("start", help="begin a session for a live process of yours; prints its id")
    p.add_argument("--model", required=True, help=name_help)
    p.add_argument("--pid", required=True, type=int, help="the process it lasts as long as")
    p.add_argument("--label", required=True, help="what `spark status` calls it, as agent's pi")
    p.set_defaults(func=_session_start)
    p = sessions.add_parser("renew", help="renew a session, as its process's hook does")
    p.add_argument("id")
    p.set_defaults(func=_session_renew)
    p = sessions.add_parser("end", help="end a session")
    p.add_argument("id")
    p.set_defaults(func=_session_end)
    p = sessions.add_parser("hold", help="hold a session for as long as this command runs: it ends when stdin closes, "
                                         "or on SIGTERM or SIGHUP")
    p.add_argument("--model", required=True, help=name_help)
    p.add_argument("--label", required=True, help="what `spark status` calls it, as pi in Orca")
    p.set_defaults(func=_session_hold)

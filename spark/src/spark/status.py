"""`spark status` (Phase 2a; plan.md, *What you see in Phase 2a*): the gate's StatusView, in plain words, in the order
Dan asks: how much room, what's loaded, what is waiting, what is paused or held, what happened, and whether everything
is up. `--json` prints the view as the gate gave it, for 2b's menu bar.

It asks the gate only, never llama-swap, and needs no key: a login in spark-admin (by its groups now, os.getgroups())
reads `GET /v1/status` on the control socket, any other the status socket, which gives that caller its own refusals
and events and the system's, each refusal in its own key's words (agent's name none of Dan's processes). Its next steps
are Dan's for spark-admin's members and root, and name Dan for anyone else, who is never told to run a command only Dan
can run (Task 6). With the gate not answering it shows memory from /proc/meminfo, the brake's line from the deployed
registry, and says what is unknown until the gate is back.

It reports problems, it doesn't fail on them: what it can't read, it says so, and it exits 0 whatever it finds. Every
line it prints must be true and plain (Dan's rule), so a number it can't show truthfully it doesn't show: free for a
load below 1 is *none*, never a negative number, and a breakdown always adds up to the figure it explains."""

from __future__ import annotations

import argparse
import grp
import json
import os
import socket
import time
from collections.abc import Mapping
from datetime import datetime, timedelta, tzinfo
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Any

import yaml

from spark import gateclient, gateproto, messages, paths
from spark.brake import FALLBACK
from spark.gateclient import GateClient, GateError, GateRefused, GateUnavailable
from spark.memory import read_meminfo
from spark.registry import load_registry

TIMEOUT_S = 3.0  # each call to the gate: the connection, and the whole answer
# Nearer the brake line than this, the header shows tenths of a GiB: a whole number there could hide which side of the
# line the box is on (Phase 1's rule).
NEAR_GIB = 10
COLUMN = 12  # the width of a row's name: `not loaded` and two spaces
# *recent*'s rows shown, the newest; `--json` keeps every one the gate sends (the controller's ruling at Task 29: up to
# 100 whole refusals is a wall).
RECENT_SHOWN = 10
_STEP = {"dan": "make doctor", "agent": "make doctor shows Dan what's wrong"}  # the gate not answering


def local_zone() -> tzinfo | None:
    """The box's own zone, the one Dan reads his clock in: None, so each time reads with its own offset, as
    datetime.fromtimestamp gives it, never the offset of now, which a clock change within the day would put an hour
    off (Task 29's review, Minor 1)."""
    return None


# Numbers, as the plan shows them: sizes in whole GiB, times on a 24-hour clock.

def _d(value: float) -> Decimal:
    return Decimal(repr(value))


def _floor(value: Decimal, places: int = 0) -> Decimal:
    return value.quantize(Decimal((0, (1,), -places)), ROUND_FLOOR)


def _up(value: float) -> int:
    return int(_d(value).to_integral_value(ROUND_CEILING))


def _near(value: float) -> int:
    return int(_d(value).to_integral_value(ROUND_HALF_UP))


def _size(value: float) -> str:
    """A size in whole GiB, to the nearest; one there but under a GiB reads *less than 1 GiB*, never *0 GiB*."""
    near = _near(value)
    return f"{near} GiB" if near >= 1 or value <= 0 else "less than 1 GiB"


def _plain(value: Decimal) -> str:
    """A Decimal as written: 48, 48.9, never 4.8E+1."""
    return f"{value.normalize():f}"


def _when(at: float, now: float, tz: tzinfo | None) -> str:
    """A moment on the 24-hour clock in `tz`: 18:00 within a day of `now`, else with its day, *Fri 16 Oct, 18:00*, as
    messages' times read (messages._until)."""
    moment = datetime.fromtimestamp(at, tz)
    if abs(moment - datetime.fromtimestamp(now, tz)) < timedelta(days=1):
        return f"{moment:%H:%M}"
    return f"{moment:%a} {moment.day} {moment:%b}, {moment:%H:%M}"


def _shown(line: str) -> str:
    """The line with every character a terminal would act on written out instead (ESC as \\x1b, a newline as \\n):
    labels, session labels and *recent*'s texts come from the registry, the key list and callers."""
    return "".join(c if c.isprintable() else c.encode("unicode_escape").decode("ascii") for c in line)


def _row(name: str, text: str) -> str:
    return f"{name:<{COLUMN}}{text}"


def _under(text: str) -> str:
    return " " * COLUMN + text


# The rows.

def _header(host: str, total: float, available: float, brake: float) -> str:
    """`<host> · 48 GiB available of 121 · 28 above the brake's 20 GiB line`: what's available and the total rounded
    down, as `free -g` shows them, and in tenths near the line, so a box past it never reads as on it."""
    places = 1 if _d(available) - _d(brake) < NEAR_GIB else 0
    above = _floor(_d(available) - _d(brake), places)  # -0.04 is -0.1: past the line, never on it
    side = f"{abs(above):f} above" if above >= 0 else f"{abs(above):f} below"
    return (f"{host} · {_floor(_d(available), places):f} GiB available of {_floor(_d(total)):f} · {side} the brake's "
            f"{_plain(_d(brake))} GiB line")


def _adds_up(start: float, items: list[tuple[float, str]], free_shown: int | None) -> tuple[str, list[str]] | None:
    """The breakdown's terms as shown, when they add up to the figure shown (`free_shown`, None for *none*): whole GiB
    when the whole terms do, else one decimal, else two (messages._breakdown's rule, Task 6's fix round 3). The start
    rounds down, as *available* does, the rest to the nearest. None when no showing adds up."""
    for places in (0, 1, 2):
        step = Decimal((0, (1,), -places))
        first = _d(start).quantize(step, ROUND_FLOOR)
        terms = [(_d(value).quantize(step, ROUND_HALF_UP), words) for value, words in items]
        total = int((first - sum((value for value, _ in terms), Decimal(0))).to_integral_value(ROUND_FLOOR))
        if (total if total >= 1 else None) == free_shown:
            return _plain(first), [words.format(f"{_plain(value)} GiB") for value, words in terms if value]
    return None


def _free(memory: Mapping[str, Any]) -> str:
    """`free for a load: 18 GiB (48 available, less the 24 GiB reserve and 6 GiB still owed)`: the gate's own figure for
    this caller (rule 9), and the terms of the one reading it came from, by the term that gave it: the memory term, or,
    where the view says the GPU's ceiling binds, *the 102 GiB the GPU can allocate, less …*, as a refusal's breakdown
    words it; then a model still starting, and make-room's hold where the figure counts it (agent's view, since Dan's
    group loads into the hold). When no showing adds up, the figure stands alone: no cause is guessed (Task 29's review,
    I2). With no reading of memory, it says so."""
    free = memory["free_for_a_load_gib"]
    if free is None or memory["available_gib"] is None:
        return "free for a load: not known: memory can't be read"
    shown = int(_d(free).to_integral_value(ROUND_FLOOR))
    figure = f"{shown} GiB" if shown >= 1 else "none"
    if memory["ceiling_binds"]:
        start, opening = memory["ceiling_gib"], "the {} GiB the GPU can allocate"
        items = [(memory["committed_gib"], "the {} the loaded models may grow to")]
    else:
        start, opening = memory["available_gib"], "{} available"
        items = [(memory["reserve_gib"], "the {} reserve"), (memory["owed_gib"], "{} still owed")]
    items.append((memory["starting_gib"], "the {} the model still starting may take"))
    tries = [items]
    if memory["held_gib"] > 0:
        tries.append([*items, (memory["held_gib"], "the {} make-room holds for Dan")])
    for terms in tries:
        found = _adds_up(start, terms, shown if shown >= 1 else None)
        if found is not None:
            first, less = found
            taken = f", less {messages._and(less)}" if less else ""
            return f"free for a load: {figure} ({opening.format(first)}{taken})"
    return f"free for a load: {figure}"


def _unaccounted(memory: Mapping[str, Any]) -> list[str]:
    """`used by other processes: 26 GiB`, only above 0: idle MemAvailable, less what's available and the loaded models'
    footprints. Cold residents hold less than their footprints, so at idle the figure goes below 0: no line then."""
    value = memory["unaccounted_gib"]
    if value is None or not value > 0:
        return []
    return [f"used by other processes: {_size(value)}"]


def _doing(m: Mapping[str, Any]) -> str:
    """A loaded model's last column: what it is doing, and its requests in flight, with the oldest's age from a minute
    on, so a count that leaked would show (Review Focus 1)."""
    parts = {"starting": ["loading"], "draining": ["unloading"], "stopping": ["stopping"]}.get(m["state"], [])
    if m["inflight"]:
        oldest = m["oldest_request_s"]
        age = f" (oldest {int(oldest // 60)} min)" if oldest is not None and oldest >= 60 else ""
        parts.append(f"answering {m['inflight']}{age}")
    return ", ".join(parts)


def _models(view: Mapping[str, Any], now: float, tz: tzinfo | None, words: str) -> list[str]:
    models = view["models"]
    names = [m["label"] if m["label"] == m["name"] else f"{m['label']} ({m['name']})" for m in models]
    sizes = [f"up to {_up(m['footprint_gib'])} GiB" for m in models]
    kinds = ["always loaded" if m["resident"] else "loads when asked" for m in models]
    widths = [max((len(each) for each in column), default=0) + 2 for column in (names, sizes, kinds)]
    lines = []
    for loaded, row_name in ((True, "loaded"), (False, "not loaded")):
        group = [i for i, m in enumerate(models) if (m["state"] != "not_loaded") == loaded]
        if not group and loaded:
            lines.append(_row(row_name, "nothing"))
        for n, i in enumerate(group):
            cells = (names[i].ljust(widths[0]) + sizes[i].ljust(widths[1]) + kinds[i].ljust(widths[2])
                     + (_doing(models[i]) if loaded else ""))
            lines.append(_row(row_name if n == 0 else "", cells.rstrip()))
            mark = models[i]["brake_mark"]
            if mark is not None:
                seen = mark["seen_gib"]
                seen_text = f"seen using {_size(seen)}" if seen is not None else "seen using: not known"
                lines.append(_under(f"marked: it was loading when the brake fired at {_when(mark['at'], now, tz)} "
                                    f"({seen_text});"))
                lines.append(_under("only you can load it again" if words == "dan" else "only Dan can load it again"))
    return lines


def _waited(waited_s: float, wait_s: float) -> str:
    """`2 min of 10`, `12 s of 30`, `40 s of 10 min`: the unit once where both share it. Never below 0 (Task 29's
    review, Minor 8)."""
    waited_s = max(0.0, waited_s)
    if waited_s >= 60:
        waited, unit, whole = f"{int(waited_s // 60)} min", 60, wait_s % 60 == 0
    else:
        waited, unit, whole = f"{int(waited_s)} s", 1, wait_s < 60 and wait_s == int(wait_s)
    if whole:
        return f"{waited} of {int(wait_s // unit)}"
    if wait_s % 60 == 0:
        return f"{waited} of {int(wait_s // 60)} min"
    return f"{waited} of {messages.duration(wait_s)}"


def _why(wait: Mapping[str, Any], view: Mapping[str, Any], label: str, words: str,
         commands: Mapping[str, str]) -> str:
    """A wait's reason (gateproto.WAITING_WHY), in Task 29's words."""
    why = wait["why"]
    if why == "memory":  # the waiter's own group's figure, never the viewer's (Task 29's review, I1)
        footprints = {m["name"]: m["footprint_gib"] for m in view["models"]}
        parts = [f"needs {_up(footprints[wait['model']])} GiB"] if wait["model"] in footprints else []
        if wait["free_gib"] is not None:
            free = int(_d(wait["free_gib"]).to_integral_value(ROUND_FLOOR))
            parts.append(f"{free if free >= 1 else 'none'} free for a load")
        return f"waiting for memory: {', '.join(parts)}" if parts else "waiting for memory"
    if why == "slot":
        loading = [m["label"] for m in view["models"] if m["state"] == "starting"]
        return f"waiting its turn: {loading[0] if loading else 'another model'} is loading"
    if why == "dan":
        command = f"spark load {commands.get(wait['model'], wait['model'])}"
        return f"waiting for you: {command}" if words == "dan" else f"waiting for Dan: {command}"
    if why == "draining":
        return f"waiting while {label} finishes unloading"
    fixed = {"brake": "waiting for the brake's pause to end", "restart": "waiting for the model service's restart",
             "llama_swap": "waiting for the model service to answer",
             "swap_refused": "waiting to try again: llama-swap refused the load",
             "ticket": "waiting its turn: the gate is settling a load ticket it couldn't withdraw"}
    return fixed.get(why, f"waiting ({why})")


def _waiting(view: Mapping[str, Any], words: str, commands: Mapping[str, str]) -> list[str]:
    labels = {m["name"]: m["label"] for m in view["models"]}
    if not view["waiting"]:
        return [_row("waiting", "nothing")]
    lines = []
    for n, wait in enumerate(view["waiting"]):
        label = labels.get(wait["model"], wait["model"])
        lines.append(_row("waiting" if n == 0 else "", f"{wait['key_label']} → {label} · "
                          f"{_waited(wait['waited_s'], wait['wait_s'])} · {_why(wait, view, label, words, commands)}"))
    return lines


def _paused(view: Mapping[str, Any], now: float, tz: tzinfo | None, words: str) -> list[str]:
    """The brake's hold: `no` without one (the view carries no last fire or release then: the controller's ruling at
    Task 29); with one, when it fired and how low memory ran, and whether it lifts by itself or waits for Dan; for one
    whose file can't be read, the gate's reason under it."""
    paused = view["paused"]
    if paused is None:
        return [_row("paused", "no")]
    since = paused["since"]
    if paused["problem"] is not None:  # it still holds: fail closed (Task 29's review, Minor 2)
        lead = "by a brake hold whose file can't be read"
    elif since is not None:
        lead = f"since {_when(since, now, tz)}"
    else:
        lead = "by a brake hold that doesn't record when it fired"
    if paused["available_gib"] is not None:
        lead += f" ({messages._reading(paused['available_gib'])} available)"
    if paused["waits_for_dan"]:
        then = "waits for you: make brake-release" if words == "dan" else "waits for Dan: make brake-release"
    else:
        then = (f"resumes by itself after {gateproto.RELEASE_AFTER_S // 60:g} min above "
                f"{messages._line(view['memory']['warn_gib'])} available")
    return [_row("paused", f"{lead} · {then}"), *([_under(paused["problem"])] if paused["problem"] else [])]


def _held(view: Mapping[str, Any], now: float, tz: tzinfo | None, words: str) -> str:
    """Apply's hold first, then make-room's (the final check's f-5), the pins and the sessions, each by name."""
    parts = []
    applying = view["applying"]
    if applying is not None:
        parts.append(f"new requests, for make apply since {_when(applying['since'], now, tz)} · until the model "
                     "service answers again")
    held = view["held"]
    if held is not None:
        until = f"until {_when(held['until'], now, tz)}" if held["until"] is not None else "until a reboot"
        size = _size(held["size_gib"])
        parts.append(f"{size} for you {until} · spark make-room --done ends it" if words == "dan"
                     else f"{size} for Dan {until} · Dan's spark make-room --done ends it")
    if not parts:
        parts.append("nothing")
    pins = [f"{pin['label']} until {_when(pin['until'], now, tz)}" if pin["until"] is not None
            else f"{pin['label']} with no end set" for pin in view["pins"]]
    parts.append(f"pins {messages._and(pins)}" if pins else "pins none")
    sessions = [f"{s['label']}, since {_when(s['started_at'], now, tz)}" for s in view["sessions"]]
    parts.append(f"sessions: {'; '.join(sessions)}" if sessions else "sessions none")
    return _row("held", " · ".join(parts))


def _recent(view: Mapping[str, Any], now: float, tz: tzinfo | None) -> list[str]:
    """The newest RECENT_SHOWN, newest first, each its whole text, a refusal's after whom it was for, then how many
    older ones `--json` has (the controller's rulings at Task 29: the truth beats a short line, and a wall of them isn't
    read)."""
    rows = sorted(view["recent"], key=lambda each: each["at"], reverse=True)
    if not rows:
        return [_row("recent", "nothing")]
    lines = []
    for n, item in enumerate(rows[:RECENT_SHOWN]):
        said = f"for {item['key_label']}: {item['text']}" if item["key_label"] else item["text"]
        lines.append(_row("recent" if n == 0 else "", f"{_when(item['at'], now, tz)}  {said}"))
    if len(rows) > RECENT_SHOWN:
        lines.append(_under(f"… and {len(rows) - RECENT_SHOWN} older: spark status --json shows them all"))
    return lines


def _brake(brake: Mapping[str, Any], now: float, tz: tzinfo | None, words: str) -> str:
    """Only a brake that is up and whose start check passed reads ok; anything else says what, and where to look (the
    controller's ruling at Task 29): the brake's journal, which only Dan reads."""
    if brake["status"] == "ok":
        checked = brake["key_checked_at"]
        return f"brake ok (key checked {_when(checked, now, tz)})" if checked is not None else "brake ok (key checked)"
    look = "make logs s=brake" if words == "dan" else "make logs s=brake shows Dan why"
    return f"brake {brake['status']} ({look})"


def _ntfy(ntfy: Mapping[str, Any], now: float, tz: tzinfo | None) -> str:
    if ntfy["status"] == "not_set_up":  # 2a's state until Task 2, in Phase 5: there is no ntfy to fail or drop for
        return "ntfy not set up (Task 2, Phase 5)"
    if ntfy["status"] == "failing":
        since = ntfy["failing_since"]
        said = f"ntfy failing since {_when(since, now, tz)}" if since is not None else "ntfy failing"
    else:
        said = f"ntfy {ntfy['status']}"
    dropped = ntfy["dropped"]
    if dropped:
        said += f" · {dropped} notification{'s' if dropped != 1 else ''} dropped"
    return said


def _health(view: Mapping[str, Any], now: float, tz: tzinfo | None, words: str) -> list[str]:
    health = view["health"]
    parts = [f"front {health['front']}", f"gate {health['gate']}", _brake(health["brake"], now, tz, words)]
    if health["llama_swap"] != "ok":
        parts.append(f"the model service {health['llama_swap']}")
    parts.append(_ntfy(health["ntfy"], now, tz))
    lines = [_row("health", " · ".join(parts))]
    if health["damaged_at"] is not None:
        kept = health["damaged_kept_as"]
        lines.append(_under(f"state started fresh at {_when(health['damaged_at'], now, tz)}; "
                            + (f"the damaged file is kept as {kept}" if kept else "the damaged file couldn't be kept")))
    lines += [_under(problem) for problem in health["read_problems"]]
    labels = {m["name"]: m["label"] for m in view["models"]}
    for engine in health["unticketed_engines"]:
        lines.append(_under(f"{labels.get(engine['model'], engine['model'])} runs on port {engine['port']} (pid "
                            f"{engine['pid']}) without a ticket from the gate: a load around the gate"))
    refused = health["no_ticket_refusals"]
    if refused:
        lines.append(_under(f"spark launch refused {refused} start{'s' if refused != 1 else ''} without a ticket from "
                            "the gate this boot"))
    age = health["activity_age_s"]
    if age is not None and age > gateproto.ACTIVITY_STALE_S:
        lines.append(_under(f"the gate's activity record is {int(age)} s old, so the brake goes by Phase 1's order"))
    return lines


def format_status(view: Mapping[str, Any], *, tz: tzinfo | None, words: str = "dan",
                  commands: Mapping[str, str] | None = None) -> str:
    """The StatusView as plan.md lays it out, its rows in order: the header, `free for a load` (and `used by other
    processes` when above 0), `loaded`, `not loaded` (a brake mark under its model), `waiting`, `paused`, `held`,
    `recent`, `health`, then each of the gate's problems. Times in `tz`; next steps in Dan's words (`words` "dan") or
    naming Dan ("agent"); `commands` each model's name for `spark load`, its first role (its own name otherwise)."""
    if view.get("schema") != gateproto.STATUS_SCHEMA:
        raise _Unreadable(f"its schema is {view.get('schema')}, and this reads {gateproto.STATUS_SCHEMA}")
    commands = commands or {}
    now, memory = view["at"], view["memory"]
    if memory["available_gib"] is None or memory["total_gib"] is None:
        header = f"{view['host']} · memory can't be read"
    else:
        header = _header(view["host"], memory["total_gib"], memory["available_gib"], memory["brake_gib"])
    lines = [header, _free(memory),
             *_unaccounted(memory), *_models(view, now, tz, words), *_waiting(view, words, commands),
             *_paused(view, now, tz, words), _held(view, now, tz, words), *_recent(view, now, tz),
             *_health(view, now, tz, words), *(_row("problem", problem) for problem in view["problems"])]
    return "\n".join(_shown(line) for line in lines)


class _Unreadable(ValueError):
    """An answer of the gate's that isn't a StatusView this spark status can read."""


# The gate not answering.

def _down(err: GateError, registry: Any, registry_problem: str | None, words: str) -> tuple[dict, str]:
    """What `spark status` gives with no view from the gate: `--json`'s shape, `{schema, host, at, memory: {total_gib,
    available_gib, brake_gib}, gate}`, for 2b's menu bar, and the text: the header from /proc/meminfo, the brake's line
    from the deployed registry (the plan's when it won't load), then the gate's line and what couldn't be read."""
    host = socket.gethostname().split(".")[0]
    brake = FALLBACK.brake_gib if registry is None else registry.brake.brake_gib
    problems = []
    try:
        mem = read_meminfo()
        total, available = mem.total_gib, mem.available_gib
        header = _header(host, total, available, brake)
    except (OSError, ValueError) as error:
        total = available = None
        header = f"{host} · memory can't be read"
        problems.append(f"can't read memory: {error}")
    if registry_problem is not None:
        problems.append(f"{registry_problem}; the brake's line is the plan's, {_plain(_d(FALLBACK.brake_gib))} GiB")
    if isinstance(err, GateUnavailable):
        gate = "not answering"
        said = f"not answering: what's loaded, waiting, held or paused is unknown until it's back · {_STEP[words]}"
    elif isinstance(err, GateRefused) and err.status >= 500 and words == "agent":
        # The gate's own words for an error of its own name Dan's command (`make logs s=gate`): agent is told it's
        # Dan's (Task 29's review, Minor 5).
        gate = said = (f"The gate couldn't answer this request (HTTP {err.status}); Dan can see why with `make logs "
                       "s=gate` on the Spark.")
    else:
        gate = said = str(err)
    shape = {"schema": gateproto.STATUS_SCHEMA, "host": host, "at": time.time(),
             "memory": {"total_gib": total, "available_gib": available, "brake_gib": brake}, "gate": gate}
    lines = [header, _row("gate", said), *(_row("problem", problem) for problem in problems)]
    return shape, "\n".join(_shown(line) for line in lines)


# The command.

def register(subparsers) -> None:
    p = subparsers.add_parser("status", help="room for a load, what's loaded, waiting, paused or held, recent, health")
    p.add_argument("--json", action="store_true", help="the gate's view as it gave it, for the menu bar")
    p.set_defaults(func=run)


def _admin_login() -> bool:
    """Whether this login has spark-admin now (its groups, os.getgroups()), as the control socket checks it."""
    try:
        gid = grp.getgrnam(gateproto.SOCKET_GROUPS["control"]).gr_gid
    except KeyError:
        return False
    return gid == os.getegid() or gid in os.getgroups()


def _registry() -> tuple[Any, str | None]:
    try:
        return load_registry(paths.REGISTRY), None
    except (OSError, ValueError, yaml.YAMLError, RecursionError) as err:  # RecursionError: YAML nested too deep
        return None, f"the registry {paths.REGISTRY} won't load: {' '.join(str(err).split())}"


def _print_json(data: Any) -> bool:
    try:
        print(json.dumps(data, indent=2, allow_nan=False))
    except ValueError:  # a number that isn't one, which the gate never sends
        return False
    return True


def run(args: argparse.Namespace) -> int:
    path = paths.GATE_CONTROL_SOCKET if _admin_login() else paths.GATE_STATUS_SOCKET
    words = gateclient._words()  # Dan's for spark-admin's members and root, agent's for anyone else
    registry, registry_problem = _registry()
    failed: GateError | None = None
    try:
        view = GateClient(path, TIMEOUT_S).get("/v1/status")
    except GateError as err:
        failed = err
    if failed is None and args.json:
        if _print_json(view):
            return 0
        failed = GateError("answered with a number that isn't one, which JSON can't carry")
    if failed is None:
        commands = {} if registry is None else {
            name: (model.roles or (name,))[0] for name, model in registry.models.items()}
        try:
            print(format_status(view, tz=local_zone(), words=words, commands=commands))
            return 0
        except _Unreadable as err:
            why = str(err)
        except (KeyError, TypeError, ValueError, AttributeError, IndexError, ArithmeticError, OSError) as err:
            why = type(err).__name__  # a field missing, or a number past any reading: a size, a time (Minor 4)
        failed = GateError(f"answered, but with a status this spark status can't read ({why}); spark status --json "
                           "shows it as it came")
    shape, text = _down(failed, registry, registry_problem, words)
    if args.json:
        _print_json(shape)
    else:
        print(text)
    return 0

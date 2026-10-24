"""`spark gate` (Phase 2a; plan.md, *The front and the gate* and *Users, access and security*): the gate as systemd runs
it, `local-ai-gate.service` as `spark`, on the two Unix sockets `local-ai-gate-status.socket` and
`local-ai-gate-control.socket` hold, passed by their names, `status` and `control` (sockets.listen_fds).

- **Its credentials**, each a file LoadCredential= gives: `llamaswap-key`, its own key for llama-swap; `ntfy-token`, its
  publish token's header; and `keys`, the private key list, read whole (credentials.read_credential_text) and parsed
  against the registry's key groups (registry.parse_keys), which refuses a label pi's retry list matches.
- **ntfy's address and topic**, NTFY_URL and NTFY_TOPIC_GATE, from the environment, which the private values file gives
  (EnvironmentFile=). A gate without them, or without its token, still admits and unloads: it says once that ntfy
  isn't set up, and every notification then fails as ntfy out of reach does. Its publisher says it is the one for a
  gate without ntfy, so `spark status` shows ntfy not set up, not failing (2026-10-14, Dan's decision: ntfy optional
  until Task 2, in Phase 5; Task 29).
- **Who is who**: spark-front's uid and spark-admin's members, from the user and group databases. The members are
  asked at each request that needs them, and at each read of who holds memory (Dan's processes), from a copy at most
  ADMINS_TTL_S old, so a member added is known without a restart. SPARK_FRONT_UID and SPARK_ADMIN_UIDS
  (comma-separated uids), read only when set, stand in for those databases in the tests; the units never set them, and
  a gate that finds either set says so in the journal, once, at its start.
- **What it writes**, beside its state: its ledger (gate.ledger, the usage meter's counter, Task 44e), a row for each
  load, unload, refusal, brake event, start and stop, the gate's own and the front's, and counting off and on, into
  the month's file in paths.GATE_LEDGER_DIR, from a thread of its own (rowfile.RowFile), never in its way. The folder
  is opened there, once every check that can refuse the start has passed, so no state of it keeps the gate from
  starting or a startup refusal from staying one line: a folder it can't write turns counting off, one journal line
  each time (and on again once it can), `health.counting` on the control socket says since when, and the gate admits
  and unloads as ever. Its own start is the first row it writes, and its stop the last.
- **Its core** (Task 18) starts in the servers' own loop before they serve, and stops after they have. Its stop
  (`shutdown`) first finishes or cancels the bounded routes' work still going on past its 503 (app.finish_work), then
  stops the core, which cancels every task that uses the client, the loads, the drains, the loops and the watchers,
  then closes the llama-swap client and ntfy's: so nothing meets httpx's RuntimeError on a closed client, and nothing
  saves after the core's last save; then writes its stop in the ledger, and the rows still queued, within
  LEDGER_DRAIN_S, from a thread. As the stop begins, the answers that wait end (gate/app.py), so the stop never waits
  out GRACEFUL_S for the front's events call. A stop during the start cancels it (serve.run_servers).
- **Its logging** (configure_logging, called before anything else; serve's, which the front shares since Task 21):
  the gate's lines on stderr, which the journal takes; httpx's and httpcore's request lines quieted
  (serve.quiet_http_loggers), since they carry ntfy's topic and host; and on uvicorn's, no traceback, whose
  exception's text code can build from a request (`int()` of a header quotes it), and none of the "Unsupported upgrade
  request" warnings any local process can cause, `ws="none"` turning every upgrade down (Task 9).

A startup refusal is one line on stderr, exit 1: a socket missing, a credential missing or malformed (named, never
quoted), a registry or a key list that won't load (a key's name, group or line, never a key or a digest, which the
list doesn't hold), an account or a group that doesn't exist."""

from __future__ import annotations

import asyncio
import grp
import logging
import os
import pwd
import sys
import time
from collections.abc import Callable, Iterable, MutableMapping
from typing import Any

import yaml

from spark import paths, render
from spark.credentials import CredentialError, read_credential, read_credential_text, read_header_credential
from spark.gate.app import build_apps, finish_work
from spark.gate.core import GateCore
from spark.gate.ledger import PREFIX as LEDGER_PREFIX
from spark.gate.ledger import Ledger
from spark.gate.notify import NtfyError, NtfyPublisher
from spark.gateproto import GRACEFUL_S, SOCKET_GROUPS
from spark.llamaswap import LlamaSwapError
from spark.llamaswap_async import AsyncLlamaSwap, load_timeout_for
from spark.messages import Notification
from spark.protocols import make_protocol
from spark.registry import load_registry, parse_keys
from spark.rowfile import RowFile
from spark.serve import configure_logging, run_servers
from spark.sockets import CONTROL, STATUS, SocketsError, listen_fds

# The gate's load and unload calls, its /running reads and its logs speak llama-swap v257's API (Task 12's client).
TESTED_AGAINST = {"llama-swap": "v257"}
HEADER_TIMEOUT_S = 10  # a request's head, and a body nobody will read, past which a connection closes (Task 9)
FRONT_ACCOUNT = "spark-front"
ADMINS_TTL_S = 5.0  # spark-admin's members are read again once the last copy is this old
# The ledger's rows still queued at the stop are written within this, after the core's stop: about 29 s at worst for the
# whole stop (the unit's TimeoutStopSec= comment counts each part), inside its 45.
LEDGER_DRAIN_S = 2
_log = logging.getLogger(__name__)


class GateStartError(Exception):
    """An account or a group the gate needs doesn't exist, or a stand-in for one isn't a uid."""


class _NoNtfy:
    """The publisher of a gate whose ntfy isn't set up: each notification fails as ntfy out of reach does. `not_set_up`
    says it is this one, so the gate's status view reads ntfy `not_set_up`, never `failing` (Task 29)."""

    not_set_up = True

    def __init__(self, why: str):
        self._why = why

    async def __call__(self, n: Notification) -> None:
        raise NtfyError(f"ntfy isn't set up for the gate ({self._why})", "unreached")

    async def aclose(self) -> None:
        pass


def _publisher(env: MutableMapping[str, str]) -> NtfyPublisher | _NoNtfy:
    try:
        url, topic = env.get("NTFY_URL"), env.get("NTFY_TOPIC_GATE")
        if not url or not topic:
            raise ValueError("NTFY_URL or NTFY_TOPIC_GATE isn't set: the values file gives both")
        return NtfyPublisher(url, topic, read_header_credential("ntfy-token", env))
    except (ValueError, CredentialError) as err:  # none of them quotes a value: the address and topic are private
        _log.warning("ntfy isn't set up for the gate (%s): no notification reaches the phone, and `spark status` "
                     "shows ntfy not set up", err)
        return _NoNtfy(str(err))


def _uid(text: str, variable: str) -> int:
    if not (text.strip().isascii() and text.strip().isdigit()):
        raise GateStartError(f"{variable} holds something that isn't a uid")
    return int(text.strip())


def front_uid(env: MutableMapping[str, str]) -> int:
    """spark-front's uid: SPARK_FRONT_UID when set (the tests', and said in the journal), else the user database's."""
    given = env.get("SPARK_FRONT_UID")
    if given is not None:
        uid = _uid(given, "SPARK_FRONT_UID")
        _log.warning("SPARK_FRONT_UID is set, so the gate takes uid %d as spark-front's in place of the user "
                     "database: it is for the tests only", uid)
        return uid
    try:
        return pwd.getpwnam(FRONT_ACCOUNT).pw_uid
    except KeyError:
        raise GateStartError(f"there's no account {FRONT_ACCOUNT}, which runs the front: bootstrap for 2a makes "
                             "it") from None


def admin_uids(env: MutableMapping[str, str], *, clock: Callable[[], float] = time.monotonic,
               ttl_s: float = ADMINS_TTL_S) -> Callable[[], set[int]]:
    """What gives spark-admin's members' uids, asked at each request that needs them: SPARK_ADMIN_UIDS when set (the
    tests', and said in the journal), else the group database's members, and the accounts whose own group it is, from
    a copy read again once it is `ttl_s` old."""
    given = env.get("SPARK_ADMIN_UIDS")
    if given is not None:
        fixed = frozenset(_uid(each, "SPARK_ADMIN_UIDS") for each in given.split(",") if each.strip())
        _log.warning("SPARK_ADMIN_UIDS is set, so the gate takes uids %s as spark-admin's members in place of the "
                     "group database: it is for the tests only", ",".join(map(str, sorted(fixed))) or "none")
        return lambda: set(fixed)
    group = SOCKET_GROUPS["control"]
    try:
        grp.getgrnam(group)
    except KeyError:
        raise GateStartError(f"there's no group {group}, whose members use the gate's control socket: bootstrap for "
                             "2a makes it") from None

    def read() -> frozenset[int]:
        try:
            entry = grp.getgrnam(group)
        except KeyError:  # gone since the start: root alone may
            return frozenset()
        uids = {user.pw_uid for user in pwd.getpwall() if user.pw_gid == entry.gr_gid}
        for name in entry.gr_mem:
            try:
                uids.add(pwd.getpwnam(name).pw_uid)
            except KeyError:
                continue
        return frozenset(uids)

    copy: list[tuple[float, frozenset[int]]] = []  # (when read, the members); one whole tuple replaced at a time

    def members() -> set[int]:
        now = clock()
        if not copy or now - copy[0][0] >= ttl_s:
            copy[:] = [(now, read())]
        return set(copy[0][1])

    return members


_REFUSALS = (SocketsError, CredentialError, GateStartError, LlamaSwapError, ValueError, OSError, yaml.YAMLError)


def _counting_off(since: float, why: str) -> None:
    """The ledger's row file's on_off, from its thread: one line each time counting turns off; `why` is an error's
    class, or `disk`, never its text."""
    _log.error("counting off: the gate can't write its ledger to %s (%s); it admits and unloads as ever",
               paths.GATE_LEDGER_DIR, why)


def _counting_on() -> None:
    _log.warning("counting on again: the gate writes its ledger to %s", paths.GATE_LEDGER_DIR)


async def shutdown(core: GateCore, app: Any, clients: Iterable[Any], ledger_rows: RowFile | None = None) -> None:
    """`spark gate`'s stop, once its servers have stopped: the bounded routes' work still going on finished or
    cancelled (app.finish_work), then the core stopped, which cancels every task that uses the llama-swap client and
    saves last, then each client closed (the controller's ruling at Task 19's review); then the gate's stop in its
    ledger, and the ledger's rows still queued written, within LEDGER_DRAIN_S, its join in a thread, never blocking the
    loop."""
    try:
        await finish_work(app)
        await core.stop()
    finally:
        try:
            for client in clients:
                await client.aclose()
        finally:
            core.ledger.lifecycle("stop", "gate")
            if ledger_rows is not None:
                await asyncio.to_thread(ledger_rows.close, LEDGER_DRAIN_S)


def run(env: MutableMapping[str, str] = os.environ) -> int:
    """`spark gate`: until SIGTERM or SIGINT, then 0; a startup refusal, one line on stderr, then 1."""
    configure_logging()
    try:
        sockets = listen_fds(env)
        missing = [name for name in (STATUS, CONTROL) if name not in sockets]
        extra = sorted(set(sockets) - {STATUS, CONTROL})
        if missing or extra:
            raise SocketsError(f"systemd passed the gate the sockets {', '.join(sorted(sockets)) or 'none'}; it "
                               f"takes exactly {STATUS} and {CONTROL}")
        registry = load_registry(paths.REGISTRY)
        keys = parse_keys(read_credential_text("keys", env), registry.key_groups)
        swap = AsyncLlamaSwap(paths.LLAMASWAP_URL, read_credential("llamaswap-key", env),
                              load_timeout_s=load_timeout_for(render.HEALTH_CHECK_TIMEOUT_S))
        front = front_uid(env)
        admins = admin_uids(env)
        publisher = _publisher(env)
    except _REFUSALS as err:
        print(f"spark gate: {err}", file=sys.stderr)
        return 1
    spark_uid = os.geteuid()
    # Built once every check that can refuse the start has passed, so a startup refusal stays one line: its thread
    # opens the folder at once, and a folder it can't write says "counting off" on a line of its own. No state of the
    # folder is a refusal. The ledger asks the core for the registry's names at each row, so a registry read again at
    # apply's end counts.
    ledger_rows = RowFile(paths.GATE_LEDGER_DIR, LEDGER_PREFIX, on_off=_counting_off, on_on=_counting_on)
    ledger = Ledger(ledger_rows, time.time, lambda: frozenset(core.registry.models))
    core = GateCore(registry, paths.GATE_STATE, registry_path=paths.REGISTRY, llamaswap=swap, publish=publisher,
                    brake_dir=paths.STATE, launch=paths.LAUNCH, hf_home=render.HF_HOME,
                    may_pin=lambda uid: uid == 0 or uid in admins(),
                    may_hold=lambda uid: uid not in (spark_uid, front),  # the stack's own accounts hold none
                    keys=keys, spark_uid=spark_uid, dan_uids=admins, ledger=ledger)
    ledger.lifecycle("start", "gate")
    status_app, control_app = build_apps(core, front_uid=front, admin_uids=admins)
    # A SIGTERM during core.start() cancels it (serve.run_servers), which is safe: each save it makes is a whole state,
    # as the gate saves in any case, and a stop after a start cut short cancels what the start spawned and, cut before
    # the Notifier's task runs, never waits on a queue nothing sends (GateCore.stop). The one thing a cut can cost is a
    # start after damage cut in its scan of the engines running: those engines aren't recorded as found after damage,
    # so the next start names them in `health.unticketed_engines`, a report, never an action, until they stop.
    run_servers([(status_app, [sockets[STATUS]]), (control_app, [sockets[CONTROL]])],
                protocol=make_protocol(peer_cred=True, header_timeout_s=HEADER_TIMEOUT_S), graceful_s=GRACEFUL_S,
                startup=core.start, stopping=status_app.state.stopping.set,
                shutdown=lambda: shutdown(core, status_app, (swap, publisher), ledger_rows))
    return 0

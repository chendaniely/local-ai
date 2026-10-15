"""The gate's two sockets and `spark gate` (website/design/phase-2a.md, Task 19): each route and who may call it, the
caller known by the uid SO_PEERCRED gives (Task 9); the size cap and the request timeout, but for the routes that
wait; the streams' heads at once; the status socket's filter; `spark gate` taking its sockets from systemd; and its
logging, which never carries a request's content.

The apps are called two ways. Most tests call them as uvicorn would, with a scope whose `peer_cred` the test sets, so
any uid can be played. Those that time a head, a timeout, a cap or a dropped call serve them on a real uvicorn, with
Task 9's protocol, on short Unix sockets, where the caller's uid is this test's own (SO_PEERCRED is Linux's, so they
skip elsewhere, with Task 9's reason). The core under the apps is Task 18's, built by test_gate_core's Gate, on an
injected clock with stand-ins for llama-swap, the units, ntfy and /proc; `spark gate` itself runs in a subprocess, as
systemd starts it. Every uid, key, path and name here is a stand-in; none was read from the box.

One test reads the host: `spark gate`'s subprocess runs the real core's loops for about a second, which read the four
units' `systemctl show`, nvidia-smi and /proc, all read-only, and write only to the test's own folders. A host without
those units or without nvidia-smi passes it too: each failed read is a recorded problem, never an error."""

import asyncio
import contextlib
import dataclasses
import json
import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import get_type_hints

import anyio
import httpx
import pytest
import uvicorn

from fake_llamaswap import FakeLlamaSwap, serve_fake
from spark import brake, gateproto, messages, protocols, serve
from spark.brake import KeyCheck, write_key_check
from spark.gate import app as gate_app
from spark.gate.admission import AdmitRequest
from spark.gate.state import BrakeMark, RecentRecord, RefusalRecord, RoomHold, load_state
from spark.gateclient import GateClient
from spark.hold import Hold, read_hold, write_hold
from spark.llamaswap_async import AsyncLlamaSwap
from spark.messages import Holder, Moment
# anyio_backend and local_zone are test_gate_core's fixtures, imported so they apply here as there.
from test_gate_core import (AGENT, BOOT, BOUND_S, CODER, DAN, EMBED, FOOTPRINT, GEMMA, KEY, KEYS, RESIDENTS, SPARK,
                            STACK_REGISTRY, USERS, WHISPER, Front, Gate, _check_at, anyio_backend, at, loaded,
                            local_zone, record, settle, this_boots_hold)
from test_protocols import LINUX_ONLY
from test_sockets import LAUNCHER

FRONT_UID = 990  # spark-front's, a stand-in
ME = os.getuid()  # the caller over a real socket: this test
LOGS = ("spark.gate.core", "spark.gate.room", "spark.gate.policy", "spark.gate.admission", "spark.gate.notify",
        "spark.gate.app", "spark.gate.main")
EXAMPLE_KEYS = Path(__file__).resolve().parents[2] / "stack" / "keys.example.yaml"


@pytest.fixture(autouse=True)
def no_hidden_errors(caplog):
    """Nothing may fail unseen: the gate logs what it couldn't do at ERROR and carries on. A test that makes something
    fail on purpose checks the line, then clears the log."""
    for name in LOGS:
        caplog.set_level(logging.INFO, logger=name)
    yield
    assert [r.getMessage() for r in caplog.records if r.name in LOGS and r.levelno >= logging.ERROR] == []


@pytest.fixture(autouse=True)
def accounts(monkeypatch):
    """Each stand-in uid's account name, as the status socket's filter reads it from the user database."""
    monkeypatch.setattr(gate_app, "_account", lambda uid: USERS.get(uid))


def apps(core, *, admins=(DAN,), front_uid=FRONT_UID):
    return gate_app.build_apps(core, front_uid=front_uid, admin_uids=lambda: set(admins))


# The apps called as uvicorn calls them.

@dataclasses.dataclass
class Answer:
    status: int
    headers: dict[str, str]
    body: bytes
    received: bool  # whether the app called receive() at all

    def json(self):
        return json.loads(self.body)

    def lines(self) -> list[dict]:
        return [json.loads(line) for line in self.body.decode().splitlines()]


async def call(app, method: str, path: str, *, uid: int | None, body=None, query: bytes = b"",
               leave_after_head: bool = False) -> Answer:
    """One request to `app`, as uvicorn's h11 protocol makes it: `peer_cred` in the scope's extensions when `uid` is
    given (Task 9's protocol), the body in one message, then nothing until the client goes. With `leave_after_head`
    the client goes once the answer's head has come, as one that has seen a stream begin."""
    raw = b"" if body is None else body if isinstance(body, bytes) else json.dumps(body).encode()
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
             "method": method, "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": query,
             "root_path": "", "headers": [(b"host", b"gate"), (b"content-length", str(len(raw)).encode())],
             "client": None, "server": None, "extensions": {}, "state": {}}
    if uid is not None:
        scope["extensions"]["peer_cred"] = {"pid": 4242, "uid": uid, "gid": uid}
    gone = asyncio.Event()
    received = False
    sent: list[dict] = []
    first = True

    async def receive():
        nonlocal first, received
        received = True
        if first:
            first = False
            return {"type": "http.request", "body": raw, "more_body": False}
        await gone.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)
        if message["type"] == "http.response.start" and leave_after_head:
            gone.set()

    await app(scope, receive, send)
    start = sent[0]
    headers = {name.decode().lower(): value.decode() for name, value in start.get("headers", [])}
    return Answer(start["status"], headers, b"".join(m.get("body", b"") for m in sent[1:]), received)


# The apps served on a real uvicorn, on short Unix sockets.

@dataclasses.dataclass
class Served:
    status: Path
    control: Path

    def client(self, which: str = "control", timeout: float = 5) -> httpx.AsyncClient:
        path = self.status if which == "status" else self.control
        return httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=str(path)), base_url="http://gate",
                                 timeout=timeout)


@contextlib.asynccontextmanager
async def serving(core, *, admins=(ME,), front_uid=ME, header_timeout_s: float = 10.0):
    """The two apps on a real uvicorn each, through Task 9's protocol (the caller's uid in each request, the header
    deadline) and run_servers' configuration, in this event loop."""
    folder = Path(tempfile.mkdtemp(prefix="ga", dir="/tmp"))
    built = apps(core, admins=admins, front_uid=front_uid)
    protocol = protocols.make_protocol(peer_cred=True, header_timeout_s=header_timeout_s)
    servers, tasks = [], []
    try:
        for name, app in zip(("status", "control"), built, strict=True):
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.bind(str(folder / f"{name}.sock"))
            listener.listen()
            server = serve.QuietServer(serve._config(app, protocol, 0.5))
            servers.append(server)
            tasks.append(asyncio.create_task(server.serve(sockets=[listener])))
        while not all(server.started for server in servers):
            await asyncio.sleep(0.01)
        yield Served(folder / "status.sock", folder / "control.sock")
    finally:
        built[0].state.stopping.set()  # as `spark gate` does when its stop begins
        for server in servers:
            server.should_exit = True
        await asyncio.gather(*tasks, return_exceptions=True)
        shutil.rmtree(folder, ignore_errors=True)


async def raw(path: Path, head: bytes, body: bytes = b"") -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    reader, writer = await asyncio.open_unix_connection(str(path))
    writer.write(head + body)
    await writer.drain()
    return reader, writer


async def read_answer(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    """A whole answer with a Content-Length: its status and its body."""
    head = await reader.readuntil(b"\r\n\r\n")
    status = int(head.split(b" ", 2)[1])
    length = next(int(line.split(b":", 1)[1]) for line in head.split(b"\r\n")
                  if line.lower().startswith(b"content-length:"))
    return status, await reader.readexactly(length)


async def closed_within(reader: asyncio.StreamReader, seconds: float) -> bool:
    """Whether the connection reaches its end within `seconds`, whatever comes before it."""
    try:
        async with asyncio.timeout(seconds):
            while await reader.read(65536):
                pass
        return True
    except TimeoutError:
        return False


def ready_after(gate, model: str, seconds: float) -> list[float]:
    """The stand-in llama-swap readies `model`'s held load `seconds` from now; the list gets when it did."""
    readied: list[float] = []

    def ready() -> None:
        gate.swap.ready(model)
        readied.append(time.monotonic())

    asyncio.get_running_loop().call_later(seconds, ready)
    return readied


def admit_body(model: str, deadline: float, request_id: str = "r1", key: str = "dan-mac") -> dict:
    return {"model": model, "key": key, "deadline": deadline, "request_id": request_id}


def busy_on(model: str, *, at_s: float, count: int = 1) -> dict:
    """The front's report of `count` requests in flight on `model`, Dan's, since `at_s`."""
    return {model: {"count": count, "oldest_started_at": at_s, "last_end_at": None,
                    "requests": [{"key": "dan-mac", "started_at": at_s}] * count}}


# Who may call each route (Task 10's table), decided on the uid before the body is read.

@pytest.mark.anyio
async def test_an_inflight_report_from_any_uid_but_spark_fronts_is_refused(tmp_path):
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        status, _ = apps(core)
        report = {"seq": 1, "front_started_at": gate.clock.now - 60, "models": busy_on(GEMMA, at_s=gate.clock.now - 5),
                  "draining": []}
        refused = await call(status, "POST", "/v1/front/inflight", uid=DAN, body=report)
        assert refused.status == 403
        assert refused.json() == {"message": gate_app.ONLY_THE_FRONT, "code": None}
        assert not refused.received  # refused on the uid, its body never read
        assert core.front.snapshot is None  # the snapshot unchanged
        taken = await call(status, "POST", "/v1/front/inflight", uid=FRONT_UID, body=report)
        assert taken.status == 200
        assert core.front.snapshot == report
        # Task 32's review (C1): `holding` and `at`, when given, are a flag and a time.
        for bad in ({"holding": "yes"}, {"holding": None}, {"at": "now"}, {"at": float("inf")}):
            refused = await call(status, "POST", "/v1/front/inflight", uid=FRONT_UID, body=report | bad)
            assert refused.status == 400, bad
        held = report | {"holding": True, "at": gate.clock.now}
        assert (await call(status, "POST", "/v1/front/inflight", uid=FRONT_UID, body=held)).status == 200
        assert core.front.snapshot == held
        await core.stop()


@pytest.mark.anyio
async def test_admit_and_the_front_channel_are_spark_fronts_alone(tmp_path):
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        status, _ = apps(core)
        now = gate.clock.now
        routes = [("POST", "/v1/admit", admit_body(GEMMA, now + 30)),
                  ("GET", "/v1/front/events", None),
                  ("POST", "/v1/front/drained", {"model": GEMMA, "drain_id": "d1"}),
                  ("POST", "/v1/front/busy", {"model": GEMMA, "drain_id": "d1"}),
                  ("POST", "/v1/front/refused", {"at": now, "code": "model_not_found", "model": "gpt-4",
                                                 "key": "dan-mac", "message": "There's no model called gpt-4 here."})]
        for method, path, body in routes:
            refused = await call(status, method, path, uid=DAN, body=body, leave_after_head=True)
            assert (path, refused.status, refused.received) == (path, 403, False)
            assert refused.json() == {"message": gate_app.ONLY_THE_FRONT, "code": None}
        for method, path, body in routes:
            answered = await call(status, method, path, uid=FRONT_UID, body=body, leave_after_head=True)
            assert (path, answered.status) == (path, 200)
        assert core.front.connected is False  # the events call ended with its client
        await core.stop()


@pytest.mark.anyio
async def test_a_request_without_peer_cred_is_refused(tmp_path):
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        for app in apps(core):
            refused = await call(app, "GET", "/v1/status", uid=None)
            assert (refused.status, refused.received) == (403, False)
            assert refused.json() == {"message": gate_app.NO_PEER, "code": None}
        await core.stop()


@pytest.mark.anyio
async def test_the_control_socket_serves_only_spark_admin_and_root(tmp_path):
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        _, control = apps(core, admins=(DAN,))
        assert (await call(control, "GET", "/v1/status", uid=DAN)).status == 200
        assert (await call(control, "GET", "/v1/status", uid=0)).status == 200
        refused = await call(control, "GET", "/v1/status", uid=AGENT)
        assert refused.status == 403
        assert refused.json() == {"message": gate_app.ONLY_ADMIN, "code": None}
        refused = await call(control, "POST", "/v1/load", uid=AGENT, body={"model": "coder"})
        assert (refused.status, refused.received) == (403, False)
        await core.stop()


def _route_body(route: gateproto.Route) -> dict | None:
    return None if route.method in ("GET", "DELETE") else {}


def _path(route: gateproto.Route) -> str:
    return route.path.replace("{id}", "s1").replace("{model}", "coder")


@pytest.mark.anyio
@pytest.mark.parametrize("route", gateproto.ROUTES, ids=lambda r: f"{r.socket}-{r.method}-{r.path}")
async def test_every_route_refuses_whoever_its_callers_dont_include(tmp_path, route):
    # Task 10's table: `front` the front's uid only; `admin` a spark-admin uid or root; `users` and `owner` any caller
    # the socket lets in, but none the gate can't name; and a session can't be held by the stack's own accounts. Each
    # refusal comes before the body is read.
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        app = apps(core)[gateproto.SOCKETS.index(route.socket)]
        outsiders = {"front": [None, DAN, AGENT, 0], "admin": [None, AGENT, FRONT_UID, SPARK],
                     "users": [None], "owner": [None]}[route.callers]
        for uid in outsiders:
            refused = await call(app, route.method, _path(route), uid=uid, body=_route_body(route))
            assert (uid, refused.status, refused.received) == (uid, 403, False)
            assert set(refused.json()) == {"message", "code"}
        if route == gateproto.Route("status", "POST", "/v1/sessions", "owner"):
            for uid in (SPARK, FRONT_UID):  # spark and spark-front run the stack itself
                refused = await call(app, "POST", "/v1/sessions", uid=uid, body={})
                assert (uid, refused.status, refused.received) == (uid, 403, False)
        await core.stop()


@pytest.mark.anyio
async def test_the_apps_serve_gateprotos_routes_and_no_other(tmp_path):
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        status, control = apps(core)
        served = {(socket_name, route.path, method)
                  for socket_name, app in (("status", status), ("control", control))
                  for route in app.routes for method in route.methods - {"HEAD"}}
        assert served == {(r.socket, r.path, r.method) for r in gateproto.ROUTES}
        # A route on the other socket says where it is; one on neither, and a method a route doesn't take, are
        # refused in words, as every answer of the gate's is (GateRefusalBody).
        elsewhere = await call(status, "POST", "/v1/load", uid=DAN, body={"model": "coder"})
        assert (elsewhere.status, elsewhere.received) == (404, False)
        assert elsewhere.json() == {"message": gate_app.ON_THE_CONTROL_SOCKET, "code": None}
        nowhere = await call(control, "GET", "/v1/nonesuch", uid=DAN)
        assert nowhere.status == 404 and nowhere.json() == {"message": gate_app.NO_SUCH_ROUTE, "code": None}
        wrong = await call(control, "GET", "/v1/load", uid=DAN)
        assert wrong.status == 405 and set(wrong.json()) == {"message", "code"}
        slashed = await call(control, "GET", "/v1/status/", uid=DAN)  # never a redirect: it isn't a gate's answer
        assert slashed.status == 404
        await core.stop()


@pytest.mark.anyio
async def test_a_session_is_its_owners_and_dan_may_end_any(tmp_path, monkeypatch):
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        status, _ = apps(core)
        monkeypatch.setattr(core.sessions, "_owner", lambda pid: (AGENT, 77) if pid == 4321 else None)
        monkeypatch.setattr(core.sessions, "_process_live", lambda session: True)
        begun = await call(status, "POST", "/v1/sessions", uid=AGENT,
                           body={"model": CODER, "pid": 4321, "label": "agent's pi"})
        assert begun.status == 200
        granted = begun.json()
        assert set(granted) == {"id", "expires_at"}
        sid = granted["id"]
        assert core.state.sessions[sid].uid == AGENT  # the caller's uid, as SO_PEERCRED gave it
        # Another account's renewal and end are refused; its owner's renewal is taken, and Dan may end it.
        refused = await call(status, "POST", f"/v1/sessions/{sid}/renew", uid=DAN)
        assert refused.status == 403 and refused.json()["code"] is None
        refused = await call(status, "DELETE", f"/v1/sessions/{sid}", uid=FRONT_UID)
        assert refused.status == 403
        assert (await call(status, "POST", f"/v1/sessions/{sid}/renew", uid=AGENT)).status == 200
        assert (await call(status, "DELETE", f"/v1/sessions/{sid}", uid=DAN)).status == 200
        assert core.state.sessions == {}
        gone = await call(status, "DELETE", f"/v1/sessions/{sid}", uid=AGENT)
        assert gone.status == 404 and set(gone.json()) == {"message", "code"}
        await core.stop()


@pytest.mark.anyio
async def test_a_session_takes_a_role_as_every_control_route_does(tmp_path, monkeypatch):
    # The controller's ruling at Task 30's review (I1): `/v1/sessions` resolves its model as `spark load` does, the
    # name first, then a role, so 2b's hooks name `coder` and survive a coder swap (Task 42). A name that is neither
    # is refused in words that quote it as given.
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        status, _ = apps(core)
        monkeypatch.setattr(core.sessions, "_owner", lambda pid: (AGENT, 77) if pid == 4321 else None)
        monkeypatch.setattr(core.sessions, "_process_live", lambda session: True)
        begun = await call(status, "POST", "/v1/sessions", uid=AGENT,
                           body={"model": "coder", "pid": 4321, "label": "pi in Orca"})
        assert begun.status == 200
        assert core.state.sessions[begun.json()["id"]].model == CODER  # the registry's name, kept
        by_name = await call(status, "POST", "/v1/sessions", uid=AGENT,
                             body={"model": GEMMA, "pid": 4321, "label": "agent's pi"})
        assert by_name.status == 200 and core.state.sessions[by_name.json()["id"]].model == GEMMA
        unknown = await call(status, "POST", "/v1/sessions", uid=AGENT,
                             body={"model": "nope", "pid": 4321, "label": "x"})
        assert (unknown.status, unknown.json()) == (409, {
            "message": "There's no model 'nope' in the registry, so no session can keep it loaded.", "code": None})
        await core.stop()


# What the status socket shows a caller: its own refusals and events, and the system's.

def _no_fit_words(*, dans: bool) -> str:
    """The coder's no_fit at the examples' moment, in the asking key's group's words: Dan's names his python job,
    agent's sees it only as a process of Dan's."""
    group = "dan" if dans else "agent"
    return messages.refusal("no_fit", Moment(
        needed_gib=FOOTPRINT[CODER], free_gib=18, available_gib=48, reserve_gib=24, owed_gib=6,
        holders=[Holder("python3 (chendaniely)", 32, True)], model_label="the coder", model_command="coder",
        key_label="pi on the Mac" if dans else "agent", words=group, names_processes=dans,
        wait_s=30 if dans else 600)).message


@pytest.mark.anyio
async def test_status_through_the_status_socket_shows_only_the_callers_own_refusals(tmp_path):
    dans, agents = _no_fit_words(dans=True), _no_fit_words(dans=False)
    refusals = deque([
        RefusalRecord(at=at(9, 1), model=CODER, key="dan-mac", uid=None, code="no_fit", message=dans),
        RefusalRecord(at=at(9, 2), model=CODER, key="agent", uid=None, code="no_fit", message=agents),  # its key's
        RefusalRecord(at=at(9, 3), model=CODER, key=None, uid=AGENT, code="no_fit", message=agents),  # its uid's
    ])
    recent = deque([RecentRecord(at=at(8, 0), kind="unload", text="Dan's own event", model=CODER, uids=[DAN]),
                    RecentRecord(at=at(8, 1), kind="brake_released", text="brake released; nothing to reload",
                                 model=None, uids=[])])
    hold = RoomHold(size_gib=10.0, until=None, boot_id=BOOT, created_at=at(8, 30), unloaded=[], total_gib=10.0)
    gate = Gate(tmp_path, state=loaded(*RESIDENTS, refusals=refusals, recent=recent, room_hold=hold),
                shown=dict.fromkeys(RESIDENTS, "ready"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        status, control = apps(core)
        agents_view = (await call(status, "GET", "/v1/status", uid=AGENT)).json()
        assert [row["text"] for row in agents_view["recent"]] == ["brake released; nothing to reload", agents, agents]
        assert "a process of Dan's, 32 GiB" in agents and "chendaniely" not in json.dumps(agents_view)
        full = (await call(control, "GET", "/v1/status", uid=DAN)).json()
        assert [row["text"] for row in full["recent"]] == ["Dan's own event", "brake released; nothing to reload",
                                                         dans, agents, agents]
        assert "python3 (chendaniely) 32 GiB" in dans
        # Dan's own account through the status socket sees his own, by his key's account and by his uid.
        dans_view = (await call(status, "GET", "/v1/status", uid=DAN)).json()
        assert [row["text"] for row in dans_view["recent"]] == ["Dan's own event", "brake released; nothing to reload",
                                                              dans]
        # Free for a load as the caller's group sees it: agent's never loads into make-room's hold, so it counts it.
        free = full["memory"]["free_for_a_load_gib"]
        assert dans_view["memory"]["free_for_a_load_gib"] == free
        assert agents_view["memory"]["free_for_a_load_gib"] == free - 10
        await core.stop()


def _keys_of(typed) -> set[str]:
    return set(get_type_hints(typed))


@pytest.mark.anyio
async def test_status_view_has_every_key(tmp_path):
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        for app, uid in zip(apps(core), (AGENT, DAN), strict=True):
            view = (await call(app, "GET", "/v1/status", uid=uid)).json()
            assert set(view) == _keys_of(gateproto.StatusView)
            assert set(view["memory"]) == _keys_of(gateproto.MemoryView)
            assert set(view["health"]) == _keys_of(gateproto.HealthView)
            assert set(view["health"]["brake"]) == _keys_of(gateproto.BrakeHealth)
            assert set(view["health"]["ntfy"]) == _keys_of(gateproto.NtfyHealth)
            assert [set(model) for model in view["models"]] == [_keys_of(gateproto.ModelView)] * 4
        await core.stop()


MOMENT = at(9, 14)  # the examples' moment (plan.md, *What you see in Phase 2a*), past its 09:12 refusal


@pytest.mark.anyio
async def test_status_view_at_the_examples_moment(tmp_path):
    # The residents' footprints 32, 8 and 3, holding 27, 7 and 3: 6 GiB still owed. 117 at idle, 48 available: Dan's
    # python job, 26 GiB once the residents' growth still owed is counted. The coder marked by the brake at 03:12,
    # seen using 26; the brake's hold released at 03:40; agent's request for the coder waiting for Dan, 2 min of 10.
    held = {GEMMA: 27.0, EMBED: 7.0, WHISPER: 3.0}
    state = loaded(*RESIDENTS, brake_marks={CODER: BrakeMark(CODER, at(3, 12), 26.0)},
                   last_auto_release_at=at(3, 40),
                   recent=deque([RecentRecord(at=at(3, 40), kind="brake_released", model=None, uids=[],
                                              text=messages.recent_brake_released(["Gemma", "the embeddings"],
                                                                                  by_dan=False))]),
                   refusals=deque([
                       RefusalRecord(at=at(8, 50), model=CODER, key="agent", uid=None, code="no_fit",
                                     message=_no_fit_words(dans=False)),
                       RefusalRecord(at=at(9, 12), model=CODER, key="dan-mac", uid=None, code="no_fit",
                                     message=_no_fit_words(dans=True))]))
    state.models = {name: dataclasses.replace(record(name), load_fall_gib=fall) for name, fall in held.items()}
    gate = Gate(tmp_path, state=state, shown=dict.fromkeys(RESIDENTS, "ready"))
    gate.clock.now = MOMENT
    gate.available = 48.0
    # The brake's start check passed at 09:00 (Task 29: the gate reads it for `health`).
    write_key_check(gate.brake, KeyCheck(_check_at(at(9, 0)), True, "passed", "credential llamaswap-key"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        front = Front(core)
        front.report(**busy_on(GEMMA, at_s=MOMENT - 20))
        waiting = asyncio.create_task(core.admitter.admit(
            AdmitRequest("r-agent", CODER, "key", KEYS["agent"], MOMENT + 480)))  # received 2 min ago, of 10
        await settle()
        core.read_memory()
        await core.each_five_s()  # the units read: the brake's is up
        core.each_second()  # the activity record written now
        status, control = apps(core)
        full = (await call(control, "GET", "/v1/status", uid=DAN)).json()

        def model(name, **more):
            known = core.registry.models[name]
            return {"name": name, "label": known.label, "resident": known.resident,
                    "footprint_gib": known.footprint_gib, "state": "ready", "inflight": 0, "oldest_request_s": None,
                    "last_use": MOMENT, "pinned": False, "pinned_until": None, "sessions": [],
                    "brake_mark": None} | more

        assert full == {
            "schema": 1, "host": "brightroar", "at": MOMENT,
            "memory": {"total_gib": 121.6, "available_gib": 48.0, "brake_gib": 20, "warn_gib": 28,
                       "above_brake_gib": 28.0, "reserve_gib": 24, "owed_gib": 6.0, "held_gib": 0.0,
                       "free_for_a_load_gib": 18.0, "unaccounted_gib": 26.0, "ceiling_gib": 102.0,
                       "committed_gib": 43.0, "starting_gib": 0.0, "ceiling_binds": False},
            "models": [model(GEMMA, inflight=1, oldest_request_s=20.0), model(EMBED), model(WHISPER),
                       model(CODER, state="not_loaded", last_use=None,
                             brake_mark={"at": at(3, 12), "seen_gib": 26.0})],
            "waiting": [{"key_label": "agent", "model": CODER, "waited_s": 120.0, "wait_s": 600, "why": "dan",
                         "free_gib": 18.0}],
            "paused": None, "held": None, "pins": [], "sessions": [],
            "recent": [
                {"at": at(3, 40), "text": "brake released; reloading Gemma and the embeddings", "code": None,
                 "model": None, "key_label": None},
                {"at": at(8, 50), "text": _no_fit_words(dans=False), "code": "no_fit", "model": CODER,
                 "key_label": "agent"},
                {"at": at(9, 12), "text": _no_fit_words(dans=True), "code": "no_fit", "model": CODER,
                 "key_label": "pi on the Mac"}],
            "health": {"front": "ok", "gate": "ok", "brake": {"status": "ok", "key_checked_at": at(9, 0)},
                       "llama_swap": "ok", "ntfy": {"status": "ok", "failing_since": None, "dropped": 0},
                       "activity_age_s": 0.0, "unticketed_engines": [], "no_ticket_refusals": 0,
                       "damaged_at": None, "damaged_kept_as": None, "read_problems": []},
            "applying": None, "problems": []}
        # agent's view of the same moment, through the status socket: its own refusal and the system's event, the
        # brake's release, which every caller sees; Dan's refusal left out, and Dan's process unnamed.
        agents = (await call(status, "GET", "/v1/status", uid=AGENT)).json()
        assert agents == full | {"recent": full["recent"][:2]}
        assert "chendaniely" not in json.dumps(agents) and "a process of Dan's, 32 GiB" in json.dumps(agents)
        waiting.cancel()
        await core.stop()


# The size cap and the request timeout, on a real uvicorn.

@LINUX_ONLY
@pytest.mark.anyio
async def test_a_request_over_the_size_cap_is_refused_not_crashed(tmp_path):
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    over = gateproto.MAX_REQUEST_BYTES + 1  # 65,537 bytes
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        async with serving(core) as served:
            head = f"POST /v1/canary HTTP/1.1\r\nHost: gate\r\nContent-Length: {over}\r\n\r\n".encode()
            reader, writer = await raw(served.control, head, b"{" * over)
            status, body = await read_answer(reader)
            assert status == 413
            assert json.loads(body) == {"message": gate_app.TOO_BIG, "code": None}
            writer.close()
            # Chunked, with no length to refuse it by: counted as it is read, and refused the same way.
            head = b"POST /v1/canary HTTP/1.1\r\nHost: gate\r\nTransfer-Encoding: chunked\r\n\r\n"
            chunk = b"{" * 4096
            chunks = b"".join(b"%x\r\n%s\r\n" % (len(chunk), chunk) for _ in range(over // 4096 + 1)) + b"0\r\n\r\n"
            reader, writer = await raw(served.control, head, chunks)
            status, body = await read_answer(reader)
            assert (status, json.loads(body)) == (413, {"message": gate_app.TOO_BIG, "code": None})
            writer.close()
            async with served.client() as client:  # the gate serves on
                assert (await client.get("/v1/status")).status_code == 200
        await core.stop()


@LINUX_ONLY
@pytest.mark.anyio
async def test_a_caller_that_hangs_times_out(tmp_path, monkeypatch):
    monkeypatch.setattr(gate_app, "REQUEST_TIMEOUT_S", 0.3)
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    gate.swap.hold_loads = True
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        async with serving(core) as served:
            # A body that never finishes: 10 of its 100 bytes, then nothing. The header deadline is 10 s here, so only
            # the request timeout can close it.
            head = b"POST /v1/front/inflight HTTP/1.1\r\nHost: gate\r\nContent-Length: 100\r\n\r\n"
            began = time.monotonic()
            reader, writer = await raw(served.status, head, b'{"seq": 1,')
            assert await closed_within(reader, 1.0)
            assert time.monotonic() - began >= 0.3
            writer.close()
            # An admit call held for 2 s, past the timeout: its key's wait bounds it, never REQUEST_TIMEOUT_S.
            readied = ready_after(gate, CODER, 2.0)
            async with served.client("status") as client:
                answer = await client.post("/v1/admit", json=admit_body(CODER, gate.clock.now + 30))
            answered = time.monotonic()
            assert readied and answered >= readied[0]  # answered once the load was done, 2 s on
            assert (answer.status_code, answer.json()) == (200, {"ok": True, "model": CODER})
        await core.stop()


@LINUX_ONLY
@pytest.mark.anyio
async def test_a_held_load_outlasts_the_request_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(gate_app, "REQUEST_TIMEOUT_S", 0.3)
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    gate.swap.hold_loads = True
    with anyio.fail_after(BOUND_S):
        core = await gate.started(may_pin=lambda uid: True)
        front = Front(core, auto=False)
        front.report()
        async with serving(core) as served, served.client() as client:
            readied = ready_after(gate, CODER, 1.0)
            async with client.stream("POST", "/v1/load", json={"model": "coder"}) as answer:
                assert answer.status_code == 200
                lines = [json.loads(line) async for line in answer.aiter_lines()]
            assert readied and time.monotonic() >= readied[0]  # its result came once the load was done, 1 s on
            assert lines[0] == {"kind": "loading", "model": CODER, "label": "the coder", "last_s": None}
            assert set(lines[-1]) == {"label", "seconds", "idle_min", "command"}
            assert (lines[-1]["label"], lines[-1]["command"]) == ("the coder", "coder")
            # The unload: its count at once, then its result once the drain is done, past the timeout.
            front.report(**busy_on(CODER, at_s=gate.clock.now))
            async with client.stream("POST", "/v1/unload", json={"model": "coder"}) as answer:
                lines = answer.aiter_lines()
                began = time.monotonic()
                assert json.loads(await anext(lines)) == {"kind": "unloading", "inflight": 1}
                assert time.monotonic() - began < 0.2
                await asyncio.sleep(0.5)
                [drain] = front.ops("drain")
                core.front.drained(CODER, drain["drain_id"])
                assert [json.loads(line) async for line in lines] == [{"unloaded": True, "pin_ended": False}]
            assert time.monotonic() - began >= 0.5
        await core.stop()


@LINUX_ONLY
@pytest.mark.anyio
@pytest.mark.parametrize("route", ["load", "pin", "make-room"])
async def test_a_streams_head_goes_at_once_even_behind_another_load(tmp_path, route):
    # Gemma's load, held by the stand-in for 2 s, has the one-load slot: the coder's load queues behind it. A handler
    # that sent its head only with its first line, or with its result, would leave the CLI reading a gate queued
    # behind a load as a gate that is down.
    shown = dict.fromkeys([EMBED, WHISPER], "ready") | ({GEMMA: "ready"} if route == "make-room" else {})
    gate = Gate(tmp_path, state=loaded(*shown), shown=shown)
    gate.swap.hold_loads = True  # Gemma's load, the boot preload's, waits until the test says
    with anyio.fail_after(BOUND_S):
        core = await gate.started(may_pin=lambda uid: True)
        front = Front(core, auto=False)  # it answers no drain: make-room's waits on Gemma's request in flight
        front.report(**busy_on(GEMMA, at_s=gate.clock.now) if route == "make-room" else {})
        gemma = None
        if route != "make-room":
            gemma = asyncio.create_task(core.admitter.admit(
                AdmitRequest("g1", GEMMA, "command", None, gate.clock.now + 200, uid=DAN)))  # joins that load
            await settle()
            assert gate.swap.loads() == [GEMMA]
            gate.swap.hold_loads = False  # the coder's own load, once it starts, comes ready at once
            readied = ready_after(gate, GEMMA, 2.0)
        async with serving(core) as served, served.client() as client:
            if route == "make-room":
                planned = (await client.post("/v1/make-room/plan", json={"size_gib": 80})).json()  # 50 free now
                assert planned["plan"]["unload"] == [GEMMA]
                request = client.stream("POST", "/v1/make-room", json={"plan_id": planned["plan_id"], "for_s": None})
            else:
                body = {"model": "coder"} | ({"until": None} if route == "pin" else {})
                request = client.stream("POST", f"/v1/{route}", json=body)
            began = time.monotonic()
            async with request as answer:
                assert answer.status_code == 200
                assert time.monotonic() - began < 0.5  # its head
                lines = answer.aiter_lines()
                first = json.loads(await anext(lines))
                assert time.monotonic() - began < 0.5  # and its first line
                if route == "make-room":
                    assert first == {"kind": "unloading", "model": GEMMA, "label": "Gemma", "inflight": 1}
                else:
                    assert (first["kind"], first["why"], first["loading_label"]) == ("waiting", "slot", "Gemma")
                    assert not readied  # Gemma's load still held
                    loading = json.loads(await anext(lines))
                    assert loading == {"kind": "loading", "model": CODER, "label": "the coder", "last_s": None}
                    assert readied and time.monotonic() >= readied[0]  # only once Gemma's load was done
                    result = json.loads(await anext(lines))
                    assert result["label"] == "the coder" and "message" not in result
        if gemma is not None:
            assert (await gemma).model == GEMMA
        await core.stop()


@LINUX_ONLY
@pytest.mark.anyio
@pytest.mark.parametrize("route", ["load", "unload", "pin", "make-room"])
async def test_a_streams_head_never_waits_for_its_first_line(tmp_path, monkeypatch, route):
    # Each stream's first line comes at once in the gate as it is, so the test above can't tell a head sent at once
    # from one sent with the first line: here the first line waits 1 s, and the head must not.
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    later = asyncio.Event()

    async def held(*args, **kwargs):
        await later.wait()
        yield {"kind": "loading", "model": CODER, "label": "the coder", "last_s": None}

    with anyio.fail_after(BOUND_S):
        core = await gate.started(may_pin=lambda uid: True)
        part = {"load": core.room, "unload": core.room, "pin": core.pins, "make-room": core.room}[route]
        monkeypatch.setattr(part, {"load": "load", "unload": "unload", "pin": "pin", "make-room": "execute"}[route],
                            held)
        body = {"make-room": {"plan_id": "p1", "for_s": None}, "pin": {"model": "coder", "until": None}}.get(
            route, {"model": "coder"})
        asyncio.get_running_loop().call_later(1.0, later.set)
        async with serving(core) as served, served.client() as client:
            began = time.monotonic()
            async with client.stream("POST", f"/v1/{route}", json=body) as answer:
                assert answer.status_code == 200 and time.monotonic() - began < 0.5  # its head, at once
                assert not later.is_set()
                assert [json.loads(line) async for line in answer.aiter_lines()][0]["kind"] == "loading"
        await core.stop()


@LINUX_ONLY
@pytest.mark.anyio
async def test_the_gate_keeps_serving_while_ntfy_hangs(tmp_path):
    hung = asyncio.Event()
    published = []

    async def publish(n):
        published.append(n)
        await hung.wait()  # ntfy never answers

    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started(publish=publish)
        status, _ = apps(core)
        sent = await call(status, "POST", "/v1/front/refused", uid=FRONT_UID,
                          body={"at": gate.clock.now, "code": "route_not_served", "model": None, "key": "agent",
                                "message": "This address isn't served here."})
        assert sent.status == 200
        for _ in range(100):
            if published:
                break
            await asyncio.sleep(0.01)
        assert published  # the publish under way, and hung
        async with serving(core) as served, served.client("status") as client:
            began = time.monotonic()
            assert (await client.get("/v1/status")).status_code == 200
            assert time.monotonic() - began < 0.5
        hung.set()
        await core.stop()


@pytest.mark.anyio
async def test_logs_reads_the_engines_last_lines_with_the_gates_key(tmp_path):
    fake = FakeLlamaSwap({KEY}, list(gate_core_models()))
    for name in RESIDENTS:
        fake.set_state(name, "ready")
    fake.log(CODER, [f"line {n}" for n in range(1, 11)])
    gate = Gate(tmp_path, state=loaded(*RESIDENTS))
    with anyio.fail_after(BOUND_S):
        async with serve_fake(fake) as url, AsyncLlamaSwap(url, KEY, load_timeout_s=30) as swap:
            core = await gate.started(llamaswap=swap)
            _, control = apps(core)
            answer = await call(control, "GET", "/v1/logs/coder", uid=DAN, query=b"n=5")  # a role, as spark load's
            assert (answer.status, answer.json()) == (200, {"lines": [f"line {n}" for n in range(6, 11)]})
            [seen] = [s for s in fake.requests if s.path == f"/logs/stream/{CODER}"]
            assert seen.headers["authorization"] == f"Bearer {KEY}"  # the gate's own key
            refused = await call(control, "GET", "/v1/logs/nonesuch", uid=DAN, query=b"n=5")
            assert refused.status == 409 and refused.json() == {
                "message": messages.no_such_model("nonesuch", "read"), "code": None}
            assert (await call(control, "GET", "/v1/logs/coder", uid=DAN, query=b"n=lots")).status == 400
            await core.stop()


def gate_core_models() -> list[str]:
    return [GEMMA, EMBED, WHISPER, CODER]


@LINUX_ONLY
@pytest.mark.anyio
async def test_a_dropped_admit_call_cancels_its_queue_entry(tmp_path, monkeypatch):
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    gate.available = 40.0  # 40 − 24 reserve: 16 free for a load, and the coder needs 33
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        cancelled = []
        cancel = core.admitter.cancel
        monkeypatch.setattr(core.admitter, "cancel", lambda rid: (cancelled.append((rid, time.monotonic())),
                                                                  cancel(rid)))
        async with serving(core) as served:
            body = json.dumps(admit_body(CODER, gate.clock.now + 30, request_id="r-gone")).encode()
            head = (b"POST /v1/admit HTTP/1.1\r\nHost: gate\r\nContent-Type: application/json\r\n"
                    b"Content-Length: %d\r\n\r\n" % len(body))
            reader, writer = await raw(served.status, head, body)
            for _ in range(100):
                if core.admitter.waiting():
                    break
                await asyncio.sleep(0.01)
            assert [row["why"] for row in core.admitter.waiting()] == ["memory"]
            writer.close()
            closed = time.monotonic()
            for _ in range(100):
                if cancelled:
                    break
                await asyncio.sleep(0.01)
            [(rid, when)] = cancelled
            assert rid == "r-gone" and when - closed < 1.0
            assert core.admitter.waiting() == []
            gate.available = None  # room now: nothing loads for a request that went
            core.read_memory()
            await settle()
            assert gate.swap.loads() == []
        await core.stop()


@pytest.mark.anyio
async def test_a_front_refusal_is_recorded_and_sent(tmp_path):
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        status, _ = apps(core)
        message = "There's no model called gpt-4 here. The models are …"
        answer = await call(status, "POST", "/v1/front/refused", uid=FRONT_UID,
                            body={"at": gate.clock.now - 1, "code": "model_not_found", "model": "gpt-4",
                                  "key": "dan-mac", "message": message})
        assert answer.status == 200
        [row] = core.status_view()["recent"]
        # The record keeps the front's own words; a name the registry doesn't have isn't kept as a model.
        assert row == {"at": gate.clock.now - 1, "text": message, "code": "model_not_found", "model": None,
                       "key_label": "pi on the Mac"}
        assert core.state.refusals[0].key == "dan-mac"
        for _ in range(100):
            if gate.published.of("refused"):
                break
            await asyncio.sleep(0.01)
        # One `refused`, the gate's words for the phone, built from what it knows: the key's label and words, and for
        # model_not_found the name asked for and the registry's models.
        assert gate.published.of("refused") == [
            messages.notification("refused", core.registry, code="model_not_found", moment=Moment(
                asked_name="gpt-4", key_label="pi on the Mac", words="dan", names_processes=True, wait_s=30,
                models=[(m.label, name) for name, m in core.registry.models.items()])).message]
        await core.stop()


@pytest.mark.anyio
@pytest.mark.parametrize("code", gateproto.FRONT_REFUSAL_CODES)
async def test_each_of_the_fronts_refusals_is_worded_for_the_phone(tmp_path, code):
    # The gate builds each code's moment from what it knows (Task 7's ruling): draining's why from the model's drain.
    state = loaded(*RESIDENTS)
    state.models[GEMMA] = dataclasses.replace(state.models[GEMMA], unload_requested_at=at(8, 59),
                                              drain_id="d1", drain_why="make-room")
    gate = Gate(tmp_path, state=state, shown=dict.fromkeys(RESIDENTS, "ready"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        status, _ = apps(core)
        model = {"model_not_found": "gpt-4", "route_not_served": None}.get(code, GEMMA)
        answer = await call(status, "POST", "/v1/front/refused", uid=FRONT_UID,
                            body={"at": gate.clock.now, "code": code, "model": model, "key": "agent",
                                  "message": f"the front's words for {code}"})
        assert answer.status == 200
        for _ in range(100):
            if gate.published.of("refused"):
                break
            await asyncio.sleep(0.01)
        [sent] = gate.published.of("refused")
        assert "agent" in sent
        if code == "draining":
            assert "make-room" in sent
        await core.stop()


@pytest.mark.anyio
async def test_a_front_refusal_that_isnt_one_is_refused_in_words(tmp_path):
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        status, _ = apps(core)
        base = {"at": gate.clock.now, "code": "model_not_found", "model": "x", "key": "dan-mac", "message": "m"}
        for wrong in ({"code": "no_fit"}, {"key": "nonesuch"}, {"at": "noon"}, {"message": "two\nlines"}):
            answer = await call(status, "POST", "/v1/front/refused", uid=FRONT_UID, body=base | wrong)
            assert answer.status == 400 and set(answer.json()) == {"message", "code"}
        not_json = await call(status, "POST", "/v1/front/refused", uid=FRONT_UID, body=b"{not json")
        assert not_json.status == 400 and not_json.json() == {"message": gate_app.NOT_AN_OBJECT, "code": None}
        assert list(core.state.refusals) == []
        await core.stop()


@pytest.mark.anyio
async def test_a_front_refusal_with_a_long_asked_name_is_kept_short(tmp_path):
    # model_not_found's words hold the client's own text, however long: the refusal is still kept and sent, its words
    # cut to TEXT_MAX with an ellipsis, and the asked name never kept as a model.
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        status, _ = apps(core)
        asked = "m" * 5000
        answer = await call(status, "POST", "/v1/front/refused", uid=FRONT_UID,
                            body={"at": gate.clock.now, "code": "model_not_found", "model": asked, "key": "dan-mac",
                                  "message": f"There's no model called {asked} here."})
        assert answer.status == 200
        [kept] = core.state.refusals
        assert len(kept.message) == gate_app.TEXT_MAX and kept.message.endswith("…") and kept.model is None
        for _ in range(100):
            if gate.published.of("refused"):
                break
            await asyncio.sleep(0.01)
        assert len(gate.published.of("refused")) == 1
        await core.stop()


@pytest.mark.anyio
async def test_make_room_plan_takes_a_size_or_all(tmp_path):
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        _, control = apps(core)
        planned = await call(control, "POST", "/v1/make-room/plan", uid=DAN, body={"all": True})
        assert planned.status == 200
        answer = planned.json()
        assert set(answer) == {"plan_id", "plan"}
        assert set(answer["plan"]) == {"candidates", "unload", "free_after_gib", "enough", "most_gib"}
        assert answer["plan"]["unload"] == [GEMMA, EMBED, WHISPER]  # the largest first
        sized = await call(control, "POST", "/v1/make-room/plan", uid=DAN, body={"size_gib": 10})
        assert sized.status == 200 and sized.json()["plan"]["unload"] == []  # 50 GiB free already
        for wrong in ({"all": 1}, {"size_gib": 10, "all": True}, {"size_gib": "10G"}, {}):
            refused = await call(control, "POST", "/v1/make-room/plan", uid=DAN, body=wrong)
            assert refused.status == 400 and set(refused.json()) == {"message", "code"}
        zero = await call(control, "POST", "/v1/make-room/plan", uid=DAN, body={"size_gib": 0})
        assert zero.status == 400
        assert zero.json()["message"] == "make-room's size has to be a finite number of GiB above 0."
        await core.stop()


def _room_hold(core, now: float) -> None:
    """make-room's hold of this boot, 40 GiB, nothing unloaded under it."""
    core.state.room_hold = RoomHold(size_gib=40.0, until=None, boot_id=BOOT, created_at=now, unloaded=[],
                                    total_gib=40.0)


@pytest.mark.anyio
async def test_release_takes_each_hold_alone_or_both_and_a_flag_left_out_is_false(tmp_path):
    # The controller's ruling at Task 30's review: `make brake-release` sends {brake: true} alone (Task 23), and
    # `spark make-room --done` {room: true, brake: false}; a flag left out is false, one that is there is true or
    # false, and a release that asks neither is refused in words. Before it, {brake: true} alone was a 400 (`room`
    # wasn't true or false), so make brake-release never reached the gate's release (the reviews missed it).
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        _, control = apps(core)
        # The brake's alone: its hold lifted, make-room's left standing.
        this_boots_hold(gate)
        _room_hold(core, gate.clock.now)
        lifted = await call(control, "POST", "/v1/release", uid=DAN, body={"brake": True})
        assert (lifted.status, lifted.json()) == (200, {"room": None, "brake": True, "reloading": []})
        assert read_hold(gate.brake) is None and core.state.room_hold is not None
        # make-room's alone: its hold ended, the brake's left standing.
        this_boots_hold(gate)
        ended = await call(control, "POST", "/v1/release", uid=DAN, body={"room": True})
        assert ended.status == 200
        assert ended.json() == {"room": {"unused_gib": 40.0, "total_gib": 40.0, "next_label": None}, "brake": False,
                                "reloading": []}
        assert core.state.room_hold is None and read_hold(gate.brake) is not None
        # As `spark make-room --done` sends it, with brake false: the same, and nothing to end now.
        done = await call(control, "POST", "/v1/release", uid=DAN, body={"room": True, "brake": False})
        assert (done.status, done.json()) == (200, {"room": None, "brake": False, "reloading": []})
        assert read_hold(gate.brake) is not None
        # Both at once.
        _room_hold(core, gate.clock.now)
        both = await call(control, "POST", "/v1/release", uid=DAN, body={"room": True, "brake": True})
        assert both.status == 200 and both.json()["brake"] is True and both.json()["room"] is not None
        assert read_hold(gate.brake) is None and core.state.room_hold is None
        # Neither: refused in words, nothing changed.
        this_boots_hold(gate)
        _room_hold(core, gate.clock.now)
        for neither in ({}, {"room": False}, {"room": False, "brake": False}):
            refused = await call(control, "POST", "/v1/release", uid=DAN, body=neither)
            assert (refused.status, refused.json()) == (400, {"message": gate_app.NOTHING_TO_RELEASE, "code": None})
        assert gate_app.NOTHING_TO_RELEASE == ("The release asks to end neither hold: `room: true` ends make-room's, "
                                               "and `brake: true` lifts the brake's.")
        # A flag that is there and isn't true or false: refused, naming it.
        for wrong, field in (({"room": "yes"}, "room"), ({"brake": None}, "brake"), ({"brake": 1}, "brake"),
                             ({"room": True, "brake": "no"}, "brake")):
            refused = await call(control, "POST", "/v1/release", uid=DAN, body=wrong)
            assert (refused.status, refused.json()["message"]) == (400, f"The request's {field} isn't true or false.")
        assert read_hold(gate.brake) is not None and core.state.room_hold is not None
        await core.stop()


@LINUX_ONLY
@pytest.mark.anyio
async def test_make_brake_release_lifts_the_hold_through_the_real_gate(tmp_path, capsys):
    # Task 23's `make brake-release` (brake.release_through_gate, the standard library's client) against the real
    # gate app on a temp socket: the gate lifts the hold and queues the brake's reloads, and the command says so in
    # brake_released_by_dan's words, never falling back to removing the file itself.
    # The brake unloaded the embeddings and its hold stands as the gate starts, so nothing reloads them before the
    # release does.
    gate = Gate(tmp_path, state=loaded(GEMMA, WHISPER), shown={GEMMA: "ready", WHISPER: "ready"})
    fired = datetime.fromtimestamp(gate.clock.now).isoformat(timespec="seconds")
    write_hold(gate.brake, Hold(fired, "19.6 GiB available", (EMBED,), BOOT, 1, None, 19.6))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        assert EMBED not in core.state.models
        async with serving(core) as served:
            code = await asyncio.to_thread(brake.release_through_gate, served.control, gate.brake)
        await core.stop()
    out, err = capsys.readouterr()
    assert (code, err) == (0, "")
    assert out == messages.brake_released_by_dan(["the embeddings"]) + "\n"
    assert out == "New loads resume. Reloading the embeddings…\n"
    assert read_hold(gate.brake) is None
    assert [r.kind for r in core.state.recent] == ["brake_released"]


@pytest.mark.anyio
async def test_the_canary_route_answers_found_or_not_only(tmp_path):
    needle = "canary-7f3e9b2c"
    state = loaded(*RESIDENTS, refusals=deque([RefusalRecord(
        at=at(8, 0), model=CODER, key="dan-mac", uid=None, code="no_fit", message=f"words around {needle} here")]))
    gate = Gate(tmp_path, state=state, shown=dict.fromkeys(RESIDENTS, "ready"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        _, control = apps(core)
        found = await call(control, "POST", "/v1/canary", uid=DAN, body={"needle": needle})
        assert (found.status, found.json()) == (200, {"found": ["refusals"]})  # where, never what surrounds it
        assert (await call(control, "POST", "/v1/canary", uid=DAN, body={"needle": "canary-absent"})).json() == {
            "found": []}
        (gate.launch / "refused-coder.json").write_text(json.dumps({"reason": "other-canary-11aa"}))
        assert (await call(control, "POST", "/v1/canary", uid=DAN, body={"needle": "other-canary-11aa"})).json() == {
            "found": ["launch"]}
        core.state.recent.append(RecentRecord(at=at(8, 1), kind="unload", text="third-canary-22bb", model=None))
        assert (await call(control, "POST", "/v1/canary", uid=DAN, body={"needle": "third-canary-22bb"})).json() == {
            "found": ["state"]}
        assert (await call(control, "POST", "/v1/canary", uid=DAN, body={"needle": ""})).status == 400
        await core.stop()


@pytest.mark.anyio
async def test_the_front_events_stream_its_channel_and_end_when_the_gate_stops(tmp_path):
    # The events call: hello and state at its head, what the core sends as it comes, a ping each PING_EVERY_S; and,
    # as `spark gate`'s stop begins, its end, and a held admit's answer, so the stop never waits out GRACEFUL_S on
    # either. The front asks the restarted gate again, with the admit's original deadline (Task 22).
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    gate.available = 40.0  # the coder doesn't fit: its admit is held
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        status, _ = apps(core)
        status.state.ping_every_s = 0.05
        seen: list[str | None] = []  # each line's op as the stream sends it; None for a line without one

        async def watched(scope, receive, send):
            async def tap(message):
                seen.extend(json.loads(line).get("op") for line in message.get("body", b"").splitlines())
                await send(message)

            await status(scope, receive, tap)

        events = asyncio.create_task(call(watched, "GET", "/v1/front/events", uid=FRONT_UID))
        held = asyncio.create_task(call(status, "POST", "/v1/admit", uid=FRONT_UID,
                                        body=admit_body(CODER, gate.clock.now + 30)))
        for _ in range(100):
            if core.front.connected and core.admitter.waiting():
                break
            await asyncio.sleep(0.01)
        assert core.front.send({"op": "unloaded", "model": WHISPER})  # onto the open call's stream
        # The stop waits for a ping after `unloaded`, never a fixed time. A sleep of 0.2 s, four pings' worth, saw none
        # whenever the loop stalled 0.15 s just as `unloaded` went out: the stream arms its next ping only then, so the
        # sleep ended first (forced on the Spark, 2026-10-14). The gate's own work can stall it there: admission's pass
        # for the held admit, and the state save that pass leads to, with its two fsyncs, run on the loop; and so can a
        # busy runner. Its own bound, 2 s, forty pings' worth, says what the stream sent if no ping came: twice the
        # margin of 1 s for an fsync spike or a VM's pause, and still a failure for a stream that never pings.
        try:
            with anyio.fail_after(2):
                while "unloaded" not in seen or "ping" not in seen[seen.index("unloaded"):]:
                    await asyncio.sleep(0.01)
        except TimeoutError:
            pytest.fail(f"no ping within 2 s of `unloaded`; the stream sent {seen}")
        status.state.stopping.set()
        streamed, answered = await events, await held
        ops = [line["op"] for line in streamed.lines()]
        # A heartbeat can come at any moment, even before the core's `unloaded` (CI saw it once in three runs): the
        # pings are passed over, and at least one came (the controller's ruling, at Task 23).
        assert [op for op in ops if op != "ping"][:3] == ["hello", "state", "unloaded"] and "ping" in ops
        assert answered.status == 503 and answered.json() == {"message": gate_app.GATE_STOPPING, "code": None}
        assert core.admitter.waiting() == [] and core.front.connected is False
        await core.stop()


@pytest.mark.anyio
async def test_an_event_whose_line_breaks_is_logged_and_owed_to_the_fronts_next_call(tmp_path, caplog):
    # The after-fix council's finding: a send that broke ended the stream as if it had ended, logged nothing, and lost
    # the event it had taken off the queue. Now the break is logged at ERROR, by its type and place, never its words;
    # the stream ends, so the front calls again, as after any drop; and the event taken and never written is owed, as
    # the words still queued at a call's end are (FrontChannel.close), so the next call sends it.
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        status, _ = apps(core)
        broke: list[bytes] = []

        async def breaks_once(scope, receive, send):
            async def tap(message):
                if not broke and b'"unloaded"' in message.get("body", b""):
                    broke.append(message["body"])
                    raise RuntimeError("stand-in words-q4, which the log never shows")
                await send(message)

            await status(scope, receive, tap)

        first = asyncio.create_task(call(breaks_once, "GET", "/v1/front/events", uid=FRONT_UID))
        await core.front.opened()
        assert core.front.send({"op": "unloaded", "model": WHISPER})
        ended = await first
        assert broke and [op for op in (line["op"] for line in ended.lines()) if op != "ping"] == ["hello", "state"]
        assert core.front.connected is False
        errors = [r.getMessage() for r in caplog.records if r.name in LOGS and r.levelno >= logging.ERROR]
        assert len(errors) == 1 and errors[0].startswith("the gate's GET /v1/front/events broke: RuntimeError at ")
        assert "words-q4" not in caplog.text
        caplog.clear()
        seen: list[dict] = []  # the next call's lines, as it sends them

        async def watched(scope, receive, send):
            async def tap(message):
                seen.extend(json.loads(line) for line in message.get("body", b"").splitlines())
                await send(message)

            await status(scope, receive, tap)

        again = asyncio.create_task(call(watched, "GET", "/v1/front/events", uid=FRONT_UID))
        try:
            with anyio.fail_after(2):
                while not any(line.get("op") == "unloaded" for line in seen):
                    await asyncio.sleep(0.01)
        except TimeoutError:
            pytest.fail(f"the `unloaded` whose line broke never came on the next call, which sent {seen}")
        status.state.stopping.set()
        await again
        assert [line for line in seen if line.get("op") not in ("hello", "state", "ping")] == [
            {"op": "unloaded", "model": WHISPER}]
        await core.stop()


# The bounded routes' time, and the gate's stop (the controller's rulings at Task 19's review).

@pytest.mark.anyio
async def test_a_bounded_route_past_its_bound_says_its_still_finishing(tmp_path, monkeypatch):
    # Its work goes on, so its 503 says so, in Task 6's voice, and pi's retry list matches nothing in it.
    monkeypatch.setattr(gate_app, "REQUEST_TIMEOUT_S", 0.2)
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    finished = []

    async def slow_lines(model, n=20, read_s=1.0):
        await asyncio.sleep(0.5)
        finished.append(model)
        return ["a line"]

    monkeypatch.setattr(gate.swap, "last_lines", slow_lines)
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        _, control = apps(core)
        answer = await call(control, "GET", "/v1/logs/coder", uid=DAN, query=b"n=5")
        assert answer.status == 503
        assert answer.json() == {"message": gate_app.STILL_FINISHING, "code": None}
        assert gate_app.STILL_FINISHING == ("The gate is still finishing that: `spark status` on the Spark shows "
                                            "when it's done.")
        assert messages.pi_retry_match(gate_app.STILL_FINISHING) is None
        assert finished == []
        await asyncio.sleep(0.5)
        assert finished == [CODER]  # its work went on to its end
        await core.stop()


@pytest.mark.anyio
@pytest.mark.parametrize("route", ["/v1/apply/end", "/v1/undrain-all"])
async def test_apply_end_and_undrain_all_are_bounded_past_their_running_read(tmp_path, monkeypatch, route):
    # Each reads /running first, which may take up to LLAMA_SWAP_HUNG_S, 10 s: their bound is APPLY_END_TIMEOUT_S,
    # 15 s, not REQUEST_TIMEOUT_S, so a slow read never reads as a failure to make apply.
    assert gateproto.APPLY_END_TIMEOUT_S == 15.0 > gateproto.LLAMA_SWAP_HUNG_S
    monkeypatch.setattr(gate_app, "REQUEST_TIMEOUT_S", 0.2)
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        core.drain_all(DAN)
        _, control = apps(core)
        gate.swap.pause_next = True  # the route's /running read waits
        asyncio.get_running_loop().call_later(0.5, gate.swap.release)
        answer = await call(control, "POST", route, uid=DAN)
        assert (answer.status, answer.json()) == (200, {})
        assert core.state.applying.ended is True
        await core.stop()


class Closer:
    """A client `spark gate` closes once the core has stopped: what bounded work was still running as it closed."""

    def __init__(self, gate, app):
        self.gate, self.app = gate, app
        self.pending: list | None = None

    async def aclose(self) -> None:
        self.pending = [work for work in self.app.state.bounded_work if not work.done()]
        self.gate.swap.closed = True


@pytest.mark.anyio
@pytest.mark.parametrize("route", ["apply/end", "logs"])
async def test_the_gates_stop_ends_a_503d_routes_work_before_the_client_closes(tmp_path, monkeypatch, route):
    # A bounded route answered 503 and its work went on; then the gate's stop. That work is waited for briefly, then
    # cancelled, before core.stop(): so it never uses the llama-swap client once that has closed, and never saves
    # after the core's last save.
    monkeypatch.setattr(gate_app, "REQUEST_TIMEOUT_S", 0.1)
    monkeypatch.setattr(gate_app, "APPLY_END_TIMEOUT_S", 0.1)
    monkeypatch.setattr(gate_app, "FINISH_WORK_S", 0.1)
    from spark.gate import main as gate_main

    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    let_go = asyncio.Event()
    after_close = []
    running, last_lines = gate.swap.running, gate.swap.last_lines

    async def slow(call_name, real, *args, **kwargs):
        await let_go.wait()
        if getattr(gate.swap, "closed", False):
            after_close.append(call_name)
            raise RuntimeError("Cannot send a request, as the client has been closed.")  # as httpx's
        return await real(*args, **kwargs)

    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        core.drain_all(DAN)
        monkeypatch.setattr(gate.swap, "running", lambda: slow("running", running))
        monkeypatch.setattr(gate.swap, "last_lines", lambda *a, **k: slow("last_lines", last_lines, *a, **k))
        status, control = apps(core)
        method, path, query = ("POST", "/v1/apply/end", b"") if route == "apply/end" else (
            "GET", "/v1/logs/coder", b"n=5")
        answer = await call(control, method, path, uid=DAN, query=query)
        assert answer.json() == {"message": gate_app.STILL_FINISHING, "code": None}
        closer = Closer(gate, status)
        await gate_main.shutdown(core, status, [closer])
        assert closer.pending == []  # nothing that uses the client was left running as it closed
        saved = (gate.state_dir / "state.json").read_bytes()
        let_go.set()
        await settle()
        await asyncio.sleep(0.05)
        assert after_close == []
        assert (gate.state_dir / "state.json").read_bytes() == saved  # nothing saved after the core's last save
        state, _ = load_state(gate.state_dir)
        assert state.clean_shutdown is True and state.applying.ended is False  # apply's end was cut, not half-done


@pytest.mark.anyio
async def test_a_new_admit_or_stream_at_the_gates_stop_starts_nothing(tmp_path):
    # Once the stop has begun, a call that would wait is answered 503 at once, its body unread, so no work starts in
    # the moment before uvicorn stops taking connections.
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        status, control = apps(core)
        status.state.stopping.set()
        calls = [(status, "POST", "/v1/admit", admit_body(CODER, gate.clock.now + 30), FRONT_UID),
                 (status, "GET", "/v1/front/events", None, FRONT_UID),
                 (control, "POST", "/v1/load", {"model": "coder"}, DAN),
                 (control, "POST", "/v1/unload", {"model": GEMMA}, DAN),
                 (control, "POST", "/v1/pin", {"model": "coder", "until": None}, DAN),
                 (control, "POST", "/v1/make-room", {"plan_id": "p1", "for_s": None}, DAN)]
        for app, method, path, body, uid in calls:
            answer = await call(app, method, path, uid=uid, body=body)
            assert (path, answer.status, answer.received) == (path, 503, False)
            assert answer.json() == {"message": gate_app.GATE_STOPPING, "code": None}
        await settle()
        assert core.admitter.waiting() == [] and gate.swap.loads() == [] and gate.swap.unloads() == []
        assert core.front.connected is False
        await core.stop()


def test_the_test_only_uid_overrides_are_said_in_the_journal(caplog):
    from spark.gate import main as gate_main

    with caplog.at_level(logging.WARNING, logger="spark.gate.main"):
        assert gate_main.front_uid({"SPARK_FRONT_UID": "990"}) == 990
        assert gate_main.admin_uids({"SPARK_ADMIN_UIDS": "1000,1001"})() == {1000, 1001}
    said = [r.getMessage() for r in caplog.records if r.name == "spark.gate.main" and r.levelno == logging.WARNING]
    assert len(said) == 2
    assert "SPARK_FRONT_UID" in said[0] and "SPARK_ADMIN_UIDS" in said[1]
    assert all("for the tests only" in line for line in said)


def test_spark_admins_members_are_read_again_after_a_short_while(monkeypatch):
    # Asked at each request that needs them, from a copy at most ADMINS_TTL_S old, so a member added is known without
    # a restart, and the group database isn't read for every request.
    from types import SimpleNamespace

    from spark.gate import main as gate_main

    members = ["chendaniely"]
    reads = []

    def getgrnam(name):
        reads.append(name)
        assert name == "spark-admin"
        return SimpleNamespace(gr_gid=900, gr_mem=list(members))

    accounts = {"chendaniely": 1000, "second-admin": 1003, "agent": 1001}
    monkeypatch.setattr(gate_main.grp, "getgrnam", getgrnam)
    monkeypatch.setattr(gate_main.pwd, "getpwnam", lambda name: SimpleNamespace(pw_uid=accounts[name]))
    monkeypatch.setattr(gate_main.pwd, "getpwall", lambda: [SimpleNamespace(pw_uid=uid, pw_gid=uid)
                                                           for uid in accounts.values()])
    now = [100.0]
    admins = gate_main.admin_uids({}, clock=lambda: now[0])
    assert admins() == {1000}
    members.append("second-admin")
    count = len(reads)
    assert admins() == {1000} and len(reads) == count  # the copy, not a new read
    now[0] += gate_main.ADMINS_TTL_S + 0.1
    assert admins() == {1000, 1003}


# The unauthorized caller's body, over a real socket with Task 9's deadline.

@LINUX_ONLY
@pytest.mark.anyio
async def test_an_unauthorized_caller_with_a_trickled_body_is_refused_at_once(tmp_path):
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        async with serving(core, admins=(), header_timeout_s=0.5) as served:  # this test's uid isn't an admin
            head = b"POST /v1/load HTTP/1.1\r\nHost: gate\r\nContent-Length: 1000\r\n\r\n"
            reader, writer = await raw(served.control, head)  # no body byte sent
            began = time.monotonic()
            status, body = await read_answer(reader)
            assert status == 403 and time.monotonic() - began < 0.3
            assert json.loads(body) == {"message": gate_app.ONLY_ADMIN, "code": None}

            async def trickle():
                with contextlib.suppress(OSError):
                    for _ in range(1000):
                        writer.write(b"x")
                        await writer.drain()
                        await asyncio.sleep(0.1)

            trickling = asyncio.create_task(trickle())
            assert await closed_within(reader, 0.5 + 0.5)
            trickling.cancel()
            writer.close()
        await core.stop()


# `spark gate`'s logging.

@pytest.fixture
def restored_logging():
    """`configure_logging` changes process-wide logging: put it back as it was."""
    root = logging.getLogger()
    named = [logging.getLogger(name) for name in ("httpx", "httpcore", "uvicorn.error")]
    before = (list(root.handlers), root.level, [(lg.level, list(lg.filters)) for lg in named])
    yield
    root.handlers[:] = before[0]
    root.setLevel(before[1])
    for lg, (level, filters) in zip(named, before[2], strict=True):
        lg.setLevel(level)
        lg.filters[:] = filters


@pytest.mark.anyio
async def test_spark_gates_logging_setup_quiets_httpx_and_httpcore(restored_logging, caplog):
    from spark.gate import main as gate_main

    gate_main.configure_logging()
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING

    async def ok(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-length", b"0")]})
        await send({"type": "http.response.body", "body": b""})

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    url = f"http://127.0.0.1:{listener.getsockname()[1]}/private-topic-standin"
    server = serve.QuietServer(uvicorn.Config(ok, log_config=None, lifespan="off", ws="none", access_log=False))
    serving_task = asyncio.create_task(server.serve(sockets=[listener]))
    caplog.set_level(logging.DEBUG)
    with anyio.fail_after(BOUND_S):
        while not server.started:
            await asyncio.sleep(0.01)
        async with httpx.AsyncClient(trust_env=False) as client:
            assert (await client.get(url)).status_code == 200
        server.should_exit = True
        await serving_task
    assert not [r for r in caplog.records if "private-topic-standin" in r.getMessage()
                or "127.0.0.1" in r.getMessage() and r.name.startswith(("httpx", "httpcore"))]


# A raising route, through run_servers with `spark gate`'s logging: its 500, and none of the request on stderr.
RAISING = textwrap.dedent(
    """
    import socket, sys
    from spark import protocols, serve
    from spark.gate import main

    main.configure_logging()

    async def app(scope, receive, send):
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        raise ValueError(f"a route broke on {body!r}, given {scope['headers']!r}")

    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(sys.argv[1])
    listener.listen()
    serve.run_servers([(app, [listener])], protocol=protocols.make_protocol(peer_cred=True, header_timeout_s=10),
                      graceful_s=0.5)
    """
)


@pytest.fixture
def sockdir():
    path = Path(tempfile.mkdtemp(prefix="gs", dir="/tmp"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


@LINUX_ONLY
def test_a_raising_route_puts_no_request_content_on_stderr(sockdir):
    # Three distinct stand-ins: the body's, a header's, and one sent as a bearer credential.
    markers = {"body": "BODY-MARKER-STANDIN", "header": "HEADER-MARKER-STANDIN", "bearer": "BEARER-MARKER-STANDIN"}
    path = sockdir / "r.sock"
    child = subprocess.Popen([sys.executable, "-c", RAISING, str(path)], stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
    try:
        for _ in range(500):
            if path.exists():
                break
            time.sleep(0.01)
        body = json.dumps({"prompt": markers["body"]}).encode()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(5)
            for _ in range(500):
                try:
                    client.connect(str(path))
                    break
                except (FileNotFoundError, ConnectionRefusedError):
                    time.sleep(0.01)
            client.sendall(b"POST /v1/chat HTTP/1.1\r\nHost: gate\r\nX-Marker: %s\r\nAuthorization: Bearer %s\r\n"
                           b"Content-Length: %d\r\nConnection: close\r\n\r\n%s"
                           % (markers["header"].encode(), markers["bearer"].encode(), len(body), body))
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
    assert "Exception in ASGI application" in err  # uvicorn's line came, through the gate's logging
    for marker in markers.values():
        assert marker not in err
    assert "Traceback" not in err and "ValueError" not in err


# `spark gate` itself, as systemd starts it.

class Ntfy:
    """ntfy as the gate's publisher meets it: every POST answered 200 and kept."""

    def __init__(self):
        self.posts: list[tuple[str, bytes]] = []

    async def __call__(self, scope, receive, send):
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        self.posts.append((scope["path"], body))
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-length", b"2")]})
        await send({"type": "http.response.body", "body": b"{}"})


@contextlib.asynccontextmanager
async def serve_ntfy(ntfy: Ntfy):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    server = serve.QuietServer(uvicorn.Config(ntfy, log_config=None, lifespan="off", ws="none", access_log=False))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    while not server.started:
        await asyncio.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{listener.getsockname()[1]}"
    finally:
        server.should_exit = True
        await task


SPARK_GATE = "import sys\nfrom spark import cli\nsys.exit(cli.main(['gate']))\n"


def _received(receiver: socket.socket, timeout: float) -> bytes:
    receiver.settimeout(timeout)
    return receiver.recv(4096)


@LINUX_ONLY
@pytest.mark.anyio
async def test_spark_gate_takes_its_two_sockets_from_systemd(tmp_path, sockdir):
    credentials = tmp_path / "credentials"
    credentials.mkdir()
    (credentials / "llamaswap-key").write_text(f"{KEY}\n")
    (credentials / "ntfy-token").write_text("Authorization: Bearer tk_standin\n")
    (credentials / "keys").write_text(EXAMPLE_KEYS.read_text())
    registry = tmp_path / "models.yaml"
    registry.write_text(STACK_REGISTRY.read_text())
    state_dir, brake, launch = tmp_path / "gate", tmp_path / "brake", tmp_path / "launch"
    for folder in (state_dir, brake, launch):
        folder.mkdir()
    fake = FakeLlamaSwap({KEY}, gate_core_models())
    for name in RESIDENTS:
        fake.set_state(name, "ready")  # the residents loaded already: nothing to preload
    ntfy = Ntfy()
    listeners = []
    for name in ("status", "control"):
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(sockdir / f"{name}.sock"))
        listener.listen()
        listeners.append(listener)
    with anyio.fail_after(60), socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as receiver:
        receiver.bind(str(sockdir / "notify"))
        async with serve_fake(fake) as swap_url, serve_ntfy(ntfy) as ntfy_url:
            env = {name: os.environ[name] for name in ("PATH", "LANG", "HOME") if name in os.environ}
            env |= {"LISTEN_FDS": "2", "LISTEN_FDNAMES": "status:control", "NOTIFY_SOCKET": str(sockdir / "notify"),
                    "CREDENTIALS_DIRECTORY": str(credentials), "NTFY_URL": ntfy_url, "NTFY_TOPIC_GATE": "gate-standin",
                    "SPARK_REGISTRY": str(registry), "SPARK_GATE_STATE": str(state_dir), "SPARK_STATE": str(brake),
                    "SPARK_LAUNCH": str(launch), "SPARK_LLAMASWAP_URL": swap_url,
                    "SPARK_FRONT_UID": str(ME), "SPARK_ADMIN_UIDS": str(ME)}
            fds = [listener.fileno() for listener in listeners]
            child = subprocess.Popen([sys.executable, "-c", LAUNCHER, ",".join(map(str, fds)), SPARK_GATE],
                                     pass_fds=fds, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for listener in listeners:
                listener.close()  # the gate's now, as systemd's would be
            try:
                assert await asyncio.to_thread(_received, receiver, 30) == b"READY=1"
                for name in ("status", "control"):
                    view = await asyncio.to_thread(GateClient(sockdir / f"{name}.sock", 5).get, "/v1/status")
                    assert view["schema"] == gateproto.STATUS_SCHEMA
                    assert [m["name"] for m in view["models"] if m["state"] == "ready"] == RESIDENTS
                child.send_signal(signal.SIGTERM)
                _, err = await asyncio.to_thread(child.communicate, timeout=30)
            finally:
                if child.poll() is None:
                    child.kill()
                    await asyncio.to_thread(child.communicate, timeout=10)
            assert child.returncode == 0, err
            assert await asyncio.to_thread(_received, receiver, 5) == b"STOPPING=1"
            assert "Traceback" not in err
    state, problem = load_state(state_dir)
    assert problem is None and state.clean_shutdown is True


@LINUX_ONLY
def test_spark_gate_refuses_to_start_without_its_sockets_or_its_key_list(tmp_path, sockdir):
    # Each startup refusal is one plain line on stderr, exit 1: no socket from systemd, no key list. It names a
    # credential or a key's name, never a key or a digest.
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(sockdir / "status.sock"))
    listener.listen()
    env = {name: os.environ[name] for name in ("PATH", "LANG", "HOME") if name in os.environ}
    one = subprocess.run([sys.executable, "-c", LAUNCHER, str(listener.fileno()), SPARK_GATE], pass_fds=[
        listener.fileno()], env=env | {"LISTEN_FDS": "1", "LISTEN_FDNAMES": "status"}, capture_output=True, text=True,
        timeout=30, check=False)
    assert one.returncode == 1 and "control" in one.stderr and "Traceback" not in one.stderr
    listener.close()
    credentials = tmp_path / "credentials"
    credentials.mkdir()
    (credentials / "llamaswap-key").write_text("KEY-STANDIN-NEVER-SHOWN\n")
    (credentials / "keys").write_text("keys:\n  dan-mac: {group: dan, label: pi on the 503}\n")
    registry = tmp_path / "models.yaml"
    registry.write_text(STACK_REGISTRY.read_text())
    listeners = []
    for name in ("status", "control"):
        each = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        each.bind(str(sockdir / f"k{name}.sock"))
        each.listen()
        listeners.append(each)
    fds = [each.fileno() for each in listeners]
    two = subprocess.run([sys.executable, "-c", LAUNCHER, ",".join(map(str, fds)), SPARK_GATE], pass_fds=fds,
                         env=env | {"LISTEN_FDS": "2", "LISTEN_FDNAMES": "status:control",
                                    "CREDENTIALS_DIRECTORY": str(credentials), "SPARK_REGISTRY": str(registry),
                                    "SPARK_FRONT_UID": str(ME), "SPARK_ADMIN_UIDS": str(ME)},
                         capture_output=True, text=True, timeout=30, check=False)
    for each in listeners:
        each.close()
    assert two.returncode == 1 and "Traceback" not in two.stderr
    assert "dan-mac" in two.stderr and "pi's retry list" in two.stderr
    assert "KEY-STANDIN-NEVER-SHOWN" not in two.stderr

"""Task 30's commands (website/design/phase-2a.md, Task 30): `spark load`, `unload`, `pin`, `unpin`, `make-room`,
`logs` and `session`, each on the gate's control socket or, for the sessions, its status socket, printing Task 7's words
for what the gate did, or the refusal's message. The gate here is a stand-in, the standard library's (http.server's
handler on socketserver's Unix-socket server), on short AF_UNIX paths: it answers each route as Task 10 gives it, as
the test scripts it, and checks each body as the gate's own routes check it (gate.app's helpers), so a shape the CLI
drifts from shows. Nothing touches the box's own sockets."""

import io
import json
import os
import re
import select
import shutil
import signal
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import pytest

from spark import cli, commands, gateclient, gateproto, messages, paths
from spark.budget import Candidate, make_room_plan
from spark.gate import app as gate_app
from spark.gateclient import GateClient
from spark.registry import load_registry

ROOT = Path(__file__).resolve().parents[2]
STACK = ROOT / "stack" / "models.yaml"
PLAN = ROOT / "website" / "design" / "plan.md"
CODER, GEMMA, EMBED, WHISPER = "qwen3.8-27b", "gemma-4-26b-a4b", "qwen3-embedding-0.6b", "whisper-large-v3-turbo"
NOW = datetime(2026, 10, 14, 10, 0).timestamp()  # when the commands run: 10:00, local time
AT_18 = datetime(2026, 10, 14, 18, 0).timestamp()
DOCTOR = "on the Spark, `make doctor` shows what's wrong."
MARKER = "CREDENTIAL-MARKER"  # stands for a key, wherever one might travel


def registry_text() -> str:
    """The stack's registry, whose coder is the plan's Qwen3.8-27B (its first role `coder`). Until Qwen3.6 came back
    beside it (2026-10-16), this renamed Qwen3.6's entry to the coder's, a no-op since the swap reached this branch."""
    return STACK.read_text()


# The gate's stand-in.

@dataclass
class Seen:
    socket: str  # which of the gate's sockets it came to: control or status
    method: str
    path: str  # as sent, quoted
    query: str
    headers: dict[str, str]
    body: Any
    raw: bytes


def _pattern(template: str) -> re.Pattern:
    """A route's path, its {id} and {model} parameters each one segment: /v1/sessions/{id}/renew."""
    return re.compile("^" + "[^/]+".join(re.escape(part) for part in re.split(r"\{[a-z]+\}", template)) + "$")


ROUTES = [(route, _pattern(route.path)) for route in gateproto.ROUTES]


def _route(socket_name: str, method: str, path: str) -> gateproto.Route | None:
    return next((route for route, pattern in ROUTES
                 if route.socket == socket_name and route.method == method and pattern.match(path)), None)


def _problem(route: gateproto.Route, body: Any, query: str) -> str | None:
    """What the gate's route would refuse in this body with a 400 (gate.app's _Routes), or None."""
    key = (route.socket, route.method, route.path)
    try:
        if route.method == "POST" and not isinstance(body, dict):
            raise gate_app._Bad(gate_app.NOT_AN_OBJECT)
        if key in (("control", "POST", "/v1/load"), ("control", "POST", "/v1/unload")):
            gate_app._text(body, "model")
        elif key == ("control", "POST", "/v1/pin"):
            gate_app._text(body, "model")
            gate_app._number(body, "until", none=True)
        elif key == ("control", "POST", "/v1/make-room/plan"):
            if not (set(body) == {"all"} and body["all"] is True) and not (
                    set(body) == {"size_gib"} and gate_app._number(body, "size_gib") > 0):
                raise gate_app._Bad("make-room's request is {size_gib: <GiB>} or {all: true}.")
        elif key == ("control", "POST", "/v1/make-room"):
            gate_app._text(body, "plan_id")
            gate_app._number(body, "for_s", none=True)
        elif key == ("control", "POST", "/v1/release"):
            # A flag left out is false, and one of them must be true (Task 30's review).
            if not (gate_app._flag(body, "room", absent=False) | gate_app._flag(body, "brake", absent=False)):
                raise gate_app._Bad(gate_app.NOTHING_TO_RELEASE)
        elif key == ("status", "POST", "/v1/sessions"):
            if isinstance(body.get("pid"), bool) or not isinstance(body.get("pid"), int):
                raise gate_app._Bad(gate_app._bad("pid", "a process id"))
            gate_app._text(body, "label", line=False, most=gate_app.TEXT_MAX)
            gate_app._text(body, "model")
        elif key == ("control", "GET", "/v1/logs/{model}"):
            given = parse_qs(query, keep_blank_values=True).get("n", ["20"])
            if len(given) != 1 or not given[0].isdigit() or not 1 <= int(given[0]) <= gate_app.LOGS_MAX:
                raise gate_app._Bad(gate_app._bad("n", f"a whole number from 1 to {gate_app.LOGS_MAX}"))
    except gate_app._Bad as bad:
        return str(bad)
    return None


class _Handler(BaseHTTPRequestHandler):
    """The gate's stand-in. It answers HTTP/1.1, as uvicorn does, so its streams are chunked."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # a Unix client's address is '', which the default line can't print
        pass

    def _any(self):
        gate = self.server.gate
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            body = None
        path, _, query = self.path.partition("?")
        seen = Seen(self.server.name, self.command, path, query, {k.lower(): v for k, v in self.headers.items()},
                    body, raw)
        gate.seen.append(seen)
        route = _route(self.server.name, self.command, path)
        if route is None:
            return self.answer(404, {"message": gate_app.NO_SUCH_ROUTE, "code": None})
        problem = _problem(route, body, query)
        if problem is not None:
            gate.bad.append((route.path, problem))
            return self.answer(400, {"message": problem, "code": None})
        if (route.socket, route.path) == ("status", "/v1/sessions") and gate.registry is not None:
            # The gate's model check (gate.app's session_begin, Sessions.register): the name first, then a role.
            model = gate_app._resolve(gate.registry, body["model"])
            if model not in gate.registry.models:
                words = f"There's no model {body['model']!r} in the registry, so no session can keep it loaded."
                return self.answer(409, {"message": words, "code": None})
            gate.session_models.append(model)
        script = gate.script.get((self.command, route.path))
        if script is None:
            return self.answer(500, {"message": f"The test gave no answer for {self.command} {route.path}.",
                                     "code": None})
        script(self, seen)

    do_GET = do_POST = do_DELETE = _any

    def answer(self, status: int, payload: dict) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def stream(self, items: tuple, cut: bool) -> None:
        """NDJSON, chunked: each dict a line, each number a pause of that many seconds, each Event a wait until the test
        sets it; then the last chunk, or, `cut`, the connection closed without it, as by a gate that died."""
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        try:
            for item in items:
                if isinstance(item, dict):
                    data = json.dumps(item).encode() + b"\n"
                    self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
                elif isinstance(item, threading.Event):
                    item.wait(30)
                else:
                    time.sleep(item)
            if cut:
                self.close_connection = True
            else:
                self.wfile.write(b"0\r\n\r\n")
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True


def answer(payload: dict, status: int = 200):
    return lambda handler, seen: handler.answer(status, payload)


def stream(*items, cut: bool = False):
    return lambda handler, seen: handler.stream(items, cut)


class _Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        """Kept, never printed: socketserver prints to sys.stderr, which is the test's captured stderr. A client that
        closes its connection once it has its result line, as each command does, leaves the handler a reset to meet;
        anything else is the stand-in's own fault, which the box fails on."""
        self.gate.errors.append(sys.exc_info()[1])


class GateStandIn:
    """Both of the gate's sockets in `folder`: `script[(method, route)]` answers a route, `seen` keeps every request,
    `bad` every body the gate would have refused, `errors` what broke in the stand-in's own handlers. With `registry`,
    a session's model is checked against it as the gate checks it, and `session_models` keeps each, resolved."""

    def __init__(self, folder: Path, registry: Path | None = None):
        self.seen: list[Seen] = []
        self.bad: list[tuple[str, str]] = []
        self.errors: list[BaseException] = []
        self.registry = None if registry is None else load_registry(registry)  # for the sessions' model check
        self.session_models: list[str] = []  # each session's model, as the gate resolved it
        self.script: dict[tuple[str, str], Any] = {}
        self.servers = []
        for name in ("control", "status"):
            path = folder / f"{name}.sock"
            server = _Server(str(path), _Handler)
            server.name, server.gate, server.path = name, self, path
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
            thread.start()
            self.servers.append((server, thread))

    def calls(self) -> list[tuple[str, str, str]]:
        return [(s.socket, s.method, s.path + (f"?{s.query}" if s.query else "")) for s in self.seen]

    def bodies(self, path: str) -> list[Any]:
        return [s.body for s in self.seen if s.path == path]

    def close(self) -> None:
        for server, thread in self.servers:
            server.shutdown()
            server.server_close()
            thread.join(5)
            server.path.unlink(missing_ok=True)


@contextmanager
def serving(folder: Path, registry: Path | None = None):
    gate = GateStandIn(folder, registry)
    try:
        yield gate
    finally:
        gate.close()


class _Stdin(io.StringIO):
    """A stdin with what the test types, a terminal or not."""

    def __init__(self, text: str, tty: bool):
        super().__init__(text)
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


@pytest.fixture
def box(monkeypatch, capsys):
    """`spark <command>` against the stand-in: the gate's two sockets in a short folder, the stack's registry with the
    plan's coder, a login in spark-admin (Dan's words), the clock at 10:00, and every GateClient made kept, with its
    socket, its timeout and each stream's `then_s`. `box(*argv, stdin=, tty=)` gives (exit code, stdout, stderr)."""
    folder = Path(tempfile.mkdtemp(prefix="cm", dir="/tmp"))
    registry = folder / "models.yaml"
    registry.write_text(registry_text())
    monkeypatch.setattr(paths, "GATE_CONTROL_SOCKET", folder / "control.sock")
    monkeypatch.setattr(paths, "GATE_STATUS_SOCKET", folder / "status.sock")
    monkeypatch.setattr(paths, "REGISTRY", registry)
    monkeypatch.setattr(gateclient, "_words", lambda: "dan")
    monkeypatch.setattr(commands, "_now", lambda: NOW)
    made: list[dict] = []

    class Client(GateClient):
        def __init__(self, path, timeout_s):
            self.record = {"path": Path(path), "timeout_s": timeout_s, "streams": []}
            made.append(self.record)
            super().__init__(path, timeout_s)

        def stream(self, route, body=None, *, then_s=...):
            self.record["streams"].append((route, then_s))
            return super().stream(route, body, then_s=then_s)

    monkeypatch.setattr(commands, "GateClient", Client)
    gate = GateStandIn(folder, registry)

    def run(*argv, stdin: Any = "", tty: bool = False):
        monkeypatch.setattr(sys, "stdin", _Stdin(stdin, tty) if isinstance(stdin, str) else stdin)
        code = cli.main(list(argv))
        out, err = capsys.readouterr()
        return code, out, err

    run.gate, run.made, run.folder, run.registry = gate, made, folder, registry
    yield run
    gate.close()
    shutil.rmtree(folder, ignore_errors=True)
    assert [err for err in gate.errors if not isinstance(err, ConnectionError)] == []


def loading(last_s=24, label="the coder", model=CODER) -> dict:
    return {"kind": "loading", "model": model, "label": label, "last_s": last_s}


def loaded(seconds=24, idle_min=60) -> dict:
    return {"label": "the coder", "seconds": seconds, "idle_min": idle_min, "command": "coder"}


def no_fit_line() -> dict:
    """The coder's no_fit for Dan's own command, as a refusal line (GateRefusalBody)."""
    text = messages.refusal("no_fit", messages.Moment(
        needed_gib=41, free_gib=18, available_gib=48, reserve_gib=24, owed_gib=6, model_label="the coder",
        model_command="coder", key_label="you", words="dan", names_processes=True, wait_s=200)).message
    return {"message": text, "code": "no_fit"}


# The brief's tests.

def test_load_says_loaded_in_n_seconds_and_how_to_keep_it(box):
    box.gate.script[("POST", "/v1/load")] = stream(loading(), loaded())
    code, out, err = box("load", "coder")
    assert (code, err) == (0, "")
    assert out == ("Loading the coder (24 s last time)…\n"
                   "Loaded the coder in 24 s. It unloads after 60 min idle; `spark pin coder` keeps it.\n")
    assert box.gate.calls() == [("control", "POST", "/v1/load")]
    assert box.gate.bodies("/v1/load") == [{"model": "coder"}]  # the role as given: the gate resolves it
    # A refusal line ends it: its message, exit 1, after the lines before it.
    refusal = no_fit_line()
    box.gate.script[("POST", "/v1/load")] = stream(loading(), refusal)
    code, out, err = box("load", "coder")
    assert code == 1
    assert out == "Loading the coder (24 s last time)…\n"
    assert err == f"spark load: {refusal['message']}\n"
    # And a refusal before anything streams, as the gate's 403: the same.
    box.gate.script[("POST", "/v1/load")] = answer({"message": gate_app.ONLY_ADMIN, "code": None}, 403)
    assert box("load", "coder") == (1, "", f"spark load: {gate_app.ONLY_ADMIN}\n")
    assert box.gate.bad == []


def test_a_stream_that_ends_without_its_result_says_the_gate_stopped(box):
    box.gate.script[("POST", "/v1/unload")] = stream({"kind": "unloading", "inflight": 1})
    code, out, err = box("unload", "coder")
    assert code == 1
    assert out == "Unloading the coder once its 1 request in flight finishes…\n"
    assert err == f"spark unload: The gate stopped partway through unloading the coder; {DOCTOR}\n"
    assert "Unloaded" not in out + err
    # So does a load's, and a pin's, and make-room's, each named.
    box.gate.script[("POST", "/v1/load")] = stream(loading())
    assert box("load", "coder")[2] == f"spark load: The gate stopped partway through loading the coder; {DOCTOR}\n"
    box.gate.script[("POST", "/v1/pin")] = stream(loading())
    assert box("pin", "coder")[2] == f"spark pin: The gate stopped partway through pinning the coder; {DOCTOR}\n"
    # For agent, the step is Dan's (never a command agent can't run, told to agent).
    gateclient_words = gateclient._words
    try:
        gateclient._words = lambda: "agent"
        assert box("unload", "coder")[2] == ("spark unload: The gate stopped partway through unloading the coder; "
                                             "`make doctor` on the Spark shows Dan what's wrong.\n")
    finally:
        gateclient._words = gateclient_words


def test_unload_waits_for_requests_in_flight_and_says_so(box):
    box.gate.script[("POST", "/v1/unload")] = stream({"kind": "unloading", "inflight": 1},
                                                     {"unloaded": True, "pin_ended": False})
    code, out, err = box("unload", "coder")
    assert (code, err) == (0, "")
    assert out == "Unloading the coder once its 1 request in flight finishes…\nUnloaded the coder.\n"
    assert box.gate.bodies("/v1/unload") == [{"model": "coder"}]
    assert box.gate.calls() == [("control", "POST", "/v1/unload")]


def test_unload_says_so_while_the_model_stays_stopping(box):
    stopping = {"kind": "stopping", "model": CODER, "label": "the coder"}
    box.gate.script[("POST", "/v1/unload")] = stream({"kind": "unloading", "inflight": 0}, stopping, stopping,
                                                     {"unloaded": True, "pin_ended": False})
    code, out, err = box("unload", "coder")
    assert (code, err) == (0, "")
    assert out.splitlines() == ["Unloading the coder…",
                                "Still stopping the coder: llama-swap hasn't finished its unload…",
                                "Still stopping the coder: llama-swap hasn't finished its unload…",
                                "Unloaded the coder."]


def test_unload_says_it_waits_for_a_load_to_finish_and_when_it_ended_a_pin(box):
    # The after_load line (gateproto.AfterLoadProgress), and UnloadDone's pin_ended (Task 17's review).
    box.gate.script[("POST", "/v1/unload")] = stream(
        {"kind": "unloading", "inflight": 0}, {"kind": "after_load", "model": CODER, "label": "the coder"},
        {"unloaded": True, "pin_ended": True})
    code, out, err = box("unload", "coder")
    assert (code, err) == (0, "")
    assert out.splitlines() == ["Unloading the coder…",
                                "Waiting for the coder to finish loading before unloading it…",
                                "Unloaded the coder; the pin on it ended."]


@pytest.mark.parametrize("refusal", [
    messages.front_not_connected("the coder"), messages.state_not_saved("the coder"),
    messages.gate_stopping("the coder"), messages.not_unloaded("held", "the coder"),
], ids=["front_not_connected", "state_not_saved", "gate_stopping", "held"])
def test_unload_that_couldnt_drain_says_why_as_any_refusal(box, refusal):
    # The front not connected (Task 16's ruling), the state not saved and the gate stopping (Task 16's review), and a
    # drain that went back to serving: each a refusal line after the count, its sentence, exit 1.
    box.gate.script[("POST", "/v1/unload")] = stream({"kind": "unloading", "inflight": 1},
                                                     {"message": refusal, "code": None})
    code, out, err = box("unload", "coder")
    assert code == 1
    assert out == "Unloading the coder once its 1 request in flight finishes…\n"
    assert err == f"spark unload: {refusal}\n"
    assert "Unloaded the coder" not in out + err


def test_unload_of_a_model_not_loaded_says_so(box):
    box.gate.script[("POST", "/v1/unload")] = stream({"message": messages.not_loaded("the coder"), "code": None})
    assert box("unload", "coder") == (
        1, "", "spark unload: The coder isn't loaded, so there's nothing to unload.\n")


def test_pin_with_a_duration_says_until_when_and_how_to_end_it(box):
    box.gate.script[("POST", "/v1/pin")] = stream(
        loading(), {"label": "the coder", "until": AT_18, "loaded_s": 24, "command": "coder"})
    code, out, err = box("pin", "coder", "8h")
    assert (code, err) == (0, "")
    assert box.gate.bodies("/v1/pin") == [{"model": "coder", "until": AT_18}]  # 10:00 and 8 h: 18:00
    assert out == ("Loading the coder (24 s last time)…\n"
                   "The coder stays loaded until 18:00 (loaded it first, 24 s). `spark unpin coder` ends the pin.\n")
    # With no duration, no end.
    box.gate.script[("POST", "/v1/pin")] = answer(
        {"label": "the coder", "until": None, "loaded_s": None, "command": "coder"})
    assert box("pin", "coder") == (0, "The coder stays loaded, with no end set. `spark unpin coder` ends the pin.\n",
                                   "")
    assert box.gate.bodies("/v1/pin")[-1] == {"model": "coder", "until": None}


def test_a_pin_on_a_model_already_loaded_says_only_the_pin(box, monkeypatch):
    # The gate answers once (Content-Length, not a stream), which stream() reads as its one line.
    box.gate.script[("POST", "/v1/pin")] = answer(
        {"label": "the coder", "until": AT_18, "loaded_s": None, "command": "coder"})
    code, out, err = box("pin", "coder", "8h")
    assert (code, out, err) == (0, "The coder stays loaded until 18:00. `spark unpin coder` ends the pin.\n", "")
    assert "Loading" not in out
    # The gate down (systemd holds its socket, nothing answers): GateUnavailable's sentence within the request
    # timeout, 5 s (scaled down here), exit 1.
    box.gate.close()
    held = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    held.bind(str(paths.GATE_CONTROL_SOCKET))
    held.listen(8)
    try:
        assert commands.REQUEST_TIMEOUT_S == gateproto.REQUEST_TIMEOUT_S == 5
        monkeypatch.setattr(commands, "REQUEST_TIMEOUT_S", 0.5)
        began = time.monotonic()
        code, out, err = box("pin", "coder", "8h")
        assert time.monotonic() - began < 3
        assert (code, out) == (1, "")
        assert err == (f"spark pin: The gate didn't answer on {paths.GATE_CONTROL_SOCKET} within 0.5 s. On the Spark, "
                       "`make doctor` shows what's wrong.\n")
    finally:
        held.close()


def test_unpin_says_when_it_unloads(box):
    box.gate.script[("DELETE", "/v1/pin/{model}")] = answer({"label": "the coder", "idle_min": 60, "command": "coder"})
    assert box("unpin", "coder") == (0, "The pin on the coder ended; it unloads after 60 min idle.\n", "")
    assert box.gate.calls() == [("control", "DELETE", "/v1/pin/coder")]
    box.gate.script[("DELETE", "/v1/pin/{model}")] = answer(
        {"message": "The coder isn't pinned, so there is no pin to end.", "code": None}, 409)
    assert box("unpin", "coder") == (1, "", "spark unpin: The coder isn't pinned, so there is no pin to end.\n")


# make-room: the plan's example, the residents and the coder loaded, 9 GiB free for a load.

def candidates() -> list[Candidate]:
    """The plan's example's candidates, as Room.plan makes them: the coder idle 12 min, the residents idle too."""
    return [Candidate(CODER, "the coder", 41, False, False, None, 0, None, 12),
            Candidate(GEMMA, "Gemma", 32, True, False, None, 0, None, 1.5),
            Candidate(EMBED, "the embeddings", 8, True, False, None, 0, None, 30),
            Candidate(WHISPER, "whisper", 3, True, False, None, 0, None, 5)]


def plan_answer(target, free_now: float, cands, plan_id="p-1") -> dict:
    """/v1/make-room/plan's answer as the gate makes it: Task 5's plan, as JSON (gate.app's room_plan), from the
    gate's own figure for free for a load."""
    plan = make_room_plan(target, Decimal(repr(free_now)), cands)
    return {"plan_id": plan_id, "plan": json.loads(json.dumps(asdict(plan), default=gate_app._plain))}


def plan_block() -> list[str]:
    """plan.md's make-room list, the block under *make-room's list, with the residents and the coder loaded*."""
    after = PLAN.read_text().split("make-room's list, with the residents and the coder loaded", 1)[1]
    return after.split("```\n", 1)[1].split("\n```", 1)[0].splitlines()


def room_result(unloaded, free, hold, until=None, ended=()) -> dict:
    return {"unloaded": list(unloaded), "free_gib": free, "hold_gib": hold, "until": until, "ended": list(ended)}


def progress(model, label, inflight=0) -> dict:
    return {"kind": "unloading", "model": model, "label": label, "inflight": inflight}


HELD_40 = ("Unloaded the coder. 50 GiB is free for a load, and 40 GiB of it is held for you until `spark make-room "
           "--done` or a reboot; your own requests can load into it, agent's and automatic reloads can't.")


def test_make_room_shows_the_list_and_asks_once(box):
    box.gate.script[("POST", "/v1/make-room/plan")] = answer(plan_answer(Decimal(40), 9, candidates()))
    box.gate.script[("POST", "/v1/make-room")] = stream(progress(CODER, "the coder"),
                                                        room_result(["the coder"], 50.0, 40.0))
    code, out, err = box("make-room", "40G", stdin="y\n", tty=True)
    assert (code, err) == (0, "")
    block = plan_block()
    *rows, question = block
    assert question == "Unloading 1 leaves 50 GiB free for a load. Unload 1? [y/N]"
    assert out == "\n".join(rows) + "\n" + question + " " + "Unloading the coder…\n" + HELD_40 + "\n"
    assert out.count("[y/N]") == 1
    assert box.gate.bodies("/v1/make-room/plan") == [{"size_gib": 40.0}]  # parse_size's Decimal, as a float
    assert box.gate.bodies("/v1/make-room") == [{"plan_id": "p-1", "for_s": None}]
    assert box.gate.bad == []
    # Anything else: nothing posted, exit 1.
    for reply in ("n\n", "\n", "", "maybe\n"):
        box.gate.seen.clear()
        code, out, err = box("make-room", "40G", stdin=reply, tty=True)
        assert code == 1, reply
        assert out.endswith("Nothing unloaded, and nothing held.\n"), (reply, out)
        assert [call[2] for call in box.gate.calls()] == ["/v1/make-room/plan"], reply


def test_make_room_asked_too_much_offers_the_most(box):
    # 70 GiB with the 32 GiB python job running: unloading everything leaves 61 (plan.md's command table).
    box.gate.script[("POST", "/v1/make-room/plan")] = answer(plan_answer(Decimal(70), -23, candidates()))
    box.gate.script[("POST", "/v1/make-room")] = stream(
        *(progress(c.model, c.label) for c in candidates()),
        room_result(["the coder", "Gemma", "the embeddings", "whisper"], 61.0, 61.0))
    code, out, err = box("make-room", "70G", stdin="y\n", tty=True)
    assert (code, err) == (0, "")
    lines = out.split("\n")
    assert lines[0] == "To make 70 GiB free for a load (nothing now):"
    assert lines[4].startswith("  4  whisper")
    assert lines[5].startswith("Unloading everything leaves 61 GiB free for a load, not 70. Free 61 and hold it? "
                               "[y/N] Unloading the coder…")
    assert out.count("[y/N]") == 1
    assert lines[-2].startswith("Unloaded the coder, Gemma, the embeddings and whisper. 61 GiB is free for a load, "
                                "and 61 GiB of it is held for you")
    # Nothing at all to free: said, and nothing asked.
    box.gate.script[("POST", "/v1/make-room/plan")] = answer(plan_answer(Decimal(70), -90, candidates()))
    box.gate.seen.clear()
    code, out, err = box("make-room", "70G", stdin="y\n", tty=True)
    assert code == 1 and "[y/N]" not in out
    assert out.endswith("Unloading everything leaves nothing free for a load, not 70.\n")
    assert [call[2] for call in box.gate.calls()] == ["/v1/make-room/plan"]


def test_make_room_for_sets_its_end(box):
    box.gate.script[("POST", "/v1/make-room/plan")] = answer(plan_answer(Decimal(40), 9, candidates()))
    box.gate.script[("POST", "/v1/make-room")] = stream(progress(CODER, "the coder"),
                                                        room_result(["the coder"], 50.0, 40.0, AT_18))
    code, out, err = box("make-room", "40G", "--for", "8h", stdin="y\n", tty=True)
    assert (code, err) == (0, "")
    assert box.gate.bodies("/v1/make-room") == [{"plan_id": "p-1", "for_s": 28800}]
    assert out.endswith("Unloaded the coder. 50 GiB is free for a load, and 40 GiB of it is held for you until 18:00, "
                        "`spark make-room --done` or a reboot; your own requests can load into it, agent's and "
                        "automatic reloads can't.\n")
    # --for goes with a size only: room_all's words have no end to give.
    with pytest.raises(SystemExit) as exc:
        box("make-room", "--all", "--for", "8h")
    assert exc.value.code == 2


def test_make_room_all_and_done(box):
    from spark.budget import ALL

    box.gate.script[("POST", "/v1/make-room/plan")] = answer(plan_answer(ALL, 9, candidates()))
    box.gate.script[("POST", "/v1/make-room")] = stream(
        *(progress(c.model, c.label) for c in candidates()),
        room_result(["the coder", "Gemma", "the embeddings", "whisper"], 93.0, 93.0))
    code, out, err = box("make-room", "--all", stdin="yes\n", tty=True)
    assert (code, err) == (0, "")
    assert box.gate.bodies("/v1/make-room/plan") == [{"all": True}]
    lines = out.split("\n")
    assert lines[0] == "To unload everything (9 GiB free for a load now):"
    assert lines[5] == ("Unloading 1, 2, 3 and 4 leaves 93 GiB free for a load. Unload 1, 2, 3 and 4? [y/N] Unloading "
                        "the coder…")
    assert lines[6:9] == ["Unloading Gemma…", "Unloading the embeddings…", "Unloading whisper…"]
    assert lines[9] == ("Unloaded everything. The whole box is held for you until `spark make-room --done` or a "
                        "reboot; your own requests can load into it.")
    assert out.count("[y/N]") == 1
    # --done: POST /v1/release asking the room only (never the brake's pause), worded from its answer's room.
    box.gate.script[("POST", "/v1/release")] = answer(
        {"room": {"unused_gib": 40.0, "total_gib": 40.0, "next_label": "the coder"}, "brake": False, "reloading": []})
    box.gate.seen.clear()
    assert box("make-room", "--done") == (
        0, "Hold ended, all 40 GiB of it unused. Nothing to reload: the coder loads on its next request.\n", "")
    assert box.gate.calls() == [("control", "POST", "/v1/release")]
    assert box.gate.bodies("/v1/release") == [{"room": True, "brake": False}]
    box.gate.script[("POST", "/v1/release")] = answer(
        {"room": {"unused_gib": 9.0, "total_gib": 41.0, "next_label": None}, "brake": False, "reloading": ["Gemma"]})
    assert box("make-room", "--done")[1] == "Hold ended, 9 of its 41 GiB unused. Reloading Gemma.\n"
    # With no hold standing, it says so.
    box.gate.script[("POST", "/v1/release")] = answer({"room": None, "brake": False, "reloading": []})
    assert box("make-room", "--done") == (0, "There's no make-room hold, so there's nothing to end.\n", "")
    assert box.gate.bad == []


def test_make_room_with_nothing_loaded_stops_there(box):
    # The deferred note's: after *Nothing is loaded, so there is nothing to unload.*, the command ends, with no
    # room_too_much question, no room_all confirmation, and no POST /v1/make-room.
    from spark.budget import ALL

    for argv, target in ((("make-room", "100G"), Decimal(100)), (("make-room", "--all"), ALL)):
        box.gate.seen.clear()
        box.gate.script[("POST", "/v1/make-room/plan")] = answer(plan_answer(target, 93, []))
        code, out, err = box(*argv, stdin="y\n", tty=True)
        assert (code, out, err) == (1, "Nothing is loaded, so there is nothing to unload.\n", ""), argv
        assert [call[2] for call in box.gate.calls()] == ["/v1/make-room/plan"], argv


def test_make_room_says_the_pins_and_sessions_it_ended(box):
    box.gate.script[("POST", "/v1/make-room/plan")] = answer(plan_answer(Decimal(40), 9, candidates()))
    box.gate.script[("POST", "/v1/make-room")] = stream(
        progress(CODER, "the coder"),
        room_result(["the coder"], 50.0, 40.0, ended=["your pin on the coder", "the session agent's pi"]))
    code, out, err = box("make-room", "40G", stdin="y\n", tty=True)
    assert (code, err) == (0, "")
    assert out.endswith(f"{HELD_40}\nYour pin on the coder ended, and the session agent's pi lost its model.\n")


def test_make_room_refusals_print_as_any_refusal(box):
    box.gate.script[("POST", "/v1/make-room/plan")] = answer(plan_answer(Decimal(70), 9, candidates()))
    # A plan the gate no longer has.
    box.gate.script[("POST", "/v1/make-room")] = stream({"message": messages.room_plan_gone(), "code": None})
    code, out, err = box("make-room", "70G", stdin="y\n", tty=True)
    assert code == 1
    assert err == ("spark make-room: That make-room plan isn't one the gate has: it was used already, or the gate "
                   "restarted since. Run `spark make-room` again.\n")
    # A drain refused part-way: what it unloaded, and that the room stays held.
    partly = messages.room_partly_held(["the coder"], messages.front_not_connected("Gemma"), None)
    box.gate.script[("POST", "/v1/make-room")] = stream(progress(CODER, "the coder"), progress(GEMMA, "Gemma", 1),
                                                        {"message": partly, "code": None})
    code, out, err = box("make-room", "70G", stdin="y\n", tty=True)
    assert code == 1
    assert out.endswith("Unloading the coder…\nUnloading Gemma once its 1 request in flight finishes…\n")
    assert err == f"spark make-room: {partly}\n"


def test_a_cut_after_the_result_is_never_shown_as_an_error(box):
    box.gate.script[("POST", "/v1/unload")] = stream({"kind": "unloading", "inflight": 1}, {"unloaded": True},
                                                     cut=True)
    code, out, err = box("unload", "coder")
    assert (code, err) == (0, "")
    assert out == "Unloading the coder once its 1 request in flight finishes…\nUnloaded the coder.\n"


def test_a_queued_load_says_what_it_waits_for(box):
    wait = {"kind": "waiting", "model": CODER, "label": "the coder", "needed_gib": None, "free_gib": None,
            "loading_label": None, "release_waits_for_dan": None, "release_after_s": None}
    box.gate.script[("POST", "/v1/load")] = stream(
        wait | {"why": "slot", "loading_label": "Gemma"},
        wait | {"why": "memory", "needed_gib": 41.0, "free_gib": 18.0},
        loading(), loaded())
    code, out, err = box("load", "coder")
    assert (code, err) == (0, "")
    assert out.splitlines() == [
        "The coder is waiting its turn to load: Gemma is loading, and one model loads at a time…",
        "The coder is waiting for memory: it needs 41 GiB, and 18 GiB is free for a load…",
        "Loading the coder (24 s last time)…",
        "Loaded the coder in 24 s. It unloads after 60 min idle; `spark pin coder` keeps it."]
    # A pin that loads says the same.
    box.gate.script[("POST", "/v1/pin")] = stream(
        wait | {"why": "brake", "release_waits_for_dan": True}, loading(),
        {"label": "the coder", "until": None, "loaded_s": 24, "command": "coder"})
    code, out, err = box("pin", "coder")
    assert out.splitlines() == [
        "The coder is waiting while new loads are paused. They stay paused until you release them: on the Spark, "
        "`make brake-release`.",
        "Loading the coder (24 s last time)…",
        "The coder stays loaded, with no end set (loaded it first, 24 s). `spark unpin coder` ends the pin."]


def test_a_question_without_a_terminal_refuses_without_yes(box):
    box.gate.script[("POST", "/v1/make-room/plan")] = answer(plan_answer(Decimal(40), 9, candidates()))
    box.gate.script[("POST", "/v1/make-room")] = stream(progress(CODER, "the coder"),
                                                        room_result(["the coder"], 50.0, 40.0))
    code, out, err = box("make-room", "40G", stdin="y\n", tty=False)
    assert code == 2
    assert err == ("spark make-room asks before it unloads or holds anything, and there's no terminal here to ask on: "
                   "run it in a terminal, or add `--yes`, which answers yes.\n")
    assert "/v1/make-room" not in [call[2] for call in box.gate.calls()]
    # --yes answers yes: the question shows with its answer.
    box.gate.seen.clear()
    code, out, err = box("make-room", "40G", "--yes", stdin="", tty=False)
    assert (code, err) == (0, "")
    assert "Unload 1? [y/N] y\nUnloading the coder…\n" in out and out.endswith(HELD_40 + "\n")
    assert box.gate.bodies("/v1/make-room") == [{"plan_id": "p-1", "for_s": None}]


def test_logs_reads_through_the_control_socket(box):
    box.gate.script[("GET", "/v1/logs/{model}")] = answer(
        {"lines": ["srv  load_model: loading", "a \x1b[31mred\x1b[0m line", "the last"]})
    code, out, err = box("logs", "coder", "-n", "5")
    assert (code, err) == (0, "")
    assert box.gate.calls() == [("control", "GET", "/v1/logs/coder?n=5")]
    assert out == "srv  load_model: loading\na \\x1b[31mred\\x1b[0m line\nthe last\n"  # escaped, never acted on
    box.gate.script[("GET", "/v1/logs/{model}")] = answer(
        {"message": messages.no_such_model("nope", "read"), "code": None}, 409)
    assert box("logs", "nope") == (1, "", "spark logs: There's no model 'nope' in the registry, so there's nothing to "
                                          "read.\n")
    assert box.gate.calls()[-1] == ("control", "GET", "/v1/logs/nope?n=20")


def test_session_commands_use_the_status_socket(box):
    box.gate.script[("POST", "/v1/sessions")] = answer({"id": "s-1", "expires_at": NOW + 43200})
    box.gate.script[("POST", "/v1/sessions/{id}/renew")] = answer({"id": "s-1", "expires_at": NOW + 43200})
    box.gate.script[("DELETE", "/v1/sessions/{id}")] = answer({})
    assert box("session", "start", "--model", "coder", "--pid", "4242", "--label", "agent's pi") == (0, "s-1\n", "")
    assert box.gate.bodies("/v1/sessions") == [{"model": "coder", "pid": 4242, "label": "agent's pi"}]
    assert box.gate.session_models == [CODER]  # the role, resolved as the gate resolves it (Task 30's review, I1)
    assert box("session", "renew", "s-1") == (0, "", "")
    assert box("session", "end", "s-1") == (0, "", "")
    assert box.gate.calls() == [("status", "POST", "/v1/sessions"), ("status", "POST", "/v1/sessions/s-1/renew"),
                                ("status", "DELETE", "/v1/sessions/s-1")]
    assert all(record["path"] == paths.GATE_STATUS_SOCKET for record in box.made)
    box.gate.script[("POST", "/v1/sessions/{id}/renew")] = answer(
        {"message": "Session s-9 has ended: its process has exited, or it went 12 hours without a renewal.",
         "code": None}, 404)
    assert box("session", "renew", "s-9") == (
        1, "", "spark session: Session s-9 has ended: its process has exited, or it went 12 hours without a renewal.\n")
    # A name that is neither a model's nor a role: the gate's words, quoting it as given.
    assert box("session", "start", "--model", "nope", "--pid", "4242", "--label", "x") == (
        1, "", "spark session: There's no model 'nope' in the registry, so no session can keep it loaded.\n")
    assert box.gate.bad == []


def _cli_env(folder: Path, registry: Path) -> dict[str, str]:
    return {"PATH": os.environ["PATH"], "HOME": str(folder), "PYTHONIOENCODING": "utf-8",
            "SPARK_GATE_CONTROL": str(folder / "control.sock"), "SPARK_GATE_STATUS": str(folder / "status.sock"),
            "SPARK_REGISTRY": str(registry), **({"TZ": os.environ["TZ"]} if "TZ" in os.environ else {})}


HOLD = """
import sys
from spark import cli, commands
commands.SESSION_HOLD_RENEW_S = float(sys.argv[1])
sys.exit(cli.main(sys.argv[2:]))
"""


def _until(condition, within_s: float = 15.0) -> bool:
    end = time.monotonic() + within_s
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.02)
    return condition()


@pytest.mark.parametrize("end", ["stdin closed", "SIGHUP", "SIGTERM"])
def test_session_hold_lives_as_long_as_its_stdin(box, end):
    # Its own pid registered; a renewal every SESSION_HOLD_RENEW_S (60 s, injected shorter); stdin closed, SIGHUP or
    # SIGTERM → DELETE, exit 0. As `ssh brightroar spark session hold …` runs it in the background for Orca's pi.
    assert commands.SESSION_HOLD_RENEW_S == gateproto.SESSION_HOLD_RENEW_S == 60
    box.gate.script[("POST", "/v1/sessions")] = answer({"id": "s-7", "expires_at": NOW + 43200})
    box.gate.script[("POST", "/v1/sessions/{id}/renew")] = answer({"id": "s-7", "expires_at": NOW + 43200})
    box.gate.script[("DELETE", "/v1/sessions/{id}")] = answer({})
    proc = subprocess.Popen([sys.executable, "-c", HOLD, "0.3", "session", "hold", "--model", "coder", "--label",
                             "pi in Orca"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=_cli_env(box.folder, box.registry))
    try:
        assert _until(lambda: box.gate.bodies("/v1/sessions")), proc.stderr.read1() if proc.poll() is not None else ""
        began = time.monotonic()
        assert box.gate.bodies("/v1/sessions") == [{"model": "coder", "pid": proc.pid, "label": "pi in Orca"}]
        assert _until(lambda: box.gate.bodies("/v1/sessions/s-7/renew"))
        assert time.monotonic() - began >= 0.25
        assert proc.poll() is None  # it lives on
        assert ("status", "DELETE", "/v1/sessions/s-7") not in box.gate.calls()
        if end == "stdin closed":
            proc.stdin.close()
        else:  # stdin stays open: the signal alone ends it
            proc.send_signal(signal.SIGHUP if end == "SIGHUP" else signal.SIGTERM)
        proc.wait(timeout=15)
        out, err = proc.stdout.read(), proc.stderr.read()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(5)
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            pipe.close()
    assert proc.returncode == 0, err
    assert (out, err) == (b"", b"")
    assert box.gate.calls()[-1] == ("status", "DELETE", "/v1/sessions/s-7")
    assert all(call[0] == "status" for call in box.gate.calls())


def _hold(box, renew_s: str = "0.3", stdin: Any = subprocess.PIPE) -> subprocess.Popen:
    """`spark session hold --model coder` in a subprocess, its renewal every `renew_s`, its stdin a pipe we hold."""
    return subprocess.Popen([sys.executable, "-c", HOLD, renew_s, "session", "hold", "--model", "coder", "--label",
                             "pi in Orca"], stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=_cli_env(box.folder, box.registry))


def _ended(proc: subprocess.Popen) -> tuple[bytes, bytes]:
    try:
        proc.wait(timeout=15)
        return proc.stdout.read(), proc.stderr.read()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(5)
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            if pipe is not None:
                pipe.close()


@pytest.mark.parametrize("status", [404, 403])
def test_session_hold_ends_when_the_gate_says_its_session_is_gone(box, status):
    # A renewal refused 404 (the session has ended) or 403 (not this account's): the gate's sentence, exit 1, and no
    # DELETE, since there is nothing of this account's to end (Task 30's review, Minors 1 and 2).
    words = {404: "Session s-7 has ended: its process has exited, or it went 12 hours without a renewal.",
             403: "Only the account that began session s-7 can renew it."}[status]
    box.gate.script[("POST", "/v1/sessions")] = answer({"id": "s-7", "expires_at": NOW + 43200})
    box.gate.script[("POST", "/v1/sessions/{id}/renew")] = answer({"message": words, "code": None}, status)
    box.gate.script[("DELETE", "/v1/sessions/{id}")] = answer({})
    proc = _hold(box)
    out, err = _ended(proc)
    assert (proc.returncode, out, err) == (1, b"", f"{words}\n".encode())
    assert [call[2] for call in box.gate.calls()] == ["/v1/sessions", "/v1/sessions/s-7/renew"]


def test_session_hold_goes_on_when_a_renewal_doesnt_take(box):
    # The gate out of reach (the connection closed with no answer), then its own error (a 503): each said, and the
    # hold goes on, renewing again; only its stdin's close ends it (Task 30's review, Minors 1 and 2).
    tries = []

    def renew(handler, seen):
        tries.append(seen)
        if len(tries) == 1:
            handler.close_connection = True  # no answer at all
        elif len(tries) == 2:
            handler.answer(503, {"message": gate_app.STILL_FINISHING, "code": None})
        else:
            handler.answer(200, {"id": "s-7", "expires_at": NOW + 43200})

    box.gate.script[("POST", "/v1/sessions")] = answer({"id": "s-7", "expires_at": NOW + 43200})
    box.gate.script[("POST", "/v1/sessions/{id}/renew")] = renew
    box.gate.script[("DELETE", "/v1/sessions/{id}")] = answer({})
    proc = _hold(box)
    assert _until(lambda: len(tries) >= 3)
    assert proc.poll() is None
    proc.stdin.close()
    out, err = _ended(proc)
    assert (proc.returncode, out) == (0, b"")
    lines = err.decode().splitlines()
    assert len(lines) == 2, lines
    assert all(line.startswith("The session wasn't renewed this time: ") for line in lines), lines
    assert gate_app.STILL_FINISHING in lines[1]
    assert box.gate.calls()[-1] == ("status", "DELETE", "/v1/sessions/s-7")


def test_session_hold_renews_while_its_stdin_keeps_sending(box):
    # Data always waiting on stdin never holds a renewal back: its time is checked on every pass (Task 30's review,
    # Minor 3). /dev/zero is always readable and never ends, so a hold that renewed only when stdin was quiet would
    # never renew; SIGTERM ends it.
    box.gate.script[("POST", "/v1/sessions")] = answer({"id": "s-7", "expires_at": NOW + 43200})
    box.gate.script[("POST", "/v1/sessions/{id}/renew")] = answer({"id": "s-7", "expires_at": NOW + 43200})
    box.gate.script[("DELETE", "/v1/sessions/{id}")] = answer({})
    with open("/dev/zero", "rb") as zeros:
        proc = _hold(box, "0.2", stdin=zeros)
    try:
        assert _until(lambda: len(box.gate.bodies("/v1/sessions/s-7/renew")) >= 3, within_s=10)
        assert proc.poll() is None
        proc.send_signal(signal.SIGTERM)
    finally:
        out, err = _ended(proc)
    assert (proc.returncode, out, err) == (0, b"", b"")
    assert box.gate.calls()[-1] == ("status", "DELETE", "/v1/sessions/s-7")


def test_each_command_waits_as_long_as_its_route(box, monkeypatch):
    # Each command's client timeouts: the connection and the head within REQUEST_TIMEOUT_S; a load's lines within the
    # load call's 200 s and 10 more; an unload's and make-room's as long as their drains take; the rest bounded.
    assert (commands.REQUEST_TIMEOUT_S, commands.LOAD_THEN_S) == (5, gateproto.LOAD_CALL_TIMEOUT_S + 10) == (5, 210)
    box.gate.script[("POST", "/v1/load")] = stream(loading(), loaded())
    box.gate.script[("POST", "/v1/pin")] = answer({"label": "the coder", "until": None, "loaded_s": None,
                                                   "command": "coder"})
    box.gate.script[("POST", "/v1/unload")] = stream({"kind": "unloading", "inflight": 0}, {"unloaded": True})
    box.gate.script[("DELETE", "/v1/pin/{model}")] = answer({"label": "the coder", "idle_min": 60, "command": "coder"})
    box.gate.script[("POST", "/v1/make-room/plan")] = answer(plan_answer(Decimal(40), 9, candidates()))
    box.gate.script[("POST", "/v1/make-room")] = stream(progress(CODER, "the coder"),
                                                        room_result(["the coder"], 50.0, 40.0))
    box.gate.script[("POST", "/v1/release")] = answer({"room": None, "brake": False, "reloading": []})
    box.gate.script[("GET", "/v1/logs/{model}")] = answer({"lines": []})
    box.gate.script[("POST", "/v1/sessions")] = answer({"id": "s-1", "expires_at": NOW})
    expected = {("load", "coder"): [("/v1/load", 210)], ("pin", "coder"): [("/v1/pin", 210)],
                ("unload", "coder"): [("/v1/unload", None)], ("unpin", "coder"): [],
                ("make-room", "40G", "--yes"): [("/v1/make-room", None)], ("make-room", "--done"): [],
                ("logs", "coder"): [], ("session", "start", "--model", "coder", "--pid", "1", "--label", "x"): []}
    for argv, streams in expected.items():
        box.made.clear()
        assert box(*argv)[0] == 0, argv
        assert all(record["timeout_s"] == 5 for record in box.made), argv
        assert [s for record in box.made for s in record["streams"]] == streams, argv
    # A load the stand-in holds longer than the request timeout (6 s against 5, scaled down) still gets its
    # confirmation, never the gate isn't answering: only the head is bound by it.
    monkeypatch.setattr(commands, "REQUEST_TIMEOUT_S", 0.3)
    box.gate.script[("POST", "/v1/load")] = stream(0.6, loading(), 0.6, loaded())
    code, out, err = box("load", "coder")
    assert (code, err) == (0, "")
    assert out.endswith("Loaded the coder in 24 s. It unloads after 60 min idle; `spark pin coder` keeps it.\n")
    # And `unload` prints its count before the stand-in sends its result, through a pipe, as over SSH.
    released = threading.Event()
    box.gate.script[("POST", "/v1/unload")] = stream({"kind": "unloading", "inflight": 1}, released,
                                                     {"unloaded": True, "pin_ended": False})
    main = "import sys; from spark import cli; sys.exit(cli.main(sys.argv[1:]))"
    proc = subprocess.Popen([sys.executable, "-c", main, "unload", "coder"], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=_cli_env(box.folder, box.registry))
    try:
        ready, _, _ = select.select([proc.stdout], [], [], 15)
        assert ready, "no line before the result"
        assert proc.stdout.readline() == "Unloading the coder once its 1 request in flight finishes…\n".encode()
        assert not released.is_set()
        released.set()
        out, err = proc.communicate(timeout=15)
    finally:
        released.set()
        if proc.poll() is None:
            proc.kill()
            proc.wait(5)
    assert (proc.returncode, out, err) == (0, "Unloaded the coder.\n".encode(), b"")


def test_no_command_sends_a_key(box, monkeypatch):
    # No command reads a key from the environment, nor sends one: the socket's group says who may, and the gate knows
    # each caller by its uid.
    reads: list[str] = []

    class Recording(dict):
        def __getitem__(self, name):
            reads.append(name)
            return super().__getitem__(name)

        def get(self, name, default=None):
            reads.append(name)
            return super().get(name, default)

        def __contains__(self, name):
            reads.append(name)
            return super().__contains__(name)

    environ = Recording(os.environ, SPARK_API_KEY=MARKER, LLAMASWAP_KEY_DAN_MAC=MARKER, OPENAI_API_KEY=MARKER)
    monkeypatch.setattr(os, "environ", environ)
    box.gate.script[("POST", "/v1/load")] = stream(loading(), loaded())
    box.gate.script[("POST", "/v1/pin")] = stream(loading(), {"label": "the coder", "until": AT_18, "loaded_s": 24,
                                                              "command": "coder"})
    box.gate.script[("POST", "/v1/unload")] = stream({"kind": "unloading", "inflight": 0}, {"unloaded": True})
    box.gate.script[("DELETE", "/v1/pin/{model}")] = answer({"label": "the coder", "idle_min": 60, "command": "coder"})
    box.gate.script[("POST", "/v1/make-room/plan")] = answer(plan_answer(Decimal(40), 9, candidates()))
    box.gate.script[("POST", "/v1/make-room")] = stream(progress(CODER, "the coder"),
                                                        room_result(["the coder"], 50.0, 40.0))
    box.gate.script[("POST", "/v1/release")] = answer({"room": None, "brake": False, "reloading": []})
    box.gate.script[("GET", "/v1/logs/{model}")] = answer({"lines": ["a line"]})
    box.gate.script[("POST", "/v1/sessions")] = answer({"id": "s-1", "expires_at": NOW})
    box.gate.script[("POST", "/v1/sessions/{id}/renew")] = answer({"id": "s-1", "expires_at": NOW})
    box.gate.script[("DELETE", "/v1/sessions/{id}")] = answer({})
    for argv in (("load", "coder"), ("pin", "coder", "8h"), ("unload", "coder"), ("unpin", "coder"),
                 ("make-room", "40G", "--yes"), ("make-room", "--done"), ("logs", "coder"),
                 ("session", "start", "--model", "coder", "--pid", "1", "--label", "x"), ("session", "renew", "s-1"),
                 ("session", "end", "s-1")):
        assert box(*argv)[0] == 0, argv
    read, write = os.pipe()
    os.close(write)  # stdin closed at once: the hold registers, then ends
    with os.fdopen(read) as closed:
        monkeypatch.setattr(sys, "stdin", closed)
        assert cli.main(["session", "hold", "--model", "coder", "--label", "x"]) == 0
    assert len(box.gate.seen) == 13
    for seen in box.gate.seen:
        assert not {"authorization", "x-api-key", "cookie"} & set(seen.headers), seen
        assert MARKER not in json.dumps(seen.headers) and MARKER.encode() not in seen.raw, seen
    assert not [name for name in reads if re.search("KEY|TOKEN|SECRET", name)], reads


def test_sizes_and_durations_parse():
    for text in ("41G", "41GiB", "41"):
        assert commands.parse_size(text) == Decimal(41), text
    assert commands.parse_size("40.5G") == Decimal("40.5")
    assert (commands.parse_duration("8h"), commands.parse_duration("90m"), commands.parse_duration("2d")) == (
        28800, 5400, 172800)
    assert commands.parse_duration("45s") == 45
    for bad in ("x", "", "41GB", "-4G", "0", "0G", "4 G", "１G"):
        with pytest.raises(ValueError, match=re.escape(repr(bad))):
            commands.parse_size(bad)
    for bad in ("x", "", "8", "8 h", "1.5h", "0h", "-2d", "366d", "９h"):
        with pytest.raises(ValueError, match=re.escape(repr(bad))):
            commands.parse_duration(bad)


def test_a_bad_size_or_duration_is_a_usage_error_naming_it(box, capsys):
    for argv, said in ((("make-room", "x"), "'x' isn't a size"), (("pin", "coder", "x"), "'x' isn't a duration"),
                       (("make-room", "40G", "--for", "1.5h"), "'1.5h' isn't a duration")):
        with pytest.raises(SystemExit) as exc:
            cli.main(list(argv))
        assert exc.value.code == 2, argv
        assert said in capsys.readouterr().err, argv
    assert box.gate.seen == []


# The rulings that named this task, beyond the brief's test list.

def test_progress_lines_are_told_apart_by_kind_never_by_their_keys(box):
    # A StoppingProgress's keys are a subset of a MakeRoomProgress's, in the same stream (the controller's ruling at
    # Task 12's second re-review): only `kind` tells them apart, even with keys added.
    box.gate.script[("POST", "/v1/make-room/plan")] = answer(plan_answer(Decimal(40), 9, candidates()))
    box.gate.script[("POST", "/v1/make-room")] = stream(
        progress(CODER, "the coder", 2), {"kind": "stopping", "model": CODER, "label": "the coder", "inflight": 2},
        {"kind": "after_load", "model": CODER, "label": "the coder", "inflight": 2},
        {"kind": "a-kind-from-later", "model": CODER, "label": "the coder", "inflight": 2},
        room_result(["the coder"], 50.0, 40.0))
    code, out, err = box("make-room", "40G", "--yes")
    assert (code, err) == (0, "")
    assert out.split("[y/N] y\n", 1)[1].splitlines() == [
        "Unloading the coder once its 2 requests in flight finish…",
        "Still stopping the coder: llama-swap hasn't finished its unload…",
        "Waiting for the coder to finish loading before unloading it…",
        HELD_40]


def test_every_progress_kind_and_waiting_reason_has_words():
    # A kind or a reason added later fails here until it is worded (gateproto's tuples, read).
    base = {"model": CODER, "label": "the coder", "inflight": 1, "last_s": 24.0, "needed_gib": 41.0,
            "free_gib": 18.0, "loading_label": "Gemma", "release_waits_for_dan": False, "release_after_s": 300.0}
    for kind in gateproto.PROGRESS_KINDS:
        for why in (gateproto.WAITING_WHY if kind == "waiting" else (None,)):
            words = commands.progress_words(base | {"kind": kind, "why": why}, "the coder", 28)
            assert words and words != "The coder is waiting…", (kind, why)
            assert messages.pi_retry_match(words) is None, (kind, why, words)
    assert commands.progress_words({"kind": "a-kind-from-later"}, "the coder", 28) is None
    # A wait whose reason can't be read says only that it waits, never a crash mid-wait.
    assert commands.progress_words({"kind": "waiting", "why": "memory", "label": "the coder"}, "x", 28) == (
        "The coder is waiting…")


def test_names_go_in_a_route_quoted(box):
    box.gate.script[("DELETE", "/v1/pin/{model}")] = answer({"label": "the coder", "idle_min": 60, "command": "coder"})
    box.gate.script[("GET", "/v1/logs/{model}")] = answer({"lines": []})
    box.gate.script[("POST", "/v1/sessions/{id}/renew")] = answer({"id": "x", "expires_at": NOW})
    box.gate.script[("DELETE", "/v1/sessions/{id}")] = answer({})
    box("unpin", "a/b")
    box("logs", "x y?n=1000")
    box("session", "renew", "../x")
    box("session", "end", "a?b#c")
    assert box.gate.calls() == [("control", "DELETE", "/v1/pin/a%2Fb"),
                                ("control", "GET", "/v1/logs/x%20y%3Fn%3D1000?n=20"),
                                ("status", "POST", "/v1/sessions/..%2Fx/renew"),
                                ("status", "DELETE", "/v1/sessions/a%3Fb%23c")]


def test_ctrl_c_ends_the_wait_never_the_work(box, monkeypatch):
    # Ctrl-C ends the wait and the command, saying what the gate goes on with: never a traceback, exit 130.
    stopping = {"kind": "stopping", "model": CODER, "label": "the coder"}
    real = commands.GateClient.stream

    def interrupted(self, route, body=None, *, then_s=...):
        lines = iter(real(self, route, body, then_s=then_s))

        def gen():
            yield next(lines)
            raise KeyboardInterrupt

        class Stream:
            def __iter__(self):
                return gen()

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return None

        return Stream()

    monkeypatch.setattr(commands.GateClient, "stream", interrupted)
    box.gate.script[("POST", "/v1/unload")] = stream({"kind": "unloading", "inflight": 0}, stopping,
                                                     {"unloaded": True})
    code, out, err = box("unload", "coder")
    assert (code, out) == (130, "Unloading the coder…\n")
    assert err == ("Stopped waiting; the gate goes on unloading the coder. On the Spark, `spark status` shows when "
                   "it's done.\n")
    box.gate.script[("POST", "/v1/load")] = stream(loading(), loaded())
    code, out, err = box("load", "coder")
    assert code == 130 and "Traceback" not in err
    assert err == ("Stopped waiting. If the coder's load had started, it goes on; if not, the gate drops the request. "
                   "On the Spark, `spark status` shows which.\n")
    # Ctrl-C at the question is a no: nothing posted.
    monkeypatch.setattr(commands.GateClient, "stream", real)

    class Interrupting(_Stdin):
        def readline(self, *args):
            raise KeyboardInterrupt

    box.gate.script[("POST", "/v1/make-room/plan")] = answer(plan_answer(Decimal(40), 9, candidates()))
    box.gate.seen.clear()
    code, out, err = box("make-room", "40G", stdin=Interrupting("", True))
    assert code == 130
    assert out.endswith("Unload 1? [y/N] \nNothing unloaded, and nothing held.\n") and err == ""
    assert [call[2] for call in box.gate.calls()] == ["/v1/make-room/plan"]


def test_no_line_matches_pis_retry_list(box):
    # Dan's clarity rule: no confirmation or refusal of these commands holds words pi's retry list matches.
    box.gate.script[("POST", "/v1/load")] = stream(loading(), loaded())
    box.gate.script[("POST", "/v1/unload")] = stream({"kind": "unloading", "inflight": 1},
                                                     {"kind": "stopping", "model": CODER, "label": "the coder"})
    box.gate.script[("POST", "/v1/make-room/plan")] = answer(plan_answer(Decimal(70), 9, candidates()))
    box.gate.script[("POST", "/v1/make-room")] = stream(progress(CODER, "the coder"), room_result(
        ["the coder"], 50.0, 40.0, AT_18, ["your pin on the coder", "the session agent's pi"]))
    box.gate.script[("POST", "/v1/release")] = answer({"room": None, "brake": False, "reloading": []})
    said = []
    for argv, given in ((("load", "coder"), {}), (("unload", "coder"), {}), (("make-room", "70G"), {"tty": False}),
                        (("make-room", "70G"), {"stdin": "y\n", "tty": True}), (("make-room", "70G"), {"tty": True}),
                        (("make-room", "--done"), {})):
        code, out, err = box(*argv, **given)
        said += (out + err).splitlines()
    assert len(said) > 15
    assert [line for line in said if messages.pi_retry_match(line)] == []


def test_the_commands_are_registered_with_their_help():
    parser = cli.build_parser()
    for argv in (["load", "coder"], ["unload", "coder"], ["pin", "coder"], ["pin", "coder", "8h"], ["unpin", "coder"],
                 ["make-room", "40G"], ["make-room", "40G", "--for", "8h", "--yes"], ["make-room", "--all"],
                 ["make-room", "--done"], ["logs", "coder", "-n", "5"],
                 ["session", "start", "--model", "coder", "--pid", "1", "--label", "x"], ["session", "renew", "s"],
                 ["session", "end", "s"], ["session", "hold", "--model", "coder", "--label", "x"]):
        assert callable(parser.parse_args(argv).func), argv
    usage = parser.format_help()
    for command in ("load", "unload", "pin", "unpin", "make-room", "logs", "session"):
        assert re.search(rf"^\s+{command}\s+\S", usage, re.MULTILINE), command

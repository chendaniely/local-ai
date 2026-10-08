"""Task 10's gate protocol (gateproto), the CLI's client for it (gateclient) and the paths they use. The client speaks
JSON over HTTP/1.1 on a Unix socket with the standard library only. Its gate here is a stand-in, also the standard
library's (http.server's handler on socketserver's Unix-socket server), on a short AF_UNIX path; nothing touches the
box's own sockets."""

import json
import os
import shutil
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from types import SimpleNamespace

import pytest

from spark import gateclient, gateproto, messages, paths
from spark.gateclient import GateClient, GateError, GateForbidden, GateRefused, GateUnavailable

WORDS = gateclient._words  # the real one, before dans_words replaces it
DAN_STEP = "On the Spark, `make doctor` shows what's wrong."
AGENT_STEP = "`make doctor` on the Spark shows Dan what's wrong."
MARKER = "BODY-MARKER"  # stands for what an answer's body holds beyond its message
CREDENTIAL = "CREDENTIAL-MARKER"  # stands for a key, wherever one might travel


@pytest.fixture(autouse=True)
def dans_words(monkeypatch):
    """A sentence's next step is in Dan's words unless a test says otherwise: which words a login gets depends on its
    groups (test_the_next_step_is_dans_only_for_a_login_that_can_take_it)."""
    monkeypatch.setattr(gateclient, "_words", lambda: "dan")


@pytest.fixture
def sockdir():
    """A short folder for Unix sockets: macOS caps an AF_UNIX path at 104 bytes, and pytest's tmp_path there is
    longer."""
    path = Path(tempfile.mkdtemp(prefix="gc", dir="/tmp"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


class _Gate(BaseHTTPRequestHandler):
    """The gate's stand-in. It answers HTTP/1.1, as uvicorn does, so its stream is chunked."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # a Unix client's address is '', which the default line can't print
        pass

    def _json(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _chunk(self, data: bytes) -> None:
        self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))

    def _body(self) -> dict:
        return json.loads(self.rfile.read(int(self.headers["Content-Length"])))

    def _canned(self) -> None:
        """The test's answer, as given: (status, the body's bytes)."""
        status, data = self.server.canned
        self.send_response(status)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/v1/status":
            self._json(200, {"ok": True})
        elif self.path == "/v1/canned":
            self._canned()
        else:
            self._json(404, {"message": "no such route"})

    def do_DELETE(self):
        self._json(200, {"method": self.command, "path": self.path})

    def do_POST(self):
        body = self._body()
        if self.path == "/v1/load":
            self._json(200, {"method": self.command, "got": body, "content_type": self.headers["Content-Type"]})
        elif self.path == "/v1/pin":
            self._json(403, {"message": "not yours"})
        elif self.path == "/v1/canned":
            self._canned()
        elif self.path == "/v1/unload":
            # Three NDJSON lines (the gate's /v1/unload sends two, its count at once and its result once the drain is
            # done): the first, then a wait until the test has read it, so a client that waits for the whole answer
            # before yielding anything is caught; then a line split across two chunks, and the last line in the same
            # chunk as the end of that one.
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            self._chunk(json.dumps({"inflight": 1, "got": body}).encode() + b"\n")
            self.server.saw_first_read = self.server.first_read.wait(5)
            self._chunk(b'{"draini')
            self._chunk(b'ng": true}\n{"unloaded": true}\n')
            self.wfile.write(b"0\r\n\r\n")
        else:
            self._json(404, {"message": "no such route"})


@contextmanager
def _serving(path: Path):
    server = socketserver.ThreadingUnixStreamServer(str(path), _Gate)
    server.daemon_threads = True
    server.first_read = threading.Event()
    server.saw_first_read = None
    server.canned = (200, b"{}")
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_the_client_speaks_http_over_a_unix_socket(sockdir):
    path = sockdir / "g.sock"
    with _serving(path):
        client = GateClient(path, 5)
        assert client.get("/v1/status") == {"ok": True}
        assert client.post("/v1/load", {"model": "coder"}) == {
            "method": "POST", "got": {"model": "coder"}, "content_type": "application/json"}
        assert client.delete("/v1/pin/coder") == {"method": "DELETE", "path": "/v1/pin/coder"}


def test_a_missing_socket_reads_as_gate_unavailable(sockdir):
    path = sockdir / "absent.sock"
    with pytest.raises(GateUnavailable) as raised:
        GateClient(path, 1).get("/v1/status")
    assert str(raised.value) == f"The gate can't be reached: there is no socket at {path}. {DAN_STEP}"


def test_a_socket_nobody_listens_on_reads_as_gate_unavailable(sockdir):
    # A socket file whose listener has gone: the connection is refused.
    path = sockdir / "gone.sock"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as gone:
        gone.bind(str(path))
    with pytest.raises(GateUnavailable) as raised:
        GateClient(path, 1).get("/v1/status")
    assert str(raised.value) == f"The gate can't be reached: nothing is listening on {path}. {DAN_STEP}"


def test_a_socket_nobody_answers_times_out_as_gate_unavailable(sockdir):
    # As systemd holds the gate's socket while the gate is down: the connection waits in the backlog, never accepted.
    path = sockdir / "held.sock"
    raised = []

    def ask():
        try:
            GateClient(path, 0.5).get("/v1/status")
        except GateUnavailable as err:
            raised.append(err)

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as held:
        held.bind(str(path))
        held.listen(8)
        # In a thread, so a client that waits for ever fails this test rather than hanging it.
        asker = threading.Thread(target=ask, daemon=True)
        started = time.monotonic()
        asker.start()
        asker.join(5)
        took = time.monotonic() - started
    assert not asker.is_alive() and took < 2
    [error] = raised
    assert str(error) == f"The gate didn't answer on {path} within 0.5 s. {DAN_STEP}"


def test_a_closed_socket_says_who_may_use_it(monkeypatch):
    def refuse(self, address):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    with pytest.raises(GateForbidden) as raised:
        GateClient(paths.GATE_CONTROL_SOCKET, 1).get("/v1/status")
    assert "spark-admin" in str(raised.value) and str(paths.GATE_CONTROL_SOCKET) in str(raised.value)
    with pytest.raises(GateForbidden) as raised:
        GateClient(paths.GATE_STATUS_SOCKET, 1).get("/v1/status")
    assert "spark-users" in str(raised.value) and "spark-admin" not in str(raised.value)
    # A socket that is neither of the two: the text names both, and which group each is for.
    with pytest.raises(GateForbidden) as raised:
        GateClient(Path("/run/elsewhere.sock"), 1).get("/v1/status")
    assert str(raised.value) == ("/run/elsewhere.sock refused this login: the gate's status socket is for members of "
                                 "spark-users, and its control socket for members of spark-admin.")


def test_a_refusal_comes_back_as_gate_refused_with_its_body(sockdir):
    path = sockdir / "g.sock"
    with _serving(path):
        with pytest.raises(GateRefused) as raised:
            GateClient(path, 5).post("/v1/pin", {"model": "coder", "until": None})
    assert (raised.value.status, raised.value.body) == (403, {"message": "not yours"})
    assert str(raised.value) == "not yours"


def test_no_gate_error_carries_a_credential_or_a_response_body(sockdir):
    # The controller's ruling at Task 10: cli.main prints a GateError's text, so the text never holds what was sent,
    # nor what the gate answered beyond its `message`, the one plain line the gate words for whoever asked.
    path = sockdir / "g.sock"
    with _serving(path) as server:
        client = GateClient(path, 5)
        errors = []

        def caught(call):
            with pytest.raises(GateError) as raised:
                call()
            errors.append(raised.value)
            return raised.value

        # A refusal: its message only, never the rest of its body.
        server.canned = (409, json.dumps({"message": "Not loading the coder now.", "detail": MARKER,
                                          "key": CREDENTIAL}).encode())
        refused = caught(lambda: client.post("/v1/canned", {"model": "coder", "key": CREDENTIAL}))
        assert str(refused) == "Not loading the coder now." and refused.body["detail"] == MARKER
        # A message that isn't one line of printable text isn't shown at all, and nor is a body that isn't JSON.
        server.canned = (409, json.dumps({"message": f"Not loading.\n{MARKER}"}).encode())
        assert str(caught(lambda: client.get("/v1/canned"))) == "The gate refused that request (HTTP 409)."
        server.canned = (409, json.dumps({"message": "Not loading." + "x" * 2000}).encode())
        assert str(caught(lambda: client.get("/v1/canned"))) == "The gate refused that request (HTTP 409)."
        server.canned = (500, f"Internal Server Error {MARKER}".encode())
        assert str(caught(lambda: client.get("/v1/canned"))) == (
            f"The gate failed on that request (HTTP 500). {DAN_STEP}")
        # An answer that isn't a JSON object, whole or as a stream's line.
        server.canned = (200, f"{MARKER} {CREDENTIAL}".encode())
        assert str(caught(lambda: client.post("/v1/canned", {"key": CREDENTIAL}))) == (
            f"The gate's answer couldn't be read: it isn't a JSON object. {DAN_STEP}")
        server.canned = (200, f'{{"ok": true}}\n{MARKER}\n'.encode())
        caught(lambda: list(client.stream("/v1/canned")))
    # And a call that never reached the gate.
    caught(lambda: GateClient(sockdir / "absent.sock", 1).post("/v1/load", {"model": CREDENTIAL}))
    for error in errors:
        for shown in (str(error), repr(error)):
            assert MARKER not in shown and CREDENTIAL not in shown, shown


def test_the_next_step_is_dans_only_for_a_login_that_can_take_it(monkeypatch, sockdir):
    # Task 6's convention: agent is never told to run a command only Dan can run. `make doctor` is Dan's, so his
    # words go to root and to spark-admin's members, who can run it, and agent's to every other login.
    admin = SimpleNamespace(gr_gid=990)
    monkeypatch.setattr(gateclient.grp, "getgrnam", lambda name: admin if name == "spark-admin" else None)
    monkeypatch.setattr(gateclient.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(gateclient.os, "getegid", lambda: 1000)
    monkeypatch.setattr(gateclient.os, "getgroups", lambda: [1000, 990])
    assert WORDS() == "dan"
    monkeypatch.setattr(gateclient.os, "getgroups", lambda: [1000])
    assert WORDS() == "agent"
    monkeypatch.setattr(gateclient.os, "getegid", lambda: 990)
    assert WORDS() == "dan"
    monkeypatch.setattr(gateclient.os, "getegid", lambda: 1000)
    monkeypatch.setattr(gateclient.os, "geteuid", lambda: 0)
    assert WORDS() == "dan"

    def no_group(name):
        raise KeyError(name)

    monkeypatch.setattr(gateclient.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(gateclient.grp, "getgrnam", no_group)  # a box, or the Mac, without the group
    assert WORDS() == "agent"
    # agent's form, as a sentence.
    monkeypatch.setattr(gateclient, "_words", lambda: "agent")
    path = sockdir / "absent.sock"
    with pytest.raises(GateUnavailable) as raised:
        GateClient(path, 1).get("/v1/status")
    assert str(raised.value) == f"The gate can't be reached: there is no socket at {path}. {AGENT_STEP}"
    # The same steps as Task 6's refusals give for a service that is down, each in its own words.
    assert messages._ALERT["dan"].endswith(" on the Spark, `make doctor` shows what's wrong.")
    assert messages._ALERT["agent"].endswith(f" and {AGENT_STEP}")


def test_a_stream_yields_each_line_as_json(sockdir):
    path = sockdir / "g.sock"
    with _serving(path) as server:
        client = GateClient(path, 10)
        lines = client.stream("/v1/unload", {"model": "coder"})
        assert next(lines) == {"inflight": 1, "got": {"model": "coder"}}
        server.first_read.set()
        assert list(lines) == [{"draining": True}, {"unloaded": True}]
        assert server.saw_first_read  # the first line came while the stand-in still held the rest
        # Without a body it is a GET, and an answer's last line needs no newline.
        assert list(client.stream("/v1/status")) == [{"ok": True}]
        # A refusal comes before any line.
        with pytest.raises(GateRefused) as raised:
            client.stream("/v1/pin", {"model": "coder"})
        assert raised.value.status == 403


# The table in website/design/phase-2a.md, Task 10: (socket, method, path, callers).
TABLE = {
    ("status", "POST", "/v1/admit", "front"),
    ("status", "GET", "/v1/front/events", "front"),
    ("status", "POST", "/v1/front/inflight", "front"),
    ("status", "POST", "/v1/front/drained", "front"),
    ("status", "POST", "/v1/front/busy", "front"),
    ("status", "POST", "/v1/front/refused", "front"),
    ("status", "GET", "/v1/status", "users"),
    ("status", "POST", "/v1/sessions", "owner"),
    ("status", "POST", "/v1/sessions/{id}/renew", "owner"),
    ("status", "DELETE", "/v1/sessions/{id}", "owner"),
    ("control", "GET", "/v1/status", "admin"),
    ("control", "POST", "/v1/load", "admin"),
    ("control", "POST", "/v1/unload", "admin"),
    ("control", "POST", "/v1/pin", "admin"),
    ("control", "DELETE", "/v1/pin/{model}", "admin"),
    ("control", "POST", "/v1/make-room/plan", "admin"),
    ("control", "POST", "/v1/make-room", "admin"),
    ("control", "POST", "/v1/release", "admin"),
    ("control", "GET", "/v1/quiet", "admin"),
    ("control", "POST", "/v1/drain-all", "admin"),
    ("control", "POST", "/v1/undrain-all", "admin"),
    ("control", "POST", "/v1/apply/renew", "admin"),
    ("control", "POST", "/v1/apply/begin", "admin"),
    ("control", "POST", "/v1/apply/end", "admin"),
    ("control", "GET", "/v1/logs/{model}", "admin"),
    ("control", "POST", "/v1/canary", "admin"),
}


def test_every_route_names_its_socket_and_callers():
    routes = gateproto.ROUTES
    for route in routes:
        assert route.socket in {"status", "control"}, route
        assert route.callers in {"front", "users", "owner", "admin"}, route
    # None twice, and exactly the plan's table.
    assert len({(r.socket, r.method, r.path) for r in routes}) == len(routes)
    assert {tuple(r) for r in routes} == TABLE
    # The front's are exactly its six, all on the status socket; the control socket's are all spark-admin's.
    assert {(r.socket, r.method, r.path) for r in routes if r.callers == "front"} == {
        ("status", "POST", "/v1/admit"), ("status", "GET", "/v1/front/events"),
        ("status", "POST", "/v1/front/inflight"), ("status", "POST", "/v1/front/drained"),
        ("status", "POST", "/v1/front/busy"), ("status", "POST", "/v1/front/refused")}
    assert {r.callers for r in routes if r.socket == "control"} == {"admin"}
    assert gateproto.SOCKET_GROUPS == {"status": "spark-users", "control": "spark-admin"}


def test_the_status_view_carries_the_plans_fields():
    # Task 19 builds it, Task 29 prints it, and 2b's menu bar reads it as `spark status --json`.
    assert gateproto.StatusView.__required_keys__ == {
        "schema", "host", "at", "memory", "models", "waiting", "paused", "held", "pins", "sessions", "recent",
        "health", "applying", "problems"}
    assert gateproto.MemoryView.__required_keys__ == {
        "total_gib", "available_gib", "brake_gib", "warn_gib", "above_brake_gib", "reserve_gib", "owed_gib",
        "held_gib", "free_for_a_load_gib", "unaccounted_gib"}
    assert gateproto.ModelView.__required_keys__ == {
        "name", "label", "resident", "footprint_gib", "state", "inflight", "oldest_request_s", "last_use",
        "pinned", "pinned_until", "sessions", "brake_mark"}
    assert gateproto.MODEL_STATES == ("ready", "starting", "draining", "not_loaded")
    assert gateproto.WaitingView.__required_keys__ == {"key_label", "model", "waited_s", "wait_s", "why"}
    assert gateproto.WAITING_WHY == ("memory", "brake", "slot", "dan", "restart", "llama_swap")
    assert gateproto.PausedView.__required_keys__ == {
        "since", "available_gib", "releases_at", "waits_for_dan", "last_fired", "last_released"}
    assert gateproto.HeldView.__required_keys__ == {"size_gib", "until"}
    assert gateproto.RecentView.__required_keys__ == {"at", "text", "code", "model", "key_label"}
    assert gateproto.HealthView.__required_keys__ == {
        "front", "gate", "brake", "llama_swap", "ntfy", "activity_age_s", "unticketed_engines", "no_ticket_refusals"}
    assert "key_checked_at" in gateproto.BrakeHealth.__required_keys__
    assert "failing_since" in gateproto.NtfyHealth.__required_keys__
    assert gateproto.EngineView.__required_keys__ == {"model", "port", "pid"}
    assert gateproto.ApplyingView.__required_keys__ == {"since", "restarting"}
    assert gateproto.STATUS_SCHEMA == 1


def test_a_pin_with_an_end_and_one_without_read_as_spark_pin_words_them():
    # The controller's ruling at Task 10: `pinned` says whether there is a pin, and `pinned_until` only when it ends,
    # None for no end, as Task 7's `pinned` confirmation takes it (and make-room's list marks `pinned`).
    now = datetime(2026, 10, 8, 9, 0).astimezone()
    six = datetime(2026, 10, 8, 18, 0).astimezone().timestamp()
    # The fields of a ModelView these need; the rest are as any model's.
    ends = {"label": "the coder", "pinned": True, "pinned_until": six}
    endless = {"label": "the coder", "pinned": True, "pinned_until": None}
    words = [messages.pinned(v["label"], v["pinned_until"], None, "coder", now=now) for v in (ends, endless)]
    assert words == ["The coder stays loaded until 18:00. `spark unpin coder` ends the pin.",
                     "The coder stays loaded, with no end set. `spark unpin coder` ends the pin."]


PATHS = ("GATE_STATUS_SOCKET", "GATE_CONTROL_SOCKET", "GATE_STATE", "LAUNCH", "WHISPER_TMP", "VALUES", "FRONT_URL",
         "LLAMASWAP_URL")
READ_PATHS = f"import json; from spark import paths; print(json.dumps({{n: str(getattr(paths, n)) for n in {PATHS}}}))"


def _paths(**overrides: str) -> dict[str, str]:
    # paths reads the environment once, at import, so each reading is a process of its own.
    result = subprocess.run([sys.executable, "-c", READ_PATHS], env={**os.environ, **overrides}, capture_output=True,
                            text=True, timeout=60, check=False)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_the_gates_paths_default_to_the_plans_and_each_variable_overrides_it():
    assert _paths() == {
        "GATE_STATUS_SOCKET": "/run/local-ai/gate-status.sock",
        "GATE_CONTROL_SOCKET": "/run/local-ai/gate-control.sock",
        "GATE_STATE": "/var/lib/local-ai/gate",
        "LAUNCH": "/var/lib/local-ai/launch",
        "WHISPER_TMP": "/var/lib/local-ai/whisper-tmp",
        "VALUES": "/etc/local-ai/values.env",
        "FRONT_URL": "http://127.0.0.1:9100",
        "LLAMASWAP_URL": "http://127.0.0.1:900",
    }
    variables = {"GATE_STATUS_SOCKET": "SPARK_GATE_STATUS", "GATE_CONTROL_SOCKET": "SPARK_GATE_CONTROL",
                 "GATE_STATE": "SPARK_GATE_STATE", "LAUNCH": "SPARK_LAUNCH", "WHISPER_TMP": "SPARK_WHISPER_TMP",
                 "VALUES": "SPARK_VALUES", "FRONT_URL": "SPARK_FRONT_URL", "LLAMASWAP_URL": "SPARK_LLAMASWAP_URL"}
    assert _paths(**{variable: f"/stand-in/{name}" for name, variable in variables.items()}) == {
        name: f"/stand-in/{name}" for name in variables}

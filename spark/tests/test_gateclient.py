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
import typing
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from types import SimpleNamespace

import pytest

from spark import gateclient, gateproto, messages, paths
from spark.gateclient import GateClient, GateError, GateForbidden, GateInputError, GateRefused, GateUnavailable

WORDS = gateclient._words  # the real one, before dans_words replaces it
DAN_STEP = "On the Spark, `make doctor` shows what's wrong."
AGENT_STEP = "`make doctor` on the Spark shows Dan what's wrong."
ANSWER_BOUND = 1 << 20  # 1 MiB: a single answer's bound
LINE_BOUND = 64 << 10  # 64 KiB: a streamed line's
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

    def _short(self) -> None:
        """An answer that ends before its Content-Length, as from a gate that died after its head: (status, the length
        its head gives, the bytes that come), then the connection closes."""
        status, length, data = self.server.short
        self.send_response(status)
        self.send_header("Content-Length", str(length))
        self.end_headers()
        self.wfile.write(data)
        self.close_connection = True

    def _cut(self) -> None:
        """A chunked stream cut off after its first line, as by a gate that died mid-drain: no last chunk, a chunk
        that stops short of its size, or a line that stops short of its Content-Length."""
        how = self.server.cut
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        first = b'{"inflight": 1}\n'
        if how == "short of its length":
            self.send_header("Content-Length", str(len(first) + 20))
            self.end_headers()
            self.wfile.write(first + b'{"unl')
        else:
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            self._chunk(first)
            if how == "mid-chunk":
                self.wfile.write(b"14\r\n" + b'{"unl')  # a 20-byte chunk, 5 of them sent
        self.close_connection = True

    def _garbled(self) -> None:
        """Bytes that aren't an HTTP answer at all, as the test gives them."""
        self.wfile.write(self.server.garbled)
        self.close_connection = True

    def do_GET(self):
        if self.path == "/v1/status":
            self._json(200, {"ok": True})
        elif self.path == "/v1/garbled":
            self._garbled()
        elif self.path == "/v1/canned":
            self._canned()
        elif self.path == "/v1/short":
            self._short()
        elif self.path == "/v1/cut":
            self._cut()
        else:
            self._json(404, {"message": "no such route"})

    def do_DELETE(self):
        if self.path == "/v1/canned":
            self._canned()
        else:
            self._json(200, {"method": self.command, "path": self.path})

    def _stream_head(self, *, close: bool = False) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Transfer-Encoding", "chunked")
        if close:
            self.send_header("Connection", "close")
        self.end_headers()

    def do_POST(self):
        body = self._body()
        if self.path == "/v1/load":
            self._json(200, {"method": self.command, "got": body, "content_type": self.headers["Content-Type"]})
        elif self.path == "/v1/pin":
            self._json(403, {"message": "not yours"})
        elif self.path == "/v1/canned":
            self._canned()
        elif self.path == "/v1/short":
            self._short()
        elif self.path == "/v1/cut":
            self._cut()
        elif self.path == "/v1/slow":
            # Its head and a line at once, then nothing for gap_s, as a drain behind a long request.
            try:
                self._stream_head()
                self._chunk(b'{"inflight": 1}\n')
                time.sleep(self.server.gap_s)
                self._chunk(b'{"unloaded": true}\n')
                self.wfile.write(b"0\r\n\r\n")
            except (BrokenPipeError, ConnectionResetError):
                pass  # the client stopped waiting, as the test meant it to
        elif self.path == "/v1/held":
            # Its head and a line, then it waits for the client to close the connection, and says when it has. With
            # Connection: close, http.client hands the socket to the answer, which the client must close as well.
            self._stream_head(close=body.get("close", False))
            self._chunk(b'{"inflight": 1}\n')
            while self.rfile.read(1):
                pass
            self.server.client_gone.set()
        elif self.path == "/v1/unload":
            # Three NDJSON lines (the gate's /v1/unload sends two, its count at once and its result once the drain is
            # done): the first, then a wait until the test has read it, so a client that waits for the whole answer
            # before yielding anything is caught; then a line split across two chunks, and the last line in the same
            # chunk as the end of that one.
            self._stream_head()
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
    server.gap_s = 0.9
    server.short = (200, 40, b"")
    server.cut = "no last chunk"
    server.garbled = b"HELLO\r\n\r\n"
    server.client_gone = threading.Event()
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


@contextmanager
def _held(path: Path):
    """A socket that listens and never accepts, as systemd holds the gate's while the gate is down."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as held:
        held.bind(str(path))
        held.listen(8)
        yield held


def _connected(held: socket.socket) -> bool:
    """Whether a connection waits on a held socket: whether the client connected at all."""
    held.setblocking(False)
    try:
        connection, _ = held.accept()
    except BlockingIOError:
        return False
    connection.close()
    return True


@contextmanager
def _mute(path: Path):
    """A gate that accepts every connection and never answers: a process that took the socket and hung."""
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    listener.listen(8)
    listener.settimeout(0.05)
    stop, taken = threading.Event(), []

    def accept():
        while not stop.is_set():
            try:
                taken.append(listener.accept()[0])
            except TimeoutError:
                pass

    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(5)
        for connection in taken:
            connection.close()
        listener.close()


def _in_a_thread(call, catch):
    """Runs call in a thread, so a client that waits for ever fails a test rather than hanging it: (what it raised,
    whether it finished within 5 s, how long it took)."""
    raised = []

    def run():
        try:
            call()
        except catch as err:
            raised.append(err)

    worker = threading.Thread(target=run, daemon=True)
    started = time.monotonic()
    worker.start()
    worker.join(5)
    return raised, not worker.is_alive(), time.monotonic() - started


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
    with _held(path):
        raised, finished, took = _in_a_thread(lambda: GateClient(path, 0.5).get("/v1/status"), GateUnavailable)
    assert finished and took < 2
    [error] = raised
    assert str(error) == f"The gate didn't answer on {path} within 0.5 s. {DAN_STEP}"


def test_a_gate_that_accepts_and_never_answers_is_unavailable_even_when_its_stream_may_wait(sockdir):
    # The review's I-1: timeout_s bounds the connection and the answer's head always; then_s=None lets only the lines
    # after the head wait, as long as a drain takes. So a gate that hangs is still GateUnavailable in time.
    path = sockdir / "mute.sock"
    with _mute(path):
        raised, finished, took = _in_a_thread(
            lambda: list(GateClient(path, 0.5).stream("/v1/unload", {"model": "coder"}, then_s=None)), GateUnavailable)
    assert finished and took < 2
    [error] = raised
    assert str(error) == f"The gate didn't answer on {path} within 0.5 s. {DAN_STEP}"


def test_a_stream_waits_out_a_quiet_gate_only_when_told_to(sockdir):
    path = sockdir / "g.sock"
    with _serving(path) as server:
        server.gap_s = 0.9  # three times the client's timeout
        client = GateClient(path, 0.3)
        both = [{"inflight": 1}, {"unloaded": True}]
        assert list(client.stream("/v1/slow", {"model": "coder"}, then_s=None)) == both
        assert list(client.stream("/v1/slow", {"model": "coder"}, then_s=2)) == both
        # By default the lines after the head wait as long as the head may.
        lines = client.stream("/v1/slow", {"model": "coder"})
        assert next(lines) == {"inflight": 1}
        with pytest.raises(GateUnavailable) as raised:
            next(lines)
        assert str(raised.value) == f"The gate's stream on {path} sent no line for 0.3 s. {DAN_STEP}"


def test_closing_a_stream_closes_its_connection_even_before_its_first_line(sockdir):
    path = sockdir / "g.sock"
    with _serving(path) as server:
        lines = GateClient(path, 5).stream("/v1/held", {"model": "coder"})
        lines.close()
        assert server.client_gone.wait(2)
        # After a line, and as a context manager, too.
        server.client_gone.clear()
        with GateClient(path, 5).stream("/v1/held", {"model": "coder"}) as lines:
            assert next(lines) == {"inflight": 1}
        assert server.client_gone.wait(2)
        # And an answer that ends its connection itself.
        server.client_gone.clear()
        lines = GateClient(path, 5).stream("/v1/held", {"model": "coder", "close": True})
        lines.close()
        assert server.client_gone.wait(2)  # with `lines` still held, so it is close() that closed it, not the GC


def test_an_empty_2xx_answer_reads_as_an_empty_object(sockdir):
    path = sockdir / "g.sock"
    with _serving(path) as server:
        server.canned = (204, b"")
        assert GateClient(path, 5).delete("/v1/canned") == {}
        server.canned = (200, b"")
        assert GateClient(path, 5).post("/v1/canned", {"model": "coder"}) == {}


@pytest.mark.parametrize(("status", "data"), [
    pytest.param(200, b"", id="a-head-only"),
    pytest.param(200, b'{"seconds": 24}', id="a-whole-object-short-of-its-length"),
    pytest.param(200, b'{"seconds": 2', id="half-a-body"),
    pytest.param(409, b'{"message": "Not lo', id="half-a-refusal"),
])
def test_an_answer_that_stops_short_of_its_length_reads_as_gate_unavailable(status, data, sockdir):
    # The re-review's N1: a gate that dies between its head and its body is no success, and no refusal either.
    path = sockdir / "g.sock"
    with _serving(path) as server:
        server.short = (status, 40, data)
        for call in (lambda: GateClient(path, 5).get("/v1/short"),
                     lambda: GateClient(path, 5).post("/v1/short", {"model": "coder"})):
            with pytest.raises(GateUnavailable) as raised:
                call()
            assert str(raised.value) == f"The gate stopped answering on {path}: its answer broke off. {DAN_STEP}"


@pytest.mark.parametrize("how", ["no last chunk", "mid-chunk", "short of its length"])
def test_a_stream_cut_off_partway_reads_as_gate_unavailable_not_its_end(how, sockdir):
    # A gate that dies mid-drain: the lines that came are read, and then the stream says it broke off, never ends as
    # if it were done.
    path = sockdir / "g.sock"
    with _serving(path) as server:
        server.cut = how
        lines = GateClient(path, 5).stream("/v1/cut", {"model": "coder"}, then_s=None)
        assert next(lines) == {"inflight": 1}
        with pytest.raises(GateUnavailable) as raised:
            next(lines)
        assert str(raised.value) == f"The gate stopped answering on {path}: its answer broke off. {DAN_STEP}"


@pytest.mark.parametrize("garbled", [
    pytest.param(b"HELLO\r\n\r\n", id="a-status-line-that-isnt-http"),
    pytest.param(b"HTTP/1.1 200 OK\r\n" + b"X" * 70000 + b"\r\n\r\n", id="a-header-line-too-long"),
    pytest.param(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n" + b"f" * 70000 + b"\r\n",
                 id="a-chunk-size-line-too-long"),
])
def test_an_answer_the_client_cant_read_is_worded_never_named(garbled, sockdir):
    # http.client's own errors for an answer that isn't HTTP read as plain words, never a class name.
    path = sockdir / "g.sock"
    with _serving(path) as server:
        server.garbled = garbled
        client = GateClient(path, 5)
        for call in (lambda: client.get("/v1/garbled"), lambda: list(client.stream("/v1/garbled"))):
            with pytest.raises(GateUnavailable) as raised:
                call()
            assert str(raised.value) == (f"The gate stopped answering on {path}: its answer wasn't one the client "
                                         f"could read. {DAN_STEP}")


@pytest.mark.parametrize(("route", "holds"), [
    pytest.param("/v1/logs/my model", "a space", id="space"),
    pytest.param("/v1/logs/modèle", "'è'", id="beyond-ascii"),
    pytest.param("/v1/logs/x\x1b[2J", "'\\x1b'", id="control"),
])
def test_a_route_that_cant_be_sent_is_refused_as_the_callers_before_connecting(route, holds, sockdir):
    # A name Dan gave, not the gate, is at fault, so the sentence says so, and no connection is made.
    path = sockdir / "held.sock"
    with _held(path) as held:
        with pytest.raises(GateInputError) as raised:
            GateClient(path, 1).get(route)
        assert not _connected(held)
    assert isinstance(raised.value, ValueError)  # so cli.main prints it as it does any refusal of its input
    assert str(raised.value) == (f"Can't ask the gate for {route!r}: it holds {holds}, which a request's path can't "
                                 "carry. Check the name given.")
    assert "\x1b" not in str(raised.value)  # shown escaped, never sent to the terminal as it is


def test_a_body_that_cant_be_sent_is_refused_before_connecting(sockdir):
    # parse_size's Decimal, say (Task 30 sends float(size)): refused before any connection, so none is left open.
    path = sockdir / "held.sock"
    with _held(path) as held:
        with pytest.raises(TypeError):
            GateClient(path, 1).post("/v1/make-room/plan", {"size_gib": Decimal("41")})
        assert not _connected(held)


def test_an_answer_or_a_line_past_its_bound_is_a_gate_error_not_a_gate_thats_down(sockdir):
    path = sockdir / "g.sock"
    with _serving(path) as server:
        client = GateClient(path, 5)
        at = "x" * (ANSWER_BOUND - len('{"a": ""}'))
        server.canned = (200, json.dumps({"a": at}).encode())  # exactly 1 MiB: read
        assert client.get("/v1/canned") == {"a": at}
        server.canned = (200, json.dumps({"a": at + "x"}).encode())
        with pytest.raises(GateError) as raised:
            client.get("/v1/canned")
        # A plain GateError: the gate is up and answering, so no caller takes it for a gate that is down (Task 23's
        # direct release of the brake's hold does that only on GateUnavailable).
        assert type(raised.value) is GateError
        assert str(raised.value) == (f"The gate's answer on {path} was larger than 1 MiB, more than any of its answers "
                                     f"holds. {DAN_STEP}")
        line = json.dumps({"a": "x" * (LINE_BOUND - len('{"a": ""}\n'))}).encode() + b"\n"  # exactly 64 KiB: read
        over = json.dumps({"a": "y" * (LINE_BOUND + 1 - len('{"a": ""}\n'))}).encode() + b"\n"
        assert len(over) == LINE_BOUND + 1  # exactly one byte past the bound, so an off-by-one fails this
        server.canned = (200, line + over)
        lines = client.stream("/v1/canned")
        assert next(lines) == json.loads(line)
        with pytest.raises(GateError) as raised:
            next(lines)
        assert type(raised.value) is GateError
        assert str(raised.value) == (f"A line of the gate's stream on {path} was longer than 64 KiB, more than any of "
                                     f"its lines holds. {DAN_STEP}")


def test_a_closed_socket_says_who_may_use_it(monkeypatch):
    # Who the socket is for, and the next step for whoever was refused (the controller's ruling at Task 10's review),
    # by whether this account is in the socket's group (the group database) and whether this login has it yet.
    def refuse(self, address):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(socket.socket, "connect", refuse)

    def said(path, member, now):
        monkeypatch.setattr(gateclient, "_membership", lambda group: (member, now))
        with pytest.raises(GateForbidden) as raised:
            GateClient(path, 1).get("/v1/status")
        return str(raised.value)

    control, status = paths.GATE_CONTROL_SOCKET, paths.GATE_STATUS_SOCKET
    refused = f"The gate's control socket, {control}, refused this login"
    assert said(control, False, False) == (f"{refused}: only spark-admin's members can use it, so that is Dan's "
                                           "command, which runs as Dan, not as agent.")
    assert said(control, True, False) == (f"{refused}: this account joined spark-admin after the login began. Log in "
                                          "again, then run it.")
    assert said(control, True, True) == (f"{refused}, though it is in spark-admin: the socket's permissions are "
                                         f"wrong. {DAN_STEP}")
    assert said(status, False, False) == (f"The gate's status socket, {status}, refused this login: only "
                                          "spark-users' members can use it, and Dan decides who they are.")
    # A socket that is neither of the two: the text names both, and which group each is for.
    assert said(Path("/run/elsewhere.sock"), False, False) == (
        "/run/elsewhere.sock refused this login: the gate's status socket is for members of spark-users, and its "
        "control socket for members of spark-admin.")


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
    # Membership is the account's, by the group database: a member who joined since this login began is still Dan.
    admin = SimpleNamespace(gr_gid=990, gr_mem=[])
    monkeypatch.setattr(gateclient.grp, "getgrnam", lambda name: admin if name == "spark-admin" else None)
    monkeypatch.setattr(gateclient.pwd, "getpwuid", lambda uid: SimpleNamespace(pw_name="chendaniely", pw_gid=1000))
    monkeypatch.setattr(gateclient.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(gateclient.os, "getuid", lambda: 1000)
    monkeypatch.setattr(gateclient.os, "getegid", lambda: 1000)
    monkeypatch.setattr(gateclient.os, "getgroups", lambda: [1000, 990])
    assert WORDS() == "dan" and gateclient._membership("spark-admin") == (True, True)
    monkeypatch.setattr(gateclient.os, "getgroups", lambda: [1000])
    assert WORDS() == "agent" and gateclient._membership("spark-admin") == (False, False)
    admin.gr_mem = ["chendaniely"]  # joined since this login began
    assert WORDS() == "dan" and gateclient._membership("spark-admin") == (True, False)
    admin.gr_mem = []
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
        # A route that answers once, as a pin on a model already loaded does, reads through stream() as its one line,
        # so the CLI reads /v1/pin through stream() whether or not the pin loads first.
        assert list(client.stream("/v1/load", {"model": "coder"})) == [
            {"method": "POST", "got": {"model": "coder"}, "content_type": "application/json"}]
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
    # Every non-2xx answer of the gate's: the message GateRefused shows, and its code.
    assert gateproto.GateRefusalBody.__required_keys__ == {"message", "code"}
    # The progress lines of the streams the CLI waits on: make-room's drains, and a load as it starts (the
    # controller's rulings at Task 10's review and re-review), each worded by Task 7's `unloading` and `load_started`.
    assert gateproto.MakeRoomProgress.__required_keys__ == {"model", "label", "inflight"}
    assert gateproto.LoadProgress.__required_keys__ == {"model", "label", "last_s"}
    # A pin's result, as Task 7's `pinned` words it; a pin that loads streams a LoadProgress before it.
    assert gateproto.PinConfirmation.__required_keys__ == {"label", "until", "loaded_s", "command"}
    # A load, or a pin that loads, that waits for the slot or for memory says so before it starts (the controller's
    # ruling at Task 10's second re-review): the fields Task 7's `waiting` words a reason with.
    assert gateproto.WaitProgress.__required_keys__ == {
        "model", "label", "why", "needed_gib", "free_gib", "loading_label", "release_waits_for_dan", "release_after_s"}
    # An unload sent whose model stays stopping says so every 15 s, so Dan never waits in silence (the controller's
    # ruling at Task 12's re-review): Task 30 words it, *Still stopping the coder: llama-swap hasn't finished its
    # unload…*.
    assert gateproto.StoppingProgress.__required_keys__ == {"model", "label"}
    assert gateproto.STOPPING_EVERY_S == 15
    # seconds None for a model already loaded, as `messages.loaded` takes it.
    assert typing.get_type_hints(gateproto.LoadConfirmation)["seconds"] == float | None


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


def test_a_models_pin_round_trips_with_an_end_or_without():
    # The controller's ruling at Task 10: `pinned` says whether there is a pin, and `pinned_until` only when it ends,
    # None for no end, as Task 7's `pinned` confirmation takes it (and make-room's list marks `pinned`).
    hints = typing.get_type_hints(gateproto.ModelView)
    assert hints["pinned"] is bool and hints["pinned_until"] == float | None
    now = datetime(2026, 10, 8, 9, 0).astimezone()
    six = datetime(2026, 10, 8, 18, 0).astimezone().timestamp()
    coder = {"name": "qwen3.8-27b", "label": "the coder", "resident": False, "footprint_gib": 41, "state": "ready",
             "inflight": 0, "oldest_request_s": None, "last_use": six - 600, "sessions": [], "brake_mark": None}
    for until, words in ((six, "The coder stays loaded until 18:00. `spark unpin coder` ends the pin."),
                         (None, "The coder stays loaded, with no end set. `spark unpin coder` ends the pin.")):
        view = {**coder, "pinned": True, "pinned_until": until}
        assert set(view) == gateproto.ModelView.__required_keys__
        view = json.loads(json.dumps(view))  # as GET /v1/status and `spark status --json` carry it
        assert view["pinned"] is True and view["pinned_until"] == until
        assert messages.pinned(view["label"], view["pinned_until"], None, "coder", now=now) == words


PATHS = ("GATE_STATUS_SOCKET", "GATE_CONTROL_SOCKET", "GATE_STATE", "LAUNCH", "WHISPER_TMP", "VALUES", "FRONT_URL",
         "LLAMASWAP_URL")
READ_PATHS = f"import json; from spark import paths; print(json.dumps({{n: str(getattr(paths, n)) for n in {PATHS}}}))"


def _paths(**overrides: str) -> dict[str, str]:
    # paths reads the environment once, at import, so each reading is a process of its own, given no SPARK_ variable
    # but the overrides, whatever the shell running the tests exports.
    environment = {name: value for name, value in os.environ.items() if not name.startswith("SPARK_")}
    result = subprocess.run([sys.executable, "-c", READ_PATHS], env={**environment, **overrides},
                            capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_the_gates_paths_default_to_the_plans_and_each_variable_overrides_it(monkeypatch):
    monkeypatch.setenv("SPARK_LLAMASWAP_URL", "http://127.0.0.1:1")  # a shell's own, which the defaults' reading drops
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

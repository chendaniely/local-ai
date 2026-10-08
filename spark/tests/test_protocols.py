"""Task 9's uvicorn protocol: each caller's uid from SO_PEERCRED, and the deadline for a request's head and for a body
nobody reads. A real uvicorn (the locked 0.54.0), with the services' own settings (serve's), serves each test on a
short AF_UNIX path or on 127.0.0.1, in a thread of its own, or, for the forwarded headers, through serve.run_servers
in a process of its own; this file and test_sockets.py run first on a uvicorn bump (website/how-to/updates.md), since
protocols.py subclasses its internals."""

import asyncio
import base64
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
import uvicorn
import uvicorn.protocols.websockets.auto
from uvicorn.protocols.http.h11_impl import H11Protocol

from spark import protocols, serve

LINUX_ONLY = pytest.mark.skipif(sys.platform != "linux",
                                reason="SO_PEERCRED is Linux's, and the gate runs only on the Spark")
HEADER_TIMEOUT_S = 0.5
TRICKLE_S = 0.1  # a byte this often, well inside the deadline: a deadline each read put back would never fire
# A WebSocket handshake's request. Its nonce, 16 bytes in base64 as RFC 6455 asks, is built from a stand-in phrase.
NONCE = base64.b64encode(b"spark stand-in!!")
UPGRADE = (b"GET / HTTP/1.1\r\nHost: spark\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n"
           b"Sec-WebSocket-Key: " + NONCE + b"\r\nSec-WebSocket-Version: 13\r\n\r\n")


@pytest.fixture
def sockdir():
    """A short folder for Unix sockets: macOS caps an AF_UNIX path at 104 bytes, and pytest's tmp_path there is
    longer."""
    path = Path(tempfile.mkdtemp(prefix="sk", dir="/tmp"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


async def _answer(send, body: bytes) -> None:
    await send({"type": "http.response.start", "status": 200,
                "headers": [(b"content-type", b"text/plain"), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


async def who(scope, receive, send):
    """Answers the caller's credentials as the scope carries them, and as protocols.peer reads them."""
    cred = protocols.peer(scope)
    await _answer(send, json.dumps({"raw": (scope.get("extensions") or {}).get("peer_cred"),
                                    "peer": None if cred is None else [cred.pid, cred.uid, cred.gid]}).encode())


async def refuse(scope, receive, send):
    """Answers 401 at once and reads none of the body, as the front does for a key it doesn't know."""
    await send({"type": "http.response.start", "status": 401, "headers": [(b"content-length", b"0")]})
    await send({"type": "http.response.body", "body": b""})


async def refuse_posts(scope, receive, send):
    """A POST gets 401 at once, its body unread, as `refuse` gives it; anything else is answered `hi`."""
    if scope["method"] == "POST":
        await refuse(scope, receive, send)
    else:
        await _answer(send, b"hi")


async def echo(scope, receive, send):
    """Reads the whole body, however slowly it comes, and answers it."""
    body = b""
    while True:
        message = await receive()
        body += message.get("body", b"")
        if not message.get("more_body"):
            break
    await _answer(send, body)


class StandInWebSocket(asyncio.Protocol):
    """What a WebSocket library's protocol does with the upgrade uvicorn hands it (h11_impl.py's
    handle_websocket_upgrade): it takes the connection over and answers 101, serving the app uvicorn's config loaded,
    so neither the connection's uid nor its header deadline would follow the request there."""

    def __init__(self, config, server_state, app_state, _loop=None):
        self.transport = None
        self.answered = False

    def connection_made(self, transport):
        self.transport = transport

    def data_received(self, data):
        if not self.answered:
            self.answered = True
            self.transport.write(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                                 b"Connection: Upgrade\r\n\r\n")


@pytest.fixture
def websocket_library(monkeypatch):
    """As if a WebSocket library were installed, which uvicorn's ws="auto" would then take up."""
    monkeypatch.setattr(uvicorn.protocols.websockets.auto, "AutoWebSocketsProtocol", StandInWebSocket)


@contextmanager
def serving(app, listener: socket.socket, protocol: type, *, keep_alive_s: float | None = None):
    """A real uvicorn serving `app` on `listener` through `protocol`, with the services' own settings (serve's), on its
    own loop in a thread of its own."""
    config = serve._config(app, protocol, graceful_s=1)
    if keep_alive_s is not None:
        config.timeout_keep_alive = keep_alive_s
    server = uvicorn.Server(config)
    thread = threading.Thread(target=lambda: asyncio.run(server.serve(sockets=[listener])), daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert thread.is_alive() and time.monotonic() < deadline, "uvicorn didn't start"
        time.sleep(0.01)
    try:
        yield
    finally:
        server.should_exit = True
        thread.join(10)
        assert not thread.is_alive(), "uvicorn didn't stop"


def _unix_listener(path: Path) -> socket.socket:
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    listener.listen()
    return listener


def _tcp_listener() -> socket.socket:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    return listener


def _connect(listener: socket.socket) -> socket.socket:
    client = socket.socket(listener.family, socket.SOCK_STREAM)
    client.settimeout(5)
    client.connect(listener.getsockname())
    return client


def _read_response(client: socket.socket) -> tuple[bytes, bytes]:
    """One response, read by its content-length, so the connection can carry another: (status line, body)."""
    lines, body = _response(client)
    return lines[0], body


def _response(client: socket.socket) -> tuple[list[bytes], bytes]:
    """One response, read by its content-length (none: no body): (its head's lines, its body)."""
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = client.recv(65536)
        assert chunk, f"closed before a response: {data!r}"
        data += chunk
    head, _, body = data.partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    length = next((int(line.split(b":", 1)[1]) for line in lines if line.lower().startswith(b"content-length:")), 0)
    while len(body) < length:
        chunk = client.recv(65536)
        assert chunk, "closed mid-body"
        body += chunk
    return lines, body


def _get(listener: socket.socket, app_path: str = "/") -> tuple[bytes, bytes]:
    with _connect(listener) as client:
        client.sendall(f"GET {app_path} HTTP/1.1\r\nHost: spark\r\nConnection: close\r\n\r\n".encode())
        return _read_response(client)


def _trickle_until_closed(client: socket.socket, byte: bytes, limit_s: float) -> float:
    """Sends `byte` every TRICKLE_S until the server closes the connection: how long that took, from this call; fails
    past `limit_s`."""
    started = time.monotonic()
    client.settimeout(TRICKLE_S)
    while time.monotonic() - started < limit_s:
        try:
            client.sendall(byte)
            if client.recv(65536) == b"":
                return time.monotonic() - started
            pytest.fail("the server answered instead of closing")
        except TimeoutError:
            continue
        except (BrokenPipeError, ConnectionResetError):
            return time.monotonic() - started
    pytest.fail(f"the connection was still open after {limit_s} s of trickled bytes")


def _closed_after(client: socket.socket, limit_s: float) -> float:
    """How long the server took to close `client`'s connection, which sends nothing more; fails past `limit_s`."""
    started = time.monotonic()
    client.settimeout(limit_s)
    try:
        assert client.recv(65536) == b"", "the server answered instead of closing"
    except TimeoutError:
        pytest.fail(f"the connection was still open after {limit_s} s")
    return time.monotonic() - started


@LINUX_ONLY
def test_the_gate_receives_each_callers_uid(sockdir, websocket_library):
    with _unix_listener(sockdir / "gate.sock") as listener, \
            serving(who, listener, protocols.make_protocol(peer_cred=True, header_timeout_s=None)):
        status, body = _get(listener)
        with _connect(listener) as client:  # a WebSocket upgrade is never taken, so it carries the uid too
            client.sendall(UPGRADE)
            upgrade_status, upgrade_body = _read_response(client)
    assert status == upgrade_status == b"HTTP/1.1 200 OK"
    for answer in (json.loads(body), json.loads(upgrade_body)):
        assert answer["raw"] == {"pid": os.getpid(), "uid": os.getuid(), "gid": os.getegid()}
        assert answer["peer"] == [os.getpid(), os.getuid(), os.getegid()]


def test_peer_cred_refuses_off_linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    with pytest.raises(RuntimeError, match="SO_PEERCRED"):
        protocols.make_protocol(peer_cred=True, header_timeout_s=None)
    assert issubclass(protocols.make_protocol(peer_cred=False, header_timeout_s=10), H11Protocol)


@LINUX_ONLY
def test_a_tcp_request_carries_no_peer_cred():
    with _tcp_listener() as listener, \
            serving(who, listener, protocols.make_protocol(peer_cred=True, header_timeout_s=None)):
        status, body = _get(listener)
    assert status == b"HTTP/1.1 200 OK"
    assert json.loads(body) == {"raw": None, "peer": None}


# serve.run_servers, as the front and the gate run it, in a process of its own (it takes SIGTERM and SIGINT): an app
# answering the scope's client address and scheme, and the type of every scope it was called with, on 127.0.0.1. It
# prints the port it listens on.
FORWARDED = textwrap.dedent(
    """
    import json, socket
    from spark import protocols, serve

    seen = []

    async def app(scope, receive, send):
        seen.append(scope["type"])
        if scope["type"] != "http":
            return  # a lifespan scope, which lifespan="off" never sends
        body = json.dumps({"client": scope["client"][0], "scheme": scope["scheme"], "seen": seen}).encode()
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    print(listener.getsockname()[1], flush=True)
    serve.run_servers([(app, [listener])], protocol=protocols.make_protocol(peer_cred=False, header_timeout_s=10),
                      graceful_s=0.5)
    """
)


def test_forwarded_headers_change_nothing():
    # uvicorn's default, proxy_headers=True, trusts X-Forwarded-For and X-Forwarded-Proto from 127.0.0.1 and ::1, so
    # any local process, agent's included, could make the front or the gate see a forged address or scheme.
    child = subprocess.Popen([sys.executable, "-c", FORWARDED], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True)
    try:
        port = int(child.stdout.readline())
        with socket.create_connection(("127.0.0.1", port), timeout=5) as client:
            client.sendall(b"GET / HTTP/1.1\r\nHost: spark\r\nX-Forwarded-For: 203.0.113.9\r\n"
                           b"X-Forwarded-Proto: https\r\nConnection: close\r\n\r\n")
            lines, body = _response(client)
        assert lines[0] == b"HTTP/1.1 200 OK"
        assert json.loads(body) == {"client": "127.0.0.1", "scheme": "http", "seen": ["http"]}  # and no lifespan
        # run_servers' other settings, seen from outside: no `server: uvicorn` and no `date:` on any answer.
        assert not [line for line in lines[1:] if line.lower().startswith((b"server:", b"date:"))]
        child.send_signal(signal.SIGTERM)
        _, err = child.communicate(timeout=10)
        assert child.returncode == 0, err
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=10)


def test_a_connection_that_never_sends_its_headers_is_closed():
    with _tcp_listener() as listener, \
            serving(echo, listener, protocols.make_protocol(peer_cred=False, header_timeout_s=HEADER_TIMEOUT_S)):
        with _connect(listener) as client:
            client.sendall(b"GET / HTTP/1.1\r\n")
            took = _closed_after(client, 2)
        assert took >= HEADER_TIMEOUT_S - 0.1  # closed by the deadline, not at once

        with _connect(listener) as silent:  # a connection that sends nothing at all, the same
            assert _closed_after(silent, 2) >= HEADER_TIMEOUT_S - 0.1


def test_a_request_that_sends_its_headers_in_time_is_served():
    with _tcp_listener() as listener, \
            serving(echo, listener, protocols.make_protocol(peer_cred=False, header_timeout_s=HEADER_TIMEOUT_S)):
        status, body = _get(listener)
    assert status == b"HTTP/1.1 200 OK" and body == b""


def test_a_slow_body_after_timely_headers_is_served():
    chunks = [b"spark-", b"body-", b"comes-", b"in-", b"six-", b"parts"]
    whole = b"".join(chunks)
    with _tcp_listener() as listener, \
            serving(echo, listener, protocols.make_protocol(peer_cred=False, header_timeout_s=HEADER_TIMEOUT_S)):
        with _connect(listener) as client:
            client.sendall(f"POST / HTTP/1.1\r\nHost: spark\r\nContent-Length: {len(whole)}\r\n"
                           "Connection: close\r\n\r\n".encode())
            for chunk in chunks:  # 1.5 s in all, three times the header deadline
                time.sleep(0.25)
                client.sendall(chunk)
            status, body = _read_response(client)
    assert status == b"HTTP/1.1 200 OK"
    assert body == whole


def test_a_later_request_on_the_connection_gets_the_same_deadline():
    # uvicorn's own keep-alive timer stops at a request's first byte, so without this, a client could send one request
    # at once and then keep its connection open forever by sending the next one's headers a little at a time.
    with _tcp_listener() as listener, \
            serving(echo, listener, protocols.make_protocol(peer_cred=False, header_timeout_s=HEADER_TIMEOUT_S)):
        with _connect(listener) as client:
            client.sendall(b"GET / HTTP/1.1\r\nHost: spark\r\n\r\n")
            assert _read_response(client) == (b"HTTP/1.1 200 OK", b"")
            client.sendall(b"GET / HTTP/1.1\r\n")
            assert _closed_after(client, 2) >= HEADER_TIMEOUT_S - 0.1


def test_an_answered_request_cant_hold_its_connection_with_a_trickled_body():
    # An app that answers before reading the body, as the front's 401 does. uvicorn starts the next request only once
    # that body ends, and each byte of it stops uvicorn's keep-alive timer, so without the deadline a client refused at
    # once could keep its connection for good by sending the body a byte at a time. A byte every TRICKLE_S, inside the
    # deadline, shows the deadline runs from the first of them and no byte puts it back.
    with _tcp_listener() as listener, \
            serving(refuse, listener, protocols.make_protocol(peer_cred=False, header_timeout_s=HEADER_TIMEOUT_S),
                    keep_alive_s=2):
        with _connect(listener) as client:
            client.sendall(b"POST / HTTP/1.1\r\nHost: spark\r\nContent-Length: 1000\r\n\r\n")
            assert _read_response(client)[0] == b"HTTP/1.1 401 Unauthorized"
            took = _trickle_until_closed(client, b"x", limit_s=3)
    assert HEADER_TIMEOUT_S - 0.1 <= took <= HEADER_TIMEOUT_S + 0.5


def test_a_head_sent_a_byte_at_a_time_is_closed_by_its_deadline():
    # The same for a request's head: the deadline runs from the connection's start, and the bytes that trickle in,
    # each well inside it, never put it back.
    with _tcp_listener() as listener, \
            serving(echo, listener, protocols.make_protocol(peer_cred=False, header_timeout_s=HEADER_TIMEOUT_S)):
        with _connect(listener) as client:  # its deadline starts as the connection opens, a moment before this
            client.sendall(b"GET / HTTP/1.1\r\nHost: spark\r\nX-Slow: ")
            took = _trickle_until_closed(client, b"a", limit_s=3)
    assert HEADER_TIMEOUT_S - 0.1 <= took <= HEADER_TIMEOUT_S + 0.5


def test_a_client_that_finishes_an_unread_body_at_once_is_served_next():
    # After an early answer, the next request's deadline runs from the first read after that answer: a client that
    # sends the rest of the body at once, then its next request, is served, as keep-alive allows.
    with _tcp_listener() as listener, \
            serving(refuse_posts, listener,
                    protocols.make_protocol(peer_cred=False, header_timeout_s=HEADER_TIMEOUT_S)):
        with _connect(listener) as client:
            client.sendall(b"POST / HTTP/1.1\r\nHost: spark\r\nContent-Length: 10\r\n\r\n")
            assert _read_response(client)[0] == b"HTTP/1.1 401 Unauthorized"
            client.sendall(b"01234")  # the rest of the body in two reads, so one ends with the body still owed
            time.sleep(0.05)
            client.sendall(b"56789" + b"GET / HTTP/1.1\r\nHost: spark\r\n\r\n")
            assert _read_response(client) == (b"HTTP/1.1 200 OK", b"hi")


def test_a_websocket_upgrade_stays_a_plain_request_on_its_connection(websocket_library):
    # ws="none": were a WebSocket library ever installed, an upgrade would leave this protocol for the library's,
    # with the app uvicorn loaded rather than the connection's own, so without its uid and its header deadline.
    with _tcp_listener() as listener, \
            serving(echo, listener, protocols.make_protocol(peer_cred=False, header_timeout_s=HEADER_TIMEOUT_S)):
        with _connect(listener) as client:
            client.sendall(UPGRADE)
            assert _read_response(client) == (b"HTTP/1.1 200 OK", b"")  # the app answered it: no 101
            client.sendall(b"GET / HTTP/1.1\r\n")  # and the connection is still this protocol's
            assert _closed_after(client, 2) >= HEADER_TIMEOUT_S - 0.1

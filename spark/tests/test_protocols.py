"""Task 9's uvicorn protocol: each caller's uid from SO_PEERCRED, and a deadline for a request's headers. A real
uvicorn (the locked 0.54.0) serves each test on a short AF_UNIX path or on 127.0.0.1, in a thread of its own, or, for
the forwarded headers, through serve.run_servers in a process of its own; this file and test_sockets.py run first on
a uvicorn bump (website/how-to/updates.md), since protocols.py subclasses its internals."""

import asyncio
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
from uvicorn.protocols.http.h11_impl import H11Protocol

from spark import protocols

LINUX_ONLY = pytest.mark.skipif(sys.platform != "linux",
                                reason="SO_PEERCRED is Linux's, and the gate runs only on the Spark")
HEADER_TIMEOUT_S = 0.5


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


async def echo(scope, receive, send):
    """Reads the whole body, however slowly it comes, and answers it."""
    body = b""
    while True:
        message = await receive()
        body += message.get("body", b"")
        if not message.get("more_body"):
            break
    await _answer(send, body)


@contextmanager
def serving(app, listener: socket.socket, protocol: type):
    """A real uvicorn serving `app` on `listener` through `protocol`, on its own loop in a thread of its own."""
    config = uvicorn.Config(app, http=protocol, lifespan="off", log_config=None, access_log=False,
                            proxy_headers=False, timeout_graceful_shutdown=1)
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
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = client.recv(65536)
        assert chunk, f"closed before a response: {data!r}"
        data += chunk
    head, _, body = data.partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    length = next(int(line.split(b":", 1)[1]) for line in lines if line.lower().startswith(b"content-length:"))
    while len(body) < length:
        chunk = client.recv(65536)
        assert chunk, "closed mid-body"
        body += chunk
    return lines[0], body


def _get(listener: socket.socket, app_path: str = "/") -> tuple[bytes, bytes]:
    with _connect(listener) as client:
        client.sendall(f"GET {app_path} HTTP/1.1\r\nHost: spark\r\nConnection: close\r\n\r\n".encode())
        return _read_response(client)


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
def test_the_gate_receives_each_callers_uid(sockdir):
    with _unix_listener(sockdir / "gate.sock") as listener, \
            serving(who, listener, protocols.make_protocol(peer_cred=True, header_timeout_s=None)):
        status, body = _get(listener)
    assert status == b"HTTP/1.1 200 OK"
    answer = json.loads(body)
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
# answering the scope's client address and scheme, on 127.0.0.1. It prints the port it listens on.
FORWARDED = textwrap.dedent(
    """
    import json, socket
    from spark import protocols, serve

    async def app(scope, receive, send):
        body = json.dumps({"client": scope["client"][0], "scheme": scope["scheme"]}).encode()
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
            status, body = _read_response(client)
        assert status == b"HTTP/1.1 200 OK"
        assert json.loads(body) == {"client": "127.0.0.1", "scheme": "http"}
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

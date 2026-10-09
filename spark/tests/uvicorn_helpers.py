"""Real-uvicorn helpers the tests share (test_protocols.py, test_front_app.py): uvicorn serving an app on a listener,
in a thread of its own, with the services' own settings (serve's), and a raw socket's view of what comes back."""

import asyncio
import socket
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
import uvicorn

from spark import serve

HEADER_TIMEOUT_S = 0.5
TRICKLE_S = 0.1  # a byte this often, well inside the deadline: a deadline each read put back would never fire


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


def unix_listener(path: Path) -> socket.socket:
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    listener.listen()
    return listener


def tcp_listener() -> socket.socket:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    return listener


def connect(listener: socket.socket) -> socket.socket:
    client = socket.socket(listener.family, socket.SOCK_STREAM)
    client.settimeout(5)
    client.connect(listener.getsockname())
    return client


def read_response(client: socket.socket) -> tuple[bytes, bytes]:
    """One response, read by its content-length, so the connection can carry another: (status line, body)."""
    lines, body = response(client)
    return lines[0], body


def response(client: socket.socket) -> tuple[list[bytes], bytes]:
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


def get(listener: socket.socket, app_path: str = "/") -> tuple[bytes, bytes]:
    with connect(listener) as client:
        client.sendall(f"GET {app_path} HTTP/1.1\r\nHost: spark\r\nConnection: close\r\n\r\n".encode())
        return read_response(client)


def trickle_until_closed(client: socket.socket, byte: bytes, limit_s: float) -> float:
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


def closed_after(client: socket.socket, limit_s: float) -> float:
    """How long the server took to close `client`'s connection, which sends nothing more; fails past `limit_s`."""
    started = time.monotonic()
    client.settimeout(limit_s)
    try:
        assert client.recv(65536) == b"", "the server answered instead of closing"
    except TimeoutError:
        pytest.fail(f"the connection was still open after {limit_s} s")
    return time.monotonic() - started

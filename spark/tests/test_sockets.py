"""Task 9's sockets from systemd (LISTEN_FDS), and serve.run_servers. Taking sockets and serving them run in a process
of their own, started as systemd starts the services: `listen_fds` reads fds 3 upward and `run_servers` takes SIGTERM
and SIGINT, neither of which a test can do inside pytest's own process. The refusals, which touch no fd, and
QuietServer's check run here. Every socket is a stand-in made here."""

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
import uvicorn

from spark import serve, sockets
from spark.sockets import SocketsError

# Starts a script as systemd starts a socket-activated service: the sockets passed as fds 3 upward, in order, and
# LISTEN_PID the service's own pid, which exec keeps. argv: the passed fds, comma-separated; the script.
LAUNCHER = textwrap.dedent(
    """
    import fcntl, os, sys
    passed = [int(fd) for fd in sys.argv[1].split(",")]
    high = [fcntl.fcntl(fd, fcntl.F_DUPFD, 100) for fd in passed]  # out of the way of 3 upward first
    for fd in passed:
        os.close(fd)
    for i, fd in enumerate(high):
        os.dup2(fd, 3 + i)
        os.close(fd)
    os.environ["LISTEN_PID"] = str(os.getpid())
    os.execv(sys.executable, [sys.executable, "-c", sys.argv[2]])
    """
)


def _launch(script: str, passed: list[socket.socket], names: str) -> subprocess.CompletedProcess:
    fds = [s.fileno() for s in passed]
    environment = {**os.environ, "LISTEN_FDS": str(len(fds)), "LISTEN_FDNAMES": names}
    return subprocess.run([sys.executable, "-c", LAUNCHER, ",".join(map(str, fds)), script], pass_fds=fds,
                          env=environment, capture_output=True, text=True, timeout=30, check=False)


@pytest.fixture
def sockdir():
    """A short folder for Unix sockets: macOS caps an AF_UNIX path at 104 bytes, and pytest's tmp_path there is
    longer."""
    path = Path(tempfile.mkdtemp(prefix="sk", dir="/tmp"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


def _tcp_listener() -> socket.socket:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    return listener


REPORT = textwrap.dedent(
    """
    import json, os, socket
    from spark import sockets
    taken = sockets.listen_fds()
    print(json.dumps({
        "families": {name: socket.AddressFamily(s.family).name for name, s in taken.items()},
        "fds": {name: s.fileno() for name, s in taken.items()},
        "inheritable": [s.get_inheritable() for s in taken.values()],
        "left": sorted(name for name in ("LISTEN_PID", "LISTEN_FDS", "LISTEN_FDNAMES") if name in os.environ),
    }))
    """
)


def test_listen_fds_takes_systemds_named_sockets(sockdir):
    with _tcp_listener() as front, socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as status:
        status.bind(str(sockdir / "status.sock"))
        status.listen()
        result = _launch(REPORT, [front, status], "front:status")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "families": {"front": "AF_INET", "status": "AF_UNIX"},
        "fds": {"front": 3, "status": 4},
        "inheritable": [False, False],  # as sd_listen_fds leaves them: no child process inherits a listener
        "left": [],
    }


def test_listen_fds_refuses_another_processs_sockets():
    env = {"LISTEN_PID": "1", "LISTEN_FDS": "1", "LISTEN_FDNAMES": "front"}
    with pytest.raises(SocketsError, match="LISTEN_PID"):
        sockets.listen_fds(env)
    assert env == {}  # gone on every path, as sd_listen_fds unsets them, so no child process sees them

    with pytest.raises(SocketsError, match="LISTEN_PID"):
        sockets.listen_fds({})


def test_listen_fds_refuses_a_count_and_names_that_disagree():
    pid = str(os.getpid())
    with pytest.raises(SocketsError, match="LISTEN_FDS.*LISTEN_FDNAMES|LISTEN_FDNAMES.*LISTEN_FDS"):
        sockets.listen_fds({"LISTEN_PID": pid, "LISTEN_FDS": "2", "LISTEN_FDNAMES": "front"})
    with pytest.raises(SocketsError, match="LISTEN_FDNAMES"):
        sockets.listen_fds({"LISTEN_PID": pid, "LISTEN_FDS": "1"})
    with pytest.raises(SocketsError, match="LISTEN_FDS"):
        sockets.listen_fds({"LISTEN_PID": pid, "LISTEN_FDS": "two", "LISTEN_FDNAMES": "front:status"})
    with pytest.raises(SocketsError, match="status"):
        sockets.listen_fds({"LISTEN_PID": pid, "LISTEN_FDS": "2", "LISTEN_FDNAMES": "status:status"})


NODELAY = textwrap.dedent(
    """
    import asyncio, socket
    from spark import sockets

    async def main():
        loop = asyncio.get_running_loop()
        accepted = loop.create_future()

        class Probe(asyncio.Protocol):
            def connection_made(self, transport):
                sock = transport.get_extra_info("socket")
                accepted.set_result(sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY))
                transport.close()

        listener = sockets.listen_fds()["front"]
        address = listener.getsockname()
        server = await loop.create_server(Probe, sock=listener)
        _, writer = await asyncio.open_connection(*address)
        print(await asyncio.wait_for(accepted, 10))
        writer.close()
        server.close()

    asyncio.run(main())
    """
)


@pytest.mark.skipif(not hasattr(socket, "SO_PROTOCOL"),
                    reason="socket.socket(fileno=) reads a socket's protocol only where SO_PROTOCOL exists (Linux), "
                           "and the front runs only on the Spark")
def test_a_tcp_socket_from_systemd_keeps_tcp_nodelay():
    # asyncio sets TCP_NODELAY only on a socket it knows is TCP: socket.socket(fileno=) reads the protocol, and
    # socket.fromfd() leaves it 0, so the front's every small streamed chunk would wait on Nagle's algorithm.
    with _tcp_listener() as front:
        result = _launch(NODELAY, [front], "front")
    assert result.returncode == 0, result.stderr
    assert int(result.stdout) != 0


# serve.run_servers, in a process of its own: two apps on two Unix sockets, each answering its letter (at /slow, after
# 0.4 s), and a stream at /stream that never ends. argv: the two socket paths, then the mode: `listening` binds and
# listens, as systemd hands the sockets over; `slow-b` only binds them, so each refuses until its server starts, and
# starts b's server 0.3 s late.
SERVE = textwrap.dedent(
    """
    import asyncio, socket, sys
    from spark import protocols, serve

    def answer(letter):
        async def app(scope, receive, send):
            if scope["path"] == "/stream":
                await send({"type": "http.response.start", "status": 200,
                            "headers": [(b"content-type", b"text/plain")]})
                while True:
                    await send({"type": "http.response.body", "body": letter.encode(), "more_body": True})
                    await asyncio.sleep(0.1)
            if scope["path"] == "/slow":
                await asyncio.sleep(0.4)
            await send({"type": "http.response.start", "status": 200, "headers": [(b"content-length", b"1")]})
            await send({"type": "http.response.body", "body": letter.encode()})
        return app

    listeners = []
    for path in sys.argv[1:3]:
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(path)
        if sys.argv[3] == "listening":
            listener.listen()
        listeners.append(listener)
    if sys.argv[3] == "slow-b":
        start = serve.QuietServer.startup
        async def slow_b(self, sockets=None):
            if sockets[0] is listeners[1]:
                await asyncio.sleep(0.3)
            await start(self, sockets=sockets)
        serve.QuietServer.startup = slow_b
    serve.run_servers([(answer("a"), [listeners[0]]), (answer("b"), [listeners[1]])],
                      protocol=protocols.make_protocol(peer_cred=False, header_timeout_s=10), graceful_s=0.5)
    print("stopped", *(listener.fileno() == -1 for listener in listeners))
    """
)
GRACEFUL_S = 0.5


@contextmanager
def _notify_socket(sockdir: Path):
    """systemd's end of NOTIFY_SOCKET: a datagram socket, and its path."""
    path = str(sockdir / "notify")
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as receiver:
        receiver.bind(path)
        yield receiver, path


def _notified(receiver: socket.socket, timeout: float) -> bytes:
    receiver.settimeout(timeout)
    return receiver.recv(4096)


def _waiting(receiver: socket.socket) -> list[bytes]:
    receiver.setblocking(False)
    messages = []
    while True:
        try:
            messages.append(receiver.recv(4096))
        except BlockingIOError:
            return messages


@contextmanager
def _serving(sockdir: Path, mode: str, notify_path: str):
    child = subprocess.Popen([sys.executable, "-c", SERVE, str(sockdir / "a.sock"), str(sockdir / "b.sock"), mode],
                             env={**os.environ, "NOTIFY_SOCKET": notify_path}, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
    try:
        yield child
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=10)


def _send_get(path: Path, target: str = "/") -> socket.socket:
    """A GET sent over the Unix socket at `path`, its response not yet read."""
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(5)
    client.connect(str(path))
    client.sendall(f"GET {target} HTTP/1.1\r\nHost: spark\r\nConnection: close\r\n\r\n".encode())
    return client


def _body(client: socket.socket) -> bytes:
    """The response's body, read to the end of the connection, which `Connection: close` asks the server to close."""
    with client:
        response = b""
        while chunk := client.recv(65536):
            response += chunk
    head, _, body = response.partition(b"\r\n\r\n")
    assert head.startswith(b"HTTP/1.1 200 "), head
    return body


def _get(path: Path, target: str = "/") -> bytes:
    return _body(_send_get(path, target))


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT], ids=["SIGTERM", "SIGINT"])
def test_run_servers_serves_two_apps_on_two_sockets_and_shuts_down_in_time(sockdir, signum):
    with _notify_socket(sockdir) as (receiver, notify_path), _serving(sockdir, "listening", notify_path) as child:
        assert _notified(receiver, 10) == b"READY=1"
        assert _get(sockdir / "a.sock") == b"a"
        assert _get(sockdir / "b.sock") == b"b"

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
            stream.settimeout(5)
            stream.connect(str(sockdir / "a.sock"))
            stream.sendall(b"GET /stream HTTP/1.1\r\nHost: spark\r\n\r\n")
            assert stream.recv(65536).startswith(b"HTTP/1.1 200 ")  # the stream has started, and never ends
            slow = _send_get(sockdir / "a.sock", "/slow")
            time.sleep(0.1)  # its request is in, and its answer 0.3 s away

            signalled = time.monotonic()
            child.send_signal(signum)
            assert _body(slow) == b"a"  # a request in flight finishes within the graceful time; the stream is cut
            out, err = child.communicate(timeout=10)
            took = time.monotonic() - signalled

        assert child.returncode == 0, err  # returned, not killed by the signal (-15 or -2)
        assert took <= GRACEFUL_S + 1.5
        assert out == "stopped True True\n"  # both servers ran their shutdown, which closes each listener
        assert _notified(receiver, 1) == b"STOPPING=1"


def test_quiet_server_leaves_sigint_and_sigterm_alone():
    # uvicorn 0.54.0's own capture_signals puts its handler in place of whatever was there, and raises any signal it
    # took again on the way out; run_servers' loop handlers are the only ones the services' signals reach.
    def handlers():
        return signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)

    before = handlers()
    plain = uvicorn.Server(uvicorn.Config(None, log_config=None))
    with plain.capture_signals():
        assert handlers() == (plain.handle_exit, plain.handle_exit)  # what the override exists to avoid
    assert handlers() == before

    quiet = serve.QuietServer(uvicorn.Config(None, log_config=None))
    with quiet.capture_signals():
        assert handlers() == before
    assert handlers() == before


def test_run_servers_says_ready_once_both_listen(sockdir):
    with _notify_socket(sockdir) as (receiver, notify_path), _serving(sockdir, "slow-b", notify_path) as child:
        assert _notified(receiver, 10) == b"READY=1"
        # Each socket refuses until its server listens, and b's starts 0.3 s after a's: so both answering, the moment
        # READY=1 arrives, means it waited for both.
        assert _get(sockdir / "a.sock") == b"a"
        assert _get(sockdir / "b.sock") == b"b"

        child.send_signal(signal.SIGTERM)
        _, err = child.communicate(timeout=10)
        assert child.returncode == 0, err
        assert _waiting(receiver) == [b"STOPPING=1"]  # READY=1 came once, not once per server

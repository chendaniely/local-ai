"""Task 9's sd_notify: READY=1, STOPPING=1 and the watchdog's WATCHDOG=1, sent to a datagram socket standing in for
systemd's NOTIFY_SOCKET."""

import asyncio
import contextlib
import os
import shutil
import socket
import sys
import tempfile
import time
import uuid
from pathlib import Path

import anyio
import pytest

from spark import sdnotify


@pytest.fixture
def anyio_backend():
    return "asyncio"  # the services run on asyncio's loop, and watchdog_loop sleeps on it


@pytest.fixture
def sockdir():
    """A short folder for Unix sockets: macOS caps an AF_UNIX path at 104 bytes, and pytest's tmp_path there is
    longer."""
    path = Path(tempfile.mkdtemp(prefix="sk", dir="/tmp"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def notify_socket(sockdir, monkeypatch):
    """systemd's end: a datagram socket bound at NOTIFY_SOCKET, which this process's environment names."""
    path = str(sockdir / "notify")
    receiver = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    receiver.bind(path)
    receiver.setblocking(False)
    monkeypatch.setenv("NOTIFY_SOCKET", path)
    yield receiver
    receiver.close()


def _received(receiver: socket.socket) -> list[bytes]:
    """Every datagram waiting, without waiting for more."""
    messages = []
    while True:
        try:
            messages.append(receiver.recv(4096))
        except BlockingIOError:
            return messages


def test_sd_notify_sends_ready_watchdog_and_stopping(notify_socket):
    env = {"NOTIFY_SOCKET": os.environ["NOTIFY_SOCKET"]}
    assert sdnotify.ready(env) is True
    assert sdnotify.notify("WATCHDOG=1", env) is True
    assert sdnotify.stopping(env) is True
    assert _received(notify_socket) == [b"READY=1", b"WATCHDOG=1", b"STOPPING=1"]


@pytest.mark.skipif(sys.platform != "linux", reason="abstract Unix sockets are Linux's, as systemd is")
def test_sd_notify_reaches_an_abstract_socket():
    name = f"spark-test-{uuid.uuid4().hex}"
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as receiver:
        receiver.bind(f"\0{name}")
        receiver.settimeout(2)
        assert sdnotify.notify("READY=1", {"NOTIFY_SOCKET": f"@{name}"}) is True
        assert receiver.recv(4096) == b"READY=1"


def test_notify_without_notify_socket_is_a_quiet_no():
    assert sdnotify.notify("READY=1", {}) is False
    assert sdnotify.notify("READY=1", {"NOTIFY_SOCKET": ""}) is False
    assert "NOTIFY_SOCKET" not in os.environ  # conftest's environment has none
    assert sdnotify.ready() is False and sdnotify.stopping() is False


def test_the_watchdog_interval_is_half_watchdog_usec_and_only_for_our_pid():
    assert sdnotify.watchdog_interval_s({"WATCHDOG_USEC": "30000000"}) == 15.0
    assert sdnotify.watchdog_interval_s({"WATCHDOG_USEC": "30000000", "WATCHDOG_PID": str(os.getpid())}) == 15.0
    assert sdnotify.watchdog_interval_s({"WATCHDOG_USEC": "30000000", "WATCHDOG_PID": "1"}) is None
    assert sdnotify.watchdog_interval_s({}) is None
    assert sdnotify.watchdog_interval_s() is None  # conftest's environment has none


@pytest.mark.anyio
async def test_the_watchdog_loop_pings_only_while_the_loop_runs(notify_socket):
    with anyio.fail_after(5):
        pinging = asyncio.create_task(sdnotify.watchdog_loop(0.05))
        try:
            await asyncio.sleep(0.3)
            pings = _received(notify_socket)
            assert len(pings) >= 3 and set(pings) == {b"WATCHDOG=1"}

            await asyncio.sleep(0)
            _received(notify_socket)  # what came before the block
            time.sleep(0.3)  # the loop is stuck: nothing runs on it until this returns
            assert _received(notify_socket) == []

            await asyncio.sleep(0.2)
            assert _received(notify_socket)  # and once the loop runs again, so do the pings
        finally:
            pinging.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pinging

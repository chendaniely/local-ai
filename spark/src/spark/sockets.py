"""The sockets systemd holds for the new services (plan.md, *The front and the gate*): `local-ai-front.socket` holds
127.0.0.1:9100, and `local-ai-gate-status.socket` and `local-ai-gate-control.socket` the gate's two Unix sockets, from
boot and across restarts. Each service takes them from LISTEN_FDS, by the names their units give with
FileDescriptorName=, and hands them to uvicorn (serve.run_servers); never uvicorn's own --uds or --fd, which bind
themselves or assume a Unix socket.

systemd's protocol (sd_listen_fds(3)): the sockets are fds 3 upward, LISTEN_FDS says how many, LISTEN_FDNAMES their
names, colon-separated, and LISTEN_PID the process they are for."""

from __future__ import annotations

import os
import socket
from collections.abc import MutableMapping

LISTEN_FDS_START = 3  # SD_LISTEN_FDS_START
FRONT = "front"  # the front's 127.0.0.1:9100
STATUS = "status"  # the gate's status socket, group spark-users
CONTROL = "control"  # the gate's control socket, group spark-admin
VARIABLES = ("LISTEN_PID", "LISTEN_FDS", "LISTEN_FDNAMES")


class SocketsError(Exception):
    """systemd's sockets missing, or the variables that pass them disagreeing; the text says which."""


def listen_fds(env: MutableMapping[str, str] = os.environ, pid: int | None = None) -> dict[str, socket.socket]:
    """The sockets systemd passed this process, by name: each made with socket.socket(fileno=…), which reads its
    family, type and protocol from the socket itself (so asyncio knows a TCP socket as TCP, and sets TCP_NODELAY on
    each connection), and made non-inheritable, as sd_listen_fds leaves them. The three variables are removed from
    `env` whatever the outcome, as sd_listen_fds removes them, so no child process takes them for its own. Everything
    is checked before any fd is touched."""
    pid = os.getpid() if pid is None else pid
    listen_pid, count, names = (env.pop(name, None) for name in VARIABLES)

    if not listen_pid:
        raise SocketsError("LISTEN_PID isn't set, so systemd passed no sockets: start this service through its "
                           ".socket unit")
    if _number(listen_pid) != pid:
        raise SocketsError(f"LISTEN_PID is {listen_pid!r}, not this process's pid ({pid}): the sockets were passed "
                           f"to another process")
    if _number(count) is None:
        raise SocketsError(f"LISTEN_FDS isn't a count of sockets: {count!r}")
    if names is None:
        raise SocketsError("LISTEN_FDNAMES isn't set: each socket unit names its socket with FileDescriptorName=")
    named = names.split(":")
    if len(named) != int(count):
        raise SocketsError(f"LISTEN_FDS says {count} sockets, but LISTEN_FDNAMES names {len(named)}: {names}")
    for name in named:
        if named.count(name) > 1:
            raise SocketsError(f"LISTEN_FDNAMES names {name} more than once: {names}")

    taken = {}
    for fd, name in enumerate(named, start=LISTEN_FDS_START):
        sock = socket.socket(fileno=fd)
        sock.set_inheritable(False)
        taken[name] = sock
    return taken


def _number(text: str | None) -> int | None:
    """A decimal number of ASCII digits, or None (str.isdigit alone takes '²', which int() refuses)."""
    return int(text) if text and text.isascii() and text.isdigit() else None

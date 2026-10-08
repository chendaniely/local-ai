"""systemd's notify protocol for the front and the gate (`Type=notify`, `WatchdogSec=`), standard library only:
`READY=1` once they serve, `STOPPING=1` as they begin to stop, and `WATCHDOG=1` from the event loop, so that a loop
that hangs stops the pings and systemd restarts the service (plan.md, *The front and the gate*).

Each message is one datagram to the socket NOTIFY_SOCKET names: a path, or `@name` for an abstract socket. Run
anywhere else, with no NOTIFY_SOCKET, nothing is sent."""

from __future__ import annotations

import asyncio
import os
import socket
from collections.abc import Mapping


def notify(message: str, env: Mapping[str, str] = os.environ) -> bool:
    """Sends `message` to NOTIFY_SOCKET: True once sent, False, quietly, when no NOTIFY_SOCKET is set. A send that
    fails raises its OSError."""
    address = env.get("NOTIFY_SOCKET")
    if not address:
        return False
    if address.startswith("@"):
        address = "\0" + address[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
        sock.sendto(message.encode("utf-8"), address)
    return True


def ready(env: Mapping[str, str] = os.environ) -> bool:
    """`READY=1`: the service serves."""
    return notify("READY=1", env)


def stopping(env: Mapping[str, str] = os.environ) -> bool:
    """`STOPPING=1`: the service has begun to stop."""
    return notify("STOPPING=1", env)


def watchdog_interval_s(env: Mapping[str, str] = os.environ) -> float | None:
    """How often to send `WATCHDOG=1`: half of WATCHDOG_USEC, as systemd advises, when its unit sets `WatchdogSec=`
    and WATCHDOG_PID, if set, is this process's; otherwise None, no watchdog."""
    usec = env.get("WATCHDOG_USEC")
    if not usec:
        return None
    pid = env.get("WATCHDOG_PID")
    if pid and int(pid) != os.getpid():
        return None
    return int(usec) / 1_000_000 / 2


async def watchdog_loop(interval_s: float) -> None:
    """`WATCHDOG=1` every `interval_s`, sent from the event loop itself, so a loop that is stuck sends none. Runs until
    cancelled. A ping that fails to send is passed over: the watchdog counts it as missed, which is what it is."""
    while True:
        try:
            notify("WATCHDOG=1")
        except OSError:
            pass
        await asyncio.sleep(interval_s)

"""Runs the front's and the gate's uvicorn servers on the sockets systemd holds (plan.md, *The front and the gate*):
one event loop for all of a service's servers, `READY=1` once each listens, the watchdog's pings from that loop, and a
shutdown on SIGTERM or SIGINT that each server bounds with its graceful timeout, below the unit's TimeoutStopSec=.

uvicorn's `Server.serve` takes SIGINT and SIGTERM for itself (`capture_signals`, uvicorn 0.54.0's server.py): it puts
its own handler in place of whatever was there, puts that back on the way out, and raises any signal it took again.
Two such servers in one loop, with nothing else, die by the signal: the second's handler replaces the first's, so the
first hears of it only from the second's raise, and its own raise then meets the default handler (seen on the Spark,
2026-10-08: the process ended by SIGTERM, and by SIGINT), which systemd counts as a failure. QuietServer gives all
that up, and run_servers takes both signals with the loop's own handlers, which tell every server. What else this
relies on, in uvicorn 0.54.0's server.py: `serve(sockets=…)`; `should_exit`, which its main loop reads every 0.1 s;
and `started`, set once a server listens."""

from __future__ import annotations

import asyncio
import contextlib
import signal
import socket
from collections.abc import Generator

import uvicorn

from spark import sdnotify
from spark.protocols import ASGIApp

TESTED_AGAINST = {"pypi:uvicorn": "0.54.0"}
SIGNALS = (signal.SIGTERM, signal.SIGINT)
_STARTED_POLL_S = 0.01


class QuietServer(uvicorn.Server):
    """uvicorn's Server, leaving SIGINT and SIGTERM to whoever runs it."""

    @contextlib.contextmanager
    def capture_signals(self) -> Generator[None, None, None]:
        yield


def run_servers(pairs: list[tuple[ASGIApp, list[socket.socket]]], *, protocol: type, graceful_s: float) -> None:
    """Serves each app on its sockets, all in one event loop, until SIGTERM or SIGINT, then stops every server and
    returns once each has, within `graceful_s` of its own. A server that fails stops the others, and its error is
    raised once they have stopped."""
    asyncio.run(_run_servers(pairs, protocol, graceful_s))


def _config(app: ASGIApp, protocol: type, graceful_s: float) -> uvicorn.Config:
    return uvicorn.Config(
        app,
        http=protocol,
        # Never a WebSocket: were a library for one ever installed, an upgrade would leave `protocol` for the
        # library's, and its request would reach the app without its caller's uid or the header deadline.
        ws="none",
        lifespan="off",
        # uvicorn configures no logging, so its WARNING-and-up lines and tracebacks ("Exception in ASGI application")
        # reach stderr, and so the journal, through logging's last-resort handler. Tasks 19 and 21 set the policy.
        log_config=None,
        access_log=False,  # an access line names the client and the path; the front logs what it means to
        server_header=False,
        date_header=False,
        # uvicorn's default trusts X-Forwarded-For and X-Forwarded-Proto from 127.0.0.1 and ::1, where every local
        # process is, agent's included: none of them may make the front or the gate see a forged address or scheme.
        proxy_headers=False,
        timeout_graceful_shutdown=graceful_s,
    )


async def _run_servers(pairs: list[tuple[ASGIApp, list[socket.socket]]], protocol: type, graceful_s: float) -> None:
    loop = asyncio.get_running_loop()
    servers = [QuietServer(_config(app, protocol, graceful_s)) for app, _ in pairs]
    stopping = False

    def stop() -> None:
        nonlocal stopping
        for server in servers:
            server.should_exit = True
        if not stopping:
            stopping = True
            with contextlib.suppress(OSError):  # systemd sees the process end either way
                sdnotify.stopping()

    for signum in SIGNALS:
        loop.add_signal_handler(signum, stop)
    interval = sdnotify.watchdog_interval_s()
    watchdog = asyncio.create_task(sdnotify.watchdog_loop(interval)) if interval else None
    tasks = [asyncio.create_task(server.serve(sockets=sockets)) for server, (_, sockets) in zip(servers, pairs)]
    try:
        while not all(server.started for server in servers):
            done, _ = await asyncio.wait(tasks, timeout=_STARTED_POLL_S, return_when=asyncio.FIRST_COMPLETED)
            if done:
                break  # a server ended before every one listened: a failure, raised below
        else:
            if not stopping:
                sdnotify.ready()
        await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
    finally:
        stop()
        await asyncio.wait(tasks)
        if watchdog is not None:
            watchdog.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watchdog
        for signum in SIGNALS:
            loop.remove_signal_handler(signum)
    for task in tasks:
        task.result()

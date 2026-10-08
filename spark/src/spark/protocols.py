"""uvicorn's h11 protocol, subclassed for the front and the gate (plan.md, *The front and the gate*): each caller of
the gate known by its uid, read with SO_PEERCRED from the Unix socket it connected to; and a deadline, so a client
can't hold a connection open by sending its request's head a little at a time, nor, once answered, by trickling the
rest of a body nobody will read.

It subclasses uvicorn's internals, which no release promises to keep, so uvicorn is pinned exactly, and a bump runs
test_protocols.py and test_sockets.py first (website/how-to/updates.md). What it relies on, in uvicorn 0.54.0's
uvicorn/protocols/http/h11_impl.py: `connection_made(transport)`, `data_received(data)` and `connection_lost(exc)`,
which it extends; `self.app`, the app each request's cycle runs, read as the request's head arrives (`handle_events`);
`self.conn`, the connection's h11 state, whose client stays IDLE until a request's head is complete; `self.loop` and
`self.transport`; uvicorn's keep-alive timer, which `data_received` stops at every read; and, once the answer is sent,
uvicorn dropping the body's data unread and starting the next request only when both sides are DONE."""

from __future__ import annotations

import asyncio
import socket
import struct
import sys
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any, NamedTuple

import h11
from uvicorn.protocols.http.h11_impl import H11Protocol

TESTED_AGAINST = {"pypi:uvicorn": "0.54.0"}
_UCRED = struct.Struct("iII")  # Linux's struct ucred: pid_t pid, uid_t uid, gid_t gid

Scope = MutableMapping[str, Any]
ASGIApp = Callable[[Scope, Callable[[], Awaitable[Any]], Callable[[Any], Awaitable[None]]], Awaitable[None]]


class PeerCred(NamedTuple):
    """The process at the other end of a Unix socket, as the kernel recorded it when it connected."""

    pid: int
    uid: int
    gid: int


def peer(scope: Scope) -> PeerCred | None:
    """The caller's credentials from a request's scope, or None: a TCP connection, or a protocol without peer_cred."""
    cred = (scope.get("extensions") or {}).get("peer_cred")
    return None if cred is None else PeerCred(pid=cred["pid"], uid=cred["uid"], gid=cred["gid"])


def make_protocol(*, peer_cred: bool, header_timeout_s: float | None) -> type:
    """A subclass of uvicorn's H11Protocol, for uvicorn's `http=`. With `peer_cred`, every request on a Unix socket
    carries its caller's {"pid", "uid", "gid"} as `scope["extensions"]["peer_cred"]`; a TCP connection's carries
    none. With `header_timeout_s`, a connection is closed when a request's head isn't complete in time:
    `header_timeout_s` after the connection opened, or, for a later request on it, after the read that began the wait
    for it. Its body has no such limit while the app may still read it; once the answer is sent, what is left of the
    body gets the same deadline, from the next read."""
    if peer_cred and sys.platform != "linux":
        raise RuntimeError("SO_PEERCRED is Linux's, so a caller's uid can't be read here: the gate runs only on the "
                           "Spark")
    return type("SparkH11Protocol", (_SparkH11Protocol,),
                {"_peer_cred": peer_cred, "_header_timeout_s": header_timeout_s})


class _SparkH11Protocol(H11Protocol):
    _peer_cred: bool = False
    _header_timeout_s: float | None = None
    _header_timer: asyncio.TimerHandle | None = None

    def connection_made(self, transport) -> None:
        super().connection_made(transport)
        if self._peer_cred:
            self._take_peer_cred(transport)
        self._watch_headers()

    def data_received(self, data: bytes) -> None:
        super().data_received(data)
        self._watch_headers()

    def connection_lost(self, exc: Exception | None) -> None:
        self._stop_header_timer()
        super().connection_lost(exc)

    def _watch_headers(self) -> None:
        """Called once the connection opens and after each read. The deadline runs while the client owes something
        nobody is waiting for (`_unawaited`): from the connection's start, and otherwise from the read that leaves it
        so, since uvicorn's keep-alive timer stops at every read. It is set once for each such stretch and never put
        back by the bytes that trickle in. A body the app is still reading, before it answers, has no such limit."""
        if self._header_timeout_s is None:
            return
        if self._unawaited() and not self.transport.is_closing():
            if self._header_timer is None:
                self._header_timer = self.loop.call_later(self._header_timeout_s, self._too_slow)
        else:
            self._stop_header_timer()

    def _unawaited(self) -> bool:
        """h11 has the client IDLE, its request's head not yet complete; or the answer is already sent (ours DONE)
        while the client is still sending a body (SEND_BODY), which uvicorn drops unread and which holds back the
        next request until it ends."""
        their, ours = self.conn.their_state, self.conn.our_state
        return their is h11.IDLE or (ours is h11.DONE and their is h11.SEND_BODY)

    def _too_slow(self) -> None:
        self._header_timer = None
        if self._unawaited() and not self.transport.is_closing():
            self.transport.close()

    def _stop_header_timer(self) -> None:
        if self._header_timer is not None:
            self._header_timer.cancel()
            self._header_timer = None

    def _take_peer_cred(self, transport) -> None:
        sock = transport.get_extra_info("socket")
        if sock is None or sock.family != socket.AF_UNIX:
            return  # TCP: there is no uid to read, and the scope carries none
        try:
            raw = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, _UCRED.size)
        except OSError:
            transport.close()  # a caller the gate can't name is never served
            return
        self.app = _with_peer_cred(self.app, PeerCred(*_UCRED.unpack(raw)))


def _with_peer_cred(app: ASGIApp, cred: PeerCred) -> ASGIApp:
    """`app`, with `cred` in each request's scope: a copy of the scope, so uvicorn's own is left as it made it."""

    async def with_peer_cred(scope: Scope, receive, send) -> None:
        extensions = {**(scope.get("extensions") or {}), "peer_cred": cred._asdict()}
        await app({**scope, "extensions": extensions}, receive, send)

    return with_peer_cred

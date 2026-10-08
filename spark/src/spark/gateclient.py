"""The CLI's client for the gate (Phase 2a; plan.md, *The front and the gate*): JSON over HTTP/1.1 on one of the
gate's Unix sockets, with the standard library's http.client, so `spark status --json`, which 2b's menu bar polls over
SSH, and the other commands that ask the gate import neither uvicorn nor httpx (test_tested_against.py). The routes
and their messages are gateproto's.

A call that gets no usable answer raises a GateError whose text is a sentence: GateUnavailable when there is no socket,
nothing listening on it, or no answer within the timeout, as when systemd holds the socket while the gate is down;
GateForbidden when the socket refuses this login, naming the group it is for; GateRefused when the gate answers with
a status other than 2xx, carrying the status and the answer's body."""

from __future__ import annotations

import http.client
import json
import os
import socket
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, TypeVar

from spark import gateproto, paths

_T = TypeVar("_T")


class GateError(Exception):
    """A call to the gate that got no usable answer; the text says why. A 2xx answer, or a stream's line, that isn't a
    JSON object, as every answer of the gate's is to be (gateproto), raises this itself."""


class GateUnavailable(GateError):
    """No socket, nothing listening on it, or no answer within the timeout: the gate isn't there to ask."""


class GateForbidden(GateError):
    """The socket refused this login (EACCES on connect); the text names the group the socket is for."""


class GateRefused(GateError):
    """The gate answered with a status other than 2xx. `body` is its answer, a JSON object, or {} for an answer that
    isn't one, such as uvicorn's own plain-text 500."""

    def __init__(self, status: int, body: dict[str, Any]) -> None:
        super().__init__(status, body)
        self.status = status
        self.body = body

    def __str__(self) -> str:
        message = self.body.get("message")
        return message if isinstance(message, str) and message else f"The gate answered HTTP {self.status}."


class _UnixConnection(http.client.HTTPConnection):
    """http.client's connection, made to a Unix socket in place of a host and port. The Host header it sends,
    `localhost`, is there because HTTP/1.1 requires one; on a Unix socket it names nothing."""

    def __init__(self, socket_path: Path, timeout_s: float | None) -> None:
        super().__init__("localhost", timeout=timeout_s)
        self.socket_path = socket_path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(self.timeout)
            sock.connect(os.fspath(self.socket_path))
        except BaseException:
            sock.close()
            raise
        self.sock = sock


class GateClient:
    """Calls to one of the gate's sockets, one connection per call. `timeout_s` bounds each wait for the gate: the
    connection, and every read of its answer. None waits as long as the gate takes, for a route whose answer waits on
    a drain, which waits for the requests in flight (`spark unload`)."""

    def __init__(self, path: Path, timeout_s: float | None) -> None:
        self.path = Path(path)
        self.timeout_s = timeout_s

    def get(self, route: str) -> dict[str, Any]:
        return self._call("GET", route, None)

    def post(self, route: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._call("POST", route, body)

    def delete(self, route: str) -> dict[str, Any]:
        return self._call("DELETE", route, None)

    def stream(self, route: str, body: dict[str, Any] | None = None) -> Iterator[dict[str, Any]]:
        """An NDJSON answer, each line as it comes: a POST when a body is given (/v1/unload), else a GET. The call is
        made, and a refusal raised, before this returns; the connection closes when the lines end, or when the
        iterator is closed."""
        method = "GET" if body is None else "POST"
        connection, response = self._open(method, route, body)
        if not 200 <= response.status < 300:
            try:
                raw = self._talk(response.read)
            finally:
                connection.close()
            raise GateRefused(response.status, _refusal_body(raw))
        return self._lines(method, route, connection, response)

    def _call(self, method: str, route: str, body: dict[str, Any] | None) -> dict[str, Any]:
        connection, response = self._open(method, route, body)
        try:
            raw = self._talk(response.read)
        finally:
            connection.close()
        if not 200 <= response.status < 300:
            raise GateRefused(response.status, _refusal_body(raw))
        return _json_object(method, route, raw)

    def _open(self, method: str, route: str,
              body: dict[str, Any] | None) -> tuple[http.client.HTTPConnection, http.client.HTTPResponse]:
        connection = _UnixConnection(self.path, self.timeout_s)
        try:
            connection.connect()
        except PermissionError as err:
            raise GateForbidden(_who_may(self.path)) from err
        except FileNotFoundError as err:
            raise GateUnavailable(f"The gate can't be reached: there is no socket at {self.path}.") from err
        except ConnectionRefusedError as err:
            raise GateUnavailable(f"The gate can't be reached: nothing is listening on {self.path}.") from err
        except TimeoutError as err:
            raise GateUnavailable(self._no_answer()) from err
        except OSError as err:
            raise GateUnavailable(f"The gate can't be reached on {self.path}: {_reason(err)}.") from err
        payload = None if body is None else json.dumps(body, allow_nan=False).encode()
        headers = {} if payload is None else {"Content-Type": "application/json"}

        def ask() -> http.client.HTTPResponse:
            connection.request(method, route, body=payload, headers=headers)
            return connection.getresponse()

        try:
            return connection, self._talk(ask)
        except BaseException:
            connection.close()
            raise

    def _talk(self, step: Callable[[], _T]) -> _T:
        """One step of the exchange once connected: a timeout, or a connection that breaks, is the gate not
        answering."""
        try:
            return step()
        except TimeoutError as err:
            raise GateUnavailable(self._no_answer()) from err
        except (OSError, http.client.HTTPException) as err:
            raise GateUnavailable(f"The gate stopped answering on {self.path}: {_reason(err)}.") from err

    def _lines(self, method: str, route: str, connection: http.client.HTTPConnection,
               response: http.client.HTTPResponse) -> Iterator[dict[str, Any]]:
        try:
            while line := self._talk(response.readline):
                if line.strip():
                    yield _json_object(method, route, line)
        finally:
            connection.close()

    def _no_answer(self) -> str:
        within = "" if self.timeout_s is None else f" within {self.timeout_s:g} s"
        return f"The gate didn't answer on {self.path}{within}."


def _json_object(method: str, route: str, raw: bytes) -> dict[str, Any]:
    try:
        answer = json.loads(raw)
    except ValueError:
        answer = None
    if not isinstance(answer, dict):
        raise GateError(f"The gate's answer to {method} {route} isn't a JSON object.")
    return answer


def _refusal_body(raw: bytes) -> dict[str, Any]:
    try:
        body = json.loads(raw)
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _reason(err: BaseException) -> str:
    if isinstance(err, http.client.RemoteDisconnected):
        return "it closed the connection without answering"
    if isinstance(err, http.client.IncompleteRead):
        return "its answer broke off"
    if isinstance(err, OSError) and err.strerror:
        return err.strerror
    return type(err).__name__


def _who_may(path: Path) -> str:
    """Which group the socket is for, by which of the gate's sockets it is. EACCES is the socket's group (or its
    folder) keeping this login out, so the text says who it is for, not that this login isn't among them: a group
    joined since logging in counts only from the next login."""
    for name, socket_path in (("control", paths.GATE_CONTROL_SOCKET), ("status", paths.GATE_STATUS_SOCKET)):
        if path == socket_path:
            return (f"The gate's {name} socket, {path}, refused this login: it is for members of "
                    f"{gateproto.SOCKET_GROUPS[name]}. A group joined since logging in counts from the next login.")
    return (f"{path} refused this login: the gate's status socket is for members of "
            f"{gateproto.SOCKET_GROUPS['status']}, and its control socket for members of "
            f"{gateproto.SOCKET_GROUPS['control']}.")

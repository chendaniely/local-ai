"""The CLI's client for the gate (Phase 2a; plan.md, *The front and the gate*): JSON over HTTP/1.1 on one of the
gate's Unix sockets, with the standard library's http.client, so `spark status --json`, which 2b's menu bar polls over
SSH, and the other commands that ask the gate import neither uvicorn nor httpx (test_tested_against.py). The routes
and their messages are gateproto's.

A call that gets no usable answer raises a GateError whose text is a sentence:
- GateUnavailable when there is no socket, nothing listening on it, no answer within the timeout, as when systemd
  holds the socket while the gate is down, or an answer that broke off partway;
- GateForbidden when the socket refuses this login, naming the group it is for and the next step;
- GateRefused when the gate answers with a status other than 2xx, carrying the status and the answer's body;
- GateInputError, a ValueError too, for a route that can't be sent: the caller's input is at fault, not the gate;
- GateError itself for an answer the gate did send but the client can't take: not a JSON object, or past its bound.
cli.main prints that text as the command's one line (the controller's ruling at Task 10), so it says what state the
socket is in and, where there is one, the next step, in the words of Task 6's refusals; and it never holds what was
sent, nor what the gate answered beyond its `message`, the one plain line the gate words for whoever asked."""

from __future__ import annotations

import grp
import http.client
import json
import os
import pwd
import socket
from collections.abc import Callable
from pathlib import Path
from types import EllipsisType
from typing import Any, TypeVar

from spark import gateproto, paths

_T = TypeVar("_T")
ANSWER_MAX = 1 << 20  # a single answer above 1 MiB, far more than any of the gate's, is refused as a GateError,
LINE_MAX = 64 << 10  # and so is a stream's line above 64 KiB, its newline counted
MESSAGE_MAX = 2000  # a refusal's message above this many characters isn't shown, nor one that isn't a printable line
# Where to look when the gate is down: `make doctor`, Dan's, as Task 6's refusals say it for a service that is down
# (messages._ALERT, whose phone half doesn't hold here: a socket can be missing with no alert sent).
_STEP = {"dan": "On the Spark, `make doctor` shows what's wrong.",
         "agent": "`make doctor` on the Spark shows Dan what's wrong."}


def _membership(group: str) -> tuple[bool, bool]:
    """Whether this account is in the group, by the group database, and whether this login has the group now. An
    account added since the login began is the first without the second, until it logs in again."""
    try:
        entry = grp.getgrnam(group)
    except KeyError:
        return False, False
    now = entry.gr_gid == os.getegid() or entry.gr_gid in os.getgroups()
    try:
        account = pwd.getpwuid(os.getuid())
    except KeyError:
        return now, now
    return now or account.pw_name in entry.gr_mem or account.pw_gid == entry.gr_gid, now


def _words() -> str:
    """Whose words a next step is in: Dan's for root and spark-admin's members, who can take it, a member counted
    by the account (the group database), even before its login has the group; agent's for every other account,
    since agent is never told to run a command only Dan can run (Task 6)."""
    if os.geteuid() == 0 or _membership(gateproto.SOCKET_GROUPS["control"])[0]:
        return "dan"
    return "agent"


def _step() -> str:
    return _STEP[_words()]


class GateError(Exception):
    """A call to the gate that got no usable answer; the text says why, in a sentence. An answer the gate sent but
    the client can't take raises this itself: a 2xx answer with a body, or a stream's line, that isn't a JSON object,
    as every answer of the gate's is to be (gateproto), and an answer or a line past its bound. The gate is up then,
    so it is never a GateUnavailable, which a caller may take for a gate that is down (Task 23's direct release)."""


class GateUnavailable(GateError):
    """No socket, nothing listening on it, no answer within the timeout, or an answer that broke off partway: the gate
    isn't there to ask, or stopped answering."""


class GateForbidden(GateError):
    """The socket refused this login (EACCES on connect); the text names the group the socket is for, and the next
    step for this account."""


class GateInputError(GateError, ValueError):
    """A route that can't be sent, since it holds a space, a control character or a character outside ASCII: the name
    the caller gave is at fault, not the gate, and nothing was sent."""


class GateRefused(GateError):
    """The gate answered with a status other than 2xx. `body` is its answer, a JSON object (gateproto.GateRefusalBody),
    or {} for an answer that isn't one, such as uvicorn's own plain-text 500. Its text is the body's `message` when
    that is one printable line of at most MESSAGE_MAX characters, else a sentence with the status; nothing else of
    the body, in its text or its repr, which shows only the status."""

    def __init__(self, status: int, body: dict[str, Any]) -> None:
        super().__init__(status)
        self.status = status
        self.body = body

    def __str__(self) -> str:
        message = self.body.get("message")
        if isinstance(message, str) and message.strip() and message.isprintable() and len(message) <= MESSAGE_MAX:
            return message
        if self.status >= 500:
            return f"The gate failed on that request (HTTP {self.status}). {_step()}"
        return f"The gate refused that request (HTTP {self.status})."


class _UnixConnection(http.client.HTTPConnection):
    """http.client's connection, made to a Unix socket in place of a host and port. The Host header it sends,
    `localhost`, is there because HTTP/1.1 requires one; on a Unix socket it names nothing. `unix_sock` keeps the
    socket after http.client lets go of it, so a stream can set the timeout of the reads after the answer's head."""

    def __init__(self, socket_path: Path, timeout_s: float) -> None:
        super().__init__("localhost", timeout=timeout_s)
        self.socket_path = socket_path
        self.unix_sock: socket.socket | None = None

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(self.timeout)
            sock.connect(os.fspath(self.socket_path))
        except BaseException:
            sock.close()
            raise
        self.sock = self.unix_sock = sock


class GateStream:
    """An NDJSON answer's lines, each a dict as it comes, from GateClient.stream. Closing it, or leaving a `with`
    block, closes the connection, whether or not a line was read; so does the end of its lines, or an error. A stream
    cut off partway, its last chunk missing or its Content-Length short, is GateUnavailable, never its end."""

    def __init__(self, client: GateClient, connection: _UnixConnection, response: http.client.HTTPResponse,
                 wait_s: float | None) -> None:
        self._client = client
        self._connection = connection
        self._response = response
        self._wait_s = wait_s
        self._closed = False
        self._buffer = b""
        self._ended = False

    def __iter__(self) -> GateStream:
        return self

    def __next__(self) -> dict[str, Any]:
        if self._closed:
            raise StopIteration
        try:
            while True:
                line = self._line()
                if not line:
                    raise StopIteration
                if line.strip():
                    return _json_object(line)
        except BaseException:
            self.close()
            raise

    def _line(self) -> bytes:
        """The next line, its newline kept (the last may have none), or b'' at the stream's end. Read with read1, not
        http.client's readline, which takes a chunked answer cut off before its last chunk for a whole one; read1
        raises IncompleteRead there, and a short Content-Length shows as `length` still owed."""
        while True:
            newline = self._buffer.find(b"\n")
            end = newline + 1 if newline >= 0 else len(self._buffer)
            if end > LINE_MAX:
                raise GateError(f"A line of the gate's stream on {self._client.path} was longer than 64 KiB, more than "
                                f"any of its lines holds. {_step()}")
            if newline >= 0 or self._ended:
                line, self._buffer = self._buffer[:end], self._buffer[end:]
                return line
            data = self._client._talk(lambda: self._response.read1(LINE_MAX), self._quiet)
            if not data:
                if self._response.length:
                    raise GateUnavailable(self._client._broke_off())
                self._ended = True
            self._buffer += data

    def _quiet(self) -> str:
        if self._wait_s is None:  # no timeout was set, so only the system's own ETIMEDOUT
            return f"The gate's stream on {self._client.path} timed out. {_step()}"
        return f"The gate's stream on {self._client.path} sent no line for {self._wait_s:g} s. {_step()}"

    def close(self) -> None:
        self._closed = True
        self._response.close()  # its file holds the socket too, once http.client has handed the socket to it
        self._connection.close()

    def __enter__(self) -> GateStream:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class GateClient:
    """Calls to one of the gate's sockets, one connection per call. `timeout_s` bounds each wait for the gate: the
    connection, the answer's head, and every read of an answer that isn't a stream. A stream's reads after its head
    wait for `then_s` instead (GateClient.stream)."""

    def __init__(self, path: Path, timeout_s: float) -> None:
        self.path = Path(path)
        self.timeout_s = timeout_s

    def get(self, route: str) -> dict[str, Any]:
        return self._call("GET", route, None)

    def post(self, route: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._call("POST", route, body)

    def delete(self, route: str) -> dict[str, Any]:
        return self._call("DELETE", route, None)

    def stream(self, route: str, body: dict[str, Any] | None = None, *,
               then_s: float | None | EllipsisType = ...) -> GateStream:
        """An NDJSON answer, each line as it comes: a POST when a body is given (/v1/unload, /v1/make-room), else a
        GET. The call is made, and a refusal raised, before this returns. `timeout_s` bounds the connection and the
        answer's head, so a gate that is down, or hung, is GateUnavailable in time; the reads after the head wait
        `then_s` each, `timeout_s` by default, and None waits as long as the gate streams, as a drain behind a
        long request needs."""
        method = "GET" if body is None else "POST"
        payload = _prepare(route, body)
        connection, response = self._open(method, route, payload)
        if not 200 <= response.status < 300:
            try:
                raw = self._read(response)
            finally:
                connection.close()
            raise GateRefused(response.status, _refusal_body(raw))
        wait_s = self.timeout_s if then_s is ... else then_s
        try:
            connection.unix_sock.settimeout(wait_s)  # set by connect(); http.client's reader reads through it
        except BaseException:
            response.close()
            connection.close()
            raise
        return GateStream(self, connection, response, wait_s)

    def _call(self, method: str, route: str, body: dict[str, Any] | None) -> dict[str, Any]:
        payload = _prepare(route, body)
        connection, response = self._open(method, route, payload)
        try:
            raw = self._read(response)
        finally:
            connection.close()
        if not 200 <= response.status < 300:
            raise GateRefused(response.status, _refusal_body(raw))
        if not raw.strip():
            return {}  # a 204, or a 2xx answer with nothing in it
        return _json_object(raw)

    def _open(self, method: str, route: str,
              payload: bytes | None) -> tuple[_UnixConnection, http.client.HTTPResponse]:
        connection = _UnixConnection(self.path, self.timeout_s)
        try:
            connection.connect()
        except PermissionError as err:
            raise GateForbidden(_who_may(self.path)) from err
        except FileNotFoundError as err:
            raise GateUnavailable(f"The gate can't be reached: there is no socket at {self.path}. {_step()}") from err
        except ConnectionRefusedError as err:
            raise GateUnavailable(f"The gate can't be reached: nothing is listening on {self.path}. {_step()}") from err
        except TimeoutError as err:
            raise GateUnavailable(self._no_answer()) from err
        except OSError as err:
            raise GateUnavailable(f"The gate can't be reached on {self.path}: {_reason(err)}. {_step()}") from err
        headers = {} if payload is None else {"Content-Type": "application/json"}

        def ask() -> http.client.HTTPResponse:
            connection.request(method, route, body=payload, headers=headers)
            return connection.getresponse()

        try:
            return connection, self._talk(ask, self._no_answer)
        except BaseException:
            connection.close()
            raise

    def _read(self, response: http.client.HTTPResponse) -> bytes:
        """A whole answer, bounded at ANSWER_MAX. read() with a size takes a Content-Length answer that ends short for
        a whole one, and says so only in `length`, the bytes still owed, which is 0 for a whole answer or a 204 and
        None for a chunked one, whose cut raises IncompleteRead itself."""
        raw = self._talk(lambda: response.read(ANSWER_MAX + 1), self._no_answer)
        if len(raw) > ANSWER_MAX:
            raise GateError(f"The gate's answer on {self.path} was larger than 1 MiB, more than any of its answers "
                            f"holds. {_step()}")
        if response.length:
            raise GateUnavailable(self._broke_off())
        return raw

    def _broke_off(self) -> str:
        return f"The gate stopped answering on {self.path}: its answer broke off. {_step()}"

    def _talk(self, step: Callable[[], _T], quiet: Callable[[], str]) -> _T:
        """One step of the exchange once connected: a timeout (worded by `quiet`), or a connection that breaks, is
        the gate not answering."""
        try:
            return step()
        except TimeoutError as err:
            raise GateUnavailable(quiet()) from err
        except (OSError, http.client.HTTPException) as err:
            raise GateUnavailable(f"The gate stopped answering on {self.path}: {_reason(err)}. {_step()}") from err

    def _no_answer(self) -> str:
        return f"The gate didn't answer on {self.path} within {self.timeout_s:g} s. {_step()}"


def _prepare(route: str, body: dict[str, Any] | None) -> bytes | None:
    """The request's body, made before any connection, as is the route's check: what can't be sent is refused with
    nothing sent and no connection left open. A body json can't encode (a Decimal, say) raises its TypeError here."""
    bad = next((c for c in route if not "\x21" <= c <= "\x7e"), None)
    if bad is not None:
        holds = "a space" if bad == " " else repr(bad)
        raise GateInputError(f"Can't ask the gate for {route!r}: it holds {holds}, which a request's path can't "
                             "carry. Check the name given.")
    return None if body is None else json.dumps(body, allow_nan=False).encode()


def _json_object(raw: bytes) -> dict[str, Any]:
    try:
        answer = json.loads(raw)
    except ValueError:
        answer = None
    if not isinstance(answer, dict):
        raise GateError(f"The gate's answer couldn't be read: it isn't a JSON object. {_step()}")
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
    """Which group the socket is for, by which of the gate's sockets it is, and the next step for this account: log
    in again, for a member whose login began before it joined; `make doctor`, for a member the socket still refused
    (its permissions are wrong); for anyone else, whose the socket is."""
    for name, socket_path in (("control", paths.GATE_CONTROL_SOCKET), ("status", paths.GATE_STATUS_SOCKET)):
        if path != socket_path:
            continue
        group = gateproto.SOCKET_GROUPS[name]
        refused = f"The gate's {name} socket, {path}, refused this login"
        member, now = _membership(group)
        if member and now:
            return f"{refused}, though it is in {group}: the socket's permissions are wrong. {_step()}"
        if member:
            return f"{refused}: this account joined {group} after the login began. Log in again, then run it."
        if name == "control":
            return (f"{refused}: only {group}'s members can use it, so that is Dan's command, which runs as Dan, not "
                    "as agent.")
        return f"{refused}: only {group}' members can use it, and Dan decides who they are."
    return (f"{path} refused this login: the gate's status socket is for members of "
            f"{gateproto.SOCKET_GROUPS['status']}, and its control socket for members of "
            f"{gateproto.SOCKET_GROUPS['control']}.")

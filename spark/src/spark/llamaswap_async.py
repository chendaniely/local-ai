"""The gate's llama-swap client (Phase 2a; plan.md, *The front and the gate*): what's running, a load, an unload and
an engine's last lines, over one httpx.AsyncClient. The gate builds it as `AsyncLlamaSwap(paths.LLAMASWAP_URL,
<its internal key>, load_timeout_s=load_timeout_for(render.HEALTH_CHECK_TIMEOUT_S))`.

What it relies on in llama-swap v257, read from its source (tag v257, commit f00d375, github.com/mostlygeek/llama-swap):
`GET /upstream/<model>/health` starts the model, or joins a start under way, and answers once it is ready (the
engine's own 200), or with a 500 once its start has failed or it was unloaded. Nothing of llama-swap's ends that wait
but healthCheckTimeout, and that only once a health poll returns: the poll has no response-header timeout
(internal/config/model_config.go:181), and the deadline is checked between polls
(internal/process/process_command.go:587-596). `POST /api/models/unload/<model>` answers `OK` once the engine has
stopped. `GET /logs/stream/<model>` sends the model's log so far, then each new line, until the client closes it. Its
errors are Phase 1's (spark.llamaswap): a call nothing answered in time is LlamaSwapUnreachable (LlamaSwapNotSent,
its subclass, when it never had a connection), and one answered with an error or with something it can't read is
LlamaSwapAnswered; a load raises neither, and comes to a LoadOutcome. No error's text holds the key, and each is
raised from None, so a traceback never shows httpx's own, whose request holds it."""

from __future__ import annotations

import asyncio
import json
import urllib.parse
from dataclasses import dataclass
from types import TracebackType
from typing import ClassVar, Literal, get_args

import httpx

from spark import gateproto
from spark.llamaswap import LlamaSwapAnswered, LlamaSwapError, LlamaSwapUnreachable, Running, _url_ok

# The llama-swap whose API this client was written for: its paths, its answers and /running's shape.
# test_tested_against.py fails once stack/versions.yaml moves on without it: check this code against the new release,
# then move it.
TESTED_AGAINST = {"llama-swap": "v257"}
# A load waits for its deadline, llama-swap's healthCheckTimeout, and this beyond it: the 5 s llama-swap gives a
# stuck start between SIGTERM and SIGKILL (v257's internal/process/process_command.go:553), and a margin.
PAST_THE_DEADLINE_S = 20.0
ANSWER_MAX = 1 << 20  # an answer above 1 MiB, far more than any of llama-swap's, is refused, as gateclient's are
TAIL_MAX = 64 << 10  # last_lines keeps at most the stream's last 64 KiB
# A failed load's text goes into notifications and the gate's refusal history: its head, at most this many characters
# once escaped, the last of them … when it was cut (the controller's ruling at Task 12's review).
FAILED_TEXT_MAX = 2048
# Why a load is unknown, for the gate's journal: no answer in time; no connection, so nothing was sent; the
# connection dropped after the call was sent; an answer over ANSWER_MAX; an answer it couldn't decode.
UnknownWhy = Literal["timeout", "refused", "dropped", "too big", "unreadable"]
UNKNOWN_WHY: tuple[str, ...] = get_args(UnknownWhy)
# httpx's errors for a call that never had a connection, so sent nothing: LlamaSwapNotSent.
_NO_CONNECTION = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)


def load_timeout_for(health_check_timeout_s: float) -> float:
    """How long the gate's load call waits: the load's deadline plus PAST_THE_DEADLINE_S (200 s for 2a's 180)."""
    return float(health_check_timeout_s) + PAST_THE_DEADLINE_S


@dataclass(frozen=True)
class LoadOutcome:
    """What a load call came to. READY: the model answered ready, a 200. failed(status, text): any other answer, such
    as a failed start's 500 or the 429 of a model's eleventh request, with its status and its text, every
    non-printable character escaped, at most FAILED_TEXT_MAX of it. unknown(why): the call timed out, dropped or
    failed, so the load may be under way, and the gate settles it with the load's ticket (Task 15); `why`, one of
    UNKNOWN_WHY, says which, for the journal."""

    kind: Literal["ready", "failed", "unknown"]
    status: int | None = None
    text: str = ""
    why: str = ""

    READY: ClassVar[LoadOutcome]

    @classmethod
    def failed(cls, status: int, text: str) -> LoadOutcome:
        return cls("failed", status, text)

    @classmethod
    def unknown(cls, why: UnknownWhy) -> LoadOutcome:
        if why not in UNKNOWN_WHY:
            raise ValueError(f"an unknown load's why is one of {UNKNOWN_WHY}, not {why!r}")
        return cls("unknown", why=why)


LoadOutcome.READY = LoadOutcome("ready")


def printable(text: str) -> str:
    """`text` with every non-printable character escaped as Python writes it (ESC as \\x1b, a tab as \\t), so what
    llama-swap or an engine wrote reaches a terminal, a phone or the journal as one line of plain text."""
    return "".join(c if c.isprintable() else c.encode("unicode_escape").decode("ascii") for c in text)


class LlamaSwapNotSent(LlamaSwapUnreachable):
    """Nothing was sent: no connection to llama-swap, refused or not made in time. Still a LlamaSwapUnreachable, so a
    caller that doesn't care tells no difference. A drain does: an unload never sent leaves the model loaded, so the
    drain goes back to serving, whereas one sent waits for the model to go, since v257 never takes back an unload it
    took (the controller's ruling at Task 12's re-review; Task 16). `running()` may raise either for a connection not
    made in time, since its whole call has the same bound as its connection; `unload()` and `last_lines()` bound
    their whole calls by more than that, so they always tell."""


class _TooBig(Exception):
    pass


async def _read(response: httpx.Response) -> bytes:
    body = bytearray()
    async for chunk in response.aiter_bytes():
        body += chunk
        if len(body) > ANSWER_MAX:
            raise _TooBig
    return bytes(body)


def _quoted(model: str) -> str:
    return urllib.parse.quote(model, safe="")


class AsyncLlamaSwap:
    """One httpx.AsyncClient for every call: no proxy from the environment, which the key would go to even for
    127.0.0.1; no redirect followed, since whatever answers on the port could send the key anywhere; the key only in
    `Authorization: Bearer`. `call_timeout_s`, by default gateproto.LLAMA_SWAP_HUNG_S, after which the gate counts
    llama-swap as down, bounds every call's connection, and `running()` whole. A load, which lasts as long as an
    engine's start, is bounded whole by `load_timeout_s`, and an unload by gateproto.UNLOAD_CALL_TIMEOUT_S;
    `last_lines` waits up to `call_timeout_s` for its head, then reads for `read_s`. Close it with `aclose()`, or use
    it as an async context manager."""

    def __init__(self, base_url: str, key: str, *, load_timeout_s: float,
                 call_timeout_s: float = gateproto.LLAMA_SWAP_HUNG_S):
        if not _url_ok(base_url):
            raise LlamaSwapError(f"llama-swap: invalid URL {base_url!r} (want http://host:port)")
        if not (key and key.isascii() and key.isprintable()):
            # Never the value, nor any part of it. The likely cause: a CRLF line in the file the key was read from.
            raise LlamaSwapError("llama-swap: the key is empty or isn't printable ASCII (a stray CR or LF?), so "
                                 "nothing was sent")
        self.base_url = base_url.rstrip("/")
        self.load_timeout_s = load_timeout_s
        self.call_timeout_s = call_timeout_s
        self._client = httpx.AsyncClient(headers={"Authorization": f"Bearer {key}"}, timeout=call_timeout_s,
                                         trust_env=False, follow_redirects=False)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> AsyncLlamaSwap:
        return self

    async def __aexit__(self, kind: type[BaseException] | None, value: BaseException | None,
                        traceback: TracebackType | None) -> None:
        await self.aclose()

    def _unreachable(self, what: str) -> LlamaSwapUnreachable:
        return LlamaSwapUnreachable(f"llama-swap unreachable at {self.base_url}: {what}")

    def _not_sent(self, err: httpx.HTTPError) -> LlamaSwapNotSent:
        return LlamaSwapNotSent(f"llama-swap unreachable at {self.base_url}: {type(err).__name__}")

    async def _call(self, method: str, path: str, bound_s: float) -> bytes:
        """The answer's body to `method path`, all of it within `bound_s`, or one of Phase 1's errors."""
        where = f"llama-swap {method} {path}"
        answered = False
        try:
            async with asyncio.timeout(bound_s):
                async with self._client.stream(method, self.base_url + path,
                                               timeout=httpx.Timeout(self.call_timeout_s, read=bound_s)) as response:
                    answered = True
                    body = await _read(response)
        except TimeoutError:
            raise self._unreachable(f"no answer within {bound_s:g} s") from None
        except _NO_CONNECTION as err:
            raise self._not_sent(err) from None
        except httpx.TransportError as err:
            # Reset or dropped before its head, or too slow at any point: nothing answered in time.
            if not answered or isinstance(err, httpx.TimeoutException):
                raise self._unreachable(type(err).__name__) from None
            raise LlamaSwapAnswered(f"{where}: an answer that broke off ({type(err).__name__})") from None
        except httpx.RequestError as err:  # one it can't decode, such as a body its Content-Encoding doesn't fit
            raise LlamaSwapAnswered(f"{where}: an answer it can't read ({type(err).__name__})") from None
        except _TooBig:
            raise LlamaSwapAnswered(f"{where}: an answer above {ANSWER_MAX} bytes") from None
        if not response.is_success:  # a redirect included: it is the answer, never followed
            raise LlamaSwapAnswered(f"{where}: HTTP {response.status_code}")
        return body

    async def running(self) -> list[Running]:
        """Each model that isn't stopped, with its state: starting, ready or stopping (v257's
        internal/router/base.go:366-376), sorted by model (internal/server/api.go:363)."""
        body = await self._call("GET", "/running", self.call_timeout_s)
        try:
            listed = json.loads(body)["running"]
        except (ValueError, KeyError, TypeError, RecursionError) as err:  # unparsable, or has no "running"
            unreadable = type(err).__name__
            raise LlamaSwapAnswered(f"llama-swap GET /running: an answer it can't read ({unreadable})") from None
        # Phase 1's check (spark.llamaswap): v257 always sends {"running": [...]}, each entry with a string model and
        # state. Anything else must not read as nothing running: the gate would load over a model already loaded.
        if not (isinstance(listed, list) and all(isinstance(r, dict) and isinstance(r.get("model"), str)
                                                 and isinstance(r.get("state"), str) for r in listed)):
            raise LlamaSwapAnswered("llama-swap GET /running: an answer it can't read (not v257's shape)")
        return [Running(r["model"], r["state"]) for r in listed]

    async def load(self, model: str) -> LoadOutcome:
        """Loads `model`, or joins its load under way, and waits for it up to `load_timeout_s`. Any timeout or error
        is unknown, a call never sent included: the gate settles each the same way, with the load's ticket (plan.md),
        so the one-load slot is never freed while a start may be under way, and the start, if it began, goes on in
        llama-swap without its caller. On an open client only: a closed one raises httpx's RuntimeError, so the gate
        cancels its load tasks before `aclose()` (Task 19)."""
        path = f"/upstream/{_quoted(model)}/health"
        try:
            async with asyncio.timeout(self.load_timeout_s):
                async with self._client.stream("GET", self.base_url + path, timeout=httpx.Timeout(
                        self.call_timeout_s, read=self.load_timeout_s)) as response:
                    body = await _read(response)
        except (TimeoutError, httpx.TimeoutException):
            return LoadOutcome.unknown("timeout")
        except httpx.ConnectError:
            return LoadOutcome.unknown("refused")
        except httpx.TransportError:
            return LoadOutcome.unknown("dropped")
        except _TooBig:
            return LoadOutcome.unknown("too big")
        except httpx.HTTPError:  # every other error of httpx's, such as a body it can't decode
            return LoadOutcome.unknown("unreadable")
        if response.status_code == 200:
            return LoadOutcome.READY
        text = printable(body.decode("utf-8", "backslashreplace"))
        if len(text) > FAILED_TEXT_MAX:
            text = text[:FAILED_TEXT_MAX - 1] + "…"
        return LoadOutcome.failed(response.status_code, text)

    async def unload(self, model: str) -> None:
        """Unloads `model`, returning once its engine has stopped: SIGTERM, then SIGKILL after the model's
        unloadTimeout, 10 s by default (v257's internal/config/config.go:12), each unload in turn through llama-swap's
        one run loop (internal/router/base.go:399-440). Bounded by gateproto.UNLOAD_CALL_TIMEOUT_S, read at each call.
        Its timeout is LlamaSwapUnreachable, but the stop goes on in llama-swap, which still answers /running: the
        gate counts the model stopping until /running shows it gone, never llama_swap_down."""
        await self._call("POST", f"/api/models/unload/{_quoted(model)}", gateproto.UNLOAD_CALL_TIMEOUT_S)

    async def last_lines(self, model: str, n: int = 20, read_s: float = 1.0) -> list[str]:
        """The last `n` lines of `model`'s log: its stream read for `read_s`, then closed. Empty lines are left out,
        and every non-printable character is escaped."""
        path = f"/logs/stream/{_quoted(model)}"
        where = f"llama-swap GET {path}"
        tail = bytearray()
        trimmed = False
        answered = False
        try:
            async with asyncio.timeout(self.call_timeout_s + read_s):
                async with self._client.stream("GET", self.base_url + path) as response:
                    answered = True
                    if not response.is_success:  # a redirect included: never followed
                        raise LlamaSwapAnswered(f"{where}: HTTP {response.status_code}")
                    try:
                        async with asyncio.timeout(read_s):
                            async for chunk in response.aiter_bytes():
                                tail += chunk
                                if len(tail) > TAIL_MAX:
                                    del tail[:-TAIL_MAX]
                                    trimmed = True
                    except (TimeoutError, httpx.ReadTimeout):
                        pass  # read_s is up, or nothing more came: the stream stays open until this closes it
        except TimeoutError:
            raise self._unreachable(f"no answer within {self.call_timeout_s + read_s:g} s") from None
        except _NO_CONNECTION as err:
            raise self._not_sent(err) from None
        except httpx.TransportError as err:
            if not answered:  # reset, dropped or too slow before its head
                raise self._unreachable(type(err).__name__) from None
            # The stream broke off: what came before it is still the log's tail.
        except httpx.RequestError as err:  # one it can't decode, such as a body its Content-Encoding doesn't fit
            raise LlamaSwapAnswered(f"{where}: an answer it can't read ({type(err).__name__})") from None
        pieces = bytes(tail).split(b"\n")  # on \n alone: str.splitlines would also split on \x1c, \x85, \u2028
        if trimmed:
            pieces = pieces[1:]  # the first may be the end of a line cut off
        lines = [printable(p.removesuffix(b"\r").decode("utf-8", "backslashreplace")) for p in pieces]
        lines = [line for line in lines if line]
        return lines[-n:] if n > 0 else []

"""A request's row (the usage meter's counter, Task 44d; website/design/usage-meter.md, §1): one line of numbers the front
puts on its row file (spark.rowfile) as each request ends, after its journal line, but before a cut's, whose line waits
for the gate's why. The app builds it (usage_row) from the request as it holds it then, and reads the request's shape
from its body as it reads the body (read_shape).

What a row holds, by the plan's *The row and the ledger row*; a field whose value is None is left out:
- **Who and what**: `v`, the row's version; `id`, the request's own, minted once and kept through the front's resends,
  the gate's request id for it; `key`, the key's name; `route`, the route's name (usage.ROUTE_NAMES); `model`, the
  registry's name it resolved to, never the client's; `role`, only when the client asked by one of that model's
  roles; `engine`, the model list's, when its entry names one; `asked_len`, for a model it didn't resolve, the length
  of the name it asked for, never the name.
- **When**: `t0` and `t1`, UTC epoch seconds by the front's clock. Every duration is by the monotonic clock: `wait_s`,
  from the front receiving it to its last forwarding (its body read, then the gate's clear, any load among it), or to
  its end when it was never forwarded; `first_s`, from that forwarding to its answer's first event (a ping isn't one);
  `load_s`, by the gate's admit answer, the seconds of the load it started, only on the row of the request that
  started it.
- **How it ended**, `end`: `done`, its answer whole; `cut`; `gone`, its client went first; `error`, the engine's error
  event, or an answer that isn't 2xx (llama-swap's, passed on, or the front's own 500); `refused`, a refusal of the
  front's or the gate's, its code in `code`. `status`, the status sent, when one was.
- **Counts**, from its answer's usage scan (usage.py): `p`, `c` (absent when not reported, never 0), `o`, `from`
  (`usage`, `timings` or `none`; always written), `ev_think`, `ev_text`, `prompt_ms`, `gen_ms` and `resp_id`.
- **Shape**: `stream`, whether its body asked to stream; `attempts`, its sends to llama-swap; `n`, above 1 only;
  `images`; `bytes_in`; `effort`, from EFFORTS only.
- `dropped`, the rows lost before this one, is the row file's to add, and `refused_n`, the refused rows its key's limit
  folded into this one (rowfile.RefusedLimit), the app's.

Every text in a row is from a fixed set, a registry name, or passes usage.safe_text: nothing a client chose is kept."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from spark.front import parse
from spark.front.usage import LIMIT, ROUTE_NAMES, Counts, safe_text

if TYPE_CHECKING:
    from spark.front.app import _Request

VERSION = 1
EFFORTS = frozenset({"none", "minimal", "low", "medium", "high", "xhigh", "max"})  # the reasoning efforts a row keeps
_EFFORTS = {effort: effort for effort in EFFORTS}  # each one's own copy, so the client's string is never kept
# An image part, as chat completions (`image_url`), messages (`image`) and responses (`input_image`) name it, in each
# message's content, on the routes that take one.
IMAGE_PARTS = frozenset({"image_url", "image", "input_image"})
IMAGE_PATHS = frozenset({"/v1/chat/completions", "/v1/messages", "/v1/responses"})
_NO_COUNTS = Counts()


def usage_row(request: _Request, ended: float, monotonic_ended: float, roles: frozenset[str]) -> dict:
    """`request`'s row, as it ended at `ended` (the front's clock) and `monotonic_ended` (the monotonic clock). `roles`
    are the roles of the model it resolved to: its `role` is kept only when the client asked by one of them."""
    counts = request.scan.finish() if request.scan is not None else _NO_COUNTS
    forwarded, first = request.mono_forwarded, request.mono_first
    row: dict[str, Any] = {
        "v": VERSION,
        "id": safe_text(request.id),
        "t0": round(request.started, 3),
        "t1": round(ended, 3),
        "key": safe_text(request.key),
        "route": ROUTE_NAMES.get(request.route[1]) if request.route is not None else None,
        "model": safe_text(request.model),
        "role": safe_text(request.asked) if request.model is not None and request.asked in roles else None,
        "engine": safe_text(request.entry.get("engine")) if request.entry is not None else None,
        "asked_len": len(request.asked) if request.asked is not None and request.model is None else None,
        "end": _end(request),
        "status": request.status,
        "code": safe_text(request.refused),
        "p": counts.p,
        "c": counts.c,
        "o": counts.o,
        "from": counts.source,
        "ev_think": counts.ev_think,
        "ev_text": counts.ev_text,
        "wait_s": _seconds((monotonic_ended if forwarded is None else forwarded) - request.mono_started),
        "load_s": None if request.load_s is None else _seconds(request.load_s),
        "first_s": None if forwarded is None or first is None else _seconds(first - forwarded),
        "prompt_ms": counts.prompt_ms,
        "gen_ms": counts.gen_ms,
        "stream": request.stream,
        "attempts": request.attempts,
        "n": request.n if isinstance(request.n, int) and request.n > 1 else None,
        "images": request.images,
        "bytes_in": request.bytes_in,
        "effort": request.effort if isinstance(request.effort, str) and request.effort in EFFORTS else None,
        "resp_id": safe_text(counts.resp_id),
    }
    return {name: value for name, value in row.items() if value is not None}


def _end(request: _Request) -> str:
    if request.refused is not None:
        return "refused"
    outcome = request.outcome()
    if isinstance(outcome, str):
        return outcome  # gone, cut, or error: the engine's error event
    return "done" if 200 <= outcome < 300 else "error"


def _seconds(value: float) -> float:
    return round(max(value, 0.0), 3)


def read_shape(request: _Request, path: str, body: dict | parse.Form) -> None:
    """`request`'s shape, from its body as the front read it for `path`, into numbers and fixed words on `request`: the
    body itself is never kept, since the front's memory bound counts each parse as transient (app.py, step 6). A JSON
    body gives `stream`, `n`, `images` (on IMAGE_PATHS), `effort` (EFFORTS' own copy, from `reasoning_effort` or a
    responses body's `reasoning.effort`) and `bytes_in`, the bytes it goes on as; a form, `bytes_in` alone, its files'
    bytes."""
    if isinstance(body, parse.Form):
        request.bytes_in = sum(len(data) if isinstance(data, bytes) else data.stat().st_size
                               for data in (part.path_or_bytes for part in body.files))
        return
    request.stream = body.get("stream") is True
    n = body.get("n")
    request.n = n if type(n) is int and 0 <= n < LIMIT else None
    request.images = _images(body) if path in IMAGE_PATHS else None
    effort = body.get("reasoning_effort")
    reasoning = body.get("reasoning")
    if effort is None and isinstance(reasoning, dict):
        effort = reasoning.get("effort")
    request.effort = _EFFORTS.get(effort) if isinstance(effort, str) else None
    request.bytes_in = None if request.json is None else len(request.json)


def _images(body: dict) -> int:
    """The image parts in each message's content, of a chat's or a messages body's `messages` or a responses body's
    `input`: one level down, never further."""
    count = 0
    for name in ("messages", "input"):
        items = body.get(name)
        if not isinstance(items, list):
            continue
        for item in items:
            parts = item.get("content") if isinstance(item, dict) else None
            if isinstance(parts, list):
                count += sum(1 for part in parts if isinstance(part, dict) and isinstance(part.get("type"), str)
                             and part["type"] in IMAGE_PARTS)
    return count

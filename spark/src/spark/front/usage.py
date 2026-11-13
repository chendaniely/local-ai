"""The front's usage scanner (the usage meter's counter, Task 44a): the numbers of one request, read from its response
as the chunks pass, holding a fixed head and tail of each line and never more.

A stream is read per data line, a body that isn't streamed as one line; of each line it keeps the first HEAD bytes and
the last TAIL, so a multi-megabyte line costs the same memory as a short one. At a line's end it takes the last
`"usage"` and the last `"timings"` object from the tail (the head, for a usage that sits before an embeddings body's
vectors), each matched by exactly OBJECT after its key, which takes linear time on any input, and parses only that
object. llama-server b11146 puts them at the end of the last chunk, and at the end of a whole body (server-task.cpp).
Nothing from a client or from the model's text becomes a counted field: the numbers are checked ints, and the
response's id passes `safe_text`. A transcription's usage (Task E3) is llama-server's for Qwen3-ASR
(server-task.cpp, to_json_oaicompat_asr): `input_tokens`, `output_tokens` and `input_tokens_details.cached_tokens`,
read on that route alone, so chat's rows never take them. The front never scans whisper.cpp's answers (app.py, step
11a): a transcript is the model's own text, and one could hold anything that looks like a usage object.

**Whisper's recording length** (Task E3; website/design/usage-meter-every-model.md, *The front learns each Whisper
recording's length*). whisper.cpp v1.9.4's server (examples/server/server.cpp) gives a recording's length only in
`verbose_json`, its `duration`. For a transcription by whisper.cpp, whisper_plan reads the form as whisper does (the
first of a field given twice, as its httplib's multimap gives it) and decides:
- `verbose_json`: the answer is held and read for `duration`, and goes on as it came.
- `json`, no format, or `text`, with no `detect_language` field (json's answer then gains `language`), for an upload of
  at most CONVERT_UPLOAD_MAX: the form goes on asking for `verbose_json` (verbose_fields), with
  `no_language_probabilities=true`, since json and text never carry them and they cost whisper another encoder pass
  (server.cpp, 1076-1088); and the answer is held, read, and sent as whisper's own `json` or `text` for it, byte for
  byte (hear): `{"text": …}` dumped as its nlohmann::ordered_json dumps it, compact and in UTF-8 with
  only `"`, `\\` and the control characters escaped (`\\b \\t \\n \\f \\r` short, the rest `\\u00xx` in lower case), as
  `application/json`; or the text itself up to its first NUL (a std::string made of a C string), as `text/html;
  charset=utf-8` (server.cpp, 1017-1021 and 1161-1173). Whisper writes verbose_json's `text` with the same escaper, so
  json's is verbose_json's, byte for byte. A converted form also pins `token_timestamps` to what whisper would give the
  client's format: whisper turns token timestamps on for verbose_json, unless the form says otherwise, and with them on
  its server wraps every segment at 60 characters, which changes the text's line breaks (server.cpp, 627-636 and 942;
  src/whisper.cpp, 7789-7795); for `json` and `text` it turns them on only with `max_len` above 0 or `split_on_word`,
  and never with `no_timestamps`.
  That reckoning starts from whisper-server's own defaults, so the stack's whisper-servers run none of
  WHISPER_FLAGS_ASSUMED_OFF (test_front_usage.py checks stack/models.yaml).
- `srt` or `vtt`: held, the last cue's end read as an estimate of the length (`audio_est`), and sent as it came. Only a
  cue's own timing line is read: one that opens the answer or follows a blank line, in srt after its cue's number.
- Anything else (a format whisper doesn't name, which it answers as json; a form with a file part named like a field
  this reads; a `max_len` whisper can't read, which it answers 500 whatever the format; json or text for an upload
  over CONVERT_UPLOAD_MAX): as it came, unheld, no length.
Where a converted answer differs from whisper's own: segment text whisper.cpp leaves as bytes that aren't UTF-8
reaches verbose_json, as json, with U+FFFD in their place, while whisper's `text` sends the bytes themselves, so the
rebuilt json is whisper's own and the rebuilt text has U+FFFD there. An unknown `language` code makes whisper's
verbose_json fail (its `language` field is a null C string, server.cpp 1070; whisper.cpp's whisper_lang_str_full),
which json would have answered, so the client gets whisper's 500. And an answer the front can't use (app.py, step
11a) gets the front's own 502."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from spark.front import parse


HEAD = 4096
TAIL = 16384
LIMIT = 2**53  # a counted value is a non-negative int below this

ROUTE_NAMES = {
    "/v1/chat/completions": "chat",
    "/v1/completions": "completions",
    "/v1/responses": "responses",
    "/v1/messages": "messages",
    "/v1/embeddings": "embeddings",
    "/v1/audio/transcriptions": "transcriptions",
}
if set(ROUTE_NAMES) != set(parse.INFERENCE_PATHS):  # a new route gets a name here
    raise ImportError("usage.ROUTE_NAMES doesn't name exactly parse.INFERENCE_PATHS")
TRANSCRIPTIONS = "/v1/audio/transcriptions"
ROUTES_COUNTED = ("/v1/chat/completions", "/v1/completions", "/v1/embeddings", TRANSCRIPTIONS)

# One object after its key, one level of nesting. The two alternatives start with different bytes, so the match never
# backtracks into a choice: linear time (a `[^{}]+` inside the loop is exponential on an unclosed object).
OBJECT = rb"\{(?:[^{}]|\{[^{}]*\})*\}"
_OBJECT = re.compile(OBJECT)
_USAGE_KEY = re.compile(rb'"usage"\s*:\s*')
_TIMINGS_KEY = re.compile(rb'"timings"\s*:\s*')
_TRIES = 4  # key copies tried from the last backward, so unclosed copies cost a bounded number of scans
_THINK = re.compile(rb'"reasoning_content"\s*:\s*"')
_TEXT = re.compile(rb'"content"\s*:\s*"')
_ID = re.compile(rb'"id"\s*:\s*"([^"\\]{1,200})"')
_EOL = re.compile(rb"\r\n|\r|\n")
_SAFE = re.compile(r"[A-Za-z0-9._:-]{1,64}", re.ASCII)

Source = Literal["usage", "timings", "none"]


@dataclass(frozen=True)
class Counts:
    p: int | None = None  # prompt tokens, cached ones included
    c: int | None = None  # cached prompt tokens; None when not reported, never 0 for that
    o: int | None = None  # output tokens
    source: Source = "none"
    ev_think: int | None = None  # events with reasoning text (streams)
    ev_text: int | None = None  # events with answer text (streams)
    prompt_ms: int | None = None
    gen_ms: int | None = None
    resp_id: str | None = None


def safe_text(value: object) -> str | None:
    """`value` if it is text of `[A-Za-z0-9._:-]{1,64}` only, else None."""
    if isinstance(value, str) and _SAFE.fullmatch(value):
        return value
    return None


def _count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < LIMIT:
        return None
    return value


def _millis(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not 0 <= value < LIMIT:  # also false for nan
        return None
    return int(round(value))


def _last_object(key: re.Pattern[bytes], *parts: bytes) -> dict | None:
    """The last JSON object after `key` in the first of `parts` that has one."""
    for part in parts:
        starts = [found.end() for found in key.finditer(part)]
        for start in starts[-1:-_TRIES - 1:-1]:
            found = _OBJECT.match(part, start)
            if found:
                try:
                    obj = json.loads(found.group())
                except ValueError:
                    continue
                if isinstance(obj, dict):
                    return obj
    return None


def _field(obj: object, name: str) -> int | None:
    return _count(obj.get(name)) if isinstance(obj, dict) else None


class UsageScan:
    """Scans one response. `feed(chunk)` as its bytes pass, `finish()` once for its Counts (again, the same ones)."""

    def __init__(self, path: str, *, streamed: bool):
        self.path = path
        self.streamed = streamed
        self._head = bytearray()
        self._tail = bytearray()
        self._cr = False
        self._usage: dict | None = None
        self._timings: dict | None = None
        self._think = 0
        self._text = 0
        self._id: str | None = None
        self._id_done = False
        self._counts: Counts | None = None

    def held_bytes(self) -> int:
        return len(self._head) + len(self._tail)

    def feed(self, chunk: bytes) -> None:
        if self._counts is not None:
            return
        if not self.streamed:
            self._take(chunk, 0, len(chunk))
            return
        at = 0
        if self._cr:
            self._cr = False
            if chunk[:1] == b"\n":
                at = 1
        for end in _EOL.finditer(chunk, at):
            self._take(chunk, at, end.start())
            self._line_ends()
            at = end.end()
            if end.group() == b"\r" and at == len(chunk):
                self._cr = True
        self._take(chunk, at, len(chunk))

    def _take(self, chunk: bytes, start: int, stop: int) -> None:
        room = HEAD - len(self._head)
        if room > 0:
            self._head += chunk[start:min(stop, start + room)]
        if stop - start >= TAIL:
            self._tail[:] = chunk[stop - TAIL:stop]
        else:
            self._tail += chunk[start:stop]
            if len(self._tail) > TAIL:
                del self._tail[:len(self._tail) - TAIL]

    def _line_ends(self) -> None:
        head, tail = bytes(self._head), bytes(self._tail)
        self._head.clear()
        self._tail.clear()
        if not head.startswith(b"data:"):  # an event name, a ping, a blank line
            return
        if _THINK.search(head):
            self._think += 1
        if _TEXT.search(head):
            self._text += 1
        if not self._id_done:
            self._id_done = True
            # The last id: llama-server puts the top-level one after `choices`, whose tool calls carry the model's own.
            found = list(_ID.finditer(tail)) or list(_ID.finditer(head))
            self._id = safe_text(found[-1].group(1).decode("ascii", "replace")) if found else None
        self._look(head, tail)

    def _look(self, head: bytes, tail: bytes) -> None:
        usage = _last_object(_USAGE_KEY, tail, head)
        if usage is not None:
            self._usage = usage
        timings = _last_object(_TIMINGS_KEY, tail, head)
        if timings is not None:
            self._timings = timings

    def finish(self) -> Counts:
        if self._counts is not None:
            return self._counts
        if self.streamed:
            if self._head:
                self._line_ends()
        else:
            head, tail = bytes(self._head), bytes(self._tail)
            self._look(head, tail)
            self._id = self._body_id(tail)
        self._head.clear()
        self._tail.clear()
        self._counts = self._result()
        return self._counts

    @staticmethod
    def _body_id(tail: bytes) -> str | None:
        """The body's top-level id: the last one in the tail, and, when a tool call is there, only one after the
        last `"tool_calls"` and the `"usage"` that follows the choices."""
        floor = 0
        if b'"tool_calls"' in tail:
            usage = [found.start() for found in _USAGE_KEY.finditer(tail)]
            if not usage or usage[-1] < tail.rfind(b'"tool_calls"'):
                return None
            floor = usage[-1]
        found = [m for m in _ID.finditer(tail) if m.start() >= floor]
        return safe_text(found[-1].group(1).decode("ascii", "replace")) if found else None

    def _result(self) -> Counts:
        usage, timings = self._usage, self._timings
        prompt_ms = gen_ms = None
        if timings is not None:
            prompt_ms = _millis(timings.get("prompt_ms"))
            gen_ms = _millis(timings.get("predicted_ms"))
        p = c = o = None
        source: Source = "none"
        if usage is not None:
            if self.path == TRANSCRIPTIONS:  # Qwen3-ASR's, by llama-server: its own keys, on this route alone
                p, o = _field(usage, "input_tokens"), _field(usage, "output_tokens")
                c = _field(usage.get("input_tokens_details"), "cached_tokens")
            else:
                p, o = _field(usage, "prompt_tokens"), _field(usage, "completion_tokens")
                c = _field(usage.get("prompt_tokens_details"), "cached_tokens")
            if c is None:
                c = _field(timings, "cache_n")
            if self.path == "/v1/embeddings" and p is not None:
                o = 0
            source = "usage"
        elif timings is not None:
            cache = _field(timings, "cache_n")
            prompt = _field(timings, "prompt_n")
            if prompt is not None:
                p = _count(prompt + (cache or 0))
            c, o = cache, _field(timings, "predicted_n")
            source = "timings"
        if p is None and c is None and o is None:
            source, prompt_ms, gen_ms = "none", None, None
        streamed = self.streamed
        return Counts(p, c, o, source, self._think if streamed else None, self._text if streamed else None,
                      prompt_ms, gen_ms, self._id)


def scan_for(path: str, status: int, headers: Iterable[tuple[bytes, bytes]]) -> UsageScan | None:
    """A UsageScan for a 2xx answer on a counted route, or None. `streamed` reads the media type as
    upstream.StreamEnd.watching does; an encoded body can't be scanned, so it gets None."""
    if path not in ROUTES_COUNTED or not 200 <= status < 300:
        return None
    found = {name.lower(): value for name, value in headers}
    if found.get(b"content-encoding", b"identity").strip().lower() != b"identity":
        return None
    kind = found.get(b"content-type", b"").split(b";", 1)[0].strip().lower()
    return UsageScan(path, streamed=kind == b"text/event-stream")


# Whisper's recording length (Task E3; the module's docstring).

WHISPER_ENGINE = "whisper.cpp"  # the registry's engine whose transcriptions are held
HELD_MAX = 16 << 20  # a held answer's most, in bytes: a JSON body's cap, so its parse is within the memory bound
# The largest upload, in file bytes, whose answer is converted: its transcript can't come near HELD_MAX. A larger one
# asking for json or text goes as it came, its length estimated from its size when the meter reads its row.
CONVERT_UPLOAD_MAX = 16 << 20
WHISPER_TYPES = {"json": b"application/json", "text": b"text/html; charset=utf-8"}  # server.cpp's, for each format
_SLICE = 1 << 20  # the characters of a rebuilt json's text escaped and encoded at a time (_json_text)
# The fields whisper-server v1.9.4 reads (get_req_parameters) that decide its answer's format, or the text it gives
# json and text.
_READ = frozenset({"response_format", "detect_language", "token_timestamps", "no_timestamps", "max_len",
                   "split_on_word"})
# whisper-server's flags that change those fields' defaults (whisper_params_parse): whisper_plan reckons from the
# defaults, so the stack's whisper-servers run none of them.
WHISPER_FLAGS_ASSUMED_OFF = frozenset({"-ml", "--max-len", "-sow", "--split-on-word", "-nt", "--no-timestamps", "-dl",
                                       "--detect-language"})
_TRUE = frozenset({"true", "1", "yes", "y"})  # server.cpp's parse_str_to_bool
_STOI = re.compile(r"[ \t\n\v\f\r]*([+-]?[0-9]+)")  # std::stoi's: C's spaces, a sign, base-10 digits, then anything
_INT32 = range(-(2**31), 2**31)  # past it, std::stoi throws
# Each cue's timing line, as server.cpp writes it (1022-1064) with common-whisper.cpp's to_timestamp,
# `%02d:%02d:%02d%s%03d`: a line of only `start --> end`, opening the answer or after a blank line, and in srt after its
# cue's number (whisper's own, offset_n added, so it can be negative). Its end's parts are read; a transcript line that
# looks like one isn't.
_STAMP = rb"(\d{2,20}):(\d{2}):(\d{2})%s(\d{3})"
_CUE_END = {kind: re.compile(rb"(?:\A|\n\n)" + number + rb"\d{2,20}:\d{2}:\d{2}" + mark + rb"\d{3} --> "
                             + _STAMP % mark + rb"\n")
            for kind, number, mark in (("srt", rb"-?\d+\n", rb","), ("vtt", rb"", rb"\."))}


@dataclass(frozen=True)
class Whisper:
    """What the front does with a whisper.cpp transcription's answer: `asked`, the format whisper reads from the form;
    `convert`, whether the form goes on asking for verbose_json and the answer is rebuilt as `asked`; and the
    `token_timestamps` a converted form gains, or None when it adds none."""

    asked: str
    convert: bool
    token_timestamps: str | None = None


@dataclass(frozen=True)
class Heard:
    """A held answer, read: the body the client gets; whisper's own Content-Type for it when the front rebuilt it, else
    None (the answer's own headers); the recording's length in seconds, to 3 places, or None; and whether that length
    is an estimate."""

    body: bytes
    content_type: bytes | None = None
    audio_s: float | None = None
    audio_est: bool = False


def _stoi(text: str) -> int | None:
    """std::stoi's reading of `text`, or None where it throws: no digits, or a number past an int."""
    found = _STOI.match(text)
    if found is None:
        return None
    value = int(found.group(1))
    return value if value in _INT32 else None


def whisper_plan(form: parse.Form, upload_bytes: int | None) -> Whisper | None:
    """What the front does with this form's answer, when whisper.cpp transcribes it, its file parts `upload_bytes` in
    all; None to send it as it came and pass its answer on unheld (the module's docstring). A form asking for json or
    text is converted only when its upload is known and at most CONVERT_UPLOAD_MAX."""
    if any(part.name in _READ for part in form.files):
        return None
    first: dict[str, str] = {}
    for name, value in form.fields:
        first.setdefault(name, value)
    asked = first.get("response_format", "json")
    if asked in ("verbose_json", "srt", "vtt"):
        return Whisper(asked, convert=False)
    if asked not in WHISPER_TYPES or "detect_language" in first:
        return None
    if upload_bytes is None or upload_bytes > CONVERT_UPLOAD_MAX:
        return None
    if "token_timestamps" in first:  # whisper uses the form's own, whatever the format
        return Whisper(asked, convert=True)
    max_len = _stoi(first["max_len"]) if "max_len" in first else 0
    if max_len is None:
        return None
    on = first.get("no_timestamps") not in _TRUE and (max_len > 0 or first.get("split_on_word") in _TRUE)
    return Whisper(asked, convert=True, token_timestamps="true" if on else "false")


def verbose_fields(form: parse.Form, plan: Whisper) -> list[tuple[str, str]]:
    """A converted form's fields: its own in order but `response_format` and `no_language_probabilities`, then one
    `response_format=verbose_json`, the plan's `token_timestamps` when it has one, and
    `no_language_probabilities=true`: the probabilities cost whisper another encoder pass (server.cpp, 1076-1088), and
    json and text never carry them."""
    fields = [(name, value) for name, value in form.fields
              if name not in ("response_format", "no_language_probabilities")]
    fields.append(("response_format", "verbose_json"))
    if plan.token_timestamps is not None:
        fields.append(("token_timestamps", plan.token_timestamps))
    fields.append(("no_language_probabilities", "true"))
    return fields


def _not_json(constant: str) -> None:
    raise ValueError("NaN and the infinities aren't JSON")


def _verbose(body: bytes) -> dict | None:
    """A verbose_json answer parsed, as a JSON body is, within parse.MAX_JSON_PARTS parts; None for any other."""
    if parse.json_parts(body) > parse.MAX_JSON_PARTS:
        return None
    try:
        parsed = json.loads(body, parse_constant=_not_json)
    except (ValueError, RecursionError):  # a JSONDecodeError or a UnicodeDecodeError is a ValueError
        return None
    return parsed if isinstance(parsed, dict) else None


def _seconds(value: object) -> float | None:
    """A length in seconds: a number from 0 up to LIMIT, to 3 places; None for anything else."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value < LIMIT:  # nan fails too
        return None
    return round(float(value), 3)


def _last_cue_end(asked: str, body: bytes) -> float | None:
    found = None
    for found in _CUE_END[asked].finditer(body):
        pass
    if found is None:
        return None
    hours, minutes, seconds, millis = (int(part) for part in found.groups())
    return _seconds(hours * 3600 + minutes * 60 + seconds + millis / 1000)


def hear(plan: Whisper, body: bytes) -> Heard | None:
    """A held whisper answer read for its recording's length, and the body the client gets: as it came, or, for a
    converted one, whisper's own json or text for it. None for a converted answer without a readable `text` (a string)
    and `duration` (a number of seconds), which can't be sent: the client asked for another format. An unconverted
    answer always goes on as it came, with no length when it can't be read."""
    if plan.asked in _CUE_END:
        length = _last_cue_end(plan.asked, body)
        return Heard(body, audio_s=length, audio_est=length is not None)
    parsed = _verbose(body)
    length = None if parsed is None else _seconds(parsed.get("duration"))
    if not plan.convert:
        return Heard(body, audio_s=length)
    text = None if parsed is None else parsed.get("text")
    del parsed  # the rest of the answer, its segments, gone before the rebuild
    if length is None or not isinstance(text, str):
        return None
    try:
        rebuilt = text.split("\0", 1)[0].encode() if plan.asked == "text" else _json_text(text)
    except UnicodeEncodeError:  # a lone surrogate, which whisper never writes
        return None
    return Heard(rebuilt, WHISPER_TYPES[plan.asked], length)


def _json_text(text: str) -> bytes:
    """`{"text": text}` as whisper's json: compact, in UTF-8, the text escaped as nlohmann's dump_escaped escapes it
    with ensure_ascii false (examples/json.hpp, 18275-18519), which Python's encoder does too with ensure_ascii false:
    `"` and `\\`, and each control character, short where it has a short form, else `\\u00xx` in lower case; every
    other character as it is (test_front_usage.py checks every code point against nlohmann's rules). A slice at a time,
    each escape being one character's: a whole long text's escaped copy, and the four bytes a character its encoding
    sets aside first, would each cost as much as the text again."""
    pieces = [b'{"text":"']
    for at in range(0, len(text), _SLICE):
        pieces.append(json.dumps(text[at:at + _SLICE], ensure_ascii=False)[1:-1].encode())
    pieces.append(b'"}')
    return b"".join(pieces)


def whisper_headers(headers: Sequence[tuple[bytes, bytes]], content_type: bytes,
                    length: int | None) -> list[tuple[bytes, bytes]]:
    """A rebuilt answer's headers: the answer's own, in order, its Content-Type `content_type` and its Content-Length
    `length`, each added when it had none; with `length` None (a cut), no Content-Length."""
    out: list[tuple[bytes, bytes]] = []
    typed = sized = False
    for name, value in headers:
        if name == b"content-type":
            if not typed:
                out.append((name, content_type))
            typed = True
        elif name == b"content-length":
            if length is not None and not sized:
                out.append((name, str(length).encode()))
            sized = True
        else:
            out.append((name, value))
    if not typed:
        out.append((b"content-type", content_type))
    if length is not None and not sized:
        out.append((b"content-length", str(length).encode()))
    return out

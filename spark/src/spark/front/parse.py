"""The front's parsing (Phase 2a; plan.md, *The front and the gate*, and its *Its limits*): the routes it serves, the
client key a request carries, the body read within its cap and rebuilt, the model it names, the headers that go on,
each key's caps and the journal line.

- **Routes.** Only `GET /v1/models` and `POST` to the six inference paths reach llama-swap (ROUTES); `GET /health` is
  the front's own. llama-swap's admin routes (`/unload`, `/api/*`, `/logs*`, `/upstream/*`, `/running`, `/ui`,
  `/metrics`) are not among them, so they are never reached through the front.
- **Keys.** The key comes from `Authorization: Bearer <key>` or `x-api-key: <key>`, and is matched against the
  digests in constant time (credentials.match_key). Two headers that carry different keys name no one.
- **Bodies.** A JSON body is read whole, in memory, up to its cap, and goes on re-serialized from what was parsed, so
  the front and llama-swap read the same model. A multipart form (a whisper upload) is read as it comes: a file part
  over SPOOL_ABOVE goes to a file in the spool folder (the unit's PrivateTmp= /tmp, on disk), never to memory, and a
  smaller one stays in memory; its fields that aren't files stay in memory, FIELDS_MAX in all, and the parts number
  MAX_PARTS at most. A part is a file when it has a non-empty file name, as Go's mime/multipart reads a form (its
  readForm and Part.FileName, read 2026-10-08 at Go's master branch), and two `model` fields are refused, since
  llama-swap takes the first value where Python's parser keeps the last (plan.md). The form goes on rebuilt
  (encode_form), never as the client's bytes.
- **Refusals.** Each is a Refused, its words fixed text that quotes nothing a client sent, so they may reach the
  journal too; pi's retry list matches none of them (test_front_parse.py)."""

from __future__ import annotations

import json
import os
import re
import secrets
import tempfile
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any
from urllib.parse import unquote_to_bytes

from spark.credentials import match_key
from spark.gateproto import ModelEntry

if TYPE_CHECKING:  # the front imports only what it runs on (Task 21's FRONT_MODULES)
    from spark.registry import ClientKey, KeyGroup, Registry

Headers = list[tuple[bytes, bytes]]
Receive = Callable[[], Awaitable[dict[str, Any]]]

# The paths llama-swap serves through the front (plan.md, *The front and the gate*), as route_not_served's words list
# them.
INFERENCE_PATHS = ("/v1/chat/completions", "/v1/completions", "/v1/responses", "/v1/messages", "/v1/embeddings",
                   "/v1/audio/transcriptions")
ROUTES: frozenset[tuple[str, str]] = frozenset({("GET", "/v1/models"), *(("POST", path) for path in INFERENCE_PATHS)})
HEALTH = ("GET", "/health")  # the front's own, answered with no key
FORM_PATHS = frozenset({"/v1/audio/transcriptions"})  # the routes whose body is a multipart form; the rest take JSON

SPOOL_ABOVE = 1 << 20  # a file part past this goes to disk
FIELDS_MAX = 1 << 20  # a form's fields that aren't files, in all: each is text, held in memory
MAX_PARTS = 32  # a form's parts, so its parts held in memory stay few
_PART_HEAD_MAX = 16 << 10  # one part's headers
_LINE_MAX = 256  # what may follow a delimiter before its line ends: padding, which RFC 2046 allows
_READ_CHUNK = 1 << 20  # a spooled file is sent on in pieces this big

BAD_REQUEST = "invalid_request"  # the code of every 400
TOO_LARGE = "request_too_large"  # the code of every 413

# The front's own words for a body it can't take.
NOT_JSON = "The request's body isn't JSON, so the front couldn't read which model it asks for."
NOT_AN_OBJECT = "The request's body isn't a JSON object, so the front couldn't read which model it asks for."
NO_MODEL = 'The request names no model: its body needs "model", the model\'s name as text.'
FORM_NOT_TAKEN = "This address takes a JSON body, not a form."
NOT_A_FORM = 'This address takes an upload as a multipart form, with the model\'s name in its "model" field.'
NO_BOUNDARY = "The upload's Content-Type gives no boundary the front can use, so it couldn't read the form."
MALFORMED_FORM = "The upload's form is malformed, so the front couldn't read it."
INCOMPLETE_FORM = "The upload's form stops before its closing boundary, so the front couldn't read it."
ENCODED_PART = ("A part of the upload has a Content-Transfer-Encoding the front doesn't take; send its bytes as they "
                "are.")
TWO_MODELS = 'The form names the model more than once; send one "model" field.'
NO_MODEL_IN_FORM = 'The form names no model: add a "model" field with the model\'s name.'
FIELD_NOT_TEXT = "A field of the form that isn't a file isn't UTF-8 text."
FIELDS_TOO_BIG = (f"The form's fields that aren't files come to more than {FIELDS_MAX >> 20} MiB; only a file may be "
                  "that large.")
TOO_MANY_PARTS = f"The form has more than {MAX_PARTS} parts."
BOTH_LENGTHS = ("The request gives both Content-Length and Transfer-Encoding, so its length is unclear; send one of "
                "them.")
BAD_LENGTH = "The request's Content-Length isn't one whole number."

_TOKEN = r"[!#$%&'*+.^_`|~0-9A-Za-z-]+"  # RFC 9110's token
_HEADER_NAME = re.compile(_TOKEN)
_KIND = re.compile(rf"[ \t]*({_TOKEN}(?:/{_TOKEN})?)[ \t]*")
_PARAM = re.compile(rf'[ \t]*;[ \t]*({_TOKEN})[ \t]*=[ \t]*("(?:[^"\\]|\\.)*"|{_TOKEN})')
_ESCAPE = re.compile(r"\\(.)")
_CONTROL = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")  # a control character other than a tab
_BOUNDARY = re.compile(r"[0-9A-Za-z'()+_,\-./:=? ]{0,69}[0-9A-Za-z'()+_,\-./:=?]")  # RFC 2046's
_PLAIN_ENCODINGS = ("7bit", "8bit", "binary")
# What never goes upstream: the client's credentials, its framing, and the body's own headers, which the front writes
# for the body it rebuilds.
_DROPPED = frozenset({b"authorization", b"x-api-key", b"cookie", b"proxy-authorization", b"host", b"content-length",
                      b"transfer-encoding", b"content-type", b"content-encoding", b"expect"})
# RFC 9110's hop-by-hop headers, and the ones the connection header names.
_HOP_BY_HOP = frozenset({b"connection", b"keep-alive", b"proxy-connection", b"te", b"trailer", b"upgrade",
                         b"proxy-authenticate"})


class Refused(Exception):
    """A request the front answers itself, as it reads it: its status, its code and its words."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message

    def body(self) -> dict:
        """OpenAI's error shape, as messages.Refusal's: `message` and `code` only."""
        return {"error": {"message": self.message, "code": self.code}}

    def headers(self) -> Headers:
        return [(b"content-type", b"application/json")]


class ClientGone(Exception):
    """The client went before its body came whole."""


def _bad(message: str) -> Refused:
    return Refused(400, BAD_REQUEST, message)


def too_big(max_bytes: int) -> Refused:
    """413: a body over `max_bytes`, by its Content-Length or as it came."""
    return Refused(413, TOO_LARGE, f"The request is over {_size(max_bytes)}, the most the front takes at this address, "
                                   "so it wasn't read.")


def _size(count: int) -> str:
    for unit, shift in (("MiB", 20), ("KiB", 10)):
        if count >= 1 << shift and count % (1 << shift) == 0:
            return f"{count >> shift} {unit}"
    return f"{count} bytes"


# Keys and headers.

def authenticate(headers: Headers, digests: dict[str, bytes]) -> str | None:
    """The name of the key the request carries, in `Authorization: Bearer <key>` or `x-api-key: <key>`, or None: no
    key, a key no digest matches, an Authorization that isn't a bearer key, or headers that carry two different keys.
    Header names come lower-cased, as ASGI gives them."""
    presented: set[bytes] = set()
    for name, value in headers:
        if name == b"authorization":
            scheme, _, key = value.partition(b" ")
            if scheme.lower() != b"bearer":
                return None
            presented.add(key.strip(b" \t"))
        elif name == b"x-api-key":
            presented.add(value)
    if len(presented) != 1:
        return None
    try:
        key = presented.pop().decode("ascii")  # every key the Spark issues is printable ASCII
    except UnicodeDecodeError:
        return None
    return match_key(key, digests)


def clean_headers(headers: Headers) -> Headers:
    """The headers that go upstream: all but the client's credentials (`authorization`, `x-api-key`, `cookie`,
    `proxy-authorization`), `host`, its framing (`content-length`, `transfer-encoding`), the body's own headers
    (`content-type`, `content-encoding`, `expect`), which the front writes for the body it rebuilds, and the hop-by-hop
    headers, those the `connection` header names among them."""
    named = {token.strip().lower() for name, value in headers if name == b"connection" for token in value.split(b",")}
    return [(name, value) for name, value in headers
            if name not in _DROPPED and name not in _HOP_BY_HOP and name not in named]


def both_lengths(headers: Headers) -> bool:
    """Both Content-Length and Transfer-Encoding: a length two readers can disagree on, refused 400."""
    names = {name for name, _ in headers}
    return b"content-length" in names and b"transfer-encoding" in names


def declared_length(headers: Headers) -> int | None:
    """The body's Content-Length, or None when it has none (a chunked body); 400 for one that isn't a whole number,
    or two that differ."""
    values = {value for name, value in headers if name == b"content-length"}
    if not values:
        return None
    if len(values) != 1:
        raise _bad(BAD_LENGTH)
    value = values.pop()
    if not value.isdigit():  # bytes.isdigit is ASCII's digits only
        raise _bad(BAD_LENGTH)
    return int(value)


# Bodies.

class _Body:
    """A request's body as uvicorn gives it, a message at a time, counted against `max_bytes` as it comes."""

    def __init__(self, receive: Receive, max_bytes: int):
        self._receive, self._max = receive, max_bytes
        self._total = 0
        self._done = False

    async def next(self) -> bytes | None:
        """The body's next piece, or None once it has all come. 413 past the cap; ClientGone if the client goes."""
        while not self._done:
            message = await self._receive()
            if message["type"] == "http.disconnect":
                raise ClientGone
            chunk = message.get("body", b"")
            self._done = not message.get("more_body", False)
            self._total += len(chunk)
            if self._total > self._max:
                raise too_big(self._max)
            if chunk:
                return chunk
        return None


def _not_json(constant: str) -> None:
    """json's parse_constant: NaN and the infinities, which Python reads and JSON (and llama-swap) doesn't."""
    raise ValueError("a constant JSON doesn't have")


async def read_json(receive: Receive, *, max_bytes: int) -> tuple[dict, bytes]:
    """The JSON body, read whole within `max_bytes` (413 past it), and the bytes to send on, re-serialized from it,
    compact. 400 for a body that isn't a JSON object, or has no `model` that is text; an empty `model` is a name, which
    model_not_found then words."""
    body = _Body(receive, max_bytes)
    data = bytearray()
    while (chunk := await body.next()) is not None:
        data += chunk
    try:
        parsed = json.loads(data, parse_constant=_not_json)
    except (ValueError, RecursionError):  # UnicodeDecodeError is a ValueError
        raise _bad(NOT_JSON) from None
    if not isinstance(parsed, dict):
        raise _bad(NOT_AN_OBJECT)
    if not isinstance(parsed.get("model"), str):
        raise _bad(NO_MODEL)
    try:
        sent = json.dumps(parsed, separators=(",", ":")).encode()
    except (ValueError, RecursionError):
        raise _bad(NOT_JSON) from None
    return parsed, sent


@dataclass(frozen=True)
class FilePart:
    """A form's file part: in memory (bytes) up to the spool threshold, else the spooled file's path."""

    name: str
    filename: str
    content_type: str | None
    path_or_bytes: Path | bytes


@dataclass
class Form:
    """A multipart form as the front read it: its one `model` field's value, every field that isn't a file (`model`
    among them), in order, and every file part."""

    model: str
    fields: list[tuple[str, str]]
    files: list[FilePart]

    def close(self) -> None:
        """Deletes every spooled file; safe to call more than once."""
        for part in self.files:
            if isinstance(part.path_or_bytes, Path):
                part.path_or_bytes.unlink(missing_ok=True)


def _params(value: str) -> tuple[str, dict[str, str]] | None:
    """A header value's type and its parameters, `type; name=value; …`, each value a token or a quoted string (RFC
    9110): (the type in lower case, {each name in lower case: its value}). None when it doesn't parse, or names a
    parameter twice."""
    kind = _KIND.match(value)
    if kind is None:
        return None
    params: dict[str, str] = {}
    at = kind.end()
    while value[at:].strip(" \t;"):
        param = _PARAM.match(value, at)
        if param is None:
            return None
        name, raw = param.group(1).lower(), param.group(2)
        if name in params:
            return None
        params[name] = _ESCAPE.sub(r"\1", raw[1:-1]) if raw.startswith('"') else raw
        at = param.end()
    return kind.group(1).lower(), params


def _boundary(content_type: str) -> bytes:
    parsed = _params(content_type)
    kind = parsed[0] if parsed else content_type.split(";", 1)[0].strip().lower()
    if kind != "multipart/form-data":
        raise _bad(NOT_A_FORM)
    boundary = parsed[1].get("boundary") if parsed else None
    if boundary is None or not _BOUNDARY.fullmatch(boundary):
        raise _bad(NO_BOUNDARY)
    return boundary.encode("ascii")


def _ext_value(value: str) -> str | None:
    """An RFC 8187 value, `charset'language'percent-encoded`, in UTF-8 or ASCII; None for anything else."""
    charset, mark, rest = value.partition("'")
    _, mark2, encoded = rest.partition("'")
    if not (mark and mark2) or charset.lower() not in ("utf-8", "us-ascii"):
        return None
    try:
        return unquote_to_bytes(encoded).decode(charset.lower())
    except UnicodeDecodeError:
        return None


def _part_head(block: bytes) -> tuple[str, str | None, str | None]:
    """A part's headers: (its name, its file name or None, its content type or None). Its Content-Disposition is
    `form-data` with a name; `filename*` (RFC 8187) wins over `filename`, as Go's mime.ParseMediaType reads them. A
    header the front reads given twice, a parameter given twice, or a `filename*` it can't decode is refused: stricter
    than Go here and there, which is safe, since the form goes on rebuilt from what the front read."""
    try:
        text = block.decode("utf-8")
    except UnicodeDecodeError:
        raise _bad(MALFORMED_FORM) from None
    found: dict[str, str] = {}
    for line in text.split("\r\n"):
        name, colon, value = line.partition(":")
        if not colon or not _HEADER_NAME.fullmatch(name) or _CONTROL.search(value):
            raise _bad(MALFORMED_FORM)
        name = name.lower()
        if name in ("content-disposition", "content-type", "content-transfer-encoding"):
            if name in found:
                raise _bad(MALFORMED_FORM)
            found[name] = value.strip(" \t")
    encoding = found.get("content-transfer-encoding")
    if encoding is not None and encoding.lower() not in _PLAIN_ENCODINGS:
        raise _bad(ENCODED_PART)
    disposition = _params(found.get("content-disposition", ""))
    if disposition is None or disposition[0] != "form-data":
        raise _bad(MALFORMED_FORM)
    params = disposition[1]
    name, filename = params.get("name"), params.get("filename")
    if "filename*" in params:
        filename = _ext_value(params["filename*"])
        if filename is None:
            raise _bad(MALFORMED_FORM)
    content_type = found.get("content-type")
    if not name or (content_type is not None and not content_type.isascii()) \
            or any(text is not None and _CONTROL.search(text) for text in (name, filename)):
        raise _bad(MALFORMED_FORM)
    return name, filename or None, content_type


@dataclass
class _Part:
    name: str
    filename: str | None  # None: a field
    content_type: str | None
    data: bytearray
    path: Path | None = None
    file: IO[bytes] | None = None


def _delimiter_ends(buf: bytearray, after: int) -> bool | None:
    """Whether a delimiter found in the data is one, by the bytes after it, as Go's multipart reader decides
    (matchAfterPrefix): followed by padding, a line's end or `--`, it is; followed by anything else, it is data. None
    while the bytes to decide haven't come."""
    if len(buf) <= after:
        return None
    if buf[after] in b" \t\r\n":
        return True
    if buf[after] == ord("-"):
        return None if len(buf) <= after + 1 else buf[after + 1] == ord("-")
    return False


class _FormReader:
    """A multipart/form-data body (RFC 7578), parsed as it comes."""

    def __init__(self, boundary: bytes, spool_dir: Path, spool_above: int):
        self._dash = b"--" + boundary
        self._delimiter = b"\r\n" + self._dash
        self._spool_dir, self._spool_above = spool_dir, spool_above
        self._buf = bytearray()
        self._state = "start"
        self._parts = 0
        self._fields_size = 0
        self._part: _Part | None = None
        self.fields: list[tuple[str, str]] = []
        self.files: list[FilePart] = []
        self.spooled: list[Path] = []

    def feed(self, chunk: bytes) -> None:
        self._buf += chunk
        while self._step():
            pass

    def end(self) -> Form:
        if self._state != "end":
            raise _bad(INCOMPLETE_FORM)
        models = [value for name, value in self.fields if name == "model"]
        if not models:
            raise _bad(NO_MODEL_IN_FORM)
        if len(models) > 1:
            raise _bad(TWO_MODELS)
        return Form(models[0], self.fields, self.files)

    def discard(self) -> None:
        """Every spooled file deleted, the one being written included."""
        if self._part is not None and self._part.file is not None:
            self._part.file.close()
        for path in self.spooled:
            path.unlink(missing_ok=True)

    def _step(self) -> bool:
        """One step of the parse on what has come: True to take another, False until more comes."""
        buf = self._buf
        if self._state == "start":  # the body opens with the first delimiter; no preamble
            seen = min(len(buf), len(self._dash))
            if buf[:seen] != self._dash[:seen]:
                raise _bad(MALFORMED_FORM)
            if len(buf) < len(self._dash):
                return False
            del buf[:len(self._dash)]
            self._state = "delimited"
            return True
        if self._state == "delimited":  # after a delimiter: `--` ends the form, else padding and a line's end
            if len(buf) < 2:
                return False
            if buf.startswith(b"--"):
                self._state = "end"
                buf.clear()
                return False
            end = buf.find(b"\r\n")
            if end < 0:
                if len(buf) > _LINE_MAX:
                    raise _bad(MALFORMED_FORM)
                return False
            if buf[:end].strip(b" \t"):
                raise _bad(MALFORMED_FORM)
            del buf[:end + 2]
            self._state = "head"
            return True
        if self._state == "head":
            if buf.startswith(b"\r\n"):  # a part with no headers has no name
                raise _bad(MALFORMED_FORM)
            end = buf.find(b"\r\n\r\n")
            if end < 0:
                if len(buf) > _PART_HEAD_MAX:
                    raise _bad(MALFORMED_FORM)
                return False
            name, filename, content_type = _part_head(bytes(buf[:end]))
            del buf[:end + 4]
            self._parts += 1
            if self._parts > MAX_PARTS:
                raise _bad(TOO_MANY_PARTS)
            self._part = _Part(name, filename, content_type, bytearray())
            self._state = "data"
            return True
        if self._state == "data":
            at = buf.find(self._delimiter)
            while at >= 0:
                ends = _delimiter_ends(buf, at + len(self._delimiter))
                if ends is None:  # the delimiter's next bytes haven't come: keep it, write what is before it
                    self._write(buf[:at])
                    del buf[:at]
                    return False
                if ends:
                    self._write(buf[:at])
                    del buf[:at + len(self._delimiter)]
                    self._finish()
                    self._state = "delimited"
                    return True
                at = buf.find(self._delimiter, at + 1)
            keep = len(self._delimiter) - 1  # what may be the start of a delimiter still coming
            if len(buf) > keep:
                self._write(buf[:-keep])
                del buf[:-keep]
            return False
        buf.clear()  # the epilogue, after the closing delimiter: ignored
        return False

    def _write(self, data: bytearray) -> None:
        part = self._part
        if not data or part is None:
            return
        if part.filename is None:
            self._fields_size += len(data)
            if self._fields_size > FIELDS_MAX:
                raise _bad(FIELDS_TOO_BIG)
            part.data += data
            return
        if part.file is None and len(part.data) + len(data) > self._spool_above:
            fd, name = tempfile.mkstemp(prefix="upload-", dir=self._spool_dir)  # 0600, a name of its own
            part.path = Path(name)
            self.spooled.append(part.path)
            part.file = os.fdopen(fd, "wb")
            part.file.write(part.data)
            part.data = bytearray()
        if part.file is not None:
            part.file.write(data)
        else:
            part.data += data

    def _finish(self) -> None:
        part, self._part = self._part, None
        if part.filename is None:
            try:
                self.fields.append((part.name, part.data.decode("utf-8")))
            except UnicodeDecodeError:
                raise _bad(FIELD_NOT_TEXT) from None
        elif part.file is not None:
            part.file.close()
            self.files.append(FilePart(part.name, part.filename, part.content_type, part.path))
        else:
            self.files.append(FilePart(part.name, part.filename, part.content_type, bytes(part.data)))


async def read_form(receive: Receive, content_type: str, *, spool_dir: Path, max_bytes: int,
                    spool_above: int = SPOOL_ABOVE) -> Form:
    """The multipart form, read as it comes within `max_bytes` (413 past it): a file part over `spool_above` goes to
    a file in `spool_dir`, never to memory. 400 for a Content-Type that isn't multipart/form-data with a usable
    boundary, a form that is malformed or stops early, a field that isn't UTF-8 text, too many parts or too much
    field text, and no `model` field or two. Whatever ends it early deletes what it spooled; once it is read,
    Form.close() does."""
    reader = _FormReader(_boundary(content_type), spool_dir, spool_above)
    body = _Body(receive, max_bytes)
    try:
        while (chunk := await body.next()) is not None:
            reader.feed(chunk)
        return reader.end()
    except BaseException:
        reader.discard()
        raise


def _quote(text: str) -> str:
    """A name or a file name as a quoted string's inside: a backslash before each `"` and `\\`."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def encode_form(form: Form) -> tuple[str, AsyncIterator[bytes]]:
    """The form rebuilt for upstream: its Content-Type, with a boundary of the front's own, and its body, a piece at a
    time, each spooled file read from disk as it is sent. Its fields first, `model` among them, then its files."""
    boundary = f"spark-{secrets.token_hex(16)}"
    dash = f"--{boundary}\r\n".encode()

    def head(name: str, filename: str | None = None, content_type: str | None = None) -> bytes:
        disposition = f'Content-Disposition: form-data; name="{_quote(name)}"'
        if filename is None:
            return dash + f"{disposition}\r\n\r\n".encode()
        return dash + (f'{disposition}; filename="{_quote(filename)}"\r\n'
                       f"Content-Type: {content_type or 'application/octet-stream'}\r\n\r\n").encode()

    async def body() -> AsyncIterator[bytes]:
        for name, value in form.fields:
            yield head(name) + value.encode() + b"\r\n"
        for part in form.files:
            yield head(part.name, part.filename, part.content_type)
            if isinstance(part.path_or_bytes, bytes):
                yield part.path_or_bytes
            else:
                with part.path_or_bytes.open("rb") as file:
                    while chunk := file.read(_READ_CHUNK):
                        yield chunk
            yield b"\r\n"
        yield f"--{boundary}--\r\n".encode()

    return f"multipart/form-data; boundary={boundary}", body()


# The model list.

def _entry(raw: Any) -> ModelEntry:
    """A model list entry, checked and copied: `name` and `label` non-empty text, `roles` a list of text, `resident`
    true or false."""
    if not isinstance(raw, dict):
        raise ValueError("a model list entry isn't a mapping")
    name, roles, label, resident = (raw.get(field) for field in ("name", "roles", "label", "resident"))
    if not (isinstance(name, str) and name and isinstance(label, str) and label and isinstance(resident, bool)
            and isinstance(roles, list) and all(isinstance(role, str) for role in roles)):
        raise ValueError("a model list entry needs a name, a list of roles, a label, and resident true or false")
    return {"name": name, "roles": list(roles), "label": label, "resident": resident}


class ModelList:
    """The front's current model list, in its order: the deployed registry's at start (from_registry), then the gate's
    from each `hello` and `state` event (Task 22), replaced whole, the last one kept while the gate is down (Dan's
    decision, 2026-10-07)."""

    def __init__(self, entries: Iterable[ModelEntry] = ()):
        self._entries: tuple[ModelEntry, ...] = ()
        self.replace(entries)

    @classmethod
    def from_registry(cls, registry: Registry) -> ModelList:
        return cls({"name": model.name, "roles": list(model.roles), "label": model.label, "resident": model.resident}
                   for model in registry.models.values())

    def replace(self, entries: Iterable[ModelEntry]) -> None:
        """The list replaced by `entries`, each checked first: a bad one changes nothing (ValueError)."""
        self._entries = tuple(_entry(entry) for entry in entries)

    def __iter__(self) -> Iterator[ModelEntry]:
        return iter(self._entries)

    def __len__(self) -> int:
        return len(self._entries)


def resolve(name: str, models: ModelList) -> ModelEntry | None:
    """The model a request names: by its name first, then by one of its roles, as llama-swap looks them up; None for a
    name neither matches."""
    for entry in models:
        if entry["name"] == name:
            return entry
    for entry in models:
        if name in entry["roles"]:
            return entry
    return None


# Caps.

class KeyCaps:
    """Each key's caps, by its group (Task 4): its requests waiting for a load (`max_waiting`), and its requests open,
    waiting or answering (`max_open`). Past either, the front answers too_many_requests. A key the list doesn't have
    has no room."""

    def __init__(self, keys: dict[str, ClientKey], groups: dict[str, KeyGroup]):
        self._caps = {name: (groups[key.group].max_waiting, groups[key.group].max_open) for name, key in keys.items()}
        self._waiting: Counter[str] = Counter()
        self._open: Counter[str] = Counter()

    def _enter(self, counts: Counter[str], key: str, cap: int | None) -> bool:
        if cap is None or counts[key] >= cap:
            return False
        counts[key] += 1
        return True

    def enter_open(self, key: str) -> bool:
        return self._enter(self._open, key, self._caps[key][1] if key in self._caps else None)

    def enter_waiting(self, key: str) -> bool:
        return self._enter(self._waiting, key, self._caps[key][0] if key in self._caps else None)

    def leave_open(self, key: str) -> None:
        self._open[key] = max(self._open[key] - 1, 0)

    def leave_waiting(self, key: str) -> None:
        self._waiting[key] = max(self._waiting[key] - 1, 0)

    def open(self, key: str) -> int:
        return self._open[key]

    def waiting(self, key: str) -> int:
        return self._waiting[key]


# The journal.

def journal_line(key: str, model: str, status: int | str, seconds: float) -> str:
    """`front: <key> <model> <status> <seconds, one decimal>s`, nothing more (plan.md, *Its journal lines*): the key's
    name and the model's, never a key, a digest, a header, a body or a file name. `-` stands for a key or a model the
    request didn't name; a status that is a word says how a request ended without one (`gone`: its client went
    first)."""
    return f"front: {key} {model} {status} {max(seconds, 0.0):.1f}s"

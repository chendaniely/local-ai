"""`spark usage import`: the Mac's per-message token rows for the by-hand engines (the design's part 3), read from
stdin, checked, and merged into `opencode.jsonl` by message id. The input is untrusted: every row is checked before
anything is written, a refusal's reason is one of a few fixed words, and the verdict never holds a value from the
input. At module level this imports only the standard library and `spark.paths`."""

from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from spark import paths

FIELDS = ("v", "src", "mid", "sid", "prov", "model", "t0", "t1", "in", "out", "reas", "cr", "cw", "end")
ENDS = ("done", "error", "cut")
MAX_LINE = 4096
MAX_LINES = 200_000
_EARLIEST = 1_767_225_600.0  # 2026-01-01T00:00Z
_TEXT = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
_HEX16 = re.compile(r"[0-9a-f]{16}")
_REASONS = ("field", "text", "count", "time", "end", "version", "source")


def _count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 2**53


def _time(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def check(obj: object, now: float) -> str | None:
    """None for a good row, else the reason in fixed words, never the value."""
    if not isinstance(obj, dict) or set(obj) != set(FIELDS):
        return "field"
    if obj["v"] != 1 or isinstance(obj["v"], bool):
        return "version"
    if obj["src"] != "opencode":
        return "source"
    for key in ("mid", "prov", "model"):
        if not isinstance(obj[key], str) or not _TEXT.fullmatch(obj[key]):
            return "text"
    if not isinstance(obj["sid"], str) or not _HEX16.fullmatch(obj["sid"]):
        return "text"
    if not all(_count(obj[k]) for k in ("in", "out", "reas", "cr", "cw")):
        return "count"
    t0, t1 = obj["t0"], obj["t1"]
    if not (_time(t0) and _time(t1) and _EARLIEST <= t0 <= t1 <= now + 86400):  # NaN fails the comparisons
        return "time"
    if obj["end"] not in ENDS:
        return "end"
    return None


@dataclass(frozen=True)
class Verdict:
    new: int
    changed: int
    unchanged: int
    refused: int
    reasons: dict[str, int]

    def line(self) -> str:
        text = f"import: {self.new} new, {self.changed} changed, {self.unchanged} unchanged, {self.refused} refused"
        if self.reasons:
            text += " (" + ", ".join(f"{w} {self.reasons[w]}" for w in _REASONS if w in self.reasons) + ")"
        return text


def _parse(line: str) -> object:
    try:
        return json.loads(line)
    except (ValueError, RecursionError):  # a torn line, or nesting too deep
        return None


def _stored(path: Path) -> tuple[dict[str, dict], bool]:
    """The last stored object per mid, and whether the file ends without a newline (a torn last line)."""
    last: dict[str, dict] = {}
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return last, False
    for raw in data.decode("utf-8", errors="replace").splitlines():
        obj = _parse(raw)
        if isinstance(obj, dict) and isinstance(obj.get("mid"), str):
            last[obj["mid"]] = obj
    return last, bool(data) and not data.endswith(b"\n")


def import_rows(lines: Iterable[str], folder: Path, now: float) -> Verdict:
    """Check every line, then merge the good ones under a lock: a new or changed row is appended, an unchanged one
    isn't. The whole batch is read before anything is written."""
    good: list[dict] = []
    refused: Counter[str] = Counter()
    for number, line in enumerate(lines, 1):
        if number > MAX_LINES or len(line.encode("utf-8", errors="replace")) > MAX_LINE:
            refused["field"] += 1
            continue
        if not line.strip():
            continue
        obj = _parse(line)
        why = check(obj, now)
        if why:
            refused[why] += 1
        else:
            good.append(obj)  # type: ignore[arg-type]
    folder.mkdir(parents=True, exist_ok=True)
    new = changed = unchanged = 0
    lock = os.open(folder / ".import.lock", os.O_WRONLY | os.O_CREAT, 0o640)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = folder / "opencode.jsonl"
        last, torn = _stored(path)
        out = []
        for obj in good:
            before = last.get(obj["mid"])
            if before == obj:
                unchanged += 1
                continue
            if before is None:
                new += 1
            else:
                changed += 1
            last[obj["mid"]] = obj
            out.append(json.dumps(obj, sort_keys=True, allow_nan=False) + "\n")
        if out:
            payload = (("\n" if torn else "") + "".join(out)).encode()
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o640)
            try:
                os.fchmod(fd, 0o640)
                if os.write(fd, payload) != len(payload):
                    raise OSError("short write to the imported rows' file")
            finally:
                os.close(fd)
        handle, temp = tempfile.mkstemp(dir=folder, prefix=".imported-")
        try:
            with os.fdopen(handle, "w") as stream:
                stream.write(repr(float(now)) + "\n")
            os.chmod(temp, 0o640)
            os.replace(temp, folder / "opencode.imported")
        except BaseException:
            try:
                os.unlink(temp)
            except OSError:
                pass
            raise
    finally:
        os.close(lock)
    return Verdict(new, changed, unchanged, sum(refused.values()), dict(refused))


def run(stream: Iterable[str], now: float, folder: Path | None = None) -> Verdict:
    """The verb's work."""
    return import_rows(stream, paths.USAGE_SESSIONS_DIR if folder is None else folder, now)

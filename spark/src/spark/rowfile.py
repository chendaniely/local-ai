"""A row file: JSONL rows written from a thread of its own, never in a request's way.

The front puts one row of numbers per request into `usage-YYYY-MM.jsonl` and the gate its ledger rows into
`ledger-YYYY-MM.jsonl`. Both are long-lived services that must start whatever state the folder is in and whose
requests this writer must never slow or fail, so:

- `put` serializes the row and queues the bytes under a lock held only for a few instructions, never during I/O. It
  never blocks and never raises, also after `close`; a row it can't take is dropped and counted.
- One daemon thread writes each line as one `write()` on an `O_APPEND` descriptor. A file it creates is `fchmod`ed to
  `mode`, so the mode never depends on the umask. The month is by UTC, from the row's own time.
- A failure (a short write, any `Exception`) drops the row, turns counting off and closes the descriptor; it reopens
  only after `retry_s` (by `monotonic`). The next line written carries `"dropped": n`, and starts after a `\\n` when a
  write may have left a torn line.
- `on_off` is called once per off-spell with the error's class name (or `disk`), never its text; `on_on` when the
  spell ends. Both only log; an exception from either is swallowed."""

from __future__ import annotations

import collections
import json
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path

_DISK_CHECK_EVERY = 1000  # rows between free-space checks while counting
_WINDOW_S = 60.0


class ShortWrite(Exception):
    """write() took fewer bytes than the line."""


def _month_name(at: float) -> str:
    t = time.gmtime(at)  # UTC, never the local zone
    return f"{t.tm_year:04d}-{t.tm_mon:02d}"


class RowFile:
    def __init__(
        self,
        folder: Path,
        prefix: str,
        *,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        write: Callable[[int, bytes], int] = os.write,
        statvfs: Callable[[str], object] = os.statvfs,
        queue_bytes: int = 4 << 20,
        mode: int = 0o640,
        retry_s: float = 30.0,
        free_floor: int = 20 << 30,
        on_off: Callable[[float, str], None] = lambda since, why: None,
        on_on: Callable[[], None] = lambda: None,
    ) -> None:
        self._folder = Path(folder)
        self._prefix = prefix
        self._clock = clock
        self._monotonic = monotonic
        self._write = write
        self._statvfs = statvfs
        self._queue_bytes = queue_bytes
        self._mode = mode
        self._retry_s = retry_s
        self._free_floor = free_floor
        self._on_off = on_off
        self._on_on = on_on

        self._cond = threading.Condition()  # guards the five fields below
        self._queue: collections.deque[tuple[bytes, float]] = collections.deque()
        self._queued = 0
        self._dropped = 0
        self._stop = False
        self._busy = False

        # Owned by the writer thread alone.
        self._fd: int | None = None
        self._month: str | None = None
        self._off_since: float | None = None
        self._failed_at = 0.0
        self._need_newline = False
        self._since_check = 0

        self._thread = threading.Thread(target=self._run, name=f"rowfile-{prefix}", daemon=True)
        self._thread.start()

    # ---- the caller's side: never blocks, never raises -------------------------------------------------------

    def put(self, row: dict, at: float) -> None:
        try:
            try:
                if not isinstance(row, dict):
                    raise TypeError("a row is a dict")
                line = json.dumps(row, separators=(",", ":"), allow_nan=False).encode("utf-8")
            except Exception:
                self._count_drop()
                return
            with self._cond:
                if self._stop or self._queued + len(line) > self._queue_bytes:
                    self._dropped += 1
                    return
                self._queue.append((line, at))
                self._queued += len(line)
                self._cond.notify()
        except BaseException:  # nothing a request does may hear of this writer
            pass

    def off_since(self) -> float | None:
        """When counting went off (by `clock`), or None while it is on."""
        return self._off_since

    def close(self, timeout_s: float) -> None:
        """Stops the thread after it drains what is queued, waiting at most `timeout_s` for it. Never blocks on a
        full queue; a write stuck in the kernel is left behind (the thread is a daemon)."""
        try:
            with self._cond:
                self._stop = True
                self._cond.notify_all()
            self._thread.join(timeout_s)
        except BaseException:
            pass

    # ---- the writer thread -----------------------------------------------------------------------------------

    def _drained(self) -> bool:
        """True when nothing is queued or being written (for tests)."""
        with self._cond:
            return not self._queue and not self._busy

    def _count_drop(self) -> None:
        with self._cond:
            self._dropped += 1

    def _run(self) -> None:
        try:
            self._start()
            while True:
                with self._cond:
                    while not self._queue and not self._stop:
                        self._cond.wait()
                    if not self._queue:
                        return
                    line, at = self._queue.popleft()
                    self._queued -= len(line)
                    self._busy = True
                try:
                    self._write_row(line, at)
                finally:
                    with self._cond:
                        self._busy = False
        finally:
            self._close_fd()

    def _start(self) -> None:
        """Tries the month's file once, so a folder it can't write shows as off at once."""
        try:
            self._ensure_open(_month_name(self._clock()))
        except Exception as exc:
            self._fail(type(exc).__name__)

    def _low_disk(self) -> bool:
        try:
            st = self._statvfs(str(self._folder))
            return st.f_bavail * st.f_frsize < self._free_floor  # type: ignore[attr-defined]
        except Exception:
            return False  # an unreadable gauge is not a reason to stop counting

    def _close_fd(self) -> None:
        fd, self._fd, self._month = self._fd, None, None
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass

    def _go_off(self, why: str) -> None:
        self._close_fd()
        self._failed_at = self._monotonic()
        if self._off_since is None:
            self._off_since = self._clock()
            try:
                self._on_off(self._off_since, why)
            except Exception:
                pass

    def _fail(self, why: str) -> None:
        self._go_off(why)

    def _ensure_open(self, month: str) -> bool:
        """True with the month's descriptor open; False when counting is off and stays off for now. An error from
        the filesystem raises."""
        if self._fd is not None and self._month == month:
            return True
        self._close_fd()
        if self._off_since is not None and self._monotonic() - self._failed_at < self._retry_s:
            return False
        self._since_check = 0
        if self._low_disk():
            self._go_off("disk")
            return False
        path = self._folder / f"{self._prefix}-{month}.jsonl"
        flags = os.O_WRONLY | os.O_APPEND | os.O_CLOEXEC
        try:
            fd = os.open(path, flags | os.O_CREAT | os.O_EXCL, self._mode)
            created = True
        except FileExistsError:
            fd = os.open(path, flags | os.O_CREAT, self._mode)
            created = False
        if created:
            try:
                os.fchmod(fd, self._mode)  # not the umask's mode
            except BaseException:
                os.close(fd)
                raise
        self._fd, self._month = fd, month
        return True

    def _write_row(self, line: bytes, at: float) -> None:
        attempted = False
        try:
            if not self._ensure_open(_month_name(at)):
                self._count_drop()
                return
            with self._cond:
                n = self._dropped
            data = line
            if n:
                data = (line[:-1] if line == b"{}" else line[:-1] + b",") + b'"dropped":%d}' % n
            data += b"\n"
            if self._need_newline:
                data = b"\n" + data
            attempted = True
            written = self._write(self._fd, data)  # type: ignore[arg-type]
            if written != len(data):
                raise ShortWrite()
        except Exception as exc:
            if attempted:
                self._need_newline = True
            self._count_drop()
            self._fail(type(exc).__name__)
            return
        self._need_newline = False
        with self._cond:
            self._dropped -= n
        if self._off_since is not None:
            self._off_since = None
            try:
                self._on_on()
            except Exception:
                pass
        self._since_check += 1
        if self._since_check >= _DISK_CHECK_EVERY:
            self._since_check = 0
            if self._low_disk():
                self._go_off("disk")


class RefusedLimit:
    """A per-key rate limit for rows about refusals, so a flood of them can't flood the file. A fixed one-minute
    window per key, started by the first `now` it sees: `allow` is True for the first `per_minute` calls in a
    window and False past them, counting each False; `folded` returns that count and resets it. No I/O, and not
    thread-safe: the front calls it from its event loop."""

    def __init__(self, per_minute: int = 60) -> None:
        self._per_minute = per_minute
        self._windows: dict[str, list[float]] = {}  # key -> [window start, allowed in it]
        self._held: dict[str, int] = {}

    def allow(self, key: str, now: float) -> bool:
        window = self._windows.get(key)
        if window is None or now - window[0] >= _WINDOW_S:
            if len(self._windows) > 1024:  # keep the table small: forget windows that have run out
                self._windows = {k: w for k, w in self._windows.items() if now - w[0] < _WINDOW_S}
            window = self._windows[key] = [now, 0.0]
        if window[1] < self._per_minute:
            window[1] += 1
            return True
        self._held[key] = self._held.get(key, 0) + 1
        return False

    def folded(self, key: str) -> int:
        return self._held.pop(key, 0)

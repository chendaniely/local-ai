"""The processes behind the memory figures (website/design/plan.md, *Admission and memory rules*): each engine's
anonymous RSS, rule 9's growth read; the engines' pids by their ports; nvidia-smi's per-process list; and the top memory
holders a refusal names (rule 7). Everything is read from /proc, which each function takes as `proc`, so the tests read
trees they build.

A process is named by its short name, `comm`, and its user, never by its command line, which can hold anything (a
token, a path). A process that exits between the listing and a read is passed over. A /proc that lists another user's
process but won't let the gate read it is an error the caller hears of, since the holders would otherwise leave it out
unsaid; one that doesn't list it at all can't be told apart, which is why the gate's unit gets no
ProtectProc=invisible (plan.md, *The front and the gate*)."""

from __future__ import annotations

import pwd
from pathlib import Path
from urllib.parse import urlsplit

from spark.memory import KIB_PER_GIB
from spark.messages import Holder, _one_line

PROC = Path("/proc")
ENGINES = ("llama-server", "whisper-server")  # the comm of every engine render starts
NVIDIA_APPS = ["nvidia-smi", "--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"]
MIB_PER_GIB = 1024
HOLDER_MIN_GIB = 1.0  # a process outside the stack is named from this size up
_GONE = (FileNotFoundError, ProcessLookupError)  # the process exited between the listing and the read


def rss_anon_gib(pid: int, *, proc: Path = PROC) -> float | None:
    """The process's anonymous RSS, `RssAnon` in its status, in GiB: None for a process gone or a status without it (a
    kernel thread, a zombie). Never VmRSS, whose file-backed part is page cache that MemAvailable still counts."""
    status = _status(pid, proc)
    return None if status is None else _rss_anon(status)


def port_of(proxy: str) -> int | None:
    """The port of `/running`'s `proxy` field (`http://127.0.0.1:801` → 801), or None for one with no port."""
    try:
        return urlsplit(proxy).port
    except ValueError:  # a port that isn't a number, or no URL at all
        return None


def start_time(pid: int, *, proc: Path = PROC) -> int | None:
    """When the process started, in clock ticks since boot: field 22 of `<proc>/<pid>/stat`. None for a process gone,
    or a stat with no such field. The name, in parentheses, comes second and can hold spaces and parentheses of its
    own, so the fields are read after its last `)`: field 3 first, field 22 the 20th word (checked on the Spark,
    2026-10-08). With the pid, it names one process for the life of the boot, since a pid can be handed out again."""
    try:
        text = (proc / str(pid) / "stat").read_bytes()
    except _GONE:
        return None
    words = text[text.rfind(b")") + 1:].split()
    if b")" not in text or len(words) < 20 or not words[19].isdigit():
        return None
    return int(words[19])


def engine_pid(port: int, *, spark_uid: int, recorded: int | None = None, recorded_start: int | None = None,
               proc: Path = PROC) -> int | None:
    """The engine serving `port`. A `recorded` pid (launch's, which the gate keeps in state.ticketed) is taken as it
    is, whatever its comm, so a later engine kind isn't counted as an outside holder, but only while it is alive, runs
    as spark, and started at `recorded_start` (its Ticketed.start_time, start_time's ticks): a pid the kernel handed to
    another process of spark's after the engine died is never read as the engine, nor its RssAnon as the engine's
    growth (the controller's ruling at Task 8's review). With no `recorded_start`, no recorded pid is trusted.
    Otherwise the process whose real uid is spark's, whose comm is an engine's, and whose argv holds `--port` and then
    the port; None when there is none."""
    if recorded is not None and recorded_start is not None:
        status = _status(recorded, proc)
        if (status is not None and _alive(status) and _real_uid(status) == spark_uid
                and start_time(recorded, proc=proc) == recorded_start):
            return recorded
    wanted = [b"--port", str(port).encode()]
    for pid in _pids(proc):
        status = _status(pid, proc)
        if status is None or _real_uid(status) != spark_uid or _comm(pid, proc) not in ENGINES:
            continue
        argv = _argv(pid, proc) or []
        if any(argv[i:i + 2] == wanted for i in range(len(argv) - 1)):
            return pid
    return None


def parse_nvidia_apps(text: str) -> dict[int, float | None]:
    """NVIDIA_APPS's output, `<pid>, <MiB>` lines, as {pid: GiB}. A figure nvidia-smi doesn't give (`[N/A]`) reads as
    None, unknown; a line with no pid is passed over."""
    apps: dict[int, float | None] = {}
    for line in text.splitlines():
        pid, _, used = line.partition(",")
        try:
            number = int(pid)
        except ValueError:
            continue
        try:
            apps[number] = int(used) / MIB_PER_GIB
        except ValueError:
            apps[number] = None
    return apps


def top_holders(loaded: dict[str, tuple[str, float]], nvidia: dict[int, float | None], *, engine_pids: set[int],
                dan_uids: set[int], proc: Path = PROC, limit: int = 3) -> list[Holder]:
    """The largest memory holders, largest first, `limit` of them (rule 7). `loaded`, each loaded model's
    {name: (label, GiB it holds now)}, gives the stack's, each by its label. Every other process holding at least
    HOLDER_MIN_GIB is named `<comm> (<user>)`, at the larger of its nvidia-smi figure (`nvidia`, by pid) and its
    RssAnon, and is Dan's when its real uid is in `dan_uids`. `engine_pids`, the engines' own, are left out: they are
    the models. Every name is put on one line as messages puts it, with no control character, since comm is the
    process's own raw text and a holder's name reaches a terminal (`spark status`) as well as the messages."""
    holders = [Holder(_one_line(label), gib, False) for label, gib in loaded.values()]
    for pid in _pids(proc):
        if pid in engine_pids:
            continue
        status = _status(pid, proc)
        if status is None or (uid := _real_uid(status)) is None:
            continue
        figures = [gib for gib in (nvidia.get(pid), _rss_anon(status)) if gib is not None]
        if not figures or max(figures) < HOLDER_MIN_GIB:
            continue
        comm = _comm(pid, proc)
        if comm is not None:
            holders.append(Holder(_one_line(f"{comm} ({_user(uid)})"), max(figures), uid in dan_uids))
    holders.sort(key=lambda h: (-h.gib, h.name))
    return holders[:limit]


def _pids(proc: Path) -> list[int]:
    """The processes /proc lists, in pid order; its other entries (meminfo, sys, self) aren't processes."""
    return sorted(int(entry.name) for entry in proc.iterdir() if entry.name.isascii() and entry.name.isdigit())


def _status(pid: int, proc: Path) -> dict[str, str] | None:
    """`<proc>/<pid>/status` as {field: value}, or None for a process gone. Its lines are split at the kernel's line
    breaks only, which it escapes in Name: never at what else Python counts as one (\\r, \\x1c, \\x85), which a process
    could put in its name to write a line of its own here."""
    try:
        text = (proc / str(pid) / "status").read_bytes().decode(errors="replace")
    except _GONE:
        return None
    fields: dict[str, str] = {}
    for line in text.split("\n"):
        key, sep, value = line.partition(":")
        if sep:
            fields[key] = value.strip()
    return fields


def _rss_anon(status: dict[str, str]) -> float | None:
    """RssAnon (`1048576 kB`) in GiB, or None where status has no memory lines."""
    value = status.get("RssAnon")
    return None if value is None else int(value.split()[0]) / KIB_PER_GIB


def _alive(status: dict[str, str]) -> bool:
    """Neither a zombie (exited, not yet reaped) nor dead: such a process holds no memory and serves nothing."""
    return not status.get("State", "").startswith(("Z", "X"))


def _real_uid(status: dict[str, str]) -> int | None:
    """The real uid, the first of Uid's four (real, effective, saved, filesystem)."""
    ids = status.get("Uid", "").split()
    return int(ids[0]) if ids else None


def _comm(pid: int, proc: Path) -> str | None:
    """The process's short name, as `comm` holds it, without the kernel's newline; None for a process gone. Read as
    bytes, so no \\r in it is turned into a line break; top_holders puts every name on one line."""
    try:
        return (proc / str(pid) / "comm").read_bytes().decode(errors="replace").removesuffix("\n")
    except _GONE:
        return None


def _argv(pid: int, proc: Path) -> list[bytes] | None:
    """The process's arguments, which `cmdline` holds each with a NUL after it; None for a process gone. Only engine_pid
    reads them, to match `--port`; nothing names a process by them."""
    try:
        data = (proc / str(pid) / "cmdline").read_bytes()
    except _GONE:
        return None
    return data.split(b"\0")[:-1] if data.endswith(b"\0") else data.split(b"\0")


def _user(uid: int) -> str:
    """The uid's user name, or the uid itself for one with no name (a container's, say)."""
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)

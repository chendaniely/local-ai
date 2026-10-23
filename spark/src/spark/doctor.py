"""`spark doctor` (`make doctor`) — Phase 0's guardrails and Phase 2a's stack, checked in one pass (Task 33).

Run it on the Spark, from the repo root, after any update, a reboot or upgrade day, and after the cutover (Task 38):
before it, and after a rollback to Phase 1's layout, 9100 is Phase 1's llama-swap, and the lines for the front, the
gate, the sockets and the scenarios fail. It needs no sudo, and it is read-only on the box: it loads no model, restarts
nothing, and writes nothing but its own lines. A model is asked something only once the gate's view shows it loaded,
so the request can't start a load, and make-room is asked only for its plan, which unloads nothing. What its requests
leave behind is what any request leaves: the front's journal line for each; the gate's record of the refusals it asks
for on purpose (S03's model and the routes the front doesn't serve), which `spark status` shows under *recent*; and
make-room's plan, which the gate keeps in memory among its newest few. The front line sends llama-swap's own admin
routes, `GET /unload` among them, with your key, as the plan's check requires: it relies on the front's route filter to
refuse them, so a front whose filter were broken would pass `GET /unload` on to llama-swap with its own key, and that
would unload every model.

**Is 9100 the front?** Decided once a run, first (front_identity), by its /health alone: only an answer of exactly the
front's own body, FRONT_HEALTH, counts. Unless 9100 answered so, nothing goes there with your key, and nothing that
could load a model: it gets nothing after its /health, and each line that would have sent it something fails, saying
so. Phase 1's llama-swap, on 9100 until the cutover and again after a rollback, answers its /health 200 and a request
without a key 401, as the front does, and obeys `GET /unload` with your key. Until 2026-10-15 those two answers were
all the front line asked before it sent `GET /unload` with your key, which there would have unloaded every model (found
that day, on the box, read-only, before anyone ran doctor against Phase 1's layout).

It never prints a key, a token, a topic or an address from the environment, which can carry a password: your key
goes only in a header, only to the front, once 9100 has answered as the front, and to llama-swap's port (which must
refuse it), through no proxy and never on to where a redirect points.

Each line reads `ok`, `FAIL` or `WARN`. A `WARN` (only ntfy's, while it isn't set up: Task 2, in Phase 5) passes, fails
nothing, and isn't counted in *N of M checks pass*; the summary says how many warned. One check per Phase 2a scenario
(S01, S02, S03, S05, S14, S17). `spark doctor --full` adds the two heavy ones: a 170 MB upload to speech-to-text
through the front, and the privacy canary.
The usage meter's three (Task 44g): `usage records` and `gate ledger` (each folder's owner, group, mode and files) and
`counting` (a fresh row, the ledger's file, the gate's health); the canary also looks in the meter's two folders. Each
prints a verdict only, never a file's content, a row or a key name.
"""

from __future__ import annotations

import argparse
import grp
import http.client
import json
import os
import pwd
import re
import secrets
import stat
import struct
import subprocess
import time
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Iterator
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml

from spark import gateproto, paths, render, schema
from spark.apply import read_quiet
from spark.brake import KEY_CHECK_FILE, KeyCheck, read_key_check
from spark.gateclient import _STEP as GATE_CLIENT_STEPS
from spark.gateclient import GateClient, GateError
from spark.gateproto import REQUEST_TIMEOUT_S
from spark.llamaswap import _KeyStaysHere, _url_ok, key_from_env
from spark.memory import read_meminfo
from spark.messages import _and
from spark.registry import Registry, load_registry, parse_keys
from spark.status import _when

# The stack's units that keep running (Task 33): the five services and the three sockets systemd holds for the front
# and the gate. The pull and the S05 drill's brake are oneshots that run only when asked, and the failure notifier runs
# only when OnFailure= starts it, so none of them is checked here.
SERVICES = ("local-ai-llama-swap.service", "local-ai-brake.service", "local-ai-compose.service",
            "local-ai-front.service", "local-ai-gate.service")
SOCKET_UNITS = ("local-ai-front.socket", "local-ai-gate-status.socket", "local-ai-gate-control.socket")
UNITS = (*SOCKET_UNITS, *SERVICES)
LLAMA_SWAP_UNIT, BRAKE_UNIT = "local-ai-llama-swap.service", "local-ai-brake.service"
# The units the failure notifier watches (S14), each with OnFailure=; the first three also never stop restarting
# (StartLimitIntervalSec=0), while llama-swap's keeps systemd's own start limit, with RestartSec=5 (Task 25's units).
FAILURE_UNITS = ("local-ai-front.service", "local-ai-gate.service", "local-ai-brake.service", LLAMA_SWAP_UNIT)
START_LIMIT_UNITS = FAILURE_UNITS[:3]
SECRETS = Path("/etc/local-ai/secrets")
# Root's own copies of the units and the Compose project: what root runs, installed by `make install-units` from what
# `spark apply` staged. All of render.UNITS, 2a's eleven.
ROOT_FOLDERS = (Path(render.COMPOSE_DIR), Path(render.COMPOSE_DIR, "searxng"))
ROOT_FILES = (*(Path(render.UNIT_DIR, unit) for unit in render.UNITS),
              Path(render.COMPOSE_DIR, "compose.yaml"), Path(render.COMPOSE_DIR, "searxng/settings.yml"))
UFW_CONF = Path("/etc/ufw/ufw.conf")
EARLYOOM_DEFAULT = Path("stack/host/earlyoom.default")  # no --prefer since 2a's bootstrap (Task 28)
# needrestart's override, which bootstrap installs from the repo's copy.
NEEDRESTART_CONF = Path("/etc/needrestart/conf.d/local-ai.conf")
NEEDRESTART_REPO = Path("stack/host/needrestart.conf")
# spark's home, which is root's (Phase 0's review), and the brake's folder, spark's and shared with spark-admin; from
# Phase 2a (Task 27), the gate's, launch's and whisper's folders, spark's, the Hugging Face cache, spark-pull's with
# spark's group setgid, and the pull's own cache: each with the owner, group and mode bootstrap gives it. Each is in
# /var/lib/local-ai, root's 755, so your account reads its owner without being in its group.
STATE_HOME = Path("/var/lib/local-ai")
PULL_CACHE = STATE_HOME / "pull-cache"  # the pull's XDG_CACHE_HOME (its unit)
SPARK_FOLDERS = ((STATE_HOME, ("root", "root", 0o755)), (paths.STATE, ("spark", "spark-admin", 0o2770)),
                 (paths.GATE_STATE, ("spark", "spark", 0o750)), (paths.LAUNCH, ("spark", "spark", 0o750)),
                 (paths.WHISPER_TMP, ("spark", "spark", 0o750)), (Path(render.HF_HOME), ("spark-pull", "spark", 0o2750)),
                 (PULL_CACHE, ("spark-pull", "spark-pull", 0o750)))
# The usage meter's two folders (Task 44g): the front's rows, `spark-front:spark-admin`, and the gate's ledger,
# `spark:spark-admin`, each 2750 with setgid so a file's group is spark-admin's, and each file 0640 (UMask=0027): only
# the front or the gate writes, and spark-admin, whose one member is Dan, reads. Bootstrap makes both.
USAGE_OWNER = ("spark-front", "spark-admin")
LEDGER_OWNER = ("spark", "spark-admin")
METER_MODE = 0o2750
USAGE_PREFIX, LEDGER_PREFIX = "usage", "ledger"  # a month's file is <prefix>-YYYY-MM.jsonl, the month in UTC
ROWS_TAIL_MAX = 8 << 20  # a row file is read from its last bytes only: the rows doctor looks for are the newest
COUNTING_TRIES = 4  # the front writes a request's row as it ends, from a thread: a moment, no more
COUNTING_STEP_S = 0.5
# Where llama-server looks for a config.ini, which it reads before its command line (llama.cpp b11146, as Task 10's
# pre-dispatch scan read it): /etc/llama.cpp/, then $XDG_CONFIG_HOME/llama.cpp/ or $HOME/.config/llama.cpp/. The
# engines run with HOME=/var/lib/local-ai, spark's, and no XDG_CONFIG_HOME. Options added there would come around
# the flags render refuses, so neither folder may exist.
ENGINE_CONFIG = (Path("/etc/llama.cpp"), STATE_HOME / ".config")
# Each should answer 200 on / (Probe.http follows a redirect for a request without a key). (Seen 2026-09-28, in Phase
# 1's Task 13: both answered 200.)
WEB = (("Open WebUI", "http://127.0.0.1:3000/"), ("SearXNG", "http://127.0.0.1:8888/"))
HOLD_DRY_RUN = ["bash", "stack/host/bootstrap.sh", "--hold-gpu", "--dry-run"]
HOLD_SUMMARY = re.compile(r"^==> GPU set: (\d+) packages, (\d+) already held$", re.MULTILINE)
HOLD_STOPS = re.compile(r"\(a real run stops here: (.+)\)$", re.MULTILINE)
KEY_ENV = "SPARK_API_KEY"  # where doctor reads your client key, unless --key-env names another variable
# agent's OOM score (Task 28), as bootstrap installs it: the hook's line before sshd's first session line, its script,
# the drop-in for agent's user manager, and cron closed to agent. An openssh-server upgrade can replace sshd's PAM
# file, a dpkg conffile, and the line goes silently (Task 28's concern C5).
PAM_SSHD = Path("/etc/pam.d/sshd")
AGENT_HOOK = Path("/usr/local/libexec/local-ai-agent-oom")
AGENT_HOOK_REPO = Path("stack/host/local-ai-agent-oom")
PAM_LINE = f"session optional pam_exec.so type=open_session {AGENT_HOOK}"
AGENT_DROP_IN = "local-ai-oom.conf"  # in /etc/systemd/system/user@<agent's uid>.service.d
AGENT_DROP_IN_REPO = Path("stack/host/agent-oom.conf")
CRON_DENY = Path("/etc/cron.deny")
CRON_ALLOW = Path("/etc/cron.allow")
# The failure notifier's script (Task 26), which bootstrap installs and local-ai-notify@.service runs.
NOTIFIER = Path("/usr/local/libexec/local-ai-notify")
NOTIFIER_REPO = Path("stack/host/local-ai-notify")
# Version drift: the repo's pins, the lock, and what is deployed.
VERSIONS = Path("stack/versions.yaml")
LOCK = Path("spark/uv.lock")
APP_PYTHON = Path(render.DEPLOY, "app/.venv/bin/python")
UVICORN_QUERY = "import importlib.metadata as m; print(m.version('uvicorn'))"
LLAMA_SWAP_SAYS = re.compile(r"^version: (\S+)", re.MULTILINE)  # `version: v257 (f00d375), built at …`
LLAMA_SERVER_SAYS = re.compile(r"\(build (\d+),")  # `version: 0.5.0-dev (build 11146, commit 7fe450e19)`
# llama-swap's deployed config, and the schema of the version render was written for, vendored beside the templates.
DEPLOYED_CONFIG = Path(render.DEPLOY, "etc/llama-swap.yaml")
SCHEMA = Path("stack/llama-swap", f"config-schema.{render.TESTED_AGAINST['llama-swap']}.json")
# The routes llama-swap serves and the front mustn't (plan.md: only the model API's), each with how it is asked.
NOT_SERVED = (("GET", "/unload"), ("POST", "/api/models/unload/x"), ("GET", "/logs"))
MADE_UP_MODEL = "doctor-made-up-model"  # S03: a name no registry has, which pi's retry list doesn't match
IDLE_MIN = 60  # S01: the plan's idle time for an on-demand model, in minutes
NO_VIEW = "the gate didn't answer (its own line says why)"
# What a line says to do when the gate gives no answer: the gate's own client ends its words with `make doctor`, which
# doctor is (gateclient._STEP), so doctor names the real steps instead.
GATE_STEP = ("`make logs s=gate` shows why, and the stack units and sockets lines say whether the gate's units and "
             "sockets are up")
# --full: the upload, a WAV of silence, 48 kHz stereo, about 15 minutes; within the front's 200 MiB for a body.
UPLOAD_BYTES = 170_000_000
UPLOAD_TIMEOUT_S = 900.0
CANARY_PLACES = {"refusals": "the gate's refusal history", "state": "the gate's state", "launch": "launch's records"}
JOURNAL_SETTLE_S = 2.0  # the front writes a request's journal line as the request ends: a moment for journald
JOURNAL_GROUPS = {"adm", "systemd-journal"}  # either reads the system journal
# --all: without it, journalctl shows a field holding a CR, an ESC or a byte that isn't UTF-8, the kind a line of
# streamed tokens holds, as `[N bytes blob data]`, and the canary in it would never be found (Task 33's review, I2).
JOURNAL_ARGS = ("journalctl", "--no-pager", "--quiet", "--all", "--output=verbose")
ANSWER_MAX = 4 << 20  # an answer's body is read up to this; doctor reads only its status and a few fields
# Whether 9100 is the front (front_identity): the front answers GET /health with this, 200, as JSON (front/app.py, its
# step 1), and doctor reads at most HEALTH_MAX bytes of the answer. Phase 1's llama-swap answers the text `OK`.
FRONT_HEALTH = {"status": "ok"}
HEALTH_MAX = 256
NOT_THE_FRONT = ("9100 answers as llama-swap does, not as the front: the cutover hasn't happened, or this is Phase 1's "
                 "layout after a rollback. Phase 2a's doctor runs after the cutover, and `make doctor` from a `main` "
                 "worktree checks Phase 1's layout")


@dataclass(frozen=True)
class Check:
    """One line of the report. `warn` marks a line that reads WARN: it passes (`ok`), fails nothing, and isn't counted
    in *N of M checks pass*."""

    name: str
    ok: bool
    detail: str
    warn: bool = False

    def __post_init__(self) -> None:
        if self.warn and not self.ok:
            raise ValueError("a warning passes: a check that fails is a FAIL")


def _who(st: os.stat_result) -> tuple[str, str, int]:
    """Who owns a file, by name, and its permission bits."""
    return pwd.getpwuid(st.st_uid).pw_name, grp.getgrgid(st.st_gid).gr_name, st.st_mode & 0o7777


def _sendable(key: str) -> bool:
    return key.isascii() and key.isprintable()


def _unusable(key: str | None, key_env: str) -> str | None:
    """Why the key read from `key_env` can't be sent, or None when it can. A key that a header can't carry unchanged
    is never sent, and never shown: the likely cause is a CRLF line in the file it was set from, and http.client's
    error would print the whole header."""
    if key is None:
        return (f"no key in this shell: {key_env} isn't set — set it as website/how-to/deploy.md's *Before the "
                "first deploy* says, then log in afresh")
    if not _sendable(key):
        return (f"the key in {key_env} isn't printable ASCII (a stray CR or LF?), so it wasn't sent: set it again, "
                "as website/how-to/deploy.md's *Before the first deploy* does")
    return None


def _form_head(boundary: str, name: str, file_name: str | None = None) -> bytes:
    """A part's head in a multipart form. Every name doctor sends is its own: letters, digits, '-', '_' and '.'."""
    disposition = f'Content-Disposition: form-data; name="{name}"'
    if file_name is None:
        return f"--{boundary}\r\n{disposition}\r\n\r\n".encode()
    return f'--{boundary}\r\n{disposition}; filename="{file_name}"\r\nContent-Type: audio/wav\r\n\r\n'.encode()


def wav(size: int, *, rate: int = 48000, channels: int = 2) -> tuple[int, Iterator[bytes]]:
    """A WAV file of silence, `size` bytes in all, 16-bit PCM: its size, and its bytes a MiB at a time, so a 170 MB
    upload is never held whole in memory."""
    data = size - 44
    frame = channels * 2
    if data < 0 or data % frame:
        raise ValueError(f"a WAV of {size} bytes doesn't hold whole frames")
    header = (b"RIFF" + struct.pack("<I", size - 8) + b"WAVE" + b"fmt "
              + struct.pack("<IHHIIHH", 16, 1, channels, rate, rate * frame, frame, 16)
              + b"data" + struct.pack("<I", data))

    def chunks() -> Iterator[bytes]:
        yield header
        zeros = bytes(1 << 20)
        left = data
        while left:
            n = min(left, len(zeros))
            yield zeros if n == len(zeros) else zeros[:n]
            left -= n

    return size, chunks()


class Probe:
    """What the checks read from the box. The tests hand in a fake instead."""

    def __init__(self, repo: Path):
        self.repo = Path(repo)
        # Every request goes through this opener. No proxy from the environment (http_proxy): the key would go to
        # it, even for 127.0.0.1, and a web check would ask the proxy, not the service.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _KeyStaysHere)

    def run(self, argv: list[str]) -> tuple[int, str, str]:
        """A command's status and output, a byte that isn't UTF-8 read as U+FFFD; 127 when it couldn't run."""
        try:
            done = subprocess.run(argv, cwd=self.repo, capture_output=True, text=True, errors="replace", timeout=120)
        except (OSError, subprocess.TimeoutExpired) as err:
            return 127, "", str(err)
        return done.returncode, done.stdout, done.stderr

    def read(self, path: Path) -> str | None:
        """A file's text, or None if it can't be read, or isn't UTF-8. A relative path is inside the repo."""
        try:
            return (self.repo / path).read_text()
        except (OSError, ValueError):  # ValueError: a byte that isn't UTF-8 (UnicodeDecodeError)
            return None

    def owner(self, path: Path) -> tuple[str, str, int] | None:
        try:
            return _who(Path(path).stat())
        except (OSError, KeyError):
            return None

    def listable(self, path: Path) -> bool:
        try:
            os.listdir(path)
        except OSError:
            return False
        return True

    def entry(self, path: Path) -> tuple[str, str, int, str] | None:
        """A path's owner, group, permission bits and kind ("file", "folder", "link", "socket" or "other"), read
        without following a link; None when it's missing or out of this account's reach."""
        try:
            st = os.lstat(path)
            owner = _who(st)
        except (OSError, KeyError):
            return None
        kinds = ((stat.S_ISLNK, "link"), (stat.S_ISDIR, "folder"), (stat.S_ISREG, "file"), (stat.S_ISSOCK, "socket"))
        return *owner, next((kind for test, kind in kinds if test(st.st_mode)), "other")

    def files(self, folder: Path) -> list[tuple[str, str, str, int]]:
        """Each entry of a folder as (name, owner, group, st_mode), read without following a link, so a link's mode
        says so (stat.S_ISLNK) and a plain file's permission bits are st_mode & 0o7777. Raises PermissionError when the
        listing is refused, as a login before spark-admin's does."""
        found = []
        for name in sorted(os.listdir(folder)):
            st = os.lstat(Path(folder) / name)
            try:
                owner, group, _ = _who(st)
            except KeyError:
                owner, group = str(st.st_uid), str(st.st_gid)
            found.append((name, owner, group, st.st_mode))
        return found

    def _tail(self, path: Path) -> bytes | None:
        """The last ROWS_TAIL_MAX bytes of a regular file, opened without following a link or waiting on a FIFO; None
        when it isn't there or isn't a regular file. A refusal raises PermissionError."""
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except PermissionError:
            raise
        except OSError:
            return None
        try:
            size = os.fstat(fd).st_size
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return None
            os.lseek(fd, max(0, size - ROWS_TAIL_MAX), os.SEEK_SET)
            return os.read(fd, ROWS_TAIL_MAX)
        finally:
            os.close(fd)

    def holds(self, path: Path, needle: str) -> bool:
        """Whether a row file's last bytes hold `needle`; never its content. False for a file that isn't there."""
        data = self._tail(path)
        return data is not None and needle.encode() in data

    def newest_t0(self, path: Path) -> float | None:
        """The latest `t0` of the rows in a row file's last bytes, or None for no file or no row with one: a moment, not
        a row's content."""
        newest = None
        for line in (self._tail(path) or b"").splitlines():
            try:
                t0 = json.loads(line).get("t0")
            except (ValueError, AttributeError, RecursionError):
                continue
            if isinstance(t0, (int, float)) and not isinstance(t0, bool) and (newest is None or t0 > newest):
                newest = float(t0)
        return newest

    def exists(self, path: Path) -> bool | None:
        """Whether a path exists, a link included, dangling or not; None when this account can't tell, as when a
        folder on the way to it is closed to it (entry says None for both)."""
        try:
            os.lstat(path)
        except FileNotFoundError:
            return False
        except OSError:
            return None
        return True

    def uid(self, name: str) -> int | None:
        try:
            return pwd.getpwnam(name).pw_uid
        except KeyError:
            return None

    def groups(self) -> set[str]:
        """The names of this process's groups, as the system journal's reader checks them."""
        names = set()
        for gid in {os.getegid(), *os.getgroups()}:
            try:
                names.add(grp.getgrgid(gid).gr_name)
            except KeyError:
                pass
        return names

    def now(self) -> float:
        return time.time()

    def mem_available_gib(self) -> float | None:
        """MemAvailable from /proc/meminfo, in GiB, which needs no gate; None when it can't be read."""
        try:
            return read_meminfo().available_gib
        except (OSError, ValueError, KeyError):
            return None

    def pause(self, seconds: float) -> None:
        time.sleep(seconds)

    def brake_key_check(self) -> tuple[KeyCheck | None, str | None]:
        """The brake's start check, read as the gate reads it (brake.read_key_check, Task 29's review): never following
        a link, never waiting on a FIFO, and only up to a cap, since spark writes its folder. None and None when the
        brake recorded none; None and why, for one that can't be read."""
        return read_key_check(paths.STATE)

    def gate_get(self, route: str) -> dict:
        """The gate's answer to GET `route` on its control socket, as the CLI asks it (gateclient): needs no key, and
        raises a GateError whose text says what state the socket is in."""
        return GateClient(paths.GATE_CONTROL_SOCKET, REQUEST_TIMEOUT_S).get(route)

    def gate_post(self, route: str, body: dict) -> dict:
        """The gate's answer to POST `route` with `body` on its control socket, as gate_get."""
        return GateClient(paths.GATE_CONTROL_SOCKET, REQUEST_TIMEOUT_S).post(route, body)

    def http(self, url: str, *, key: str | None = None, body: dict | None = None,
             timeout: float = 10.0, limit: int = ANSWER_MAX) -> tuple[int, str]:
        """The HTTP status and body, a refusal's body included; 0 when nothing answered, when no whole HTTP answer came
        back (a body longer than `limit` bytes among them), or when the request couldn't be made. A POST when `body` is
        given, sent as JSON. The key goes in a header, never a URL, and only to `url`: through no proxy, and never on
        to where a redirect points, a keyed request's redirect coming back as its status. A key that isn't printable
        ASCII isn't sent at all (the checks refuse it first, and say so)."""
        if key and not _sendable(key):
            return 0, ""
        try:
            request = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode())
        except ValueError:  # a URL urllib can't take, from a variable set to what isn't one
            return 0, ""
        if body is not None:
            request.add_header("Content-Type", "application/json")
        if key:
            request.add_header("Authorization", f"Bearer {key}")
        return self._send(request, timeout, limit)

    def upload(self, url: str, *, key: str | None, fields: dict[str, str], file_name: str, size: int,
               chunks: Iterable[bytes], timeout: float = UPLOAD_TIMEOUT_S) -> tuple[int, str]:
        """A multipart form POSTed to `url`, its file part `size` bytes from `chunks`, sent as they come, with its
        Content-Length, so the front reads it by its length and doctor never holds it whole; as http() for the rest."""
        if key and not _sendable(key):
            return 0, ""
        boundary = f"doctor-{secrets.token_hex(16)}"
        head = b"".join(_form_head(boundary, name) + value.encode() + b"\r\n" for name, value in fields.items())
        head += _form_head(boundary, "file", file_name)
        tail = f"\r\n--{boundary}--\r\n".encode()

        def body() -> Iterator[bytes]:
            yield head
            yield from chunks
            yield tail

        try:
            request = urllib.request.Request(url, data=body(), method="POST")
        except ValueError:
            return 0, ""
        request.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        request.add_header("Content-Length", str(len(head) + size + len(tail)))
        if key:
            request.add_header("Authorization", f"Bearer {key}")
        return self._send(request, timeout)

    def _send(self, request: urllib.request.Request, timeout: float, limit: int = ANSWER_MAX) -> tuple[int, str]:
        try:
            with self._opener.open(request, timeout=timeout) as response:
                data = response.read(limit + 1)
                # Bytes its Content-Length promised that never came, or a body past the cap, by its Content-Length or
                # as it came (chunked): no answer.
                if response.length or len(data) > limit:
                    return 0, ""
                return response.status, data.decode(errors="replace")
        except urllib.error.HTTPError as err:  # it answered with an error, or with a redirect a key didn't follow
            try:
                text = err.read(limit).decode(errors="replace")
            except (OSError, http.client.HTTPException, ValueError):
                text = ""
            finally:
                err.close()
            return err.code, text
        # Nothing answered, the answer broke off or wasn't HTTP, or the request couldn't be made: a ValueError's
        # text can name a header's value, the key's included, so none of it goes any further.
        except (OSError, http.client.HTTPException, ValueError):
            return 0, ""


def _printable(text: str) -> str:
    """`text` with every character a terminal would act on written out instead: it comes from a file, a command or a
    service doctor doesn't control."""
    return "".join(c if c.isprintable() else c.encode("unicode_escape").decode("ascii") for c in text)


def _gate_said(err: GateError) -> str:
    """A GateError's text for a doctor line: the gate's client ends most with `make doctor`, which is doctor itself
    (gateclient._STEP), so that step gives way to GATE_STEP, the real one."""
    said = str(err)
    stepped = any(step in said for step in GATE_CLIENT_STEPS.values())
    for step in GATE_CLIENT_STEPS.values():
        said = said.replace(step, "")
    said = _printable(" ".join(said.split()))
    return f"{said} {GATE_STEP}" if stepped else said


def _low_memory(probe: Probe, registry: Registry) -> str | None:
    """Why something that takes memory (llama-server's CUDA start, llama-swap's copy of a 170 MB upload) isn't run now,
    or None: MemAvailable, from /proc/meminfo so that it works with the gate down, under the registry's warn line, or
    not readable (Task 33's review, I1)."""
    available, warn = probe.mem_available_gib(), registry.brake.warn_gib
    if available is None:
        return "MemAvailable can't be read from /proc/meminfo"
    if available < warn:
        return f"{available:.1f} GiB available, under the {warn:g} GiB warn line"
    return None


def _error_code(body: str) -> tuple[str | None, str | None]:
    """The code and message of an answer in OpenAI's error shape, `{"error": {"message", "code"}}`, as the front
    refuses; None and None for any other body."""
    try:
        error = json.loads(body)["error"]
    except (ValueError, KeyError, TypeError):
        return None, None
    if not isinstance(error, dict):
        return None, None
    code, message = error.get("code"), error.get("message")
    return (code if isinstance(code, str) else None), (message if isinstance(message, str) else None)


def _answered(code: int, body: str = "") -> str:
    """An answer as a line names it: its status, and the front's code when it gave one."""
    if not code:
        return "nothing"
    error, _ = _error_code(body)
    return f"{code} {_printable(error)}" if error else str(code)


def _redirect(code: int) -> bool:
    return 300 <= code < 400


def _redirected(code: int, who: str, logs: str) -> str:
    """What a check says of a redirect that a request with the key got, which Probe.http never follows. It names no
    address: one from the environment can carry a password."""
    return (f"{code}, a redirect, which doctor never follows with your key: is it {who} that answers there? "
            f"`make logs s={logs}`")


def _unknown_key(who: str, key_env: str) -> str:
    return f"{who} doesn't know the key in {key_env} — see 'When something is wrong' in website/how-to/deploy.md"


def _bad_url(variable: str, url: str, whose: str) -> str | None:
    """Why doctor can't ask a service at all, or None: `variable` set to what isn't an http(s) URL, which would
    otherwise read as the service not answering. Neither the value nor an example address is shown: a URL can carry a
    password, and the default is the right one."""
    if _url_ok(url):
        return None
    return f"{variable} isn't an http(s) URL: set it to one, or unset it for {whose} own address"


def _front_url() -> str | None:
    return _bad_url("SPARK_FRONT_URL", paths.FRONT_URL, "the front's")


def _installed(probe: Probe, path: Path, repo: Path, mode: int) -> str | None:
    """Why `path`, which bootstrap installs from the repo's `repo`, isn't root's own regular file at `mode` with the
    repo's text; None when it is."""
    entry = probe.entry(path)
    if entry is None:
        return f"{path} is missing, or out of your account's reach"
    user, group, have, kind = entry
    if kind != "file":
        return f"{path} is a {kind}, not a file"
    if (user, group, have) != ("root", "root", mode):
        return f"{path} is {user}:{group} {have:o}, not root:root {mode:o}"
    want = probe.read(repo)
    if want is None:
        return f"can't read the repo's {repo}: run doctor from the clone's root"
    if probe.read(path) != want:
        return f"{path} differs from the repo's {repo}"
    return None


# Phase 0's guardrails


def hooks(probe: Probe) -> Check:
    _, out, _ = probe.run(["git", "config", "core.hooksPath"])
    if out.strip() == ".githooks":
        return Check("leak hooks", True, "on in this clone")
    return Check("leak hooks", False, "off in this clone: run `make hooks`")


def judge_gpu_set(code: int, out: str, err: str) -> Check:
    """Reads the hold's own dry run (`make hold-gpu-dry-run`), so doctor and the hold agree on what
    the GPU set is."""
    stops = HOLD_STOPS.search(out)
    summary = HOLD_SUMMARY.search(out)
    if code != 0 or stops or not summary:
        why = stops.group(1) if stops else " ".join(err.split()) or (
            "the hold's dry run found no GPU set: `make hold-gpu-dry-run` shows what it found")
        return Check("GPU set", False, why)
    total, held = int(summary.group(1)), int(summary.group(2))
    if held < total:
        return Check("GPU set", False, f"{total - held} of {total} packages aren't held: run `make hold-gpu`")
    return Check("GPU set", True, f"all {total} packages held")


def gpu_set(probe: Probe) -> Check:
    return judge_gpu_set(*probe.run(HOLD_DRY_RUN))


def running_modules(probe: Probe) -> Check:
    name = "running kernel's modules"
    _, kernel, _ = probe.run(["uname", "-r"])
    kernel = kernel.strip()
    _, out, _ = probe.run(["dpkg-query", "-W", "-f=${db:Status-Abbrev}\t${Package}\n",
                           f"linux-modules-nvidia-*-{kernel}"])
    rows = [line.split() for line in out.splitlines() if len(line.split()) == 2]
    held = [package for status, package in rows if status == "hi"]
    unheld = [package for status, package in rows if status == "ii"]
    if held:
        return Check(name, True, f"{held[0]} is held")
    if unheld:
        return Check(name, False, f"{unheld[0]} isn't held: run `make hold-gpu`")
    return Check(name, False, f"no NVIDIA modules package for the running kernel {kernel}: "
                              "see website/how-to/updates.md")


def driver(probe: Probe) -> Check:
    _, module, _ = probe.run(["modinfo", "-F", "version", "nvidia"])
    _, smi, _ = probe.run(["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"])
    module, smi = module.strip(), (smi.split() or [""])[0]
    if module and module == smi:
        return Check("GPU driver", True, f"{smi}: the kernel module and nvidia-smi agree")
    return Check("GPU driver", False, f"kernel module {module or 'missing'}, nvidia-smi {smi or 'failed'}: "
                                      "see website/how-to/updates.md")


def earlyoom_args(text: str) -> list[str]:
    """EARLYOOM_ARGS from stack/host/earlyoom.default, split as systemd splits it: on spaces."""
    for line in text.splitlines():
        if line.startswith("EARLYOOM_ARGS="):
            return line.removeprefix("EARLYOOM_ARGS=").strip('"').split()
    return []


def earlyoom(probe: Probe) -> Check:
    """earlyoom runs with the repo's arguments, which have no --prefer since 2a's bootstrap (Task 28): the engines' own
    OOM scores put them first."""
    _, state, _ = probe.run(["systemctl", "is-active", "earlyoom"])
    if state.strip() != "active":
        return Check("earlyoom", False, f"{state.strip() or 'not running'}: `make bootstrap` enables it")
    _, pid, _ = probe.run(["systemctl", "show", "--property=MainPID", "--value", "earlyoom"])
    cmdline = probe.read(Path(f"/proc/{pid.strip()}/cmdline")) or ""
    running = [arg for arg in cmdline.split("\0")[1:] if arg]
    if running != earlyoom_args(probe.read(EARLYOOM_DEFAULT) or ""):
        return Check("earlyoom", False, "running with other arguments than stack/host/earlyoom.default: "
                                        "`make bootstrap` puts the repo's back")
    return Check("earlyoom", True, "active, with the repo's arguments")


def _session_line(words: list[str]) -> bool:
    """Whether a PAM line's words, comments taken out, make a session line, as bootstrap's pam_plan reads one: its type,
    read without regard to case as libpam reads it, `session` or `-session`, or an include of common-session."""
    kind = words[0].lower()
    return kind in ("session", "-session") or (kind == "@include" and len(words) > 1
                                               and words[1].startswith("common-session"))


def _continues(raw: str) -> bool:
    """Whether libpam joins the next line onto this one, as bootstrap's `continues` reads it: it ends in a backslash,
    spaces after it allowed, and has no #, where libpam stops a line."""
    return "#" not in raw and raw.rstrip().endswith("\\")


def pam_hook_problem(text: str) -> str | None:
    """What keeps sshd's PAM file from running the hook as bootstrap put it, read as bootstrap's pam_plan reads it, or
    None: bootstrap's line, exactly, as the first session line, no line before it joined onto it with a backslash (a
    blank or comment line between them keeps the join), and no other line naming the hook. A line that names the hook
    anywhere else, or differently, bootstrap refuses to change: it is taken out by hand."""
    first = joined = None
    joins = None  # the number of the last line that ends in a backslash, until a line that doesn't
    hooks = []
    for n, raw in enumerate(text.splitlines(), 1):
        words = raw.split("#", 1)[0].split()
        if raw == PAM_LINE or str(AGENT_HOOK) in raw.split("#", 1)[0]:
            hooks.append(n)
        if not words:  # libpam skips a blank or comment line, and a backslash before it still joins across it
            continue
        if first is None and _session_line(words):
            first, joined = (n, raw), joins
        joins = n if _continues(raw) else None
    if first is None:
        return (f"{PAM_SSHD} has no session line, so there's no place for the hook's line before sshd's session modules, "
                "and bootstrap refuses it: website/how-to/bootstrap.md, Phase 2a's re-run")
    if not hooks:
        return (f"{PAM_SSHD} doesn't run the hook: its line is gone (an openssh-server upgrade can replace the file), "
                "and `make bootstrap` puts it back")
    by_hand = ("bootstrap refuses to change it, so take it out by hand, as website/how-to/bootstrap.md says (its "
               "Phase 2a re-run), then run `make bootstrap`")
    if hooks == [first[0]] and first[1] != PAM_LINE:
        return f"{PAM_SSHD}:{first[0]} names the hook, but isn't bootstrap's line, {PAM_LINE}: {by_hand}"
    if hooks != [first[0]]:
        at = _and([str(n) for n in hooks if n != first[0]])
        where = ("besides bootstrap's line before its first session line" if first[1] == PAM_LINE
                 else f"but not as bootstrap's line before its first session line, line {first[0]}")
        return f"{PAM_SSHD} names the hook at line {at}, {where}: {by_hand}"
    if joined is not None:
        return (f"line {joined} of {PAM_SSHD} ends in a backslash, so libpam reads the hook's line as part of it and "
                "never runs the hook: take the backslash out by hand, then run `make bootstrap`")
    return None


def agent_oom(probe: Probe) -> Check:
    """agent's processes get an OOM score root sets (Task 28): sshd's PAM file is root's own, and runs the hook's line,
    exactly bootstrap's, before its first session line, where nothing of agent's own environment has been read yet; the
    hook's script and the drop-in for agent's user manager are the repo's, root's own; and cron.deny names agent, while
    a cron.allow, which cron would read instead, doesn't. Each is read as bootstrap reads it (Task 33's review)."""
    name = "agent's OOM score"
    wrong, installs = [], []
    entry = probe.entry(PAM_SSHD)
    text = probe.read(PAM_SSHD)
    if entry is None or text is None:
        wrong.append(f"can't read {PAM_SSHD}: `ls -l {PAM_SSHD}` shows why")
    elif entry[3] != "file" or entry[0] != "root" or entry[2] & 0o022:
        wrong.append(f"{PAM_SSHD} is {entry[0]}:{entry[1]} {entry[2]:o}, a {entry[3]}, not root's own file that only root "
                     "writes, which bootstrap needs before it changes it: put it back to root:root 644 by hand, then "
                     "run `make bootstrap`")
    elif (problem := pam_hook_problem(text)) is not None:
        wrong.append(problem)
    targets = [(AGENT_HOOK, AGENT_HOOK_REPO, 0o755)]
    uid = probe.uid("agent")
    if uid is None:
        wrong.append("there's no account agent, whose user manager gets the drop-in: `make bootstrap` makes it")
    else:
        targets.append((Path(render.UNIT_DIR, f"user@{uid}.service.d", AGENT_DROP_IN), AGENT_DROP_IN_REPO, 0o644))
    for path, repo, mode in targets:
        if (problem := _installed(probe, path, repo, mode)) is not None:
            installs.append(problem)
    if installs:
        wrong.append("; ".join(installs) + f": `make bootstrap` installs {'them' if len(installs) > 1 else 'it'}")
    wrong += _cron_problems(probe)
    if wrong:
        return Check(name, False, "; ".join(wrong))
    return Check(name, True, "sshd's PAM stack runs the hook before its first session line; the hook's script and the "
                             "drop-in for agent's user manager are the repo's, root's own; and cron.deny names agent")


def _cron_problems(probe: Probe) -> list[str]:
    """cron closed to agent, as bootstrap leaves it: cron.deny names agent, and a cron.allow, which cron reads instead
    when it exists, doesn't. A file that is there but can't be read says so, never passes as absent."""
    wrong = []
    for path in (CRON_DENY, CRON_ALLOW):
        text = probe.read(path)
        there = probe.exists(path) if text is None else True
        if text is None and there is not False:
            wrong.append(f"{path} can't be read from your account, so whether it names agent is unknown: "
                         f"`ls -l {path}` shows why")
        elif path == CRON_DENY and (text is None or "agent" not in text.splitlines()):
            wrong.append(f"{CRON_DENY} doesn't name agent: `make bootstrap` adds it")
        elif path == CRON_ALLOW and text is not None and "agent" in text.splitlines():
            wrong.append(f"{CRON_ALLOW} names agent, and cron reads it instead of {CRON_DENY}: take agent out of it")
    return wrong


def firewall(probe: Probe) -> Check:
    _, state, _ = probe.run(["systemctl", "is-active", "ufw"])
    conf = probe.read(UFW_CONF)
    if conf is None:
        return Check("firewall", False, f"can't read {UFW_CONF}: check by hand with `sudo ufw status`")
    if state.strip() == "active" and any(line.strip() == "ENABLED=yes" for line in conf.splitlines()):
        return Check("firewall", True, "ufw is on")
    return Check("firewall", False, "ufw is off: `sudo ufw status`; bootstrap's firewall step turns it on")


def secrets_folder(probe: Probe) -> Check:
    owner = probe.owner(SECRETS)
    if owner is None:
        # /etc/local-ai is root:spark-admin 750: a session from before bootstrap, without spark-admin, can't see
        # into it, so the folder may well be there.
        return Check("secrets folder", False, f"{SECRETS} is missing, or out of your account's reach (log in "
                                              "again): `make bootstrap` creates it")
    # Root's alone from Phase 2a (Dan's decision, 2026-10-07; bootstrap since Task 27): PID 1 and root's Compose read
    # the secret files, and nothing reads them as spark. Phase 1's root:spark 750 fails here until 2a's bootstrap.
    user, group, mode = owner
    if (user, group, mode) != ("root", "root", 0o700):
        return Check("secrets folder", False, f"{SECRETS} is {user}:{group} {mode:o}, not root:root 700: "
                                              "`make bootstrap` sets it")
    if probe.listable(SECRETS):  # root:root 700, so this account is root
        return Check("secrets folder", False, f"your account can list {SECRETS}; it must not: run doctor as "
                                              "yourself, not with sudo")
    return Check("secrets folder", True, "root:root 700, and closed to you")


def spark_folders(probe: Probe) -> Check:
    """spark's home is root's, so spark can't swap a folder that root creates in it for a link, and the brake's
    folder is spark's, shared with spark-admin; the gate's, launch's and whisper's folders are spark's, the Hugging
    Face cache spark-pull's, read by spark through its setgid group, and the pull's own cache spark-pull's: each a
    folder, read without following a link, as bootstrap sets it (SPARK_FOLDERS). launch's own folders are inside one
    of spark's, out of your account's reach, so they aren't read."""
    wrong = []
    for path, (user, group, mode) in SPARK_FOLDERS:
        entry = probe.entry(path)
        if entry is None:
            wrong.append(f"{path} is missing, or out of your account's reach")
        elif entry[3] != "folder":
            wrong.append(f"{path} is a {entry[3]}, not a folder")
        elif entry[:3] != (user, group, mode):
            wrong.append(f"{path} is {entry[0]}:{entry[1]} {entry[2]:o}, not {user}:{group} {mode:o}")
    if wrong:
        them = "them" if len(wrong) > 1 else "it"
        return Check("spark's folders", False, "; ".join(wrong) + f": `make bootstrap` sets {them}")
    return Check("spark's folders", True, f"{STATE_HOME} is root's; the brake's folder spark's, shared with "
                                          "spark-admin; the gate's, launch's and whisper's spark's; the Hugging Face "
                                          "cache spark-pull's, read by spark through its group; the pull's own cache "
                                          "spark-pull's")


# The stack


def root_copies(probe: Probe) -> Check:
    """What root runs is root's own: each copy a folder or a regular file, never a link, owned by
    root and not writable by group or others."""
    wrong = []
    for path in (*ROOT_FOLDERS, *ROOT_FILES):
        want = "folder" if path in ROOT_FOLDERS else "file"
        entry = probe.entry(path)
        if entry is None:
            # The Compose project sits under /etc/local-ai, root:spark-admin 750: a session from before
            # bootstrap, without spark-admin, can't see into it, so the copy may well be there.
            wrong.append(f"{path} is missing, or out of your account's reach (log in again)")
            continue
        user, group, mode, kind = entry
        if kind != want:
            wrong.append(f"{path} is a {kind}, not a {want}")
        elif user != "root" or mode & 0o022:
            wrong.append(f"{path} is {user}:{group} {mode:o}")
    if wrong:
        return Check("root's copies", False, "; ".join(wrong) + ": run `make install-units`")
    return Check("root's copies", True, "the units and the Compose project that root runs are root's own files")


def needrestart(probe: Probe) -> Check:
    """needrestart restarts no local-ai-* unit after an apt run: the override bootstrap installs is the repo's."""
    want = probe.read(NEEDRESTART_REPO)
    if want is None:
        return Check("needrestart", False, f"can't read the repo's {NEEDRESTART_REPO}: run doctor from the clone's "
                                           "root")
    installed = probe.read(NEEDRESTART_CONF)
    if installed is None:
        return Check("needrestart", False, f"{NEEDRESTART_CONF} is missing, or can't be read: `make bootstrap` "
                                           "installs it")
    if installed != want:
        return Check("needrestart", False, f"{NEEDRESTART_CONF} differs from {NEEDRESTART_REPO}: `make bootstrap` "
                                           "installs the repo's")
    return Check("needrestart", True, "leaves the local-ai-* units alone: the repo's override is installed")


def engine_config(probe: Probe) -> Check:
    """llama-server finds no config.ini to read before its command line (ENGINE_CONFIG)."""
    wrong = []
    for path in ENGINE_CONFIG:
        there = probe.exists(path)
        if there is None:
            wrong.append(f"can't tell from your account whether {path} exists: `sudo ls -ld {path}` can")
        elif there:
            wrong.append(f"{path} exists, and llama-server reads its config.ini before its command line: look at "
                         "what it holds, then remove it")
    if wrong:
        return Check("engine config files", False, "; ".join(wrong))
    folders = " and ".join(str(path) for path in ENGINE_CONFIG)
    return Check("engine config files", True, f"llama-server finds no config.ini: {folders} don't exist")


def _logs_then_start(units: list[str]) -> str:
    """What to do about stack units that aren't active: a socket's status, then its start, which needs sudo (the polkit
    rule leaves the sockets out, render.POLKIT_UNITS); a service's logs, then its start, which the polkit rule lets
    spark-admin run without sudo."""
    steps = []
    sockets = [unit for unit in units if unit.endswith(".socket")]
    services = [unit for unit in units if not unit.endswith(".socket")]
    if sockets:
        steps.append(f"`systemctl status {' '.join(sockets)}`, then `sudo systemctl start {' '.join(sockets)}`")
    if services:
        logs = " and ".join(f"`make logs s={unit.removeprefix('local-ai-').removesuffix('.service')}`"
                            for unit in services)
        steps.append(f"{logs}, then `systemctl start {' '.join(services)}`")
    return "; ".join(steps)


def brake_start_check(probe: Probe) -> tuple[bool, str]:
    """Whether llama-swap took the brake's own key when the brake started, as the brake recorded it in its state folder
    (Phase 1's council, 2026-09-28), read as the gate reads it since Task 29: an active brake whose key llama-swap
    refuses could unload nothing."""
    path = paths.STATE / KEY_CHECK_FILE
    check, problem = probe.brake_key_check()
    if problem is not None:
        return False, (f"the brake runs, but {_printable(problem)} — `ls -l {paths.STATE}` shows what is there, and "
                       "`make logs s=brake` what the brake said")
    if check is None:
        return False, f"the brake runs, but {path}, its start check, is missing: `make logs s=brake`"
    at, detail = _printable(check.at), _printable(check.detail)
    if check.ok is None:
        return False, (f"the brake's start check has waited for llama-swap since {at}: run doctor again in 30 s, or "
                       "`make logs s=brake`")
    if not check.ok:
        # What's known is that the check failed, and why. A brake whose check failed because llama-swap answered late
        # can unload once it answers; one whose key llama-swap refuses can't (the re-review of Phase 1's council
        # fixes, N5).
        return False, (f"the brake's start check failed at {at}: {detail}, so nothing shows that it can unload a "
                       "model — `make logs s=brake`, and 'When something is wrong' in website/how-to/deploy.md")
    return True, f"llama-swap took the brake's key at {at}"


def units(probe: Probe) -> Check:
    """The stack's units are active, the three sockets with the five services, and the brake's start check passed:
    folded in here, so that doctor keeps one line per concern. The pull and the S05 drill's brake run only when asked,
    so they aren't checked."""
    _, out, _ = probe.run(["systemctl", "is-active", *UNITS])
    states = out.split()
    down = [(unit, state) for unit, state in zip(UNITS, states) if state != "active"]
    if down:
        named = ", ".join(f"{unit} ({state})" for unit, state in sorted(down, key=lambda each: each[0]))
        return Check("stack units", False, f"{named}: {_logs_then_start([unit for unit, _ in down])}")
    if len(states) != len(UNITS):
        return Check("stack units", False, f"systemctl didn't answer: {_logs_then_start(list(UNITS))}")
    passed, said = brake_start_check(probe)
    if not passed:
        return Check("stack units", False, said)
    return Check("stack units", True, "llama-swap, the brake, the web services, the front and the gate are active, "
                                      f"with the front's and the gate's sockets, and {said}")


def sockets(probe: Probe) -> Check:
    """The gate's two socket files, which systemd holds (the controller's ruling at Task 25's review): the status
    socket spark:spark-users, the control socket root:spark-admin, each 0660."""
    wrong = []
    for path, (user, group, mode) in ((paths.GATE_STATUS_SOCKET, ("spark", "spark-users", 0o660)),
                                      (paths.GATE_CONTROL_SOCKET, ("root", "spark-admin", 0o660))):
        entry = probe.entry(path)
        if entry is None:
            wrong.append(f"{path} is missing, or out of your account's reach")
        elif entry[3] != "socket":
            wrong.append(f"{path} is a {entry[3]}, not a socket")
        elif entry[:3] != (user, group, mode):
            wrong.append(f"{path} is {entry[0]}:{entry[1]} {entry[2]:o}, not {user}:{group} {mode:o}")
    if wrong:
        return Check("sockets", False, "; ".join(wrong) + ": root's socket units set them, and "
                                                          "`make install-units-dry-run` shows whether they differ "
                                                          "from the repo's")
    return Check("sockets", True, "the gate's status socket is spark:spark-users 660, and its control socket "
                                  "root:spark-admin 660")


def key_list(probe: Probe, registry: Registry | None, problem: str | None) -> Check:
    """The private key list (Task 4) loads, 0640 root:spark-admin, each key in one of the registry's key groups. It
    holds names, groups, labels and accounts, never a key or a digest, so a reason may quote them."""
    name, path = "key list", paths.KEYS
    step = "website/how-to/secret-files.md, step 13, installs it"
    entry = probe.entry(path)
    if entry is None:
        return Check(name, False, f"{path} is missing, or out of your account's reach (log in again): {step}")
    if entry[3] != "file":
        return Check(name, False, f"{path} is a {entry[3]}, not a file: {step}")
    if entry[:3] != ("root", "spark-admin", 0o640):
        return Check(name, False, f"{path} is {entry[0]}:{entry[1]} {entry[2]:o}, not root:spark-admin 640: {step}")
    if registry is None:
        return Check(name, False, f"can't load the deployed registry: {problem} — `make apply`")
    text = probe.read(path)
    if text is None:
        return Check(name, False, f"{path} can't be read from your account (log in again): {step}")
    try:
        keys = parse_keys(text, registry.key_groups)
    except Exception as err:  # noqa: BLE001 (malformed or invalid: a FAIL line says so, never a traceback)
        return Check(name, False, f"{path} doesn't load: {_printable(' '.join(str(err).split()))}: {step}")
    return Check(name, True, f"{path} loads: {len(keys)} keys, each in one of the registry's key groups "
                             f"({_and(list(registry.key_groups))})")


def _pins(probe: Probe) -> tuple[dict[str, str], str | None]:
    """Each component's version from the repo's stack/versions.yaml, and the lock's uvicorn; or why they can't be read."""
    text, lock = probe.read(VERSIONS), probe.read(LOCK)
    if text is None or lock is None:
        return {}, f"can't read the repo's {VERSIONS if text is None else LOCK}: run doctor from the clone's root"
    try:
        components = yaml.safe_load(text)["components"]
        pins = {name: str(c["version"]) for name, c in components.items()}
        pins["uvicorn"] = next(str(p["version"]) for p in tomllib.loads(lock)["package"] if p["name"] == "uvicorn")
    except (yaml.YAMLError, tomllib.TOMLDecodeError, KeyError, TypeError, AttributeError, StopIteration) as err:
        return {}, f"the repo's {VERSIONS} or {LOCK} can't be read: {type(err).__name__}"
    return pins, None


def llama_swap_binary(version: str) -> Path:
    """Where llama-swap's binary of `version` is, as its unit runs it."""
    return Path(render.DEPLOY, "bin/llama-swap", version, "llama-swap")


def version_drift(probe: Probe, registry: Registry | None, problem: str | None) -> Check:
    """The deployed components are the ones the repo pins: llama-swap's binary names its version, llama-server its
    build (b11146 reads as `build 11146`), whisper-server is the binary under its version's folder, as the registry
    names it, and the deployed app's uvicorn is the lock's. Each mismatch is named. `llama-server --version` starts
    CUDA (about 0.3 s and 330 MB, seen 2026-10-15), so with memory under the warn line, or unreadable, it isn't run, and
    the build is read from the registry's folder for it, as whisper-server's is, and the line says so (Task 33's
    review, I1)."""
    name = "version drift"
    if registry is None:
        return Check(name, False, f"can't load the deployed registry: {problem} — `make apply`")
    pins, why = _pins(probe)
    if why is not None:
        return Check(name, False, why)
    moves = "website/how-to/updates.md moves it"
    wrong = []
    swap = llama_swap_binary(pins["llama-swap"])
    code, out, err = probe.run([str(swap), "-version"])
    said = LLAMA_SWAP_SAYS.search(f"{out}\n{err}")
    if code != 0 or said is None:
        wrong.append(f"{swap} -version didn't run, or named no version: website/how-to/updates.md installs it")
    elif said.group(1) != pins["llama-swap"]:
        wrong.append(f"llama-swap -version names {_printable(said.group(1))}, not {pins['llama-swap']}: {moves}")
    server = registry.engines.get("llama.cpp")
    by_folder = ""
    if server is not None and (low := _low_memory(probe, registry)) is not None:
        by_folder = f" (by its folder: --version not run, {low})"
        if Path(server).parent.name != pins["llama.cpp"]:
            wrong.append(f"the registry's llama-server, {server}, isn't under {pins['llama.cpp']}: `make apply` deploys "
                         f"the repo's registry; --version not run, {low}")
    elif server is not None:
        code, out, err = probe.run([server, "--version"])
        build = LLAMA_SERVER_SAYS.search(f"{out}\n{err}")
        if code != 0 or build is None:
            wrong.append(f"{server} --version didn't run, or named no build: website/how-to/updates.md installs it")
        elif f"b{build.group(1)}" != pins["llama.cpp"]:
            wrong.append(f"llama-server --version names b{build.group(1)}, not {pins['llama.cpp']}: {moves}")
    whisper = registry.engines.get("whisper.cpp")
    if whisper is not None:
        if Path(whisper).parent.name != pins["whisper.cpp"]:
            wrong.append(f"the registry's whisper-server, {whisper}, isn't under {pins['whisper.cpp']}: `make apply` "
                         "deploys the repo's registry")
        elif (probe.entry(Path(whisper)) or ("", "", 0, ""))[3] != "file":
            wrong.append(f"{whisper} is missing, or out of your account's reach: website/how-to/updates.md installs it")
    code, out, _ = probe.run([str(APP_PYTHON), "-I", "-c", UVICORN_QUERY])
    deployed = out.strip()
    if code != 0 or not deployed:
        wrong.append(f"the deployed app's uvicorn can't be read with {APP_PYTHON}: `make apply` syncs the app")
    elif deployed != pins["uvicorn"]:
        wrong.append(f"the deployed app's uvicorn is {_printable(deployed)}, not the lock's {pins['uvicorn']}: "
                     "`make apply` syncs it")
    if wrong:
        return Check(name, False, "; ".join(wrong))
    return Check(name, True, f"llama-swap {pins['llama-swap']}, llama-server {pins['llama.cpp']}{by_folder}, whisper-server under "
                             f"{pins['whisper.cpp']} and uvicorn {pins['uvicorn']}, as stack/versions.yaml and the "
                             "lock pin them")


def llama_swap_config(probe: Probe) -> Check:
    """The deployed llama-swap config passes the schema of the version render was written for (render.TESTED_AGAINST),
    vendored in the repo, read as `spark render` reads it. The config holds its keys only as ${env.…} names."""
    name, version = "llama-swap config", render.TESTED_AGAINST["llama-swap"]
    text = probe.read(DEPLOYED_CONFIG)
    if text is None:
        return Check(name, False, f"{DEPLOYED_CONFIG} is missing, or can't be read: `make apply` deploys it")
    vendored = probe.read(SCHEMA)
    if vendored is None:
        return Check(name, False, f"can't read the repo's {SCHEMA}: run doctor from the clone's root")
    try:
        config = yaml.safe_load(text)
        problems = schema.check(config, schema.load(vendored))
    except (yaml.YAMLError, ValueError, RecursionError) as err:
        return Check(name, False, f"{DEPLOYED_CONFIG} doesn't load: {_printable(' '.join(str(err).split()))[:300]}: "
                                  "`make apply` deploys the repo's")
    if problems:
        shown = "; ".join(_printable(problem) for problem in problems[:5])
        more = f"; and {len(problems) - 5} more" if len(problems) > 5 else ""
        return Check(name, False, f"{DEPLOYED_CONFIG} doesn't pass llama-swap {version}'s schema: {shown}{more}: "
                                  "`make apply-dry-run` shows how it differs from the repo's")
    return Check(name, True, f"{DEPLOYED_CONFIG} passes llama-swap {version}'s schema")


def llama_swap(probe: Probe, key: str | None, key_env: str = KEY_ENV) -> Check:
    """llama-swap, behind the front from the cutover: its unit active, its /health answering, and /running refusing a
    call without a key and one with your client key, which only the front takes (plan.md: client keys reach only the
    front, as digests; llama-swap takes the internal keys)."""
    name = "llama-swap"
    if bad := _bad_url("SPARK_LLAMASWAP_URL", paths.LLAMASWAP_URL, "llama-swap's"):
        return Check(name, False, bad)
    _, state, _ = probe.run(["systemctl", "is-active", LLAMA_SWAP_UNIT])
    if state.strip() != "active":
        return Check(name, False, f"its unit is {state.strip() or 'not running'}: `make logs s=llama-swap`, then "
                                  f"`systemctl start {LLAMA_SWAP_UNIT}`")
    health, _ = probe.http(f"{paths.LLAMASWAP_URL}/health")
    if health != 200:
        return Check(name, False, f"/health answered {health or 'nothing'}: `make logs s=llama-swap`")
    anonymous, _ = probe.http(f"{paths.LLAMASWAP_URL}/running")
    if anonymous != 401:
        return Check(name, False, f"/running without a key answered {anonymous or 'nothing'}, "
                                  "not 401: its keys aren't enforced. `make apply-dry-run` shows "
                                  "whether the deployed config, which sets them, differs from the repo's; "
                                  "`make logs s=llama-swap` shows what llama-swap said")
    if unusable := _unusable(key, key_env):
        return Check(name, False, unusable)
    keyed, _ = probe.http(f"{paths.LLAMASWAP_URL}/running", key=key)
    if _redirect(keyed):
        return Check(name, False, f"/running with your client key answered "
                                  f"{_redirected(keyed, 'llama-swap', 'llama-swap')}")
    if keyed == 200:
        return Check(name, False, f"/running took your client key, from {key_env}: llama-swap must take only the "
                                  "internal keys, and clients go through the front. `make apply-dry-run` shows "
                                  "whether the deployed config, which sets them, differs from the repo's")
    if keyed != 401:
        return Check(name, False, f"/running with your client key answered {keyed or 'nothing'}, not 401: "
                                  "`make logs s=llama-swap`")
    return Check(name, True, "its unit is active, it answers on its own port, and it refuses a call without a key and "
                             "one with your client key, which only the front takes")


def front_identity(probe: Probe) -> str | None:
    """None when 9100 is the front: its /health, asked without a key, answers 200 with a JSON body that is exactly the
    front's own, FRONT_HEALTH, read up to HEALTH_MAX bytes. Otherwise why not, in the front line's words, NOT_THE_FRONT
    for any other 200: a body that isn't JSON, or isn't exactly that object. `checks` asks it once, first, and no check
    sends 9100 your key, or anything that could load a model, unless it was None (2026-10-15: until the cutover, and
    after a rollback, 9100 is Phase 1's llama-swap, which answers /health 200 `OK` and obeys `GET /unload` with your
    key). It tells the front from llama-swap; it is no defence against a process that takes 9100 to pass for the
    front, which the front's socket, held by PID 1, is for."""
    if bad := _front_url():
        return bad
    code, body = probe.http(f"{paths.FRONT_URL}/health", limit=HEALTH_MAX)
    if code != 200:
        return f"/health answered {code or 'nothing'}: `make logs s=front`"
    try:
        said = json.loads(body)
    except (ValueError, RecursionError):  # not JSON, or nested past what the parser follows
        return NOT_THE_FRONT
    return None if said == FRONT_HEALTH else NOT_THE_FRONT


def _not_sent(not_front: str) -> str:
    """The words of a line that would have sent your key, or asked a model, through 9100, which front_identity didn't
    find to be the front: it sent nothing."""
    if not_front == NOT_THE_FRONT:
        return f"nothing sent: {NOT_THE_FRONT}"
    return "nothing sent: 9100 didn't answer /health as the front does (the front line says why)"


def front(probe: Probe, key: str | None, key_env: str = KEY_ENV, *, not_front: str | None) -> Check:
    """The front, on 9100. `not_front` is front_identity's verdict, asked once a run: unless it is None, its words are
    the line, and nothing else is sent. Then a request without a key gets 401, and each of llama-swap's own routes
    NOT_SERVED gets 404 `route_not_served`, with your key, since the front checks the key before the route."""
    name = "front"
    if not_front is not None:
        return Check(name, False, not_front)
    anonymous, _ = probe.http(f"{paths.FRONT_URL}/v1/models")
    if anonymous != 401:
        return Check(name, False, f"GET /v1/models without a key answered {anonymous or 'nothing'}, not 401: the front "
                                  "isn't checking keys — `make logs s=front`")
    if unusable := _unusable(key, key_env):
        return Check(name, False, unusable)
    wrong = []
    for method, path in NOT_SERVED:
        code, body = probe.http(f"{paths.FRONT_URL}{path}", key=key, body={} if method == "POST" else None)
        if _redirect(code):
            return Check(name, False, f"{method} {path} with your key answered {_redirected(code, 'the front', 'front')}")
        if code == 401:
            return Check(name, False, f"{method} {path} with your key answered 401: "
                                      f"{_unknown_key('the front', key_env)}")
        if (code, _error_code(body)[0]) != (404, "route_not_served"):
            wrong.append((f"{method} {path}", _answered(code, body)))
    if wrong:
        said = ", and ".join(f"{route} {answer}" if n else f"{route} answered {answer}"
                             for n, (route, answer) in enumerate(wrong))
        return Check(name, False, f"{said}, not 404 route_not_served: the front must answer only the model API's "
                                  "routes — `make logs s=front`")
    routes = _and([f"{method} {path}" for method, path in NOT_SERVED])
    return Check(name, True, f"answers /health, refuses a request without a key, and answers 404 route_not_served for "
                             f"llama-swap's own routes ({routes})")


def gate_view(probe: Probe) -> tuple[dict | None, str]:
    """The gate's full StatusView, from GET /v1/status on its control socket, or None and why the gate line fails."""
    try:
        view = probe.gate_get("/v1/status")
    except GateError as err:
        return None, f"GET /v1/status on its control socket got no answer: {_gate_said(err)}"
    if view.get("schema") != gateproto.STATUS_SCHEMA:
        return None, (f"its answer to GET /v1/status has schema {_printable(str(view.get('schema')))}, and this doctor "
                      f"reads {gateproto.STATUS_SCHEMA}: `make apply` deploys the app this clone has")
    if not (isinstance(view.get("health"), dict) and isinstance(view.get("models"), list)
            and all(isinstance(m, dict) for m in view["models"]) and isinstance(view.get("at"), (int, float))):
        return None, "its answer to GET /v1/status isn't a status this doctor reads: `make logs s=gate`"
    return view, ""


def _status(value: Any) -> str:
    """A service's health as the gate words it: a string, or the brake's `status` within its record."""
    if isinstance(value, dict):
        value = value.get("status")
    return value if isinstance(value, str) else repr(value)


def gate(view: dict | None, why: str) -> Check:
    """The gate answers GET /v1/status on its control socket, and its health shows the four services, the front, the
    gate, the brake (its unit and its start check, Task 29) and llama-swap, each ok."""
    if view is None:
        return Check("gate", False, why)
    health = view["health"]
    wrong = []
    for service, field, logs in (("the front", "front", "front"), ("the gate", "gate", "gate"),
                                 ("the brake", "brake", "brake"), ("llama-swap", "llama_swap", "llama-swap")):
        said = _status(health.get(field))
        if said != "ok":
            wrong.append(f"{service} {_printable(said)} (`make logs s={logs}`)")
    if wrong:
        return Check("gate", False, f"it answers, but its health shows {_and(wrong)}")
    return Check("gate", True, "answers on its control socket, and its health shows the front, the gate, the brake and "
                               "llama-swap ok")


def ntfy(view: dict | None) -> Check:
    """The gate's notifications, by its health.ntfy: ok; failing, since when; or, until Task 2 sets ntfy up in Phase 5
    (Dan's decision, 2026-10-14), not set up, a WARN. A gate running without ntfy is flagged so (Task 19's note). It
    prints a verdict, never ntfy's address, a topic or a token, none of which the gate's view holds."""
    if view is None:
        return Check("ntfy", False, f"{NO_VIEW}, so whether its notifications go out is unknown")
    said = view["health"].get("ntfy")
    status = _status(said)
    if status == "not_set_up":
        return Check("ntfy", True, "not set up (Task 2, Phase 5)", warn=True)
    if status == "ok":
        return Check("ntfy", True, "the gate's notifications reach it")
    if status == "failing":
        since = said.get("failing_since") if isinstance(said, dict) else None
        try:  # the gate's own time, on the box's clock, as `spark status` shows it
            when = f" since {_when(since, view['at'], None)}" if isinstance(since, (int, float)) else ""
        except (OverflowError, OSError, ValueError):  # a time no clock reads
            when = ""
        return Check("ntfy", False, f"notifications failing{when}: `make logs s=gate`")
    return Check("ntfy", False, f"the gate says ntfy is {_printable(status)}: `make logs s=gate`")


def _labels(view: dict | None, registry: Registry | None) -> dict[str, str]:
    labels = {name: m.label for name, m in registry.models.items()} if registry is not None else {}
    if view is not None:
        labels |= {m["name"]: m["label"] for m in view["models"]
                   if isinstance(m.get("name"), str) and isinstance(m.get("label"), str)}
    return labels


def bypass(view: dict | None, registry: Registry | None) -> Check:
    """FAILs only on evidence of a real bypass (the plan's ruling N-4): an engine running that `spark launch` didn't
    start with a ticket from the gate, by the gate's bypass sweep (health.unticketed_engines), named by model, port and
    pid. The starts launch refused for want of a ticket since boot (health.no_ticket_refusals) are the backstop working:
    a note with their count, never a failure."""
    if view is None:
        return Check("bypass", False, f"{NO_VIEW}, so whether an engine runs without a ticket is unknown")
    health = view["health"]
    engines, refused = health.get("unticketed_engines"), health.get("no_ticket_refusals")
    if not (isinstance(engines, list) and isinstance(refused, int)
            and all(isinstance(e, dict) and isinstance(e.get("model"), str) and isinstance(e.get("port"), int)
                    and isinstance(e.get("pid"), int) for e in engines)):
        return Check("bypass", False, "the gate's health doesn't say which engines run without a ticket: "
                                      "`make logs s=gate`")
    if engines:
        labels = _labels(view, registry)
        named = "; ".join(f"{_printable(labels.get(e['model'], e['model']))} runs on port {e['port']} (pid {e['pid']}) "
                          f"without a ticket from the gate, a load around it: `ps -o user,args -p {e['pid']}` shows "
                          "who started it" for e in engines)
        return Check("bypass", False, named)
    note = (f"; {refused} start{'s' if refused != 1 else ''} refused for want of a ticket since boot: the backstop "
            "working") if refused else ""
    return Check("bypass", True, f"every engine that runs was started with a ticket from the gate{note}")


def web(probe: Probe) -> Check:
    down = []
    for name, url in WEB:
        code, _ = probe.http(url)
        if code != 200:
            down.append(f"{name} ({code or 'no answer'})")
    if down:
        return Check("web services", False, ", ".join(down) + ": `make logs s=open-webui` or `s=searxng`")
    return Check("web services", True, "Open WebUI and SearXNG answer")


def load_deployed_registry(path: Path) -> tuple[Registry | None, str | None]:
    """The deployed registry, or None and why it doesn't load."""
    try:
        return load_registry(path), None
    except FileNotFoundError:
        return None, f"{path} is missing"
    except Exception as err:  # noqa: BLE001 (unreadable, malformed or invalid: a FAIL line says so, never a traceback)
        return None, f"{path} doesn't load: " + " ".join(f"{type(err).__name__}: {err}".split())


def _not_ready(view: dict | None, model: Any) -> str | None:
    """Why `model` can't be asked without starting a load, or None when the gate's view shows it ready: doctor asks a
    model only then, so its request never loads one."""
    label = model.label
    if view is None:
        return f"{NO_VIEW}, so doctor can't see the state of {label}, and loads nothing"
    state = next((m.get("state") for m in view["models"] if m.get("name") == model.name), None)
    if state == "ready":
        return None
    command = model.roles[0] if model.roles else model.name
    if state == "not_loaded":
        return f"the gate shows {label} as not loaded, and doctor loads nothing: `spark load {command}`, then doctor again"
    if state == "starting":
        return f"the gate shows {label} as still loading: run doctor again in a minute"
    if state in ("draining", "stopping"):
        return f"the gate shows {label} as being unloaded, and doctor loads nothing: `make status` says why"
    return f"the gate doesn't list {label}: `make logs s=gate`"


def model(probe: Probe, key: str | None, registry: Registry | None, view: dict | None, problem: str | None = None,
          key_env: str = KEY_ENV, *, not_front: str | None) -> Check:
    """One request through the front, with your key, to the embeddings model, once 9100 has answered as the front
    (`not_front` None, front_identity) and the gate shows the model loaded."""
    name = "a model, end to end"
    if registry is None:
        return Check(name, False, f"can't load the deployed registry: {problem} — `make apply`")
    models = [m for m in registry.models.values() if m.capability == "embeddings"]
    if not models:
        return Check(name, False, "the registry has no embeddings model, which this check asks: give "
                                  "stack/models.yaml one, then `make apply`")
    embed = models[0]
    if bad := _front_url():
        return Check(name, False, bad)
    if not_front is not None:
        return Check(name, False, _not_sent(not_front))
    if unusable := _unusable(key, key_env):
        return Check(name, False, unusable)
    if waits := _not_ready(view, embed):
        return Check(name, False, waits)
    code, body = probe.http(f"{paths.FRONT_URL}/v1/embeddings", key=key,
                            body={"model": embed.name, "input": "doctor"}, timeout=300)
    try:
        vector = json.loads(body)["data"][0]["embedding"]
    except (ValueError, KeyError, IndexError, TypeError):
        vector = []
    if code == 200 and vector:
        return Check(name, True, f"{embed.label} answered through the front")
    if _redirect(code):
        return Check(name, False, f"{embed.label} answered {_redirected(code, 'the front', 'front')}")
    if code == 0:
        return Check(name, False, "the front didn't answer: `make logs s=front`")
    if code == 401:
        return Check(name, False, f"{embed.label} answered 401: {_unknown_key('the front', key_env)}")
    if code == 200:
        return Check(name, False, f"{embed.label} answered 200, but with no embedding: `make logs s=front`")
    return Check(name, False, f"{embed.label} answered {_answered(code, body)}: `make status` says why")


def _month_file(folder: Path, prefix: str, at: float) -> Path:
    return Path(folder) / f"{prefix}-{datetime.fromtimestamp(at, timezone.utc):%Y-%m}.jsonl"


def _meter_folder(probe: Probe, name: str, folder: Path, owner: tuple[str, str], writer: str) -> Check:
    """One of the meter's folders: a folder (not a link), `owner` and group, mode 2750 with setgid, and in it only
    regular files of the same owner and group that others can't read. A verdict only: no file's content, and no row."""
    user, group = owner
    entry = probe.entry(folder)
    if entry is None:
        return Check(name, False, f"{folder} is missing, or out of your account's reach: `make bootstrap` creates it")
    if entry[3] != "folder":
        return Check(name, False, f"{folder} is a {entry[3]}, not a folder: `make bootstrap` sets it")
    if entry[:3] != (user, group, METER_MODE):
        return Check(name, False, f"{folder} is {entry[0]}:{entry[1]} {entry[2]:o}, not {user}:{group} "
                                  f"{METER_MODE:o}: `make bootstrap` sets it")
    try:
        files = probe.files(folder)
    except PermissionError:
        return Check(name, False, f"your account can't list {folder}: log in again, so spark-admin's group applies")
    except OSError:
        return Check(name, False, f"{folder} can't be listed")
    wrong = []
    for file, who, whose, mode in files:
        shown = _printable(file)
        if stat.S_ISLNK(mode):
            wrong.append(f"{shown} is a link")
        elif stat.S_IFMT(mode) not in (0, stat.S_IFREG):
            wrong.append(f"{shown} isn't a regular file")
        elif (who, whose) != (user, group):
            wrong.append(f"{shown} is {_printable(who)}:{_printable(whose)}, not {user}:{group}")
        elif mode & 0o007:
            wrong.append(f"{shown} can be read by others")
    if wrong:
        more = f", and {len(wrong) - 3} more" if len(wrong) > 3 else ""
        return Check(name, False, f"in {folder}, {_and(wrong[:3])}{more}: only the {writer} writes there, mode 0640, and "
                                  "only spark-admin reads")
    return Check(name, True, f"{user}:{group} {METER_MODE:o}, setgid; no file in it is readable by others, and no "
                             f"link; only the {writer} writes, and only spark-admin reads")


def usage_records(probe: Probe) -> Check:
    return _meter_folder(probe, "usage records", paths.USAGE_DIR, USAGE_OWNER, "front")


def gate_ledger(probe: Probe) -> Check:
    return _meter_folder(probe, "gate ledger", paths.GATE_LEDGER_DIR, LEDGER_OWNER, "gate")


def counting(probe: Probe, view: dict | None, began: float) -> Check:
    """The meter counts (Task 44g): this month's usage file holds a row whose `t0` is after doctor began (the end-to-end
    check's own request, counted: nothing is printed of it), the gate's ledger has this month's file, and the gate's
    health shows neither the front's counting nor the ledger's off. A gate without `counting` in its health is older
    than the meter: unknown, which fails, since nothing says either is on."""
    name = "counting"
    now = probe.now()
    usage = _month_file(paths.USAGE_DIR, USAGE_PREFIX, now)
    ledger = _month_file(paths.GATE_LEDGER_DIR, LEDGER_PREFIX, now)
    again = "log in again, so spark-admin's group applies"
    try:
        newest = probe.newest_t0(usage)
        for _ in range(COUNTING_TRIES - 1):
            if newest is not None and newest > began:
                break
            probe.pause(COUNTING_STEP_S)
            newest = probe.newest_t0(usage)
    except PermissionError:
        return Check(name, False, f"your account can't read {usage}: {again}")
    if newest is None or newest <= began:
        return Check(name, False, f"{usage.name} holds no row from this run, though the check before this one asked "
                                  "the front for an embedding: counting is off, or that check failed. `make status` "
                                  "shows 'counting off since' and why; `make logs s=front`")
    present = probe.exists(ledger)
    if present is None:
        return Check(name, False, f"your account can't tell whether {ledger.name} is there: {again}")
    if not present:
        return Check(name, False, f"{ledger.name} isn't in {paths.GATE_LEDGER_DIR}: the gate's ledger isn't written, "
                                  "`make logs s=gate`")
    if view is None:
        return Check(name, False, "the gate's health isn't readable, so whether counting is on can't be told (the gate "
                                  "line says why)")
    said = view["health"].get("counting")
    if not (isinstance(said, dict) and all(k in said for k in ("front_off_since", "ledger_off_since"))):
        return Check(name, False, "the gate's health says nothing of counting: unknown, as an older gate's does. "
                                  "`make apply` deploys the one this clone has")
    off = []
    for who, key in (("the front's usage rows", "front_off_since"), ("the gate's ledger", "ledger_off_since")):
        since = said[key]
        if since is not None:
            when = _when(since, view["at"], None) if isinstance(since, (int, float)) else "an unreadable time"
            off.append(f"{who} since {when}")
    if off:
        return Check(name, False, f"the gate's health shows counting off for {_and(off)}: `make status`, and "
                                  "`make logs s=front` or `make logs s=gate` says why")
    return Check(name, True, "this run's request was counted in this month's usage file, the gate's ledger has this "
                             "month's file, and the gate's health shows neither off")


# One check per Phase 2a scenario.


def s01(registry: Registry | None, problem: str | None, view: dict | None) -> Check:
    """S01, the morning start: the deployed registry unloads an on-demand model after the plan's 60 minutes idle, and the
    gate counts the same models always loaded as the registry, which never unload for being idle."""
    if registry is None:
        return Check("S01", False, f"can't load the deployed registry: {problem} — `make apply`")
    idle = registry.gate.idle_unload_min
    if idle != IDLE_MIN:
        return Check("S01", False, f"the deployed registry unloads an on-demand model after {idle} min idle, not the "
                                   f"plan's {IDLE_MIN}: `make apply-dry-run` shows how it differs from the repo's")
    if view is None:
        return Check("S01", False, f"{NO_VIEW}, so which models it keeps always loaded is unknown")
    residents = [m.name for m in registry.models.values() if m.resident]
    counted = [m["name"] for m in view["models"] if m.get("resident") is True]
    labels = _labels(view, registry)

    def named(names: list[str]) -> str:  # labels come from the registry and the gate: shown escaped
        return _printable(_and([labels.get(n, n) for n in names])) or "none"

    if set(counted) != set(residents):
        return Check("S01", False, f"the gate counts as always loaded {named(counted)}, and the registry "
                                   f"{named(residents)}: `make logs s=gate`")
    # What it checks, not whether they are loaded now: the end-to-end check and `make status` show that (Task 33's
    # review).
    return Check("S01", True, f"an on-demand model unloads after {IDLE_MIN} min idle; the gate counts "
                              f"{named(residents)} as always loaded, as the registry does, and never unloads those for "
                              "being idle")


def s02(probe: Probe) -> Check:
    """S02, the big job: `spark make-room` asks the gate for a plan first. Doctor asks only for that, for 1 GiB, and
    never posts the plan's id to /v1/make-room, so nothing unloads."""
    try:
        answer = probe.gate_post("/v1/make-room/plan", {"size_gib": 1})
    except GateError as err:
        return Check("S02", False, f"the gate didn't answer POST /v1/make-room/plan, which spark make-room asks first: "
                                   f"{_gate_said(err)}")
    if not (isinstance(answer.get("plan_id"), str) and isinstance(answer.get("plan"), dict)):
        return Check("S02", False, "the gate's answer to POST /v1/make-room/plan isn't one spark make-room can read: "
                                   "`make logs s=gate`")
    return Check("S02", True, "the gate answers make-room's plan for 1 GiB; doctor asks only for the plan, so nothing "
                              "unloads")


def s03(probe: Probe, key: str | None, key_env: str = KEY_ENV, *, not_front: str | None) -> Check:
    """S03, a model that can't be had: a made-up model through the front gets 404 with model_not_found's words, and
    loads nothing, since the front refuses it before it asks the gate. Asked only once 9100 has answered as the front
    (`not_front` None, front_identity)."""
    if bad := _front_url():
        return Check("S03", False, bad)
    if not_front is not None:
        return Check("S03", False, _not_sent(not_front))
    if unusable := _unusable(key, key_env):
        return Check("S03", False, unusable)
    code, body = probe.http(f"{paths.FRONT_URL}/v1/chat/completions", key=key,
                            body={"model": MADE_UP_MODEL, "messages": [{"role": "user", "content": "doctor"}]})
    error, message = _error_code(body)
    if code == 404 and error == "model_not_found":
        if (message or "").startswith(f"There's no model called {MADE_UP_MODEL} here."):
            return Check("S03", True, "a made-up model through the front gets 404 model_not_found, in its words, and "
                                      "loads nothing")
        return Check("S03", False, "a made-up model through the front answered 404 model_not_found, but not in its "
                                   "words: `make logs s=front`")
    if _redirect(code):
        return Check("S03", False, f"a made-up model through the front answered "
                                   f"{_redirected(code, 'the front', 'front')}")
    if code == 401:
        return Check("S03", False, f"a made-up model through the front answered 401: "
                                   f"{_unknown_key('the front', key_env)}")
    return Check("S03", False, f"a made-up model through the front answered {_answered(code, body)}, not 404 "
                               "model_not_found: `make logs s=front`")


def s05(probe: Probe, view: dict | None) -> Check:
    """S05, memory critically low: the brake active, its start check passed, and the gate's activity record, whose
    in-flight order the brake reads, fresh: health.activity_age_s under ACTIVITY_STALE_S. Doctor, as you, can't read the
    gate's folder, so it takes the age from the gate."""
    _, state, _ = probe.run(["systemctl", "is-active", BRAKE_UNIT])
    if state.strip() != "active":
        return Check("S05", False, f"the brake is {state.strip() or 'not running'}: `make logs s=brake`, then "
                                   f"`systemctl start {BRAKE_UNIT}`")
    passed, said = brake_start_check(probe)
    if not passed:
        return Check("S05", False, said)
    if view is None:
        return Check("S05", False, f"{NO_VIEW}, so the age of its activity record, which the brake's order reads, is "
                                   "unknown")
    age = view["health"].get("activity_age_s")
    if age is None:
        return Check("S05", False, "the gate hasn't written its activity record, so the brake would go by Phase 1's "
                                   "order: `make logs s=gate`")
    if not isinstance(age, (int, float)) or isinstance(age, bool) or age >= gateproto.ACTIVITY_STALE_S:
        shown = f"{int(age)} s" if isinstance(age, (int, float)) and not isinstance(age, bool) else repr(age)
        return Check("S05", False, f"the gate's activity record is {shown} old, so the brake would go by Phase 1's "
                                   "order: `make logs s=gate`")
    return Check("S05", True, f"the brake is active and {said}, and the gate's activity record is {age:.1f} s old, so "
                              "the brake goes by the gate's order")


def s14(probe: Probe) -> Check:
    """S14, the gate down: the front, the gate, the brake and llama-swap each start the failure notifier when they fail
    (OnFailure=), by `systemctl show`; the first three never stop restarting (StartLimitIntervalSec=0; systemctl shows
    it as StartLimitIntervalUSec), as their units set it, while llama-swap's keeps systemd's own limit; and the
    notifier's script is the repo's, root's own."""
    wrong = []
    for unit in FAILURE_UNITS:
        code, out, _ = probe.run(["systemctl", "show", "--property=OnFailure", "--property=StartLimitIntervalUSec",
                                  unit])
        if code != 0:
            wrong.append(f"`systemctl show {unit}` failed: `make install-units`")
            continue
        props = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
        if unit in START_LIMIT_UNITS and props.get("StartLimitIntervalUSec") != "0":
            wrong.append(f"{unit}'s StartLimitIntervalSec= is "
                         f"{_printable(props.get('StartLimitIntervalUSec') or 'not shown')}, not 0: `make install-units`")
        notifier = f"local-ai-notify@{unit}.service"
        if notifier not in props.get("OnFailure", "").split():
            wrong.append(f"{unit} starts no {notifier} when it fails: `make install-units`")
    if (problem := _installed(probe, NOTIFIER, NOTIFIER_REPO, 0o755)) is not None:
        wrong.append(f"{problem}: `make bootstrap` installs it")
    if wrong:
        return Check("S14", False, "; ".join(wrong))
    return Check("S14", True, "the front, the gate, the brake and llama-swap start the failure notifier when they fail, "
                              "the first three never stop restarting, and the notifier's script is installed")


def s17(probe: Probe) -> Check:
    """S17, changing models mid-task (Task 32): `make apply`'s wait for a quiet moment reads the gate's GET /v1/quiet,
    on its control socket; without it, apply can't see what's in flight, and refuses a restart that would cut it off.
    The answer must be one apply's own read_quiet takes (Task 32's re-review, N7): the front's hold, its call to the
    gate and its snapshot's time with the counts."""
    try:
        answer = probe.gate_get("/v1/quiet")
    except GateError as err:
        return Check("S17", False, f"the gate didn't answer GET /v1/quiet, so make apply can't see what's in flight: "
                                   f"{_gate_said(err)}")
    try:
        read_quiet(SimpleNamespace(get=lambda route: answer))
    except GateError:
        return Check("S17", False, "the gate's answer to GET /v1/quiet isn't one make apply can read: `make logs s=gate`")
    return Check("S17", True, "the gate answers GET /v1/quiet, which make apply's wait for a quiet moment reads")


# spark doctor --full


def _ready_one(view: dict | None, models: list) -> tuple[Any, str | None]:
    """The first of `models` the gate shows loaded, the always-loaded first; or the first and why it can't be asked."""
    ordered = sorted(models, key=lambda m: not m.resident)
    for each in ordered:
        if _not_ready(view, each) is None:
            return each, None
    return ordered[0], _not_ready(view, ordered[0])


def upload(probe: Probe, key: str | None, registry: Registry | None, view: dict | None, problem: str | None = None,
           key_env: str = KEY_ENV, *, not_front: str | None) -> Check:
    """A 170 MB upload to speech-to-text through the front, once 9100 has answered as the front (`not_front` None,
    front_identity) and the gate shows the model loaded. The front spools it and sends it on as a chunked form, which
    v257 passes on still chunked (Task 21): this is the check that whisper-server takes a chunked body (Task 21's
    note). llama-swap reads a POST body whole into memory, so with memory under the warn line, or unreadable, it isn't
    sent (Task 33's review, I1)."""
    name = "a 170 MB upload"
    if registry is None:
        return Check(name, False, f"can't load the deployed registry: {problem} — `make apply`")
    models = [m for m in registry.models.values() if m.capability == "transcription"]
    if not models:
        return Check(name, False, "the registry has no speech-to-text model: give stack/models.yaml one, then "
                                  "`make apply`")
    if bad := _front_url():
        return Check(name, False, bad)
    if not_front is not None:
        return Check(name, False, _not_sent(not_front))
    if unusable := _unusable(key, key_env):
        return Check(name, False, unusable)
    stt, waits = _ready_one(view, models)
    if waits:
        return Check(name, False, waits)
    if (low := _low_memory(probe, registry)) is not None:
        return Check(name, False, f"not sent: {low}, and llama-swap holds a whole upload in memory — run `spark doctor "
                                  "--full` again once `make status` shows memory back above the warn line")
    size, chunks = wav(UPLOAD_BYTES)
    code, body = probe.upload(f"{paths.FRONT_URL}/v1/audio/transcriptions", key=key,
                              fields={"model": stt.name, "response_format": "json"}, file_name="doctor.wav",
                              size=size, chunks=chunks, timeout=UPLOAD_TIMEOUT_S)
    try:
        text = json.loads(body).get("text")
    except (ValueError, AttributeError):
        text = None
    megabytes = UPLOAD_BYTES // 1_000_000
    if code == 200 and isinstance(text, str):
        return Check(name, True, f"{stt.label} took a {megabytes} MB upload through the front, which sends a form on "
                                 "chunked, and answered")
    if _redirect(code):
        return Check(name, False, f"{stt.label} answered {_redirected(code, 'the front', 'front')}")
    if code == 401:
        return Check(name, False, f"{stt.label} answered 401: {_unknown_key('the front', key_env)}")
    if code == 0:
        # The front refuses some uploads before it reads them (a key it doesn't know, too many at once, a body past its
        # cap) and closes the connection, which reaches doctor, still sending, as a reset: no answer it can read.
        return Check(name, False, f"no answer came back for the {megabytes} MB upload: the front may have refused it "
                                  "before reading it, which closes the connection mid-send, or nothing answered — "
                                  "`make logs s=front` shows the front's line for it, if it had one")
    return Check(name, False, f"{stt.label} answered a {megabytes} MB upload through the front with "
                              f"{_answered(code, body)}: `make logs s=front`")


def canary(probe: Probe, key: str | None, registry: Registry | None, view: dict | None, problem: str | None = None,
           key_env: str = KEY_ENV, *, not_front: str | None) -> Check:
    """The privacy canary: a unique string, sent through chat and speech-to-text with your key, each to a model the
    gate shows loaded (as a message, a model name the front must refuse with 404, and a reasoning effort), then looked
    for in the journal, which your account reads in group adm, through the gate's POST /v1/canary, in its state, its
    refusal history and launch's records, which only spark reads, and in this month's usage and ledger files, which
    spark-admin reads. Found in none, it passes. The front's spool isn't searched: no account but the front sees its private /tmp, and Task 20's tests pin
    that each upload's file goes when its request ends. Sent only once 9100 has answered as the front (`not_front`
    None, front_identity)."""
    name = "privacy canary"
    if registry is None:
        return Check(name, False, f"can't load the deployed registry: {problem} — `make apply`")
    if bad := _front_url():
        return Check(name, False, bad)
    if not_front is not None:
        return Check(name, False, _not_sent(not_front))
    if unusable := _unusable(key, key_env):
        return Check(name, False, unusable)
    chats = [m for m in registry.models.values() if m.capability == "chat"]
    stts = [m for m in registry.models.values() if m.capability == "transcription"]
    if not chats or not stts:
        return Check(name, False, "the registry needs a chat model and a speech-to-text model to send it through: "
                                  "give stack/models.yaml each, then `make apply`")
    chat, waits = _ready_one(view, chats)
    if waits:
        command = chat.roles[0] if chat.roles else chat.name
        return Check(name, False, f"no chat model is loaded to send it through, and doctor loads nothing: "
                                  f"`spark load {command}` loads {chat.label}")
    stt, waits = _ready_one(view, stts)
    if waits:
        return Check(name, False, waits)
    if not probe.groups() & JOURNAL_GROUPS:
        return Check(name, False, "your account isn't in group adm, so it can't read the system journal: log in again")
    needle = f"doctor-canary-{secrets.token_hex(8)}"
    since = int(probe.now()) - 1
    code, body = probe.http(f"{paths.FRONT_URL}/v1/chat/completions", key=key, timeout=120,
                            body={"model": chat.name, "max_tokens": 1,
                                  "messages": [{"role": "user", "content": f"Reply with one word. {needle}"}]})
    if code != 200:
        return Check(name, False, f"the canary couldn't be sent through chat: {chat.label} answered "
                                  f"{_answered(code, body)} — `make logs s=front`, and `make status` for the model")
    # The meter's rows hold numbers and the registry's own model name, so the needle goes where a client chooses a
    # string a row could keep: as the model, which the front refuses as unknown (404), and as a reasoning effort.
    code, body = probe.http(f"{paths.FRONT_URL}/v1/chat/completions", key=key, timeout=120,
                            body={"model": needle, "max_tokens": 1,
                                  "messages": [{"role": "user", "content": "Reply with one word."}]})
    if code != 404:
        return Check(name, False, f"the canary sent as a model name should be refused as unknown (404), and the front "
                                  f"answered {_answered(code, body)} — `make logs s=front`")
    code, body = probe.http(f"{paths.FRONT_URL}/v1/chat/completions", key=key, timeout=120,
                            body={"model": chat.name, "max_tokens": 1, "reasoning_effort": needle,
                                  "messages": [{"role": "user", "content": "Reply with one word."}]})
    if code == 0 or code == 401:
        return Check(name, False, f"the canary couldn't be sent as a reasoning effort: the front answered "
                                  f"{_answered(code, body)} — `make logs s=front`")
    size, chunks = wav(48044, rate=16000, channels=1)  # 1.5 s of silence
    code, body = probe.upload(f"{paths.FRONT_URL}/v1/audio/transcriptions", key=key,
                              fields={"model": stt.name, "prompt": needle, "response_format": "json"},
                              file_name=f"{needle}.wav", size=size, chunks=chunks, timeout=120)
    if code != 200:
        return Check(name, False, f"the canary couldn't be sent through speech-to-text: {stt.label} answered "
                                  f"{_answered(code, body)} — `make logs s=front`, and `make status` for the model")
    probe.pause(JOURNAL_SETTLE_S)
    found = []
    code, out, _ = probe.run([*JOURNAL_ARGS, f"--since=@{since}"])
    if code != 0:
        return Check(name, False, "journalctl couldn't read the system journal: `journalctl -n 1` shows why")
    if needle in out:
        found.append("the journal")
    try:
        answer = probe.gate_post("/v1/canary", {"needle": needle})
    except GateError as err:
        return Check(name, False, f"the gate didn't answer POST /v1/canary: {_gate_said(err)}")
    places = answer.get("found")
    if not (isinstance(places, list) and all(isinstance(place, str) for place in places)):
        return Check(name, False, "the gate's answer to POST /v1/canary isn't one doctor can read: `make logs s=gate`")
    found += [CANARY_PLACES.get(place, _printable(place)) for place in places]
    meter = []
    try:
        for place, folder, prefix in (("the usage records", paths.USAGE_DIR, USAGE_PREFIX),
                                      ("the gate's ledger", paths.GATE_LEDGER_DIR, LEDGER_PREFIX)):
            if probe.holds(_month_file(folder, prefix, probe.now()), needle):
                meter.append(place)
    except PermissionError:
        return Check(name, False, "your account can't read the usage records or the gate's ledger, so it can't show the "
                                  "string isn't in them: log in again, so spark-admin's group applies")
    found += meter
    if found:
        steps = [f"`{' '.join(JOURNAL_ARGS)} --since=@{since} | grep -n {needle}` shows where in the journal"
                 ] if "the journal" in found else []
        steps += [f"`sudo -u spark grep -rl {needle} {paths.GATE_STATE} {paths.LAUNCH}` shows which of the gate's and "
                  "launch's files"] if places else []
        steps += [f"`grep -l {needle} {paths.USAGE_DIR}/* {paths.GATE_LEDGER_DIR}/*` shows which month's file"
                  ] if meter else []
        return Check(name, False, f"the string sent through chat and speech-to-text turned up in {_and(found)}: find "
                                  f"what wrote it there before sending anything private — {'; '.join(steps)}")
    return Check(name, True, "a string sent through chat (as a message, a model name and a reasoning effort) and "
                             "speech-to-text is in none of the journal, the gate's state, its refusal history, "
                             "launch's records, the usage records or the gate's ledger")


def checks(probe: Probe, key: str | None, registry: Registry | None, problem: str | None = None,
           key_env: str = KEY_ENV, *, full: bool = False) -> list[Check]:
    """Every check, in order; with `full`, the upload and the canary last. `key` is what the shell's `key_env` holds,
    and the checks that need it name that variable when it's missing. The gate's view is read once, for every line
    that needs it; and whether 9100 is the front is decided once, first, before anything that carries your key or
    could load a model goes there (front_identity)."""
    began = probe.now()
    not_front = front_identity(probe)
    view, why = gate_view(probe)
    results = [
        hooks(probe), gpu_set(probe), running_modules(probe), driver(probe), earlyoom(probe), agent_oom(probe),
        firewall(probe), secrets_folder(probe), spark_folders(probe), usage_records(probe), gate_ledger(probe),
        root_copies(probe), needrestart(probe),
        engine_config(probe), units(probe), sockets(probe), key_list(probe, registry, problem),
        version_drift(probe, registry, problem), llama_swap_config(probe), llama_swap(probe, key, key_env),
        front(probe, key, key_env, not_front=not_front), gate(view, why), ntfy(view), bypass(view, registry),
        web(probe), model(probe, key, registry, view, problem, key_env, not_front=not_front),
        counting(probe, view, began),
        s01(registry, problem, view), s02(probe), s03(probe, key, key_env, not_front=not_front), s05(probe, view),
        s14(probe), s17(probe),
    ]
    if full:
        results += [upload(probe, key, registry, view, problem, key_env, not_front=not_front),
                    canary(probe, key, registry, view, problem, key_env, not_front=not_front)]
    return results


def report(results: list[Check]) -> str:
    """One line a check, `ok`, `FAIL` or `WARN`, then the count: a WARN passes and isn't counted in N of M."""
    lines = [f"{'WARN' if c.warn else 'ok  ' if c.ok else 'FAIL'}  {c.name}: {c.detail}" for c in results]
    counted = [c for c in results if not c.warn]
    warned = len(results) - len(counted)
    summary = f"doctor: {sum(c.ok for c in counted)} of {len(counted)} checks pass"
    if warned:
        summary += f", {warned} warning{'s' if warned != 1 else ''}"
    lines.append(summary)
    return "\n".join(lines)


def register(subparsers) -> None:
    p = subparsers.add_parser("doctor", help="Phase 0's guardrails and the stack, checked (on the Spark)")
    p.add_argument("--key-env", default=KEY_ENV, help="env var holding your client key")
    p.add_argument("--full", action="store_true",
                   help="also a 170 MB upload to speech-to-text, and the privacy canary, through the front")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    repo = Path.cwd()
    if not (repo / "stack/host/bootstrap.sh").exists():
        print("doctor: run it from the repo root (make doctor)")
        return 2
    registry, problem = load_deployed_registry(paths.REGISTRY)
    results = checks(Probe(repo), key_from_env(args.key_env), registry, problem, args.key_env, full=args.full)
    print(report(results))
    return 0 if all(c.ok for c in results) else 1

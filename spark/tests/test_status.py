"""`spark status` for Phase 2a (website/design/phase-2a.md, Task 29): the gate's StatusView, read on the control socket
by a spark-admin login and on the status socket by any other, in plan.md's layout and words (*What you see in Phase
2a*). The gate here is a stand-in, the standard library's (http.server's handler on socketserver's Unix-socket server),
on a short AF_UNIX path; nothing touches the box's own sockets, and every view is one the test builds, in the
StatusView's shape (gateproto)."""

import copy
import json
import os
import re
import shutil
import socket
import socketserver
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from types import SimpleNamespace

import pytest

from spark import cli, gateclient, gateproto, messages, paths, status
from spark.memory import MemInfo
from spark.messages import Holder, Moment
from spark.status import format_status

UTC = timezone.utc
FIXTURE = Path(__file__).parent / "fixtures" / "models.yaml"
PLAN = Path(__file__).resolve().parents[2] / "website" / "design" / "plan.md"
ADMIN_GID = 990  # spark-admin's, a stand-in
GEMMA, EMBED, WHISPER, CODER = "gemma-4-26b-a4b", "qwen3-embedding-0.6b", "whisper-large-v3-turbo", "qwen3.8-27b"
COMMANDS = {GEMMA: "small", EMBED: "embed", WHISPER: "stt", CODER: "coder"}  # each model's first role


def at(hour: int, minute: int, *, day: int = 14) -> float:
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC).timestamp()


MOMENT = at(9, 14)  # the examples' moment, past its 09:12 refusal


def no_fit(*, dans: bool) -> str:
    """The coder's no_fit at the examples' moment, in the asking key's group's words, as the gate keeps it: Dan's names
    his python job, agent's sees it only as a process of Dan's (plan.md's refusal table)."""
    return messages.refusal("no_fit", Moment(
        needed_gib=41, free_gib=18, available_gib=48, reserve_gib=24, owed_gib=6,
        holders=[Holder("python3 (chendaniely)", 32, True), Holder("Gemma", 27, False)], model_label="the coder",
        model_command="coder", key_label="pi on the Mac" if dans else "agent", words="dan" if dans else "agent",
        names_processes=dans, wait_s=30 if dans else 600)).message


def model(name: str, label: str, footprint: float, resident: bool, **more) -> dict:
    return {"name": name, "label": label, "resident": resident, "footprint_gib": footprint, "state": "ready",
            "inflight": 0, "oldest_request_s": None, "last_use": MOMENT, "pinned": False, "pinned_until": None,
            "sessions": [], "brake_mark": None} | more


def example_view() -> dict:
    """The StatusView of plan.md's moment, as the control socket gives it to Dan: 121.6 GiB in all, 117 at idle, 48
    available, the residents loaded with Gemma answering 1, the coder marked from 03:12 seen using 26, agent waiting 2
    min of 10 for Dan, the brake fired at 03:12 and released at 03:40, agent's pi session since 08:40, the brake's
    release and Dan's refusal in *recent*, its key checked at 09:00."""
    return {
        "schema": 1, "host": "brightroar", "at": MOMENT,
        "memory": {"total_gib": 121.6, "available_gib": 48.0, "brake_gib": 20, "warn_gib": 28, "above_brake_gib": 28.0,
                   "reserve_gib": 24, "owed_gib": 6.0, "held_gib": 0.0, "free_for_a_load_gib": 18.0,
                   "unaccounted_gib": 26.0},
        "models": [model(GEMMA, "Gemma", 32, True, inflight=1, oldest_request_s=20.0),
                   model(EMBED, "the embeddings", 8, True), model(WHISPER, "whisper", 3, True),
                   model(CODER, "the coder", 41, False, state="not_loaded", last_use=None,
                         brake_mark={"at": at(3, 12), "seen_gib": 26.0})],
        "waiting": [{"key_label": "agent", "model": CODER, "waited_s": 120.0, "wait_s": 600, "why": "dan"}],
        "paused": None, "held": None, "pins": [],
        "sessions": [{"id": "s-1", "model": CODER, "label": "agent's pi", "started_at": at(8, 40),
                      "expires_at": at(20, 40)}],
        "recent": [{"at": at(3, 40), "text": "brake released; reloading Gemma and the embeddings", "code": None,
                    "model": None, "key_label": None},
                   {"at": at(9, 12), "text": no_fit(dans=True), "code": "no_fit", "model": CODER,
                    "key_label": "pi on the Mac"}],
        "health": {"front": "ok", "gate": "ok", "brake": {"status": "ok", "key_checked_at": at(9, 0)},
                   "llama_swap": "ok", "ntfy": {"status": "ok", "failing_since": None, "dropped": 0},
                   "activity_age_s": 0.4, "unticketed_engines": [], "no_ticket_refusals": 0, "damaged_at": None,
                   "damaged_kept_as": None, "read_problems": []},
        "applying": None, "problems": []}


# plan.md's example, line for line, as corrected at Task 29 (2026-10-14, the controller's rulings): `paused` reads
# `no`, since the view carries no last-fired or released time when no hold stands, and a refusal in *recent* is its
# whole text, after whom it was for.
EXAMPLE = [
    "brightroar · 48 GiB available of 121 · 28 above the brake's 20 GiB line",
    "free for a load: 18 GiB (48 available, less the 24 GiB reserve and 6 GiB still owed)",
    "used by other processes: 26 GiB",
    "loaded      Gemma (gemma-4-26b-a4b)                up to 32 GiB  always loaded     answering 1",
    "            the embeddings (qwen3-embedding-0.6b)  up to 8 GiB   always loaded",
    "            whisper (whisper-large-v3-turbo)       up to 3 GiB   always loaded",
    "not loaded  the coder (qwen3.8-27b)                up to 41 GiB  loads when asked",
    "            marked: it was loading when the brake fired at 03:12 (seen using 26 GiB);",
    "            only you can load it again",
    "waiting     agent → the coder · 2 min of 10 · waiting for you: spark load coder",
    "paused      no",
    "held        nothing · pins none · sessions: agent's pi, since 08:40",
    "recent      09:12  for pi on the Mac: The coder didn't load: it needs 41 GiB, and 18 GiB is free for a load (48 "
    "GiB available, less the 24 GiB reserve and the 6 GiB the loaded models may still grow into). Using memory now: "
    "python3 (chendaniely) 32 GiB, Gemma 27 GiB. Free space with `spark make-room 41G` on the Spark, then try again.",
    "            03:40  brake released; reloading Gemma and the embeddings",
    "health      front ok · gate ok · brake ok (key checked 09:00) · ntfy ok",
]


def text(view: dict, **given) -> str:
    return format_status(view, tz=given.pop("tz", UTC), commands=given.pop("commands", COMMANDS), **given)


def row(view: dict, label: str, **given) -> list[str]:
    """The lines of the row `label` (`held`, `health` …): its own, and the indented ones under it."""
    lines = text(view, **given).splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(f"{label:<12}"))
    under = []
    for line in lines[start + 1:]:
        if not line.startswith(" " * 12):
            break
        under.append(line)
    return [lines[start], *under]


def standard(out: str):
    """JSON as a strict parser reads it: NaN and Infinity aren't JSON."""

    def refuse(constant):
        raise ValueError(f"{constant} isn't standard JSON")

    return json.loads(out, parse_constant=refuse)


# The gate's stand-in, on a short Unix socket.

class _Gate(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # a Unix client's address is '', which the default line can't print
        pass

    def do_GET(self):
        self.server.seen.append((self.command, self.path, {k.lower(): v for k, v in self.headers.items()}))
        data = json.dumps(self.server.view).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@contextmanager
def serving(path: Path, view: dict):
    server = socketserver.ThreadingUnixStreamServer(str(path), _Gate)
    server.daemon_threads = True
    server.view, server.seen = view, []
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        path.unlink(missing_ok=True)  # a Unix socket's file stays after its close


@pytest.fixture
def box(monkeypatch, capsys):
    """`spark status [flags]` on a stand-in box: the gate's two sockets in a short folder, a login in spark-admin or not
    (`admin`), in Dan's words or agent's (`words`), the fixture registry with the coder under the plan's name, `coder`
    its first role, 48 of 121.6 GiB available, the clock read in
    UTC, and every GateClient made kept (its socket and timeout). Gives (exit code, what it printed)."""
    folder = Path(tempfile.mkdtemp(prefix="st", dir="/tmp"))
    made = []

    class Client(gateclient.GateClient):
        def __init__(self, path, timeout_s):
            made.append((Path(path), timeout_s))
            super().__init__(path, timeout_s)

    monkeypatch.setattr(paths, "GATE_CONTROL_SOCKET", folder / "control.sock")
    monkeypatch.setattr(paths, "GATE_STATUS_SOCKET", folder / "status.sock")
    registry = folder / "models.yaml"  # the fixture registry, its coder under the plan's name for it
    registry.write_text(FIXTURE.read_text().replace("\n  coder:\n", f"\n  {CODER}:\n"))
    monkeypatch.setattr(paths, "REGISTRY", registry)
    monkeypatch.setattr(status, "GateClient", Client)
    monkeypatch.setattr(status, "read_meminfo", lambda: MemInfo(121.6, 48.0))
    monkeypatch.setattr(status, "local_zone", lambda: UTC)
    monkeypatch.setattr(socket, "gethostname", lambda: "brightroar.local")

    def getgrnam(name):
        if name == "spark-admin":
            return SimpleNamespace(gr_gid=ADMIN_GID, gr_mem=[])
        raise KeyError(name)

    monkeypatch.setattr(status.grp, "getgrnam", getgrnam)
    monkeypatch.setattr(status.os, "getegid", lambda: 1000)

    def run(*flags, admin=True, words=None):
        monkeypatch.setattr(status.os, "getgroups", lambda: [1000, ADMIN_GID] if admin else [1000, 991])
        monkeypatch.setattr(gateclient, "_words", lambda: words or ("dan" if admin else "agent"))
        code = cli.main(["status", *flags])
        return code, capsys.readouterr().out

    run.folder, run.made = folder, made
    yield run
    shutil.rmtree(folder, ignore_errors=True)


# The brief's tests.

def test_status_prints_the_plans_example_moment_line_for_line(box):
    assert text(example_view()) == "\n".join(EXAMPLE)
    # plan.md's example is these lines, so the two change together.
    block = PLAN.read_text().split("**on the Spark**, `spark status` shows:", 1)[1].split("```\n", 1)[1]
    assert block.split("\n```", 1)[0].splitlines() == EXAMPLE
    # And through the CLI, from the stand-in on the control socket, the command named by the registry's first role.
    view = example_view()
    with serving(box.folder / "control.sock", view):
        code, out = box()
    assert code == 0 and out == "\n".join(EXAMPLE) + "\n"


def test_agents_status_shows_only_its_refusals_and_no_dans_processes(box):
    # The status socket's view of the same moment (gate.app's status_for): agent's own refusal and the system's event,
    # Dan's refusal left out, and Dan's process unnamed in agent's words. Dan's steps are named as Dan's (Task 6).
    view = example_view()
    view["recent"] = [view["recent"][0], {"at": at(8, 50), "text": no_fit(dans=False), "code": "no_fit",
                                          "model": CODER, "key_label": "agent"}]
    with serving(box.folder / "status.sock", view) as gate:
        code, out = box(admin=False)
    assert code == 0 and gate.seen and not (box.folder / "control.sock").exists()
    lines = out.splitlines()
    assert lines[7:10] == ["            marked: it was loading when the brake fired at 03:12 (seen using 26 GiB);",
                           "            only Dan can load it again",
                           "waiting     agent → the coder · 2 min of 10 · waiting for Dan: spark load coder"]
    assert lines[12:14] == [
        "recent      08:50  for agent: The coder didn't load: it needs 41 GiB, and 18 GiB is free for a load (48 GiB "
        "available, less the 24 GiB reserve and the 6 GiB the loaded models may still grow into). Using memory now: a "
        "process of Dan's, 32 GiB, Gemma 27 GiB. Only Dan can free memory for it, on the Spark; try again after that.",
        "            03:40  brake released; reloading Gemma and the embeddings"]
    assert "chendaniely" not in out and "pi on the Mac" not in out and " you" not in out


def test_status_shows_paused_and_held_when_they_stand():
    view = example_view()
    view["paused"] = {"since": at(3, 12), "available_gib": 19.6, "releases_at": None, "waits_for_dan": False,
                      "last_fired": at(3, 12), "last_released": None}
    assert row(view, "paused") == [
        "paused      since 03:12 (19.6 GiB available) · resumes by itself after 5 min above 28 GiB available"]
    view["paused"]["waits_for_dan"] = True
    assert row(view, "paused") == ["paused      since 03:12 (19.6 GiB available) · waits for you: make brake-release"]
    assert row(view, "paused", words="agent") == [
        "paused      since 03:12 (19.6 GiB available) · waits for Dan: make brake-release"]
    # A hold that doesn't record when it fired or how low memory ran (Phase 1's, or one that can't be read).
    view["paused"] |= {"since": None, "available_gib": None, "last_fired": None}
    assert row(view, "paused") == [
        "paused      by a brake hold that doesn't record when it fired · waits for you: make brake-release"]

    view = example_view()
    view["sessions"] = []
    view["held"] = {"size_gib": 40.0, "until": at(18, 0)}
    view["pins"] = [{"model": CODER, "label": "the coder", "until": at(18, 0)}]
    assert row(view, "held") == [
        "held        40 GiB for you until 18:00 · spark make-room --done ends it · pins the coder until 18:00 · "
        "sessions none"]
    assert row(view, "held", words="agent") == [
        "held        40 GiB for Dan until 18:00 · Dan's spark make-room --done ends it · pins the coder until 18:00 · "
        "sessions none"]
    # No end: make-room's until a reboot, a pin with no end set (Task 10's note), as Task 7's `pinned` words it.
    view["held"]["until"] = None
    view["pins"] = [{"model": CODER, "label": "the coder", "until": None}]
    assert row(view, "held") == [
        "held        40 GiB for you until a reboot · spark make-room --done ends it · pins the coder with no end set · "
        "sessions none"]
    view["pins"].insert(0, {"model": GEMMA, "label": "Gemma", "until": at(18, 0)})
    assert row(view, "held")[0].endswith(" · pins Gemma until 18:00 and the coder with no end set · sessions none")
    # Apply's hold, alone, and first beside make-room's (the final check's f-5).
    view["held"], view["pins"] = None, []
    view["applying"] = {"since": at(14, 2), "restarting": ["llama-swap"]}
    assert row(view, "held") == [
        "held        new requests, for make apply since 14:02 · until the model service answers again · pins none · "
        "sessions none"]
    view["held"] = {"size_gib": 40.0, "until": at(18, 0)}
    assert row(view, "held") == [
        "held        new requests, for make apply since 14:02 · until the model service answers again · 40 GiB for you "
        "until 18:00 · spark make-room --done ends it · pins none · sessions none"]


WAITING_WORDS = {  # each reason in gateproto.WAITING_WHY, as Task 29's interface words it
    "memory": "waiting for memory: needs 41 GiB, 18 free for a load",
    "brake": "waiting for the brake's pause to end",
    "slot": "waiting its turn: Gemma is loading",
    "dan": "waiting for you: spark load coder",
    "restart": "waiting for the model service's restart",
    "llama_swap": "waiting for the model service to answer",
    "draining": "waiting while the coder finishes unloading",
    "swap_refused": "waiting to try again: llama-swap refused the load",
    "ticket": "waiting its turn: the gate is settling a load ticket it couldn't withdraw",
}


@pytest.mark.parametrize("why", gateproto.WAITING_WHY)
def test_each_waiting_reason_has_its_words(why):
    # The tuple is read, so a reason added later fails here until it has its own words.
    assert why in WAITING_WORDS, f"{why!r} has no words in spark status"
    view = example_view()
    view["waiting"][0]["why"] = why
    if why == "slot":
        view["models"][0]["state"] = "starting"
    if why == "draining":
        view["models"][3]["state"] = "draining"
    assert row(view, "waiting") == [f"waiting     agent → the coder · 2 min of 10 · {WAITING_WORDS[why]}"]


def test_every_reason_with_words_is_one_the_gate_gives():
    assert set(WAITING_WORDS) == set(gateproto.WAITING_WHY)


def test_unaccounted_memory_is_idle_less_available_less_footprints():
    view = example_view()
    view["memory"]["unaccounted_gib"] = 117 - 48 - 64.0  # the gate's figure: idle, less available and footprints
    assert text(view).splitlines()[2] == "used by other processes: 5 GiB"
    view["memory"]["unaccounted_gib"] = 0.3
    assert text(view).splitlines()[2] == "used by other processes: less than 1 GiB"
    view["memory"]["unaccounted_gib"] = 0.0
    assert text(view).splitlines()[2].startswith("loaded ") and "used by other" not in text(view)


def test_cold_residents_print_no_other_processes_line(box):
    view = example_view()
    view["memory"] |= {"available_gib": 83.5, "above_brake_gib": 63.5, "owed_gib": 9.5, "free_for_a_load_gib": 50.0,
                       "unaccounted_gib": 117 - 83.5 - 43}  # the residents' 43 GiB of footprints holding 33.5
    with serving(box.folder / "control.sock", view):
        code, out = box()
        assert code == 0 and "used by other processes" not in out
        assert out.splitlines()[:2] == [
            "brightroar · 83 GiB available of 121 · 63 above the brake's 20 GiB line",
            "free for a load: 50 GiB (83.5 available, less the 24 GiB reserve and 9.5 GiB still owed)"]
        code, out = box("--json")
    assert code == 0 and standard(out)["memory"]["unaccounted_gib"] == -9.5


def test_the_oldest_request_shows_from_a_minute():
    view = example_view()
    view["models"][0]["oldest_request_s"] = 180.0
    assert text(view).splitlines()[3].endswith("always loaded     answering 1 (oldest 3 min)")
    view["models"][0]["oldest_request_s"] = 40.0
    assert text(view).splitlines()[3].endswith("always loaded     answering 1")


def test_notifications_failing_since_shows():
    view = example_view()
    view["health"]["ntfy"] = {"status": "failing", "failing_since": at(8, 52), "dropped": 0}
    assert row(view, "health") == ["health      front ok · gate ok · brake ok (key checked 09:00) · ntfy failing since "
                                   "08:52"]
    # What the full queue dropped since the gate started, when there is any (Task 14's review).
    view["health"]["ntfy"]["dropped"] = 3
    assert row(view, "health")[0].endswith(" · ntfy failing since 08:52 · 3 notifications dropped")
    view["health"]["ntfy"] = {"status": "ok", "failing_since": None, "dropped": 1}
    assert row(view, "health")[0].endswith(" · ntfy ok · 1 notification dropped")


def test_ntfy_not_set_up_reads_as_such(box):
    view = example_view()
    view["health"]["ntfy"] = {"status": "not_set_up", "failing_since": at(8, 52), "dropped": 3}
    assert row(view, "health") == [
        "health      front ok · gate ok · brake ok (key checked 09:00) · ntfy not set up (Task 2, Phase 5)"]
    with serving(box.folder / "control.sock", view):
        code, out = box("--json")
    assert code == 0 and standard(out)["health"]["ntfy"]["status"] == "not_set_up"  # as it is, for 2b's menu bar


def test_with_the_gate_down_status_says_so_and_shows_memory(box, monkeypatch):
    code, out = box()  # no socket at either path
    assert code == 0 and out.splitlines() == [
        "brightroar · 48 GiB available of 121 · 28 above the brake's 20 GiB line",
        "gate        not answering: what's loaded, waiting, held or paused is unknown until it's back · make doctor"]
    code, out = box(admin=False)
    assert code == 0 and out.splitlines()[1] == ("gate        not answering: what's loaded, waiting, held or paused is "
                                                 "unknown until it's back · make doctor shows Dan what's wrong")
    code, out = box("--json")
    down = standard(out)
    assert code == 0 and isinstance(down.pop("at"), float)
    assert down == {"schema": 1, "host": "brightroar", "gate": "not answering",
                    "memory": {"total_gib": 121.6, "available_gib": 48.0, "brake_gib": 20}}
    # The brake's line from the deployed registry, the plan's when it won't load; memory that can't be read is said.
    registry = box.folder / "broken.yaml"
    registry.write_text("budget: {allocatable_gib: 102}\n")
    monkeypatch.setattr(paths, "REGISTRY", registry)

    def no_memory():
        raise OSError(2, "No such file or directory")

    monkeypatch.setattr(status, "read_meminfo", no_memory)
    code, out = box()
    lines = out.splitlines()
    assert code == 0 and lines[0] == "brightroar · memory can't be read"
    assert lines[1].startswith("gate        not answering: ")
    assert lines[2] == "problem     can't read memory: [Errno 2] No such file or directory"
    assert lines[3].startswith(f"problem     the registry {registry} won't load: ")
    assert lines[3].endswith("; the brake's line is the plan's, 20 GiB")
    code, out = box("--json")
    assert code == 0 and standard(out)["memory"] == {"total_gib": None, "available_gib": None, "brake_gib": 20}


def test_nothing_free_for_a_load_reads_none():
    # agent's view while make-room holds 70 GiB for Dan: 36 − 24 − 70 = −58, which is never shown.
    view = example_view()
    view["memory"] |= {"available_gib": 36.0, "above_brake_gib": 16.0, "owed_gib": 0.0, "held_gib": 70.0,
                       "free_for_a_load_gib": -58.0, "unaccounted_gib": -1.0}
    view["held"] = {"size_gib": 70.0, "until": None}
    shown = text(view, words="agent")
    assert shown.splitlines()[1] == ("free for a load: none (36 available, less the 24 GiB reserve and the 70 GiB "
                                     "make-room holds for Dan)")
    assert not re.search(r"(?<![\w.])-\d", shown) and "−" not in shown


def test_json_carries_the_same_for_the_menu_bar(box):
    view = example_view()
    with serving(box.folder / "control.sock", view):
        code, out = box("--json")
    assert code == 0 and standard(out) == view


def test_status_reads_the_socket_its_account_may(box):
    control, status_view = example_view(), example_view()
    control["host"], status_view["host"] = "from-control", "from-status"
    with serving(box.folder / "control.sock", control) as on_control, \
            serving(box.folder / "status.sock", status_view) as on_status:
        code, out = box()
        assert code == 0 and out.startswith("from-control · ") and len(on_control.seen) == 1 and not on_status.seen
        code, out = box(admin=False)
        assert code == 0 and out.startswith("from-status · ") and len(on_control.seen) == 1 and len(on_status.seen) == 1
    assert box.made == [(box.folder / "control.sock", 3), (box.folder / "status.sock", 3)]
    assert [seen[:2] for seen in on_control.seen + on_status.seen] == [("GET", "/v1/status")] * 2


def test_status_sends_no_key(box, monkeypatch):
    monkeypatch.setenv("SPARK_API_KEY", "KEY-MARKER")
    llama = socket.socket()
    llama.bind(("127.0.0.1", 0))
    llama.listen(8)
    llama.settimeout(0.2)
    monkeypatch.setattr(paths, "LLAMASWAP_URL", f"http://127.0.0.1:{llama.getsockname()[1]}")
    try:
        with serving(box.folder / "control.sock", example_view()) as gate:
            outs = [box()[1], box("--json")[1]]
        with pytest.raises(TimeoutError):
            llama.accept()  # nothing called llama-swap
    finally:
        llama.close()
    assert all("KEY-MARKER" not in out for out in outs)
    assert all(not {"authorization", "x-api-key"} & set(headers) for _, _, headers in gate.seen)


# The rulings' and the interface's other words.

def test_recent_shows_the_newest_ten_and_says_how_many_more_json_has(box):
    # The controller's ruling at Task 29: up to 100 rows of whole refusals is a wall, so the human view shows the newest
    # 10 and says how many older ones `--json` keeps.
    view = example_view()
    view["recent"] = [{"at": at(9, minute), "text": f"event {minute}", "code": None, "model": None, "key_label": None}
                      for minute in range(13)]
    assert row(view, "recent") == [
        "recent      09:12  event 12",
        *(f"            09:{minute:02d}  event {minute}" for minute in range(11, 2, -1)),
        "            … and 3 older: spark status --json shows them all"]
    view["recent"] = view["recent"][3:]  # exactly ten: no line
    assert len(row(view, "recent")) == 10 and "older" not in text(view)
    view["recent"] = view["recent"][:1] + view["recent"]  # eleven, one older
    assert row(view, "recent")[-1] == "            … and 1 older: spark status --json shows them all"
    view["recent"] = [{"at": at(9, minute), "text": f"event {minute}", "code": None, "model": None, "key_label": None}
                      for minute in range(13)]
    with serving(box.folder / "control.sock", view):
        code, out = box("--json")
    assert code == 0 and standard(out)["recent"] == view["recent"]  # every row


def test_a_brake_mark_with_no_reading_says_not_known():
    view = example_view()
    view["models"][3]["brake_mark"]["seen_gib"] = None
    assert text(view).splitlines()[7] == ("            marked: it was loading when the brake fired at 03:12 (seen "
                                          "using: not known);")


def test_the_brakes_health_says_whats_wrong_never_ok():
    # The gate reads the brake's start check (the controller's ruling at Task 29): only a passed one reads ok.
    view = example_view()
    for status_word, said in [
            ("down", "brake down (make logs s=brake)"),
            ("unknown", "brake unknown (make logs s=brake)"),
            ("start check failed: llama-swap GET /running: HTTP 401",
             "brake start check failed: llama-swap GET /running: HTTP 401 (make logs s=brake)"),
            ("start check waiting for llama-swap", "brake start check waiting for llama-swap (make logs s=brake)"),
            ("start check not recorded", "brake start check not recorded (make logs s=brake)"),
            ("start check unreadable", "brake start check unreadable (make logs s=brake)")]:
        view["health"]["brake"] = {"status": status_word, "key_checked_at": None}
        assert row(view, "health") == [f"health      front ok · gate ok · {said} · ntfy ok"]
        agents = said.replace("(make logs s=brake)", "(make logs s=brake shows Dan why)")  # the journal is Dan's
        assert row(view, "health", words="agent") == [f"health      front ok · gate ok · {agents} · ntfy ok"]


def test_whats_wrong_on_the_gates_side_shows_under_health():
    view = example_view()
    view["health"] |= {
        "front": "not connected", "llama_swap": "not answering", "activity_age_s": 12.3,
        "unticketed_engines": [{"model": CODER, "port": 803, "pid": 4242}], "no_ticket_refusals": 2,
        "damaged_at": at(6, 15), "damaged_kept_as": "/var/lib/local-ai/gate/state.json.damaged-20261014T061500Z",
        "read_problems": ["nvidia-smi couldn't be read: it didn't answer within 5 s"]}
    assert row(view, "health") == [
        "health      front not connected · gate ok · brake ok (key checked 09:00) · the model service not answering · "
        "ntfy ok",
        "            state started fresh at 06:15; the damaged file is kept as "
        "/var/lib/local-ai/gate/state.json.damaged-20261014T061500Z",
        "            nvidia-smi couldn't be read: it didn't answer within 5 s",
        "            the coder runs on port 803 (pid 4242) without a ticket from the gate: a load around the gate",
        "            spark launch refused 2 starts without a ticket from the gate this boot",
        "            the gate's activity record is 12 s old, so the brake goes by Phase 1's order"]
    view["health"]["damaged_kept_as"] = None
    assert row(view, "health")[1] == "            state started fresh at 06:15; the damaged file couldn't be kept"


def test_the_gates_problems_each_get_a_line():
    # A model llama-swap runs that the registry doesn't know is counted at the largest footprint (Task 13), and named.
    view = example_view()
    view["problems"] = ["llama-swap runs ghost, which the registry doesn't know; it is counted at 41 GiB, the largest "
                        "footprint the registry has."]
    assert text(view).splitlines()[-1] == ("problem     llama-swap runs ghost, which the registry doesn't know; it is "
                                           "counted at 41 GiB, the largest footprint the registry has.")


def test_free_for_a_loads_terms_add_up_as_shown():
    view = example_view()
    # Whole terms that don't add up to the figure shown read to a decimal (messages' rule, Task 6's fix round 3).
    view["memory"] |= {"available_gib": 48.9, "owed_gib": 6.6, "free_for_a_load_gib": 18.3}
    assert text(view).splitlines()[1] == ("free for a load: 18 GiB (48.9 available, less the 24 GiB reserve and 6.6 "
                                          "GiB still owed)")
    # agent's view counts Dan's hold, and says so.
    view["memory"] |= {"available_gib": 70.0, "owed_gib": 6.0, "held_gib": 30.0, "free_for_a_load_gib": 10.0}
    assert text(view, words="agent").splitlines()[1] == (
        "free for a load: 10 GiB (70 available, less the 24 GiB reserve, 6 GiB still owed and the 30 GiB make-room "
        "holds for Dan)")
    # Dan's view doesn't: his hold is his to load into (rule 4).
    view["memory"]["free_for_a_load_gib"] = 40.0
    assert text(view).splitlines()[1] == ("free for a load: 40 GiB (70 available, less the 24 GiB reserve and 6 GiB "
                                          "still owed)")
    # The GPU's ceiling or a load starting gave a figure the memory terms don't reach: never a sum that doesn't add up.
    view["memory"] |= {"held_gib": 0.0, "free_for_a_load_gib": 10.0}
    assert text(view).splitlines()[1] == (
        "free for a load: 10 GiB (the GPU's ceiling or a load still starting holds it below 70 available, less the 24 "
        "GiB reserve and 6 GiB still owed)")


@pytest.mark.parametrize("available, header", [
    (70.0, "brightroar · 70 GiB available of 121 · 50 above the brake's 20 GiB line"),
    (25.37, "brightroar · 25.3 GiB available of 121 · 5.3 above the brake's 20 GiB line"),
    (20.04, "brightroar · 20.0 GiB available of 121 · 0.0 above the brake's 20 GiB line"),
    (19.96, "brightroar · 19.9 GiB available of 121 · 0.1 below the brake's 20 GiB line"),
    (19.6, "brightroar · 19.6 GiB available of 121 · 0.4 below the brake's 20 GiB line"),
])
def test_near_the_brake_line_the_header_shows_tenths_rounded_against_the_box(available, header):
    # What's available rounds down, so a box past the line never reads as on it, and nothing shows as -0 (Phase 1).
    view = example_view()
    view["memory"] |= {"available_gib": available, "above_brake_gib": available - 20}
    assert text(view).splitlines()[0] == header


def test_times_more_than_a_day_away_show_their_day():
    view = example_view()
    view["held"] = {"size_gib": 40.0, "until": at(18, 0, day=16)}
    view["recent"][0]["at"] = at(3, 40, day=12)
    assert row(view, "held")[0].startswith("held        40 GiB for you until Fri 16 Oct, 18:00 · ")
    assert row(view, "recent")[-1] == ("            Mon 12 Oct, 03:40  brake released; reloading Gemma and the "
                                       "embeddings")
    # The zone is the one given: seven hours behind UTC, 09:12 is 02:12.
    assert row(view, "recent", tz=timezone(timedelta(hours=-7)))[0].startswith("recent      02:12  for pi on the Mac: ")


def test_waits_read_in_their_units():
    view = example_view()
    for waited, wait, said in [(12.0, 30, "12 s of 30"), (40.0, 600, "40 s of 10 min"), (120.0, 600, "2 min of 10"),
                               (75.0, 90, "1 min of 1 minute 30 s")]:
        view["waiting"][0] |= {"waited_s": waited, "wait_s": wait}
        assert row(view, "waiting") == [f"waiting     agent → the coder · {said} · waiting for you: spark load coder"]


def test_loaded_rows_say_what_each_model_is_doing():
    view = example_view()
    view["models"][1]["state"] = "starting"
    view["models"][2] |= {"state": "draining", "inflight": 1, "oldest_request_s": 200.0}
    view["models"][3] |= {"state": "stopping", "brake_mark": None}
    lines = text(view).splitlines()
    assert lines[3:7] == [
        "loaded      Gemma (gemma-4-26b-a4b)                up to 32 GiB  always loaded     answering 1",
        "            the embeddings (qwen3-embedding-0.6b)  up to 8 GiB   always loaded     loading",
        "            whisper (whisper-large-v3-turbo)       up to 3 GiB   always loaded     unloading, answering 1 "
        "(oldest 3 min)",
        "            the coder (qwen3.8-27b)                up to 41 GiB  loads when asked  stopping"]
    assert lines[7].startswith("waiting ")  # every model loaded: no `not loaded` row


def test_nothing_loaded_waiting_or_recent_reads_so():
    view = example_view()
    for each in view["models"]:
        each["state"] = "not_loaded"
    view["waiting"], view["recent"], view["sessions"] = [], [], []
    lines = text(view).splitlines()
    assert lines[3] == "loaded      nothing"
    assert lines[4].startswith("not loaded  Gemma (gemma-4-26b-a4b)  ")
    assert row(view, "waiting") == ["waiting     nothing"]
    assert row(view, "recent") == ["recent      nothing"]
    assert row(view, "held") == ["held        nothing · pins none · sessions none"]


def test_spark_load_names_the_models_first_role_from_the_registry(box, monkeypatch):
    view = example_view()
    view["models"].append(model("vision-chat", "the vision model", 20, True, state="not_loaded"))
    view["waiting"][0]["model"] = "vision-chat"  # the fixture registry's roles: [small, vision]
    with serving(box.folder / "control.sock", view):
        code, out = box()
        assert code == 0 and "waiting     agent → the vision model · 2 min of 10 · waiting for you: spark load small" \
            in out.splitlines()
        monkeypatch.setattr(paths, "REGISTRY", box.folder / "missing.yaml")  # then the name, which `spark load` takes
        code, out = box()
    assert code == 0 and next(line for line in out.splitlines() if line.startswith("waiting")).endswith(
        "waiting for you: spark load vision-chat")


def test_what_the_gate_says_is_shown_escaped():
    # Labels, session labels and *recent* texts come from the registry, the key list and callers: none of it may drive
    # the terminal or forge a line of its own.
    view = example_view()
    view["sessions"][0]["label"] = "agent's pi\nhealth      all fine\x1b[2J"
    view["recent"][0]["text"] = "brake released\x1b]0;title\x07"
    shown = text(view)
    assert "agent's pi\\nhealth      all fine\\x1b[2J" in shown and "brake released\\x1b]0;title\\x07" in shown
    assert len(shown.splitlines()) == len(EXAMPLE) and all(c.isprintable() for c in shown.replace("\n", ""))


def test_an_answer_status_cant_read_is_said_not_a_crash(box):
    with serving(box.folder / "control.sock", {"schema": 2, "host": "brightroar"}):
        code, out = box()
        assert code == 0 and out.splitlines() == [
            "brightroar · 48 GiB available of 121 · 28 above the brake's 20 GiB line",
            "gate        answered, but with a status this spark status can't read (its schema is 2, and this reads 1); "
            "spark status --json shows it as it came"]
        code, out = box("--json")
    assert code == 0 and standard(out) == {"schema": 2, "host": "brightroar"}
    view = example_view()
    del view["memory"]["owed_gib"]
    with serving(box.folder / "control.sock", view):
        code, out = box()
    assert code == 0 and out.splitlines()[1] == ("gate        answered, but with a status this spark status can't read "
                                                 "(KeyError); spark status --json shows it as it came")


def test_a_socket_that_refuses_this_login_is_said_as_it_is(box):
    if os.geteuid() == 0:
        pytest.skip("root connects to any socket")
    with serving(box.folder / "control.sock", example_view()):
        (box.folder / "control.sock").chmod(0)
        code, out = box()
    lines = out.splitlines()
    assert code == 0 and lines[0].startswith("brightroar · 48 GiB available of 121")
    assert lines[1].startswith(f"gate        The gate's control socket, {box.folder / 'control.sock'}, refused this "
                               "login")


def test_the_view_is_never_changed_by_formatting_it():
    view = example_view()
    kept = copy.deepcopy(view)
    text(view)
    assert view == kept


def test_make_status_says_what_it_shows():
    makefile = (Path(__file__).resolve().parents[2] / "Makefile").read_text()
    assert ("status: ## On the Spark: room for a load, what's loaded, waiting, paused or held, recent, health (from "
            "the gate)\n\t$(SPARK) status\n") in makefile

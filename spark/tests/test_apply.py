import argparse
import itertools
import json
import math
import os
import re
import select
import shutil
import signal
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from types import SimpleNamespace

import pytest

from spark import apply as spark_apply
from spark import cli, gateproto, messages, paths
from spark import render as spark_render
from spark.apply import (RUNNING_UNITS, UNKNOWN_START, ApplyGate, RestartPlan, app_diff, apply_files, diff_text,
                         diff_tree, not_installed, outdated_units, plan_restarts, read_copies, restart_order,
                         start_times, started_at, validation_env, wait_for_drained, wait_for_quiet)
from spark.gate.app import STILL_FINISHING
from spark.gateclient import GateClient, GateRefused, GateUnavailable
from spark.registry import load_registry
from spark.render import INTERNAL_KEY_ENVS, installed_path, render
from spark.versions import load_versions

LLAMA, BRAKE, COMPOSE = "local-ai-llama-swap.service", "local-ai-brake.service", "local-ai-compose.service"
FRONT, GATE = "local-ai-front.service", "local-ai-gate.service"  # Phase 2a's (Task 31)
FILES = {"llama-swap.yaml": "a", "models.yaml": "m", "compose/compose.yaml": "c",
         "systemd/local-ai-llama-swap.service": "u"}
# Root's copies of the units and the Compose project, as `make install-units` installed them.
ROOTS = {"compose/compose.yaml": "c", "systemd/local-ai-llama-swap.service": "u"}
# The registry apply deployed last and the one it deploys: the same, unless a test says otherwise (Task 31: the front
# restarts for a change to its key groups, which it reads only at start).
REGISTRY = load_registry(Path(__file__).parent / "fixtures/models.yaml")


def seeded(etc):
    for rel, content in FILES.items():
        (etc / rel).parent.mkdir(parents=True, exist_ok=True)
        (etc / rel).write_text(content)
    return etc


# Task 32: the gate, as `spark apply` sees it on its control socket. GateScript answers each of apply's routes as the
# gate's core does (gate/core.py's quiet, drain_all, apply_renew, apply_begin, apply_end and undrain_all, and the
# status view's health and applying), scripted by the test; ScriptClient hands it apply's calls in-process, raising as
# GateClient does, and serve_script puts it on a short Unix socket for `spark apply` whole (Box) and for a `spark
# apply` in a subprocess. FakeTime is the clock apply's waits are given.

def idle(quiet_for_s, last_label="the coder", **front):
    """/v1/quiet with nothing in flight for `quiet_for_s` (gateproto.QuietAnswer). `front` sets what the gate says of
    the front's snapshot: `front_connected`, `holding`, `snapshot_at`; GateScript fills in each left out (Task 32's
    review, C1)."""
    return {"quiet_for_s": float(quiet_for_s), "last_label": last_label, "inflight": [], **front}


def busy(*askers, label="the coder", age_s=20.0, **front):
    """/v1/quiet with a request in flight for each of `askers`, by its key's label, the oldest first."""
    return {"quiet_for_s": 0.0, "last_label": label,
            "inflight": [{"model": "coder", "model_label": label, "key_label": asker, "age_s": age_s - i}
                         for i, asker in enumerate(askers)], **front}


QUIET = idle(120)
# What a dry run says of restarts that wait for the quiet moment, with the gate answering.
WOULD_WAIT = ("apply: dry run — would wait until nothing has been in flight for 60 s (for up to 15 minutes, then offer "
              "to drain now), then restart ")


class Down(Exception):
    """The gate doesn't answer this call: the socket holds it, or nothing listens. `sent` false: it never got there
    (GateUnavailable's `sent`)."""

    def __init__(self, sent: bool = True):
        super().__init__()
        self.sent = sent


class GateScript:
    """The gate, for apply's calls. Each call is kept in `calls` as (method, route, body, time by `clock`), and named in
    `events` as `gate <method> <route>`, a renewal's apart (`renewals`), since it comes from a thread of its own.
    - GET /v1/quiet: `quiet`, a list of readings (the last repeated) or a function of (n, held), n counting from 0;
      always quiet by default. `around` scripts the readings before drain-all and after it. A reading that is an
      exception is raised, as a Ctrl-C during the wait.
    - POST /v1/drain-all: apply's hold, from then: {since}. /v1/apply/renew: {ok} while it stands, {ended} once ended.
    - POST /v1/apply/begin: {ok} while the hold stands, else {ended}. /v1/apply/end and /v1/undrain-all end it: {},
      or `end_status` with STILL_FINISHING.
    - GET /v1/status: health.llama_swap and applying. After llama-swap's restart (`restarted`), "not answering" for
      `llama_back_after` readings (math.inf: for ever), then "ok", the hold ended, as the gate's own end does once
      llama-swap answers again (gate/core.py's _check_apply_end).
    - `down`: every call goes unanswered; after the gate's restart, `gate_back_after` calls do (math.inf: for ever).
    - `front_connected`: whether the front's events call is open (the status view's health.front, and /v1/quiet's
      `front_connected`). A reading left without them gets `front_connected`, `holding` (the hold stands and the front
      is connected) and `snapshot_at` (now): the real front posts at once when it starts holding (Task 32's review, C1).
    - `renew_status`: apply/renew's status, 200 or an error. `end_hold_at`, `down_at` and `lose_hold_at`: a unit at
      whose restart the gate ends the hold by itself (between renewals), stops answering, or loses the hold (a start
      fresh from a damaged state file: the next renewal re-creates it, and says so). `drain_down`: drain-all goes
      unanswered, with `sent` as given.
    A call the gate's routes don't have, or a POST whose body isn't an object, is answered 404 or 400 and kept in
    `bad`, as the gate would answer it."""

    def __init__(self, clock=time.monotonic, events=None):
        self.clock = clock
        self.events = [] if events is None else events
        self.calls, self.renewals, self.bad = [], [], []
        self.quiet = [QUIET]
        self.readings = 0
        self.down = False
        self.gate_back_after = 0
        self.llama_back_after = 0
        self.end_status = 200
        self.begin_answer = None  # None: as the hold stands; else this answer
        self.front_connected = True
        self.renew_status = 200
        self.end_hold_at = self.down_at = self.lose_hold_at = None
        self.drain_down: Down | None = None
        self.drain_status = 200  # drain-all's status: 200, or an error the gate answers with
        self.end_down = False  # apply/end and undrain-all go unanswered
        self.hold = None  # {"since", "ended", "restarting"}
        self._gate_down = 0
        self._llama_wait = None
        self.lock = threading.RLock()

    def around(self, before=(QUIET,), after=(QUIET,)):
        """The readings before drain-all, each in turn, the last repeated; then those after it."""
        ahead, behind = list(before), list(after)
        counts = {"before": 0, "after": 0}

        def reading(n, held):
            key, seq = ("after", behind) if held else ("before", ahead)
            at = counts[key]
            counts[key] += 1
            return seq[min(at, len(seq) - 1)]

        self.quiet = reading

    def restarted(self, unit):
        """`systemctl restart <unit>`, as the box saw it."""
        with self.lock:
            if unit == GATE:
                self._gate_down = self.gate_back_after
            if unit == LLAMA:
                self._llama_wait = self.llama_back_after
            if unit == self.end_hold_at and self.hold is not None:
                self.hold["ended"] = True
            if unit == self.down_at:
                self.down = True
            if unit == self.lose_hold_at:
                self.hold = None

    def held(self):
        return self.hold is not None and not self.hold["ended"]

    def routes(self, route):
        return [call for call in self.calls if call[1] == route]

    def handle(self, method, route, body):
        with self.lock:
            self.calls.append((method, route, body, self.clock()))
            (self.renewals if route == "/v1/apply/renew" else self.events).append(f"gate {method} {route}")
            if self.down:
                raise Down
            if self._gate_down > 0:
                self._gate_down -= 1
                raise Down
            known = {(r.method, r.path) for r in gateproto.ROUTES if r.socket == "control"}
            if (method, route) not in known:
                self.bad.append((method, route))
                return 404, {"message": "no such route", "code": None}
            if method == "POST" and not isinstance(body, dict):
                self.bad.append((method, route))
                return 400, {"message": "not an object", "code": None}
            return self._answer(method, route, body)

    def _answer(self, method, route, body):
        if route == "/v1/quiet":
            n, self.readings = self.readings, self.readings + 1
            answer = self.quiet(n, self.held()) if callable(self.quiet) else self.quiet[min(n, len(self.quiet) - 1)]
            if isinstance(answer, BaseException):
                raise answer
            answer = dict(answer)
            answer.setdefault("front_connected", self.front_connected)
            answer.setdefault("holding", answer["front_connected"] and self.held())
            answer.setdefault("snapshot_at", self.clock())
            return 200, answer
        if route == "/v1/drain-all":
            if self.drain_down is not None:
                raise self.drain_down
            if self.drain_status != 200:
                return self.drain_status, {"message": "The gate failed on that request.", "code": None}
            if not self.held():
                self.hold = {"since": self.clock(), "ended": False, "restarting": []}
            return 200, {"since": self.hold["since"], "at": self.clock()}
        if route == "/v1/apply/renew":
            if self.renew_status != 200:
                return self.renew_status, {"message": "The gate failed on that request.", "code": None}
            if self.hold is None:
                self.hold = {"since": body["since"], "ended": False, "restarting": []}
                return 200, {"ok": True, "recreated": True}
            return 200, {"ended": True} if self.hold["ended"] else {"ok": True}
        if route == "/v1/apply/begin":
            if self.begin_answer is not None:
                return 200, self.begin_answer
            if not self.held():
                return 200, {"ended": True}
            self.hold["restarting"] = list(body["restarting"])
            return 200, {"ok": True}
        if route in ("/v1/apply/end", "/v1/undrain-all"):
            if self.end_down:
                raise Down
            if self.end_status != 200:
                said = STILL_FINISHING if self.end_status == 503 else "The gate failed on that request."
                return self.end_status, {"message": said, "code": None}
            if self.hold is not None:
                self.hold["ended"] = True
            return 200, {}
        if route == "/v1/status":
            answering = "ok"
            if self._llama_wait is not None:
                if self._llama_wait > 0:
                    self._llama_wait -= 1
                    answering = "not answering"
                elif self.hold is not None:
                    self.hold["ended"] = True  # the gate's own end: llama-swap answered after its restart
            applying = {"since": self.hold["since"], "restarting": self.hold["restarting"]} if self.held() else None
            front = "ok" if self.front_connected else "not connected"
            return 200, {"health": {"llama_swap": answering, "front": front}, "applying": applying}
        raise AssertionError(f"GateScript has no answer for {method} {route}")


class ScriptClient:
    """GateClient's calls, answered by a GateScript in-process, with GateClient's errors."""

    def __init__(self, script, timeout_s=gateproto.REQUEST_TIMEOUT_S):
        self.script, self.timeout_s = script, timeout_s

    def get(self, route):
        return self._call("GET", route, None)

    def post(self, route, body):
        return self._call("POST", route, body)

    def _call(self, method, route, body):
        try:
            status, payload = self.script.handle(method, route, body)
        except Down as down:
            raise GateUnavailable(f"The gate didn't answer on /run/local-ai/gate-control.sock within "
                                  f"{self.timeout_s:g} s. On the Spark, `make doctor` shows what's wrong.",
                                  sent=down.sent) from None
        if not 200 <= status < 300:
            raise GateRefused(status, payload)
        return payload


class _ScriptHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _any(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length)) if length else None
        try:
            status, payload = self.server.script.handle(self.command, self.path, body)
        except Down:  # no answer: the connection closes, as a gate that died does
            self.close_connection = True
            return
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = _any


class _ScriptServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


def serve_script(script, path):
    """`script` on a Unix socket at `path`, in threads of its own; returns the server, to shut down."""
    server = _ScriptServer(str(path), _ScriptHandler)
    server.script = script
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    return server


class FakeTime:
    """apply's clock in a test: `sleep` moves it on at once, then waits until each watched hold's renewal thread has
    made every renewal due by then and gone back to its `pause`, so a renewal lands at the time it is due."""

    def __init__(self, start=1000.0):
        self.now = start
        self.slept = []
        self.cond = threading.Condition()
        self.waiting = {}  # each renewal thread in its pause, and when it wakes
        self.holds = []  # the ApplyGates whose renewal thread `sleep` waits for

    def clock(self):
        return self.now

    def pause(self, due, stopped):
        me = threading.current_thread()
        with self.cond:
            self.waiting[me] = due
            self.cond.notify_all()
            try:
                while self.now < due and not stopped.is_set():
                    self.cond.wait(0.01)
            finally:
                del self.waiting[me]
                self.cond.notify_all()
            return stopped.is_set()

    def _settled(self):
        for hold in self.holds:
            thread = hold._thread
            if thread is None or not thread.is_alive():
                continue
            if self.waiting.get(thread, -math.inf) <= self.now:
                return False
        return True

    def sleep(self, seconds):
        with self.cond:
            self.slept.append(seconds)
            self.now += seconds
            self.cond.notify_all()
            deadline = time.monotonic() + 10
            while not self._settled():
                assert time.monotonic() < deadline, "the renewal thread didn't settle"
                self.cond.wait(0.01)


def apply_gate(script, clock=None, logs=None):
    """An ApplyGate on `script`, timed by a FakeTime (`clock`) whose sleep waits for its renewals."""
    clock = clock or FakeTime()
    script.clock = clock.clock
    gate = ApplyGate(ScriptClient(script), ScriptClient(script, spark_apply.END_TIMEOUT_S), clock=clock.clock,
                     sleep=clock.sleep, pause=clock.pause, log=(logs.append if logs is not None else print))
    clock.holds.append(gate)
    return gate


def run_apply(etc, files, *, installed=ROOTS, unreadable=(), outdated=(), active=(LLAMA, BRAKE, COMPOSE, FRONT, GATE),
              app_changes=(), now_ok=False, dry_run=False, fails=(), stays_up=None, registry_old=REGISTRY,
              registry_new=REGISTRY, restart_front=False, script=None, clock=None, answers=(), deadline_s=None,
              events=None):
    """apply_files with stand-ins: systemctl (`fails` don't restart), the sync, and the gate (`script`, a quiet one by
    default), timed by `clock`; `answers` answers apply's questions in turn. `events`, when given, gets each restart,
    the sync and each call to the gate, in order."""
    ran, logs, synced, asked = [], [], [], []
    events = [] if events is None else events
    script = GateScript() if script is None else script
    script.events = events
    gate = apply_gate(script, clock, logs)
    replies = list(answers)

    def run_cmd(cmd):  # systemctl, as a stand-in: a unit in `fails` doesn't restart
        if cmd[-1] in fails:
            raise subprocess.CalledProcessError(1, cmd)
        ran.append(cmd)
        events.append(f"restart {cmd[-1]}")
        script.restarted(cmd[-1])

    def ask(question):
        asked.append(question)
        return replies.pop(0) if replies else None

    def sync():
        synced.append(True)
        events.append("sync")

    extra = {} if deadline_s is None else {"deadline_s": deadline_s}
    code = apply_files(files, etc, installed=dict(installed), unreadable=set(unreadable), outdated=list(outdated),
                       active=lambda unit: unit in active, app_changes=list(app_changes), now_ok=now_ok,
                       dry_run=dry_run, sync_app=sync, run_cmd=run_cmd, log=logs.append, stays_up=stays_up,
                       registry_old=registry_old, registry_new=registry_new, restart_front=restart_front, gate=gate,
                       ask=ask, **extra)
    run_apply.asked, run_apply.script, run_apply.gate = asked, script, gate
    assert script.bad == []
    return code, ran, logs, synced


def test_nothing_changed_does_nothing(tmp_path):
    code, ran, logs, _ = run_apply(seeded(tmp_path), FILES)
    assert (code, ran, logs) == (0, [], ["apply: nothing to change"])


def test_a_llama_swap_config_change_restarts_it_after_a_quiet_moment(tmp_path):
    # Task 32: whatever is loaded. Phase 1 restarted it only with no model loaded; the gate's quiet reading replaces that.
    code, ran, _, _ = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b"})
    assert code == 0 and ran == [["systemctl", "restart", "local-ai-llama-swap.service"]]
    assert (tmp_path / "llama-swap.yaml").read_text() == "b"


def test_now_restarts_llama_swap_anyway(tmp_path):
    script = GateScript()
    script.quiet = [busy("pi on the Mac")]
    code, ran, _, _ = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b"}, now_ok=True, script=script,
                                answers=[True])
    assert code == 0 and ran == [["systemctl", "restart", "local-ai-llama-swap.service"]]


def test_an_app_change_is_synced_and_restarts_the_brake_and_the_gate(tmp_path):
    # Task 31: the gate imports the app too, so it restarts with the brake, after it.
    code, ran, _, synced = run_apply(seeded(tmp_path), FILES, app_changes=["src/spark/brake.py"])
    assert code == 0 and synced == [True]
    assert ran == [["systemctl", "restart", BRAKE], ["systemctl", "restart", GATE]]


def test_a_changed_unit_is_staged_for_root_and_nothing_else_moves(tmp_path):
    # Root runs only root's copy. apply stages the new unit where `make install-units` reads it, and
    # deploys and restarts nothing until root has it: not llama-swap for its new config, which the
    # new unit may need, not the brake for the app, not the web services for their older copy.
    new = FILES | {"systemd/local-ai-llama-swap.service": "u2", "llama-swap.yaml": "b"}
    code, ran, logs, synced = run_apply(seeded(tmp_path), new, app_changes=["src/spark/brake.py"],
                                        outdated=[COMPOSE])
    assert code == 0 and ran == [] and synced == []
    assert (tmp_path / "systemd/local-ai-llama-swap.service").read_text() == "u2"
    assert (tmp_path / "llama-swap.yaml").read_text() == "a"
    assert "systemd/local-ai-llama-swap.service differs from root's copy" in "\n".join(logs)
    assert "make install-units" in logs[-1]


def test_the_first_deploy_stages_roots_files_before_anything_else(tmp_path):
    code, ran, logs, synced = run_apply(tmp_path, FILES, installed={}, active=(), app_changes=["pyproject.toml"])
    assert code == 0 and ran == [] and synced == []
    staged = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file())
    assert staged == sorted(ROOTS)
    assert "root has no copy of compose/compose.yaml yet" in "\n".join(logs)
    assert "make install-units" in logs[-1]


def test_a_running_unit_is_restarted_once_its_new_definition_is_installed(tmp_path):
    # `make install-units` installed a new Compose project; the web services still run the old one.
    code, ran, _, _ = run_apply(seeded(tmp_path), FILES, outdated=[COMPOSE])
    assert code == 0 and ran == [["systemctl", "restart", "local-ai-compose.service"]]


def test_llama_swap_waits_for_a_quiet_moment_to_run_its_new_definition(tmp_path):
    # Phase 1 waited for idle models, or --now; since Task 32, for 60 s with nothing in flight.
    script, clock = GateScript(), FakeTime()
    script.around(before=[busy("agent"), busy("agent"), *(idle(s) for s in range(0, 61))])
    code, ran, _, _ = run_apply(seeded(tmp_path), FILES, outdated=[LLAMA], script=script, clock=clock)
    assert code == 0 and ran == [["systemctl", "restart", "local-ai-llama-swap.service"]]
    # Task 32's review (C1): and one more, just before llama-swap's restart, under the hold.
    assert script.readings == 65 and sum(clock.slept) == 62  # each reading a second apart, the last one quiet


def test_a_unit_that_isnt_running_is_never_started(tmp_path):
    code, ran, logs, _ = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b"}, active=())
    assert code == 0 and ran == [] and (tmp_path / "llama-swap.yaml").read_text() == "b"
    assert logs[-1] == "apply: local-ai-llama-swap.service isn't running; it starts with the new config"


def test_a_dry_run_lists_only_the_restarts_the_real_run_makes(tmp_path):
    code, ran, logs, _ = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b", "models.yaml": "m2"},
                                   active=(BRAKE,), dry_run=True)
    assert code == 0 and ran == []
    assert "apply: dry run — would restart local-ai-brake.service" in logs
    assert "apply: dry run — local-ai-llama-swap.service isn't running; it starts with the new config" in logs


def test_a_restart_that_fails_says_the_files_are_deployed_and_how_to_finish(tmp_path):
    # A unit that couldn't start again is stopped, and apply restarts only units that run, so the next apply finds
    # nothing to change: this is the one time to say which unit still needs it. (One that still runs, because systemd
    # refused the restart, started before its files were written, and the next apply restarts it: Task 7's fix round 1.)
    code, ran, logs, _ = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b", "models.yaml": "m2"},
                                   fails=(BRAKE,))
    assert code == 1
    assert ran == [["systemctl", "restart", GATE], ["systemctl", "restart", LLAMA]]  # the other restarts still happen
    assert (tmp_path / "models.yaml").read_text() == "m2"
    assert ("apply: restarting local-ai-brake.service failed, and its new files are deployed: see "
            "`make logs s=brake`, and once it's fixed, `systemctl restart local-ai-brake.service`") in logs
    # Task 32: a failure after drain-all ends apply's hold with undrain-all, never apply/end.
    assert run_apply.script.routes("/v1/undrain-all") and not run_apply.script.routes("/v1/apply/end")


def test_a_copy_apply_cant_read_is_not_called_missing(tmp_path):
    code, _, logs, _ = run_apply(seeded(tmp_path), FILES, installed={"systemd/local-ai-llama-swap.service": "u"},
                                 unreadable={"compose/compose.yaml"})
    text = "\n".join(logs)
    assert code == 0 and "can't read root's copy of compose/compose.yaml" in text
    assert "root has no copy of compose/compose.yaml" not in text


def test_dry_run_changes_nothing(tmp_path):
    code, ran, logs, synced = run_apply(seeded(tmp_path), FILES | {"models.yaml": "m2"},
                                        app_changes=["pyproject.toml"], outdated=[COMPOSE], dry_run=True)
    assert code == 0 and ran == [] and synced == []
    assert (tmp_path / "models.yaml").read_text() == "m"
    # pyproject.toml is the app's environment, which the front runs too (Task 31), so they wait for a quiet moment
    # (Task 32). The dry run reads /v1/quiet, to say what the real run would do, and nothing else of the gate.
    assert logs[-1] == WOULD_WAIT + f"{BRAKE}, {GATE}, {FRONT}, {COMPOSE}"
    assert [call[:2] for call in run_apply.script.calls] == [("GET", "/v1/quiet")]


def test_a_dry_run_shows_what_it_would_stage_and_changes_nothing(tmp_path):
    code, ran, logs, synced = run_apply(seeded(tmp_path), FILES | {"compose/compose.yaml": "c2"}, dry_run=True)
    assert code == 0 and ran == [] and synced == []
    assert (tmp_path / "compose/compose.yaml").read_text() == "c"
    assert "compose/compose.yaml differs from root's copy" in "\n".join(logs)
    assert "make install-units" in logs[-1]


def test_restart_mapping():
    # Task 31: units_to_restart's cases, as plan_restarts has them, the gate beside the brake.
    def restarts(changed=(), app=()):
        plan = plan_restarts(list(changed), list(app), REGISTRY, REGISTRY, [], restart_front=False,
                             active=lambda unit: True)
        return plan.now + plan.after_quiet

    assert restarts(["models.yaml"]) == [BRAKE, GATE]
    assert restarts(["llama-swap.yaml"]) == [LLAMA]
    assert restarts(app=["src/spark/brake.py"]) == [BRAKE, GATE]
    # Root's files restart nothing by themselves: a unit restarts once root's copy is installed.
    assert restarts(["compose/searxng/settings.yml", "systemd/local-ai-brake.service"]) == []


def test_roots_files_wait_for_make_install_units_until_roots_copies_match():
    installed = {"compose/compose.yaml": "c", "systemd/local-ai-llama-swap.service": "old"}
    files = FILES | {"systemd/local-ai-brake.service": "b"}
    assert not_installed(files, installed) == ["systemd/local-ai-brake.service", "systemd/local-ai-llama-swap.service"]


def test_a_running_unit_that_started_before_its_copy_was_installed_is_outdated():
    started = {LLAMA: 100.0, BRAKE: 100.0, COMPOSE: 100.0}
    changed_at = {
        "systemd/local-ai-llama-swap.service": 100.25,  # installed a quarter second after llama-swap started
        "systemd/local-ai-brake.service": 100.0,        # installed as the brake started: not after
        "compose/searxng/settings.yml": 150.0,         # the Compose project is the web services' definition
        "systemd/local-ai-pull.service": 200.0,         # the pull unit runs only when asked
    }
    assert outdated_units(started, changed_at) == ["local-ai-compose.service", "local-ai-llama-swap.service"]
    # A unit that isn't running starts with its new definition.
    assert outdated_units({LLAMA: None}, {"systemd/local-ai-llama-swap.service": 200.0}) == []


def test_started_at_reads_when_the_last_start_began():
    # InactiveExitTimestamp is when the start began, ActiveEnterTimestamp when it ended: for the compose
    # unit, whose start runs `docker compose up` for up to 15 minutes, a copy installed meanwhile is
    # newer than the start's beginning, and would look older than its end.
    show = ("ActiveState=active\n"
            "InactiveExitTimestamp=Fri 2026-09-25 23:19:46.826238 UTC\n"
            "ActiveEnterTimestamp=Fri 2026-09-25 23:34:46.000000 UTC\n")
    assert started_at(show) == datetime(2026, 9, 25, 23, 19, 46, 826238, tzinfo=timezone.utc).timestamp()
    # A stopped unit keeps the time it last started: only its state says it isn't running.
    assert started_at(show.replace("ActiveState=active", "ActiveState=inactive")) is None
    assert started_at("") is None
    for stamp in ("", "@1790371186"):  # a running unit's time that isn't the us+utc form: see start_times
        with pytest.raises(ValueError, match="InactiveExitTimestamp="):
            started_at(f"ActiveState=active\nInactiveExitTimestamp={stamp}\n")


def test_a_running_unit_whose_start_cant_be_read_is_named_not_skipped():
    shows = {LLAMA: "ActiveState=active\nInactiveExitTimestamp=Fri 2026-09-25 23:19:46.826238 UTC\n",
             BRAKE: "ActiveState=active\nInactiveExitTimestamp=@1790371186\n",
             COMPOSE: "ActiveState=inactive\nInactiveExitTimestamp=\n"}
    logs = []
    started = start_times([LLAMA, BRAKE, COMPOSE], shows.get, log=logs.append)
    assert started[COMPOSE] is None and started[BRAKE] == UNKNOWN_START and started[LLAMA] < UNKNOWN_START
    # Task 7's fix round 2 (M3): the line names both reasons its files can be newer, root's copies and apply's own.
    assert logs == [f"apply: can't read when {BRAKE} started (InactiveExitTimestamp='@1790371186'), so it can't "
                    f"tell whether {BRAKE} runs its latest files: if they changed since it started "
                    f"(`make install-units`, or an apply that didn't restart it), run `systemctl restart {BRAKE}`"]
    # Still running, so a changed config restarts it; but no file, however new, counts as newer.
    assert outdated_units(started, {"systemd/local-ai-brake.service": 9e9, "models.yaml": 9e9, "app": 9e9}) == []
    # Restarting llama-swap by hand stops every loaded model, so its line says when to.
    logs.clear()
    start_times([LLAMA], {LLAMA: shows[BRAKE]}.get, log=logs.append)
    assert logs == [f"apply: can't read when {LLAMA} started (InactiveExitTimestamp='@1790371186'), so it can't "
                    f"tell whether {LLAMA} runs its latest files: if they changed since it started "
                    f"(`make install-units`, or an apply that didn't restart it), run `systemctl restart {LLAMA}` "
                    "once no model is loaded (restarting it stops them all)"]


# Phase 1's models_loaded and its tests went with Task 32: apply no longer asks llama-swap what is loaded, with Dan's
# key, which llama-swap refuses from the cutover (only the internal keys reach it); it asks the gate what is in flight.


def test_validate_gives_each_key_its_own_placeholder():
    env = validation_env({"PATH": "/usr/bin", "LLAMASWAP_KEY_GATE": "real"})
    assert env["PATH"] == "/usr/bin"
    values = [env[name] for name in INTERNAL_KEY_ENVS]
    assert len(set(values)) == len(INTERNAL_KEY_ENVS) and "real" not in values


@pytest.mark.skipif(__import__("os").geteuid() == 0, reason="root reads any file")
def test_read_copies_tells_a_missing_copy_from_one_it_cant_read(tmp_path):
    (tmp_path / "a").write_text("x")
    (tmp_path / "b").write_text("y")
    (tmp_path / "b").chmod(0)
    where = {"systemd/a": tmp_path / "a", "systemd/b": tmp_path / "b", "systemd/c": tmp_path / "c"}
    texts, times, unreadable = read_copies({**dict.fromkeys(where, ""), "models.yaml": ""}, where.get)
    assert texts == {"systemd/a": "x", "systemd/b": None, "systemd/c": None} and unreadable == {"systemd/b"}
    assert set(times) == {"systemd/a"} and isinstance(times["systemd/a"], float)


def test_a_missing_file_counts_as_changed(tmp_path):
    assert diff_tree({"new.yaml": "x"}, tmp_path) == ["new.yaml"]


def test_app_diff_skips_the_venv_and_caches(tmp_path):
    src = tmp_path / "src"
    for rel in ("pyproject.toml", "src/spark/cli.py", ".venv/bin/python", "src/spark/__pycache__/cli.pyc"):
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_text("x")
    assert app_diff(src, tmp_path / "app") == ["pyproject.toml", "src/spark/cli.py"]


# Task 7's fix round 1. apply_files' own rules first; then `spark apply` whole, through cli.main, with everything it
# touches faked (Box): the review found run() untested, and a one-line slip there passed every test above.


def put(path, text, at):
    """`path` holding `text`, last modified at `at` (seconds since the epoch)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    os.utime(path, (at, at))


def test_a_dry_run_refuses_what_the_real_run_refuses(tmp_path):
    # Minor 5: it says what the real run would do, and exits as the real run would. Since Task 32 the real run refuses
    # only while the gate isn't answering, with llama-swap running.
    script = GateScript()
    script.down = True
    code, ran, logs, synced = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b", "models.yaml": "m2"},
                                        dry_run=True, script=script)
    assert code == 1 and ran == [] and synced == [] and (tmp_path / "llama-swap.yaml").read_text() == "a"
    assert logs[-1] == ("apply: dry run — would refuse: apply can't see what's in flight while the gate isn't "
                        f"answering; with --now it would restart {BRAKE}, {GATE}, {LLAMA}")
    script.down = False
    script.quiet = [busy("agent")]
    code, _, logs, _ = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b"}, dry_run=True, now_ok=True,
                                 script=script)
    assert code == 0 and logs[-1] == (f"apply: dry run — would restart {LLAMA} now, after asking whether to cut off "
                                      "the 1 request in flight")


def test_the_staging_stop_says_nothing_but_the_staging(tmp_path):
    # Minor 5: at that stop nothing else is deployed, so nothing else is listed as changing. Task 31: the diff it
    # shows is of root's files only, the unit's installed copy against the one it stages.
    new = FILES | {"systemd/local-ai-llama-swap.service": "u2", "llama-swap.yaml": "b"}
    for dry_run in (False, True):
        etc = seeded(tmp_path / str(dry_run))
        _, _, logs, _ = run_apply(etc, new, app_changes=["src/spark/brake.py"], outdated=[COMPOSE], dry_run=dry_run)
        assert logs[:-1] == ["apply: systemd/local-ai-llama-swap.service differs from root's copy "
                             "(/etc/systemd/system/local-ai-llama-swap.service)",
                             "--- installed: /etc/systemd/system/local-ai-llama-swap.service",
                             f"+++ staged: {etc}/systemd/local-ai-llama-swap.service",
                             "@@ -1 +1 @@", "-u", "+u2"]
        assert "make install-units" in logs[-1]


def test_a_llama_swap_whose_unit_is_stopped_is_never_refused_for(tmp_path):
    # Minor 6: however /running answered, a stopped unit has nothing loaded, and apply restarts nothing that's stopped.
    code, ran, logs, _ = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b"}, active=(BRAKE, COMPOSE))
    assert code == 0 and ran == [] and (tmp_path / "llama-swap.yaml").read_text() == "b"
    assert logs[-1] == f"apply: {LLAMA} isn't running; it starts with the new config"


def test_each_file_apply_deploys_belongs_to_the_units_that_run_on_it():
    # Task 31: the gate reads the registry and imports the app, as the brake does; the front's own files are its own.
    # Task 31's review (I2): apply's stamps, for the front's restart for the key groups, and for `--restart`'s.
    rels = ("llama-swap.yaml", "models.yaml", spark_apply.APP, spark_apply.FRONT_APP, "compose/searxng/settings.yml",
            "systemd/local-ai-brake.service", "systemd/local-ai-front.service", "systemd/local-ai-gate.service",
            "systemd/local-ai-pull.service", "systemd/local-ai-front.socket", "systemd/local-ai-notify@.service",
            spark_apply.KEY_GROUPS_STAMP, spark_apply.RESTART_FRONT_STAMP)
    assert [spark_apply.units_of(rel) for rel in rels] == [(LLAMA,), (BRAKE, GATE), (BRAKE, GATE), (FRONT,),
                                                          (COMPOSE,), (BRAKE,), (FRONT,), (GATE,), (), (), (),
                                                          (FRONT,), (GATE, FRONT)]


def test_a_running_unit_that_started_before_apply_wrote_its_files_is_outdated():
    # I2: a run cut off after it wrote a file, before the restart it owed. The next run owes that restart still.
    started = {LLAMA: 100.0, BRAKE: 100.0, COMPOSE: 100.0}
    assert outdated_units(started, {"llama-swap.yaml": 100.25, "models.yaml": 99.0}) == [LLAMA]
    assert outdated_units(started, {"models.yaml": 100.25}) == [BRAKE]
    assert outdated_units(started, {spark_apply.APP: 100.25}) == [BRAKE]  # the app's last sync finished after it
    assert outdated_units({LLAMA: None, BRAKE: None}, {"llama-swap.yaml": 200.0, spark_apply.APP: 200.0}) == []


def test_deployed_times_are_when_apply_wrote_its_files_and_last_synced_the_app(tmp_path):
    etc, app = tmp_path / "etc", tmp_path / "app"
    put(etc / "llama-swap.yaml", "x", 100.0)
    put(app / spark_apply.SYNC_STAMP, "", 300.0)
    put(etc / "compose/compose.yaml", "x", 400.0)  # staged for root: root's copy says when it was installed
    assert spark_apply.deployed_times(etc, app) == {"llama-swap.yaml": 100.0, spark_apply.APP: 300.0}
    put(etc / spark_apply.RESTART_FRONT_STAMP, "", 500.0)  # Task 31's review (I2): apply's stamps are dated too
    assert spark_apply.deployed_times(etc, app) == {"llama-swap.yaml": 100.0, spark_apply.APP: 300.0,
                                                    spark_apply.RESTART_FRONT_STAMP: 500.0}


def test_app_diff_counts_every_file_until_a_sync_finishes_and_files_removed_from_spark(tmp_path):
    # C1 and Minor 8: a sync that failed is tried again, and a file removed from spark/ leaves the app too.
    src, app = tmp_path / "src", tmp_path / "app"
    for rel in ("pyproject.toml", "src/spark/cli.py"):
        put(src / rel, "x", 0)
        put(app / rel, "x", 0)
    put(app / "src/spark/retired.py", "x", 0)
    put(app / ".venv/bin/spark", "x", 0)  # the venv is uv's, not the app's
    assert app_diff(src, app) == ["pyproject.toml", "src/spark/cli.py", "src/spark/retired.py"]  # no sync finished
    put(app / spark_apply.SYNC_STAMP, "", 0)
    assert app_diff(src, app) == ["src/spark/retired.py"]
    put(src / "src/spark/cli.py", "y", 0)
    assert app_diff(src, app) == ["src/spark/cli.py", "src/spark/retired.py"]


def test_after_its_restart_apply_waits_a_bounded_time_for_the_gate_to_see_llama_swap_answer():
    # Phase 1 asked llama-swap's GET /running with Dan's key, which llama-swap refuses from the cutover. Since Task 32
    # the gate tells: its health.llama_swap ok, and apply's hold ended by the gate itself, which it does only once a
    # /running read sent after llama-swap's new start has answered (gate/core.py's _check_apply_end).
    for back_after, said in ((3, None), (math.inf, "llama-swap didn't answer the gate within 30 s of its restart: see "
                                                   "`make logs s=llama-swap`")):
        script, clock, logs = GateScript(), FakeTime(), []
        gate = apply_gate(script, clock, logs)
        gate.drain_all()
        script.llama_back_after = back_after
        script.restarted(LLAMA)
        assert gate.llama_swap_back() == said
        assert logs == ["apply: waiting up to 30 s for the gate to see llama-swap answer again"]
        assert sum(clock.slept) == (3 if said is None else 30)  # never past its time
        gate.undrain()
    # An "ok" while the hold still stands isn't enough: that reading may be from before the restart. Concern 2's ruling:
    # the words say what apply waited for, since llama-swap may well be answering.
    script, clock = GateScript(), FakeTime()
    gate = apply_gate(script, clock, [])
    gate.drain_all()
    assert gate.llama_swap_back() == (
        "the gate, which sees llama-swap answer, didn't confirm its new start within 30 s. apply counts llama-swap as "
        "back only once the gate has ended the hold itself, after a reading taken since the restart, so llama-swap may "
        "well be serving: `make status` shows it, and a gate that can't read llama-swap's unit says so on its health "
        "line") and sum(clock.slept) == 30
    gate.undrain()
    # A gate that doesn't answer at all.
    script, clock = GateScript(), FakeTime()
    gate = apply_gate(script, clock, [])
    gate.drain_all()
    script.restarted(LLAMA)
    script.down = True
    assert gate.llama_swap_back() == ("the gate didn't answer within 30 s of llama-swap's restart, so apply can't "
                                      "confirm llama-swap answers again: `make doctor` shows what's wrong")
    script.down = False
    gate.undrain()


# Phase 1's council (reliability I1): Type=exec counts the brake as started once its Python runs, before the app is
# imported, so a brake that fails once it runs passes `systemctl restart` and then restarts every 2 s.

UP = "ActiveState=active\nSubState=running\nNRestarts=0\n"


@pytest.mark.parametrize("after, said", [
    pytest.param(UP, None, id="stays-up"),
    pytest.param("ActiveState=activating\nSubState=auto-restart\nNRestarts=1\n",
                 "it isn't running 3 s later (activating, auto-restart): see `make logs s=brake`",
                 id="between-restarts"),
    pytest.param("ActiveState=failed\nSubState=failed\nNRestarts=5\n",
                 "it isn't running 3 s later (failed, failed): see `make logs s=brake`", id="gave-up"),
    pytest.param("ActiveState=active\nSubState=running\nNRestarts=1\n",
                 "systemd restarted it again within 3 s, so it stops once it runs: see `make logs s=brake`",
                 id="running-again"),
    pytest.param("ActiveState=active\nSubState=running\n",
                 "apply can't tell whether it stayed up, since systemd gave no restart count: "
                 "`systemctl status local-ai-brake.service`", id="no-count"),
])
def test_after_restarting_the_brake_apply_checks_it_stays_up(after, said):
    shows, slept = iter([UP, after]), []
    assert spark_apply.check_stays_up(BRAKE, show=lambda unit: next(shows), sleep=slept.append) == said
    assert slept == [spark_apply.STAY_UP_S] == [3.0]  # longer than the unit's RestartSec, 2 s


def test_a_brake_that_doesnt_stay_up_after_its_restart_is_said(tmp_path):
    asked = []

    def stays_up(unit):
        asked.append(unit)
        return "it isn't running 3 s later (activating, auto-restart): see `make logs s=brake`"

    code, ran, logs, _ = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b"},
                                   app_changes=["src/spark/brake.py"], stays_up=stays_up)
    assert code == 1 and asked == [BRAKE]  # not llama-swap nor the gate (notify), which the gate's answers confirm
    assert ran == [["systemctl", "restart", BRAKE], ["systemctl", "restart", GATE], ["systemctl", "restart", LLAMA]]
    assert logs[-2:] == [f"apply: restarted {BRAKE}, but it isn't running 3 s later (activating, auto-restart): see "
                         "`make logs s=brake`", "apply: the hold on new requests is ended"]  # Task 32: a failure


@pytest.mark.parametrize("how", ["restart fails", "dry run", "not running"])
def test_only_a_brake_apply_restarted_is_checked(tmp_path, how):
    asked = []
    run_apply(seeded(tmp_path), FILES, app_changes=["src/spark/brake.py"], stays_up=asked.append,
              fails=(BRAKE,) if how == "restart fails" else (), dry_run=how == "dry run",
              active=(LLAMA, COMPOSE) if how == "not running" else (LLAMA, BRAKE, COMPOSE))
    assert asked == []


def test_validate_gets_path_and_a_placeholder_for_each_key_and_nothing_else():
    # Minor 9: Dan's environment can hold secrets of his own, and -validate needs none of it.
    placeholders = {name: f"validate-only-{i}" for i, name in enumerate(INTERNAL_KEY_ENVS)}
    assert validation_env({"PATH": "/usr/bin", "HOME": "/home/dan", "SPARK_API_KEY": "k"}) == {"PATH": "/usr/bin",
                                                                                              **placeholders}
    assert validation_env({}) == {"PATH": os.defpath, **placeholders}


ROOT = Path(__file__).resolve().parents[2]
FIX = Path(__file__).parent / "fixtures"
OLD = time.time() - 3600  # when a finished apply wrote its files, and `make install-units` installed root's copies
T0 = OLD + 600  # when the running units started
# The registry a finished apply deployed before this change: one that loads, with the fixture's key groups, so its
# change restarts the brake and the gate and never the front (Task 31). One that won't load is
# test_run_restarts_the_front_for_the_deployed_registrys_key_groups's.
OLDER_REGISTRY = (FIX / "models.yaml").read_text() + "# the registry before this change\n"


def systemctl_show(start):
    """What `systemctl show --timestamp=us+utc` says of a unit whose start began at `start`; None: not running."""
    if start is None:
        return "ActiveState=inactive\nInactiveExitTimestamp=\n"
    stamp = datetime.fromtimestamp(start, tz=timezone.utc).strftime("%a %Y-%m-%d %H:%M:%S.%f UTC")
    return f"ActiveState=active\nInactiveExitTimestamp={stamp}\n"


class Box:
    """Everything `spark apply` touches, faked under tmp_path, to drive it whole through cli.main:
    - the repo: the fixture registry, its versions pinned, stack's own templates, a small spark/ project;
    - /opt/local-ai (DEPLOY), with a llama-swap binary so -validate runs, and root's two folders;
    - systemd: `units`, when each unit's start began (None: not running); a restart starts it anew;
    - the gate's control socket (Task 32): `gate`, a GateScript on a short Unix socket, reached through apply's own
      GateClient; or, with `gate_path`, a socket some other process serves;
    - its clock: a FakeTime, unless `fake_time` is False; and its questions: `replies`, answered in turn;
    - every command apply runs, each faked here; any other fails the test. Nothing real runs.
    `events` lists, in order, what apply asked of each."""

    def __init__(self, tmp_path, monkeypatch, capsys, *, fake_time=True, gate_path=None):
        self.capsys = capsys
        self.repo, self.opt = tmp_path / "repo", tmp_path / "opt"
        self.etc, self.app = self.opt / "etc", self.opt / "app"
        shutil.copytree(ROOT / "stack/templates", self.repo / "stack/templates")
        shutil.copytree(ROOT / "stack/llama-swap", self.repo / "stack/llama-swap")  # the schema render checks against
        shutil.copy(FIX / "models.yaml", self.repo / "stack/models.yaml")
        pinned = (FIX / "versions.yaml").read_text().replace("pin: null", "pin: sha256:" + "0" * 64)
        (self.repo / "stack/versions.yaml").write_text(pinned)
        # The app's file that changes stands for a change elsewhere in the app, one that never restarts the front: since
        # Task 31's review spark.cli is the front's (FRONT_MODULES), so it is brake.py.
        for rel, text in {"pyproject.toml": "p", "uv.lock": "l", "src/spark/brake.py": "v1"}.items():
            put(self.repo / "spark" / rel, text, OLD)
        put(self.opt / "bin/llama-swap/v257/llama-swap", "", OLD)
        self.units = dict.fromkeys(RUNNING_UNITS)
        self.interrupted, self.failing, self.uv_fails, self.uv_lock = set(), set(), False, None
        self.uv_slow, self.uv_error = False, "error: the stand-in failed"
        self.validate_code, self.validate_env, self.uv_env = 0, None, None
        self.events, self.replies, self.questions = [], [], []
        self.time = FakeTime(time.time()) if fake_time else None
        self.gate = GateScript(self.time.clock if fake_time else time.monotonic, self.events)
        self.server = None
        if gate_path is None:
            folder = Path(tempfile.mkdtemp(prefix="ap", dir="/tmp"))  # an AF_UNIX path is short (macOS: 104 bytes)
            gate_path = folder / "control.sock"
            self.server = serve_script(self.gate, gate_path)
            self.folder = folder
        monkeypatch.setattr(paths, "GATE_CONTROL_SOCKET", Path(gate_path))
        if fake_time:
            monkeypatch.setattr(spark_apply, "_clock", self.time.clock)
            monkeypatch.setattr(spark_apply, "_sleep", self.time.sleep)
        monkeypatch.setattr(spark_apply, "_ask", self.ask)
        monkeypatch.setattr(spark_apply, "DEPLOY", str(self.opt))
        monkeypatch.setattr(spark_render, "UNIT_DIR", str(tmp_path / "root/systemd"))
        monkeypatch.setattr(spark_render, "COMPOSE_DIR", str(tmp_path / "root/compose"))
        monkeypatch.setattr(spark_apply, "_show", lambda unit: systemctl_show(self.units[unit]))
        monkeypatch.setattr(spark_apply, "subprocess",
                            SimpleNamespace(run=self.run, CalledProcessError=subprocess.CalledProcessError,
                                            TimeoutExpired=subprocess.TimeoutExpired))
        monkeypatch.setattr(spark_apply, "write_tree", self.write_tree)
        # What systemd says of the brake after its restart, one answer per look, the last repeated: it stays up.
        self.brake_shows = ["ActiveState=active\nSubState=running\nNRestarts=0\n"]
        monkeypatch.setattr(spark_apply, "_show_restarts", self.show_restarts)
        monkeypatch.setattr(spark_apply, "STAY_UP_S", 0.0)  # no test waits
        monkeypatch.chdir(self.repo)

    def close(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            shutil.rmtree(self.folder, ignore_errors=True)

    def ask(self, question):
        self.questions.append(question)
        print(question)
        return self.replies.pop(0) if self.replies else None

    def show_restarts(self, unit):
        assert unit == BRAKE, unit
        return self.brake_shows.pop(0) if len(self.brake_shows) > 1 else self.brake_shows[0]

    def run(self, cmd, **kwargs):
        cmd = [str(word) for word in cmd]
        if cmd[:3] == ["git", "status", "--porcelain"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if len(cmd) == 4 and cmd[1] == "-config" and cmd[3] == "-validate":
            self.events.append("-validate")
            self.validate_env = kwargs.get("env")
            return subprocess.CompletedProcess(cmd, self.validate_code)
        if cmd[:1] == ["uv"] and "sync" in cmd:
            self.events.append("uv sync")
            self.uv_env, self.uv_argv, self.uv_timeout = kwargs.get("env"), cmd, kwargs.get("timeout")
            # uv colours its lines under FORCE_COLOR unless told `--color never` (uv 0.12.18, seen 2026-10-15).
            never = "--color" in cmd and cmd[cmd.index("--color") + 1] == "never"
            colour = "FORCE_COLOR" in (kwargs.get("env") or {}) and not never
            debug = "\x1b[34mDEBUG\x1b[39m" if colour else "DEBUG"
            if self.uv_fails:
                raise subprocess.CalledProcessError(2, cmd, output="", stderr=f"{debug} Found project root\n"
                                                                              f"{self.uv_error}\n")
            if self.uv_lock is not None:  # uv waits on a lock another process holds, until apply's bound
                said = (f"{debug} Found project root: `{self.app}`\n"
                        f"INFO Waiting to acquire exclusive lock for `.venv` at `{self.uv_lock}`\n").encode()
                raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"), output=b"", stderr=said)
            if self.uv_slow:  # a lock waited on, then got ("Checked …" follows), and the sync slow after it
                said = (f"INFO Waiting to acquire exclusive lock for `.venv` at `{self.app}/.venv/.lock`\n"
                        "Checked in 0.00ms\nDownloading uvicorn (1.2MiB)\n").encode()
                raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"), output=b"", stderr=said)
            put(self.app / ".venv/bin/spark", "", time.time())
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr=f"{debug} Found project root\nInstalled 1 "
                                                                          "package in 3ms\n")
        if cmd[:2] == ["systemctl", "restart"] and len(cmd) == 3:
            self.events.append(f"restart {cmd[2]}")
            if cmd[2] in self.interrupted:
                raise KeyboardInterrupt  # Ctrl-C, before systemd took the job
            if cmd[2] in self.failing:  # systemctl exits 1, which only check=True turns into an error, as for real
                if kwargs.get("check"):
                    raise subprocess.CalledProcessError(1, cmd)
                return subprocess.CompletedProcess(cmd, 1)
            self.units[cmd[2]] = time.time()
            self.gate.restarted(cmd[2])
            return subprocess.CompletedProcess(cmd, 0)
        raise AssertionError(f"apply ran {cmd}, which no test fakes")

    def write_tree(self, files, out):
        self.events.extend(f"write {rel}" for rel in files)
        spark_render.write_tree(files, out)

    def rendered(self):
        registry = self.repo / "stack/models.yaml"
        return render(load_registry(registry), load_versions(self.repo / "stack/versions.yaml"), registry.read_text())

    def installed(self, at=OLD):
        """`make install-units` ran at `at`: root's copies are what render renders, and so are the staged ones."""
        for rel, text in self.rendered().items():
            if installed_path(rel) is not None:
                put(Path(installed_path(rel)), text, at)
                put(self.etc / rel, text, at)

    def deployed(self, old=None, running=True):
        """A finished apply left the stack at OLD: root's copies installed, every file deployed (each in `old` with
        that text instead), the app synced. Its units run, started at T0, unless `running` is False."""
        self.installed()
        for rel, text in self.rendered().items():
            if installed_path(rel) is None:
                put(self.etc / rel, (old or {}).get(rel, text), OLD)
        for path in (self.repo / "spark").rglob("*"):
            if path.is_file():
                put(self.app / path.relative_to(self.repo / "spark"), path.read_text(), OLD)
        put(self.app / ".venv/bin/spark", "", OLD)
        put(self.app / spark_apply.SYNC_STAMP, "", OLD)
        self.units = dict.fromkeys(RUNNING_UNITS, T0 if running else None)

    def apply(self, *flags):
        """`spark apply <flags>`: its exit code, what it printed, and what it asked of the fakes."""
        self.events.clear()
        try:
            code = cli.main(["apply", *flags])
        finally:
            printed = self.capsys.readouterr()
        return code, (printed.out + printed.err).splitlines(), list(self.events)


@pytest.fixture
def box(tmp_path, monkeypatch, capsys):
    made = Box(tmp_path, monkeypatch, capsys)
    yield made
    made.close()
    assert made.gate.bad == []


# What `spark apply` asks of the gate (Task 32), as Box's events name each call.
QUIET_READ, DRAIN = "gate GET /v1/quiet", "gate POST /v1/drain-all"
BEGIN, STATUS = "gate POST /v1/apply/begin", "gate GET /v1/status"
END, UNDRAIN = "gate POST /v1/apply/end", "gate POST /v1/undrain-all"
HELD = [QUIET_READ, DRAIN, QUIET_READ]  # quiet at the first reading, the hold, and nothing in flight under it
# With the app to sync: the sync after the quiet reading, before the hold (the controller's ruling at Task 35).
SYNCED = [QUIET_READ, "uv sync", DRAIN, QUIET_READ]


def test_run_stages_roots_files_first_and_says_and_changes_nothing_else(box):
    # The first deploy: root has no copies yet. apply stages them for `make install-units`, and stops there.
    roots = sorted(rel for rel in box.rendered() if installed_path(rel) is not None)
    code, lines, events = box.apply()
    assert code == 0 and events == ["-validate", *(f"write {rel}" for rel in roots)]
    assert sorted(p.relative_to(box.etc).as_posix() for p in box.etc.rglob("*") if p.is_file()) == roots
    assert not box.app.exists()
    said = [line for line in lines[:-1] if line.startswith("apply: ")]  # Task 31: the diff follows them
    assert [line.split(" yet (")[0] for line in said] == [f"apply: root has no copy of {rel}" for rel in roots]
    assert [line for line in lines if line.startswith("+++ ")] == [f"+++ staged: {box.etc}/{rel}" for rel in roots]
    assert lines[-1].startswith(f"apply: staged in {box.etc}; nothing else is deployed or restarted")


def test_run_refuses_a_restart_that_waits_while_the_gate_isnt_answering_unless_now(box):
    # Task 32 replaces Phase 1's look at llama-swap (I4's first slip read a hung one as idle): what is in flight comes
    # from the gate, and while it doesn't answer, apply can't see it.
    box.deployed(old={"llama-swap.yaml": "old config"})
    box.gate.down = True
    code, lines, events = box.apply()
    assert (code, events) == (1, ["-validate", QUIET_READ])  # nothing written, nothing restarted
    assert (box.etc / "llama-swap.yaml").read_text() == "old config"
    assert lines[-2].startswith(f"apply: The gate stopped answering on {paths.GATE_CONTROL_SOCKET}")
    assert lines[-1] == ("apply: nothing was changed: apply can't see what's in flight while the gate isn't "
                         "answering; `make apply-now` restarts anyway")
    code, lines, events = box.apply("--now")  # restarted, with no hold, and no gate to confirm it
    assert (code, events) == (1, ["-validate", QUIET_READ, "write llama-swap.yaml", f"restart {LLAMA}"])
    assert lines[-1] == (f"apply: restarted {LLAMA}, but the gate isn't answering, so apply can't confirm llama-swap "
                         "answers again: see `make logs s=llama-swap`, and `make status` once the gate is back")


def test_run_restarts_llama_swap_under_the_hold_after_the_quiet_moment(box):
    box.deployed(old={"llama-swap.yaml": "old config"})
    code, lines, events = box.apply()
    assert (code, events) == (0, ["-validate", *HELD, "write llama-swap.yaml", BEGIN, QUIET_READ, f"restart {LLAMA}", STATUS, END])
    assert box.gate.routes("/v1/apply/begin")[0][2] == {"restarting": [LLAMA]}
    assert lines[-1] == ("apply: done: the gate has released the requests it held, and reloads the always-loaded "
                         "models one at a time")


def test_run_starts_no_unit_that_isnt_running(box):
    # I4's second slip, `active=lambda unit: True`, started units that weren't running.
    box.deployed(old={"llama-swap.yaml": "old config", "models.yaml": OLDER_REGISTRY}, running=False)
    put(box.repo / "spark/src/spark/brake.py", "v2", time.time())
    code, lines, events = box.apply()
    assert (code, events) == (0, ["-validate", "uv sync", "write llama-swap.yaml", "write models.yaml"])
    assert lines[-3:] == [f"apply: {BRAKE} isn't running; it starts with the new config",
                          f"apply: {GATE} isn't running; it starts with the new config",
                          f"apply: {LLAMA} isn't running; it starts with the new config"]


def test_run_passes_dry_run_on_and_a_dry_run_changes_nothing(box):
    # Task 7's fix round 2 (M1): `make apply-dry-run` goes through run(). It writes, syncs and restarts nothing, and
    # exits as the real run would: 0 when it would deploy, 1 when the real run would refuse. Since Task 32 it reads the
    # gate's /v1/quiet, and nothing else of the gate.
    box.deployed(old={"llama-swap.yaml": "old config", "models.yaml": OLDER_REGISTRY})
    put(box.repo / "spark/src/spark/brake.py", "v2", time.time())
    code, lines, events = box.apply("--dry-run")
    assert (code, events) == (0, ["-validate", QUIET_READ])
    assert lines[-1] == WOULD_WAIT + f"{BRAKE}, {GATE}, {LLAMA}"
    assert (box.etc / "llama-swap.yaml").read_text() == "old config"
    assert (box.app / "src/spark/brake.py").read_text() == "v1" and box.units == dict.fromkeys(RUNNING_UNITS, T0)
    box.gate.down = True
    code, lines, events = box.apply("--dry-run")
    assert (code, events) == (1, ["-validate", QUIET_READ])
    assert lines[-1] == ("apply: dry run — would refuse: apply can't see what's in flight while the gate isn't "
                         f"answering; with --now it would restart {BRAKE}, {GATE}, {LLAMA}")


@pytest.mark.parametrize("stopped", [False, True], ids=["systemd refused it", "it couldn't start"])
def test_run_says_when_a_restart_fails_and_the_next_run_does_what_it_can(box, stopped):
    # Task 7's fix round 2 (M1): the failed-restart path through run(), whose systemctl must run with check=True: a job
    # that fails exits 1, and only check turns that into an error (Box's fake does as subprocess.run does). Refused
    # (a session without spark-admin), the brake still runs its older files, so the next run restarts it; unable to
    # start (Type=exec: a binary missing, say), it's stopped, so only the line says how to finish.
    box.deployed(old={"models.yaml": OLDER_REGISTRY})
    box.failing = {BRAKE}
    code, lines, events = box.apply()
    # Nothing waits for the quiet moment, so no hold; the gate's control socket answers after its restart (Task 32).
    assert (code, events) == (1, ["-validate", "write models.yaml", f"restart {BRAKE}", f"restart {GATE}", STATUS])
    assert (f"apply: restarting {BRAKE} failed, and its new files are deployed: see `make logs s=brake`, "
            f"and once it's fixed, `systemctl restart {BRAKE}`") in lines
    box.failing = set()
    if stopped:
        box.units[BRAKE] = None
    code, lines, events = box.apply()
    if stopped:
        assert (code, events, lines) == (0, ["-validate"], ["apply: nothing to change"])
    else:
        assert (code, events) == (0, ["-validate", f"restart {BRAKE}"])


def test_a_start_time_that_cant_be_read_is_named_after_a_run_that_was_cut_off(box, monkeypatch):
    # Task 7's fix round 2 (M3): a cut-off run wrote llama-swap's config and never restarted it, and now its start time
    # comes in another form. apply can't tell it's outdated, so it says what to run by hand, and why: its line names
    # an apply that didn't restart it, not only `make install-units`. Since Task 32 the Ctrl-C ends apply's hold, and
    # apply exits 130.
    box.deployed(old={"llama-swap.yaml": "old config"})
    box.interrupted = {LLAMA}
    code, lines, events = box.apply()
    assert (code, events) == (130, ["-validate", *HELD, "write llama-swap.yaml", BEGIN, QUIET_READ, f"restart {LLAMA}", UNDRAIN])
    box.interrupted = set()
    shows = {unit: systemctl_show(box.units[unit]) for unit in RUNNING_UNITS}
    shows[LLAMA] = "ActiveState=active\nInactiveExitTimestamp=@1790371186\n"
    monkeypatch.setattr(spark_apply, "_show", shows.get)
    code, lines, events = box.apply()
    assert (code, events) == (0, ["-validate"])
    assert lines == [f"apply: can't read when {LLAMA} started (InactiveExitTimestamp='@1790371186'), so it can't tell "
                     f"whether {LLAMA} runs its latest files: if they changed since it started (`make install-units`, "
                     f"or an apply that didn't restart it), run `systemctl restart {LLAMA}` once no model is loaded "
                     "(restarting it stops them all)",
                     "apply: nothing to change"]


def test_a_failed_sync_is_said_and_tried_again_until_it_succeeds(box):
    # C1: the first deploy after `make install-units`. uv sync fails: apply says so, deploys nothing else, exits 1.
    # The app counts as changed until a sync finishes, so the next run syncs again, then deploys the rest.
    box.installed()
    box.uv_fails = True
    code, lines, events = box.apply()
    assert (code, events) == (1, ["-validate", "uv sync"])
    assert lines[-1] == (f"apply: the app's files are copied into {box.app}, but `uv sync` failed (exit 2), so its "
                         "environment isn't synced; nothing else was deployed or restarted — once it's fixed, "
                         "`make apply` syncs it again")
    assert not (box.etc / "llama-swap.yaml").exists() and not (box.app / spark_apply.SYNC_STAMP).exists()
    box.uv_fails = False
    code, _, events = box.apply()
    assert (code, events) == (0, ["-validate", "uv sync", "write llama-swap.yaml", "write models.yaml"])
    assert (box.app / ".venv/bin/spark").exists() and (box.app / spark_apply.SYNC_STAMP).exists()
    assert box.apply()[1] == ["apply: nothing to change"]


def test_a_failed_sync_on_a_running_stack_restarts_the_brake_once_it_succeeds(box):
    box.deployed()
    put(box.repo / "spark/src/spark/brake.py", "v2", time.time())
    box.uv_fails = True
    code, _, events = box.apply()
    assert (code, events) == (1, ["-validate", "uv sync"])
    box.uv_fails = False
    code, _, events = box.apply()
    # Until a sync finishes every file of the app counts, the front's among them (Task 31): one that failed part-way
    # may have changed the environment under it. The front's restart waits for the quiet moment (Task 32).
    assert (code, events) == (0, ["-validate", *SYNCED, BEGIN, f"restart {BRAKE}", f"restart {GATE}", STATUS,
                                  QUIET_READ, f"restart {FRONT}", END])


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes anywhere")
def test_a_copy_that_fails_is_said_and_tried_again(box):
    box.deployed()
    put(box.repo / "spark/src/spark/brake.py", "v2", time.time())
    (box.app / "src/spark").chmod(0o555)
    try:
        code, lines, events = box.apply()
    finally:
        (box.app / "src/spark").chmod(0o755)
    assert (code, events) == (1, ["-validate"])
    assert lines[-1].startswith(f"apply: copying the app into {box.app} failed (")
    code, _, events = box.apply()
    assert (code, events) == (0, ["-validate", *SYNCED, BEGIN, f"restart {BRAKE}", f"restart {GATE}", STATUS,
                                  QUIET_READ, f"restart {FRONT}", END])  # every file counts until a sync finishes


def test_the_restarts_a_cut_off_run_owed_are_made_by_the_next(box):
    # I2: Ctrl-C after apply wrote llama-swap's config and the registry, before any restart (the brake's is the first,
    # since Task 31's order). Since Task 32 the Ctrl-C ends apply's hold, and apply exits 130.
    box.deployed(old={"llama-swap.yaml": "old config", "models.yaml": OLDER_REGISTRY})
    box.interrupted = {BRAKE}
    code, lines, events = box.apply()
    assert (code, events) == (130, ["-validate", *HELD, "write llama-swap.yaml", "write models.yaml", BEGIN,
                                    f"restart {BRAKE}", UNDRAIN])
    # The hold ends first, then the line (Task 32's review, I1: a terminal that hung up can't stop the hold's end).
    assert lines[-2:] == ["apply: the hold on new requests is ended", "apply: stopped by Ctrl-C after deploying the "
                          "files; the next `make apply` restarts each unit still running older ones"]
    assert box.units == dict.fromkeys(RUNNING_UNITS, T0) and (box.etc / "llama-swap.yaml").read_text() != "old config"
    box.interrupted = set()
    code, lines, events = box.apply()
    assert (code, events) == (0, ["-validate", *HELD, BEGIN, f"restart {BRAKE}", f"restart {GATE}", STATUS,
                                  QUIET_READ, f"restart {LLAMA}", STATUS, END])
    for unit in (BRAKE, GATE, LLAMA):
        assert f"apply: {unit} started before its latest files were in place, so it still runs older ones" in lines


def test_apply_says_when_the_brake_it_restarted_doesnt_stay_up(box):
    # Phase 1's council (reliability I1): the app fails once it runs, so the restart passes and the unit loops.
    box.deployed()
    put(box.repo / "spark/src/spark/brake.py", "v2", time.time())
    box.brake_shows = [UP, "ActiveState=activating\nSubState=auto-restart\nNRestarts=1\n"]
    code, lines, events = box.apply()
    assert (code, events) == (1, ["-validate", "uv sync", f"restart {BRAKE}", f"restart {GATE}", STATUS])
    assert lines[-1] == (f"apply: restarted {BRAKE}, but it isn't running 0 s later (activating, auto-restart): see "
                         "`make logs s=brake`")  # the Box gives it no time (STAY_UP_S)


def test_a_brake_that_started_before_the_apps_last_sync_is_restarted(box):
    # I2, for the app: the sync finished, and the run was cut off before the brake's restart.
    box.deployed()
    put(box.repo / "spark/src/spark/brake.py", "v2", time.time())
    box.interrupted = {BRAKE}
    assert box.apply()[0] == 130
    box.interrupted = set()
    code, _, events = box.apply()
    assert (code, events) == (0, ["-validate", f"restart {BRAKE}", f"restart {GATE}", STATUS])


def test_llama_swap_restarts_last_inside_the_hold_and_compose_after_it(box):
    # I3: nothing else between the second look and llama-swap's restart, not compose's restart of up to 15 minutes.
    # Phase 1 restarted llama-swap first; since Task 31 it goes last, after the brake and the gate, and the compose
    # unit restarts on its own after it. Since Task 32 the hold covers the four, and compose restarts after its end.
    box.deployed(old={"llama-swap.yaml": "old config", "models.yaml": OLDER_REGISTRY})
    os.utime(installed_path("compose/compose.yaml"), (T0 + 60, T0 + 60))  # installed after compose started
    code, _, events = box.apply()
    assert (code, events) == (0, ["-validate", *HELD, "write llama-swap.yaml", "write models.yaml", BEGIN,
                                  f"restart {BRAKE}", f"restart {GATE}", STATUS, QUIET_READ, f"restart {LLAMA}", STATUS, END,
                                  f"restart {COMPOSE}"])
    assert box.gate.routes("/v1/apply/begin")[0][2] == {"restarting": [BRAKE, GATE, LLAMA]}  # never compose


def test_apply_says_when_the_gate_doesnt_see_a_restarted_llama_swap_answer(box):
    # The plan's rule: every config change is followed by a start and llama-swap answering, not just -validate. Since
    # Task 32 the gate tells, within 30 s; then the hold ends with undrain-all, and apply exits 1.
    box.deployed(old={"llama-swap.yaml": "old config"})
    box.gate.llama_back_after = math.inf
    code, lines, events = box.apply()
    assert code == 1
    assert events == ["-validate", *HELD, "write llama-swap.yaml", BEGIN, QUIET_READ, f"restart {LLAMA}", *[STATUS] * 31, UNDRAIN]
    assert lines[-2:] == [f"apply: restarted {LLAMA}, but llama-swap didn't answer the gate within 30 s of its "
                          "restart: see `make logs s=llama-swap`", "apply: the hold on new requests is ended"]


@pytest.mark.parametrize("what, text", [
    ("registry", "budget: " + "[" * 100_000 + "]" * 100_000 + "\n"),
    ("registry", "budget: {allocatable_gib: 102}\n"),
    ("versions file", "components: {llama-swap: [\n"),
], ids=["a registry nested too deep", "a registry that isn't valid", "versions that aren't YAML"])
def test_a_file_apply_cant_load_is_named_not_a_traceback(box, what, text):
    # Minor 7: apply loads them as `spark render` does, and cli.main prints the refusal.
    path = box.repo / ("stack/models.yaml" if what == "registry" else "stack/versions.yaml")
    path.write_text(text)
    code, lines, events = box.apply()
    assert (code, events) == (1, [])
    assert lines[0].startswith(f"spark apply: the {what} {path} won't load: ")


def test_uv_sync_is_given_the_tests_own_environment_and_uvs_settings_only(box):
    # Task 7's fix round 2: apply hands uv sync its environment, and the fake it reaches raises when uv fails, so a
    # traceback would print it. It's the test's own (conftest.py's fake_environment, which keeps TZ when the run sets
    # one). Names only, taken first: pytest prints the arguments of a call in a failing assert.
    box.deployed()
    put(box.repo / "spark/src/spark/brake.py", "v2", time.time())
    box.apply()
    names = set(box.uv_env) - {"PYTEST_CURRENT_TEST"}
    # UV_LOCK_TIMEOUT: past apply's own bound, so uv never goes on without a lock before apply stops it (Task 35).
    assert names <= {"PATH", "HOME", "LANG", "TZ", "UV_PYTHON_INSTALL_DIR", "UV_LINK_MODE", "UV_LOCK_TIMEOUT"}


def test_validate_is_run_with_path_and_placeholder_keys_only(box, monkeypatch):
    # Minor 9, as run() runs it, in the test's own environment (conftest.py): PROBE_SECRET stands in for a secret of
    # Dan's, and the expectation is built from that fake, never the shell's.
    monkeypatch.setenv("PROBE_SECRET", "not for llama-swap")
    box.deployed()
    box.apply()
    assert box.validate_env == validation_env({"PATH": os.environ["PATH"]})
    box.validate_code = 1
    code, lines, events = box.apply()
    assert (code, events) == (1, ["-validate"])
    assert lines[-1] == "apply: llama-swap rejected the rendered config; nothing was changed"


def test_the_app_is_replaced_file_by_file_and_loses_what_spark_no_longer_has(box):
    # Minor 8: a file removed from spark/ leaves the app; each changed file is replaced whole; the venv stays.
    box.deployed()
    put(box.app / "src/spark/retired/old.py", "gone from spark/", OLD)
    put(box.repo / "spark/src/spark/brake.py", "v2", time.time())
    before = (box.app / "src/spark/brake.py").stat().st_ino
    code, _, events = box.apply()
    assert (code, events) == (0, ["-validate", "uv sync", f"restart {BRAKE}", f"restart {GATE}", STATUS])
    assert not (box.app / "src/spark/retired").exists()
    brake = box.app / "src/spark/brake.py"
    assert brake.read_text() == "v2" and brake.stat().st_ino != before
    assert (box.app / ".venv/bin/spark").exists() and (box.app / spark_apply.SYNC_STAMP).exists()


# Task 31: the diff, and what each change restarts. llama-swap's restart and the front's wait for the quiet moment (Task
# 32's wait, under apply's hold); the brake's and the gate's are made at once when nothing waits, and inside the hold
# when something does; the compose unit's on its own, as in Phase 1.


def plan(changed=(), app=(), *, old=REGISTRY, new=REGISTRY, outdated=(), restart_front=False,
         active=lambda unit: True):
    return plan_restarts(list(changed), list(app), old, new, list(outdated), restart_front=restart_front, active=active)


def registry_of(tmp_path, *edits):
    """The fixture registry with each (old, new) edit made, each of which must change it once, loaded."""
    text = (FIX / "models.yaml").read_text()
    for old, new in edits:
        assert text.count(old) == 1, old
        text = text.replace(old, new)
    path = tmp_path / "models.yaml"
    path.write_text(text)
    return load_registry(path)


def test_apply_shows_a_diff_of_what_it_would_change(tmp_path):
    etc = seeded(tmp_path / "config")
    (etc / "llama-swap.yaml").write_text("healthCheckTimeout: 600\nttl: 0\n")
    code, ran, logs, _ = run_apply(etc, FILES | {"llama-swap.yaml": "healthCheckTimeout: 180\nttl: 0\n"})
    assert code == 0 and ran == [["systemctl", "restart", LLAMA]]
    start = logs.index(f"--- deployed: {etc}/llama-swap.yaml")
    assert logs[start:start + 6] == [f"--- deployed: {etc}/llama-swap.yaml", "+++ rendered: llama-swap.yaml",
                                     "@@ -1,2 +1,2 @@", "-healthCheckTimeout: 600", "+healthCheckTimeout: 180",
                                     " ttl: 0"]
    assert [line for line in logs if line.startswith(("--- ", "+++ "))] == logs[start:start + 2]  # only what changes
    # A changed unit: root's installed copy against the copy apply stages for `make install-units`.
    unit = "systemd/local-ai-llama-swap.service"
    etc = seeded(tmp_path / "unit")
    code, ran, logs, _ = run_apply(etc, FILES | {unit: "[Service]\nRestartSec=5\n"},
                                   installed=ROOTS | {unit: "[Service]\nRestartSec=2\n"})
    assert code == 0 and ran == []
    assert logs[1:7] == ["--- installed: /etc/systemd/system/local-ai-llama-swap.service", f"+++ staged: {etc}/{unit}",
                         "@@ -1,2 +1,2 @@", " [Service]", "-RestartSec=2", "+RestartSec=5"]


def test_the_diff_shows_a_new_file_whole_and_escapes_control_characters(tmp_path):
    # A file with no copy yet diffs against nothing. A control character is shown escaped, as `make install-units`
    # refuses one, so no line of the diff can hide another on the terminal.
    (tmp_path / "models.yaml").write_text("m\n")  # unchanged: no diff
    files = {"llama-swap.yaml": "ttl: 0\n", "models.yaml": "m\n", "systemd/local-ai-gate.service": "a\x1b[2Kb\n"}
    text = diff_text(files, tmp_path, {"systemd/local-ai-gate.service": "a\r\n"})
    assert text.splitlines() == ["--- /dev/null", "+++ rendered: llama-swap.yaml", "@@ -0,0 +1 @@", "+ttl: 0",
                                 "--- installed: /etc/systemd/system/local-ai-gate.service",
                                 f"+++ staged: {tmp_path}/systemd/local-ai-gate.service", "@@ -1 +1 @@", "-a\\x0d",
                                 "+a\\x1b[2Kb"]
    assert diff_text({"models.yaml": "m\n"}, tmp_path, {}) == ""


def test_apply_names_each_file_of_the_app_it_changes(tmp_path):
    code, _, logs, _ = run_apply(seeded(tmp_path), FILES, app_changes=["src/spark/cli.py", "src/spark/gate/core.py"])
    assert code == 0 and logs[:2] == ["apply: changes app/src/spark/cli.py", "apply: changes app/src/spark/gate/core.py"]


def test_a_change_to_the_gates_code_restarts_the_gate_and_the_brake_at_once():
    assert plan(app=["src/spark/gate/admission.py"]) == RestartPlan(now=[BRAKE, GATE], after_quiet=[])


@pytest.mark.parametrize("rel", ["src/spark/leakcheck.py", "src/spark/gate/core.py", "src/spark/brake.py",
                                 "tests/test_front_forward.py"])
def test_a_change_elsewhere_in_the_app_never_restarts_the_front(rel):
    assert plan(app=[rel]) == RestartPlan(now=[BRAKE, GATE], after_quiet=[])


@pytest.mark.parametrize("rel", ["src/spark/front/parse.py", "src/spark/messages.py", "src/spark/front/__init__.py",
                                 "src/spark/__init__.py"])
def test_a_change_to_the_fronts_modules_restarts_it_after_the_quiet_moment(rel):
    assert plan(app=[rel]) == RestartPlan(now=[BRAKE, GATE], after_quiet=[FRONT])


@pytest.mark.parametrize("rel", ["uv.lock", "pyproject.toml", ".python-version"])
def test_a_change_to_the_apps_environment_restarts_the_front_too(rel):
    # uvicorn and httpx are code the front runs too: a bump changes it, and `uv sync` replaces them under it.
    assert plan(app=[rel]) == RestartPlan(now=[BRAKE, GATE], after_quiet=[FRONT])


def test_every_front_module_is_a_file_whose_change_restarts_the_front():
    from spark.front import FRONT_MODULES
    files = [path.relative_to(ROOT / "spark").as_posix() for path in (ROOT / "spark/src").rglob("*.py")]
    fronts = [rel for rel in files if spark_apply.front_file(rel)]
    assert sorted(spark_apply.module_of(rel) for rel in fronts) == sorted(FRONT_MODULES)


@pytest.mark.parametrize("rel", ["src/spark/cli.py", "src/spark/gateclient.py"])
def test_a_change_to_the_fronts_start_path_restarts_it(rel):
    # Task 31's review: the unit runs `spark front`, spark.cli's main and _front, the front's start and its exit; and
    # spark.cli imports spark.gateclient at module level.
    assert plan(app=[rel]) == RestartPlan(now=[BRAKE, GATE], after_quiet=[FRONT])


@pytest.mark.parametrize("edit", [
    pytest.param(("roles: [coder]", "roles: [coder, agentic]"), id="a role added"),
    pytest.param(("\n  coder:\n", "\n  big-coder:\n"), id="a model renamed"),
    pytest.param(("footprint_gib: 28", "footprint_gib: 30"), id="a footprint changed"),
])
def test_a_change_to_the_registrys_names_or_roles_never_restarts_the_front(tmp_path, edit):
    # They reach the front through the gate's events (Dan's decision, 2026-10-07, after the forward-and-back council).
    assert plan(["models.yaml"], new=registry_of(tmp_path, edit)) == RestartPlan(now=[BRAKE, GATE], after_quiet=[])


@pytest.mark.parametrize("edit", [("wait_s: 600", "wait_s: 900"), ("max_waiting: 8", "max_waiting: 6")],
                         ids=["a wait", "a cap"])
def test_a_change_to_the_registrys_key_groups_restarts_the_front_after_the_quiet_moment(tmp_path, edit):
    # The front reads the key groups only at start, for each key's wait and caps (spark.front.main).
    assert plan(["models.yaml"], new=registry_of(tmp_path, edit)) == RestartPlan(now=[BRAKE, GATE],
                                                                                 after_quiet=[FRONT])


def test_a_deployed_registry_that_wont_load_restarts_the_front_too():
    # One apply can't load (an older shape, say) may have had other key groups.
    assert plan(["models.yaml"], old=None) == RestartPlan(now=[BRAKE, GATE], after_quiet=[FRONT])
    assert plan([], old=None) == RestartPlan(now=[], after_quiet=[])  # unless its text didn't change


def test_llama_swaps_restart_waits_for_the_quiet_moment():
    assert plan(["llama-swap.yaml"]) == RestartPlan(now=[], after_quiet=[LLAMA])
    assert plan(outdated=[LLAMA]) == RestartPlan(now=[], after_quiet=[LLAMA])  # its unit, once root's copy is installed


def test_restart_front_waits_too():
    # `make restart-front`, after a change of the key digests or the key list, which apply doesn't deploy. The gate
    # reads the key list at start too (spark.gate.main), so it restarts with the front, before it.
    assert plan(restart_front=True) == RestartPlan(now=[GATE], after_quiet=[FRONT])


def test_a_unit_whose_new_definition_is_installed_restarts_as_its_files_would():
    assert plan(outdated=[FRONT, GATE, COMPOSE, LLAMA, BRAKE]) == RestartPlan(now=[BRAKE, GATE, COMPOSE],
                                                                              after_quiet=[FRONT, LLAMA])


def test_a_unit_that_isnt_running_is_never_restarted(tmp_path):
    def running(unit):
        return unit != FRONT

    assert plan(app=["src/spark/front/parse.py"], active=running) == RestartPlan(now=[BRAKE, GATE], after_quiet=[],
                                                                                 not_running=[FRONT])
    assert plan(restart_front=True, active=running) == RestartPlan(now=[GATE], after_quiet=[], not_running=[FRONT])
    # apply never starts it: it starts with its new files.
    code, ran, logs, _ = run_apply(seeded(tmp_path), FILES, app_changes=["src/spark/front/parse.py"],
                                   active=(LLAMA, BRAKE, COMPOSE, GATE))
    assert code == 0 and ran == [["systemctl", "restart", BRAKE], ["systemctl", "restart", GATE]]
    assert logs[-1] == f"apply: {FRONT} isn't running; it starts with the new config"


def test_restarts_follow_a_fixed_order():
    for units in itertools.permutations([LLAMA, FRONT, GATE, BRAKE]):
        assert restart_order(list(units)) == [BRAKE, GATE, FRONT, LLAMA]
    # The compose unit restarts on its own, after them, as in Phase 1: its start can take minutes.
    assert restart_order([COMPOSE, LLAMA, BRAKE]) == [BRAKE, LLAMA, COMPOSE]


def test_apply_restarts_in_that_order_inside_the_hold_and_compose_after_it(tmp_path):
    # Task 31's order; since Task 32 the first four inside apply's hold, from drain-all to apply/end, and compose after.
    events = []
    code, _, _, _ = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b", "models.yaml": "m2"},
                              outdated=[COMPOSE], app_changes=["src/spark/front/app.py"], events=events)
    # Task 32's review (C1): the front's counts read again under the hold just before each restart that cuts.
    assert code == 0 and events == [
        "gate GET /v1/quiet", "sync", "gate POST /v1/drain-all", "gate GET /v1/quiet", "gate POST /v1/apply/begin",
        f"restart {BRAKE}", f"restart {GATE}", "gate GET /v1/status", "gate GET /v1/quiet", f"restart {FRONT}",
        "gate GET /v1/quiet", f"restart {LLAMA}", "gate GET /v1/status", "gate POST /v1/apply/end",
        f"restart {COMPOSE}"]


# Task 31's interim rule, an after_quiet restart only with no model loaded or with --now, and its tests
# (test_until_the_quiet_wait_the_fronts_restart_waits_for_no_model_loaded_or_now,
# test_a_model_loaded_before_the_fronts_restart_holds_back_every_restart_that_waits), went with Task 32: the quiet wait
# and apply's hold replace it.


def test_the_gate_and_the_front_are_outdated_by_their_own_files():
    started = dict.fromkeys(RUNNING_UNITS, 100.0)
    assert outdated_units(started, {"models.yaml": 100.25}) == [BRAKE, GATE]
    assert outdated_units(started, {spark_apply.APP: 100.25}) == [BRAKE, GATE]
    assert outdated_units(started, {spark_apply.FRONT_APP: 100.25}) == [FRONT]
    assert outdated_units(started, {"systemd/local-ai-front.service": 100.25,
                                    "systemd/local-ai-gate.service": 100.25}) == [FRONT, GATE]
    assert outdated_units(started, {"systemd/local-ai-front.socket": 100.25}) == []  # apply restarts no socket


def test_the_fronts_files_are_dated_by_their_last_write(tmp_path):
    etc, app = tmp_path / "etc", tmp_path / "app"
    put(app / "src/spark/front/parse.py", "x", 200.0)
    put(app / "uv.lock", "x", 250.0)
    put(app / "src/spark/gate/core.py", "x", 400.0)  # not the front's
    put(app / ".venv/lib/python3.12/site-packages/uvicorn/main.py", "x", 500.0)  # uv's, not the app's
    assert spark_apply.deployed_times(etc, app) == {spark_apply.FRONT_APP: 250.0}


def test_run_restarts_the_front_for_its_own_modules_and_a_cut_off_run_leaves_it_owed(box):
    box.deployed()
    put(box.repo / "spark/src/spark/front/parse.py", "v2", time.time())
    box.interrupted = {FRONT}
    code, _, events = box.apply()
    assert (code, events) == (130, ["-validate", *SYNCED, BEGIN, f"restart {BRAKE}", f"restart {GATE}",
                                    STATUS, QUIET_READ, f"restart {FRONT}", UNDRAIN])
    box.interrupted = set()
    code, lines, events = box.apply()
    assert (code, events) == (0, ["-validate", *HELD, BEGIN, QUIET_READ, f"restart {FRONT}", END])
    assert f"apply: {FRONT} started before its latest files were in place, so it still runs older ones" in lines


@pytest.mark.parametrize("old", [OLDER_REGISTRY.replace("wait_s: 600", "wait_s: 900"), "an older shape: [\n"],
                         ids=["other key groups", "one that won't load"])
def test_run_restarts_the_front_for_the_deployed_registrys_key_groups(box, old):
    box.deployed(old={"models.yaml": old})
    code, _, events = box.apply()
    assert (code, events) == (0, ["-validate", *HELD, "write models.yaml", BEGIN, f"restart {BRAKE}",
                                  f"restart {GATE}", STATUS, QUIET_READ, f"restart {FRONT}", END])


def test_run_says_renders_warnings_before_its_diff(box, monkeypatch):
    # The controller's note at Task 5: only `spark render` and CI showed them.
    monkeypatch.setattr(spark_apply, "check_budget", lambda registry: ["every model loaded at once leaves 20 GiB"])
    box.deployed(old={"llama-swap.yaml": "old config\n"})
    code, lines, _ = box.apply("--dry-run")
    assert code == 0
    assert lines.index("apply: warning — every model loaded at once leaves 20 GiB") < lines.index(
        f"--- deployed: {box.etc}/llama-swap.yaml")


def test_make_restart_front_is_apply_restarting_the_front(box):
    makefile = (ROOT / "Makefile").read_text()
    assert re.search(r"^restart-front: ## On the Spark: .*\n\t\$\(SPARK\) apply --restart local-ai-front\.service$",
                     makefile, re.M)
    assert "restart-front" in re.search(r"^\.PHONY: (.*)$", makefile, re.M).group(1).split()
    # Through run(), with nothing else changed: the gate, then the front, both inside the hold (Task 32).
    box.deployed()
    code, _, events = box.apply("--restart", FRONT)
    assert (code, events) == (0, ["-validate", *HELD, BEGIN, f"restart {GATE}", STATUS, QUIET_READ, f"restart {FRONT}", END])
    with pytest.raises(SystemExit):
        cli.main(["apply", "--restart", BRAKE])  # the front's only


def test_restart_front_stages_roots_files_first_and_says_to_run_it_again(tmp_path):
    code, ran, logs, _ = run_apply(seeded(tmp_path), FILES | {"systemd/local-ai-llama-swap.service": "u2"},
                                   restart_front=True)
    assert code == 0 and ran == [] and logs[-1].endswith("then run `make restart-front` again")


def test_restart_front_waits_for_the_quiet_moment_and_its_steps_name_make_restart_front(tmp_path):
    # Task 31's review: no make target passes --now with --restart, and Task 31's interim refusal said to unload what's
    # loaded first. Since Task 32 `make restart-front` waits for the quiet moment as `make apply` does, and drains at
    # its deadline; so its words never offer `make apply-now`, which wouldn't restart the front for the key list.
    script, clock = GateScript(), FakeTime()
    script.quiet = [idle(20, "Gemma"), *(idle(s, "Gemma") for s in range(21, 61))]
    code, ran, logs, _ = run_apply(seeded(tmp_path), FILES, restart_front=True, script=script, clock=clock)
    assert (code, ran) == (0, [["systemctl", "restart", unit] for unit in (GATE, FRONT)])
    assert ("Waiting for a quiet moment: Gemma answered 20 s ago, and it needs 60 s with nothing in flight. Ctrl-C "
            "leaves everything as it was.") in logs
    assert not [line for line in logs if "apply-now" in line]
    # Declined at the deadline, and with the gate down: the step is `make restart-front` again.
    script = GateScript()
    script.quiet = [busy("agent")]
    code, ran, logs, _ = run_apply(seeded(tmp_path), FILES, restart_front=True, script=script, answers=[False])
    assert (code, ran) == (1, []) and logs[-1] == ("apply: nothing was changed; `make restart-front` waits for a quiet "
                                                   "minute again")
    script = GateScript()
    script.down = True
    code, ran, logs, _ = run_apply(seeded(tmp_path), FILES, restart_front=True, script=script)
    assert (code, ran) == (1, [])
    assert logs[-1] == ("apply: nothing was changed: apply can't see what's in flight while the gate isn't answering; "
                        "once it answers (`make doctor` shows what's wrong), `make restart-front` waits for a quiet "
                        "moment")
    # `make apply`'s points at `make apply-now`, which passes --now.
    code, _, logs, _ = run_apply(seeded(tmp_path), FILES, app_changes=["src/spark/front/app.py"], script=script)
    assert code == 1 and logs[-1].endswith("`make apply-now` restarts anyway")


def test_restart_fronts_failed_sync_says_to_run_it_again(box):
    # Task 31's review: `make apply` would sync the app and never restart the front for the key list. Since the
    # controller's ruling at Task 35 the sync comes before the hold, so a failed one takes none.
    box.deployed()
    put(box.repo / "spark/src/spark/brake.py", "v2", time.time())
    box.uv_fails = True
    code, lines, events = box.apply("--restart", FRONT)
    assert code == 1 and lines[-1].endswith("once it's fixed, `make restart-front` syncs it again")
    assert events == ["-validate", QUIET_READ, "uv sync"] and not box.gate.routes("/v1/drain-all")


# Task 31's review (I2): the front's restart for the registry's key groups, and the gate's and the front's for
# `--restart`, are recorded by a stamp apply touches just before it writes, so a run cut off before them leaves them
# owed to the next, as each file's time does.

KEY_GROUPS_CHANGED = OLDER_REGISTRY.replace("wait_s: 600", "wait_s: 900")


def stamps(box):
    return sorted(stamp for stamp in (spark_apply.KEY_GROUPS_STAMP, spark_apply.RESTART_FRONT_STAMP)
                  if (box.etc / stamp).exists())


def test_a_run_cut_off_before_the_fronts_restart_for_the_key_groups_leaves_it_owed(box):
    box.deployed(old={"models.yaml": KEY_GROUPS_CHANGED})
    box.interrupted = {FRONT}
    code, _, events = box.apply()
    assert (code, events) == (130, ["-validate", *HELD, "write models.yaml", BEGIN, f"restart {BRAKE}",
                                    f"restart {GATE}", STATUS, QUIET_READ, f"restart {FRONT}", UNDRAIN])
    assert box.units[FRONT] == T0  # cut off before systemd took the job
    box.interrupted = set()
    code, lines, events = box.apply()  # the registry is deployed now: only the stamp records the front's restart
    assert (code, events) == (0, ["-validate", *HELD, BEGIN, QUIET_READ, f"restart {FRONT}", END])
    assert f"apply: {FRONT} started before its latest files were in place, so it still runs older ones" in lines
    assert box.apply()[1] == ["apply: nothing to change"]


def test_a_run_cut_off_before_restart_fronts_restarts_leaves_the_gate_and_the_front_owed(box):
    box.deployed()
    box.interrupted = {GATE}
    code, _, events = box.apply("--restart", FRONT)
    assert (code, events) == (130, ["-validate", *HELD, BEGIN, f"restart {GATE}", UNDRAIN])
    box.interrupted = set()
    code, _, events = box.apply()  # `make apply`, not `make restart-front`
    assert (code, events) == (0, ["-validate", *HELD, BEGIN, f"restart {GATE}", STATUS, QUIET_READ, f"restart {FRONT}", END])
    assert box.apply()[1] == ["apply: nothing to change"]


def test_a_dry_run_a_refusal_and_the_staging_stop_leave_no_stamp(box):
    box.deployed(old={"models.yaml": KEY_GROUPS_CHANGED})
    for flags in ((), ("--restart", FRONT)):
        assert box.apply("--dry-run", *flags)[0] == 0 and stamps(box) == []
    box.gate.down = True  # since Task 32, the refusal: the gate isn't answering
    for flags in ((), ("--restart", FRONT)):
        assert box.apply(*flags)[0] == 1 and stamps(box) == []
    box.gate.down = False
    box.gate.quiet = [busy("agent")]  # and a no at the deadline
    for flags in ((), ("--restart", FRONT)):
        assert box.apply(*flags)[0] == 1 and stamps(box) == []
    box.gate.quiet = [QUIET]
    unit = Path(installed_path("systemd/local-ai-front.service"))
    unit.write_text(unit.read_text() + "# an older copy\n")  # root's copy differs: apply stages and stops
    for flags in ((), ("--restart", FRONT)):
        code, lines, _ = box.apply(*flags)
        assert code == 0 and "make install-units" in lines[-1] and stamps(box) == []
    unit.write_text(unit.read_text().removesuffix("# an older copy\n"))
    box.apply("--restart", FRONT)  # a run that writes touches both
    assert stamps(box) == sorted([spark_apply.KEY_GROUPS_STAMP, spark_apply.RESTART_FRONT_STAMP])


# Task 32: the quiet wait, drain now and apply-now.


def test_apply_waits_for_60s_with_nothing_in_flight_and_says_what_its_waiting_on(tmp_path):
    script, clock, said = GateScript(), FakeTime(), []
    script.clock = clock.clock
    script.quiet = [idle(20 + n) for n in range(41)]  # the coder answered 20 s ago; a reading each second
    assert wait_for_quiet(ScriptClient(script), clock=clock.clock, sleep=clock.sleep, say=said.append,
                          ask=lambda question: pytest.fail(question)) == "quiet"
    assert said == ["Waiting for a quiet moment: the coder answered 20 s ago, and it needs 60 s with nothing in "
                    "flight. Ctrl-C leaves everything as it was; `make apply-now` restarts now."]
    assert script.readings == 41 and clock.slept == [1.0] * 40  # quiet_for_s reached 60: it goes on
    assert [call[:2] for call in script.calls] == [("GET", "/v1/quiet")] * 41  # nothing but readings until then
    # Through apply_files: the sentence once, then the restart.
    script = GateScript()
    script.around(before=[idle(20 + n) for n in range(41)])
    code, ran, logs, _ = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b"}, script=script)
    assert code == 0 and ran == [["systemctl", "restart", LLAMA]]
    assert [line for line in logs if line.startswith("Waiting")] == said


def test_the_waiting_sentence_is_said_again_only_when_what_it_waits_on_changes():
    script, clock, said = GateScript(), FakeTime(), []
    script.clock = clock.clock
    script.quiet = [busy("agent"), busy("agent"), idle(1), idle(2), busy("pi on the Mac", label="Gemma"), idle(1, "Gemma"),
                    idle(2, "Gemma"), idle(1, "Gemma"), idle(2, "Gemma"), idle(0, None), idle(5, None), idle(60, None)]
    wait_for_quiet(ScriptClient(script), clock=clock.clock, sleep=clock.sleep, say=said.append, ask=lambda q: False)
    tail = ", and it needs 60 s with nothing in flight. Ctrl-C leaves everything as it was; `make apply-now` restarts now."
    assert said == ["Waiting for a quiet moment: the coder is answering now" + tail,
                    "Waiting for a quiet moment: the coder answered 1 s ago" + tail,
                    "Waiting for a quiet moment: Gemma is answering now" + tail,
                    "Waiting for a quiet moment: Gemma answered 1 s ago" + tail,
                    # a request came and went between two readings: a new quiet moment begins
                    "Waiting for a quiet moment: Gemma answered 1 s ago" + tail,
                    # the gate restarted, and has no count from the front yet; then the front's, with no request yet
                    "Waiting for a quiet moment: the gate doesn't have the front's counts yet, so it can't tell what's "
                    "in flight" + tail,
                    "Waiting for a quiet moment: nothing has been in flight since the front started 5 s ago" + tail]


def test_ctrl_c_during_the_wait_leaves_nothing_changed(tmp_path, monkeypatch):
    written = []
    monkeypatch.setattr(spark_apply, "write_tree", lambda files, out: written.append(sorted(files)))
    script = GateScript()
    script.quiet = [busy("agent"), busy("agent"), KeyboardInterrupt()]
    etc = seeded(tmp_path)
    code, ran, logs, synced = run_apply(etc, FILES | {"llama-swap.yaml": "b", "models.yaml": "m2"},
                                        app_changes=["src/spark/brake.py"], script=script)
    assert (code, ran, synced, written) == (130, [], [], [])
    assert [call[1] for call in script.calls] == ["/v1/quiet"] * 3  # no drain-all, so no hold to end
    assert (etc / "llama-swap.yaml").read_text() == "a" and logs[-1] == "apply: stopped by Ctrl-C; nothing was changed"


def test_after_15_minutes_apply_offers_drain_now(tmp_path):
    for answer in (True, False):
        script, clock = GateScript(), FakeTime()
        script.around(before=[busy("pi on the Mac", "agent")], after=[busy("agent"), busy("agent"), QUIET])
        events = []
        code, ran, logs, synced = run_apply(seeded(tmp_path / str(answer)), FILES | {"llama-swap.yaml": "b"},
                                            app_changes=["src/spark/brake.py"], script=script, clock=clock,
                                            answers=[answer], events=events)
        assert run_apply.asked == ["No quiet minute in 15 minutes. Drain now, holding new requests while the 2 in "
                                   "flight finish? [y/N]"]
        drains = script.routes("/v1/drain-all")
        if answer:  # drain-all at once, then on, with nothing in flight
            assert code == 0 and drains[0][3] == 1000.0 + 900
            # The sync just before drain-all (the controller's ruling at Task 35), then the drain's own wait.
            assert events[events.index("sync"):events.index("gate POST /v1/apply/begin")] == [
                "sync", "gate POST /v1/drain-all", *["gate GET /v1/quiet"] * 3]
            assert ran == [["systemctl", "restart", unit] for unit in (BRAKE, GATE, LLAMA)]
        else:  # exit 1, nothing changed
            assert (code, ran, synced, drains) == (1, [], [], [])
            assert (tmp_path / str(answer) / "llama-swap.yaml").read_text() == "a"
            assert logs[-1] == ("apply: nothing was changed; `make apply` waits for a quiet minute again, and `make "
                                "apply-now` restarts now")


def test_the_deadline_can_be_shortened_for_a_drill(box, capsys):
    box.deployed(old={"llama-swap.yaml": "old config"})
    box.gate.quiet = [busy("agent")]
    box.replies = [False]
    code, lines, _ = box.apply("--deadline-s", "60")
    assert code == 1 and box.questions == ["No quiet minute in 1 minute. Drain now, holding new requests while the 1 "
                                           "in flight finishes? [y/N]"]
    assert box.time.now - box.gate.calls[0][3] == 60  # asked at 60 s
    with pytest.raises(SystemExit):
        cli.main(["apply", "--help"])
    assert "deadline" not in capsys.readouterr().out  # `--help` doesn't name it


def test_drain_now_holds_new_requests_until_those_in_flight_finish(tmp_path):
    # After drain-all apply waits for /v1/quiet's inflight to empty, and says what it waits on, once per change.
    script, clock, events = GateScript(), FakeTime(), []
    script.around(before=[busy("pi on the Mac", "agent")],
                  after=[busy("pi on the Mac", "agent")] * 3 + [busy("agent")] * 2 + [idle(0.5)])
    code, _, logs, _ = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b"}, script=script, clock=clock,
                                 answers=[True], events=events)
    assert code == 0
    drained = events.index("gate POST /v1/drain-all")
    assert events[drained:events.index("gate POST /v1/apply/begin")] == ["gate POST /v1/drain-all",
                                                                          *["gate GET /v1/quiet"] * 6]
    assert [line for line in logs if line.startswith("Holding")] == [
        "Holding new requests while the 2 requests in flight finish (pi on the Mac, agent). Ctrl-C ends the hold and "
        "leaves everything as it was.",
        "Holding new requests while the 1 request in flight finishes (agent). Ctrl-C ends the hold and leaves "
        "everything as it was."]
    assert "apply: nothing is in flight, and new requests are held until the restarts are done" in logs
    # A failure after it: undrain-all, which ends the hold.
    script = GateScript()
    script.around(before=[busy("agent")])
    code, _, logs, _ = run_apply(seeded(tmp_path / "failed"), FILES | {"llama-swap.yaml": "b"}, fails=(LLAMA,),
                                 script=script, answers=[True])
    assert code == 1 and script.routes("/v1/undrain-all") and not script.routes("/v1/apply/end")
    assert logs[-1] == "apply: the hold on new requests is ended" and not script.held()


def test_apply_renews_the_hold_while_it_runs(tmp_path):
    script, clock = GateScript(), FakeTime()
    script.around(before=[busy("agent")], after=[busy("agent")] * 100 + [QUIET])  # a drain wait of 100 s
    code, _, _, _ = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b"}, script=script, clock=clock,
                              answers=[True])
    assert code == 0
    drained_at = script.routes("/v1/drain-all")[0][3]
    renewals = script.routes("/v1/apply/renew")
    # The thread's six; then the one apply makes itself just before llama-swap's restart (concern 3's ruling).
    assert [at - drained_at for _, _, _, at in renewals] == [15, 30, 45, 60, 75, 90, 100]
    assert all(body == {"since": drained_at} for _, _, body, _ in renewals)
    clock.sleep(60)  # after apply/end, none
    assert script.routes("/v1/apply/renew") == renewals
    order = [call[1] for call in script.calls]
    assert len(order) - 1 - order[::-1].index("/v1/apply/renew") < order.index("/v1/apply/end")
    assert not run_apply.gate._thread.is_alive()


def test_the_renewals_come_from_a_thread_of_their_own_so_a_long_step_never_lets_the_hold_lapse(tmp_path, monkeypatch):
    # With the real clock: a restart under the hold that holds apply's own thread for 0.5 s (the brake's here), renewals
    # every 0.05 s meanwhile. (A long sync was the case until the controller's ruling at Task 35 put the sync before
    # the hold.)
    script = GateScript()
    gate = ApplyGate(ScriptClient(script), ScriptClient(script), renew_s=0.05, log=lambda line: None)
    renewed_during = []

    def run_cmd(cmd):
        if cmd[-1] == BRAKE:
            before = len(script.routes("/v1/apply/renew"))
            time.sleep(0.5)
            renewed_during.append(len(script.routes("/v1/apply/renew")) - before)
        script.restarted(cmd[-1])

    code = apply_files(FILES | {"llama-swap.yaml": "b"}, seeded(tmp_path), installed=ROOTS, unreadable=set(),
                       outdated=[], active=lambda unit: True, app_changes=["src/spark/brake.py"], now_ok=False,
                       dry_run=False, sync_app=lambda: None, run_cmd=run_cmd, log=lambda line: None,
                       registry_old=REGISTRY, registry_new=REGISTRY, gate=gate, ask=lambda question: None)
    assert code == 0 and renewed_during[0] >= 4 and not gate._thread.is_alive()


def test_sighup_and_sigterm_clean_up_like_ctrl_c(tmp_path):
    # `spark apply` in a subprocess against the gate's stand-in: the quiet reading, drain-all, and then a request found
    # in flight (it came between the reading and the hold), so apply waits; the signal comes during that drain wait.
    for signum, code in ((signal.SIGHUP, 129), (signal.SIGTERM, 143)):
        folder = Path(tempfile.mkdtemp(prefix="ap", dir="/tmp"))
        script = GateScript()
        script.around(before=[QUIET], after=[busy("agent")])
        server = serve_script(script, folder / "control.sock")
        work = tmp_path / signum.name
        driver = (f"import sys\nsys.path.insert(0, {str(Path(__file__).parent)!r})\nimport pytest, test_apply\n"
                  f"from pathlib import Path\nfrom spark import cli\n"
                  f"box = test_apply.Box(Path({str(work)!r}), pytest.MonkeyPatch(), None, fake_time=False, "
                  f"gate_path=Path({str(folder / 'control.sock')!r}))\n"
                  "box.deployed(old={'llama-swap.yaml': 'old config'})\n"
                  "sys.exit(cli.main(['apply']))\n")
        process = subprocess.Popen([sys.executable, "-c", driver], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True)
        try:
            deadline = time.monotonic() + 60
            while not [call for call in script.calls[1:] if call[1] == "/v1/quiet" and script.routes("/v1/drain-all")]:
                assert process.poll() is None and time.monotonic() < deadline, process.communicate()[0]
                time.sleep(0.05)
            process.send_signal(signum)
            out, _ = process.communicate(timeout=60)
        finally:
            if process.poll() is None:
                process.kill()
            server.shutdown()
            server.server_close()
            shutil.rmtree(folder, ignore_errors=True)
        assert process.returncode == code, out
        assert "Holding new requests while the 1 request in flight finishes (agent)." in out  # the drain wait
        assert script.routes("/v1/undrain-all") and not script.routes("/v1/apply/begin"), out
        assert (work / "opt/etc/llama-swap.yaml").read_text() == "old config"  # nothing written
        assert f"apply: stopped by {signum.name}; nothing was changed" in out.splitlines()


def test_a_gate_that_fails_at_start_stops_apply(tmp_path):
    script, clock = GateScript(), FakeTime()
    script.gate_back_after = math.inf  # its socket doesn't answer after its restart
    code, ran, logs, _ = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b", "models.yaml": "m2"},
                                   app_changes=["src/spark/front/app.py"], script=script, clock=clock)
    assert code == 1 and ran == [["systemctl", "restart", unit] for unit in (BRAKE, GATE)]  # no further restart
    assert sum(clock.slept) >= 30
    assert logs[-2:] == [
        f"apply: restarted {GATE}, but the gate didn't answer on its control socket within 30 s: see `make logs s=gate`; "
        "apply restarts nothing more",
        "apply: the gate didn't answer undrain-all, so the hold on new requests ends a minute after its last renewal: "
        "the gate lets it lapse, or the front drops it once the gate has been gone that long"]


def test_the_quiet_path_holds_new_requests_too(tmp_path, monkeypatch):
    events = []
    monkeypatch.setattr(spark_apply, "write_tree", lambda files, out: events.append("write"))
    code, _, _, _ = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b"}, app_changes=["src/spark/brake.py"],
                              events=events)
    assert code == 0
    # drain-all before the files and the restarts. (Corrected 2026-10-15, the controller's ruling at Task 35: and after
    # the app's sync, which can wait on locks other accounts can hold, so it never runs with every request held.)
    assert events[:6] == ["gate GET /v1/quiet", "sync", "gate POST /v1/drain-all", "gate GET /v1/quiet", "write",
                          "gate POST /v1/apply/begin"]
    assert events.index("gate POST /v1/drain-all") < events.index(f"restart {BRAKE}")


def test_root_files_pending_stage_before_any_gate_call(tmp_path):
    # The cutover's Step 2: root's files pending, llama-swap active, the gate not answering.
    script = GateScript()
    script.down = True
    code, ran, logs, synced = run_apply(seeded(tmp_path), FILES | {"systemd/local-ai-llama-swap.service": "u2",
                                                                   "llama-swap.yaml": "b"},
                                        app_changes=["src/spark/front/app.py"], script=script)
    assert (code, ran, synced, script.calls) == (0, [], [], [])
    assert "make install-units" in logs[-1]
    run_apply(seeded(tmp_path), FILES | {"systemd/local-ai-llama-swap.service": "u2"}, dry_run=True, script=script)
    assert script.calls == []


def test_nothing_is_written_until_the_drain_is_done(tmp_path, monkeypatch):
    events = []
    monkeypatch.setattr(spark_apply, "write_tree", lambda files, out: events.append("write"))
    script = GateScript()
    script.around(before=[busy("agent")], after=[busy("agent"), busy("agent"), QUIET])
    code, _, _, _ = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b"}, app_changes=["src/spark/brake.py"],
                              script=script, answers=[True], events=events)
    assert code == 0
    waited = events[:events.index("gate POST /v1/apply/begin")]  # the reads just before the restarts come after
    last_reading = len(waited) - 1 - waited[::-1].index("gate GET /v1/quiet")
    assert events.index("write") > last_reading
    assert events.index("gate POST /v1/drain-all") < last_reading  # the drain's own wait came first
    # The app's sync after the quiet wait's last reading, and Dan's yes, but before the hold (the controller's ruling
    # at Task 35): a lock another account holds can then make it wait with no request held.
    quiet_wait = events[:events.index("gate POST /v1/drain-all")]
    assert quiet_wait[-1] == "sync" and quiet_wait[-2] == "gate GET /v1/quiet"


def test_apply_now_names_the_requests_it_would_cut_off_and_asks(tmp_path):
    for answer in (True, False):
        script, events = GateScript(), []
        script.quiet = [busy("pi on the Mac", "agent")]
        code, ran, logs, _ = run_apply(seeded(tmp_path / str(answer)), FILES | {"llama-swap.yaml": "b"}, now_ok=True,
                                       script=script, answers=[answer], events=events)
        assert run_apply.asked == ["This restarts the model service now and cuts off the 2 requests in flight (pi on "
                                   "the Mac, agent). Continue? [y/N]"]
        if answer:  # restarts, under the hold, without waiting for those in flight
            assert code == 0 and ran == [["systemctl", "restart", LLAMA]]
            assert events[:3] == ["gate GET /v1/quiet", "gate POST /v1/drain-all", "gate POST /v1/apply/begin"]
        else:  # nothing
            assert (code, ran, events) == (1, [], ["gate GET /v1/quiet"])
            assert logs[-1] == "apply: nothing was changed"
    # With nothing in flight it doesn't ask.
    code, ran, _, _ = run_apply(seeded(tmp_path / "idle"), FILES | {"llama-swap.yaml": "b"}, now_ok=True)
    assert (code, run_apply.asked) == (0, [])


def test_the_gate_is_told_before_and_after_the_restart(tmp_path):
    script, events = GateScript(), []
    script.llama_back_after = 2
    code, _, _, _ = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b"}, script=script, events=events)
    assert code == 0
    assert events[events.index("gate POST /v1/apply/begin"):] == [
        "gate POST /v1/apply/begin", "gate GET /v1/quiet", f"restart {LLAMA}", *["gate GET /v1/status"] * 3,
        "gate POST /v1/apply/end"]
    assert script.routes("/v1/apply/begin")[0][2] == {"restarting": [LLAMA]}


def test_the_restarts_run_in_their_fixed_order(tmp_path):
    events = []
    code, _, _, _ = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b", "models.yaml": "m2"},
                              app_changes=["src/spark/front/app.py"], events=events)
    assert code == 0
    assert [event for event in events if event.startswith("restart")] == [f"restart {unit}" for unit in
                                                                           (BRAKE, GATE, FRONT, LLAMA)]
    assert events.index("gate POST /v1/apply/begin") < events.index(f"restart {BRAKE}")
    between = events[events.index(f"restart {GATE}") + 1:events.index(f"restart {FRONT}")]
    # The gate's control socket answered between its restart and the front's; then, just before the front's, the
    # front's counts under the hold (Task 32's review, C1).
    assert between == ["gate GET /v1/status", "gate GET /v1/quiet"]


def test_a_front_whose_files_didnt_change_isnt_restarted(tmp_path):
    (tmp_path / "registry").mkdir()
    registry_new = registry_of(tmp_path / "registry", ("footprint_gib: 28", "footprint_gib: 30"))
    code, ran, _, _ = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b", "models.yaml": "m2"},
                                registry_new=registry_new)
    assert code == 0 and ran == [["systemctl", "restart", unit] for unit in (BRAKE, GATE, LLAMA)]


def test_a_change_needing_no_restart_applies_at_once(tmp_path):
    # Nothing waits for the quiet moment: no wait, no hold; the gate and the brake restarted at once, and the gate asked
    # only whether it answers after its restart.
    (tmp_path / "registry").mkdir()
    registry_new = registry_of(tmp_path / "registry", ("footprint_gib: 28", "footprint_gib: 30"))
    events = []
    code, ran, _, _ = run_apply(seeded(tmp_path), FILES | {"models.yaml": "m2"}, registry_new=registry_new,
                                events=events)
    assert code == 0 and ran == [["systemctl", "restart", unit] for unit in (BRAKE, GATE)]
    assert events == [f"restart {BRAKE}", f"restart {GATE}", "gate GET /v1/status"]


def test_with_the_gate_down_apply_restarts_llama_swap_only_with_now(tmp_path):
    script = GateScript()
    script.down = True
    code, ran, logs, synced = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b"}, script=script)
    assert (code, ran, synced) == (1, [], []) and (tmp_path / "llama-swap.yaml").read_text() == "a"
    assert logs[-1] == ("apply: nothing was changed: apply can't see what's in flight while the gate isn't answering; "
                        "`make apply-now` restarts anyway")
    code, ran, logs, _ = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b"}, script=script, now_ok=True)
    assert code == 1 and ran == [["systemctl", "restart", LLAMA]]  # restarted, but not confirmed
    assert ("apply: the gate isn't answering, so apply can't hold new requests; with --now it restarts anyway, and any "
            "request in flight is cut off") in logs


def test_with_llama_swap_stopped_apply_deploys_without_the_gate(tmp_path):
    # The cutover's Step 4: llama-swap stopped, the front and the gate not started yet, the gate not answering.
    script = GateScript()
    script.down = True
    code, ran, logs, synced = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b", "models.yaml": "m2"},
                                        app_changes=["src/spark/front/app.py"], active=(COMPOSE,), script=script)
    assert (code, ran, synced, script.calls) == (0, [], [True], [])
    assert (tmp_path / "llama-swap.yaml").read_text() == "b" and (tmp_path / "models.yaml").read_text() == "m2"


def test_a_front_that_must_restart_with_llama_swap_stopped_still_waits_while_the_gate_answers(tmp_path):
    # The front holds requests waiting for admission even while llama-swap is stopped: the gate is asked.
    script = GateScript()
    script.quiet = [busy("agent"), QUIET]
    code, ran, _, _ = run_apply(seeded(tmp_path), FILES, app_changes=["src/spark/front/app.py"],
                                active=(BRAKE, COMPOSE, FRONT, GATE), script=script)
    assert code == 0 and ran == [["systemctl", "restart", unit] for unit in (BRAKE, GATE, FRONT)]
    assert script.routes("/v1/drain-all")
    # And with the gate down too, it deploys and restarts anyway: nothing can be in flight to llama-swap.
    script = GateScript()
    script.down = True
    code, ran, logs, _ = run_apply(seeded(tmp_path), FILES, app_changes=["src/spark/front/app.py"],
                                   active=(BRAKE, COMPOSE, FRONT, GATE), script=script, clock=FakeTime())
    assert ran[:2] == [["systemctl", "restart", unit] for unit in (BRAKE, GATE)]


def test_apply_end_and_undrain_all_wait_past_the_gates_own_bound(box, monkeypatch):
    made = []
    real = spark_apply.GateClient

    def client(path, timeout_s):
        made.append(timeout_s)
        return real(path, timeout_s)

    monkeypatch.setattr(spark_apply, "GateClient", client)
    box.deployed(old={"llama-swap.yaml": "old config"})
    assert box.apply()[0] == 0
    assert sorted(made) == [gateproto.REQUEST_TIMEOUT_S, spark_apply.END_TIMEOUT_S]
    assert spark_apply.END_TIMEOUT_S > gateproto.APPLY_END_TIMEOUT_S


@pytest.mark.parametrize("failing", [False, True], ids=["apply/end", "undrain-all"])
def test_a_503_from_the_holds_end_is_said_and_never_counted_a_failure(box, failing):
    # gate.app.STILL_FINISHING: the gate is still ending the hold, past its 15 s bound, and its work goes on.
    box.deployed(old={"llama-swap.yaml": "old config"})
    box.gate.end_status = 503
    if failing:
        box.failing = {LLAMA}
    code, lines, events = box.apply()
    assert events[-1] == (UNDRAIN if failing else END)
    assert code == (1 if failing else 0)  # only the failed restart counts
    assert lines[-1] == ("apply: the restarts are done, and the gate is ending its hold on new requests. "
                         if not failing else "apply: the gate is ending its hold on new requests. ") + STILL_FINISHING
    assert "`spark status`" in lines[-1]


def test_a_hold_the_gate_ended_before_begin_restarts_nothing(tmp_path):
    script = GateScript()
    script.begin_answer = {"ended": True}
    code, ran, logs, _ = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b"}, script=script)
    assert (code, ran) == (1, []) and (tmp_path / "llama-swap.yaml").read_text() == "b"
    assert ("apply: the gate's hold on new requests had already ended, so apply restarted nothing; the files are "
            "deployed, and the next `make apply` restarts each unit still running older ones") in logs


def test_the_makefiles_help_says_apply_and_apply_now_wait_for_and_cut_off_what(tmp_path):
    makefile = (ROOT / "Makefile").read_text()
    apply_help = re.search(r"^apply: ## (.*)$", makefile, re.M).group(1)
    now_help = re.search(r"^apply-now: ## (.*)$", makefile, re.M).group(1)
    assert "llama-swap and the front" in apply_help and "quiet minute" in apply_help
    assert "llama-swap and the front" in now_help and "in flight" in now_help
    # Task 32's review: M1, apply-now asks only when the gate lists what's in flight, and with the gate down it can't;
    # M2, restart-front waits as apply does.
    assert "when the gate lists requests in flight" in now_help and "with the gate down" in now_help
    front_help = re.search(r"^restart-front: ## (.*)$", makefile, re.M).group(1)
    assert "quiet minute" in front_help


def test_a_gate_that_goes_away_during_the_wait_is_waited_for_then_changes_nothing(tmp_path):
    # The gate restarted mid-wait: said once, asked again each second, and the wait goes on once it answers.
    script, clock = GateScript(), FakeTime()
    script.around(before=[busy("agent"), Down(), Down(), idle(70)])
    code, ran, logs, _ = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b"}, script=script, clock=clock)
    assert code == 0 and ran == [["systemctl", "restart", LLAMA]]
    assert len([line for line in logs if line.endswith("It asks again each second, for up to 30 s.")]) == 1
    assert "apply: the gate answers again." in logs
    # Gone 30 s: nothing was changed, and no hold was made.
    script, clock = GateScript(), FakeTime()
    script.around(before=[busy("agent"), Down()])
    code, ran, logs, synced = run_apply(seeded(tmp_path / "gone"), FILES | {"llama-swap.yaml": "b"}, script=script,
                                        clock=clock)
    assert (code, ran, synced, script.routes("/v1/drain-all")) == (1, [], [], [])
    assert logs[-1].startswith("apply: nothing was changed: The gate didn't answer") and sum(clock.slept) == 31
    # Gone under the hold, in the drain wait: the same, and undrain-all ends the hold once the gate answers it.
    script, clock = GateScript(), FakeTime()
    script.around(before=[QUIET], after=[busy("agent"), Down()])
    code, ran, logs, _ = run_apply(seeded(tmp_path / "held"), FILES | {"llama-swap.yaml": "b"}, script=script,
                                   clock=clock)
    assert (code, ran) == (1, []) and script.routes("/v1/undrain-all") and not script.held()
    assert logs[-2].startswith("apply: nothing was changed: The gate didn't answer")


def test_a_gate_that_refuses_apply_changes_nothing_and_says_why(tmp_path):
    script = GateScript()
    script.quiet = [GateRefused(403, {"message": "Only spark-admin's members can ask that.", "code": None})]
    code, ran, logs, _ = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b"}, script=script)
    assert (code, ran) == (1, []) and logs[-1] == "apply: nothing was changed: Only spark-admin's members can ask that."
    script = GateScript()
    script.quiet = [{"quiet_for_s": "soon"}]  # an answer apply can't read
    code, _, logs, _ = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b"}, script=script)
    assert code == 1 and logs[-1].startswith("apply: nothing was changed: The gate's answer to GET /v1/quiet couldn't")


def test_the_drain_wait_never_takes_a_reading_without_the_fronts_counts_for_nothing_in_flight():
    # quiet_for_s 0 with nothing listed: the gate has no count from the front (gate/core.py's quiet, after its restart).
    script, clock, said = GateScript(), FakeTime(), []
    script.clock = clock.clock
    script.hold = {"since": clock.clock(), "ended": False, "restarting": []}  # drain-all taken: the front holds
    script.quiet = [idle(0), idle(0), idle(0.5)]
    assert wait_for_drained(ScriptClient(script), after=clock.clock(), clock=clock.clock, sleep=clock.sleep,
                            say=said.append) == "waited"
    assert script.readings == 3 and said == [
        "Waiting for the front to confirm that it holds new requests, with its counts. Ctrl-C ends the hold and leaves "
        "everything as it was."]


def test_a_hold_the_gate_ended_meanwhile_stops_the_restarts_that_would_cut_requests_off(tmp_path):
    # A renewal answered {ended: true}: the hold lapsed, or ended some other way, and requests flow again.
    for when in ("sync", GATE):
        script, logs, ran = GateScript(), [], []
        gate = apply_gate(script, FakeTime(), logs)

        def run_cmd(cmd):
            ran.append(cmd[-1])
            script.restarted(cmd[-1])
            if cmd[-1] == when:
                gate.ended.set()  # as the renewal thread sets it on {ended: true}

        code = apply_files(FILES | {"llama-swap.yaml": "b"}, seeded(tmp_path / when), installed=ROOTS,
                           unreadable=set(), outdated=[], active=lambda unit: True,
                           app_changes=["src/spark/front/app.py"], now_ok=False, dry_run=False,
                           sync_app=lambda: gate.ended.set() if when == "sync" else None, run_cmd=run_cmd,
                           log=logs.append, registry_old=REGISTRY, registry_new=REGISTRY, gate=gate,
                           ask=lambda question: None)
        assert code == 1 and script.routes("/v1/undrain-all") and not script.routes("/v1/apply/end")
        if when == "sync":  # before begin: nothing restarted
            assert ran == [] and not script.routes("/v1/apply/begin")
            assert ("apply: the gate's hold on new requests had already ended, so apply restarted nothing; the files "
                    "are deployed, and the next `make apply` restarts each unit still running older ones") in logs
        else:  # before the front's restart: the brake and the gate only
            assert ran == [BRAKE, GATE]
            assert logs[-2:] == [ENDED_BEFORE.format(unit=FRONT), "apply: the hold on new requests is ended"]


# Concern 3's ruling (2026-10-15): just before each restart under the hold, apply asks the gate itself.
ENDED_BEFORE = ("apply: the gate's hold on new requests has ended (it lapsed, or something else ended it), and requests "
                "flow again, which a restart now would cut off; so apply restarts nothing more, {unit} included. Its "
                "files are deployed: run `make apply` again, and it waits for a quiet moment, then restarts each unit "
                "still running older ones")


@pytest.mark.parametrize("ended_at, stopped_before", [(BRAKE, GATE), (GATE, FRONT), (FRONT, LLAMA)])
def test_a_hold_the_gate_ended_since_the_last_renewal_is_never_followed_by_a_restart(tmp_path, ended_at, stopped_before):
    # The gate ends the hold during the restart before (between renewals: no renewal comes in between).
    script = GateScript()
    script.end_hold_at = ended_at
    code, ran, logs, _ = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b", "models.yaml": "m2"},
                                   app_changes=["src/spark/front/app.py"], script=script)
    order = [BRAKE, GATE, FRONT, LLAMA]
    assert code == 1 and ran == [["systemctl", "restart", unit] for unit in order[:order.index(stopped_before)]]
    assert ENDED_BEFORE.format(unit=stopped_before) in logs
    assert script.routes("/v1/apply/renew")  # it asked, just before
    assert not script.routes("/v1/apply/end")


@pytest.mark.parametrize("how", ["unanswered", "refused"])
def test_a_renewal_that_fails_just_before_a_restart_means_nothing_is_restarted(tmp_path, how):
    script = GateScript()
    if how == "unanswered":
        script.down_at = BRAKE  # the gate stops answering after the brake's restart
    else:
        script.renew_status = 500
    code, ran, logs, _ = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b", "models.yaml": "m2"},
                                   app_changes=["src/spark/front/app.py"], script=script)
    assert code == 1 and ran == [["systemctl", "restart", BRAKE]]  # not the gate, the front or llama-swap
    said = next(line for line in logs if "so apply restarts nothing more" in line)
    assert said.startswith("apply: the gate didn't confirm that its hold on new requests still stands (")
    assert said.endswith(f"); so apply restarts nothing more, {GATE} included. Its files are deployed: once the gate "
                         "answers (`make doctor` shows what's wrong), run `make apply` again, and it waits for a quiet "
                         "moment, then restarts each unit still running older ones")


# Concern 4's ruling, and the review's C1 and M6 (2026-10-15): the gate's /v1/quiet says whether the front's call is open,
# so a front that isn't reporting its counts (it crashed, say), whose last snapshot the gate keeps, is said at once.
FRONT_SILENT = ("Waiting for a quiet moment: the front isn't reporting its counts (`make logs s=front` shows why), so "
                "apply can't tell what's in flight, and it needs 60 s with nothing in flight. Ctrl-C leaves everything "
                "as it was; `make apply-now` restarts now.")
FRONT_GONE = ("The front isn't connected to the gate (`make logs s=front` shows why), so it holds no new requests, and "
              "apply can't tell what's in flight; it waits until the front is back and holding. Ctrl-C ends the hold "
              "and leaves everything as it was.")
NOT_YET = ("Waiting for the front to confirm that it holds new requests, with its counts. Ctrl-C ends the hold and "
           "leaves everything as it was.")


def test_the_wait_says_when_the_front_isnt_reporting_its_counts():
    script, clock, said = GateScript(), FakeTime(), []
    script.clock = clock.clock
    # A stale snapshot: idle for long enough, but the front's call is down. Never quiet, and said at once.
    script.quiet = [idle(70, front_connected=False)] * 5 + [busy("agent")] * 2 + [idle(60)]
    assert wait_for_quiet(ScriptClient(script), clock=clock.clock, sleep=clock.sleep, say=said.append,
                          ask=lambda question: False) == "quiet"
    assert said == [FRONT_SILENT, "Waiting for a quiet moment: the coder is answering now, and it needs 60 s with "
                                  "nothing in flight. Ctrl-C leaves everything as it was; `make apply-now` restarts now."]
    assert [call[1] for call in script.calls] == ["/v1/quiet"] * 8  # nothing else asked


def test_the_drain_wait_never_says_holding_while_the_front_isnt_connected():
    # M6: with the front's call down, the gate's hold_all never reached it, so nothing is held.
    script, clock, said = GateScript(), FakeTime(), []
    script.clock = clock.clock
    script.hold = {"since": clock.clock(), "ended": False, "restarting": []}  # drain-all taken at the gate
    script.quiet = [busy("agent", front_connected=False)] * 3 + [busy("agent")] + [idle(1)]
    assert wait_for_drained(ScriptClient(script), after=clock.clock(), clock=clock.clock, sleep=clock.sleep,
                            say=said.append, ask=lambda question: False) == "waited"
    assert said == [FRONT_GONE, "Holding new requests while the 1 request in flight finishes (agent). Ctrl-C ends the "
                                "hold and leaves everything as it was."]


# Task 32's review, C1 (2026-10-15): "nothing in flight" only from a snapshot the front made while holding new requests,
# its call open, made since apply's drain-all; and read again just before the front's and llama-swap's restarts.

def test_a_lagging_reading_after_drain_all_is_waited_for(tmp_path):
    # The reviewer's gap: a request let through between the quiet reading and the front's hold shows one reading late.
    # The reading at once after drain-all is still the front's last snapshot from before it held: idle, not holding.
    script, clock, events = GateScript(), FakeTime(), []
    script.around(before=[QUIET], after=[idle(121, holding=False, snapshot_at=999.0), busy("agent"), idle(1)])
    code, ran, logs, _ = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b"}, script=script, clock=clock,
                                   events=events)
    assert code == 0 and ran == [["systemctl", "restart", LLAMA]]
    drained = events.index("gate POST /v1/drain-all")
    assert events[drained:events.index("gate POST /v1/apply/begin")] == ["gate POST /v1/drain-all",
                                                                          *["gate GET /v1/quiet"] * 3]
    assert [line for line in logs if line.startswith(("Waiting for the front", "Holding"))] == [
        NOT_YET, "Holding new requests while the 1 request in flight finishes (agent). Ctrl-C ends the hold and leaves "
                 "everything as it was."]


def test_a_reading_made_before_drain_all_is_never_taken_for_drained(tmp_path):
    # Holding, idle and connected, but made before the gate took this drain-all (a hold of before, say).
    script, clock = GateScript(), FakeTime()
    script.around(before=[QUIET], after=[idle(121, snapshot_at=999.0), idle(122)])
    code, ran, logs, _ = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b"}, script=script, clock=clock)
    assert code == 0 and NOT_YET in logs and script.readings == 4  # the last just before llama-swap's restart


def test_a_front_whose_call_is_down_is_never_taken_for_quiet_or_drained(tmp_path):
    # C1.2: the gate keeps a disconnected front's last snapshot, idle; the front serves what's loaded meanwhile.
    for answer in (False, True):
        script, clock, events = GateScript(), FakeTime(), []
        script.front_connected = False
        # Its last snapshot, stale: two requests long gone, and it says nothing of any since.
        script.around(before=[busy("agent", "pi on the Mac")],
                      after=[idle(301)] * 3 + [idle(302, front_connected=True, holding=True)])
        code, ran, logs, synced = run_apply(seeded(tmp_path / str(answer)), FILES | {"llama-swap.yaml": "b"},
                                            app_changes=["src/spark/brake.py"], script=script, clock=clock,
                                            answers=[answer], events=events)
        # M6: the question counts nothing from a stale snapshot.
        assert run_apply.asked == ["No quiet minute in 15 minutes. Drain now, holding new requests? [y/N]"]
        assert FRONT_SILENT in logs
        if not answer:
            assert (code, ran, synced) == (1, [], [])
        else:  # drain-all, then a wait until the front is back and holding: no restart before
            assert FRONT_GONE in logs and not any(line.startswith("Holding new requests") for line in logs)
            assert code == 0 and events.index(f"restart {BRAKE}") > events.index("gate POST /v1/drain-all") + 4
            assert events.index("sync") == events.index("gate POST /v1/drain-all") - 1  # before the hold (Task 35)


def test_a_front_that_reports_idle_but_not_holding_is_never_taken_for_drained(tmp_path):
    script, clock = GateScript(), FakeTime()
    script.around(before=[QUIET], after=[idle(200, holding=False)])  # its hold never comes
    code, ran, logs, synced = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b"},
                                        app_changes=["src/spark/brake.py"], script=script, clock=clock,
                                        answers=[False])
    # M5: on the quiet path too, the drain wait asks at the deadline; a no ends the hold. Since the controller's ruling
    # at Task 35 the app's sync came before the hold, so the line says it is synced, and nothing else changed.
    assert (code, ran, synced) == (1, [], [True])
    # The re-review's N2: a quiet minute was found and new requests are held, so the question is the drain wait's own.
    assert run_apply.asked == [NOT_CONFIRMED.format(no="nothing is changed")]
    assert script.routes("/v1/undrain-all") and not script.held()
    assert logs[-2:] == ["apply: the app is synced, but nothing else was deployed or restarted; `make apply` waits "
                         "for a quiet minute again, and `make apply-now` restarts now",
                         "apply: the hold on new requests is ended"]


def test_the_quiet_paths_drain_wait_asks_at_the_deadline(tmp_path):
    # M5: a request that slipped in and won't finish holds every new request only with Dan's say.
    script, clock = GateScript(), FakeTime()
    script.around(before=[QUIET], after=[busy("agent", "pi on the Mac")] * 901 + [idle(1)])
    code, ran, _, _ = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b"}, script=script, clock=clock,
                                answers=[True])
    assert code == 0 and ran == [["systemctl", "restart", LLAMA]]
    assert run_apply.asked == ["Requests are still in flight after 15 minutes: the 2 requests in flight (agent, pi on "
                               "the Mac). Keep holding new requests until they finish? No ends the hold, and nothing "
                               "is changed. [y/N]"]


def test_just_before_the_fronts_and_llama_swaps_restarts_apply_reads_again_under_the_hold(tmp_path):
    # After the gate's restart the front is found not holding (as after a gate that started fresh and released it):
    # the front's restart waits until it holds again with nothing in flight.
    script, clock, events = GateScript(), FakeTime(), []

    def reading(n, held):
        if script.calls and any(call[1] == "/v1/status" for call in script.calls) and n < 6:
            return busy("agent", holding=False)  # after the gate's restart, for a few readings
        return QUIET

    script.quiet = reading
    code, ran, logs, _ = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b", "models.yaml": "m2"},
                                   app_changes=["src/spark/front/app.py"], script=script, clock=clock, events=events)
    assert code == 0 and [cmd[-1] for cmd in ran] == [BRAKE, GATE, FRONT, LLAMA]
    gate_up = events.index("gate GET /v1/status")
    before_front = events[gate_up + 1:events.index(f"restart {FRONT}")]
    assert before_front[0] == "gate GET /v1/quiet" and len(before_front) > 1  # it waited, reading each second
    assert events[events.index(f"restart {FRONT}") + 1] == "gate GET /v1/quiet"  # and again before llama-swap's
    assert NOT_YET in logs


def test_with_now_nothing_is_read_again_before_the_restarts(tmp_path):
    # --now is Dan's say: what's in flight is cut off, and apply doesn't wait for it.
    script, events = GateScript(), []
    script.quiet = [busy("agent"), idle(1)]  # a second reading would find nothing in flight, and none is taken
    code, ran, _, _ = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b"}, now_ok=True, script=script,
                                answers=[True], events=events)
    assert code == 0 and events.count("gate GET /v1/quiet") == 1


def test_a_hold_the_gate_re_created_is_never_followed_by_a_restart(tmp_path):
    # M7: the gate restarted fresh from a damaged state file and lost the hold; its hello released the front's held
    # requests. The renewal just before the front's restart re-creates it, and says so.
    script = GateScript()
    script.lose_hold_at = GATE
    code, ran, logs, _ = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b", "models.yaml": "m2"},
                                   app_changes=["src/spark/front/app.py"], script=script)
    assert code == 1 and [cmd[-1] for cmd in ran] == [BRAKE, GATE]
    assert ("apply: the gate had lost its hold on new requests and re-created it (it started fresh from a damaged "
            "state file), so it may have let requests through meanwhile; so apply restarts nothing more, "
            f"{FRONT} included. Its files are deployed: run `make apply` again, and it waits for a quiet moment, then "
            "restarts each unit still running older ones") in logs


@pytest.mark.parametrize("sent", [False, True], ids=["never reached the gate", "its answer lost"])
def test_a_drain_all_that_didnt_answer_is_ended_only_if_it_may_have_made_a_hold(tmp_path, sent):
    # M3: GateUnavailable's `sent` tells a call that never got there from one whose answer was lost.
    script = GateScript()
    script.drain_down = Down(sent=sent)
    code, ran, logs, synced = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b"}, script=script)
    assert (code, ran, synced) == (1, [], [])
    assert bool(script.routes("/v1/undrain-all")) is sent
    if not sent:
        assert logs[-1].startswith("apply: nothing was changed: The gate didn't answer")
        assert not any("hold" in line for line in logs if line.startswith("apply: the"))
    else:
        assert logs[-1] == "apply: the hold on new requests is ended"


def test_a_dropped_ssh_session_still_ends_the_hold(tmp_path):
    # I1: on a dropped SSH session the terminal hangs up before SIGHUP comes, and every write to it fails (EIO). apply's
    # stdout and stderr on a pty whose master is closed before the signal: undrain-all is still posted, and the exit
    # is 129.
    import pty

    folder = Path(tempfile.mkdtemp(prefix="ap", dir="/tmp"))
    script = GateScript()
    script.around(before=[QUIET], after=[busy("agent")])
    server = serve_script(script, folder / "control.sock")
    work = tmp_path / "hup"
    driver = (f"import sys\nsys.path.insert(0, {str(Path(__file__).parent)!r})\nimport pytest, test_apply\n"
              f"from pathlib import Path\nfrom spark import cli\n"
              f"box = test_apply.Box(Path({str(work)!r}), pytest.MonkeyPatch(), None, fake_time=False, "
              f"gate_path=Path({str(folder / 'control.sock')!r}))\n"
              "box.deployed(old={'llama-swap.yaml': 'old config'})\n"
              "sys.exit(cli.main(['apply']))\n")
    master, slave = pty.openpty()
    process = subprocess.Popen([sys.executable, "-c", driver], stdout=slave, stderr=slave, stdin=subprocess.DEVNULL)
    os.close(slave)
    try:
        deadline = time.monotonic() + 60
        while not [call for call in script.calls[1:] if call[1] == "/v1/quiet" and script.routes("/v1/drain-all")]:
            assert process.poll() is None and time.monotonic() < deadline
            try:  # keep the pty's buffer drained while the terminal is up
                while select.select([master], [], [], 0)[0] and os.read(master, 65536):
                    pass
            except OSError:
                pass
            time.sleep(0.05)
        os.close(master)  # the terminal hangs up
        master = None
        time.sleep(0.2)
        process.send_signal(signal.SIGHUP)
        assert process.wait(timeout=60) == 129
    finally:
        if master is not None:
            os.close(master)
        if process.poll() is None:
            process.kill()
        server.shutdown()
        server.server_close()
        shutil.rmtree(folder, ignore_errors=True)
    assert script.routes("/v1/undrain-all") and not script.routes("/v1/apply/begin")
    assert (work / "opt/etc/llama-swap.yaml").read_text() == "old config"


# Task 32's re-review (2026-10-15): N1 at apply's end, N2, N5, N8 and the refused drain-all.
NOT_CONFIRMED = ("The front hasn't confirmed in 15 minutes that it holds new requests, so apply can't tell what's in "
                 "flight. Keep waiting? No ends the hold, and {no}. [y/N]")


def test_a_read_on_a_reopened_call_before_the_fronts_first_post_restarts_nothing(tmp_path):
    # N1: the front's call drops and opens again just before llama-swap's restart. The gate still holds the old idle,
    # holding snapshot, made since drain-all, but reports `holding` false until the front posts on the new call
    # (test_gate_core's test_a_reopened_call_holds_nothing_until_the_front_reports_on_it). apply waits for that post.
    script, clock, events = GateScript(), FakeTime(), []
    readings = {"n": 0}

    def reading(n, held):
        if held and any(event == "gate POST /v1/apply/begin" for event in events):
            readings["n"] += 1
            if readings["n"] <= 3:  # the reopened call, before the front's first post on it
                return idle(130, holding=False)
        return QUIET

    script.quiet = reading
    code, ran, _, _ = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b"}, script=script, clock=clock,
                                events=events)
    assert code == 0 and ran == [["systemctl", "restart", LLAMA]]
    begun = events.index("gate POST /v1/apply/begin")
    # Three before the front's post on the new call, then holding; the hold confirmed again after the wait, and read.
    assert events[begun:events.index(f"restart {LLAMA}")] == ["gate POST /v1/apply/begin", *["gate GET /v1/quiet"] * 5]


def test_the_question_before_a_restart_waits_for_the_fronts_post_and_says_what_a_no_does(tmp_path):
    # N2: the deadline is long past (the quiet minute came at 14:59), and the gate has just restarted: it has no
    # snapshot yet, so its readings say nothing in flight is known. The front posts holding a few seconds later, and
    # apply asks nothing meanwhile.
    script, clock = GateScript(), FakeTime()
    seen = {"status": 0}

    def reading(n, held):
        if not held:
            return idle(1) if n < 899 else idle(60)  # quiet only at 899 s
        if seen["status"] and seen["status"] <= 4:
            seen["status"] += 1
            return idle(0, holding=False, snapshot_at=None)  # the gate restarted: no snapshot yet
        return QUIET

    def status_seen(unit):
        if unit == GATE:
            seen["status"] = 1

    script.quiet = reading
    script.restarted = (lambda original: lambda unit: (original(unit), status_seen(unit)))(script.restarted)
    code, ran, _, _ = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b", "models.yaml": "m2"},
                                app_changes=["src/spark/front/app.py"], script=script, clock=clock)
    assert code == 0 and [cmd[-1] for cmd in ran] == [BRAKE, GATE, FRONT, LLAMA] and run_apply.asked == []
    # Never confirmed: after the front's few seconds, the question, worded for files already deployed.
    script, clock = GateScript(), FakeTime()
    seen["status"] = 0

    def never(n, held):
        if not held:
            return idle(1) if n < 899 else idle(60)
        return idle(0, holding=False, snapshot_at=None) if seen["status"] else QUIET

    script.quiet = never
    script.restarted = (lambda original: lambda unit: (original(unit), status_seen(unit)))(script.restarted)
    code, ran, logs, _ = run_apply(seeded(tmp_path / "never"), FILES | {"llama-swap.yaml": "b", "models.yaml": "m2"},
                                   app_changes=["src/spark/front/app.py"], script=script, clock=clock)
    assert code == 1 and [cmd[-1] for cmd in ran] == [BRAKE, GATE]
    assert run_apply.asked == [NOT_CONFIRMED.format(no="nothing more is restarted")]
    assert any(line.startswith(f"apply: requests may still be in flight, so apply restarts nothing more, {FRONT}")
               for line in logs)


def test_with_now_and_the_front_not_connected_apply_says_nothing_is_held(tmp_path):
    # N5: drain-all's hold_all can't reach a front whose call is down, and its last snapshot is stale.
    script = GateScript()
    script.front_connected = False
    script.quiet = [busy("agent", "pi on the Mac")]
    code, ran, logs, _ = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b"}, now_ok=True, script=script,
                                   answers=[True])
    assert code == 0 and ran == [["systemctl", "restart", LLAMA]]
    assert run_apply.asked == ["The front isn't reporting its counts, so apply can't tell what's in flight. This "
                               "restarts the model service now and cuts off any request in flight. Continue? [y/N]"]
    assert ("apply: the front isn't connected to the gate, so it holds no new requests; with --now apply restarts "
            "anyway, and any request in flight is cut off") in logs
    assert "apply: new requests are held until the restarts are done" not in logs


@pytest.mark.parametrize("how", ["unanswered", "refused"])
def test_an_undrain_after_a_lost_drain_all_answer_says_the_hold_may_exist(tmp_path, how):
    # M3's words (N8): drain-all's answer was lost, so a hold may exist; then undrain-all goes unanswered, or refused.
    script = GateScript()
    script.drain_down = Down(sent=True)
    if how == "unanswered":
        script.end_down = True
    else:
        script.end_status = 500
    code, ran, logs, _ = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b"}, script=script)
    assert (code, ran) == (1, [])
    if how == "unanswered":
        assert logs[-1] == ("apply: the gate didn't answer undrain-all either, so if drain-all made a hold on new "
                            "requests, that hold ends by itself within a minute: the gate lets it lapse, or the front "
                            "drops it once the gate has been gone that long")
    else:
        assert logs[-1] == ("apply: the gate refused undrain-all (The gate failed on that request.), so if drain-all "
                            "made a hold on new requests, that hold ends by itself within a minute, when the gate lets "
                            "it lapse")


def test_a_drain_all_the_gate_refused_made_no_hold(tmp_path):
    # Beside M3: a refusal (a save that failed, which the gate takes back) made no hold, so no undrain-all follows,
    # and no line speaks of one.
    script = GateScript()
    script.drain_status = 500
    code, ran, logs, synced = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b"}, script=script)
    assert (code, ran, synced) == (1, [], []) and not script.routes("/v1/undrain-all")
    assert logs[-1] == "apply: nothing was changed: The gate failed on that request."


# The controller's ruling at Task 35 (2026-10-15): `uv sync` takes locks that any account can hold (the venv's and one in
# /tmp, each 0666), so it runs before apply's hold, bounded, and says what it waited on.

def test_the_sync_runs_before_drain_all(tmp_path):
    # The quiet path, drain now, and --now: in each, the sync before drain-all, and the files after it.
    def order(**options):
        events = []
        code, _, _, _ = run_apply(seeded(tmp_path / str(len(list(tmp_path.iterdir())))), FILES | {"llama-swap.yaml": "b"},
                                  app_changes=["src/spark/brake.py"], events=events, **options)
        assert code == 0
        return events.index("sync") < events.index("gate POST /v1/drain-all")

    assert order()
    drain_now = GateScript()
    drain_now.around(before=[busy("agent")], after=[QUIET])
    assert order(script=drain_now, answers=[True])
    assert order(now_ok=True)


def test_a_sync_that_waits_on_a_lock_takes_no_hold_and_restarts_nothing(box, monkeypatch):
    monkeypatch.setenv("UV_LOCK_TIMEOUT", "5")  # a shell's own, which would let uv go on without the lock first
    box.deployed(old={"llama-swap.yaml": "old config"})
    put(box.repo / "spark/src/spark/brake.py", "v2", time.time())
    box.uv_lock = box.app / ".venv/.lock"
    code, lines, events = box.apply()
    assert code == 1 and events == ["-validate", QUIET_READ, "uv sync"]  # no hold, no file, no restart
    assert not box.gate.routes("/v1/drain-all") and (box.etc / "llama-swap.yaml").read_text() == "old config"
    assert box.uv_timeout == spark_apply.SYNC_TIMEOUT_S == 180 and "-v" in box.uv_argv
    assert "apply: syncing the app (uv sync, up to 3 minutes)" in lines  # Task 35's review, M5
    assert int(box.uv_env["UV_LOCK_TIMEOUT"]) > spark_apply.SYNC_TIMEOUT_S
    assert lines[-1] == (f"apply: the app's sync waited on a lock: {box.uv_lock}, which another process holds, so "
                         f"apply stopped it after 3 minutes; the app's files are copied into {box.app}, and its "
                         "environment may be part-way synced; nothing else was deployed or restarted — once it's "
                         "fixed, `make apply` syncs it again")


def test_a_sync_shows_uvs_own_lines_and_never_its_debug_ones(box):
    box.deployed()
    put(box.repo / "spark/src/spark/brake.py", "v2", time.time())
    code, lines, _ = box.apply()
    assert code == 0 and "Installed 1 package in 3ms" in lines and not any(line.startswith("DEBUG") for line in lines)


def test_a_stop_after_the_sync_says_the_app_is_synced(tmp_path):
    # Ctrl-C in the wait under the hold, after the sync that now comes before it: the app is synced, nothing else.
    script, written = GateScript(), []
    script.around(before=[QUIET], after=[busy("agent"), KeyboardInterrupt()])
    code, ran, logs, synced = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b"},
                                        app_changes=["src/spark/brake.py"], script=script)
    assert (code, ran, synced) == (130, [], [True]) and (tmp_path / "llama-swap.yaml").read_text() == "a"
    assert logs[-2:] == ["apply: the hold on new requests is ended", "apply: stopped by Ctrl-C after syncing the app; "
                         "nothing else was deployed or restarted — the next `make apply` restarts each unit still "
                         "running older files"]



# Task 35's review of the sync's move (2026-10-15), M2, M3, M5 and M6.

def test_uvs_debug_lines_are_never_shown_under_force_color(box, monkeypatch):
    # M2: under FORCE_COLOR uv writes `\x1b[34mDEBUG\x1b[39m`, which the filter wouldn't know, unless told no colour.
    monkeypatch.setenv("FORCE_COLOR", "3")
    box.deployed()
    put(box.repo / "spark/src/spark/brake.py", "v2", time.time())
    code, lines, _ = box.apply()
    assert code == 0 and box.uv_argv[:3] == ["uv", "--color", "never"]
    assert "Installed 1 package in 3ms" in lines and not any("DEBUG" in line for line in lines)


def test_a_sync_that_got_its_lock_and_then_ran_slow_isnt_said_to_wait_on_a_lock(box):
    # M3 and M6: the lock was waited on, then got (uv went on), and the sync didn't finish in time: the "didn't finish"
    # line, and the environment may be part-way synced.
    box.deployed(old={"llama-swap.yaml": "old config"})
    put(box.repo / "spark/src/spark/brake.py", "v2", time.time())
    box.uv_slow = True
    code, lines, events = box.apply()
    assert code == 1 and events == ["-validate", QUIET_READ, "uv sync"] and not box.gate.routes("/v1/drain-all")
    assert lines[-1] == (f"apply: the app's sync didn't finish within 3 minutes, so apply stopped it; the app's files are "
                         f"copied into {box.app}, and its environment may be part-way synced; nothing else was deployed "
                         "or restarted — once it's fixed, `make apply` syncs it again")


@pytest.mark.parametrize("how", ["drain now", "--now"])
def test_a_sync_that_waits_on_a_lock_takes_no_hold_on_drain_now_or_with_now(box, how):
    # M6: the sync comes before drain-all on every path that holds; here after Dan's yes.
    box.deployed(old={"llama-swap.yaml": "old config"})
    put(box.repo / "spark/src/spark/brake.py", "v2", time.time())
    box.uv_lock = box.app / ".venv/.lock"
    box.gate.quiet = [busy("agent")]
    box.replies = [True]
    code, lines, events = box.apply(*(("--deadline-s", "2") if how == "drain now" else ("--now",)))
    assert code == 1 and events[-1] == "uv sync" and not box.gate.routes("/v1/drain-all")
    assert len(box.questions) == 1 and not any(event.startswith("restart") for event in events)
    assert lines[-1].startswith(f"apply: the app's sync waited on a lock: {box.uv_lock}, which another process holds")


def test_a_failed_syncs_own_lines_are_shown_escaped(box):
    # M6: uv's error is shown, its control characters escaped as the diff's are; its DEBUG lines never.
    box.deployed()
    put(box.repo / "spark/src/spark/brake.py", "v2", time.time())
    box.uv_fails = True
    box.uv_error = "error: \x1b[31mno wheel\x1b[0m for that platform"
    code, lines, _ = box.apply()
    assert code == 1 and "error: \\x1b[31mno wheel\\x1b[0m for that platform" in lines
    assert not any("DEBUG" in line for line in lines)

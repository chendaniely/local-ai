import argparse
import dataclasses
import io
import json
import os
import shutil
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import tomllib
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import yaml

from spark import brake, cli, doctor, gateproto, tickets
from spark.doctor import SECRETS, UFW_CONF, UNITS, Probe, checks, earlyoom_args, report
from spark.gateclient import GateRefused, GateUnavailable
from spark.registry import load_registry
from spark.render import COMPOSE_DIR, installed_path, llama_swap_config, render
from spark.versions import load_versions

ROOT = Path(__file__).resolve().parents[2]
REG = load_registry(Path(__file__).parent / "fixtures" / "models.yaml")
KEY = "doctor-test-key"
KERNEL = "7.0.0-1019-nvidia"
URL = doctor.paths.LLAMASWAP_URL  # llama-swap, behind the front: 127.0.0.1:900 by default since Phase 2a's Task 10
FRONT = doctor.paths.FRONT_URL  # the front, where clients reach the models: 127.0.0.1:9100
REPO_ARGS = earlyoom_args((ROOT / "stack/host/earlyoom.default").read_text())
MODULES_QUERY = ("dpkg-query", "-W", "-f=${db:Status-Abbrev}\t${Package}\n", f"linux-modules-nvidia-*-{KERNEL}")
HOLD_DRY_RUN = tuple(doctor.HOLD_DRY_RUN)
NOW = datetime(2026, 10, 15, 9, 0).timestamp()  # 09:00 in the zone the run reads, as the gate's view's `at`
PINS = {name: c.version for name, c in load_versions(ROOT / "stack/versions.yaml").items()}
LOCKED_UVICORN = next(p["version"] for p in tomllib.loads((ROOT / "spark/uv.lock").read_text())["package"]
                      if p["name"] == "uvicorn")
LLAMA_SWAP_VERSION = (str(doctor.llama_swap_binary(PINS["llama-swap"])), "-version")
LLAMA_SERVER_VERSION = (REG.engines["llama.cpp"], "--version")
UVICORN_VERSION = (str(doctor.APP_PYTHON), "-I", "-c", doctor.UVICORN_QUERY)
AGENT_UID = 1001
DROP_IN = Path(f"/etc/systemd/system/user@{AGENT_UID}.service.d/local-ai-oom.conf")
HOOK_LINE = "session optional pam_exec.so type=open_session /usr/local/libexec/local-ai-agent-oom"
# /etc/pam.d/sshd as Ubuntu 24.04 ships it, its comments left out, with the hook's line where bootstrap puts it: before
# sshd's first session line (Task 28). test_bootstrap.py's STOCK_SSHD has the whole file.
SSHD = f"""@include common-auth
account    required     pam_nologin.so
@include common-account
{HOOK_LINE}
session [success=ok ignore=ignore module_unknown=ignore default=bad]        pam_selinux.so close
session    required     pam_loginuid.so
@include common-session
session    required     pam_env.so user_readenv=1 envfile=/etc/default/locale
@include common-password
"""
FAILURE_UNITS = ("local-ai-front.service", "local-ai-gate.service", "local-ai-brake.service",
                 "local-ai-llama-swap.service")
LLAMA_SERVER_SAYS = ("0.00.000.460 I srv  llama_server: initializing ...\n"
                     "version: 0.5.0-dev (build 11146, commit 7fe450e19)\nbuilt with GNU 14.2.0 for Linux aarch64\n")


def refusal(code: str, message: str = "words") -> str:
    """A body in OpenAI's error shape, as the front answers a refusal."""
    return json.dumps({"error": {"message": message, "code": code}})


NOT_FOUND = refusal("model_not_found", f"There's no model called {doctor.MADE_UP_MODEL} here. The models are the "
                                       "vision model (vision-chat), the embeddings (embed), whisper (stt) and the coder "
                                       "(coder).")


def model_view(name: str, state: str = "ready") -> dict:
    m = REG.models[name]
    return {"name": name, "label": m.label, "resident": m.resident, "footprint_gib": m.footprint_gib, "state": state,
            "inflight": 0, "oldest_request_s": None, "last_use": None, "pinned": False, "pinned_until": None,
            "sessions": [], "brake_mark": None}


def status_view() -> dict:
    """The gate's StatusView on a healthy box, as much of it as doctor reads: the residents loaded, the coder not."""
    return {
        "schema": gateproto.STATUS_SCHEMA, "host": "brightroar", "at": NOW,
        "models": [model_view(name, "ready" if m.resident else "not_loaded") for name, m in REG.models.items()],
        "health": {"front": "ok", "gate": "ok", "brake": {"status": "ok", "key_checked_at": NOW - 3600},
                   "llama_swap": "ok", "ntfy": {"status": "ok", "failing_since": None, "dropped": 0},
                   "activity_age_s": 0.4, "unticketed_engines": [], "no_ticket_refusals": 0, "damaged_at": None,
                   "damaged_kept_as": None, "read_problems": []},
    }


class FakeProbe:
    """A healthy Spark with Phase 2a's stack running, until a test changes it."""

    def __init__(self):
        self.commands = {
            ("git", "config", "core.hooksPath"): (0, ".githooks\n", ""),
            HOLD_DRY_RUN: (0, "==> hold the GPU stack\n==> GPU set: 151 packages, 151 already held\n"
                              "+ apt-mark hold cuda-toolkit-13-0\n", ""),
            ("uname", "-r"): (0, f"{KERNEL}\n", ""),
            MODULES_QUERY: (0, f"hi \tlinux-modules-nvidia-580-open-{KERNEL}\n", ""),
            ("modinfo", "-F", "version", "nvidia"): (0, "580.178\n", ""),
            ("nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"): (0, "580.178\n", ""),
            ("systemctl", "show", "--property=MainPID", "--value", "earlyoom"): (0, "4242\n", ""),
            LLAMA_SWAP_VERSION: (0, f"version: {PINS['llama-swap']} (f00d375), built at 2026-09-22T07:20:22Z\n", ""),
            LLAMA_SERVER_VERSION: (0, "", LLAMA_SERVER_SAYS),
            UVICORN_VERSION: (0, f"{LOCKED_UVICORN}\n", ""),
        }
        # Each unit's state, as `systemctl is-active` says it: the stack's eight active, its two oneshots, which run only
        # when asked, inactive.
        self.states = {unit: "active" for unit in (*UNITS, "earlyoom", "ufw")}
        self.states |= {"local-ai-pull.service": "inactive", "local-ai-brake-drill.service": "inactive"}
        # Each unit's properties, as `systemctl show` says them: the failure notifier on the four, and the three that
        # never stop restarting (llama-swap's unit keeps systemd's own start limit, with RestartSec=5).
        self.properties = {unit: {"OnFailure": f"local-ai-notify@{unit}.service",
                                  "StartLimitIntervalUSec": "10s" if unit == "local-ai-llama-swap.service" else "0"}
                           for unit in FAILURE_UNITS}
        self.files = {
            Path("/proc/4242/cmdline"): "\0".join(["/usr/bin/earlyoom", *REPO_ARGS]) + "\0",
            UFW_CONF: "# /etc/ufw/ufw.conf\nENABLED=yes\nLOGLEVEL=low\n",
            # needrestart's override, installed by bootstrap as the repo has it.
            doctor.NEEDRESTART_CONF: (ROOT / "stack/host/needrestart.conf").read_text(),
            # The brake's start check: llama-swap took its key, from its credential (Task 23).
            doctor.paths.STATE / brake.KEY_CHECK_FILE: json.dumps(
                {"at": "2026-10-15T08:00:00", "ok": True, "key_env": "credential llamaswap-key",
                 "detail": "llama-swap answers GET /running with the key in credential llamaswap-key"}),
            # agent's OOM score (Task 28): sshd's hook, its script, the drop-in for agent's user manager, and cron.
            doctor.PAM_SSHD: SSHD,
            doctor.AGENT_HOOK: (ROOT / "stack/host/local-ai-agent-oom").read_text(),
            DROP_IN: (ROOT / "stack/host/agent-oom.conf").read_text(),
            doctor.CRON_DENY: "agent\n",
            # The failure notifier's script (Task 26), as bootstrap installs it.
            doctor.NOTIFIER: (ROOT / "stack/host/local-ai-notify").read_text(),
            # The private key list (Task 4), here the example's names, and llama-swap's deployed config.
            doctor.paths.KEYS: (ROOT / "stack/keys.example.yaml").read_text(),
            doctor.DEPLOYED_CONFIG: yaml.safe_dump(llama_swap_config(REG), sort_keys=False),
        }
        self.owners = {SECRETS: ("root", "root", 0o700)}  # root's alone since 2a's bootstrap (Task 27)
        # Root's own copies of the units and the Compose project, as `make install-units` leaves them.
        self.entries = {path: ("root", "root", 0o755, "folder") for path in doctor.ROOT_FOLDERS}
        self.entries |= {path: ("root", "root", 0o644, "file") for path in doctor.ROOT_FILES}
        # spark's home, root's own, and the brake's folder, as bootstrap sets them.
        self.entries |= {path: (*owner, "folder") for path, owner in doctor.SPARK_FOLDERS}
        self.entries |= {
            doctor.paths.GATE_STATUS_SOCKET: ("spark", "spark-users", 0o660, "socket"),
            doctor.paths.GATE_CONTROL_SOCKET: ("root", "spark-admin", 0o660, "socket"),
            doctor.paths.KEYS: ("root", "spark-admin", 0o640, "file"),
            doctor.NOTIFIER: ("root", "root", 0o755, "file"),
            doctor.AGENT_HOOK: ("root", "root", 0o755, "file"),
            DROP_IN: ("root", "root", 0o644, "file"),
            Path(REG.engines["whisper.cpp"]): ("chendaniely", "chendaniely", 0o755, "file"),
        }
        self.present = {}  # a path that exists (True) or can't be told about (None); any other doesn't exist
        self.open_folders = set()
        self.pages = {"http://127.0.0.1:3000/": 200, "http://127.0.0.1:8888/": 200}
        # A test's own answer to a URL: (status, body), or a function of the key and the body that gives one.
        self.answers = {}
        self.keys_sent = []
        self.asked = []  # (url, body) for each request made
        self.uploads = []  # (url, fields, file name, size) for each upload made
        self.upload_answer = (200, json.dumps({"text": ""}))
        # The gate's control socket: each route's answer, or the GateError it raises.
        self.gate = {"/v1/status": status_view(),
                     "/v1/quiet": {"quiet_for_s": 75.0, "last_label": "the coder", "inflight": [],
                                   "front_connected": True, "holding": False, "snapshot_at": NOW},
                     "/v1/make-room/plan": {"plan_id": "p1", "plan": {"candidates": [], "unload": [],
                                                                      "free_after_gib": 9, "enough": True,
                                                                      "most_gib": 82}},
                     "/v1/canary": {"found": []}}
        self.posted = []  # (route, body) for each POST to the gate
        self.journal = ""  # what journalctl prints, or a function of this probe that gives it
        self.groups_in = {"chendaniely", "adm", "spark-admin", "spark-users"}
        self.paused = []

    def run(self, argv):
        argv = tuple(argv)
        if argv in self.commands:
            return self.commands[argv]
        if argv[:2] == ("systemctl", "is-active"):
            states = [self.states.get(unit, "inactive") for unit in argv[2:]]
            return (0 if all(s == "active" for s in states) else 3), "".join(f"{s}\n" for s in states), ""
        if argv[:2] == ("systemctl", "show") and argv[-1] in self.properties:
            wanted = [arg.removeprefix("--property=") for arg in argv[2:-1]]
            props = self.properties[argv[-1]]
            return 0, "".join(f"{name}={props.get(name, '')}\n" for name in wanted), ""
        if argv[0] == "journalctl":
            return 0, self.journal(self) if callable(self.journal) else self.journal, ""
        return 127, "", f"{argv[0]}: not found"

    def read(self, path):
        path = Path(path)
        if path in self.files:
            return self.files[path]
        if not path.is_absolute():  # the repo's own files: the clone's, as doctor reads them from its root
            return (ROOT / path).read_text()
        return None

    def owner(self, path):
        return self.owners.get(Path(path))

    def listable(self, path):
        return Path(path) in self.open_folders

    def entry(self, path):
        return self.entries.get(Path(path))

    def exists(self, path):
        return self.present.get(Path(path), False)

    def uid(self, name):
        return {"agent": AGENT_UID}.get(name)

    def groups(self):
        return set(self.groups_in)

    def now(self):
        return NOW

    def pause(self, seconds):
        self.paused.append(seconds)

    def brake_key_check(self):
        text = self.files.get(doctor.paths.STATE / brake.KEY_CHECK_FILE)
        if text is None:
            return None, None
        try:
            return brake.parse_key_check(text), None
        except ValueError as err:
            return None, f"the brake's start check {doctor.paths.STATE / brake.KEY_CHECK_FILE} can't be read: {err}"

    def gate_get(self, route):
        answer = self.gate[route]
        if isinstance(answer, Exception):
            raise answer
        return answer

    def gate_post(self, route, body):
        self.posted.append((route, body))
        return self.gate_get(route)

    def http(self, url, *, key=None, body=None, timeout=10.0):
        self.keys_sent.append(key)
        self.asked.append((url, body))
        if url in self.answers:
            answer = self.answers[url]
            return answer(key, body) if callable(answer) else answer
        if url == f"{URL}/health":
            return 200, "OK"
        if url == f"{URL}/running":  # llama-swap takes only the internal keys from the cutover: never a client's
            return 401, ""
        if url == f"{FRONT}/health":
            return 200, '{"status": "ok"}'
        if url.startswith(FRONT):
            if key != KEY:
                return 401, refusal("invalid_api_key")
            path = url.removeprefix(FRONT)
            if path in ("/unload", "/logs", "/api/models/unload/x"):
                return 404, refusal("route_not_served")
            if path == "/v1/embeddings":
                assert body == {"model": "embed", "input": "doctor"}
                return 200, json.dumps({"data": [{"embedding": [0.25, 0.5]}]})
            if path == "/v1/chat/completions":
                if body["model"] == doctor.MADE_UP_MODEL:
                    return 404, NOT_FOUND
                return 200, json.dumps({"choices": [{"message": {"content": "OK"}}]})
        return self.pages.get(url, 0), ""

    def upload(self, url, *, key, fields, file_name, size, chunks, timeout=10.0):
        self.keys_sent.append(key)
        self.uploads.append((url, dict(fields), file_name, size))
        if key != KEY:
            return 401, refusal("invalid_api_key")
        return self.upload_answer


NO_KEY_REMEDY = " — set it as website/how-to/deploy.md's *Before the first deploy* says, then log in afresh"
# Every check, in order, without --full; --full adds the two last.
NAMES = ["leak hooks", "GPU set", "running kernel's modules", "GPU driver", "earlyoom", "agent's OOM score", "firewall",
         "secrets folder", "spark's folders", "root's copies", "needrestart", "engine config files", "stack units",
         "sockets", "key list", "version drift", "llama-swap config", "llama-swap", "front", "gate", "ntfy", "bypass",
         "web services", "a model, end to end", "S01", "S02", "S03", "S05", "S14", "S17"]
FULL = ["a 170 MB upload", "privacy canary"]
KEY_CHECKS = {"llama-swap", "front", "a model, end to end", "S03"}  # the checks that send your key


def failures(probe, key=KEY, registry=REG, **kw):
    return {c.name: c.detail for c in checks(probe, key, registry, **kw) if not c.ok}


def detail(probe, name, key=KEY, registry=REG, **kw):
    """(ok, detail) of one check."""
    return {c.name: (c.ok, c.detail) for c in checks(probe, key, registry, **kw)}[name]


def test_a_healthy_spark_passes_every_check():
    results = checks(FakeProbe(), KEY, REG)
    assert [c.name for c in results] == NAMES
    assert [c.name for c in results if not c.ok or c.warn] == []
    full = checks(FakeProbe(), KEY, REG, full=True)
    assert [c.name for c in full] == NAMES + FULL and all(c.ok and not c.warn for c in full)


def test_hooks_that_are_off_say_how_to_turn_them_on():
    probe = FakeProbe()
    probe.commands[("git", "config", "core.hooksPath")] = (1, "", "")
    assert failures(probe) == {"leak hooks": "off in this clone: run `make hooks`"}


def test_an_unheld_member_of_the_gpu_set_fails():
    probe = FakeProbe()
    probe.commands[HOLD_DRY_RUN] = (0, "==> GPU set: 151 packages, 150 already held\n", "")
    assert failures(probe) == {"GPU set": "1 of 151 packages aren't held: run `make hold-gpu`"}


def test_a_dpkg_run_that_did_not_finish_fails_with_the_holds_own_hint():
    err = ("bootstrap: these GPU-set packages are not cleanly installed, so they can't be held:\n"
           "  nvidia-driver-580-open (iU)\nfinish dpkg first: sudo dpkg --configure -a — then run this again\n")
    probe = FakeProbe()
    probe.commands[HOLD_DRY_RUN] = (1, "==> hold the GPU stack\n", err)
    found = failures(probe)["GPU set"]
    assert "nvidia-driver-580-open (iU)" in found and "sudo dpkg --configure -a" in found


def test_a_set_the_hold_would_refuse_fails_with_its_reason():
    probe = FakeProbe()
    probe.commands[HOLD_DRY_RUN] = (0, "==> GPU set: 3 packages, 3 already held\n"
                                       "+ apt-mark hold   (a real run stops here: the GPU set has no kernel)\n", "")
    assert failures(probe) == {"GPU set": "the GPU set has no kernel"}


def test_the_running_kernels_modules_must_be_held():
    probe = FakeProbe()
    probe.commands[MODULES_QUERY] = (0, f"ii \tlinux-modules-nvidia-580-open-{KERNEL}\n", "")
    assert failures(probe) == {"running kernel's modules":
                               f"linux-modules-nvidia-580-open-{KERNEL} isn't held: run `make hold-gpu`"}
    probe.commands[MODULES_QUERY] = (1, "", "dpkg-query: no packages found")
    assert "no NVIDIA modules package for the running kernel" in failures(probe)["running kernel's modules"]


def test_the_module_and_nvidia_smi_must_agree():
    # After upgrade day moved the driver, and before the reboot, the two disagree.
    probe = FakeProbe()
    probe.commands[("nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader")] = (9, "", "mismatch")
    probe.commands[("modinfo", "-F", "version", "nvidia")] = (0, "580.200\n", "")
    assert failures(probe) == {"GPU driver": "kernel module 580.200, nvidia-smi failed: see website/how-to/updates.md"}


def test_earlyoom_must_run_with_the_repos_arguments():
    probe = FakeProbe()
    old = [arg.replace("sshd.*", "sshd") for arg in REPO_ARGS]  # the args before Phase 0's fix
    probe.files[Path("/proc/4242/cmdline")] = "\0".join(["/usr/bin/earlyoom", *old]) + "\0"
    assert "other arguments than stack/host/earlyoom.default" in failures(probe)["earlyoom"]
    probe.states["earlyoom"] = "inactive"
    assert failures(probe)["earlyoom"].startswith("inactive")


def test_earlyoom_follows_the_repos_file_which_has_no_prefer():
    # Task 28: earlyoom runs without --prefer from 2a's bootstrap on, so Phase 1's arguments, which had it, fail.
    assert REPO_ARGS and not any(arg.startswith("--prefer") for arg in REPO_ARGS)
    probe = FakeProbe()
    phase_1 = [*REPO_ARGS, "--prefer", "^(llama-server|whisper-server)$"]
    probe.files[Path("/proc/4242/cmdline")] = "\0".join(["/usr/bin/earlyoom", *phase_1]) + "\0"
    assert failures(probe) == {"earlyoom": "running with other arguments than stack/host/earlyoom.default: "
                                           "`make bootstrap` puts the repo's back"}


def test_agents_oom_hook_must_stand_before_sshds_first_session_line():
    # Task 28's concern C5: an openssh-server upgrade can replace /etc/pam.d/sshd, a dpkg conffile, and the hook's line
    # goes silently. Doctor reads the line where bootstrap puts it, the script and the drop-in it installs, and cron.
    assert detail(FakeProbe(), "agent's OOM score") == (
        True, "sshd's PAM stack runs the hook before its first session line; the hook's script and the drop-in for "
              "agent's user manager are the repo's, root's own; and cron.deny names agent")
    remedy = ": `make bootstrap` puts it back"
    lines = SSHD.splitlines()
    for sshd, why in (
            ("".join(f"{line}\n" for line in lines if line != HOOK_LINE), "the hook's line is gone"),
            # After the first session line: by then pam_env may have read agent's own environment file.
            ("".join(f"{line}\n" for line in [*lines[:3], *lines[4:5], HOOK_LINE, *lines[5:]]), "after it"),
            (SSHD.replace(HOOK_LINE, HOOK_LINE.replace("optional", "required")), "a required line"),
            (SSHD.replace(HOOK_LINE, "# " + HOOK_LINE), "commented out")):
        probe = FakeProbe()
        probe.files[doctor.PAM_SSHD] = sshd
        assert failures(probe) == {"agent's OOM score": f"{doctor.PAM_SSHD} doesn't run the hook's line before its "
                                                        "first session line (an openssh-server upgrade can replace "
                                                        f"the file){remedy}"}, why
    # A session line is `session` in any case, `-session`, or an @include of common-session, as bootstrap and libpam
    # read them: the hook after any of them is after sshd's first session line.
    for first in ("-session optional pam_systemd.so", "Session required pam_loginuid.so", "@include common-session"):
        probe = FakeProbe()
        probe.files[doctor.PAM_SSHD] = SSHD.replace(f"{HOOK_LINE}\n", f"{first}\n{HOOK_LINE}\n")
        assert set(failures(probe)) == {"agent's OOM score"}, first
        probe.files[doctor.PAM_SSHD] = SSHD.replace(f"{HOOK_LINE}\n", f"{HOOK_LINE}\n{first}\n")
        assert failures(probe) == {}, first
    probe = FakeProbe()
    del probe.files[doctor.PAM_SSHD]
    assert failures(probe) == {"agent's OOM score": f"can't read {doctor.PAM_SSHD}"}

    probe = FakeProbe()
    del probe.entries[doctor.AGENT_HOOK]
    probe.entries[DROP_IN] = ("root", "root", 0o664, "file")
    assert failures(probe) == {"agent's OOM score": (
        f"{doctor.AGENT_HOOK} is missing, or out of your account's reach; {DROP_IN} is root:root 664, not root:root "
        "644: `make bootstrap` installs them")}
    probe = FakeProbe()
    probe.files[doctor.AGENT_HOOK] = "#!/bin/sh\nexit 0\n"
    assert failures(probe) == {"agent's OOM score": f"{doctor.AGENT_HOOK} differs from the repo's "
                                                    "stack/host/local-ai-agent-oom: `make bootstrap` installs it"}

    probe = FakeProbe()
    probe.files[doctor.CRON_DENY] = "nobody\nagent2\n"
    assert failures(probe) == {"agent's OOM score": f"{doctor.CRON_DENY} doesn't name agent: `make bootstrap` adds it"}
    probe = FakeProbe()
    probe.files[doctor.CRON_ALLOW] = "agent\n"
    assert failures(probe) == {"agent's OOM score": f"{doctor.CRON_ALLOW} names agent, and cron reads it instead of "
                                                    f"{doctor.CRON_DENY}: take agent out of it"}


def test_the_hook_check_reads_what_bootstrap_writes():
    # Doctor's line, script and paths are bootstrap's own, so the two can't drift.
    script = (ROOT / "stack/host/bootstrap.sh").read_text()
    assert doctor.PAM_LINE == HOOK_LINE
    assert 'PAM_LINE="session optional pam_exec.so type=open_session $AGENT_HOOK"' in script
    assert f'AGENT_HOOK="{doctor.AGENT_HOOK}"' in script
    assert f'NOTIFIER="{doctor.NOTIFIER}"' in script
    assert f'PAM_SSHD="${{BOOTSTRAP_PAM_SSHD:-{doctor.PAM_SSHD}}}"' in script
    assert f'CRON_DENY="${{BOOTSTRAP_CRON_DENY:-{doctor.CRON_DENY}}}"' in script
    assert f'CRON_ALLOW="${{BOOTSTRAP_CRON_ALLOW:-{doctor.CRON_ALLOW}}}"' in script
    assert '"$HERE/agent-oom.conf" "$folder/local-ai-oom.conf"' in script and 'folder="$UNIT_DIR/user@$uid.service.d"' in script


def test_the_firewall_must_be_on_and_an_unreadable_conf_says_how_to_check():
    probe = FakeProbe()
    probe.files[UFW_CONF] = "ENABLED=no\n"
    assert failures(probe)["firewall"].startswith("ufw is off")
    del probe.files[UFW_CONF]
    assert "sudo ufw status" in failures(probe)["firewall"]


def test_secrets_folder_must_be_root_root_700_and_closed():
    # Phase 2a (Dan's decision, 2026-10-07; Task 27): the secret files are root's alone, in a folder only root reads,
    # since PID 1 and root's Compose read them and nothing reads them as spark. Phase 1's root:spark 750 now fails.
    assert detail(FakeProbe(), "secrets folder") == (True, "root:root 700, and closed to you")
    probe = FakeProbe()
    probe.owners[SECRETS] = ("root", "spark", 0o750)
    assert failures(probe)["secrets folder"] == (
        f"{SECRETS} is root:spark 750, not root:root 700: `make bootstrap` sets it")
    probe.owners[SECRETS] = ("root", "root", 0o750)
    assert failures(probe)["secrets folder"].endswith("root:root 750, not root:root 700: `make bootstrap` sets it")
    probe = FakeProbe()
    probe.open_folders.add(SECRETS)  # root:root 700, and still listable: doctor runs as root
    assert failures(probe)["secrets folder"] == (
        f"your account can list {SECRETS}; it must not: run doctor as yourself, not with sudo")


def test_a_secrets_folder_out_of_reach_may_be_there(tmp_path):
    # /etc/local-ai is root:spark-admin 750, so a session from before bootstrap, without spark-admin, can't see into
    # it: the folder can be there, and a new login is the fix (Task 10's scan, R11).
    probe = FakeProbe()
    del probe.owners[SECRETS]
    assert failures(probe)["secrets folder"] == (
        f"{SECRETS} is missing, or out of your account's reach (log in again): `make bootstrap` creates it")


def test_what_root_runs_must_be_roots_own_files():
    # Dan's decision (2026-09-25): root runs only root's own copies, which `make install-units`
    # installs, so nothing running as Dan changes what root runs.
    unit = Path("/etc/systemd/system/local-ai-llama-swap.service")
    probe = FakeProbe()
    probe.entries[unit] = ("chendaniely", "spark-admin", 0o644, "file")
    assert failures(probe) == {"root's copies": f"{unit} is chendaniely:spark-admin 644: run `make install-units`"}
    probe.entries[unit] = ("root", "root", 0o664, "file")
    assert failures(probe)["root's copies"].startswith(f"{unit} is root:root 664")
    probe.entries[unit] = ("root", "root", 0o777, "link")
    assert failures(probe)["root's copies"].startswith(f"{unit} is a link, not a file")
    del probe.entries[unit]
    assert failures(probe)["root's copies"].startswith(f"{unit} is missing")
    # /etc/local-ai is root:spark-admin 750, so a session from before bootstrap can't see into it: the copies may
    # well be there, and a new login is the fix, as for the secrets folder (Task 10's review, M5).
    probe = FakeProbe()
    del probe.entries[Path(COMPOSE_DIR, "compose.yaml")]
    assert failures(probe) == {"root's copies": f"{COMPOSE_DIR}/compose.yaml is missing, or out of your account's "
                                                "reach (log in again): run `make install-units`"}
    probe = FakeProbe()
    probe.entries[Path(COMPOSE_DIR)] = ("root", "root", 0o775, "folder")
    assert failures(probe) == {"root's copies": f"{COMPOSE_DIR} is root:root 775: run `make install-units`"}


def test_roots_copies_follow_render_units():
    # All eleven of 2a's units (render.UNITS), not Phase 1's four: a missing copy of the gate's is named.
    assert len(doctor.render.UNITS) == 11
    assert {Path(doctor.render.UNIT_DIR, unit) for unit in doctor.render.UNITS} <= set(doctor.ROOT_FILES)
    gate = Path("/etc/systemd/system/local-ai-gate.service")
    probe = FakeProbe()
    del probe.entries[gate]
    assert failures(probe) == {"root's copies": f"{gate} is missing, or out of your account's reach (log in again): "
                                                "run `make install-units`"}


def test_doctor_checks_every_copy_that_apply_stages_for_root():
    fixtures = Path(__file__).parent / "fixtures"
    files = render(REG, load_versions(fixtures / "versions.yaml"), (fixtures / "models.yaml").read_text(),
                   templates=ROOT / "stack/templates")
    assert set(doctor.ROOT_FILES) == {Path(installed_path(rel)) for rel in files if installed_path(rel)}
    assert set(doctor.ROOT_FOLDERS) == {Path(COMPOSE_DIR), Path(COMPOSE_DIR, "searxng")}


def test_a_byte_that_isnt_utf_8_is_no_crash(tmp_path):
    # One odd byte in a file doctor reads, or in what a command prints, must not end the report: the file reads as
    # one it can't read, and the command's output keeps a stand-in character for the byte.
    (tmp_path / "ufw.conf").write_bytes(b"ENABLED=yes\n# caf\xe9\n")
    probe = Probe(ROOT)
    assert probe.read(tmp_path / "ufw.conf") is None
    assert probe.run(["printf", "caf\\351\\n"]) == (0, "caf�\n", "")


def test_the_probe_reads_a_link_as_a_link_and_a_socket_as_a_socket():
    folder = Path(tempfile.mkdtemp(prefix="dr", dir="/tmp"))
    try:
        (folder / "file").write_text("x")
        (folder / "folder").mkdir()
        (folder / "link").symlink_to(folder / "file")
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(folder / "sock"))
        listener.close()
        probe = Probe(ROOT)
        assert [probe.entry(folder / name)[3] for name in ("file", "folder", "link", "sock")] == [
            "file", "folder", "link", "socket"]
        assert probe.entry(folder / "missing") is None
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def test_sparks_folders_must_be_as_bootstrap_sets_them():
    # Task 10's scan (R10). /var/lib/local-ai is spark's home but root's, so spark can't swap a folder root creates
    # in it for a link (Phase 0's review); the brake's folder is spark's, shared with spark-admin. On brightroar this
    # fails until Phase 1's Task 12 Step 1 re-runs bootstrap: its first run left /var/lib/local-ai spark's.
    probe = FakeProbe()
    probe.entries[Path("/var/lib/local-ai")] = ("spark", "spark", 0o750, "folder")
    assert failures(probe) == {"spark's folders": "/var/lib/local-ai is spark:spark 750, not root:root 755: "
                                                  "`make bootstrap` sets it"}
    probe.entries[Path("/var/lib/local-ai/brake")] = ("spark", "spark-admin", 0o777, "link")
    assert failures(probe)["spark's folders"] == (
        "/var/lib/local-ai is spark:spark 750, not root:root 755; /var/lib/local-ai/brake is a link, not a folder: "
        "`make bootstrap` sets them")
    probe = FakeProbe()
    del probe.entries[Path("/var/lib/local-ai/brake")]
    assert failures(probe) == {"spark's folders": "/var/lib/local-ai/brake is missing, or out of your account's "
                                                  "reach: `make bootstrap` sets it"}


def test_sparks_folders_include_the_gates_launchs_and_whispers():
    # Task 27: the folders 2a's units write, as bootstrap makes them, and the Hugging Face cache, spark-pull's since
    # 2a's bootstrap, with spark's group setgid so the engines read the model files. Each is in /var/lib/local-ai,
    # root's, so your account reads each one's owner without being in its group. launch's own folders are inside a
    # folder of spark's, out of your reach, so doctor doesn't name them.
    assert dict(doctor.SPARK_FOLDERS) == {
        Path("/var/lib/local-ai"): ("root", "root", 0o755),
        Path("/var/lib/local-ai/brake"): ("spark", "spark-admin", 0o2770),
        Path("/var/lib/local-ai/gate"): ("spark", "spark", 0o750),
        Path("/var/lib/local-ai/launch"): ("spark", "spark", 0o750),
        Path("/var/lib/local-ai/whisper-tmp"): ("spark", "spark", 0o750),
        Path("/var/lib/local-ai/hf"): ("spark-pull", "spark", 0o2750),
        Path("/var/lib/local-ai/pull-cache"): ("spark-pull", "spark-pull", 0o750),
    }
    assert detail(FakeProbe(), "spark's folders") == (
        True, "/var/lib/local-ai is root's; the brake's folder spark's, shared with spark-admin; the gate's, launch's "
              "and whisper's spark's; the Hugging Face cache spark-pull's, read by spark through its group; the pull's "
              "own cache spark-pull's")
    probe = FakeProbe()
    probe.entries[Path("/var/lib/local-ai/hf")] = ("spark", "spark", 0o750, "folder")  # as Phase 1 left it
    del probe.entries[Path("/var/lib/local-ai/gate")]
    assert failures(probe) == {"spark's folders": (
        "/var/lib/local-ai/gate is missing, or out of your account's reach; /var/lib/local-ai/hf is spark:spark 750, "
        "not spark-pull:spark 2750: `make bootstrap` sets them")}


def test_llama_server_finds_no_config_file_to_read():
    # Task 10's scan (R10). llama-server reads config.ini from /etc/llama.cpp/, then from $HOME/.config/llama.cpp/,
    # before its command line, and the engines run with HOME=/var/lib/local-ai: options added there would come
    # around the flags render refuses.
    probe = FakeProbe()
    probe.present[Path("/etc/llama.cpp")] = True
    assert failures(probe) == {"engine config files": "/etc/llama.cpp exists, and llama-server reads its config.ini "
                                                      "before its command line: look at what it holds, then remove it"}
    probe.present = {Path("/var/lib/local-ai/.config"): None}
    assert failures(probe) == {"engine config files": "can't tell from your account whether /var/lib/local-ai/.config "
                                                      "exists: `sudo ls -ld /var/lib/local-ai/.config` can"}


@pytest.mark.skipif(os.geteuid() == 0, reason="root can look into any folder")
def test_the_probe_tells_a_missing_path_from_one_out_of_reach(tmp_path):
    (tmp_path / "there").mkdir()
    (tmp_path / "dangling").symlink_to(tmp_path / "nowhere")
    (tmp_path / "shut/inside").mkdir(parents=True)
    (tmp_path / "shut").chmod(0)
    try:
        found = [Probe(ROOT).exists(tmp_path / name) for name in ("there", "dangling", "missing", "shut/inside")]
    finally:
        (tmp_path / "shut").chmod(0o755)
    assert found == [True, True, False, None]


def test_needrestart_leaves_the_stack_alone():
    # Task 10's scan (R14): the stack's behaviour changed, so it gets its doctor check. On brightroar this fails
    # until Phase 1's Task 12 Step 1 re-runs bootstrap, which installs the override.
    probe = FakeProbe()
    del probe.files[doctor.NEEDRESTART_CONF]
    assert failures(probe) == {"needrestart": "/etc/needrestart/conf.d/local-ai.conf is missing, or can't be read: "
                                              "`make bootstrap` installs it"}
    probe.files[doctor.NEEDRESTART_CONF] = "$nrconf{override_rc}->{qr(^local-ai-llama)} = 0;\n"
    assert failures(probe) == {"needrestart": "/etc/needrestart/conf.d/local-ai.conf differs from "
                                              "stack/host/needrestart.conf: `make bootstrap` installs the repo's"}


def test_a_deployed_registry_that_wont_load_is_a_fail_line_not_a_traceback(tmp_path):
    for text, kind in (("budget: [unclosed\n", "doesn't load: "), (None, "is missing")):
        path = tmp_path / "models.yaml"
        path.unlink(missing_ok=True)
        if text:
            path.write_text(text)
        registry, problem = doctor.load_deployed_registry(path)
        assert registry is None and kind in problem
        failed = failures(FakeProbe(), registry=None, problem=problem)
        said = f"can't load the deployed registry: {problem} — `make apply`"
        assert failed == {name: said for name in ("key list", "version drift", "a model, end to end", "S01")}


def test_stack_units_skips_the_oneshots():
    # The five services that keep running and the three sockets; the pull and the S05 drill's brake run only when
    # asked, so their being inactive is no fault.
    probe = FakeProbe()
    assert probe.states["local-ai-pull.service"] == probe.states["local-ai-brake-drill.service"] == "inactive"
    assert detail(probe, "stack units") == (
        True, "llama-swap, the brake, the web services, the front and the gate are active, with the front's and the "
              "gate's sockets, and llama-swap took the brake's key at 2026-10-15T08:00:00")
    assert set(UNITS) == {"local-ai-llama-swap.service", "local-ai-brake.service", "local-ai-compose.service",
                          "local-ai-front.service", "local-ai-gate.service", "local-ai-front.socket",
                          "local-ai-gate-status.socket", "local-ai-gate-control.socket"}
    probe.states["local-ai-front.service"] = "inactive"
    assert failures(probe)["stack units"] == ("local-ai-front.service (inactive): `make logs s=front`, then "
                                              "`systemctl start local-ai-front.service`")


def test_a_unit_that_is_down_is_named():
    # Each FAIL says what to do (Task 10's scan, R8): a service's logs, then the start, which polkit lets you run; a
    # socket's status, then its start, which needs sudo, since the polkit rule leaves the sockets out.
    probe = FakeProbe()
    probe.states["local-ai-brake.service"] = "failed"
    probe.states["local-ai-gate-control.socket"] = "inactive"
    assert failures(probe)["stack units"] == (
        "local-ai-brake.service (failed), local-ai-gate-control.socket (inactive): `systemctl status "
        "local-ai-gate-control.socket`, then `sudo systemctl start local-ai-gate-control.socket`; `make logs s=brake`, "
        "then `systemctl start local-ai-brake.service`")
    probe = FakeProbe()
    probe.commands[("systemctl", "is-active", *UNITS)] = (1, "", "Failed to connect to bus")
    assert failures(probe)["stack units"].startswith("systemctl didn't answer: `systemctl status local-ai-front.socket "
                                                     "local-ai-gate-status.socket local-ai-gate-control.socket`")


CHECK = doctor.paths.STATE / brake.KEY_CHECK_FILE
START_CHECKS = [  # (the brake's record of its start check, or None for none; what the stack units line says)
    pytest.param(None, f"the brake runs, but {CHECK}, its start check, is missing: `make logs s=brake`", id="missing"),
    pytest.param("{not json", f"the brake runs, but the brake's start check {CHECK} can't be read: ", id="damaged"),
    pytest.param(json.dumps({"at": "T", "ok": False, "detail": "llama-swap GET /running: HTTP 401",
                             "key_env": "credential llamaswap-key"}),
                 "the brake's start check failed at T: llama-swap GET /running: HTTP 401, so nothing shows that it "
                 "can unload a model — `make logs s=brake`, and 'When something is wrong' in "
                 "website/how-to/deploy.md", id="failed"),
    # The re-review of Phase 1's council fixes (N5): a check that failed because llama-swap was slow to answer at boot
    # doesn't mean the brake can't unload now. The line says what failed and why, and claims no more.
    pytest.param(json.dumps({"at": "T", "ok": False, "key_env": "credential llamaswap-key",
                             "detail": "llama-swap didn't answer in 30 s (llama-swap unreachable at "
                                       "http://127.0.0.1:900: timed out)"}),
                 "the brake's start check failed at T: llama-swap didn't answer in 30 s (llama-swap unreachable at "
                 "http://127.0.0.1:900: timed out), so nothing shows that it can unload a model — "
                 "`make logs s=brake`, and 'When something is wrong' in website/how-to/deploy.md", id="failed-slow"),
    pytest.param(json.dumps({"at": "T", "ok": None, "detail": "waiting", "key_env": "credential llamaswap-key"}),
                 "the brake's start check has waited for llama-swap since T: run doctor again in 30 s, or "
                 "`make logs s=brake`", id="waiting"),
]


@pytest.mark.parametrize("record, said", START_CHECKS)
def test_an_active_brake_whose_start_check_didnt_pass_fails_the_stack_units_line(record, said):
    # Phase 1's council (reliability I1): a brake whose key llama-swap refuses is active, and could unload nothing.
    # Folded into the stack units line, and S05's, which reads it too; the gate's own view is the gate line's.
    probe = FakeProbe()
    if record is None:
        del probe.files[CHECK]
    else:
        probe.files[CHECK] = record
    results = checks(probe, KEY, REG)
    failed = {c.name: c.detail for c in results if not c.ok}
    assert [c.name for c in results] == NAMES and set(failed) == {"stack units", "S05"}
    assert failed["stack units"].startswith(said) and failed["S05"].startswith(said), failed


def start_check_in_a_child(state: Path) -> str:
    """What the stack units line says of the brake's start check in `state`, worked out by doctor's own Probe in a
    process of its own, given 30 s: a read that waits on a FIFO fails this run rather than hanging it."""
    script = ("from pathlib import Path\nfrom spark import doctor\n"
              "print(doctor.brake_start_check(doctor.Probe(Path('.')))[1])\n")
    done = subprocess.run([sys.executable, "-c", script], env={**os.environ, "SPARK_STATE": str(state)},
                          capture_output=True, text=True, timeout=30, cwd=ROOT)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def test_the_brakes_start_check_is_read_as_the_gate_reads_it(tmp_path):
    # Task 29's review (I3): the record is in a folder spark writes, so it is read never following a link, never
    # waiting on a FIFO, and only up to a cap: brake.read_key_check, through tickets.read_record, as the gate reads it.
    # Each gives a line that says it can't be read, never a hang and never the linked file's words.
    record = tmp_path / brake.KEY_CHECK_FILE
    cannot = f"the brake runs, but the brake's start check {record} can't be read: "
    passed = {"at": "T", "ok": True, "detail": "d", "key_env": "credential llamaswap-key"}
    os.mkfifo(record)  # opening it without O_NONBLOCK would wait for a writer
    assert start_check_in_a_child(tmp_path).startswith(cannot)
    record.unlink()
    (tmp_path / "elsewhere.json").write_text(json.dumps(passed))
    record.symlink_to(tmp_path / "elsewhere.json")
    said = start_check_in_a_child(tmp_path)
    assert said.startswith(cannot) and "took the brake's key" not in said
    record.unlink()
    record.write_text(json.dumps({**passed, "detail": "d" * tickets.RECORD_MAX_BYTES}))  # past read_record's cap
    assert start_check_in_a_child(tmp_path).startswith(cannot + f"larger than {tickets.RECORD_MAX_BYTES} bytes")
    record.write_text(json.dumps(passed))
    assert start_check_in_a_child(tmp_path) == "llama-swap took the brake's key at T"
    record.write_text(json.dumps({**passed, "ok": False, "detail": "a reason\x1b[2J"}))  # shown escaped
    said = start_check_in_a_child(tmp_path)
    assert "\x1b" not in said and "a reason\\x1b[2J" in said
    record.unlink()
    assert start_check_in_a_child(tmp_path) == (f"the brake runs, but {record}, its start check, is missing: `make "
                                                "logs s=brake`")


def test_a_stopped_brake_is_named_whatever_its_last_start_check_said():
    probe = FakeProbe()
    probe.states["local-ai-brake.service"] = "failed"
    failed = failures(probe)
    assert failed["stack units"] == ("local-ai-brake.service (failed): `make logs s=brake`, then "
                                     "`systemctl start local-ai-brake.service`")
    assert failed["S05"] == "the brake is failed: `make logs s=brake`, then `systemctl start local-ai-brake.service`"


def test_socket_modes_and_groups_are_checked():
    # The controller's ruling at Task 25's review: the status socket is spark:spark-users, the control socket
    # root:spark-admin, each 0660.
    assert detail(FakeProbe(), "sockets") == (
        True, "the gate's status socket is spark:spark-users 660, and its control socket root:spark-admin 660")
    status, control = doctor.paths.GATE_STATUS_SOCKET, doctor.paths.GATE_CONTROL_SOCKET
    remedy = ": root's socket units set them, and `make install-units-dry-run` shows whether they differ from the repo's"
    probe = FakeProbe()
    probe.entries[status] = ("spark", "spark-users", 0o666, "socket")
    assert failures(probe) == {"sockets": f"{status} is spark:spark-users 666, not spark:spark-users 660{remedy}"}
    probe = FakeProbe()
    probe.entries[control] = ("root", "spark-users", 0o660, "socket")
    assert failures(probe) == {"sockets": f"{control} is root:spark-users 660, not root:spark-admin 660{remedy}"}
    probe = FakeProbe()
    probe.entries[control] = ("root", "spark-admin", 0o660, "file")
    del probe.entries[status]
    assert failures(probe) == {"sockets": f"{status} is missing, or out of your account's reach; {control} is a file, "
                                          f"not a socket{remedy}"}


def test_the_key_list_must_load():
    assert detail(FakeProbe(), "key list") == (
        True, f"{doctor.paths.KEYS} loads: 3 keys, each in one of the registry's key groups (dan and agent)")
    step = "website/how-to/secret-files.md, step 13, installs it"
    probe = FakeProbe()
    probe.files[doctor.paths.KEYS] = "keys:\n  dans-phone:\n    group: friends\n    label: Dan's phone\n"
    found = failures(probe)["key list"]
    assert found.startswith(f"{doctor.paths.KEYS} doesn't load: keys: dans-phone: group 'friends' is not one of ")
    assert found.endswith(f": {step}")
    probe = FakeProbe()
    probe.entries[doctor.paths.KEYS] = ("root", "spark-admin", 0o644, "file")
    assert failures(probe) == {"key list": f"{doctor.paths.KEYS} is root:spark-admin 644, not root:spark-admin 640: "
                                           f"{step}"}
    probe = FakeProbe()
    del probe.entries[doctor.paths.KEYS]
    assert failures(probe) == {"key list": f"{doctor.paths.KEYS} is missing, or out of your account's reach (log in "
                                           f"again): {step}"}


def test_version_drift_names_the_component():
    assert detail(FakeProbe(), "version drift") == (
        True, f"llama-swap {PINS['llama-swap']}, llama-server {PINS['llama.cpp']}, whisper-server under "
              f"{PINS['whisper.cpp']} and uvicorn {LOCKED_UVICORN}, as stack/versions.yaml and the lock pin them")
    probe = FakeProbe()
    probe.commands[LLAMA_SWAP_VERSION] = (0, "version: v256 (abc1234), built at 2026-09-01T00:00:00Z\n", "")
    assert failures(probe) == {"version drift": f"llama-swap -version names v256, not {PINS['llama-swap']}: "
                                                "website/how-to/updates.md moves it"}
    probe = FakeProbe()
    probe.commands[LLAMA_SERVER_VERSION] = (0, "", LLAMA_SERVER_SAYS.replace("build 11146", "build 11145"))
    assert failures(probe) == {"version drift": f"llama-server --version names b11145, not {PINS['llama.cpp']}: "
                                                "website/how-to/updates.md moves it"}
    probe = FakeProbe()
    probe.commands[UVICORN_VERSION] = (0, "0.53.0\n", "")
    assert failures(probe) == {"version drift": f"the deployed app's uvicorn is 0.53.0, not the lock's {LOCKED_UVICORN}:"
                                                " `make apply` syncs it"}
    # Each mismatch named, in one line; a binary that won't run, or isn't where the registry says, too.
    registry = dataclasses.replace(REG, engines={**REG.engines,
                                                 "whisper.cpp": "/opt/local-ai/bin/whisper.cpp/v1.9.3/whisper-server"})
    probe = FakeProbe()
    del probe.commands[LLAMA_SWAP_VERSION]
    probe.commands[UVICORN_VERSION] = (0, "0.53.0\n", "")
    assert failures(probe, registry=registry) == {"version drift": (
        f"{LLAMA_SWAP_VERSION[0]} -version didn't run, or named no version: website/how-to/updates.md installs it; "
        f"the registry's whisper-server, /opt/local-ai/bin/whisper.cpp/v1.9.3/whisper-server, isn't under "
        f"{PINS['whisper.cpp']}: `make apply` deploys the repo's registry; the deployed app's uvicorn is 0.53.0, not "
        f"the lock's {LOCKED_UVICORN}: `make apply` syncs it")}
    probe = FakeProbe()
    del probe.entries[Path(REG.engines["whisper.cpp"])]
    assert failures(probe) == {"version drift": f"{REG.engines['whisper.cpp']} is missing, or out of your account's "
                                                "reach: website/how-to/updates.md installs it"}


def test_the_deployed_config_is_checked_against_v257s_schema():
    assert detail(FakeProbe(), "llama-swap config") == (
        True, f"{doctor.DEPLOYED_CONFIG} passes llama-swap {PINS['llama-swap']}'s schema")
    config = llama_swap_config(REG)
    group = config["routing"]["router"]["settings"]["groups"]["stack"]
    group["swapp"] = group.pop("swap")  # v257 drops a key it doesn't know, and swaps: its schema's closed reading doesn't
    probe = FakeProbe()
    probe.files[doctor.DEPLOYED_CONFIG] = yaml.safe_dump(config)
    found = failures(probe)["llama-swap config"]
    assert found.startswith(f"{doctor.DEPLOYED_CONFIG} doesn't pass llama-swap {PINS['llama-swap']}'s schema: ")
    assert "swapp" in found and found.endswith(": `make apply-dry-run` shows how it differs from the repo's")
    probe.files[doctor.DEPLOYED_CONFIG] = "models: [unclosed\n"
    assert failures(probe)["llama-swap config"].startswith(f"{doctor.DEPLOYED_CONFIG} doesn't load: ")
    del probe.files[doctor.DEPLOYED_CONFIG]
    assert failures(probe) == {"llama-swap config": f"{doctor.DEPLOYED_CONFIG} is missing, or can't be read: "
                                                    "`make apply` deploys it"}


def test_a_client_key_must_be_refused_at_llama_swaps_port():
    # From the cutover, llama-swap on 900 takes only the internal keys: your client key goes through the front.
    assert detail(FakeProbe(), "llama-swap") == (
        True, "its unit is active, it answers on its own port, and it refuses a call without a key and one with your "
              "client key, which only the front takes")
    probe = FakeProbe()
    probe.answers[f"{URL}/running"] = lambda key, body: (200, "") if key == KEY else (401, "")
    assert failures(probe) == {"llama-swap": (
        "/running took your client key, from SPARK_API_KEY: llama-swap must take only the internal keys, and clients "
        "go through the front. `make apply-dry-run` shows whether the deployed config, which sets them, differs from "
        "the repo's")}
    probe.answers[f"{URL}/running"] = lambda key, body: (500, "") if key == KEY else (401, "")
    assert failures(probe) == {"llama-swap": "/running with your client key answered 500, not 401: `make logs "
                                             "s=llama-swap`"}
    probe = FakeProbe()
    probe.states["local-ai-llama-swap.service"] = "failed"
    assert failures(probe)["llama-swap"] == ("its unit is failed: `make logs s=llama-swap`, then `systemctl start "
                                             "local-ai-llama-swap.service`")


def test_llama_swap_must_refuse_a_call_without_a_key():
    probe = FakeProbe()
    probe.answers[f"{URL}/running"] = (200, "")
    found = failures(probe)["llama-swap"]
    assert "keys aren't enforced" in found
    assert found.endswith("`make apply-dry-run` shows whether the deployed config, which sets them, differs from the "
                          "repo's; `make logs s=llama-swap` shows what llama-swap said")
    probe.answers[f"{URL}/health"] = (0, "")
    assert failures(probe)["llama-swap"] == "/health answered nothing: `make logs s=llama-swap`"


def test_routes_outside_the_list_must_get_route_not_served():
    assert detail(FakeProbe(), "front") == (
        True, "answers /health, refuses a request without a key, and answers 404 route_not_served for llama-swap's "
              "own routes (GET /unload, POST /api/models/unload/x and GET /logs)")
    probe = FakeProbe()
    probe.answers[f"{FRONT}/unload"] = (200, "")
    assert failures(probe) == {"front": "GET /unload answered 200, not 404 route_not_served: the front must answer "
                                        "only the model API's routes — `make logs s=front`"}
    probe.answers[f"{FRONT}/logs"] = (404, refusal("model_not_found"))
    assert failures(probe) == {"front": "GET /unload answered 200, and GET /logs 404 model_not_found, not 404 "
                                        "route_not_served: the front must answer only the model API's routes — "
                                        "`make logs s=front`"}
    # The route that unloads is a POST: sent as one, with an empty body.
    assert (f"{FRONT}/api/models/unload/x", {}) in probe.asked and (f"{FRONT}/unload", None) in probe.asked


def test_the_front_must_answer_and_check_keys():
    probe = FakeProbe()
    probe.answers[f"{FRONT}/v1/models"] = lambda key, body: (200, "{}")
    assert failures(probe)["front"] == ("GET /v1/models without a key answered 200, not 401: the front isn't checking "
                                        "keys — `make logs s=front`")
    probe = FakeProbe()
    probe.answers[f"{FRONT}/health"] = (0, "")
    assert failures(probe)["front"] == "/health answered nothing: `make logs s=front`"
    assert failures(FakeProbe(), key="another-key")["front"] == (
        "GET /unload with your key answered 401: the front doesn't know the key in SPARK_API_KEY — see 'When "
        "something is wrong' in website/how-to/deploy.md")


def test_the_gate_must_answer_and_its_health_show_the_four_services():
    assert detail(FakeProbe(), "gate") == (
        True, "answers on its control socket, and its health shows the front, the gate, the brake and llama-swap ok")
    probe = FakeProbe()
    health = probe.gate["/v1/status"]["health"]
    health["front"] = "not connected"
    health["brake"] = {"status": "start check failed: HTTP 401", "key_checked_at": None}
    assert failures(probe)["gate"] == (
        "it answers, but its health shows the front not connected (`make logs s=front`) and the brake start check "
        "failed: HTTP 401 (`make logs s=brake`)")
    probe = FakeProbe()
    down = GateUnavailable("The gate can't be reached: there is no socket at /run/local-ai/gate-control.sock. On the "
                           "Spark, `make doctor` shows what's wrong.")
    probe.gate["/v1/status"] = down
    failed = failures(probe)
    assert failed["gate"] == f"GET /v1/status on its control socket got no answer: {down}"
    # Every line that reads the gate's view says it couldn't, and points at the gate's line.
    assert set(failed) == {"gate", "ntfy", "bypass", "a model, end to end", "S01", "S05"}
    assert all(failed[name].startswith("the gate didn't answer (its own line says why)")
               for name in ("ntfy", "bypass", "a model, end to end", "S01", "S05"))
    probe.gate["/v1/status"] = {"schema": gateproto.STATUS_SCHEMA + 1, "health": {}, "models": []}
    assert failures(probe)["gate"] == (f"its answer to GET /v1/status has schema {gateproto.STATUS_SCHEMA + 1}, and "
                                       f"this doctor reads {gateproto.STATUS_SCHEMA}: `make apply` deploys the app "
                                       "this clone has")


def test_ntfy_prints_a_verdict_never_the_url(monkeypatch, capsys):
    monkeypatch.setenv("NTFY_URL", "https://ntfy.example.invalid/stand-in-topic")
    probe = FakeProbe()
    assert detail(probe, "ntfy") == (True, "the gate's notifications reach it")
    since = datetime(2026, 10, 15, 8, 52).timestamp()
    probe.gate["/v1/status"]["health"]["ntfy"] = {"status": "failing", "failing_since": since, "dropped": 2}
    assert failures(probe) == {"ntfy": "notifications failing since 08:52: `make logs s=gate`"}
    code, shown = spark_doctor(monkeypatch, capsys, probe)
    assert code == 1 and "FAIL  ntfy: notifications failing since 08:52" in shown
    assert "ntfy.example.invalid" not in shown and "stand-in-topic" not in shown


def test_ntfy_not_set_up_is_a_warning_never_a_failure(monkeypatch, capsys):
    # Dan's decision, 2026-10-14: ntfy is optional until Task 2, in Phase 5; a gate without it serves, and doctor
    # flags it (Task 19's note) as a WARN, which fails nothing and isn't counted in N of M.
    monkeypatch.setenv("SPARK_API_KEY", KEY)
    probe = FakeProbe()
    probe.gate["/v1/status"]["health"]["ntfy"] = {"status": "not_set_up", "failing_since": None, "dropped": 0}
    [ntfy] = [c for c in checks(probe, KEY, REG) if c.name == "ntfy"]
    assert ntfy.ok and ntfy.warn and ntfy.detail == "not set up (Task 2, Phase 5)"
    code, shown = spark_doctor(monkeypatch, capsys, probe)
    lines = shown.splitlines()
    assert code == 0 and "WARN  ntfy: not set up (Task 2, Phase 5)" in lines
    assert lines[-1] == f"doctor: {len(NAMES) - 1} of {len(NAMES) - 1} checks pass, 1 warning"
    assert [line for line in lines if line.startswith("FAIL")] == []
    probe.gate["/v1/status"]["health"]["ntfy"] = {"status": "failing", "failing_since": None, "dropped": 0}
    code, shown = spark_doctor(monkeypatch, capsys, probe)
    assert code == 1 and "FAIL  ntfy: notifications failing: `make logs s=gate`" in shown.splitlines()
    assert shown.splitlines()[-1] == f"doctor: {len(NAMES) - 1} of {len(NAMES)} checks pass"


def test_a_warning_is_its_own_verdict():
    with pytest.raises(ValueError):
        doctor.Check("ntfy", False, "a warning passes", warn=True)
    text = report([doctor.Check("a", True, "x"), doctor.Check("b", True, "y", warn=True), doctor.Check("c", True, "z",
                                                                                                         warn=True)])
    assert text.splitlines() == ["ok    a: x", "WARN  b: y", "WARN  c: z", "doctor: 1 of 1 checks pass, 2 warnings"]


def test_only_an_engine_without_a_ticket_fails_the_bypass_line():
    assert detail(FakeProbe(), "bypass") == (True, "every engine that runs was started with a ticket from the gate")
    probe = FakeProbe()
    probe.gate["/v1/status"]["health"]["unticketed_engines"] = [{"model": "coder", "port": 801, "pid": 4242}]
    assert failures(probe) == {"bypass": "the coder runs on port 801 (pid 4242) without a ticket from the gate, a load "
                                         "around it: `ps -o user,args -p 4242` shows who started it"}


def test_no_ticket_refusals_are_a_note_never_a_failure():
    probe = FakeProbe()
    probe.gate["/v1/status"]["health"]["no_ticket_refusals"] = 3
    assert detail(probe, "bypass") == (
        True, "every engine that runs was started with a ticket from the gate; 3 starts refused for want of a ticket "
              "since boot: the backstop working")
    probe.gate["/v1/status"]["health"]["no_ticket_refusals"] = 1
    assert detail(probe, "bypass")[1].endswith("; 1 start refused for want of a ticket since boot: the backstop "
                                               "working")


def test_each_2a_scenario_has_its_check():
    probe = FakeProbe()
    assert {name: detail(probe, name) for name in ("S01", "S02", "S03", "S05", "S14")} == {
        "S01": (True, "an on-demand model unloads after 60 min idle; the vision model, the embeddings and whisper are "
                      "always loaded, as the gate counts them too, and never unload for being idle"),
        "S02": (True, "the gate answers make-room's plan for 1 GiB; doctor asks only for the plan, so nothing unloads"),
        "S03": (True, "a made-up model through the front gets 404 model_not_found, in its words, and loads nothing"),
        "S05": (True, "the brake is active and llama-swap took the brake's key at 2026-10-15T08:00:00, and the gate's "
                      "activity record is 0.4 s old, so the brake goes by the gate's order"),
        "S14": (True, "the front, the gate, the brake and llama-swap start the failure notifier when they fail, the "
                      "first three never stop restarting, and the notifier's script is installed"),
    }
    assert ("/v1/make-room/plan", {"size_gib": 1}) in probe.posted
    assert all(route != "/v1/make-room" for route, _ in probe.posted)

    # Each fails on its own fault, and nothing else does.
    idle = dataclasses.replace(REG, gate=dataclasses.replace(REG.gate, idle_unload_min=30))
    assert failures(FakeProbe(), registry=idle) == {"S01": (
        "the deployed registry unloads an on-demand model after 30 min idle, not the plan's 60: `make apply-dry-run` "
        "shows how it differs from the repo's")}
    probe = FakeProbe()
    probe.gate["/v1/status"]["models"][0]["resident"] = False
    assert failures(probe) == {"S01": "the gate counts as always loaded the embeddings and whisper, and the registry the "
                                      "vision model, the embeddings and whisper: `make logs s=gate`"}

    probe = FakeProbe()
    probe.gate["/v1/make-room/plan"] = GateRefused(400, {"message": "make-room's request is {size_gib: <GiB>} or "
                                                                    "{all: true}.", "code": None})
    assert failures(probe) == {"S02": "the gate didn't answer POST /v1/make-room/plan, which spark make-room asks "
                                      "first: make-room's request is {size_gib: <GiB>} or {all: true}."}

    probe = FakeProbe()
    probe.answers[f"{FRONT}/v1/chat/completions"] = (200, json.dumps({"choices": []}))
    assert failures(probe) == {"S03": "a made-up model through the front answered 200, not 404 model_not_found: "
                                      "`make logs s=front`"}
    probe.answers[f"{FRONT}/v1/chat/completions"] = (404, refusal("model_not_found", "Some other words."))
    assert failures(probe) == {"S03": "a made-up model through the front answered 404 model_not_found, but not in its "
                                      "words: `make logs s=front`"}

    probe = FakeProbe()
    probe.gate["/v1/status"]["health"]["activity_age_s"] = 7.5
    assert failures(probe) == {"S05": "the gate's activity record is 7 s old, so the brake would go by Phase 1's "
                                      "order: `make logs s=gate`"}
    probe.gate["/v1/status"]["health"]["activity_age_s"] = None
    assert failures(probe) == {"S05": "the gate hasn't written its activity record, so the brake would go by Phase "
                                      "1's order: `make logs s=gate`"}

    probe = FakeProbe()
    probe.properties["local-ai-brake.service"]["OnFailure"] = ""
    assert failures(probe) == {"S14": "local-ai-brake.service starts no local-ai-notify@local-ai-brake.service.service "
                                      "when it fails: `make install-units`"}
    probe.properties["local-ai-gate.service"]["StartLimitIntervalUSec"] = "10s"
    del probe.files[doctor.NOTIFIER]
    assert failures(probe) == {"S14": (
        "local-ai-gate.service's StartLimitIntervalSec= is 10s, not 0: `make install-units`; "
        "local-ai-brake.service starts no local-ai-notify@local-ai-brake.service.service when it fails: `make "
        f"install-units`; {doctor.NOTIFIER} differs from the repo's stack/host/local-ai-notify: `make bootstrap` "
        "installs it")}

    probe = FakeProbe()
    probe.gate["/v1/quiet"] = GateUnavailable("The gate didn't answer within 5 s.")
    assert failures(probe) == {"S17": "the gate didn't answer GET /v1/quiet, so make apply can't see what's in flight: "
                                      "The gate didn't answer within 5 s."}


def test_doctor_checks_s17():
    # Task 32: `make apply`'s quiet wait reads the gate's GET /v1/quiet, on its control socket.
    assert detail(FakeProbe(), "S17") == (
        True, "the gate answers GET /v1/quiet, which make apply's wait for a quiet moment reads")
    probe = FakeProbe()
    down = GateUnavailable("The gate can't be reached: there is no socket at /run/local-ai/gate-control.sock. On the "
                           "Spark, `make doctor` shows what's wrong.")
    probe.gate["/v1/quiet"] = down
    assert failures(probe)["S17"] == (
        f"the gate didn't answer GET /v1/quiet, so make apply can't see what's in flight: {down}")
    probe.gate["/v1/quiet"] = GateRefused(403, {"message": "Only spark-admin's members can ask that.", "code": None})
    assert failures(probe)["S17"] == ("the gate didn't answer GET /v1/quiet, so make apply can't see what's in flight: "
                                      "Only spark-admin's members can ask that.")
    unreadable = "the gate's answer to GET /v1/quiet isn't one make apply can read: `make logs s=gate`"
    probe.gate["/v1/quiet"] = {"quiet_for_s": "a while"}
    assert failures(probe)["S17"] == unreadable
    # Task 32's re-review (N7): what apply's read_quiet requires, the front's hold and its call to the gate included.
    whole = FakeProbe().gate["/v1/quiet"]
    for field in ("holding", "front_connected", "snapshot_at"):
        probe.gate["/v1/quiet"] = {name: value for name, value in whole.items() if name != field}
        if field == "snapshot_at":  # None is a reading (no snapshot yet); a missing field reads as None too
            assert "S17" not in failures(probe)
            probe.gate["/v1/quiet"] = {**whole, "snapshot_at": "now"}
        assert failures(probe)["S17"] == unreadable, field
    probe.gate["/v1/quiet"] = {**whole, "inflight": [{"model": "coder", "model_label": "the coder", "age_s": 3.0}]}
    assert failures(probe)["S17"] == unreadable


def test_doctor_never_loads_a_model():
    # Doctor is read-only on the box: it asks a model only once the gate shows it loaded, so its request can't start
    # a load.
    probe = FakeProbe()
    probe.gate["/v1/status"]["models"][1]["state"] = "not_loaded"  # the embeddings
    failed = failures(probe, full=True)
    assert failed == {"a model, end to end": "the gate shows the embeddings as not loaded, and doctor loads nothing: "
                                             "`spark load embed`, then doctor again"}
    assert all(url != f"{FRONT}/v1/embeddings" for url, _ in probe.asked)
    probe = FakeProbe()
    probe.gate["/v1/status"]["models"][2]["state"] = "starting"  # whisper
    probe.gate["/v1/status"]["models"][0]["state"] = "draining"  # the only chat model loaded
    failed = failures(probe, full=True)
    assert failed == {"a 170 MB upload": "the gate shows whisper as still loading: run doctor again in a minute",
                      "privacy canary": "no chat model is loaded to send it through, and doctor loads nothing: "
                                        "`spark load small` loads the vision model"}
    assert probe.uploads == [] and all(url != f"{FRONT}/v1/chat/completions" or body["model"] == doctor.MADE_UP_MODEL
                                       for url, body in probe.asked)


def test_the_end_to_end_check_goes_through_the_front():
    probe = FakeProbe()
    assert detail(probe, "a model, end to end") == (True, "the embeddings answered through the front")
    assert (f"{FRONT}/v1/embeddings", {"model": "embed", "input": "doctor"}) in probe.asked
    probe.answers[f"{FRONT}/v1/embeddings"] = (409, refusal("held_by_brake"))
    assert failures(probe) == {"a model, end to end": "the embeddings answered 409 held_by_brake: `make status` says "
                                                      "why"}
    probe.answers[f"{FRONT}/v1/embeddings"] = (200, '{"data": []}')
    assert failures(probe) == {"a model, end to end": "the embeddings answered 200, but with no embedding: `make logs "
                                                      "s=front`"}
    probe.answers[f"{FRONT}/v1/embeddings"] = (0, "")
    assert failures(probe) == {"a model, end to end": "the front didn't answer: `make logs s=front`"}


def test_a_registry_without_an_embeddings_model_says_how_to_give_it_one():
    registry = dataclasses.replace(REG, models={name: m for name, m in REG.models.items()
                                                if m.capability != "embeddings"})
    probe = FakeProbe()
    probe.gate["/v1/status"]["models"] = [m for m in probe.gate["/v1/status"]["models"] if m["name"] != "embed"]
    assert failures(probe, registry=registry) == {
        "a model, end to end": "the registry has no embeddings model, which this check asks: give stack/models.yaml "
                               "one, then `make apply`"}


def test_full_runs_the_upload_and_the_canary():
    probe = FakeProbe()
    checks(probe, KEY, REG)
    assert probe.uploads == [] and probe.posted == [("/v1/make-room/plan", {"size_gib": 1})]
    probe = FakeProbe()
    results = {c.name: (c.ok, c.detail) for c in checks(probe, KEY, REG, full=True)}
    assert results["a 170 MB upload"] == (True, "whisper took a 170 MB upload through the front, which sends a form "
                                                "on chunked, and answered")
    assert results["privacy canary"] == (True, "a string sent through chat and speech-to-text is in none of the "
                                               "journal, the gate's state, its refusal history or launch's records")
    [big, small] = probe.uploads
    assert big[0] == small[0] == f"{FRONT}/v1/audio/transcriptions" and big[3] == 170_000_000
    assert big[1] == {"model": "stt", "response_format": "json"}
    needle = small[1]["prompt"]
    assert needle.startswith("doctor-canary-") and small[2] == f"{needle}.wav" and small[1]["model"] == "stt"
    chat = [body for url, body in probe.asked if url == f"{FRONT}/v1/chat/completions"
            and body["model"] == "vision-chat"]
    assert len(chat) == 1 and needle in chat[0]["messages"][0]["content"]
    assert ("/v1/canary", {"needle": needle}) in probe.posted and probe.paused

    # Found in the journal, which your account reads in group adm: named.
    probe = FakeProbe()
    probe.journal = lambda p: "\n".join(json.dumps(body) for _, body in p.asked)
    assert failures(probe, full=True) == {"privacy canary": (
        "the string sent through chat and speech-to-text turned up in the journal: find what wrote it there before "
        "sending anything private")}
    # Found by the gate, in its refusal history: named.
    probe = FakeProbe()
    probe.gate["/v1/canary"] = {"found": ["refusals", "launch"]}
    assert failures(probe, full=True) == {"privacy canary": (
        "the string sent through chat and speech-to-text turned up in the gate's refusal history and launch's "
        "records: find what wrote it there before sending anything private")}
    # Without group adm, the journal can't show it isn't there.
    probe = FakeProbe()
    probe.groups_in = {"chendaniely", "spark-admin"}
    assert failures(probe, full=True) == {"privacy canary": "your account isn't in group adm, so it can't read the "
                                                            "system journal: log in again"}
    # An upload the front or whisper refuses.
    probe = FakeProbe()
    probe.upload_answer = (413, refusal("body_too_large"))
    failed = failures(probe, full=True)
    assert failed["a 170 MB upload"] == ("whisper answered a 170 MB upload through the front with 413 body_too_large: "
                                         "`make logs s=front`")
    assert failed["privacy canary"] == ("the canary couldn't be sent through speech-to-text: whisper answered 413 "
                                        "body_too_large")


def test_the_upload_is_a_wav_of_the_size_asked():
    size, chunks = doctor.wav(170_000_000)
    data = b"".join(chunks)
    assert size == len(data) == 170_000_000
    assert data[:4] == b"RIFF" and data[8:16] == b"WAVEfmt " and data[36:40] == b"data"
    assert int.from_bytes(data[4:8], "little") == size - 8 and int.from_bytes(data[40:44], "little") == size - 44


def test_doctor_still_never_prints_a_key(monkeypatch, capsys):
    # Phase 1's, over every check, --full's included: your key goes to the front and to llama-swap's refusal, never
    # into a line.
    probe = FakeProbe()
    text = report(checks(probe, KEY, REG, full=True))
    assert KEY in probe.keys_sent and KEY not in text
    # And when every check that sends it fails: a key the front doesn't know.
    probe = FakeProbe()
    probe.answers[f"{URL}/running"] = lambda key, body: (200, "") if key else (401, "")
    text = report(checks(probe, "another-key-entirely", REG, full=True))
    assert "another-key-entirely" not in text
    monkeypatch.setenv("SPARK_API_KEY", KEY)
    code, shown = spark_doctor(monkeypatch, capsys, FakeProbe(), "--full")
    assert code == 0 and KEY not in shown


def test_the_report_shows_every_check_and_never_the_key():
    probe = FakeProbe()
    text = report(checks(probe, KEY, REG))
    assert KEY in probe.keys_sent and KEY not in text
    assert text.splitlines()[0] == "ok    leak hooks: on in this clone"
    assert text.splitlines()[-1] == f"doctor: {len(NAMES)} of {len(NAMES)} checks pass"


def test_a_hold_dry_run_that_found_nothing_says_how_to_look():
    probe = FakeProbe()
    probe.commands[HOLD_DRY_RUN] = (0, "==> hold the GPU stack\n", "")
    assert failures(probe) == {"GPU set": "the hold's dry run found no GPU set: `make hold-gpu-dry-run` shows what "
                                          "it found"}


def test_without_a_key_the_key_checks_fail_and_the_others_still_run():
    assert failures(FakeProbe(), key=None) == {
        # Every FAIL says what to do (Task 10's review, M4): the remedy is deploy.md's.
        name: f"no key in this shell: SPARK_API_KEY isn't set{NO_KEY_REMEDY}" for name in KEY_CHECKS}


def spark_doctor(monkeypatch, capsys, probe, *args: str) -> tuple[int, str]:
    """`spark doctor ARGS`, from the repo root, on `probe` and the fixture registry: its exit status and output."""
    monkeypatch.setattr(doctor, "Probe", lambda repo: probe)
    monkeypatch.setattr(doctor, "load_deployed_registry", lambda path: (REG, None))
    monkeypatch.chdir(ROOT)
    code = cli.main(["doctor", *args])
    shown = capsys.readouterr()
    return code, shown.out + shown.err


def test_a_missing_key_names_the_variable_doctor_read(monkeypatch, capsys):
    # --key-env picks the variable: the FAIL line names that one, not the default.
    found = {c.name: c.detail for c in checks(FakeProbe(), None, REG, key_env="LLAMASWAP_KEY_SPARK") if not c.ok}
    assert found == {name: f"no key in this shell: LLAMASWAP_KEY_SPARK isn't set{NO_KEY_REMEDY}" for name in KEY_CHECKS}
    code, shown = spark_doctor(monkeypatch, capsys, FakeProbe(), "--key-env", "MY_OWN_KEY")
    assert code == 1 and "no key in this shell: MY_OWN_KEY isn't set" in shown and "SPARK_API_KEY" not in shown
    monkeypatch.setenv("MY_OWN_KEY", "fake-key\r")
    code, shown = spark_doctor(monkeypatch, capsys, FakeProbe(), "--key-env", "MY_OWN_KEY")
    assert "the key in MY_OWN_KEY isn't printable ASCII" in shown and "fake-key" not in shown


@pytest.mark.parametrize("url", ["", "127.0.0.1:900", "http://", "http://127.0.0.1:port"],
                         ids=["set-but-empty", "no-scheme", "no-host", "port-not-a-number"])
def test_a_llama_swap_url_that_isnt_one_is_named_and_not_blamed_on_llama_swap(monkeypatch, url):
    # Task 10's review (M6): with SPARK_LLAMASWAP_URL set to what isn't an http(s) URL, the llama-swap line says so,
    # not "/health answered nothing", and the key goes nowhere. It offers no address: from the cutover 9100 is the
    # front's (Task 24's review), and llama-swap's own is the default.
    monkeypatch.setattr(doctor.paths, "LLAMASWAP_URL", url)
    probe = FakeProbe()
    bad = "SPARK_LLAMASWAP_URL isn't an http(s) URL: set it to one, or unset it for llama-swap's own address"
    assert failures(probe) == {"llama-swap": bad}
    assert not any(u in (f"{url}/health", f"{url}/running") for u, _ in probe.asked)


@pytest.mark.parametrize("url", ["", "127.0.0.1:9100", "http://"], ids=["set-but-empty", "no-scheme", "no-host"])
def test_a_front_url_that_isnt_one_is_named(monkeypatch, url):
    monkeypatch.setattr(doctor.paths, "FRONT_URL", url)
    probe = FakeProbe()
    bad = "SPARK_FRONT_URL isn't an http(s) URL: set it to one, or unset it for the front's own address"
    assert failures(probe, full=True) == {name: bad for name in (*KEY_CHECKS - {"llama-swap"}, *FULL)}


def test_doctor_runs_only_from_the_repo_root(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert doctor.run(argparse.Namespace(key_env="SPARK_API_KEY", full=False)) == 2
    assert "repo root" in capsys.readouterr().out


def test_make_doctor_runs_spark_doctor():
    # -s: under -C, GNU make 4 also prints "Entering directory" and "Leaving directory" lines.
    recipe = subprocess.run(["make", "-s", "-n", "-C", str(ROOT), "doctor"], capture_output=True, text=True, check=True)
    assert recipe.stdout.strip().endswith("spark doctor")


def test_spark_doctor_full_is_its_own_flag(monkeypatch, capsys):
    probe = FakeProbe()
    code, shown = spark_doctor(monkeypatch, capsys, probe)
    assert "a 170 MB upload" not in shown and probe.uploads == []
    monkeypatch.setenv("SPARK_API_KEY", KEY)
    code, shown = spark_doctor(monkeypatch, capsys, probe, "--full")
    assert code == 0 and "ok    a 170 MB upload:" in shown and "ok    privacy canary:" in shown


# Probe's gate calls, against a stand-in gate on a short /tmp socket.

@contextmanager
def gate_standin(answers: dict):
    """A gate on a Unix socket of its own: each (method, path) answered with its JSON, every request recorded."""
    folder = Path(tempfile.mkdtemp(prefix="dr", dir="/tmp"))
    path = folder / "control.sock"
    seen = []

    class Gate(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def answer(self):
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else None
            seen.append((self.command, self.path, body))
            data = json.dumps(answers[(self.command, self.path)]).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = answer

    server = socketserver.ThreadingUnixStreamServer(str(path), Gate)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    try:
        yield path, seen
    finally:
        server.shutdown()
        server.server_close()
        shutil.rmtree(folder, ignore_errors=True)


def test_the_probe_asks_the_gate_on_its_control_socket(monkeypatch):
    # Doctor's Probe asks the gate through the CLI's client, on the control socket's path: here a stand-in's.
    answers = {("GET", "/v1/quiet"): {"quiet_for_s": 3.0, "last_label": None, "inflight": []},
               ("POST", "/v1/canary"): {"found": []}}
    with gate_standin(answers) as (path, seen):
        monkeypatch.setattr(doctor.paths, "GATE_CONTROL_SOCKET", path)
        assert Probe(ROOT).gate_get("/v1/quiet") == {"quiet_for_s": 3.0, "last_label": None, "inflight": []}
        assert Probe(ROOT).gate_post("/v1/canary", {"needle": "n"}) == {"found": []}
        assert seen == [("GET", "/v1/quiet", None), ("POST", "/v1/canary", {"needle": "n"})]
        monkeypatch.setattr(doctor.paths, "GATE_CONTROL_SOCKET", path.parent / "missing.sock")
        with pytest.raises(GateUnavailable):
            Probe(ROOT).gate_get("/v1/quiet")


# Probe.http and Probe.upload, against stand-ins that really listen on 127.0.0.1. Task 10's pre-dispatch scan (R2) found
# the plan's listing sending the key through a proxy and on to wherever a redirect pointed, and printing it in a
# traceback's stead: the leaks Task 3's client had closed (828fb3e).
REACHED: list[tuple[str, str, str | None]] = []  # each request a stand-in got: its name, its target, its key header
REDIRECT: dict[str, str] = {}  # a path a stand-in answers, when a key comes with it, with a redirect


class StandIn(BaseHTTPRequestHandler):
    """Records every request, and answers `/` with a redirect to /login, which answers 200, as a web UI may."""

    name = "stand-in"

    def log_message(self, *args):
        pass

    def answer(self, code: int, body: bytes = b"", location: str | None = None):
        self.send_response(code)
        if location:
            self.send_header("Location", location)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        REACHED.append((self.name, self.requestline.split()[1], self.headers.get("Authorization")))
        if self.path == "/":
            return self.answer(302, location="/login")
        self.answer(200, b'{"running": []}')

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.do_GET()


class LlamaSwapStandIn(StandIn):
    """llama-swap from the cutover, as doctor asks it: /health, and /running, which refuses every key but an internal
    one, so your client key too."""

    name = "llama-swap"

    def do_GET(self):
        auth = self.headers.get("Authorization")
        REACHED.append((self.name, self.requestline.split()[1], auth))
        if auth and self.path in REDIRECT:  # a POST is sent on as a GET for a 303, and for a 301 or 302 too
            return self.answer(303 if self.command == "POST" else 302, location=REDIRECT[self.path])
        if self.path == "/health":
            return self.answer(200, b"OK")
        self.answer(401, b'{"error": "unauthorized"}')


class FrontStandIn(StandIn):
    """The front, as doctor asks it: /health; a 401 without the key KEY; 404 route_not_served for a route it doesn't
    serve; the embeddings."""

    name = "front"

    def do_GET(self):
        auth = self.headers.get("Authorization")
        REACHED.append((self.name, self.requestline.split()[1], auth))
        if auth and self.path in REDIRECT:
            return self.answer(303 if self.command == "POST" else 302, location=REDIRECT[self.path])
        if self.path == "/health":
            return self.answer(200, b'{"status": "ok"}')
        if auth != f"Bearer {KEY}":
            return self.answer(401, refusal("invalid_api_key").encode())
        if self.path == "/v1/embeddings":
            return self.answer(200, json.dumps({"data": [{"embedding": [0.25, 0.5]}]}).encode())
        self.answer(404, refusal("route_not_served").encode())


class Garbled(BaseHTTPRequestHandler):
    """Answers /short with a body cut short, and anything else with something that isn't HTTP."""

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.close_connection = True
        if self.path == "/short":  # promises 100 bytes, sends 13, and the connection closes
            self.send_response(200)
            self.send_header("Content-Length", "100")
            self.end_headers()
            self.wfile.write(b'{"running": [')
        else:
            self.wfile.write(b"garbage\r\n\r\n")


@contextmanager
def serving(handler):
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture()
def stand_ins(monkeypatch):
    """llama-swap's and the front's stand-ins, where doctor looks for them."""
    REACHED.clear()
    REDIRECT.clear()
    with serving(LlamaSwapStandIn) as llama_swap, serving(FrontStandIn) as front:
        monkeypatch.setattr(doctor.paths, "LLAMASWAP_URL", llama_swap)
        monkeypatch.setattr(doctor.paths, "FRONT_URL", front)
        yield llama_swap, front


class LiveProbe(FakeProbe):
    """FakeProbe, except that what doctor asks llama-swap and the front goes out for real, through doctor's own
    Probe.http, to their stand-ins."""

    def __init__(self):
        super().__init__()
        self.real = Probe(ROOT)

    def http(self, url, *, key=None, body=None, timeout=10.0):
        if not url.startswith((doctor.paths.LLAMASWAP_URL, doctor.paths.FRONT_URL)):
            return super().http(url, key=key, body=body, timeout=timeout)
        self.keys_sent.append(key)
        return self.real.http(url, key=key, body=body, timeout=timeout)


def keyed_elsewhere() -> list[tuple[str, str, str | None]]:
    """What reached a stand-in other than llama-swap and the front with a key."""
    return [request for request in REACHED if request[0] not in ("llama-swap", "front") and request[2] is not None]


def test_a_proxy_in_the_environment_never_gets_a_request(stand_ins, monkeypatch):
    with serving(StandIn) as proxy:
        monkeypatch.setenv("http_proxy", proxy)
        monkeypatch.setattr(urllib.request, "_opener", None)  # urlopen keeps the proxies it read on first use
        probe = LiveProbe()
        results = [doctor.llama_swap(probe, KEY), doctor.front(probe, KEY),
                   doctor.model(probe, KEY, REG, status_view())]
    assert [request for request in REACHED if request[0] not in ("llama-swap", "front")] == []
    assert [c.ok for c in results] == [True, True, True], results


BAD_KEYS = [
    pytest.param("fake-key\r", id="cr-at-the-end"),  # a CRLF line in a sourced secrets file
    pytest.param("fake-key\nsecond-line", id="lf-inside"),
    pytest.param("fake-key-é", id="a-letter-beyond-ascii"),  # http.client sends it, as Latin-1
]


@pytest.mark.parametrize("key", BAD_KEYS)
def test_a_key_a_header_cant_carry_is_never_sent_or_shown(key):
    probe = FakeProbe()
    results = checks(probe, key, REG, full=True)
    failed = {c.name: c.detail for c in results if not c.ok}
    assert set(failed) == KEY_CHECKS | set(FULL)
    assert all("isn't printable ASCII (a stray CR or LF?), so it wasn't sent" in found for found in failed.values())
    assert key not in probe.keys_sent
    assert "fake-key" not in report(results)


@pytest.mark.parametrize("key", BAD_KEYS)
def test_spark_doctor_reports_on_a_key_a_header_cant_carry_and_never_shows_it(stand_ins, monkeypatch, capsys, key):
    # The plan's listing sent the key to http.client, whose ValueError names it: `spark doctor` printed that, with
    # no report. A Latin-1 letter it sent.
    live = LiveProbe()
    monkeypatch.setattr(doctor, "Probe", lambda repo: live)
    monkeypatch.setattr(doctor, "load_deployed_registry", lambda path: (REG, None))
    monkeypatch.setenv("SPARK_API_KEY", key)
    monkeypatch.chdir(ROOT)
    code = cli.main(["doctor"])
    shown = capsys.readouterr()
    assert code == 1 and "checks pass" in shown.out, shown
    assert "fake-key" not in shown.out + shown.err
    assert key not in live.keys_sent and [auth for _, _, auth in REACHED if auth] == []


@pytest.mark.parametrize("which, path", [("llama-swap", "/running"), ("front", "/unload"),
                                         ("front", "/v1/embeddings")])
def test_a_request_with_the_key_never_follows_a_redirect(stand_ins, which, path):
    # urllib sends a redirected request on with every header it was given, the key's included, to wherever the
    # redirect points, and a POST as a GET. With the key, the redirect is the answer, and the check names its status.
    probe = Probe(ROOT)
    with serving(StandIn) as elsewhere:
        REDIRECT[path] = f"{elsewhere}/stolen"
        check = {"/running": lambda: doctor.llama_swap(probe, KEY), "/unload": lambda: doctor.front(probe, KEY),
                 "/v1/embeddings": lambda: doctor.model(probe, KEY, REG, status_view())}[path]()
    assert keyed_elsewhere() == []
    assert not check.ok and ("303" if path == "/v1/embeddings" else "302") in check.detail, check
    assert "a redirect, which doctor never follows with your key" in check.detail
    assert stand_ins[0] not in check.detail and stand_ins[1] not in check.detail  # no address, which may hold a password


def test_a_request_without_the_key_still_follows_a_redirect():
    # Open WebUI or SearXNG may answer / with a redirect, to a login page, say: the web check follows it.
    REACHED.clear()
    with serving(StandIn) as web:
        assert Probe(ROOT).http(f"{web}/") == (200, '{"running": []}')
    assert [target for _, target, _ in REACHED] == ["/", "/login"]


def test_an_error_answer_keeps_its_body(stand_ins):
    # The front's refusals carry their code in the body: the probe reads it, as for any other answer.
    _, front = stand_ins
    code, body = Probe(ROOT).http(f"{front}/unload", key=KEY)
    assert code == 404 and json.loads(body)["error"]["code"] == "route_not_served"


@pytest.mark.parametrize("path", ["/not-http", "/short"])
def test_an_answer_that_isnt_http_or_breaks_off_is_no_answer_not_a_crash(path):
    with serving(Garbled) as url:
        assert Probe(ROOT).http(f"{url}{path}") == (0, "")


def test_a_request_urllib_cant_make_is_no_answer_not_a_crash():
    # A URL with no scheme, from a SPARK_LLAMASWAP_URL that is set but empty, raises ValueError in urllib: only that
    # one reaches http()'s catch. A key http.client won't put in a header would raise one that names the header's
    # value, and a Latin-1 key it would send: http()'s own guard refuses both first, before any request is made,
    # and that guard is what the loop covers (Task 10's review, M8).
    probe = Probe(ROOT)
    assert probe.http("/health") == (0, "")
    for key in ("fake-key\r", "fake-key-é"):
        with serving(StandIn) as url:
            REACHED.clear()
            assert probe.http(f"{url}/running", key=key) == (0, "")
            size, chunks = doctor.wav(1000)
            assert probe.upload(f"{url}/up", key=key, fields={"model": "m"}, file_name="a.wav", size=size,
                                chunks=chunks) == (0, "")
        assert REACHED == []


def test_an_error_answer_is_closed(monkeypatch):
    # The answer to a refused request is an HTTPError that holds the connection: closed then, not left to the
    # garbage collector, which never closes it while something else holds the error, as a traceback or a log can.
    answer = io.BytesIO(b"unauthorized")
    held = []

    def refuse(self, fullurl, data=None, timeout=None):
        held.append(urllib.error.HTTPError("http://127.0.0.1:900/running", 401, "Unauthorized", {}, answer))
        raise held[-1]

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", refuse)
    assert Probe(ROOT).http("http://127.0.0.1:900/running") == (401, "unauthorized")
    assert answer.closed


class FormStandIn(BaseHTTPRequestHandler):
    """Reads an upload as the front does, by its Content-Length, and answers what it got."""

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        data, read = bytearray(), 0
        while read < length:
            piece = self.rfile.read(min(1 << 20, length - read))
            if not piece:
                break
            data += piece
            read += len(piece)
        REACHED.append(("form", self.headers.get("Transfer-Encoding"), self.headers.get("Authorization")))
        boundary = self.headers["Content-Type"].split("boundary=", 1)[1]
        parts = bytes(data).split(f"--{boundary}".encode())
        heads = [part.split(b"\r\n\r\n", 1)[0].decode() for part in parts[1:-1]]
        file_part = parts[-2].split(b"\r\n\r\n", 1)[1].removesuffix(b"\r\n")
        body = json.dumps({"length": length, "read": read, "heads": heads, "file": len(file_part),
                           "riff": file_part[:4].decode(), "last": parts[-1].decode()}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def test_an_upload_streams_a_multipart_form_of_its_length():
    # The 170 MB upload goes out as it is made, never held whole in doctor's memory, with its Content-Length, so the
    # front reads it by its length (one with both that and Transfer-Encoding is refused).
    REACHED.clear()
    size, chunks = doctor.wav(3 << 20)
    with serving(FormStandIn) as url:
        code, body = Probe(ROOT).upload(f"{url}/v1/audio/transcriptions", key=KEY,
                                        fields={"model": "stt", "response_format": "json"}, file_name="doctor.wav",
                                        size=size, chunks=chunks)
    got = json.loads(body)
    assert code == 200 and got["read"] == got["length"] and got["file"] == 3 << 20 and got["riff"] == "RIFF"
    assert got["heads"] == ['\r\nContent-Disposition: form-data; name="model"',
                            '\r\nContent-Disposition: form-data; name="response_format"',
                            '\r\nContent-Disposition: form-data; name="file"; filename="doctor.wav"\r\n'
                            "Content-Type: audio/wav"]
    assert got["last"] == "--\r\n"
    assert REACHED == [("form", None, f"Bearer {KEY}")]

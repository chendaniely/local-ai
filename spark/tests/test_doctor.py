import argparse
import io
import json
import subprocess
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from spark import cli, doctor
from spark.doctor import SECRETS, UFW_CONF, UNITS, Probe, checks, earlyoom_args, report
from spark.registry import load_registry
from spark.render import COMPOSE_DIR, installed_path, render
from spark.versions import load_versions

ROOT = Path(__file__).resolve().parents[2]
REG = load_registry(Path(__file__).parent / "fixtures" / "models.yaml")
KEY = "doctor-test-key"
KERNEL = "7.0.0-1019-nvidia"
URL = "http://127.0.0.1:9100"
REPO_ARGS = earlyoom_args((ROOT / "stack/host/earlyoom.default").read_text())
MODULES_QUERY = ("dpkg-query", "-W", "-f=${db:Status-Abbrev}\t${Package}\n", f"linux-modules-nvidia-*-{KERNEL}")
HOLD_DRY_RUN = tuple(doctor.HOLD_DRY_RUN)


class FakeProbe:
    """A healthy Spark with the stack running, until a test changes it."""

    def __init__(self):
        self.commands = {
            ("git", "config", "core.hooksPath"): (0, ".githooks\n", ""),
            HOLD_DRY_RUN: (0, "==> hold the GPU stack\n==> GPU set: 151 packages, 151 already held\n"
                              "+ apt-mark hold cuda-toolkit-13-0\n", ""),
            ("uname", "-r"): (0, f"{KERNEL}\n", ""),
            MODULES_QUERY: (0, f"hi \tlinux-modules-nvidia-580-open-{KERNEL}\n", ""),
            ("modinfo", "-F", "version", "nvidia"): (0, "580.178\n", ""),
            ("nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"): (0, "580.178\n", ""),
            ("systemctl", "is-active", "earlyoom"): (0, "active\n", ""),
            ("systemctl", "show", "--property=MainPID", "--value", "earlyoom"): (0, "4242\n", ""),
            ("systemctl", "is-active", "ufw"): (0, "active\n", ""),
            ("systemctl", "is-active", *UNITS): (0, "active\nactive\nactive\n", ""),
        }
        self.files = {
            Path("/proc/4242/cmdline"): "\0".join(["/usr/bin/earlyoom", *REPO_ARGS]) + "\0",
            doctor.EARLYOOM_DEFAULT: (ROOT / "stack/host/earlyoom.default").read_text(),
            UFW_CONF: "# /etc/ufw/ufw.conf\nENABLED=yes\nLOGLEVEL=low\n",
        }
        self.owners = {SECRETS: ("root", "spark", 0o750)}
        # Root's own copies of the units and the Compose project, as `make install-units` leaves them.
        self.entries = {path: ("root", "root", 0o755, "folder") for path in doctor.ROOT_FOLDERS}
        self.entries |= {path: ("root", "root", 0o644, "file") for path in doctor.ROOT_FILES}
        self.open_folders = set()
        self.pages = {f"{URL}/health": 200, "http://127.0.0.1:3000/": 200, "http://127.0.0.1:8888/": 200}
        self.keys_sent = []

    def run(self, argv):
        return self.commands.get(tuple(argv), (127, "", f"{argv[0]}: not found"))

    def read(self, path):
        return self.files.get(Path(path))

    def owner(self, path):
        return self.owners.get(Path(path))

    def listable(self, path):
        return Path(path) in self.open_folders

    def entry(self, path):
        return self.entries.get(Path(path))

    def http(self, url, *, key=None, body=None, timeout=10.0):
        self.keys_sent.append(key)
        if url == f"{URL}/running":
            return (200, '{"running": []}') if key == KEY else (401, "")
        if url == f"{URL}/v1/embeddings":
            if key != KEY:
                return 401, ""
            assert body == {"model": "embed", "input": "doctor"}
            return 200, json.dumps({"data": [{"embedding": [0.25, 0.5]}]})
        return self.pages.get(url, 0), ""


def failures(probe, key=KEY):
    return {c.name: c.detail for c in checks(probe, key, REG) if not c.ok}


def test_a_healthy_spark_passes_every_check():
    results = checks(FakeProbe(), KEY, REG)
    assert len(results) == 12 and [c.name for c in results if not c.ok] == []


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
    detail = failures(probe)["GPU set"]
    assert "nvidia-driver-580-open (iU)" in detail and "sudo dpkg --configure -a" in detail


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
    probe.commands[("systemctl", "is-active", "earlyoom")] = (3, "inactive\n", "")
    assert failures(probe)["earlyoom"].startswith("inactive")


def test_the_firewall_must_be_on_and_an_unreadable_conf_says_how_to_check():
    probe = FakeProbe()
    probe.files[UFW_CONF] = "ENABLED=no\n"
    assert failures(probe)["firewall"].startswith("ufw is off")
    del probe.files[UFW_CONF]
    assert "sudo ufw status" in failures(probe)["firewall"]


def test_the_secrets_folder_must_stay_closed_to_you():
    probe = FakeProbe()
    probe.open_folders.add(SECRETS)
    assert "can list" in failures(probe)["secrets folder"]
    probe.owners[SECRETS] = ("root", "spark", 0o755)
    assert failures(probe)["secrets folder"].endswith("root:spark 755, not root:spark 750")


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
    probe = FakeProbe()
    probe.entries[Path(COMPOSE_DIR)] = ("root", "root", 0o775, "folder")
    assert failures(probe) == {"root's copies": f"{COMPOSE_DIR} is root:root 775: run `make install-units`"}


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


def test_the_probe_reads_a_link_as_a_link(tmp_path):
    (tmp_path / "file").write_text("x")
    (tmp_path / "folder").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "file")
    probe = Probe(ROOT)
    assert [probe.entry(tmp_path / name)[3] for name in ("file", "folder", "link")] == ["file", "folder", "link"]
    assert probe.entry(tmp_path / "missing") is None


def test_a_deployed_registry_that_wont_load_is_a_fail_line_not_a_traceback(tmp_path):
    for text, kind in (("budget: [unclosed\n", "doesn't load: "), (None, "is missing")):
        path = tmp_path / "models.yaml"
        path.unlink(missing_ok=True)
        if text:
            path.write_text(text)
        registry, problem = doctor.load_deployed_registry(path)
        assert registry is None and kind in problem
        detail = {c.name: c.detail for c in checks(FakeProbe(), KEY, None, problem) if not c.ok}
        assert detail == {"a model, end to end": f"can't load the deployed registry: {problem} — `make apply`"}


def test_a_unit_that_is_down_is_named():
    probe = FakeProbe()
    probe.commands[("systemctl", "is-active", *UNITS)] = (3, "active\ninactive\nactive\n", "")
    assert failures(probe) == {"stack units": "local-ai-brake.service (inactive)"}


def test_llama_swap_must_refuse_a_call_without_a_key():
    probe = FakeProbe()
    probe.http = lambda url, key=None, body=None, timeout=10.0: (200, "")
    assert "keys aren't enforced" in failures(probe)["llama-swap"]


def test_without_a_key_the_key_checks_fail_and_the_others_still_run():
    assert failures(FakeProbe(), key=None) == {
        "llama-swap": "no key in this shell: SPARK_API_KEY isn't set",
        "a model, end to end": "no key in this shell: SPARK_API_KEY isn't set",
    }


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
    detail = {c.name: c.detail for c in checks(FakeProbe(), None, REG, key_env="LLAMASWAP_KEY_SPARK") if not c.ok}
    assert detail == {"llama-swap": "no key in this shell: LLAMASWAP_KEY_SPARK isn't set",
                      "a model, end to end": "no key in this shell: LLAMASWAP_KEY_SPARK isn't set"}
    code, shown = spark_doctor(monkeypatch, capsys, FakeProbe(), "--key-env", "MY_OWN_KEY")
    assert code == 1 and "no key in this shell: MY_OWN_KEY isn't set" in shown and "SPARK_API_KEY" not in shown
    monkeypatch.setenv("MY_OWN_KEY", "fake-key\r")
    code, shown = spark_doctor(monkeypatch, capsys, FakeProbe(), "--key-env", "MY_OWN_KEY")
    assert "the key in MY_OWN_KEY isn't printable ASCII" in shown and "fake-key" not in shown


def test_a_refused_load_points_at_make_status():
    probe = FakeProbe()
    answer = probe.http
    probe.http = lambda url, key=None, body=None, timeout=10.0: (
        (502, "") if url.endswith("/v1/embeddings") else answer(url, key=key, body=body, timeout=timeout))
    assert failures(probe) == {"a model, end to end": "embed answered 502: `make status` says why a load was refused"}


def test_the_report_shows_every_check_and_never_the_key():
    probe = FakeProbe()
    text = report(checks(probe, KEY, REG))
    assert KEY in probe.keys_sent and KEY not in text
    assert text.splitlines()[0] == "ok    leak hooks: on in this clone"
    assert text.splitlines()[-1] == "doctor: 12 of 12 checks pass"


def test_doctor_runs_only_from_the_repo_root(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert doctor.run(argparse.Namespace(key_env="SPARK_API_KEY")) == 2
    assert "repo root" in capsys.readouterr().out


def test_make_doctor_runs_spark_doctor():
    # -s: under -C, GNU make 4 also prints "Entering directory" and "Leaving directory" lines.
    recipe = subprocess.run(["make", "-s", "-n", "-C", str(ROOT), "doctor"], capture_output=True, text=True, check=True)
    assert recipe.stdout.strip().endswith("spark doctor")


# Probe.http, against stand-ins that really listen on 127.0.0.1. Task 10's pre-dispatch scan (R2) found the plan's
# listing sending the key through a proxy and on to wherever a redirect pointed, and printing it in a traceback's
# stead: the leaks Task 3's client had closed (828fb3e).
REACHED: list[tuple[str, str, str | None]] = []  # each request a stand-in got: its name, its target, its key header
REDIRECT: dict[str, str] = {}  # a path the llama-swap stand-in answers, when a key comes with it, with a redirect


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
    """llama-swap as doctor asks it: /health, and /running and /v1/embeddings with the key KEY."""

    name = "llama-swap"

    def do_GET(self):
        auth = self.headers.get("Authorization")
        REACHED.append((self.name, self.requestline.split()[1], auth))
        if auth and self.path in REDIRECT:  # a POST is sent on as a GET for a 303, and for a 301 or 302 too
            return self.answer(303 if self.command == "POST" else 302, location=REDIRECT[self.path])
        if self.path == "/health":
            return self.answer(200, b"OK")
        if auth != f"Bearer {KEY}":
            return self.answer(401)
        if self.path == "/running":
            return self.answer(200, b'{"running": []}')
        self.answer(200, json.dumps({"data": [{"embedding": [0.25, 0.5]}]}).encode())


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
def llama_swap(monkeypatch):
    """The llama-swap stand-in, where doctor looks for llama-swap."""
    REACHED.clear()
    REDIRECT.clear()
    with serving(LlamaSwapStandIn) as url:
        monkeypatch.setattr(doctor.paths, "LLAMASWAP_URL", url)
        yield url


class LiveProbe(FakeProbe):
    """FakeProbe, except that what doctor asks llama-swap goes out for real, through doctor's own Probe.http, to the
    llama-swap stand-in."""

    def __init__(self):
        super().__init__()
        self.real = Probe(ROOT)

    def http(self, url, *, key=None, body=None, timeout=10.0):
        if not url.startswith(doctor.paths.LLAMASWAP_URL):
            return super().http(url, key=key, body=body, timeout=timeout)
        self.keys_sent.append(key)
        return self.real.http(url, key=key, body=body, timeout=timeout)


def keyed_elsewhere() -> list[tuple[str, str, str | None]]:
    """What reached a stand-in other than llama-swap with a key."""
    return [request for request in REACHED if request[0] != "llama-swap" and request[2] is not None]


def test_a_proxy_in_the_environment_never_gets_a_request(llama_swap, monkeypatch):
    with serving(StandIn) as proxy:
        monkeypatch.setenv("http_proxy", proxy)
        monkeypatch.setattr(urllib.request, "_opener", None)  # urlopen keeps the proxies it read on first use
        probe = Probe(ROOT)
        results = [doctor.llama_swap(probe, KEY), doctor.model(probe, KEY, REG)]
    assert [request for request in REACHED if request[0] != "llama-swap"] == []  # nothing, so no key, reached it
    assert [c.ok for c in results] == [True, True]


BAD_KEYS = [
    pytest.param("fake-key\r", id="cr-at-the-end"),  # a CRLF line in a sourced secrets file
    pytest.param("fake-key\nsecond-line", id="lf-inside"),
    pytest.param("fake-key-é", id="a-letter-beyond-ascii"),  # http.client sends it, as Latin-1
]


@pytest.mark.parametrize("key", BAD_KEYS)
def test_a_key_a_header_cant_carry_is_never_sent_or_shown(key):
    probe = FakeProbe()
    results = checks(probe, key, REG)
    failed = {c.name: c.detail for c in results if not c.ok}
    assert set(failed) == {"llama-swap", "a model, end to end"}
    assert all("isn't printable ASCII (a stray CR or LF?), so it wasn't sent" in detail for detail in failed.values())
    assert key not in probe.keys_sent
    assert "fake-key" not in report(results)


@pytest.mark.parametrize("key", BAD_KEYS)
def test_spark_doctor_reports_on_a_key_a_header_cant_carry_and_never_shows_it(llama_swap, monkeypatch, capsys, key):
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


@pytest.mark.parametrize("path", ["/running", "/v1/embeddings"])
def test_a_request_with_the_key_never_follows_a_redirect(llama_swap, path):
    # urllib sends a redirected request on with every header it was given, the key's included, to wherever the
    # redirect points, and a POST as a GET. With the key, the redirect is the answer, and the check names its status.
    probe = Probe(ROOT)
    with serving(StandIn) as elsewhere:
        REDIRECT[path] = f"{elsewhere}/stolen"
        check = doctor.llama_swap(probe, KEY) if path == "/running" else doctor.model(probe, KEY, REG)
    assert keyed_elsewhere() == []
    assert not check.ok and ("302" if path == "/running" else "303") in check.detail, check


def test_a_request_without_the_key_still_follows_a_redirect():
    # Open WebUI or SearXNG may answer / with a redirect, to a login page, say: the web check follows it.
    REACHED.clear()
    with serving(StandIn) as web:
        assert Probe(ROOT).http(f"{web}/") == (200, '{"running": []}')
    assert [target for _, target, _ in REACHED] == ["/", "/login"]


@pytest.mark.parametrize("path", ["/not-http", "/short"])
def test_an_answer_that_isnt_http_or_breaks_off_is_no_answer_not_a_crash(path):
    with serving(Garbled) as url:
        assert Probe(ROOT).http(f"{url}{path}") == (0, "")


def test_a_request_urllib_cant_make_is_no_answer_not_a_crash():
    # A URL with no scheme, from a SPARK_LLAMASWAP_URL that is set but empty, raises ValueError in urllib; a key
    # http.client won't put in a header raises one that names the header's value, and a Latin-1 key it sends.
    probe = Probe(ROOT)
    assert probe.http("/health") == (0, "")
    for key in ("fake-key\r", "fake-key-é"):
        with serving(StandIn) as url:
            REACHED.clear()
            assert probe.http(f"{url}/running", key=key) == (0, "")
        assert REACHED == []


def test_an_error_answer_is_closed(monkeypatch):
    # The answer to a refused request is an HTTPError that holds the connection: closed then, not left to the
    # garbage collector, which never closes it while something else holds the error, as a traceback or a log can.
    answer = io.BytesIO(b"unauthorized")
    held = []

    def refuse(self, fullurl, data=None, timeout=None):
        held.append(urllib.error.HTTPError("http://127.0.0.1:9100/running", 401, "Unauthorized", {}, answer))
        raise held[-1]

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", refuse)
    assert Probe(ROOT).http("http://127.0.0.1:9100/running") == (401, "")
    assert answer.closed

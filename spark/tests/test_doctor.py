import argparse
import json
import subprocess
from pathlib import Path

from spark import doctor
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

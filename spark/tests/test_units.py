"""The units `spark render` writes for Phase 2a (Task 25): the front's socket and service, the gate's two sockets and
its service, the failure notifier, the brake, its S05 drill and the pull. Checked on the rendered text, and, where this
machine has systemd, by systemd itself: `systemd-analyze verify` on each, and transient user units that start with each
ntfy reader's values-file and credential lines, its paths moved under tmp_path, with ntfy's files there and without."""

import ast
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from spark import gateproto, render, sockets
from spark.registry import load_registry
from spark.render import SPARK_BIN
from spark.versions import load_versions

ROOT = Path(__file__).resolve().parents[2]
FIX = Path(__file__).parent / "fixtures"
FRONT_SOCKET, FRONT = "local-ai-front.socket", "local-ai-front.service"
STATUS_SOCKET, CONTROL_SOCKET = "local-ai-gate-status.socket", "local-ai-gate-control.socket"
GATE = "local-ai-gate.service"
NOTIFY, BRAKE, DRILL = "local-ai-notify@.service", "local-ai-brake.service", "local-ai-brake-drill.service"
LLAMA_SWAP, COMPOSE, PULL = "local-ai-llama-swap.service", "local-ai-compose.service", "local-ai-pull.service"
SECRETS = "/etc/local-ai/secrets"
KEY_LIST = "keys:/etc/local-ai/keys.yaml"  # the private key list (Task 4): private, not secret
VALUES = "-/etc/local-ai/values.env"  # the private values file: optional until Task 2, in Phase 5
PLACEHOLDER = "not-set-up"  # ntfy-token's default until Task 2 writes the headers: no header line, nothing secret
DRILL_COPY = "/opt/local-ai/etc/brake-drill.yaml"
# The units that read ntfy's files, each starting without them (2026-10-14, Dan's decision: ntfy optional, moving with
# the NAS phase).
NTFY_READERS = (GATE, NOTIFY, BRAKE, DRILL)
NOTIFIER_ON = (LLAMA_SWAP, BRAKE, FRONT, GATE)  # the four services whose failure starts the failure notifier
# Where this account's systemd user manager listens: XDG_RUNTIME_DIR as the run was given it, read at import, before
# conftest clears the environment, or else systemd's own default. The Mac has no manager, and GitHub's runners are
# expected to have none; the tests that need one then skip.
RUNTIME_DIR = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
# The settings whose command systemd-analyze checks is there to run.
EXEC = re.compile(r"Exec(Start|StartPre|StartPost|Condition|Reload|Stop|StopPost)")
CREDENTIALS = {
    FRONT: [f"client-keys:{SECRETS}/client-keys.sha256", f"llamaswap-key:{SECRETS}/front.key", KEY_LIST],
    GATE: [f"llamaswap-key:{SECRETS}/gate.key", f"ntfy-token:{SECRETS}/ntfy-gate.header", KEY_LIST],
    NOTIFY: [f"ntfy-token:{SECRETS}/ntfy-notify.header"],
    BRAKE: [f"llamaswap-key:{SECRETS}/brake.key", f"ntfy-token:{SECRETS}/ntfy-brake.header"],
    DRILL: [f"llamaswap-key:{SECRETS}/brake.key", f"ntfy-token:{SECRETS}/ntfy-brake.header"],
}


def rendered(registry_path: Path = FIX / "models.yaml", templates: Path = ROOT / "stack/templates") -> dict[str, str]:
    """Every unit render writes, by its name: the registry at `registry_path` with the fixture's versions."""
    files = render.render(load_registry(registry_path), load_versions(FIX / "versions.yaml"),
                          Path(registry_path).read_text(), templates=templates)
    return {rel.removeprefix("systemd/"): text for rel, text in files.items() if rel.startswith("systemd/")}


def real() -> dict[str, str]:
    """What the real registry and versions render."""
    files = render.render(load_registry(ROOT / "stack/models.yaml"), load_versions(ROOT / "stack/versions.yaml"),
                          (ROOT / "stack/models.yaml").read_text(), templates=ROOT / "stack/templates")
    return {rel.removeprefix("systemd/"): text for rel, text in files.items() if rel.startswith("systemd/")}


@pytest.fixture(params=["fixture", "real"])
def units(request) -> dict[str, str]:
    return rendered() if request.param == "fixture" else real()


def sections(text: str) -> dict[str, list[tuple[str, str]]]:
    """A unit's settings by section, each (name, value) in order; comments and blank lines left out."""
    found: dict[str, list[tuple[str, str]]] = {}
    current = None
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            found[current] = []
            continue
        name, equals, value = line.partition("=")
        assert equals and current is not None, f"not a setting in a section: {line!r}"
        found[current].append((name, value))
    return found


def settings(text: str, section: str) -> dict[str, list[str]]:
    """A unit's settings in one section, each name with every value it's given, in order."""
    found: dict[str, list[str]] = {}
    for name, value in sections(text).get(section, []):
        found.setdefault(name, []).append(value)
    return found


def words(values: list[str]) -> list[str]:
    return " ".join(values).split()


def test_render_writes_the_eleven_units_and_the_polkit_rules_seven(units):
    assert render.UNITS == (LLAMA_SWAP, BRAKE, COMPOSE, PULL, FRONT_SOCKET, FRONT, STATUS_SOCKET, CONTROL_SOCKET,
                            GATE, NOTIFY, DRILL)
    assert list(units) == list(render.UNITS)
    # The services Dan starts, stops and restarts without sudo: no socket, and not the notifier, which only OnFailure=
    # starts.
    assert render.POLKIT_UNITS == (LLAMA_SWAP, BRAKE, COMPOSE, PULL, FRONT, GATE, DRILL)


def test_9100_is_held_by_a_socket_unit_that_never_gives_up(units):
    assert settings(units[FRONT_SOCKET], "Socket") == {
        "ListenStream": ["127.0.0.1:9100"], "FileDescriptorName": [sockets.FRONT], "NoDelay": ["yes"],
        "TriggerLimitIntervalSec": ["0"], "Service": [FRONT],
        # As on the front's service: systemd applies a service's list only to the sockets it makes itself, never to
        # one passed in (systemd.resource-control(5)), so the socket's own connections get the list here.
        "IPAddressDeny": ["any"], "IPAddressAllow": ["localhost"]}
    assert settings(units[FRONT_SOCKET], "Install") == {"WantedBy": ["sockets.target"]}


def test_the_front_gate_and_brake_never_give_up_restarting(units):
    for name in (FRONT, GATE, BRAKE):
        service, unit = settings(units[name], "Service"), settings(units[name], "Unit")
        assert (service["Restart"], service["RestartSec"]) == (["always"], ["2"]), name
        assert unit["StartLimitIntervalSec"] == ["0"], name
        assert not {"StartLimitBurst", "StartLimitAction"} & (set(unit) | set(service)), name


def test_the_gates_sockets_are_0660_with_their_owners_and_groups(units):
    # The control socket is root's, so only spark-admin's members connect, never anything running as spark (the
    # controller's ruling, 2026-10-14, at Task 25); the status socket stays spark's.
    for unit, path, name, owner in ((STATUS_SOCKET, "/run/local-ai/gate-status.sock", sockets.STATUS, "spark"),
                                    (CONTROL_SOCKET, "/run/local-ai/gate-control.sock", sockets.CONTROL, "root")):
        assert settings(units[unit], "Socket") == {
            "ListenStream": [path], "SocketUser": [owner], "SocketGroup": [gateproto.SOCKET_GROUPS[name]],
            "SocketMode": ["0660"], "FileDescriptorName": [name], "TriggerLimitIntervalSec": ["0"],
            "Service": [GATE]}, unit
        assert settings(units[unit], "Install") == {"WantedBy": ["sockets.target"]}, unit
    assert gateproto.SOCKET_GROUPS == {"status": "spark-users", "control": "spark-admin"}
    assert words(settings(units[GATE], "Service")["Sockets"]) == [STATUS_SOCKET, CONTROL_SOCKET]


WHERE_THEY_LOOK = """
from spark import paths
print(paths.FRONT_URL, paths.GATE_STATUS_SOCKET, paths.GATE_CONTROL_SOCKET)
"""


def test_the_sockets_are_where_their_callers_look(tmp_path):
    # The CLI (gateclient), the front and `spark brake --release` reach the gate at paths' defaults, which no unit
    # overrides, and paths.FRONT_URL names the front's address; render's units bind them. paths reads its environment at
    # import, so a process of its own, without the variables, reads the defaults.
    env = {name: value for name, value in os.environ.items() if not name.startswith("SPARK_")}
    result = subprocess.run([sys.executable, "-c", WHERE_THEY_LOOK], cwd=tmp_path, env=env, capture_output=True,
                            text=True, timeout=60, check=False)
    assert result.returncode == 0, result.stderr
    front, status, control = result.stdout.split()
    units = rendered()
    assert front == f"http://{settings(units[FRONT_SOCKET], 'Socket')['ListenStream'][0]}"
    assert [status, control] == [settings(units[name], "Socket")["ListenStream"][0]
                                 for name in (STATUS_SOCKET, CONTROL_SOCKET)]


def test_the_front_only_wants_llama_swap_never_requires_it(units):
    unit = settings(units[FRONT], "Unit")
    assert LLAMA_SWAP in words(unit["Wants"]) and LLAMA_SWAP in words(unit["After"])
    # Nor the gate: a front whose gate is down still answers, with gate_down (plan.md, *The front and the gate*).
    for strong in ("Requires", "Requisite", "BindsTo", "PartOf", "Upholds"):
        assert not {LLAMA_SWAP, GATE} & set(words(unit.get(strong, []))), strong
    assert words(unit["Requires"]) == [FRONT_SOCKET]
    assert STATUS_SOCKET in words(unit["After"])


def test_the_front_and_the_gate_are_notify_units_with_watchdogs(units):
    for name in (FRONT, GATE):
        service = settings(units[name], "Service")
        assert (service["Type"], service["WatchdogSec"]) == (["notify"], ["30"]), name


def test_the_front_reaches_only_localhost_and_spools_under_private_tmp(units):
    service = settings(units[FRONT], "Service")
    assert (service["IPAddressDeny"], service["IPAddressAllow"]) == (["any"], ["localhost"])
    assert (service["PrivateTmp"], service["MemoryMax"]) == (["yes"], ["512M"])
    assert words(service["RestrictAddressFamilies"]) == ["AF_INET", "AF_INET6", "AF_UNIX"]


FRONT_SANDBOX = {"NoNewPrivileges": ["yes"], "ProtectSystem": ["strict"], "ProtectHome": ["yes"], "PrivateTmp": ["yes"],
                 "PrivateDevices": ["yes"], "ProtectProc": ["invisible"], "CapabilityBoundingSet": [""]}


def test_the_front_is_sandboxed_with_no_capabilities_at_all(units):
    # An empty CapabilityBoundingSet= is the empty set (systemd.exec(5)); nothing it does needs one, and 9100 is the
    # socket unit's.
    service = settings(units[FRONT], "Service")
    assert {name: service.get(name) for name in FRONT_SANDBOX} == FRONT_SANDBOX
    assert "AmbientCapabilities" not in service
    # Writable: only the usage folder, and the - lets a missing one pass; its rows are Dan's to read (2750), not the
    # world's (Task 44f).
    assert words(service["ReadWritePaths"]) == ["-/var/lib/local-ai/usage"]
    assert service["UMask"] == ["0027"]


def test_the_front_runs_as_spark_front_in_spark_users(units):
    service = settings(units[FRONT], "Service")
    assert (service["User"], service["Group"], service["SupplementaryGroups"]) == (
        ["spark-front"], ["spark-front"], ["spark-users"])
    assert service["ExecStart"] == [f"{SPARK_BIN} front"]
    assert service["Environment"] == ["SPARK_REGISTRY=/opt/local-ai/etc/models.yaml",
                                      f"SPARK_LLAMASWAP_URL=http://{render.LLAMASWAP_LISTEN}"]


def test_keys_and_tokens_arrive_by_loadcredential_never_the_environment(units):
    from spark.brake import NTFY_TOKEN
    from spark.front.app import DIGESTS_CREDENTIAL, KEYS_CREDENTIAL
    from spark.front.main import LLAMASWAP_KEY_CREDENTIAL

    for name, expected in CREDENTIALS.items():
        service = settings(units[name], "Service")
        assert service["LoadCredential"] == expected, name
        assert [f for f in service.get("EnvironmentFile", []) if f.lstrip("-").startswith(f"{SECRETS}/")] == [], name
        assert not [e for e in service.get("Environment", []) if re.search(r"KEY|TOKEN", e.split("=", 1)[0])], name
    # The names the code reads them by.
    assert {c.split(":", 1)[0] for c in CREDENTIALS[FRONT]} == {DIGESTS_CREDENTIAL, LLAMASWAP_KEY_CREDENTIAL,
                                                               KEYS_CREDENTIAL}
    # The gate names its three in its calls: each read is matched as a call, by the reader and the name it passes.
    readers = {"read_credential", "read_credential_text", "read_header_credential"}
    reads = {(call.func.id, call.args[0].value)
             for call in ast.walk(ast.parse((ROOT / "spark/src/spark/gate/main.py").read_text()))
             if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id in readers
             and call.args and isinstance(call.args[0], ast.Constant)}
    assert reads == {("read_credential", "llamaswap-key"), ("read_header_credential", "ntfy-token"),
                     ("read_credential_text", "keys")}
    assert {name for _, name in reads} == {c.split(":", 1)[0] for c in CREDENTIALS[GATE]}
    assert NTFY_TOKEN == "ntfy-token"
    assert words(settings(units[BRAKE], "Service")["ExecStart"])[-2:] == ["--key-credential", "llamaswap-key"]


def test_only_llama_swap_reads_internal_keys_env(units):
    assert [name for name, text in units.items() if "internal-keys.env" in text] == [LLAMA_SWAP]
    assert settings(units[LLAMA_SWAP], "Service")["EnvironmentFile"] == [f"{SECRETS}/internal-keys.env"]
    # Phase 1's file, with the client keys and LLAMASWAP_KEY_SPARK, stays only for the rollback (Task 51).
    assert [name for name, text in units.items() if "llama-swap.env" in text or "LLAMASWAP_KEY_SPARK" in text] == []


def test_the_gate_keeps_dev_nvidia_and_other_processes_visible(units):
    # nvidia-smi needs the GPU's device nodes, and the gate reads the engines' and Dan's processes in /proc.
    service = settings(units[GATE], "Service")
    assert not {"PrivateDevices", "ProtectProc", "ProcSubset", "DevicePolicy", "DeviceAllow"} & set(service)


def test_the_gate_runs_as_spark_and_writes_only_its_four_folders(units):
    service = settings(units[GATE], "Service")
    assert (service["User"], service["Group"], service["ExecStart"]) == (["spark"], ["spark"], [f"{SPARK_BIN} gate"])
    assert {name: service[name] for name in ("NoNewPrivileges", "ProtectSystem", "ProtectHome", "PrivateTmp")} == {
        "NoNewPrivileges": ["yes"], "ProtectSystem": ["strict"], "ProtectHome": ["yes"], "PrivateTmp": ["yes"]}
    assert words(service["ReadWritePaths"]) == ["/var/lib/local-ai/gate", "/var/lib/local-ai/launch",
                                                "/var/lib/local-ai/brake",
                                                "-/var/lib/local-ai/gate-ledger"]
    assert service["Environment"] == [
        "SPARK_REGISTRY=/opt/local-ai/etc/models.yaml", "SPARK_STATE=/var/lib/local-ai/brake",
        "SPARK_GATE_STATE=/var/lib/local-ai/gate", "SPARK_LAUNCH=/var/lib/local-ai/launch",
        f"SPARK_LLAMASWAP_URL=http://{render.LLAMASWAP_LISTEN}", f"HF_HOME={render.HF_HOME}"]
    assert settings(units[GATE], "Unit")["After"] == [LLAMA_SWAP]


def test_onfailure_is_on_the_four_services(units):
    with_it = [name for name, text in units.items() if "OnFailure" in settings(text, "Unit")]
    assert with_it == list(NOTIFIER_ON)  # not compose, the pull, the drill, a socket or the notifier itself
    for name in with_it:
        assert settings(units[name], "Unit")["OnFailure"] == ["local-ai-notify@%n.service"], name
    assert not [name for name, text in units.items() if "OnFailure" in settings(text, "Service")]


# What the drill runs differently from the brake; every other line of the brake's [Service] is the drill's too.
DRILLS_OWN = {"Type", "ExecStart", "Restart", "RestartSec", "TimeoutStartSec"}


def test_the_drill_unit_is_the_brakes_but_once_on_the_drill_copy(units):
    brake, drill = settings(units[BRAKE], "Service"), settings(units[DRILL], "Service")
    for name in ("User", "Group", "LoadCredential", "SetCredential", "EnvironmentFile"):
        assert drill[name] == brake[name], name
    # The sandbox (none in 2a, which leaves the brake's unplaced: plan.md, Phase 2a), the environment but
    # SPARK_REGISTRY, and anything the brake gains later: the same lines.
    shared = {name: values for name, values in brake.items() if name not in DRILLS_OWN}
    assert {name: values for name, values in drill.items() if name not in DRILLS_OWN} == {
        **shared, "Environment": [f"SPARK_REGISTRY={DRILL_COPY}", *(
            e for e in shared["Environment"] if not e.startswith("SPARK_REGISTRY="))]}
    assert drill["Type"] == ["oneshot"]
    assert drill["ExecStart"] == [f"{SPARK_BIN} brake --once --key-credential llamaswap-key"]
    # A oneshot has no start timeout by default (systemd.service(5)): its --once run's alert wait (brake.ALERT_WAIT_S)
    # and its bounded calls finish well inside one minute.
    from spark.brake import ALERT_WAIT_S

    assert drill["TimeoutStartSec"] == ["60"] and ALERT_WAIT_S < 60
    assert settings(units[DRILL], "Unit")["ConditionPathExists"] == [DRILL_COPY]
    assert not {"Restart", "RestartSec"} & set(drill)
    assert "OnFailure" not in settings(units[DRILL], "Unit") and "Install" not in sections(units[DRILL])


def test_a_line_the_brake_gains_reaches_the_drill(tmp_path):
    # Rendered from the brake's own lines, so the two can't drift: one added to the brake's template is the drill's.
    templates = tmp_path / "templates"
    shutil.copytree(ROOT / "stack/templates", templates)
    shutil.copytree(ROOT / "stack/llama-swap", tmp_path / "llama-swap")
    brake = templates / BRAKE
    assert brake.read_text().endswith("OOMScoreAdjust=-900\n\n[Install]\nWantedBy=multi-user.target\n")
    brake.write_text(brake.read_text().replace("OOMScoreAdjust=-900\n", "OOMScoreAdjust=-900\nProtectHome=yes\n"))
    units = rendered(templates=templates)
    assert settings(units[DRILL], "Service")["ProtectHome"] == ["yes"]
    assert settings(units[BRAKE], "Service")["ProtectHome"] == ["yes"]


def test_graceful_shutdown_fits_inside_timeout_stop_sec(units):
    from spark.front.app import ENDING_S
    from spark.front.main import ROWS_DRAIN_S
    from spark.gate.app import FINISH_WORK_S
    from spark.gate.main import LEDGER_DRAIN_S

    front = int(settings(units[FRONT], "Service")["TimeoutStopSec"][0])
    gate = int(settings(units[GATE], "Service")["TimeoutStopSec"][0])
    assert front == 30 and front > gateproto.GRACEFUL_S
    # The front's stop at worst (Task 44's ruling R5): uvicorn's grace, the requests still ending (ENDING_S), then
    # the rows still queued (ROWS_DRAIN_S).
    front_worst = gateproto.GRACEFUL_S + ENDING_S + ROWS_DRAIN_S
    assert front_worst == 27 and front >= front_worst + 3
    # The gate's stop at worst (Task 19's review, the controller's ruling): uvicorn's grace, then the bounded routes'
    # work still running past its 503, then ntfy's last publish, then its ledger's last rows (LEDGER_DRAIN_S).
    worst = gateproto.GRACEFUL_S + FINISH_WORK_S + gateproto.NTFY_TIMEOUT_S + LEDGER_DRAIN_S
    assert worst == 29 and gate == 45 and gate >= worst + 10
    for main in ("front/main.py", "gate/main.py"):  # both mains pass GRACEFUL_S to uvicorn
        assert "graceful_s=GRACEFUL_S" in (ROOT / "spark/src/spark" / main).read_text(), main


def test_the_new_services_score_minus_900(units):
    for name in (FRONT, GATE, BRAKE):
        assert settings(units[name], "Service")["OOMScoreAdjust"] == ["-900"], name


NOTIFIER_SANDBOX = {"NoNewPrivileges": ["yes"], "ProtectSystem": ["strict"], "ProtectHome": ["yes"],
                    "PrivateTmp": ["yes"], "PrivateDevices": ["yes"]}


def test_the_notifier_runs_ubuntus_curl_as_a_dynamic_user(units):
    # Ubuntu's curl is the script's (Task 26), outside the app's venv; the unit runs the script as a user of its own.
    service = settings(units[NOTIFY], "Service")
    assert (service["Type"], service["DynamicUser"], service["StateDirectory"]) == (
        ["oneshot"], ["yes"], ["local-ai-notify"])
    assert service["ExecStart"] == ["/usr/local/libexec/local-ai-notify %i"]
    # A oneshot has no start timeout by default (systemd.service(5)), and a hung run would take in every later
    # OnFailure= start for its unit: 30 s, above curl's --max-time 10 (Task 26).
    assert service["TimeoutStartSec"] == ["30"]
    assert {name: service.get(name) for name in NOTIFIER_SANDBOX} == NOTIFIER_SANDBOX
    assert not {"User", "Group", "SupplementaryGroups"} & set(service)
    assert "Install" not in sections(units[NOTIFY])


def notifier_env(tmp_path, **priorities: str) -> list[str]:
    data = yaml.safe_load((FIX / "models.yaml").read_text())
    data["notifications"].update(priorities)
    path = tmp_path / "models.yaml"
    path.write_text(yaml.safe_dump(data))
    return words(settings(rendered(path)[NOTIFY], "Service")["Environment"])


def test_the_notifier_carries_the_registrys_priorities(tmp_path):
    from spark.docs import NOTIFIER_TYPES

    assert notifier_env(tmp_path, front_down="off") == [
        "NOTIFY_FRONT=off", "NOTIFY_GATE=high", "NOTIFY_BRAKE=high", "NOTIFY_LLAMA_SWAP=high"]
    assert notifier_env(tmp_path, front_down="off", gate_down="low", brake_down="default", llama_swap_down="high") == [
        "NOTIFY_FRONT=off", "NOTIFY_GATE=low", "NOTIFY_BRAKE=default", "NOTIFY_LLAMA_SWAP=high"]
    # The four the notifications page says need `make install-units` after `make apply`.
    assert set(render.NOTIFIER_PRIORITIES.values()) == set(NOTIFIER_TYPES)


def test_the_pull_runs_as_spark_pull_with_umask_0027(units):
    # Files 0640 and folders 0750 in the cache spark-pull owns, whose setgid group, spark, reads them (Task 27).
    service = settings(units[PULL], "Service")
    assert (service["User"], service["Group"], service["UMask"]) == (["spark-pull"], ["spark-pull"], ["0027"])
    assert "XDG_CACHE_HOME=/var/lib/local-ai/pull-cache" in service["Environment"]
    assert not [e for e in service["Environment"] if e.startswith("CUDA_CACHE_PATH=")]


def test_the_brake_reads_its_key_by_credential(units):
    service, unit = settings(units[BRAKE], "Service"), settings(units[BRAKE], "Unit")
    assert service["ExecStart"] == [f"{SPARK_BIN} brake --key-credential llamaswap-key"]
    assert service["LoadCredential"][0] == f"llamaswap-key:{SECRETS}/brake.key"
    assert service["EnvironmentFile"] == [VALUES]
    assert service["Environment"] == [
        "SPARK_REGISTRY=/opt/local-ai/etc/models.yaml", "SPARK_STATE=/var/lib/local-ai/brake",
        "SPARK_GATE_STATE=/var/lib/local-ai/gate", f"SPARK_LLAMASWAP_URL=http://{render.LLAMASWAP_LISTEN}"]
    assert (unit["StartLimitIntervalSec"], unit["OnFailure"]) == (["0"], ["local-ai-notify@%n.service"])
    assert "--key-env" not in units[BRAKE]


def test_ntfys_files_are_optional_to_every_unit_that_reads_them(units):
    for name in NTFY_READERS:
        service = sections(units[name])["Service"]
        assert [value for setting, value in service if setting == "EnvironmentFile"] == [VALUES], name
        for i, (setting, value) in enumerate(service):
            if setting == "LoadCredential" and value.startswith("ntfy-token:"):
                assert service[i + 1] == ("SetCredential", f"ntfy-token:{PLACEHOLDER}"), name
        set_credentials = [value for setting, value in service if setting == "SetCredential"]
        assert set_credentials == [f"ntfy-token:{PLACEHOLDER}"], name  # never empty, never another credential
    # A key, the key list and the digests stay required: only ntfy's token has a default.
    assert [name for name, text in units.items() if "SetCredential=" in text] == [
        name for name in render.UNITS if name in NTFY_READERS]
    assert [name for name, text in units.items() if "EnvironmentFile=/etc/local-ai/values.env" in text] == []


# The stand-in each transient unit runs: whether ntfy-token exists and its size, and whether NTFY_URL is set. Never a
# value.
STAND_IN = """#!/bin/sh
if [ -n "${CREDENTIALS_DIRECTORY:-}" ] && [ -e "$CREDENTIALS_DIRECTORY/ntfy-token" ]; then
  echo "ntfy-token $(wc -c < "$CREDENTIALS_DIRECTORY/ntfy-token")"
else
  echo "ntfy-token missing"
fi
if [ -n "${NTFY_URL:-}" ]; then echo "NTFY_URL set"; else echo "NTFY_URL unset"; fi
"""
STAND_IN_HEADER = "Authorization: Bearer stand-in-not-a-token\n"
FILE_SETTINGS = ("EnvironmentFile", "LoadCredential", "SetCredential")


# Each credential's size, as a transient unit sees it: the user manager's check that it runs units the way the tests
# need. Never a value.
CREDENTIAL_PROBE = """#!/bin/sh
for name in given loaded; do
  if [ -e "$CREDENTIALS_DIRECTORY/$name" ]; then echo "$name $(wc -c < "$CREDENTIALS_DIRECTORY/$name")"
  else echo "$name missing"; fi
done
"""


def first_line(text: str) -> str:
    return (text.strip().splitlines() or ["no output"])[0][:200]


def user_manager(tmp_path: Path) -> dict[str, str]:
    """The environment that reaches this account's systemd user manager, once a transient unit there runs with a
    credential of each kind, as on the Spark. Otherwise the test skips, saying why, and never fails: no systemd-run
    (the Mac), no manager answering at RUNTIME_DIR (expected on GitHub's runners), or a manager that can't run such a
    unit. Built, not copied: PATH, and the runtime folder systemd finds the manager in."""
    for tool in ("systemd-run", "systemctl"):
        if shutil.which(tool) is None:
            pytest.skip(f"no {tool} on PATH (the Mac): transient user units can't run here")
    env = {"PATH": os.environ["PATH"], "XDG_RUNTIME_DIR": RUNTIME_DIR}
    try:
        answer = subprocess.run(["systemctl", "--user", "show", "--property=Version", "--value"], env=env,
                                capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as err:
        pytest.skip(f"systemctl --user couldn't ask a user manager at {RUNTIME_DIR}: {err}")
    if answer.returncode != 0 or not answer.stdout.strip():
        pytest.skip(f"no systemd user manager answers at {RUNTIME_DIR}, as expected on GitHub's runners: "
                    f"{first_line(answer.stderr)}")
    probe = tmp_path / "probe"
    probe.mkdir()
    (probe / "run").write_text(CREDENTIAL_PROBE)
    (probe / "run").chmod(0o755)
    (probe / "loaded").write_text("12345\n")
    try:
        run = subprocess.run(["systemd-run", "--user", "--wait", "--pipe", "--quiet", "--collect",
                              "-p", "RuntimeMaxSec=30", "-p", "SetCredential=given:abc",
                              "-p", f"LoadCredential=loaded:{probe}/loaded", "--", str(probe / "run")],
                             env=env, capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as err:
        pytest.skip(f"a transient user unit couldn't run here: {err}")
    if (run.returncode, run.stdout) != (0, "given 3\nloaded 6\n"):
        pytest.skip(f"this user manager can't run a transient unit with credentials (exit {run.returncode}): "
                    f"{first_line(run.stderr or run.stdout)}")
    return env


def usable_systemd_analyze(tmp_path: Path) -> list[str]:
    """`systemd-analyze verify`, as the test runs it, once it passes a unit with nothing wrong. Otherwise the test
    skips, saying why, and never fails: no systemd-analyze (the Mac), or one that can't verify a unit here, such as in
    a container without systemd. --recursive-errors=no keeps to the named units: without it, verify also reports the
    machine's own units they name (its docker.service's warnings, a Requires= target it lacks), which aren't render's
    (seen on the Spark, 2026-10-14)."""
    if shutil.which("systemd-analyze") is None:
        pytest.skip("no systemd-analyze on PATH (the Mac)")
    verify = ["systemd-analyze", "verify", "--recursive-errors=no"]
    probe = tmp_path / "probe" / "local-ai-probe.service"
    probe.parent.mkdir()
    probe.write_text(f"[Unit]\nDescription=probe\n\n[Service]\nType=oneshot\nExecStart={sys.executable} -c pass\n")
    try:
        run = subprocess.run([*verify, str(probe)], env={"PATH": os.environ["PATH"]}, capture_output=True, text=True,
                             timeout=120, check=False)
    except (OSError, subprocess.TimeoutExpired) as err:
        pytest.skip(f"systemd-analyze couldn't run here: {err}")
    if run.returncode != 0 or (run.stdout + run.stderr).strip():
        pytest.skip(f"systemd-analyze verify doesn't pass a sound unit here (exit {run.returncode}): "
                    f"{first_line(run.stderr or run.stdout)}")
    return verify


def moved(value: str, root: Path) -> str:
    """A setting's value with its path under /etc/local-ai moved under `root`."""
    return value.replace("/etc/local-ai/", f"{root}/etc/local-ai/")


def run_transient(env: dict[str, str], unit_text: str, root: Path) -> subprocess.CompletedProcess[str]:
    """The stand-in, as a transient user unit with the unit's values-file and credential lines, its paths under `root`:
    waited for, and unloaded whatever its end (--collect), within RuntimeMaxSec."""
    script = root / "stand-in"
    if not script.exists():
        script.write_text(STAND_IN)
        script.chmod(0o755)
    props = [arg for name, value in sections(unit_text)["Service"] if name in FILE_SETTINGS
             for arg in ("-p", f"{name}={moved(value, root)}")]
    return subprocess.run(["systemd-run", "--user", "--wait", "--pipe", "--quiet", "--collect",
                           "-p", "RuntimeMaxSec=30", *props, "--", str(script)],
                          env=env, capture_output=True, text=True, timeout=60, check=False)


def stand_in_credentials(unit_text: str, root: Path, *, ntfy: bool, skip: str | None = None) -> None:
    """Stand-in files, holding nothing secret, for each credential the unit loads, `skip` left out; ntfy's header and
    the values file only when `ntfy`."""
    for name, value in sections(unit_text)["Service"]:
        if name != "LoadCredential":
            continue
        credential, path = value.split(":", 1)
        if credential == skip or (credential == "ntfy-token" and not ntfy):
            continue
        target = Path(moved(path, root))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(STAND_IN_HEADER if credential == "ntfy-token" else f"stand-in for {credential}\n")
    if ntfy:
        (root / "etc/local-ai").mkdir(parents=True, exist_ok=True)
        (root / "etc/local-ai/values.env").write_text("NTFY_URL=http://ntfy.invalid\n")


@pytest.mark.parametrize("name", NTFY_READERS)
def test_each_unit_renders_a_form_that_starts_without_ntfys_files(tmp_path, name):
    env, text = user_manager(tmp_path), rendered()[name]
    without, with_files = tmp_path / "without", tmp_path / "with"
    for root, ntfy in ((without, False), (with_files, True)):
        root.mkdir()
        stand_in_credentials(text, root, ntfy=ntfy)
    assert not (without / "etc/local-ai/values.env").exists() and not list(without.rglob("*.header"))
    run = run_transient(env, text, without)
    assert (run.returncode, run.stdout) == (0, f"ntfy-token {len(PLACEHOLDER)}\nNTFY_URL unset\n"), run.stderr
    assert len(PLACEHOLDER) == 10
    run = run_transient(env, text, with_files)
    assert (run.returncode, run.stdout) == (0, f"ntfy-token {len(STAND_IN_HEADER)}\nNTFY_URL set\n"), run.stderr


@pytest.mark.parametrize("name", [GATE, BRAKE, DRILL])
def test_a_unit_without_its_key_still_doesnt_start(tmp_path, name):
    # Only ntfy's token has a default: without the key llama-swap takes, the unit fails to start (systemd's 243,
    # CREDENTIALS), as it would without the key list.
    env, text = user_manager(tmp_path), rendered()[name]
    stand_in_credentials(text, tmp_path, ntfy=False, skip="llamaswap-key")
    run = run_transient(env, text, tmp_path)
    assert run.returncode == 243 and run.stdout == "", (run.returncode, run.stdout, run.stderr)


def test_every_rendered_unit_passes_systemd_analyze_verify(tmp_path, units):
    verify = usable_systemd_analyze(tmp_path)
    for name, text in units.items():
        (tmp_path / name).write_text(text)
    run = subprocess.run([*verify, *(str(tmp_path / name) for name in units)], env={"PATH": os.environ["PATH"]},
                         capture_output=True, text=True, timeout=120, check=False)
    # The one complaint allowed: a binary this machine lacks, in any command a unit runs (the deployed app off the
    # Spark, the notifier's script until Task 27 installs it, docker where it isn't installed). systemd 255's verify
    # says nothing of a user or a group that doesn't exist.
    binaries = {value.lstrip("-@:+!").split()[0] for text in units.values() for section in sections(text).values()
                for name, value in section if EXEC.fullmatch(name)}
    missing = re.compile(r"^\S+: Command (\S+) is not executable: No such file or directory$")
    complaints = [line for line in (run.stdout + run.stderr).splitlines() if line.strip()]
    for line in complaints:
        found = missing.match(line)
        assert found and found[1] in binaries and not Path(found[1]).exists(), line
    assert run.returncode == 0 or complaints, run

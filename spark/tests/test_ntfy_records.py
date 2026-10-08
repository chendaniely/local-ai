"""ntfy's records: the Compose file that deploys it on the Synology, and the list of its variables.

The file is public, so everything private (the NAS's address, the topic names, the users' hashes, the tokens and the
timezone) arrives as a variable its Compose helper sets; each is required, so a missing one stops the deploy and names
itself. It follows Marius Hosting's Synology ntfy guide in its container name, folders, healthcheck, TZ and port, and
keeps the login, the tailnet-only path and the pin (Dan's decision, 2026-10-08), with `unless-stopped` for its restart.
"""

import os
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml

from spark.leakcheck import scan_text
from spark.versions import load_versions

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "stack/synology/ntfy/compose.yaml"
EXAMPLE = ROOT / "stack/synology/ntfy/variables.example"
RUNBOOK = ROOT / "website/how-to/ntfy.md"
VARIABLES = {"NTFY_BASE_URL", "NTFY_AUTH_USERS", "NTFY_AUTH_ACCESS", "NTFY_AUTH_TOKENS", "NTFY_DATA_DIR",
             "NTFY_PORT", "NTFY_TZ"}
# Marius Hosting's healthcheck, byte for byte as his guide and ntfy v2.28.0's docs/install.md give it.
HEALTHCHECK = {
    "test": ["CMD-SHELL", "wget -q --tries=1 http://localhost:80/v1/health -O - "
                          "| grep -Eo '\"healthy\"\\s*:\\s*true' || exit 1"],
    "interval": "60s",
    "timeout": "10s",
    "retries": 3,
    "start_period": "40s",
}
# Stand-ins, shaped like the real values: a bcrypt hash whose `$` is followed by a letter, which Compose expands
# unless the value is single-quoted, and a token. Neither is real. The timezone is one that names no place.
HASH = "$2a$10$" + "standinstandinstandin." + "standinstandinstandinstandinsta"  # 22-character salt, 31 of hash
STAND_INS = {
    "NTFY_BASE_URL": "http://nas.example.invalid:6741",
    "NTFY_AUTH_USERS": f"'spark-gate:{HASH}:user'",
    "NTFY_AUTH_ACCESS": "spark-gate:standin-topic:wo",
    "NTFY_AUTH_TOKENS": "spark-gate:tk_standin",
    "NTFY_DATA_DIR": "/volume1/docker/ntfy",
    "NTFY_PORT": "6741",
    "NTFY_TZ": "Etc/UTC",
}


def service() -> dict:
    return yaml.safe_load(COMPOSE.read_text())["services"]["ntfy"]


def test_ntfy_compose_pins_the_versions_digest():
    c = load_versions(ROOT / "stack/versions.yaml")["ntfy"]
    assert service()["image"] == f"{c.image}:{c.version}@{c.pin}"
    assert service()["image"] == ("docker.io/binwiederhier/ntfy:v2.28.0"
                                  "@sha256:6ef4b819f722fccdc036af611c4774cfdc2de821ab74fdd48bbf4c9d6f8973da")


def test_ntfy_compose_holds_no_private_value():
    for path in (COMPOSE, EXAMPLE):
        text = path.read_text()
        assert scan_text(path.name, text, []) == [], path.name
        assert "tk_" not in text and "$2a$" not in text, path.name
    # Every reference Compose would fill in is one of the seven, required: no default, TZ's included, and no bare
    # ${NAME} or $NAME.
    required = {"${%s:?set %s}" % (name, name) for name in VARIABLES}
    references = re.findall(r"\$\{[^}]*\}|\$[A-Za-z_][A-Za-z0-9_]*", COMPOSE.read_text())
    assert set(references) == required


def test_every_variable_is_named_in_the_example():
    in_compose = set(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)", COMPOSE.read_text()))
    lines = [line for line in EXAMPLE.read_text().splitlines() if line.strip() and not line.startswith("#")]
    for line in lines:
        assert re.fullmatch(r"[A-Z][A-Z0-9_]*=<[^<>]+>", line), line
    assert in_compose == {line.split("=", 1)[0] for line in lines} == VARIABLES


def test_ntfy_denies_by_default_and_reaches_no_relay():
    ntfy = service()
    assert ntfy["command"] == "serve"
    # Never Marius's on-failure:5: Docker doesn't restart an on-failure container when its daemon restarts, so a
    # NAS reboot or a DSM update would leave ntfy down (the controller's ruling, 2026-10-08).
    assert ntfy["restart"] == "unless-stopped"
    environment = ntfy["environment"]
    assert environment["NTFY_AUTH_DEFAULT_ACCESS"] == "deny-all"
    assert environment["NTFY_BEHIND_PROXY"] == "false"
    assert not [key for key in environment if re.search("UPSTREAM|FIREBASE|WEB_PUSH|ATTACHMENT", key)]
    # An env_file, or a folder at /etc/ntfy where a server.yml would be read, would bring in settings this file
    # doesn't show, a relay or a Firebase key among them.
    assert "env_file" not in ntfy
    assert not [volume for volume in ntfy["volumes"] if volume.split(":")[-1].startswith("/etc/ntfy")]


def test_ntfy_follows_marius_hostings_synology_layout():
    # Dan's decision, 2026-10-08: his container name, his folder with its cache subfolder at ntfy's cache path,
    # his healthcheck and TZ; the port stays a variable, and the auth database stays where Task 1 put it.
    ntfy = service()
    assert ntfy["container_name"] == "ntfy"
    data = "${NTFY_DATA_DIR:?set NTFY_DATA_DIR}"
    assert ntfy["volumes"] == [f"{data}:/var/lib/ntfy", f"{data}/cache:/var/cache/ntfy"]
    environment = ntfy["environment"]
    assert environment["NTFY_AUTH_FILE"] == "/var/lib/ntfy/user.db"
    assert environment["NTFY_CACHE_FILE"] == "/var/cache/ntfy/cache.db"
    assert environment["TZ"] == "${NTFY_TZ:?set NTFY_TZ}"
    assert ntfy["healthcheck"] == HEALTHCHECK
    assert ntfy["ports"] == ["${NTFY_PORT:?set NTFY_PORT}:80"]


def compose_config(env_file: Path) -> subprocess.CompletedProcess:
    # --env-file always: Compose would otherwise read a .env beside the file, which on a real deploy holds values.
    # And no NTFY_ variable from the runner's environment, which Compose would take over the file's.
    env = {k: v for k, v in os.environ.items() if not k.startswith("NTFY_")}
    return subprocess.run(["docker", "compose", "-f", str(COMPOSE), "--env-file", str(env_file), "config"],
                          capture_output=True, text=True, timeout=60, env=env)


def write_stand_ins(path: Path, leave_out: str | None = None) -> Path:
    path.write_text("".join(f"{k}={v}\n" for k, v in STAND_INS.items() if k != leave_out))
    return path


def has_docker_compose() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "compose", "version"], capture_output=True).returncode == 0


@pytest.mark.skipif(not has_docker_compose(), reason="docker compose not installed")
def test_docker_compose_reads_the_file(tmp_path, monkeypatch):
    # A variable in the runner's own environment would satisfy ${NAME:?…} and hide the one left out, so the runner
    # here holds all seven, and compose_config must not hand them on.
    for name in VARIABLES:
        monkeypatch.setenv(name, "from-the-runner")
    done = compose_config(write_stand_ins(tmp_path / "stand-ins"))
    assert done.returncode == 0, done.stderr
    assert HASH.replace("$", "$$") in done.stdout  # the single-quoted hash arrives whole, its `$` escaped as `$$`
    # The folder and its cache subfolder, as Compose resolves them, and the healthcheck as Docker will get it.
    resolved = yaml.safe_load(done.stdout)["services"]["ntfy"]
    assert [(v["source"], v["target"]) for v in resolved["volumes"]] == [
        ("/volume1/docker/ntfy", "/var/lib/ntfy"), ("/volume1/docker/ntfy/cache", "/var/cache/ntfy")]
    assert resolved["healthcheck"]["test"] == HEALTHCHECK["test"]
    assert resolved["environment"]["TZ"] == "Etc/UTC"

    (tmp_path / "none").write_text("")
    done = compose_config(tmp_path / "none")
    assert done.returncode != 0
    # Which one Compose names first varies from run to run (it walks a Go map), so each is checked alone below.
    assert re.search(r"required variable (NTFY_[A-Z_]+) is missing a value: set \1", done.stderr), done.stderr

    for name in sorted(VARIABLES):
        done = compose_config(write_stand_ins(tmp_path / f"without-{name}", leave_out=name))
        assert done.returncode != 0, name
        assert f"required variable {name} is missing a value: set {name}" in done.stderr, done.stderr


def runbook_blocks() -> list[str]:
    return [textwrap.dedent(body) for body in
            re.findall(r"^ *```bash\n(.*?)^ *```$", RUNBOOK.read_text(), flags=re.S | re.M)]


def test_no_runbook_block_changes_your_shell(tmp_path):
    # Dan pastes these blocks into his own shell. One that left `umask 077` there would make the next runbook's
    # `make apply` deploy files only he can read, which the services then can't; one that left him in the working
    # folder would have §10 delete it from under him. So each block runs in a shell where every command looked up
    # on PATH is a no-op that succeeds (no PATH, and bash's command_not_found_handle), leaving only bash's own
    # builtins to act (cd, umask, read, printf), and the shell's umask and folder are read afterwards. A command
    # named by its path (`./ntfy`) still fails, as does `read` at end of input, so a block whose `umask` came
    # after one of those in an `&&` chain would get past this test: each block's `cd` and `umask` come first.
    bash = shutil.which("bash")
    (tmp_path / "ntfy-setup").mkdir()
    blocks = runbook_blocks()
    assert len(blocks) == 17
    changed = {}
    for block in blocks:
        script = ("command_not_found_handle() { return 0; }\numask 0002\ncd \"$HOME\"\n" + block
                  + 'printf "after: %s %s\\n" "$(umask)" "$PWD"\n')
        done = subprocess.run([bash, "-c", script], env={"PATH": "/nonexistent", "HOME": str(tmp_path)},
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
        after = done.stdout.splitlines()[-1:]
        if after != [f"after: 0002 {tmp_path}"]:
            changed[block.splitlines()[0][:60]] = after
    assert changed == {}

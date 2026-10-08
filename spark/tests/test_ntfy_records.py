"""ntfy's records: the Compose file that deploys it on the Synology, and the list of its variables.

The file is public, so everything private (the NAS's address, the topic names, the users' hashes and the tokens)
arrives as a variable its Compose helper sets; each is required, so a missing one stops the deploy and names itself.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from spark.leakcheck import scan_text
from spark.versions import load_versions

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "stack/synology/ntfy/compose.yaml"
EXAMPLE = ROOT / "stack/synology/ntfy/variables.example"
VARIABLES = {"NTFY_BASE_URL", "NTFY_AUTH_USERS", "NTFY_AUTH_ACCESS", "NTFY_AUTH_TOKENS", "NTFY_DATA_DIR",
             "NTFY_PORT"}
# Stand-ins, shaped like the real values: a bcrypt hash whose `$` is followed by a letter, which Compose expands
# unless the value is single-quoted, and a token. Neither is real.
HASH = "$2a$10$" + "standinstandinstandin." + "standinstandinstandinstandinsta"  # 22-character salt, 31 of hash
STAND_INS = {
    "NTFY_BASE_URL": "http://nas.example.invalid:8090",
    "NTFY_AUTH_USERS": f"'spark-gate:{HASH}:user'",
    "NTFY_AUTH_ACCESS": "spark-gate:standin-topic:wo",
    "NTFY_AUTH_TOKENS": "spark-gate:tk_standin",
    "NTFY_DATA_DIR": "/volume1/docker/ntfy",
    "NTFY_PORT": "8090",
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
    # Every reference Compose would fill in is one of the six, required: no default, no bare ${NAME} or $NAME.
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
    assert ntfy["restart"] == "unless-stopped"
    environment = ntfy["environment"]
    assert environment["NTFY_AUTH_DEFAULT_ACCESS"] == "deny-all"
    assert environment["NTFY_BEHIND_PROXY"] == "false"
    assert not [key for key in environment if re.search("UPSTREAM|FIREBASE|WEB_PUSH", key)]


def compose_config(env_file: Path) -> subprocess.CompletedProcess:
    # --env-file always: Compose would otherwise read a .env beside the file, which on a real deploy holds values.
    return subprocess.run(["docker", "compose", "-f", str(COMPOSE), "--env-file", str(env_file), "config"],
                          capture_output=True, text=True, timeout=60)


def write_stand_ins(path: Path, leave_out: str | None = None) -> Path:
    path.write_text("".join(f"{k}={v}\n" for k, v in STAND_INS.items() if k != leave_out))
    return path


def has_docker_compose() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "compose", "version"], capture_output=True).returncode == 0


@pytest.mark.skipif(not has_docker_compose(), reason="docker compose not installed")
def test_docker_compose_reads_the_file(tmp_path):
    done = compose_config(write_stand_ins(tmp_path / "stand-ins"))
    assert done.returncode == 0, done.stderr
    assert HASH.replace("$", "$$") in done.stdout  # the single-quoted hash arrives whole, its `$` escaped as `$$`

    (tmp_path / "none").write_text("")
    done = compose_config(tmp_path / "none")
    assert done.returncode != 0
    # Which one Compose names first varies from run to run (it walks a Go map), so each is checked alone below.
    assert re.search(r"required variable (NTFY_[A-Z_]+) is missing a value: set \1", done.stderr), done.stderr

    for name in sorted(VARIABLES):
        done = compose_config(write_stand_ins(tmp_path / f"without-{name}", leave_out=name))
        assert done.returncode != 0, name
        assert f"required variable {name} is missing a value: set {name}" in done.stderr, done.stderr

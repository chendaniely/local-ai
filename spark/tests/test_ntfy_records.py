"""ntfy's records: the Compose file that deploys it on the Synology, and the shape of its private server.yml.

The Compose file is public and holds nothing private, so it is pasted as it is, with no variables. Everything
private (the NAS's address, the users' hashes, the topic names and the tokens) is in ntfy's server.yml on the NAS,
which the runbook writes on the Spark; server.example.yml shows its shape, and the settings that aren't private stay
in the Compose file, where the repo shows them. It follows Marius Hosting's Synology ntfy guide in its container
name, folders, server.yml, healthcheck and port, and keeps the login, the tailnet-only path and the pin, with
`unless-stopped` for its restart (Dan's decisions, 2026-10-08: his style, then his full server.yml).
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
EXAMPLE = ROOT / "stack/synology/ntfy/server.example.yml"
RUNBOOK = ROOT / "website/how-to/ntfy.md"
PRIVATE = ["base-url", "auth-users", "auth-access", "auth-tokens"]
PUBLISHERS = ["spark-gate", "spark-notify", "spark-brake"]
# Marius Hosting's healthcheck, byte for byte as his guide and ntfy v2.28.0's docs/install.md give it.
HEALTHCHECK = {
    "test": ["CMD-SHELL", "wget -q --tries=1 http://localhost:80/v1/health -O - "
                          "| grep -Eo '\"healthy\"\\s*:\\s*true' || exit 1"],
    "interval": "60s",
    "timeout": "10s",
    "retries": 3,
    "start_period": "40s",
}
# Stand-ins, shaped like the real values: a bcrypt hash, which holds `$`, and a token. Neither is real.
HASH = "$2a$10$" + "standinstandinstandin." + "standinstandinstandinstandinsta"  # 22-character salt, 31 of hash
TOKEN = "tk_" + "standinstandinstandinstandin1"  # 32 characters, as ntfy's are


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
    # No variable at all: the file is pasted as it is, and nothing private reaches it from a helper.
    assert re.findall(r"\$\{[^}]*\}|\$[A-Za-z_][A-Za-z0-9_]*", COMPOSE.read_text()) == []


def test_server_example_holds_only_the_private_settings():
    example = yaml.safe_load(EXAMPLE.read_text())
    assert list(example) == PRIVATE
    leaves = [example["base-url"], *example["auth-users"], *example["auth-access"], *example["auth-tokens"]]
    assert all(re.search(r"<[^<>]+>", leaf) for leaf in leaves), leaves
    assert [u.split(":")[0] for u in example["auth-users"]] == [*PUBLISHERS, "phone"]
    assert all(u.endswith(":user") for u in example["auth-users"])  # an admin reads and writes every topic
    access = [a.split(":") for a in example["auth-access"]]
    assert [(a[0], a[-1]) for a in access] == [(p, "wo") for p in PUBLISHERS] + [("phone", "ro")] * 3
    assert [t.split(":")[0] for t in example["auth-tokens"]] == PUBLISHERS


def runbook_blocks() -> list[str]:
    return [textwrap.dedent(body) for body in
            re.findall(r"^ *```bash\n(.*?)^ *```$", RUNBOOK.read_text(), flags=re.S | re.M)]


def test_the_runbook_writes_server_yml_in_the_examples_shape(tmp_path):
    # §3's step 5, run on stand-ins: the server.yml it writes has the example's settings, in its order and shape,
    # and nothing else; values.env and the header files are what §8 installs.
    [block] = [b for b in runbook_blocks() if "> server.yml" in b]
    setup = tmp_path / "ntfy-setup"
    setup.mkdir()
    (setup / "address").write_text("http://nas.example.invalid:6741\n")
    for user in [*PUBLISHERS, "phone"]:
        (setup / f"{user}.hash").write_text(HASH + "\n")
    for user in PUBLISHERS:
        (setup / f"{user}.token").write_text(TOKEN + "\n")
        (setup / f"{user}.topic").write_text(f"{user}-0123456789ab\n")
    done = subprocess.run([shutil.which("bash"), "-c", block], env={"PATH": os.environ["PATH"], "HOME": str(tmp_path)},
                          capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    written = yaml.safe_load((setup / "server.yml").read_text())
    example = yaml.safe_load(EXAMPLE.read_text())
    assert list(written) == list(example) == PRIVATE
    assert written["base-url"] == "http://nas.example.invalid:6741"
    assert written["auth-users"] == [f"{u}:{HASH}:user" for u in [*PUBLISHERS, "phone"]]
    assert written["auth-access"] == ([f"{p}:{p}-0123456789ab:wo" for p in PUBLISHERS]
                                      + [f"phone:{p}-0123456789ab:ro" for p in PUBLISHERS])
    assert written["auth-tokens"] == [f"{p}:{TOKEN}" for p in PUBLISHERS]
    assert oct((setup / "server.yml").stat().st_mode & 0o777) == "0o600"
    assert (setup / "values.env").read_text() == (
        "NTFY_URL=http://nas.example.invalid:6741\n"
        + "".join(f"NTFY_TOPIC_{p.split('-')[1].upper()}={p}-0123456789ab\n" for p in PUBLISHERS))
    for p in ("gate", "notify", "brake"):
        assert (setup / f"ntfy-{p}.header").read_text() == f"Authorization: Bearer {TOKEN}\n"


def test_ntfy_denies_by_default_and_reaches_no_relay():
    ntfy = service()
    # --config named outright, so ntfy refuses to start without server.yml (ntfy v2.28.0's cmd/config_loader.go).
    assert ntfy["command"] == "serve --config /etc/ntfy/server.yml"
    # Never Marius's on-failure:5: Docker doesn't restart an on-failure container when its daemon restarts, so a
    # NAS reboot or a DSM update would leave ntfy down (the controller's ruling, 2026-10-08).
    assert ntfy["restart"] == "unless-stopped"
    # The login's two switches are here, where ntfy takes them over server.yml, so a server.yml that lost a line
    # leaves ntfy closed, not open.
    environment = ntfy["environment"]
    assert environment["NTFY_AUTH_DEFAULT_ACCESS"] == "deny-all"
    assert environment["NTFY_AUTH_FILE"] == "/etc/ntfy/user.db"
    assert environment["NTFY_BEHIND_PROXY"] == "false"
    assert not [key for key in environment if re.search("UPSTREAM|FIREBASE|WEB_PUSH|ATTACHMENT", key)]
    assert not [key for key in yaml.safe_load(EXAMPLE.read_text()) if re.search("upstream|firebase|web-push|attach", key)]
    assert "env_file" not in ntfy


def test_ntfy_follows_marius_hostings_synology_layout():
    # Dan's decisions, 2026-10-08: his container name, his folder (server.yml and the auth database) with its cache
    # subfolder at ntfy's cache path, his healthcheck and his port; no TZ, which would name a place in this file.
    ntfy = service()
    assert ntfy["container_name"] == "ntfy"
    assert ntfy["volumes"] == ["/volume1/docker/ntfy:/etc/ntfy", "/volume1/docker/ntfy/cache:/var/cache/ntfy"]
    assert ntfy["environment"]["NTFY_CACHE_FILE"] == "/var/cache/ntfy/cache.db"
    assert "TZ" not in ntfy["environment"]
    assert ntfy["healthcheck"] == HEALTHCHECK
    assert ntfy["ports"] == ["6741:80"]


def has_docker_compose() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "compose", "version"], capture_output=True).returncode == 0


@pytest.mark.skipif(not has_docker_compose(), reason="docker compose not installed")
def test_docker_compose_reads_the_file(tmp_path):
    # --env-file always, an empty one: Compose would otherwise read a .env beside the file. The file needs none.
    (tmp_path / "empty").write_text("")
    env = {k: v for k, v in os.environ.items() if not k.startswith("NTFY_")}
    done = subprocess.run(["docker", "compose", "-f", str(COMPOSE), "--env-file", str(tmp_path / "empty"), "config"],
                          capture_output=True, text=True, timeout=60, env=env)
    assert done.returncode == 0, done.stderr
    resolved = yaml.safe_load(done.stdout)["services"]["ntfy"]
    assert resolved["command"] == ["serve", "--config", "/etc/ntfy/server.yml"]
    assert [(v["source"], v["target"]) for v in resolved["volumes"]] == [
        ("/volume1/docker/ntfy", "/etc/ntfy"), ("/volume1/docker/ntfy/cache", "/var/cache/ntfy")]
    assert [(p["published"], p["target"]) for p in resolved["ports"]] == [("6741", 80)]
    assert resolved["healthcheck"]["test"] == HEALTHCHECK["test"]


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
    assert len(blocks) == 18
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

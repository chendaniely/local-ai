"""`spark doctor` (`make doctor`), v0 — Phase 0's guardrails and the stack, checked in one pass.

Run it on the Spark, from the repo root, after any update, a reboot or upgrade day. It only reads:
it changes nothing, needs no sudo, and never prints a key. `spark doctor` proper, one check per
scenario, arrives with the gate in Phase 2.
"""

from __future__ import annotations

import argparse
import grp
import json
import os
import pwd
import re
import stat
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from spark import paths, render
from spark.llamaswap import key_from_env
from spark.registry import Registry, load_registry

UNITS = ("local-ai-llama-swap.service", "local-ai-brake.service", "local-ai-compose.service")
SECRETS = Path("/etc/local-ai/secrets")
# Root's own copies of the units and the Compose project: what root runs, installed by
# `make install-units` from what `spark apply` staged.
ROOT_FOLDERS = (Path(render.COMPOSE_DIR), Path(render.COMPOSE_DIR, "searxng"))
ROOT_FILES = (*(Path(render.UNIT_DIR, unit) for unit in render.UNITS),
              Path(render.COMPOSE_DIR, "compose.yaml"), Path(render.COMPOSE_DIR, "searxng/settings.yml"))
UFW_CONF = Path("/etc/ufw/ufw.conf")
EARLYOOM_DEFAULT = Path("stack/host/earlyoom.default")
# Each should answer 200 on / (urllib follows redirects). Not yet seen on the box: the first
# `make doctor` there is the check.
WEB = (("Open WebUI", "http://127.0.0.1:3000/"), ("SearXNG", "http://127.0.0.1:8888/"))
HOLD_DRY_RUN = ["bash", "stack/host/bootstrap.sh", "--hold-gpu", "--dry-run"]
HOLD_SUMMARY = re.compile(r"^==> GPU set: (\d+) packages, (\d+) already held$", re.MULTILINE)
HOLD_STOPS = re.compile(r"\(a real run stops here: (.+)\)$", re.MULTILINE)


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def _who(st: os.stat_result) -> tuple[str, str, int]:
    """Who owns a file, by name, and its permission bits."""
    return pwd.getpwuid(st.st_uid).pw_name, grp.getgrgid(st.st_gid).gr_name, st.st_mode & 0o7777


class Probe:
    """What the checks read from the box. The tests hand in a fake instead."""

    def __init__(self, repo: Path):
        self.repo = Path(repo)

    def run(self, argv: list[str]) -> tuple[int, str, str]:
        try:
            done = subprocess.run(argv, cwd=self.repo, capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired) as err:
            return 127, "", str(err)
        return done.returncode, done.stdout, done.stderr

    def read(self, path: Path) -> str | None:
        """A file's text, or None if it can't be read. A relative path is inside the repo."""
        try:
            return (self.repo / path).read_text()
        except OSError:
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
        """A path's owner, group, permission bits and kind ("file", "folder", "link" or "other"), read
        without following a link; None when it's missing or out of this account's reach."""
        try:
            st = os.lstat(path)
            owner = _who(st)
        except (OSError, KeyError):
            return None
        kinds = ((stat.S_ISLNK, "link"), (stat.S_ISDIR, "folder"), (stat.S_ISREG, "file"))
        return *owner, next((kind for test, kind in kinds if test(st.st_mode)), "other")

    def http(self, url: str, *, key: str | None = None, body: dict | None = None,
             timeout: float = 10.0) -> tuple[int, str]:
        """The HTTP status and body; 0 when nothing answered. The key goes in a header, never a URL."""
        request = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode())
        if body is not None:
            request.add_header("Content-Type", "application/json")
        if key:
            request.add_header("Authorization", f"Bearer {key}")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, response.read().decode(errors="replace")
        except urllib.error.HTTPError as err:
            return err.code, ""
        except (urllib.error.URLError, OSError):
            return 0, ""


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
        why = stops.group(1) if stops else " ".join(err.split()) or "the hold's dry run found no GPU set"
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
        return Check("secrets folder", False, f"{SECRETS} is missing: `make bootstrap` creates it")
    user, group, mode = owner
    if (user, group, mode) != ("root", "spark", 0o750):
        return Check("secrets folder", False, f"{SECRETS} is {user}:{group} {mode:o}, not root:spark 750")
    if probe.listable(SECRETS):
        return Check("secrets folder", False, f"your account can list {SECRETS}; it must not")
    return Check("secrets folder", True, "root:spark 750, and closed to you")


# The stack


def root_copies(probe: Probe) -> Check:
    """What root runs is root's own: each copy a folder or a regular file, never a link, owned by
    root and not writable by group or others."""
    wrong = []
    for path in (*ROOT_FOLDERS, *ROOT_FILES):
        want = "folder" if path in ROOT_FOLDERS else "file"
        entry = probe.entry(path)
        if entry is None:
            wrong.append(f"{path} is missing, or out of your account's reach")
            continue
        user, group, mode, kind = entry
        if kind != want:
            wrong.append(f"{path} is a {kind}, not a {want}")
        elif user != "root" or mode & 0o022:
            wrong.append(f"{path} is {user}:{group} {mode:o}")
    if wrong:
        return Check("root's copies", False, "; ".join(wrong) + ": run `make install-units`")
    return Check("root's copies", True, "the units and the Compose project that root runs are root's own files")


def units(probe: Probe) -> Check:
    _, out, _ = probe.run(["systemctl", "is-active", *UNITS])
    states = out.split()
    down = [f"{unit} ({state})" for unit, state in zip(UNITS, states) if state != "active"]
    if len(states) != len(UNITS) or down:
        return Check("stack units", False, ", ".join(down) or "systemctl didn't answer")
    return Check("stack units", True, "llama-swap, the brake and the web services are active")


def llama_swap(probe: Probe, key: str | None) -> Check:
    health, _ = probe.http(f"{paths.LLAMASWAP_URL}/health")
    if health != 200:
        return Check("llama-swap", False, f"/health answered {health or 'nothing'}: `make logs s=llama-swap`")
    anonymous, _ = probe.http(f"{paths.LLAMASWAP_URL}/running")
    if anonymous != 401:
        return Check("llama-swap", False, f"/running without a key answered {anonymous or 'nothing'}, "
                                          "not 401: its keys aren't enforced")
    if key is None:
        return Check("llama-swap", False, "no key in this shell: SPARK_API_KEY isn't set")
    keyed, _ = probe.http(f"{paths.LLAMASWAP_URL}/running", key=key)
    if keyed != 200:
        return Check("llama-swap", False, f"/running with your key answered {keyed or 'nothing'}")
    return Check("llama-swap", True, "answers, and refuses a call without a key")


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
    except Exception as err:  # unreadable, malformed or invalid: a FAIL line says so, never a traceback
        return None, f"{path} doesn't load: " + " ".join(f"{type(err).__name__}: {err}".split())


def model(probe: Probe, key: str | None, registry: Registry | None, problem: str | None = None) -> Check:
    """One request through llama-swap to the embeddings model: loaded if it isn't, on the GPU."""
    name = "a model, end to end"
    if registry is None:
        return Check(name, False, f"can't load the deployed registry: {problem} — `make apply`")
    models = [m.name for m in registry.models.values() if m.capability == "embeddings"]
    if not models:
        return Check(name, False, "the registry has no embeddings model")
    if key is None:
        return Check(name, False, "no key in this shell: SPARK_API_KEY isn't set")
    code, body = probe.http(f"{paths.LLAMASWAP_URL}/v1/embeddings", key=key,
                            body={"model": models[0], "input": "doctor"}, timeout=300)
    try:
        vector = json.loads(body)["data"][0]["embedding"]
    except (ValueError, KeyError, IndexError, TypeError):
        vector = []
    if code == 200 and vector:
        return Check(name, True, f"{models[0]} answered")
    return Check(name, False, f"{models[0]} answered {code or 'nothing'}: `make status` says why a load "
                              "was refused")


def checks(probe: Probe, key: str | None, registry: Registry | None, problem: str | None = None) -> list[Check]:
    return [
        hooks(probe), gpu_set(probe), running_modules(probe), driver(probe), earlyoom(probe),
        firewall(probe), secrets_folder(probe),
        root_copies(probe), units(probe), llama_swap(probe, key), web(probe), model(probe, key, registry, problem),
    ]


def report(results: list[Check]) -> str:
    lines = [f"{'ok  ' if c.ok else 'FAIL'}  {c.name}: {c.detail}" for c in results]
    lines.append(f"doctor: {sum(c.ok for c in results)} of {len(results)} checks pass")
    return "\n".join(lines)


def register(subparsers) -> None:
    p = subparsers.add_parser("doctor", help="Phase 0's guardrails and the stack, checked (on the Spark)")
    p.add_argument("--key-env", default="SPARK_API_KEY", help="env var holding a llama-swap key")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    repo = Path.cwd()
    if not (repo / "stack/host/bootstrap.sh").exists():
        print("doctor: run it from the repo root (make doctor)")
        return 2
    registry, problem = load_deployed_registry(paths.REGISTRY)
    results = checks(Probe(repo), key_from_env(args.key_env), registry, problem)
    print(report(results))
    return 0 if all(c.ok for c in results) else 1

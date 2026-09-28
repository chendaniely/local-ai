"""`spark render` — stack/models.yaml + stack/versions.yaml + stack/templates → deployable files."""

from __future__ import annotations

import argparse
import os
import re
import secrets
import stat
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, localcontext
from pathlib import Path

import yaml

from spark.registry import Model, Registry, Source, load_registry
from spark.versions import Component, load_versions

TEMPLATES = Path("stack/templates")
DEPLOY = "/opt/local-ai"
HF_HOME = "/var/lib/local-ai/hf"
SPARK_BIN = f"{DEPLOY}/app/.venv/bin/spark"
KEY_ENVS = ("LLAMASWAP_KEY_DAN_MAC", "LLAMASWAP_KEY_AGENT", "LLAMASWAP_KEY_OPENWEBUI", "LLAMASWAP_KEY_SPARK")
UNITS = ("local-ai-llama-swap.service", "local-ai-brake.service", "local-ai-compose.service", "local-ai-pull.service")
# What root runs is root's own copy, in folders only root can write: `make install-units` (sudo)
# installs the rendered units and Compose project there, and `spark apply` only stages them.
UNIT_DIR = "/etc/systemd/system"
COMPOSE_DIR = "/etc/local-ai/compose"
# Flags a registry's args may not set: an engine takes the last value a flag is given, and args come after render's
# own. That is every flag render passes a model (read from the command engine_cmd builds, so the list can't drift)
# and every flag in REFUSED, each in all its spellings. Keys stay out of commands because llama-swap shows each whole
# at GET /running, and the process list shows an engine's argv.
# Each engine's spellings of a flag, from its own option table: llama.cpp b11146's common/arg.cpp (the options
# llama-server takes) and whisper.cpp v1.9.4's examples/server/server.cpp. Every flag render passes has an entry,
# even with one spelling, so a test fails until a flag it starts passing is looked up there; any other flag needs an
# entry only when it has more than one spelling.
SPELLINGS = {
    "llama.cpp": (
        ("--host",), ("--port",), ("-m", "--model"), ("-mm", "--mmproj"), ("-c", "--ctx-size"), ("-np", "--parallel"),
        ("-ngl", "--gpu-layers", "--n-gpu-layers"), ("-cram", "--cache-ram"), ("--embedding", "--embeddings"),
        ("--offline",), ("-hft", "--hf-token"), ("-hf", "-hfr", "--hf-repo"), ("-hff", "--hf-file"),
        ("--spec-draft-hf", "-hfd", "-hfrd", "--hf-repo-draft"), ("-mu", "--model-url"), ("-mmu", "--mmproj-url"),
        ("-dr", "--docker-repo"), ("--mmproj-auto", "--no-mmproj", "--no-mmproj-auto"),
        ("-kvu", "--kv-unified", "-no-kvu", "--no-kv-unified"),
    ),
    "whisper.cpp": (("--host",), ("--port",), ("-m", "--model"), ("--inference-path",)),
}
_BIND = "render binds every engine to 127.0.0.1, on the port llama-swap gives it"
_FILES = "render passes the model files the registry pins in source"
_KEY = "an engine takes no key: llama-swap checks the keys, and shows every command at GET /running"
_DOWNLOAD = "an engine loads only what the registry pins: a download bypasses the pinned revision"
_SET = ("render sets it, and an engine takes the last value it's given: the model would run other than as it was "
        "admitted")
_POOL = "a model's slots share its whole context, and pi is told one request can use all of it"
# What an engine is never given, whether or not render passes it. The downloads are every option llama-server
# b11146 takes that fetches weights at start (-hf's projector switch included); whisper-server v1.9.4 has none.
REFUSED = {
    "llama.cpp": {
        "--host": _BIND, "--port": _BIND, "--model": _FILES, "--mmproj": _FILES,
        "--api-key": _KEY, "--api-key-file": _KEY, "--hf-token": _KEY, "--kv-unified-per-slot": _POOL,
        **dict.fromkeys(("--hf-repo", "--hf-file", "--hf-repo-draft", "--model-url", "--mmproj-url", "--docker-repo",
                         "--mmproj-auto", "--embd-gemma-default", "--fim-qwen-1.5b-default", "--fim-qwen-3b-default",
                         "--fim-qwen-7b-default", "--fim-qwen-7b-spec", "--fim-qwen-14b-spec",
                         "--fim-qwen-30b-default", "--gpt-oss-20b-default", "--gpt-oss-120b-default",
                         "--vision-gemma-4b-default", "--vision-gemma-12b-default"), _DOWNLOAD),
    },
    "whisper.cpp": {"--host": _BIND, "--port": _BIND, "--model": _FILES},
}
# llama-swap splits a command as a POSIX shell does, so a word with one of these reaches the engine as other words:
# `--ho\st` as --host, `a.bin --host 0.0.0.0` as three.
_SPLITS = re.compile(r"[\s'\"\\]")
TENTH = Decimal("0.1")


class RenderError(ValueError):
    pass


def installed_path(rel: str) -> str | None:
    """Where root's copy of a rendered file lives; None for a file `spark apply` deploys itself."""
    if rel.startswith("systemd/"):
        return f"{UNIT_DIR}/{rel.removeprefix('systemd/')}"
    if rel.startswith("compose/"):
        return f"{COMPOSE_DIR}/{rel.removeprefix('compose/')}"
    return None


def model_path(source: Source, file: str) -> str:
    org, name = source.repo.split("/", 1)
    return f"{HF_HOME}/hub/models--{org}--{name}/snapshots/{source.revision}/{file}"


def _flag(arg: str) -> str:
    """The flag an engine reads `arg` as: without an `=value`, and with a long flag's `_` as `-`, as llama-server reads
    it, so neither `--host=0.0.0.0` nor `--api_key` slips past."""
    flag = arg.split("=", 1)[0]
    return flag.replace("_", "-") if flag.startswith("--") else flag


def _refused(model: Model, own: list[str]) -> dict[str, str]:
    """Every spelling of each flag `model`'s args may not set, with the reason: each flag in `own`, the words render
    itself passes the engine, and each flag in REFUSED."""
    engine = "whisper.cpp" if model.engine == "whisper.cpp" else "llama.cpp"
    reasons = dict.fromkeys((word for word in own[1:] if word.startswith("-")), _SET)
    reasons.update(REFUSED[engine])
    spellings = {flag: group for group in SPELLINGS[engine] for flag in group}
    return {spelling: why for flag, why in reasons.items() for spelling in spellings.get(flag, (flag,))}


def engine_cmd(model: Model, registry: Registry) -> list[str]:
    binary = registry.engines[model.engine]
    main = model_path(model.source, model.source.file)
    if model.engine == "whisper.cpp":
        cmd = [binary, "--host", "127.0.0.1", "--port", "${PORT}", "--model", main,
               "--inference-path", "/v1/audio/transcriptions"]
    else:
        cmd = [binary, "--host", "127.0.0.1", "--port", "${PORT}", "--model", main,
               "--ctx-size", str(model.ctx), "--parallel", str(model.parallel),
               "--gpu-layers", "all", "--cache-ram", str(model.cache_ram_mib),
               # Defense in depth: a download option the refusals miss still fetches nothing (llama.cpp b11146's
               # common/arg.cpp:3927). whisper-server has no such option.
               "--offline"]
        if model.parallel > 1:
            # One KV pool for all the slots, so any one request can use the whole context rather than ctx / parallel
            # (Dan's decision, 2026-09-28). Two long requests at once share it.
            cmd += ["--kv-unified"]
        if model.source.mmproj:
            cmd += ["--mmproj", model_path(model.source, model.source.mmproj)]
        if model.capability == "embeddings":
            cmd += ["--embedding"]
    refused = _refused(model, cmd)
    for arg in model.args:
        flag = _flag(arg)
        if flag in refused:  # named as written, without an =value: that could be a key
            raise RenderError(f"{model.name}: args may not set {arg.split('=', 1)[0]}: {refused[flag]}")
    return cmd + list(model.args)


def _gib(value: float) -> Decimal:
    """The number as the registry gave it. In binary floats 72.1 − 22.1 is 49.99999999999999, which would refuse a
    50 GiB set that fits exactly."""
    return Decimal(repr(value))


def check_budget(registry: Registry) -> None:
    budget = registry.budget
    with localcontext(prec=400):  # every digit of any finite float, so even an absurd number sums and rounds exactly
        allocatable, reserve = _gib(budget.allocatable_gib), _gib(budget.reserve_gib)
        room = allocatable - reserve
        total = sum((_gib(m.footprint_gib) for m in registry.models.values()), Decimal(0))
        if total > room:
            # One decimal, each rounded against the set: the need up, the room down. So the two can't read as a fit.
            raise RenderError(f"the model set needs {total.quantize(TENTH, ROUND_CEILING):f} GiB but the budget "
                              f"allows {room.quantize(TENTH, ROUND_FLOOR):f} GiB "
                              f"(allocatable {allocatable:f} − reserve {reserve:f})")


def _one_word_and_nothing_filled_in(name: str, words: list[str]) -> list[str]:
    """`words`, once each is sure to reach the engine as itself: one word, with nothing for llama-swap to fill in but
    render's own ${PORT}. llama-swap v257 fills in ${env.…} anywhere in its config, a key included."""
    for word in words:
        if _SPLITS.search(word):
            raise RenderError(f"{name}: {word!r} wouldn't reach the engine as written: llama-swap splits and unescapes "
                              "a command as a POSIX shell does, so whitespace, quotes and backslashes are refused")
        if "${" in word.replace("${PORT}", ""):
            raise RenderError(f"{name}: {word!r} holds a ${{…}} llama-swap would fill in, and GET /running shows every "
                              "command whole; only render's own ${PORT} may appear")
    return words


def llama_swap_config(registry: Registry) -> dict:
    models = {}
    for m in registry.models.values():
        cmd = _one_word_and_nothing_filled_in(m.name, [SPARK_BIN, "launch", m.name, "--", *engine_cmd(m, registry)])
        for role in m.roles:
            if "${" in role:
                raise RenderError(f"{m.name}: role {role!r} holds a ${{…}} llama-swap would fill in, anywhere in its "
                                  "config; a role is a name, and only apiKeys refer to a key")
        models[m.name] = {
            "cmd": " ".join(cmd),
            "proxy": "http://127.0.0.1:${PORT}",
            "checkEndpoint": "/health",
            "ttl": 0,
            "aliases": list(m.roles),
        }
    return {
        "healthCheckTimeout": 600,
        "captureBuffer": 0,
        "globalTTL": 0,
        "startPort": 5800,
        "logLevel": "info",
        "apiKeys": [f"${{env.{name}}}" for name in KEY_ENVS],
        "models": models,
        "routing": {"router": {"use": "group", "settings": {"groups": {"stack": {
            "swap": False, "exclusive": False, "persistent": True, "members": sorted(models)}}}}},
    }


def _only(registry: Registry, what: str, match) -> str:
    names = [m.name for m in registry.models.values() if match(m)]
    if len(names) != 1:
        raise RenderError(f"need exactly one {what}, found {names}")
    return names[0]


def _component(versions: dict[str, Component], name: str) -> Component:
    if name not in versions:
        raise RenderError(f"{name}: not in the versions file, and render needs it")
    return versions[name]


def _image(c: Component) -> str:
    if not c.image or not c.pin:
        raise RenderError(f"{c.name}: image and pin are required to deploy")
    if not c.pin.startswith("sha256:"):  # versions.yaml's git:<40 hex> pins a source build; Compose takes a digest
        raise RenderError(f"{c.name}: an image is pinned by its sha256: digest, not {c.pin}")
    return f"{c.image}:{c.version}@{c.pin}"


def render(registry: Registry, versions: dict[str, Component], registry_text: str,
           templates: Path = TEMPLATES) -> dict[str, str]:
    check_budget(registry)
    fields = {
        "llama_swap_version": _component(versions, "llama-swap").version,
        "open_webui_image": _image(_component(versions, "open-webui")),
        "searxng_image": _image(_component(versions, "searxng")),
        "task_model": _only(registry, "model with the 'small' role", lambda m: "small" in m.roles),
        "embedding_model": _only(registry, "embeddings model", lambda m: m.capability == "embeddings"),
        "stt_model": _only(registry, "transcription model", lambda m: m.capability == "transcription"),
    }
    files = {
        "llama-swap.yaml": yaml.safe_dump(llama_swap_config(registry), sort_keys=False),
        "models.yaml": registry_text,
        "compose/compose.yaml": (templates / "compose.yaml").read_text().format(**fields),
        "compose/searxng/settings.yml": (templates / "searxng-settings.yml").read_text(),
    }
    for unit in UNITS:
        files[f"systemd/{unit}"] = (templates / unit).read_text().format(**fields)
    return files


def write_atomic(path: Path, data: str | bytes) -> None:
    """Write `path` whole: into a new file in its folder, then renamed over it. A reader sees the old file or the new
    one, never part of one (`spark launch` reads the deployed registry on every load), and a link where the file
    goes is replaced, not written through. A file replaced keeps its mode; a new one gets what an ordinary write
    would give it, 0666 less the umask. Text is encoded as Path.write_text would encode it."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        was = os.lstat(path)
    except FileNotFoundError:
        was = None
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    try:
        with open(fd, "wb" if isinstance(data, bytes) else "w") as f:
            f.write(data)
        if was is not None and stat.S_ISREG(was.st_mode):
            os.chmod(tmp, stat.S_IMODE(was.st_mode) & 0o777)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def write_tree(files: dict[str, str], out: Path) -> None:
    """Each file under `out`, making its folders, each written whole (write_atomic)."""
    for rel, content in files.items():
        write_atomic(Path(out) / rel, content)


def register(subparsers) -> None:
    p = subparsers.add_parser("render", help="render the deployable config into a directory")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--registry", type=Path, default=Path("stack/models.yaml"))
    p.add_argument("--versions", type=Path, default=Path("stack/versions.yaml"))
    p.set_defaults(func=run)


def _load(what: str, path: Path, load):
    """load(path), or a RenderError that names the file, as `spark launch` does: YAML's own message says only
    "<unicode string>", and a decoding error names no file at all."""
    try:
        return load(path)
    except (OSError, ValueError, yaml.YAMLError, RecursionError) as err:  # RecursionError: YAML nested too deep
        raise RenderError(f"the {what} {path} won't load: {err}") from err


def run(args: argparse.Namespace) -> int:
    registry = _load("registry", args.registry, load_registry)
    versions = _load("versions file", args.versions, load_versions)
    files = render(registry, versions, _load("registry", args.registry, Path.read_text))
    write_tree(files, args.out)
    print(f"render: {len(files)} files → {args.out}")
    return 0

"""`spark render` — stack/models.yaml + stack/versions.yaml + stack/templates → deployable files."""

from __future__ import annotations

import argparse
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


def engine_cmd(model: Model, registry: Registry) -> list[str]:
    binary = registry.engines[model.engine]
    main = model_path(model.source, model.source.file)
    if model.engine == "whisper.cpp":
        cmd = [binary, "--host", "127.0.0.1", "--port", "${PORT}", "--model", main,
               "--inference-path", "/v1/audio/transcriptions"]
    else:
        cmd = [binary, "--host", "127.0.0.1", "--port", "${PORT}", "--model", main,
               "--ctx-size", str(model.ctx), "--parallel", str(model.parallel),
               "--gpu-layers", "all", "--cache-ram", str(model.cache_ram_mib)]
        if model.source.mmproj:
            cmd += ["--mmproj", model_path(model.source, model.source.mmproj)]
        if model.capability == "embeddings":
            cmd += ["--embedding"]
    return cmd + list(model.args)


def check_budget(registry: Registry) -> None:
    room = registry.budget.allocatable_gib - registry.budget.reserve_gib
    total = registry.static_total_gib()
    if total > room:
        raise RenderError(f"the model set needs ~{total:.0f} GiB but the budget allows {room:.0f} GiB "
                          f"(allocatable {registry.budget.allocatable_gib:g} − reserve {registry.budget.reserve_gib:g})")


def llama_swap_config(registry: Registry) -> dict:
    models = {}
    for m in registry.models.values():
        models[m.name] = {
            "cmd": " ".join([SPARK_BIN, "launch", m.name, "--", *engine_cmd(m, registry)]),
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


def _image(c: Component) -> str:
    if not c.image or not c.pin:
        raise RenderError(f"{c.name}: image and pin are required to deploy")
    return f"{c.image}:{c.version}@{c.pin}"


def render(registry: Registry, versions: dict[str, Component], registry_text: str,
           templates: Path = TEMPLATES) -> dict[str, str]:
    check_budget(registry)
    fields = {
        "llama_swap_version": versions["llama-swap"].version,
        "open_webui_image": _image(versions["open-webui"]),
        "searxng_image": _image(versions["searxng"]),
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


def write_tree(files: dict[str, str], out: Path) -> None:
    for rel, content in files.items():
        path = Path(out) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def register(subparsers) -> None:
    p = subparsers.add_parser("render", help="render the deployable config into a directory")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--registry", type=Path, default=Path("stack/models.yaml"))
    p.add_argument("--versions", type=Path, default=Path("stack/versions.yaml"))
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    files = render(load_registry(args.registry), load_versions(args.versions), args.registry.read_text())
    write_tree(files, args.out)
    print(f"render: {len(files)} files → {args.out}")
    return 0

"""Client configs rendered from the registry — real model names, keys by env reference only."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from spark.registry import Registry, load_registry

PI_MODELS = Path("~/.pi/agent/models.json").expanduser()
COMPAT = {"supportsStore": False, "supportsDeveloperRole": False, "supportsReasoningEffort": False,
          "supportsUsageInStreaming": True, "supportsStrictMode": False, "maxTokensField": "max_tokens"}


def pi_provider(registry: Registry, base_url: str, key_env: str) -> dict:
    models = []
    for m in registry.models.values():
        if m.capability != "chat":
            continue  # embeddings and speech models don't belong in a chat picker
        window = m.ctx // m.parallel  # llama-server splits its context across the slots
        models.append({"id": m.name, "name": m.name, "reasoning": True,
                       "input": ["text", "image"] if m.source.mmproj else ["text"],
                       "contextWindow": window, "maxTokens": min(32768, window // 2)})
    return {"baseUrl": base_url, "api": "openai-completions", "apiKey": "${" + key_env + "}",
            "compat": dict(COMPAT), "models": models}


def merge_pi(path: Path, provider: dict) -> Path | None:
    """Put `provider` under providers.spark, keep every other provider, and back up the old file.
    Returns the backup, or None when there was no file to back up."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(json.dumps({"providers": {"spark": provider}}, indent=2) + "\n")
        return None
    data = json.loads(path.read_text())
    backup = path.with_suffix(".json.bak")
    shutil.copyfile(path, backup)
    data.setdefault("providers", {})["spark"] = provider
    path.write_text(json.dumps(data, indent=2) + "\n")
    return backup


def register(subparsers) -> None:
    p = subparsers.add_parser("clients", help="client configs")
    sub = p.add_subparsers(dest="clients_command", required=True)
    pi = sub.add_parser("pi", help="the Spark provider for pi")
    pi.add_argument("--write", action="store_true", help=f"merge it into {PI_MODELS}")
    pi.add_argument("--base-url", default="http://127.0.0.1:9100/v1")
    pi.add_argument("--key-env", default="SPARK_API_KEY")
    pi.add_argument("--registry", type=Path, default=Path("stack/models.yaml"))
    pi.set_defaults(func=run_pi)


def run_pi(args: argparse.Namespace) -> int:
    provider = pi_provider(load_registry(args.registry), args.base_url, args.key_env)
    if not args.write:
        print(json.dumps(provider, indent=2))
        return 0
    backup = merge_pi(PI_MODELS, provider)
    kept = f"the previous file is {backup}" if backup else "there was no previous file"
    print(f"clients: wrote the 'spark' provider to {PI_MODELS} ({kept})")
    return 0

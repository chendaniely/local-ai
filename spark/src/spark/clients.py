"""Client configs rendered from the registry — real model names, keys by env reference only."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import yaml

from spark.registry import Registry, load_registry

PI_MODELS = Path("~/.pi/agent/models.json")  # expanded when run_pi runs, under the HOME it runs with
OPENCODE_CONFIG = Path("~/.config/opencode/opencode.json")
HERMES_CONFIG = Path("~/.hermes/config.yaml")
HERMES_ENV = Path("~/.hermes/.env")
COMPAT = {"supportsStore": False, "supportsDeveloperRole": False, "supportsReasoningEffort": False,
          "supportsUsageInStreaming": True, "supportsStrictMode": False, "maxTokensField": "max_tokens"}


def pi_provider(registry: Registry, base_url: str, key_env: str) -> dict:
    models = []
    for m in registry.models.values():
        if m.capability != "chat":
            continue  # embeddings and speech models don't belong in a chat picker
        window = m.ctx  # the slots share one pool (render's --kv-unified): one request can use all of it
        models.append({"id": m.name, "name": m.name, "reasoning": True,
                       "input": ["text", "image"] if m.source.mmproj else ["text"],
                       "contextWindow": window, "maxTokens": min(32768, window // 2)})
    return {"baseUrl": base_url, "api": "openai-completions", "apiKey": "${" + key_env + "}",
            "compat": dict(COMPAT), "models": models}


def opencode_provider(registry: Registry, base_url: str, key_env: str) -> dict:
    """OpenCode custom provider config — OpenAI-compatible endpoint using AI SDK."""
    models = {}
    for m in registry.models.values():
        if m.capability != "chat":
            continue
        models[m.name] = {}
    return {
        "provider": {
            "spark": {
                "npm": "@ai-sdk/openai-compatible",
                "options": {
                    "apiKey": "{env:" + key_env + "}",
                    "baseURL": base_url,
                },
                "models": models,
            }
        }
    }


def hermes_provider(registry: Registry, base_url: str, key_env: str) -> dict:
    """Hermes config.yaml entry for a custom OpenAI-compatible provider.
    
    Hermes expects named custom providers under providers: with:
    - api (not api_base)
    - key_env (not api_key with ${})
    - api_mode: chat_completions for OpenAI-compatible
    """
    chat_models = [m.name for m in registry.models.values() if m.capability == "chat"]
    default_model = chat_models[0] if chat_models else ""
    return {
        "model": {
            "provider": "custom",
            "default": default_model,
            "base_url": base_url,
            "key_env": key_env,
        },
        "providers": {
            "spark": {
                "api": base_url,
                "key_env": key_env,
                "api_mode": "chat_completions",
                "models": {name: {} for name in chat_models},
            }
        }
    }


def hermes_env(key_env: str) -> str:
    """Generate the .env entry for Hermes."""
    return f"{key_env}=your-llama-swap-key-here\n"


class ClientsError(ValueError):
    """A refusal: the message is the reason, and names the file."""


# What json.loads gives for each kind of JSON value but an object.
KINDS = {list: "an array", str: "a string", int: "a number", float: "a number", bool: "true or false",
         type(None): "null"}


def _load_pi(path: Path) -> dict:
    """pi's models file, an object whose providers are an object too; or a ClientsError that names the file, as
    render's _load names its files: json's own message names none. Only the error's message goes on: json's error
    and the codec's keep the file's text, and the file can hold other providers' keys."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError, RecursionError) as err:  # RecursionError: JSON nested too deep
        why = str(err)
    else:
        why = None
    if why is not None:  # raised here, not in the except, so that no error is even chained to it
        raise ClientsError(f"pi's models file {path} won't load: {why}")
    if not isinstance(data, dict):
        raise ClientsError(f"pi's models file {path} holds {KINDS[type(data)]} at its top level, not the object pi "
                           "reads")
    providers = data.get("providers", {})
    if not isinstance(providers, dict):
        raise ClientsError(f"pi's models file {path}: its providers are {KINDS[type(providers)]}, not the object pi "
                           "reads")
    return data


def merge_pi(path: Path, provider: dict) -> Path | None:
    """Put `provider` under providers.spark, keep every other provider, and back up the old file.
    Returns the backup, or None when there was no file to back up. A file that won't load, or that
    isn't the object pi reads, is refused before anything is written. The backup keeps the file's
    mode, since the file can hold other providers' keys. The file is written in place, so a link to
    it, from a dotfiles repo say, stays a link."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(json.dumps({"providers": {"spark": provider}}, indent=2) + "\n")
        return None
    data = _load_pi(path)
    backup = path.with_suffix(".json.bak")
    shutil.copy2(path, backup)
    data.setdefault("providers", {})["spark"] = provider
    path.write_text(json.dumps(data, indent=2) + "\n")
    return backup


def merge_opencode(path: Path, provider: dict) -> Path | None:
    """Merge the Spark provider into OpenCode's config, preserving existing providers."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(json.dumps(provider, indent=2) + "\n")
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError, RecursionError) as err:
        raise ClientsError(f"OpenCode config {path} won't load: {err}")
    backup = path.with_suffix(".json.bak")
    shutil.copy2(path, backup)
    # Merge the provider object (update existing, don't just setdefault)
    data.setdefault("provider", {}).update(provider["provider"])
    path.write_text(json.dumps(data, indent=2) + "\n")
    return backup


def merge_hermes(path: Path, provider: dict) -> Path | None:
    """Merge the Spark provider into Hermes config.yaml, preserving existing config."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(yaml.safe_dump(provider, sort_keys=False))
        return None
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except (OSError, ValueError, yaml.YAMLError) as err:
        raise ClientsError(f"Hermes config {path} won't load: {err}")
    backup = path.with_suffix(".yaml.bak")
    shutil.copy2(path, backup)
    # Merge model config (replace entirely - it's the main model config)
    data["model"] = provider.get("model", data.get("model", {}))
    # Merge providers (update/add the spark provider)
    data.setdefault("providers", {}).update(provider.get("providers", {}))
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return backup


def write_hermes_env(path: Path, env_line: str) -> Path | None:
    """Write/append the API key to Hermes .env file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(env_line)
        return None
    # Check if key already exists
    content = path.read_text()
    key_name = env_line.split("=")[0]
    if key_name in content:
        return None  # already set, don't overwrite
    backup = path.with_suffix(".env.bak")
    shutil.copy2(path, backup)
    with path.open("a") as f:
        f.write(env_line)
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

    opencode = sub.add_parser("opencode", help="the Spark provider for OpenCode")
    opencode.add_argument("--write", action="store_true", help=f"merge it into {OPENCODE_CONFIG}")
    opencode.add_argument("--base-url", default="http://127.0.0.1:9100/v1")
    opencode.add_argument("--key-env", default="SPARK_API_KEY")
    opencode.add_argument("--registry", type=Path, default=Path("stack/models.yaml"))
    opencode.set_defaults(func=run_opencode)

    hermes = sub.add_parser("hermes", help="the Spark provider for Hermes")
    hermes.add_argument("--write", action="store_true", help=f"merge it into {HERMES_CONFIG} and {HERMES_ENV}")
    hermes.add_argument("--base-url", default="http://127.0.0.1:9100/v1")
    hermes.add_argument("--key-env", default="SPARK_API_KEY")
    hermes.add_argument("--registry", type=Path, default=Path("stack/models.yaml"))
    hermes.set_defaults(func=run_hermes)


def run_pi(args: argparse.Namespace) -> int:
    provider = pi_provider(load_registry(args.registry), args.base_url, args.key_env)
    if not args.write:
        print(json.dumps(provider, indent=2))
        return 0
    path = PI_MODELS.expanduser()
    backup = merge_pi(path, provider)
    kept = f"the previous file is {backup}" if backup else "there was no previous file"
    print(f"clients: wrote the 'spark' provider to {path} ({kept})")
    return 0


def run_opencode(args: argparse.Namespace) -> int:
    provider = opencode_provider(load_registry(args.registry), args.base_url, args.key_env)
    if not args.write:
        print(json.dumps(provider, indent=2))
        return 0
    path = OPENCODE_CONFIG.expanduser()
    backup = merge_opencode(path, provider)
    kept = f"the previous file is {backup}" if backup else "there was no previous file"
    print(f"clients: wrote the 'spark' provider to {path} ({kept})")
    return 0


def run_hermes(args: argparse.Namespace) -> int:
    provider = hermes_provider(load_registry(args.registry), args.base_url, args.key_env)
    env_line = hermes_env(args.key_env)
    if not args.write:
        print(yaml.safe_dump(provider, sort_keys=False))
        print(f"# Add to {HERMES_ENV}:")
        print(env_line)
        return 0
    config_path = HERMES_CONFIG.expanduser()
    env_path = HERMES_ENV.expanduser()
    config_backup = merge_hermes(config_path, provider)
    env_backup = write_hermes_env(env_path, env_line)
    parts = []
    if config_backup:
        parts.append(f"config backed up to {config_backup}")
    else:
        parts.append("new config")
    if env_backup:
        parts.append(f".env backed up to {env_backup}")
    else:
        parts.append(".env updated (or key already set)")
    print(f"clients: wrote the 'spark' provider to {config_path} and {env_path} ({', '.join(parts)})")
    return 0

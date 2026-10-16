"""Client configs rendered from the registry — real model names, keys by env reference only."""

from __future__ import annotations

import argparse
import errno
import json
import os
import secrets
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path

import yaml

from spark.registry import Registry, load_registry

PI_MODELS = Path("~/.pi/agent/models.json")  # expanded when run_pi runs, under the HOME it runs with
PI_SETTINGS = Path("~/.pi/agent/settings.json")  # likewise
OPENCODE_CONFIG = Path("~/.config/opencode/opencode.json")
HERMES_CONFIG = Path("~/.hermes/config.yaml")
HERMES_ENV = Path("~/.hermes/.env")  # read only for the placeholder check below; never written
HERMES_PLACEHOLDER = "your-llama-swap-key-here"  # what this command's first version wrote there
# The reasoning effort Hermes sends the Spark's models, set per model. Hermes sends its configured effort as it is,
# up to `max`, and Qwen3.8's chat template refuses all but xhigh, medium and low: a 500 (seen 2026-10-14, with `max`).
# xhigh is that template's own default, what pi gets by sending none; a template that doesn't read it, Gemma's,
# ignores it.
HERMES_REASONING = "xhigh"
# The efforts OpenCode offers each Spark model. Its own for an OpenAI-compatible model are low, medium and high, and
# that template refuses high (seen 2026-10-14). A model's variants replace OpenCode's, so these are all it offers, and
# its default sends no effort, which the template reads as xhigh (checked with OpenCode 2.0.25 and a stand-in server).
OPENCODE_EFFORTS = ("low", "medium", "xhigh")
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
    """OpenCode's custom provider: an OpenAI-compatible endpoint through the AI SDK, the key by env reference. Each
    model's limits let OpenCode tell how much of its context is left, as pi's contextWindow does, and its variants are
    the efforts it can be sent."""
    models = {}
    for m in registry.models.values():
        if m.capability != "chat":
            continue  # embeddings and speech models don't belong in a chat picker
        models[m.name] = {"name": m.name, "limit": {"context": m.ctx, "output": min(32768, m.ctx // 2)},
                          "variants": {e: {"reasoningEffort": e} for e in OPENCODE_EFFORTS}}
    return {"npm": "@ai-sdk/openai-compatible", "name": "Spark",
            "options": {"apiKey": "{env:" + key_env + "}", "baseURL": base_url}, "models": models}


def hermes_provider(registry: Registry, base_url: str, key_env: str) -> dict:
    """Hermes's named custom provider, providers.spark in its config.yaml: the endpoint, the key by the name of the
    variable that holds it, the transport, and each chat model's context. Hermes reads the variable from its
    environment; nothing here writes a key anywhere."""
    models = {m.name: {"context_length": m.ctx} for m in registry.models.values() if m.capability == "chat"}
    return {"api": base_url, "key_env": key_env, "transport": "chat_completions", "models": models}


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


# pi's settings file (Phase 2a's Task 34): `agent`'s pi has to wait longer than `agent`'s key does, and pi 0.85.1 gives
# up on a request with no response after its httpIdleTimeoutMs, 300,000 by default. The file is a user's own pi
# config, so it is never written through a link, nor in place: a new file, renamed over it.

@dataclass(frozen=True)
class SettingsWrite:
    """pi's settings file, merged and ready to write: everything that can refuse it has run."""

    path: Path
    text: str  # the new file
    old: bytes | None  # the file as it was read, or None when there was none
    mode: int | None  # its mode, which the new file and the backup keep


def _not_json(name: str):
    raise ValueError(f"{name} isn't JSON")  # Python's json reads NaN and Infinity; pi's JSON.parse doesn't


def plan_settings(path: Path, changes: dict) -> SettingsWrite:
    """pi's settings file at `path` with `changes` merged in, every other setting kept; or a ClientsError that names
    the file, before anything is written. The file is read as pi 0.85.1 reads it (an empty file is no settings, a
    byte-order mark is skipped), but never through a link, and only if it's a regular file. Only the error's message
    goes on, never the file's text, and no error is chained to the refusal: json's and the codec's keep that text."""
    fd = why = None
    link = False
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)  # a FIFO would wait for a writer
    except FileNotFoundError:
        pass
    except OSError as err:
        link = err.errno == errno.ELOOP and path.is_symlink()  # ELOOP is also a loop of links in a folder above it
        why = err.strerror
    if link:
        raise ClientsError(f"pi's settings file {path} is a link, and spark clients doesn't write through one, so "
                           "nothing was written")
    if why is not None:
        raise ClientsError(f"pi's settings file {path} won't load: {why}")
    old = mode = None
    if fd is not None:
        try:
            info = os.fstat(fd)
            if stat.S_ISREG(info.st_mode):
                with os.fdopen(fd, "rb", closefd=False) as f:
                    old, mode = f.read(), stat.S_IMODE(info.st_mode) & 0o777
        except OSError as err:
            why = err.strerror
        finally:
            os.close(fd)
        if why is not None:
            raise ClientsError(f"pi's settings file {path} won't load: {why}")
        if old is None:
            raise ClientsError(f"pi's settings file {path} isn't a regular file, so nothing was written")
    try:
        settings = json.loads(old.decode("utf-8-sig"), parse_constant=_not_json) if old else {}
        if isinstance(settings, dict):
            text = json.dumps({**settings, **changes}, indent=2, allow_nan=False) + "\n"
    except (ValueError, RecursionError) as err:  # RecursionError: JSON nested too deep, to read or to write back
        why = str(err)
    if why is not None:  # raised here, not in the except, so that no error is even chained to it
        raise ClientsError(f"pi's settings file {path} won't load: {why}")
    if not isinstance(settings, dict):
        raise ClientsError(f"pi's settings file {path} holds {KINDS[type(settings)]} at its top level, not the object "
                           "pi reads")
    return SettingsWrite(path, text, old, mode)


def _write_whole(path: Path, data: bytes, mode: int | None) -> None:
    """`data` at `path`, written into a new file of this write's own in the same folder, then renamed over `path`: pi
    reads the old file or the new one, never part of one, and a link at `path` is replaced, never written through. The
    new file gets `mode` from the start; with None, what an ordinary write gives it, 0666 less the umask."""
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                 0o666 if mode is None else 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            if mode is not None:
                os.fchmod(f.fileno(), mode)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def write_settings(plan: SettingsWrite) -> Path | None:
    """Write what plan_settings merged: the old file first, as settings.json.bak at its mode, then the new one at the
    same mode. Returns the backup, or None when there was no file to back up."""
    plan.path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if plan.old is not None:
        backup = plan.path.with_name(plan.path.name + ".bak")
        _write_whole(backup, plan.old, plan.mode)
    _write_whole(plan.path, plan.text.encode(), plan.mode)
    return backup


def _milliseconds(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        value = -1
    if value < 0:
        raise argparse.ArgumentTypeError(f"{text!r} isn't a whole number of milliseconds, 0 or more")
    return value


def _load_opencode(path: Path) -> dict:
    """OpenCode's config, an object whose provider entry is an object too; or a ClientsError that names the file, as
    _load_pi does, chaining nothing to it: the file can hold other providers' keys."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError, RecursionError) as err:  # a JSONC file, with comments, is refused here too
        why = str(err)
    else:
        why = None
    if why is not None:
        raise ClientsError(f"OpenCode's config {path} won't load: {why}")
    if not isinstance(data, dict):
        raise ClientsError(f"OpenCode's config {path} holds {KINDS[type(data)]} at its top level, not the object "
                           "OpenCode reads")
    if not isinstance(data.get("provider", {}), dict):
        raise ClientsError(f"OpenCode's config {path}: its provider entry is {KINDS[type(data['provider'])]}, not the "
                           "object OpenCode reads")
    return data


def merge_opencode(path: Path, provider: dict) -> Path | None:
    """Put `provider` under provider.spark in OpenCode's config, keep everything else, and back up the old file, as
    merge_pi does: refused before anything is written if the file won't load, the backup keeping its mode, and the
    file written in place, so a link to it stays a link."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(json.dumps({"$schema": "https://opencode.ai/config.json", "provider": {"spark": provider}},
                                   indent=2) + "\n")
        return None
    data = _load_opencode(path)
    backup = path.with_suffix(".json.bak")
    shutil.copy2(path, backup)
    data.setdefault("provider", {})["spark"] = provider
    path.write_text(json.dumps(data, indent=2) + "\n")
    return backup


# What yaml.safe_load gives for each kind of YAML value but a mapping.
YAML_KINDS = {list: "a list", str: "a string", int: "a number", float: "a number", bool: "true or false"}


def _yaml_kind(value: object) -> str:
    return YAML_KINDS.get(type(value), f"a {type(value).__name__}")


def _load_hermes(path: Path) -> dict:
    """Hermes's config.yaml, a mapping whose providers are a mapping too; or a ClientsError that names the file. Only
    the error's kind and line go on: PyYAML's own message quotes the line it stopped at, and the file can hold other
    providers' keys."""
    try:
        data = yaml.safe_load(path.read_text())
    except OSError as err:
        why = str(err)
    except ValueError:  # the codec's error keeps the file's bytes
        why = "it isn't UTF-8"
    except yaml.YAMLError as err:
        mark = getattr(err, "problem_mark", None)
        why = f"it isn't valid YAML (line {mark.line + 1})" if mark else "it isn't valid YAML"
    else:
        why = None
    if why is not None:
        raise ClientsError(f"Hermes's config {path} won't load: {why}")
    if data is None:  # an empty file
        return {}
    if not isinstance(data, dict):
        raise ClientsError(f"Hermes's config {path} holds {_yaml_kind(data)} at its top level, not the mapping Hermes "
                           "reads")
    if not isinstance(data.get("providers", {}), dict):
        raise ClientsError(f"Hermes's config {path}: its providers are {_yaml_kind(data['providers'])}, not the "
                           "mapping Hermes reads")
    return data


def merge_hermes(path: Path, provider: dict, default: str | None = None,
                 reasoning: dict[str, str] | None = None) -> Path | None:
    """Put `provider` under providers.spark in Hermes's config, keep everything else, and back up the old file, as
    merge_pi does. Hermes keeps the model it had, unless `default` names a Spark model for it: then its model's
    provider and default are set, and the rest of its model settings stay. `reasoning` maps Spark models to the effort
    Hermes sends them, in agent.reasoning_overrides, beside the overrides already there. The file is rewritten from
    its data, so comments in it don't survive; the backup keeps them."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _load_hermes(path) if path.exists() else None
    if default is not None and data is not None and not isinstance(data.get("model", {}), dict):
        raise ClientsError(f"Hermes's config {path}: its model is {_yaml_kind(data['model'])}, not a mapping, so "
                           "--default can't set it; pick the Spark model in Hermes with /model instead")
    if reasoning and data is not None:
        agent = data.get("agent", {})
        if not isinstance(agent, dict):
            raise ClientsError(f"Hermes's config {path}: its agent settings are {_yaml_kind(agent)}, not the mapping "
                               "Hermes reads")
        if not isinstance(agent.get("reasoning_overrides", {}), dict):
            raise ClientsError(f"Hermes's config {path}: its agent.reasoning_overrides are "
                               f"{_yaml_kind(agent['reasoning_overrides'])}, not the mapping Hermes reads")
    backup = None
    if data is None:
        data = {}
    else:
        backup = path.with_suffix(".yaml.bak")
        shutil.copy2(path, backup)
    data.setdefault("providers", {})["spark"] = provider
    if default is not None:
        data.setdefault("model", {}).update({"provider": "custom:spark", "default": default})
    if reasoning:
        data.setdefault("agent", {}).setdefault("reasoning_overrides", {}).update(reasoning)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    return backup


def hermes_placeholder_left(path: Path, key_env: str) -> bool:
    """Whether Hermes's .env still holds the placeholder this command's first version wrote, which Hermes would send as
    the key. Only the verdict leaves here: the file holds other secrets."""
    try:
        lines = path.read_text().splitlines()
    except (OSError, ValueError):
        return False
    return f"{key_env}={HERMES_PLACEHOLDER}" in (line.strip() for line in lines)


def register(subparsers) -> None:
    p = subparsers.add_parser("clients", help="client configs")
    sub = p.add_subparsers(dest="clients_command", required=True)

    pi = sub.add_parser("pi", help="the Spark provider for pi")
    pi.add_argument("--write", action="store_true", help=f"merge it into {PI_MODELS}")
    pi.add_argument("--base-url", default="http://127.0.0.1:9100/v1")
    pi.add_argument("--key-env", default="SPARK_API_KEY")
    pi.add_argument("--registry", type=Path, default=Path("stack/models.yaml"))
    pi.add_argument("--http-idle-timeout-ms", type=_milliseconds, metavar="N",
                    help=f"with --write, also set pi's httpIdleTimeoutMs to N in {PI_SETTINGS}, keeping its other "
                         "settings: how long pi waits for a response (agent's pi: 900000, 15 minutes)")
    pi.set_defaults(func=run_pi)

    opencode = sub.add_parser("opencode", help="the Spark provider for OpenCode")
    opencode.add_argument("--write", action="store_true", help=f"merge it into {OPENCODE_CONFIG}")
    opencode.add_argument("--base-url", default="http://127.0.0.1:9100/v1")
    opencode.add_argument("--key-env", default="SPARK_API_KEY")
    opencode.add_argument("--registry", type=Path, default=Path("stack/models.yaml"))
    opencode.set_defaults(func=run_opencode)

    hermes = sub.add_parser("hermes", help="the Spark provider for Hermes")
    hermes.add_argument("--write", action="store_true", help=f"merge it into {HERMES_CONFIG}")
    hermes.add_argument("--default", metavar="MODEL", help="also make this Spark model Hermes's default")
    hermes.add_argument("--base-url", default="http://127.0.0.1:9100/v1")
    hermes.add_argument("--key-env", default="SPARK_API_KEY")
    hermes.add_argument("--registry", type=Path, default=Path("stack/models.yaml"))
    hermes.set_defaults(func=run_hermes)


def run_pi(args: argparse.Namespace) -> int:
    provider = pi_provider(load_registry(args.registry), args.base_url, args.key_env)
    if not args.write:
        if args.http_idle_timeout_ms is not None:
            raise ClientsError("--http-idle-timeout-ms writes pi's settings file, so it goes with --write; nothing "
                               "was written")
        print(json.dumps(provider, indent=2))
        return 0
    settings = None
    if args.http_idle_timeout_ms is not None:  # read and merged first: a file pi can't read stops both writes
        settings = plan_settings(PI_SETTINGS.expanduser(), {"httpIdleTimeoutMs": args.http_idle_timeout_ms})
    path = PI_MODELS.expanduser()
    backup = merge_pi(path, provider)
    kept = f"the previous file is {backup}" if backup else "there was no previous file"
    print(f"clients: wrote the 'spark' provider to {path} ({kept})")
    if settings is not None:
        backup = write_settings(settings)
        kept = f"the previous file is {backup}" if backup else "there was no previous file"
        print(f"clients: wrote httpIdleTimeoutMs {args.http_idle_timeout_ms} to {settings.path} ({kept})")
    return 0


def run_opencode(args: argparse.Namespace) -> int:
    provider = opencode_provider(load_registry(args.registry), args.base_url, args.key_env)
    if not args.write:
        print(json.dumps({"provider": {"spark": provider}}, indent=2))
        return 0
    path = OPENCODE_CONFIG.expanduser()
    backup = merge_opencode(path, provider)
    kept = f"the previous file is {backup}" if backup else "there was no previous file"
    print(f"clients: wrote the 'spark' provider to {path} ({kept})")
    return 0


def run_hermes(args: argparse.Namespace) -> int:
    registry = load_registry(args.registry)
    chat = [m for m in registry.models.values() if m.capability == "chat"]
    if args.default is not None and args.default not in {m.name for m in chat}:
        raise ClientsError(f"--default {args.default} isn't a chat model in {args.registry}: "
                           f"{', '.join(m.name for m in chat)}")
    provider = hermes_provider(registry, args.base_url, args.key_env)
    reasoning = {m.name: HERMES_REASONING for m in chat}
    if not args.write:
        print(yaml.safe_dump({"providers": {"spark": provider}, "agent": {"reasoning_overrides": reasoning}},
                             sort_keys=False), end="")
        return 0
    path = HERMES_CONFIG.expanduser()
    backup = merge_hermes(path, provider, args.default, reasoning)
    kept = f"the previous file is {backup}" if backup else "there was no previous file"
    print(f"clients: wrote the 'spark' provider to {path} ({kept}), with reasoning effort {HERMES_REASONING} for its "
          "models")
    if args.default is not None:
        print(f"clients: Hermes's default model is now {args.default}, on the Spark")
    elif chat:
        coder = next((m.name for m in chat if "coder" in m.roles), chat[0].name)
        print(f"clients: in Hermes, /model custom:spark:{coder} picks a Spark model")
    if hermes_placeholder_left(HERMES_ENV.expanduser(), args.key_env):
        print(f"clients: {HERMES_ENV} still has the line {args.key_env}={HERMES_PLACEHOLDER}, which an earlier version "
              f"of this command wrote; delete it, or Hermes sends it as the key instead of your {args.key_env}")
    return 0

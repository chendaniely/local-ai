"""The model registry — stack/models.yaml, the one file to edit to add, swap or retire a model — and the private key
list, /etc/local-ai/keys.yaml, which names the client keys."""

from __future__ import annotations

import math
import re
from collections.abc import Hashable
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Literal

import yaml

ENGINES = {"llama.cpp": {"chat", "embeddings"}, "whisper.cpp": {"transcription"}}
NAME = re.compile(r"^[a-z0-9][a-z0-9.-]*$")
REVISION = re.compile(r"^[0-9a-f]{40}$")
# A Hugging Face repo id, org/name, as the Hub allows one: neither part starts or ends with '.' or '-', and '--' and
# '..' never appear ('--' is the Hub cache's separator: models--org--name).
_REPO_PART = r"[A-Za-z0-9_](?:[A-Za-z0-9._-]*[A-Za-z0-9_])?"
REPO = re.compile(rf"{_REPO_PART}/{_REPO_PART}")
# A file in the pinned snapshot: relative, folders split by '/', and every part starting with a letter, a digit or '_',
# so never '.' or '..'.
SNAPSHOT_FILE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]*(?:/[A-Za-z0-9_][A-Za-z0-9._-]*)*")
UNSAFE_ARG = re.compile(r"[\s'\"]")
SECTIONS = ("budget", "brake", "engines", "models", "key_groups", "notifications", "gate")
KEY_NAME = re.compile(r"[a-z0-9-]+")  # a client key's name, as the digests file has it, and a key group's
WORDS = ("dan", "agent")  # whose wording a key group's refusals use
LLAMA_SWAP_PER_MODEL = 10  # llama-swap's own limit on one model's requests: a key's waiting cap stays below it
NOTIFICATION_TYPES = (
    "brake_fired", "brake_needs_release", "gate_down", "front_down", "llama_swap_down", "brake_down", "back_up",
    "refused", "footprint_suspect", "load_failed", "brake_released", "room_hold_ended", "resident_waiting",
    "apply_restarted", "load_started", "loaded", "unloaded", "waiting", "pin_ended", "memory_warning",
)
PRIORITIES = ("high", "default", "low", "off")  # off: the type is never sent


class RegistryError(ValueError):
    pass


class _UniqueKeyLoader(yaml.SafeLoader):
    """yaml.safe_load's loader, except that a key given twice in one mapping is an error: safe_load keeps
    the last one and drops the first without a word, a whole model when its name is repeated."""

    def construct_mapping(self, node, deep=False):
        if isinstance(node, yaml.MappingNode):
            seen = set()
            for key_node, _ in node.value:
                if key_node.tag == "tag:yaml.org,2002:merge":  # `<<: *x`: the mapping's own keys override x's
                    continue
                key = self.construct_object(key_node, deep=deep)
                if not isinstance(key, Hashable):
                    continue  # SafeLoader refuses an unhashable key itself
                if key in seen:
                    line = key_node.start_mark.line + 1
                    raise RegistryError(f"{key!r} is repeated on line {line}; YAML would keep only the last")
                seen.add(key)
        return super().construct_mapping(node, deep=deep)


@dataclass(frozen=True)
class Source:
    repo: str
    revision: str
    file: str
    mmproj: str | None = None


@dataclass(frozen=True)
class Model:
    name: str
    capability: str
    engine: str
    source: Source
    resident: bool
    footprint_gib: float
    footprint_measured: bool
    ctx: int
    parallel: int
    cache_ram_mib: int
    args: tuple[str, ...]
    roles: tuple[str, ...]
    label: str  # the plain-words name messages use: "the coder", "Gemma"
    used_by: str | None = None  # what make-room's list says uses a resident: "the web UI and photos use it"
    needs_room: bool = False  # an on-demand model that loads only after make-room frees room for it


@dataclass(frozen=True)
class Budget:
    allocatable_gib: float
    reserve_gib: float
    idle_available_gib: float  # MemAvailable with no model loaded
    allocatable_measured: bool = False  # allocatable_gib measured on this box, not only reported


@dataclass(frozen=True)
class BrakeThresholds:
    warn_gib: float
    brake_gib: float
    poll_ms: int


@dataclass(frozen=True)
class KeyGroup:
    """What a group of client keys may do: the five things the design's one `dan` flag decided, apart (Dan's decision,
    2026-10-07), its wait and its caps."""

    name: str
    wait_s: int  # how long a request may wait, from when the front receives it, for its model's load to start
    queue: int  # lower goes first
    uses_hold: bool  # may load into make-room's hold
    reloads_marked: bool  # may load the model the brake marked
    words: Literal["dan", "agent"]  # whose wording its refusals use
    names_processes: bool  # whether its refusals and status name Dan's processes
    max_waiting: int  # requests waiting per key
    max_open: int  # open connections per key


@dataclass(frozen=True)
class ClientKey:
    name: str  # as the digests file names it
    group: str
    label: str  # how messages name the asker: "pi on the Mac", "the web UI", "agent"
    account: str | None  # the Unix user whose own refusals this key's are; None for a key no account owns


@dataclass(frozen=True)
class GateSettings:
    idle_unload_min: int  # an on-demand model unloads after this long with no request and no session
    owed_reads_rss: dict[str, bool]  # per engine kind: whether *owed* counts its engine's RssAnon growth as held


@dataclass(frozen=True)
class Registry:
    budget: Budget
    brake: BrakeThresholds
    engines: dict[str, str]
    models: dict[str, Model]
    key_groups: dict[str, KeyGroup]
    notifications: dict[str, str]  # each type in NOTIFICATION_TYPES, in that order, with its priority
    gate: GateSettings

    def static_total_gib(self) -> float:
        return float(sum(m.footprint_gib for m in self.models.values()))


MODEL_KEYS = tuple(f.name for f in fields(Model) if f.name != "name")  # a model's name is its key
SOURCE_KEYS = tuple(f.name for f in fields(Source))
GROUP_KEYS = tuple(f.name for f in fields(KeyGroup) if f.name != "name")  # a group's name is its key
CLIENT_KEY_FIELDS = tuple(f.name for f in fields(ClientKey) if f.name != "name")  # and a key's
GATE_KEYS = tuple(f.name for f in fields(GateSettings))


def _known(where: str, raw: dict, names: tuple[str, ...] | list[str]) -> None:
    """Refuse a key outside `names`: were it ignored, a misspelled optional key would take its default."""
    for given in raw:
        if given not in names:
            raise RegistryError(f"{where}: {given!r} is not one of {', '.join(names)}")


def _number(where: str, raw: dict, key: str, whole: bool = False, default: int | None = None) -> int | float:
    """raw[key] if YAML read a finite number there (a whole one when `whole`), never a string or a bool.
    An absent key gives `default`, and is required when there is no default."""
    if key not in raw and default is None:
        raise RegistryError(f"{where}: {key} is required")
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int if whole else (int, float)):
        kind = "a whole number" if whole else "a number"
        raise RegistryError(f"{where}: {key} must be {kind}, not {value!r}")
    if isinstance(value, float) and not math.isfinite(value):
        raise RegistryError(f"{where}: {key} must be a finite number, not {value!r}")
    return value


def _flag(where: str, raw: dict, key: str) -> bool:
    """raw[key] if YAML read a boolean there, False if absent: bool() would read the string "false" as true."""
    value = raw.get(key, False)
    if not isinstance(value, bool):
        raise RegistryError(f"{where}: {key} must be true or false, not {value!r}")
    return value


def _text(where: str, raw: dict, key: str, required: bool = True) -> str | None:
    """raw[key] if it is text on one line, more than spaces; None if absent and not required. Labels go into
    messages, notifications and status lines, which a line break would split."""
    if key not in raw:
        if required:
            raise RegistryError(f"{where}: {key} is required")
        return None
    value = raw[key]
    if not isinstance(value, str) or not value.strip() or not value.isprintable():  # isprintable: no line break
        raise RegistryError(f"{where}: {key} must be text on one line, not {value!r}")
    return value


def _list(where: str, raw: dict, key: str) -> list:
    """raw[key] if it is a YAML list, [] if absent. A scalar would split into characters."""
    value = raw.get(key, [])
    if not isinstance(value, list):
        raise RegistryError(f"{where}: {key} must be a list")
    return value


def _mapping(data: dict, key: str) -> dict:
    """data[key] if it is a YAML mapping, {} if absent."""
    value = data.get(key, {})
    if not isinstance(value, dict):
        raise RegistryError(f"{key}: must be a mapping, keyed by name")
    return value


def _required(data: dict, key: str) -> dict:
    """data[key], a section keyed by name that every registry has."""
    if key not in data:
        raise RegistryError(f"{key}: the section is required")
    return _mapping(data, key)


def _section(data: dict, key: str, cls: type):
    """budget or brake: a mapping of exactly cls's fields, each number positive, each flag true or false (false when
    absent)."""
    names = [f.name for f in fields(cls)]
    raw = data.get(key)
    if raw is None:
        raise RegistryError(f"{key}: the section is required ({', '.join(names)})")
    if not isinstance(raw, dict):
        raise RegistryError(f"{key}: must be a mapping of {', '.join(names)}")
    _known(key, raw, names)
    # f.type is the annotation: the string "int" under `from __future__ import annotations`, else the class.
    numbers = [f for f in fields(cls) if f.type not in ("bool", bool)]
    values = {f.name: _number(key, raw, f.name, whole=f.type in ("int", int)) for f in numbers}
    for field, value in values.items():
        if value <= 0:
            raise RegistryError(f"{key}: {field} must be positive, not {value!r}")
    flags = {f.name: _flag(key, raw, f.name) for f in fields(cls) if f.name not in values}
    return cls(**values, **flags)


def _model(name: str, raw: dict, engines: dict[str, str]) -> Model:
    if not isinstance(name, str) or not NAME.fullmatch(name):  # fullmatch: `$` also matches before a final newline
        raise RegistryError(f"{name!r}: names are lowercase letters, digits, '.' and '-'")
    if not isinstance(raw, dict):
        raise RegistryError(f"{name}: a model must be a mapping of its fields")
    _known(name, raw, MODEL_KEYS)
    engine = raw.get("engine")
    if not isinstance(engine, str) or engine not in engines:
        raise RegistryError(f"{name}: engine {engine!r} is not in the engines table")
    capability = raw.get("capability")
    if not isinstance(capability, str) or capability not in ENGINES.get(engine, set()):
        raise RegistryError(f"{name}: capability {capability!r} is not served by {engine}")
    src = raw.get("source")
    if not isinstance(src, dict):
        raise RegistryError(f"{name}: source must be a mapping of repo, revision and file")
    _known(f"{name}: source", src, SOURCE_KEYS)
    if not REVISION.fullmatch(str(src.get("revision", ""))):
        raise RegistryError(f"{name}: source.revision must be a 40-hex commit, not a branch or tag")
    for key in ("repo", "file"):
        if src.get(key) in (None, ""):
            raise RegistryError(f"{name}: source.{key} is required")
        if not isinstance(src[key], str):
            raise RegistryError(f"{name}: source.{key} must be a string, not {src[key]!r}")
    if "mmproj" in src and (not isinstance(src["mmproj"], str) or not src["mmproj"]):
        raise RegistryError(
            f"{name}: source.mmproj must be a non-empty string when present, not {src['mmproj']!r}"
        )
    repo = src["repo"]
    if not REPO.fullmatch(repo) or "--" in repo or ".." in repo:
        raise RegistryError(f"{name}: source.repo must be org/name as the Hub names a repo: letters, digits, '.', '_' "
                            f"and '-', neither part starting or ending with '.' or '-', no '--' or '..'; not {repo!r}")
    for key in ("file", "mmproj"):
        # The engine gets <the snapshot>/<file>: anything else could name a file the pinned revision doesn't have.
        if key in src and not SNAPSHOT_FILE.fullmatch(src[key]):
            raise RegistryError(f"{name}: source.{key} must be a relative path inside the pinned snapshot: letters, "
                                f"digits, '.', '_' and '-', folders split by '/', no part starting with '.' or '-' "
                                f"(so no '..'); not {src[key]!r}")
    listed_args = _list(name, raw, "args")
    for arg in listed_args:
        if isinstance(arg, (dict, list)):
            raise RegistryError(f"{name}: args must be a flat list; {arg!r} is nested")
        if isinstance(arg, bool):  # YAML 1.1 reads an unquoted on/off, yes/no or true/false as one
            raise RegistryError(
                f"{name}: args may not hold a boolean ({arg!r}); quote on/off, yes/no or true/false"
            )
        if not isinstance(arg, (str, int, float)):  # null (an empty item), a date, binary
            raise RegistryError(f"{name}: args may not hold {arg!r}; quote it or remove the empty item")
    args = tuple(str(a) for a in listed_args)
    if any(UNSAFE_ARG.search(a) for a in args):
        raise RegistryError(f"{name}: args may not contain whitespace or quotes")
    roles = _list(name, raw, "roles")
    for role in roles:
        if not isinstance(role, str):
            raise RegistryError(f"{name}: roles must be a list of strings; {role!r} is not one")
    model = Model(
        name=name,
        capability=capability,
        engine=engine,
        source=Source(src["repo"], src["revision"], src["file"], src.get("mmproj")),
        resident=_flag(name, raw, "resident"),
        footprint_gib=float(_number(name, raw, "footprint_gib")),
        footprint_measured=_flag(name, raw, "footprint_measured"),
        ctx=_number(name, raw, "ctx", whole=True),
        parallel=_number(name, raw, "parallel", whole=True, default=1),
        cache_ram_mib=_number(name, raw, "cache_ram_mib", whole=True, default=0),
        args=args,
        roles=tuple(roles),
        label=_text(name, raw, "label"),
        used_by=_text(name, raw, "used_by", required=False),
        needs_room=_flag(name, raw, "needs_room"),
    )
    if model.footprint_gib <= 0 or model.ctx <= 0 or model.parallel < 1 or model.cache_ram_mib < 0:
        raise RegistryError(f"{name}: footprint, ctx and parallel must be positive; cache_ram_mib ≥ 0")
    if model.needs_room and model.resident:  # a resident is loaded from the start: there is no room to make for it
        raise RegistryError(f"{name}: needs_room is only for an on-demand model, and {name} is resident")
    return model


def _key_group(name: str, raw: dict) -> KeyGroup:
    if not isinstance(name, str) or not KEY_NAME.fullmatch(name):
        raise RegistryError(f"key_groups: {name!r}: names are lowercase letters, digits and '-'")
    where = f"key_groups: {name}"
    if not isinstance(raw, dict):
        raise RegistryError(f"{where}: a group must be a mapping of {', '.join(GROUP_KEYS)}")
    _known(where, raw, GROUP_KEYS)
    for key in GROUP_KEYS:  # each one, so that no group takes a default it wasn't given
        if key not in raw:
            raise RegistryError(f"{where}: {key} is required")
    wait_s = _number(where, raw, "wait_s", whole=True)
    if wait_s <= 0:
        raise RegistryError(f"{where}: wait_s must be a positive whole number of seconds, not {wait_s!r}")
    queue = _number(where, raw, "queue", whole=True)
    if queue < 0:
        raise RegistryError(f"{where}: queue must be a whole number from 0, not {queue!r}")
    uses_hold, reloads_marked = _flag(where, raw, "uses_hold"), _flag(where, raw, "reloads_marked")
    words = raw["words"]
    if not isinstance(words, str) or words not in WORDS:
        raise RegistryError(f"{where}: words must be {' or '.join(WORDS)}, not {words!r}")
    names_processes = _flag(where, raw, "names_processes")
    max_waiting = _number(where, raw, "max_waiting", whole=True)
    if not 1 <= max_waiting < LLAMA_SWAP_PER_MODEL:
        raise RegistryError(f"{where}: max_waiting must be from 1 to {LLAMA_SWAP_PER_MODEL - 1}, below llama-swap's "
                            f"{LLAMA_SWAP_PER_MODEL} per model, not {max_waiting!r}")
    max_open = _number(where, raw, "max_open", whole=True)
    if max_open < max_waiting:
        raise RegistryError(f"{where}: max_open must be at least max_waiting ({max_waiting}), not {max_open!r}")
    return KeyGroup(name, wait_s, queue, uses_hold, reloads_marked, words, names_processes, max_waiting, max_open)


def _notifications(data: dict) -> dict[str, str]:
    """Every type in NOTIFICATION_TYPES once, with its priority, in that order; no other key."""
    raw = _required(data, "notifications")
    _known("notifications", raw, NOTIFICATION_TYPES)
    allowed = f"{', '.join(PRIORITIES[:-1])} or {PRIORITIES[-1]}"
    for kind in NOTIFICATION_TYPES:
        if kind not in raw:
            raise RegistryError(f"notifications: {kind} is missing; every notification type is listed, with its "
                                f"priority ({allowed})")
        priority = raw[kind]
        if not isinstance(priority, str) or priority not in PRIORITIES:
            # YAML 1.1 reads an unquoted off as false.
            hint = '; quote it: "off"' if priority is False else ""
            raise RegistryError(f"notifications: {kind} must be {allowed}, not {priority!r}{hint}")
    return {kind: raw[kind] for kind in NOTIFICATION_TYPES}


def _gate(data: dict, engines: dict[str, str]) -> GateSettings:
    raw = data.get("gate")
    if raw is None:
        raise RegistryError(f"gate: the section is required ({', '.join(GATE_KEYS)})")
    if not isinstance(raw, dict):
        raise RegistryError(f"gate: must be a mapping of {', '.join(GATE_KEYS)}")
    _known("gate", raw, GATE_KEYS)
    idle = _number("gate", raw, "idle_unload_min", whole=True)
    if idle <= 0:
        raise RegistryError(f"gate: idle_unload_min must be a positive whole number of minutes, not {idle!r}")
    if "owed_reads_rss" not in raw:
        raise RegistryError("gate: owed_reads_rss is required")
    owed = raw["owed_reads_rss"]
    if not isinstance(owed, dict):
        raise RegistryError("gate: owed_reads_rss must be a mapping of each engine kind to true or false")
    # Exactly the engines table's kinds: the soak may show RssAnon following one engine's growth and not another's.
    _known("gate: owed_reads_rss", owed, list(engines))
    for engine in engines:
        if engine not in owed:
            raise RegistryError(f"gate: owed_reads_rss must name every engine kind; {engine} is missing")
    return GateSettings(idle, {engine: _flag("gate: owed_reads_rss", owed, engine) for engine in engines})


def load_registry(path: Path) -> Registry:
    data = yaml.load(Path(path).read_text(), Loader=_UniqueKeyLoader) or {}
    if not isinstance(data, dict):
        raise RegistryError(f"the registry must be a mapping of {', '.join(SECTIONS)}")
    if "keys" in data:
        raise RegistryError("keys: the key list isn't the registry's: it lives in /etc/local-ai/keys.yaml, on the "
                            "Spark only, never in the repo (stack/keys.example.yaml shows its shape)")
    _known("the registry", data, SECTIONS)
    budget = _section(data, "budget", Budget)
    brake = _section(data, "brake", BrakeThresholds)
    if not brake.warn_gib > brake.brake_gib:
        raise RegistryError("brake: warn_gib must be above brake_gib")
    if not budget.reserve_gib > brake.brake_gib:
        raise RegistryError("budget: reserve_gib must exceed brake.brake_gib, or a fresh load trips the brake")
    if not budget.idle_available_gib > budget.reserve_gib:
        raise RegistryError(f"budget: idle_available_gib ({budget.idle_available_gib}) must exceed reserve_gib "
                            f"({budget.reserve_gib}), which every load leaves free")
    engines: dict[str, str] = {}
    for engine, binary in _mapping(data, "engines").items():
        if not isinstance(binary, str) or not binary:
            raise RegistryError(f"engines: {engine} must be the path to its binary, not {binary!r}")
        engines[str(engine)] = binary
    models = {name: _model(name, raw, engines) for name, raw in _mapping(data, "models").items()}
    seen: dict[str, str] = {}
    for model in models.values():
        for role in model.roles:
            if role in seen:
                raise RegistryError(f"role {role!r} is used by both {seen[role]} and {model.name}")
            # llama-swap looks a name up among the models before the roles. A role that repeats its own model's
            # name finds that model either way; another model's name never reaches this one.
            if role in models and role != model.name:
                raise RegistryError(f"{model.name}: role {role!r} is another model's name, and llama-swap finds a "
                                    f"model by its name first, so the role would never reach {model.name}")
            seen[role] = model.name
    key_groups = {name: _key_group(name, raw) for name, raw in _required(data, "key_groups").items()}
    return Registry(budget, brake, engines, models, key_groups, _notifications(data), _gate(data, engines))


def load_keys(path: Path, groups: dict[str, KeyGroup]) -> dict[str, ClientKey]:
    """The private key list, /etc/local-ai/keys.yaml (paths.KEYS), checked against the registry's key groups: each
    client key's name, as the digests file has it, with its group, its label and the account that owns it. Names and
    words only, never a key or a digest; and never in the repo, where stack/keys.example.yaml shows its shape."""
    data = yaml.load(Path(path).read_text(), Loader=_UniqueKeyLoader) or {}
    if not isinstance(data, dict):
        raise RegistryError("the key list must be a mapping of keys, each key's name to its group, label and account")
    _known("the key list", data, ("keys",))
    if "keys" not in data:
        raise RegistryError("keys: the list is required, each key's name to its group, label and account")
    keys: dict[str, ClientKey] = {}
    for name, raw in _mapping(data, "keys").items():
        if not isinstance(name, str) or not KEY_NAME.fullmatch(name):
            raise RegistryError(f"keys: {name!r}: names are lowercase letters, digits and '-'")
        where = f"keys: {name}"
        if not isinstance(raw, dict):
            raise RegistryError(f"{where}: a key must be a mapping of {', '.join(CLIENT_KEY_FIELDS)}")
        _known(where, raw, CLIENT_KEY_FIELDS)
        if "group" not in raw:
            raise RegistryError(f"{where}: group is required")
        group = raw["group"]
        if not isinstance(group, str) or group not in groups:
            raise RegistryError(f"{where}: group {group!r} is not one of {', '.join(groups)}")
        keys[name] = ClientKey(name, group, _text(where, raw, "label"), _text(where, raw, "account", required=False))
    return keys

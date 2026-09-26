"""The model registry — stack/models.yaml, the one file to edit to add, swap or retire a model."""

from __future__ import annotations

import math
import re
from collections.abc import Hashable
from dataclasses import dataclass, fields
from pathlib import Path

import yaml

ENGINES = {"llama.cpp": {"chat", "embeddings"}, "whisper.cpp": {"transcription"}}
NAME = re.compile(r"^[a-z0-9][a-z0-9.-]*$")
REVISION = re.compile(r"^[0-9a-f]{40}$")
UNSAFE_ARG = re.compile(r"[\s'\"]")
SECTIONS = ("budget", "brake", "engines", "models")


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


@dataclass(frozen=True)
class Budget:
    allocatable_gib: float
    reserve_gib: float


@dataclass(frozen=True)
class BrakeThresholds:
    warn_gib: float
    brake_gib: float
    poll_ms: int


@dataclass(frozen=True)
class Registry:
    budget: Budget
    brake: BrakeThresholds
    engines: dict[str, str]
    models: dict[str, Model]

    def static_total_gib(self) -> float:
        return float(sum(m.footprint_gib for m in self.models.values()))


MODEL_KEYS = tuple(f.name for f in fields(Model) if f.name != "name")  # a model's name is its key
SOURCE_KEYS = tuple(f.name for f in fields(Source))


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


def _section(data: dict, key: str, cls: type):
    """budget or brake: a mapping of exactly cls's fields, each a positive number."""
    names = [f.name for f in fields(cls)]
    raw = data.get(key)
    if raw is None:
        raise RegistryError(f"{key}: the section is required ({', '.join(names)})")
    if not isinstance(raw, dict):
        raise RegistryError(f"{key}: must be a mapping of {', '.join(names)}")
    _known(key, raw, names)
    # f.type is the annotation: the string "int" under `from __future__ import annotations`, else the class.
    values = {f.name: _number(key, raw, f.name, whole=f.type in ("int", int)) for f in fields(cls)}
    for field, value in values.items():
        if value <= 0:
            raise RegistryError(f"{key}: {field} must be positive, not {value!r}")
    return cls(**values)


def _model(name: str, raw: dict, engines: dict[str, str]) -> Model:
    if not isinstance(name, str) or not NAME.match(name):
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
    if not REVISION.match(str(src.get("revision", ""))):
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
    )
    if model.footprint_gib <= 0 or model.ctx <= 0 or model.parallel < 1 or model.cache_ram_mib < 0:
        raise RegistryError(f"{name}: footprint, ctx and parallel must be positive; cache_ram_mib ≥ 0")
    return model


def load_registry(path: Path) -> Registry:
    data = yaml.load(Path(path).read_text(), Loader=_UniqueKeyLoader) or {}
    if not isinstance(data, dict):
        raise RegistryError("the registry must be a mapping of budget, brake, engines and models")
    _known("the registry", data, SECTIONS)
    budget = _section(data, "budget", Budget)
    brake = _section(data, "brake", BrakeThresholds)
    if not brake.warn_gib > brake.brake_gib:
        raise RegistryError("brake: warn_gib must be above brake_gib")
    if not budget.reserve_gib > brake.brake_gib:
        raise RegistryError("budget: reserve_gib must exceed brake.brake_gib, or a fresh load trips the brake")
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
    return Registry(budget, brake, engines, models)

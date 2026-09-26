"""The model registry — stack/models.yaml, the one file to edit to add, swap or retire a model."""

from __future__ import annotations

import re
from dataclasses import dataclass, fields
from pathlib import Path

import yaml

ENGINES = {"llama.cpp": {"chat", "embeddings"}, "whisper.cpp": {"transcription"}}
NAME = re.compile(r"^[a-z0-9][a-z0-9.-]*$")
REVISION = re.compile(r"^[0-9a-f]{40}$")
UNSAFE_ARG = re.compile(r"[\s'\"]")


class RegistryError(ValueError):
    pass


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


def _number(where: str, raw: dict, key: str, whole: bool = False, default: int | None = None) -> int | float:
    """raw[key] if YAML read a number there (a whole one when `whole`), never a string or a bool.
    An absent key gives `default`, and is required when there is no default."""
    if key not in raw and default is None:
        raise RegistryError(f"{where}: {key} is required")
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int if whole else (int, float)):
        kind = "a whole number" if whole else "a number"
        raise RegistryError(f"{where}: {key} must be {kind}, not {value!r}")
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
    """budget or brake: a mapping of exactly cls's fields, each a number."""
    names = [f.name for f in fields(cls)]
    raw = data.get(key)
    if raw is None:
        raise RegistryError(f"{key}: the section is required ({', '.join(names)})")
    if not isinstance(raw, dict):
        raise RegistryError(f"{key}: must be a mapping of {', '.join(names)}")
    for given in raw:
        if given not in names:
            raise RegistryError(f"{key}: {given!r} is not one of {', '.join(names)}")
    # f.type is the annotation as a string ("int" or "float"): see `from __future__ import annotations`.
    return cls(**{f.name: _number(key, raw, f.name, whole=f.type == "int") for f in fields(cls)})


def _model(name: str, raw: dict, engines: dict[str, str]) -> Model:
    if not NAME.match(name):
        raise RegistryError(f"{name}: names are lowercase letters, digits, '.' and '-'")
    if not isinstance(raw, dict):
        raise RegistryError(f"{name}: a model must be a mapping of its fields")
    engine = raw.get("engine")
    if not isinstance(engine, str) or engine not in engines:
        raise RegistryError(f"{name}: engine {engine!r} is not in the engines table")
    capability = raw.get("capability")
    if not isinstance(capability, str) or capability not in ENGINES.get(engine, set()):
        raise RegistryError(f"{name}: capability {capability!r} is not served by {engine}")
    src = raw.get("source")
    if not isinstance(src, dict):
        raise RegistryError(f"{name}: source must be a mapping of repo, revision and file")
    if not REVISION.match(str(src.get("revision", ""))):
        raise RegistryError(f"{name}: source.revision must be a 40-hex commit, not a branch or tag")
    for key in ("repo", "file"):
        if src.get(key) in (None, ""):
            raise RegistryError(f"{name}: source.{key} is required")
    listed_args = _list(name, raw, "args")
    for arg in listed_args:
        if isinstance(arg, (dict, list)):
            raise RegistryError(f"{name}: args must be a flat list; {arg!r} is nested")
        if isinstance(arg, bool):  # YAML 1.1 reads an unquoted on/off, yes/no or true/false as one
            raise RegistryError(
                f"{name}: args may not hold a boolean ({arg!r}); quote on/off, yes/no or true/false"
            )
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
        resident=bool(raw.get("resident", False)),
        footprint_gib=float(_number(name, raw, "footprint_gib")),
        footprint_measured=bool(raw.get("footprint_measured", False)),
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
    data = yaml.safe_load(Path(path).read_text()) or {}
    if not isinstance(data, dict):
        raise RegistryError("the registry must be a mapping of budget, brake, engines and models")
    budget = _section(data, "budget", Budget)
    brake = _section(data, "brake", BrakeThresholds)
    if not brake.warn_gib > brake.brake_gib:
        raise RegistryError("brake: warn_gib must be above brake_gib")
    if not budget.reserve_gib > brake.brake_gib:
        raise RegistryError("budget: reserve_gib must exceed brake.brake_gib, or a fresh load trips the brake")
    engines = {str(k): str(v) for k, v in _mapping(data, "engines").items()}
    models = {name: _model(name, raw, engines) for name, raw in _mapping(data, "models").items()}
    seen: dict[str, str] = {}
    for model in models.values():
        for role in model.roles:
            if role in seen:
                raise RegistryError(f"role {role!r} is used by both {seen[role]} and {model.name}")
            seen[role] = model.name
    return Registry(budget, brake, engines, models)

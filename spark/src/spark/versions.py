"""Pinned component versions — the single source of truth in stack/versions.yaml."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

MACHINES = {"mac", "spark", "synology", "ci"}
PIN = re.compile(r"^sha256:[0-9a-f]{64}$")


class VersionsError(ValueError):
    pass


@dataclass(frozen=True)
class Component:
    name: str
    version: str
    where: tuple[str, ...]
    pin: str | None
    deployed: bool
    docs: str
    context7: str | None
    changelog: str
    advisories: str | None


def load_versions(path: Path) -> dict[str, Component]:
    data = yaml.safe_load(Path(path).read_text()) or {}
    components: dict[str, Component] = {}
    for name, raw in (data.get("components") or {}).items():
        where = tuple(raw.get("where") or ())
        if not where or not set(where) <= MACHINES:
            raise VersionsError(f"{name}: 'where' must list some of {sorted(MACHINES)}")
        pin = raw.get("pin")
        if pin is not None and not PIN.match(str(pin)):
            raise VersionsError(f"{name}: pin must look like sha256:<64 hex> or be null")
        for key in ("docs", "changelog"):
            if not str(raw.get(key, "")).startswith("https://"):
                raise VersionsError(f"{name}: {key} must be an https:// URL")
        components[name] = Component(
            name=name,
            version=str(raw["version"]),
            where=where,
            pin=pin,
            deployed=bool(raw.get("deployed", False)),
            docs=raw["docs"],
            context7=raw.get("context7"),
            changelog=raw["changelog"],
            advisories=raw.get("advisories"),
        )
    return components


def unpinned(components: dict[str, Component]) -> list[str]:
    return sorted(c.name for c in components.values() if c.deployed and c.pin is None)


def render_stack_page(components: dict[str, Component]) -> str:
    lines = [
        "---",
        'title: "Stack"',
        'description: "Every pinned component."',
        "---",
        "",
        "This page is generated from `stack/versions.yaml` by `spark docs stack --write`. Edit that",
        "file, not this one.",
        "",
        "| Component | Version | Runs on | Pinned | Docs | Changelog | Advisories |",
        "|---|---|---|---|---|---|---|",
    ]
    for c in sorted(components.values(), key=lambda c: c.name):
        pinned = "yes" if c.pin else ("not yet" if c.deployed else "n/a")
        advisories = f"[advisories]({c.advisories})" if c.advisories else "—"
        lines.append(
            f"| {c.name} | {c.version} | {', '.join(c.where)} | {pinned} | [docs]({c.docs}) "
            f"| [changelog]({c.changelog}) | {advisories} |"
        )
    return "\n".join(lines) + "\n"

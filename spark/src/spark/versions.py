"""Pinned component versions — the single source of truth in stack/versions.yaml."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

MACHINES = {"mac", "spark", "synology", "ci"}
PIN = re.compile(r"^(sha256:[0-9a-f]{64}|git:[0-9a-f]{40})$")
# A version and an image go into files root runs (the llama-swap unit's ExecStart, Compose's image:) and into the
# Stack page's table, so each is plain text. A version is a Docker tag and a path segment; an image is a lowercase
# registry path, a port after its host at most, with neither tag nor digest (those are version and pin).
VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_IMAGE_PART = r"[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?"
IMAGE = re.compile(rf"{_IMAGE_PART}(?::[0-9]+)?(?:/{_IMAGE_PART})*")


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
    image: str | None = None


def load_versions(path: Path) -> dict[str, Component]:
    data = yaml.safe_load(Path(path).read_text()) or {}
    components: dict[str, Component] = {}
    for name, raw in (data.get("components") or {}).items():
        if raw.get("version") in (None, ""):
            raise VersionsError(f"{name}: version is required")
        version = raw["version"]
        if not isinstance(version, str):  # `version: 1.10` reads as the number 1.1
            raise VersionsError(f"{name}: version must be text, in quotes if it looks like a number; YAML read "
                                f"{version!r}")
        if not VERSION.fullmatch(version):  # fullmatch: `$` also matches before a final newline
            raise VersionsError(f"{name}: version must be letters, digits, '.', '_' and '-', starting with a letter "
                                f"or digit; not {version!r}")
        image = raw.get("image")
        if image is not None and not (isinstance(image, str) and IMAGE.fullmatch(image)):
            raise VersionsError(f"{name}: image must be a lowercase registry path, a port after its host at most, "
                                f"without a tag or digest (those are version and pin); not {image!r}")
        where = tuple(raw.get("where") or ())
        if not where or not set(where) <= MACHINES:
            raise VersionsError(f"{name}: 'where' must list some of {sorted(MACHINES)}")
        pin = raw.get("pin")
        if pin is not None and not PIN.fullmatch(str(pin)):
            raise VersionsError(f"{name}: pin must be sha256:<64 hex>, git:<40 hex>, or null")
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
            image=raw.get("image"),
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

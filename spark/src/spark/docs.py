"""`spark docs` — generated docs pages and docs checks."""

from __future__ import annotations

import argparse
import datetime as _dt
import re as _re
import sys
from pathlib import Path

import yaml as _yaml

from spark.versions import load_versions, render_stack_page

VERSIONS = Path("stack/versions.yaml")
STACK_PAGE = Path("website/reference/stack.md")
SCENARIOS = Path("website/scenarios")
STATUSES = {"planned", "built", "verified"}
_NAME = _re.compile(r"^s(\d{2})-[a-z0-9-]+\.md$")


def _front_matter(text: str) -> dict | None:
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 4)
    if end == -1:
        return None
    return _yaml.safe_load(text[4:end]) or {}


def check_scenarios(directory: Path) -> list[str]:
    problems: list[str] = []
    for path in sorted(Path(directory).glob("s[0-9][0-9]-*.md")):
        name = path.name
        match = _NAME.match(name)
        meta = _front_matter(path.read_text())
        if match is None or meta is None:
            problems.append(f"{name}: needs YAML front matter and an sNN-slug.md name")
            continue
        expected = f"S{match.group(1)}"
        if meta.get("scenario-id") != expected:
            problems.append(f"{name}: scenario-id should be {expected}")
        if not str(meta.get("title", "")).startswith(expected):
            problems.append(f"{name}: title should start with {expected}")
        if meta.get("status") not in STATUSES:
            problems.append(f"{name}: status must be one of {sorted(STATUSES)}")
        if meta.get("phase") not in {1, 2, 3, 4, 5, "backlog"}:
            problems.append(f"{name}: phase must be 1–5 or backlog")
        if meta.get("status") == "verified" and not isinstance(meta.get("verified"), _dt.date):
            problems.append(f"{name}: a verified scenario needs verified: YYYY-MM-DD")
    return problems


def register(subparsers) -> None:
    p = subparsers.add_parser("docs", help="generate and check docs pages")
    sub = p.add_subparsers(dest="docs_command", required=True)
    stack = sub.add_parser("stack", help="the Stack page from stack/versions.yaml")
    mode = stack.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    stack.set_defaults(func=run_stack)
    check = sub.add_parser("check-scenarios", help="validate website/scenarios/*.md front matter")
    check.set_defaults(func=run_check_scenarios)


def run_stack(args: argparse.Namespace) -> int:
    page = render_stack_page(load_versions(VERSIONS))
    if args.write:
        STACK_PAGE.parent.mkdir(parents=True, exist_ok=True)
        STACK_PAGE.write_text(page)
        return 0
    if not STACK_PAGE.exists() or STACK_PAGE.read_text() != page:
        print("docs: website/reference/stack.md is stale — run `make docs`", file=sys.stderr)
        return 1
    return 0


def run_check_scenarios(args: argparse.Namespace) -> int:
    problems = check_scenarios(SCENARIOS)
    for problem in problems:
        print(f"docs: {problem}", file=sys.stderr)
    return 1 if problems else 0

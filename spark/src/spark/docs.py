"""`spark docs` — generated docs pages and docs checks."""

from __future__ import annotations

import argparse
import datetime as _dt
import re as _re
import sys
from pathlib import Path

import yaml as _yaml

from spark import messages
from spark.registry import NOTIFICATION_TYPES, Registry, load_registry
from spark.versions import load_versions, render_stack_page

VERSIONS = Path("stack/versions.yaml")
STACK_PAGE = Path("website/reference/stack.md")
REGISTRY_FILE = Path("stack/models.yaml")
NOTIFICATIONS_PAGE = Path("website/reference/notifications.md")
# The four the failure notifier sends: its unit carries their priorities, written by install-units (Task 25).
NOTIFIER_TYPES = ("gate_down", "front_down", "llama_swap_down", "brake_down")
SCENARIOS = Path("website/scenarios")
STATUSES = {"planned", "built", "verified"}
_NAME = _re.compile(r"^s(\d{2})-[a-z0-9-]+\.md$")


def _front_matter(text: str) -> dict | None:
    """The page's front matter, or None when it has none or it isn't a mapping.

    Raises yaml.YAMLError when the front matter isn't valid YAML.
    """
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 4)
    if end == -1:
        return None
    meta = _yaml.safe_load(text[4:end])
    if meta is None:
        return {}
    return meta if isinstance(meta, dict) else None


def check_scenarios(directory: Path) -> list[str]:
    problems: list[str] = []
    for path in sorted(Path(directory).glob("s[0-9][0-9]-*.md")):
        name = path.name
        match = _NAME.match(name)
        try:
            meta = _front_matter(path.read_text())
        except _yaml.YAMLError as err:
            problem = getattr(err, "problem", None) or "the parser gave no detail"
            problems.append(f"{name}: front matter is not valid YAML ({problem})")
            continue
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


def render_notifications_page(registry: Registry) -> str:
    """The Notifications page: every type, in NOTIFICATION_TYPES' order, with the registry's priority, and its *When*
    and *Example* from messages.NOTIFICATION_DOC."""
    rows = []
    for kind in NOTIFICATION_TYPES:
        when, example = messages.NOTIFICATION_DOC[kind]
        rows.append(f"| `{kind}` | {registry.notifications[kind]} | {_cell(when)} | {_cell(example)} |")
    four = ", ".join(f"`{kind}`" for kind in NOTIFIER_TYPES[:-1]) + f" or `{NOTIFIER_TYPES[-1]}`"
    return "\n".join([
        "---",
        'title: "Notifications"',
        'description: "Every notification type on Dan\'s phone, with its priority."',
        "---",
        "",
        "This page is generated from `stack/models.yaml` by `spark docs notifications --write`: the list of",
        "types and their priorities from there, and the words from `spark/src/spark/messages.py`. Edit",
        "those, not this page.",
        "",
        "Phase 2a's gate, brake and failure notifier send these to Dan's phone, through ntfy, once they are",
        "deployed (Task 38 of the [implementation plan](../design/phase-2a.md)). Each type's priority is",
        "`high`, `default` or `low`, or `off`, which sends none; changing one is a one-line edit in",
        "`stack/models.yaml`'s `notifications` section, then `make apply` on the Spark. The examples share",
        "the moments of the plan's [*What you see in",
        "Phase 2a*](../design/plan.md#what-you-see-in-phase-2a).",
        "",
        "| Type | Priority | When | Example |",
        "|---|---|---|---|",
        *rows,
        "",
        f"A change to the priority of {four}",
        "needs `make install-units` after `make apply`, both on the Spark, since the failure notifier's",
        "unit carries those four (Task 25 of the implementation plan).",
        "",
    ])


def _cell(text: str) -> str:
    """Text for a table cell: a `|` would end the cell."""
    return text.replace("|", "\\|")


def register(subparsers) -> None:
    p = subparsers.add_parser("docs", help="generate and check docs pages")
    sub = p.add_subparsers(dest="docs_command", required=True)
    stack = sub.add_parser("stack", help="the Stack page from stack/versions.yaml")
    mode = stack.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    stack.set_defaults(func=run_stack)
    notifications = sub.add_parser("notifications", help="the Notifications page from stack/models.yaml")
    mode = notifications.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    notifications.set_defaults(func=run_notifications)
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


def run_notifications(args: argparse.Namespace) -> int:
    page = render_notifications_page(load_registry(REGISTRY_FILE))
    if args.write:
        NOTIFICATIONS_PAGE.parent.mkdir(parents=True, exist_ok=True)
        NOTIFICATIONS_PAGE.write_text(page)
        return 0
    if not NOTIFICATIONS_PAGE.exists() or NOTIFICATIONS_PAGE.read_text() != page:
        print(f"docs: {NOTIFICATIONS_PAGE} is stale — run `spark docs notifications --write` (or `make docs`, "
              "which also renders the site, on the Mac)", file=sys.stderr)
        return 1
    return 0


def run_check_scenarios(args: argparse.Namespace) -> int:
    problems = check_scenarios(SCENARIOS)
    for problem in problems:
        print(f"docs: {problem}", file=sys.stderr)
    return 1 if problems else 0

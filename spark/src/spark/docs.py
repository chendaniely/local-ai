"""`spark docs` — generated docs pages and docs checks."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from spark.versions import load_versions, render_stack_page

VERSIONS = Path("stack/versions.yaml")
STACK_PAGE = Path("website/reference/stack.md")


def register(subparsers) -> None:
    p = subparsers.add_parser("docs", help="generate and check docs pages")
    sub = p.add_subparsers(dest="docs_command", required=True)
    stack = sub.add_parser("stack", help="the Stack page from stack/versions.yaml")
    mode = stack.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    stack.set_defaults(func=run_stack)


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

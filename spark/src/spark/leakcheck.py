"""Leak check for this public repo.

Flags private addresses, per-unit identifiers and privately denylisted terms. Runs from the git
hooks on every commit (`--staged`, `--message FILE`) and in CI over every tracked file
(`--tracked --ci`). It fails closed: a missing denylist is an error, never a pass.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ALLOW_MARKER = "leakcheck: allow"
DEFAULT_DENYLIST = "~/.config/local-ai/denylist"

PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "private IPv4 address",
        re.compile(
            r"(?<![\d.])(?:10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])|192\.168)\.\d{1,3}\.\d{1,3}(?![\d.])"
        ),
    ),
    (
        "tailnet IPv4 address",
        re.compile(r"(?<![\d.])100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}(?![\d.])"),
    ),
    (
        "MAC address",
        re.compile(r"(?<![0-9A-Fa-f:-])(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}(?![0-9A-Fa-f:-])"),
    ),
    ("tailnet hostname", re.compile(r"\b[a-z0-9-]+\.[a-z0-9-]+\.ts\.net\b", re.IGNORECASE)),
    (
        "Synology DDNS or QuickConnect name",
        re.compile(r"\b[a-z0-9-]+\.(?:synology\.me|quickconnect\.to)\b", re.IGNORECASE),
    ),
    ("macOS volume path", re.compile(r"/Volumes/[^\s/`'\"]+")),  # leakcheck: allow
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)


@dataclass(frozen=True)
class Finding:
    source: str
    line: int
    kind: str
    excerpt: str


class DenylistMissing(RuntimeError):
    pass


def load_denylist(path: Path) -> list[re.Pattern[str]]:
    path = Path(path).expanduser()
    if not path.is_file():
        raise DenylistMissing(
            f"denylist not found at {path} — create it (website/how-to/leak-guards.md)"
        )
    patterns = []
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            patterns.append(re.compile(line, re.IGNORECASE))
    return patterns


def _redact(text: str) -> str:
    return text[:3] + "…"


def scan_text(source: str, text: str, denylist: list[re.Pattern[str]]) -> list[Finding]:
    findings: list[Finding] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if ALLOW_MARKER in line:
            continue
        for kind, pattern in PATTERNS:
            findings += [Finding(source, number, kind, _redact(m.group(0))) for m in pattern.finditer(line)]
        for pattern in denylist:
            findings += [
                Finding(source, number, "denylisted term", _redact(m.group(0)))
                for m in pattern.finditer(line)
            ]
    return findings


def _git(args: list[str], cwd: Path | None) -> bytes:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True).stdout


def _texts(names: list[bytes], read, cwd: Path | None) -> list[tuple[str, str]]:
    out = []
    for raw in names:
        if not raw:
            continue
        name = raw.decode()
        blob = read(name)
        if b"\0" in blob:  # binary file
            continue
        out.append((name, blob.decode("utf-8", errors="replace")))
    return out


def staged_texts(cwd: Path | None = None) -> list[tuple[str, str]]:
    names = _git(["diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"], cwd).split(b"\0")
    return _texts(names, lambda name: _git(["show", f":{name}"], cwd), cwd)


def tracked_texts(cwd: Path | None = None) -> list[tuple[str, str]]:
    names = _git(["ls-files", "-z"], cwd).split(b"\0")
    root = Path(cwd or ".")
    return _texts(names, lambda name: (root / name).read_bytes(), cwd)


def register(subparsers) -> None:
    p = subparsers.add_parser("leakcheck", help="scan for private addresses, identifiers and denylisted terms")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--staged", action="store_true", help="scan staged files (pre-commit hook)")
    mode.add_argument("--message", type=Path, help="scan a commit message file (commit-msg hook)")
    mode.add_argument("--tracked", action="store_true", help="scan every tracked file (CI)")
    p.add_argument(
        "--denylist",
        type=Path,
        default=Path(os.environ.get("LOCAL_AI_DENYLIST", DEFAULT_DENYLIST)),
        help="private file of regexes, one per line (default: %(default)s)",
    )
    p.add_argument("--ci", action="store_true", help="skip the private denylist — only in CI")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    if args.ci and os.environ.get("CI") != "true":
        print("leakcheck: --ci is allowed only in CI (CI=true)", file=sys.stderr)
        return 2
    try:
        denylist = [] if args.ci else load_denylist(args.denylist)
    except DenylistMissing as err:
        print(f"leakcheck: {err}", file=sys.stderr)
        return 2
    if args.staged:
        texts = staged_texts()
    elif args.message:
        texts = [("commit message", args.message.read_text())]
    else:
        texts = tracked_texts()
    findings = [f for source, text in texts for f in scan_text(source, text, denylist)]
    for f in findings:
        print(f"leakcheck: {f.source}:{f.line}: {f.kind} ({f.excerpt})", file=sys.stderr)
    if findings:
        print(
            f"leakcheck: {len(findings)} finding(s) — refused. Fix the text, or mark a deliberate, "
            f"safe line with '{ALLOW_MARKER}'.",
            file=sys.stderr,
        )
        return 1
    return 0

---
title: "Phase 0 — implementation plan"
description: "Guardrails, prep and the docs scaffold: leak hooks, the spark package skeleton, pinned versions, the docs site with every scenario, the host bootstrap, and Dan's runbooks."
date: 2026-09-23
---

# Phase 0 — Guardrails, prep, docs scaffold — Implementation Plan

> **Retrospective:** [Phase 0 — retrospective](phase-0-retro.md) records what this plan built, where
> the build departed from it and why, what the reviews found, and how to start over.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking. Every task is labelled **[Mac]**, **[Spark]** or
> **[Dan]** — do only the tasks for the machine you are on; the ⇄ markers are the machine switch
> points (one session at a time).

**Goal:** Make the repo safe and buildable before anything serves a model — leak guards on every
commit, the `spark` package skeleton, a Makefile front door, pinned versions, the Quarto docs site
with every scenario page, the host bootstrap script and Dan's runbooks — then prepare `brightroar`.

**Architecture:** Mac first: code, tests, hooks, docs and the bootstrap script are written and
tested on `heartsbane`. The Spark session dry-runs the bootstrap and inventories the box; Dan runs
the privileged steps from the runbooks; the Spark session verifies the result. All Python lives in
one uv project (`spark/`) whose only runtime dependency is PyYAML; shell is limited to the two hook
wrappers and the bootstrap script.

**Tech Stack:** Python ≥3.12 via uv 0.12 · pytest · PyYAML · GNU make (3.81 on macOS, 4.x on
Ubuntu) · gitleaks 8.x · shellcheck · Quarto 1.10 · GitHub Actions · bash · Ubuntu 24.04 tooling
(useradd, ufw, earlyoom, polkit, systemd).

**Spec:** [`website/design/plan.md`](plan.md) — Phase 0, plus *Users, access and security*,
*Docs site and scenarios*, *Deploy workflow* and *Where work runs*.

## Global Constraints

- **Public repo.** No private IPv4 addresses (a bare last octet such as `.201` is fine), no tailnet
  addresses or names, no MACs, serials or DDNS names, no credentials, no unread terminal output —
  in files *or* commit messages. Test strings that must look like private data are built at run
  time (`".".join([...])`), never written literally.
- **Private context stays private.** Speech is described generically ("recordings such as
  lectures"). The owner's private reasons are in private memory, never in the repo.
- **Secrets by reference.** Never print, echo or `cat` a secret; test presence only
  (`[ -n "$VAR" ]`); secret files are written by Dan with commands that never display the value.
- **Python only through uv:** `uv run --frozen --project spark …`. No pip, no system Python.
- **Makefile** runs on GNU make 3.81: no `.ONESHELL`, no `$(file …)`, no grouped targets.
- **Commits:** Conventional Commits with 🤖 right after the prefix (e.g.
  `feat(spark): 🤖 add leak check`) and the trailer
  `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`. One checkpoint commit per
  task on branch `phase-0`. **Never push without Dan's explicit OK.** Never `--no-verify`.
- **Docs must be true** (`CLAUDE.md` sync table): a change to the machine updates `changelog.md` and
  `README.md` §Current state in the same commit.

## Review Focus

1. **Leak hook with a missing dependency** (no gitleaks, no denylist file) — expected: the commit is
   refused with a message saying what's missing. Never a silent pass. *(Tests in Tasks 2 and 3.)*
2. **Harmless text that looks private** — version strings, times, the bare word `ts.net` in prose,
   RFC 5737 documentation addresses — expected: not flagged. *(Test in Task 2.)*
3. **Bootstrap re-run on a bootstrapped box** — expected: no errors, no duplicate users, groups or
   config lines. *(Guards in Task 7, re-run in Task 11.)*
4. **Firewall turned on before SSH is allowed** — expected: impossible; SSH is always allowed first.
   *(Test in Task 7.)*
5. **A scenario page with broken front matter** — expected: `make docs` and CI fail and name the
   file. *(Test in Task 5.)*

***

## File structure

| Path | Responsibility |
|---|---|
| `Makefile` | Front door: `help`, `test`, `lint`, `hooks`, `docs`, `bootstrap`, `bootstrap-dry-run` |
| `.gitignore` | Adds build outputs and the local model overlay |
| `.githooks/pre-commit`, `.githooks/commit-msg` | Thin wrappers: gitleaks, then `spark leakcheck` |
| `.githooks/gitleaks.toml` | gitleaks config: the default rules |
| `.github/workflows/ci.yml` | Tests, leak scan, shellcheck, site build |
| `.github/workflows/publish-website.yml` | Manual-only publish of the site to GitHub Pages |
| `spark/pyproject.toml`, `spark/uv.lock` | The uv project |
| `spark/src/spark/__init__.py` | Version |
| `spark/src/spark/cli.py` | Argument parsing; each module registers its own subcommand |
| `spark/src/spark/leakcheck.py` | Pattern + denylist scan of staged files, messages, tracked files |
| `spark/src/spark/versions.py` | Load `stack/versions.yaml`; render the Stack page |
| `spark/src/spark/docs.py` | `spark docs stack`, `spark docs check-scenarios` |
| `spark/tests/…` | One test module per source module, plus hook and bootstrap tests |
| `stack/versions.yaml` | Every pinned component |
| `stack/host/bootstrap.sh` | Host setup (Dan runs it with sudo) |
| `stack/host/earlyoom.default` | earlyoom arguments |
| `stack/host/50-local-ai.rules` | polkit: `spark-admin` may manage `local-ai-*` units |
| `website/_quarto.yml`, `website/index.qmd` | The docs site |
| `website/scenarios/index.qmd`, `website/scenarios/sNN-*.md` | The 22 scenario pages |
| `website/reference/stack.md` | Generated from `stack/versions.yaml` — never edited by hand |
| `website/how-to/*.md` | Runbooks for Dan's steps |

***

### Task 1 [Mac]: `spark` package skeleton, Makefile front door, .gitignore

**Files:**
- Create: `spark/pyproject.toml`, `spark/src/spark/__init__.py`, `spark/src/spark/cli.py`,
  `spark/tests/test_cli.py`, `Makefile`
- Modify: `.gitignore` (append)

**Interfaces:**
- Produces: `spark.cli.main(argv: list[str] | None = None) -> int`;
  `spark.cli.build_parser() -> argparse.ArgumentParser`. Later modules add a subcommand by
  exposing `register(subparsers) -> None` and calling
  `parser.set_defaults(func=run)` where `run(args) -> int`; `build_parser()` imports and calls
  each module's `register`.

- [ ] **Step 1: Branch**

```bash
git -C ~/git/hub/local-ai switch -c phase-0
```

- [ ] **Step 2: Write the project file**

`spark/pyproject.toml`:

```toml
[project]
name = "spark"
version = "0.1.0"
description = "Control tool for the brightroar model-serving stack"
requires-python = ">=3.12"
dependencies = ["pyyaml>=6.0.2"]

[project.scripts]
spark = "spark.cli:main"

[dependency-groups]
dev = ["pytest>=8.4"]

[build-system]
requires = ["hatchling>=1.27"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/spark"]

[tool.uv]
required-version = ">=0.12.18"

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
```

`spark/src/spark/__init__.py`:

```python
"""Control tool for the brightroar model-serving stack."""

__version__ = "0.1.0"
```

- [ ] **Step 3: Write the failing test**

`spark/tests/test_cli.py`:

```python
import pytest

from spark import cli


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == "spark 0.1.0"


def test_no_command_prints_help(capsys):
    assert cli.main([]) == 0
    assert "usage: spark" in capsys.readouterr().out
```

- [ ] **Step 4: Run it and watch it fail**

Run: `cd ~/git/hub/local-ai && uv lock --project spark && uv run --frozen --project spark pytest spark/tests/test_cli.py`
Expected: FAIL — `ImportError: cannot import name 'cli'`.

- [ ] **Step 5: Implement the CLI**

`spark/src/spark/cli.py`:

```python
"""spark — control tool for the brightroar model-serving stack."""

from __future__ import annotations

import argparse
import sys

from spark import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="spark", description=__doc__)
    parser.add_argument("--version", action="version", version=f"spark {__version__}")
    parser.add_subparsers(dest="command", metavar="<command>")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 6: Run the tests — they pass**

Run: `uv run --frozen --project spark pytest spark/tests`
Expected: `2 passed`.

- [ ] **Step 7: Makefile and .gitignore**

`Makefile`:

```make
# local-ai — the front door. `make help` lists the targets.
# Kept to GNU make 3.81 features so it runs on macOS as well as Ubuntu.

UV      := uv run --frozen --quiet --project spark
SPARK   := $(UV) spark
.DEFAULT_GOAL := help
.PHONY: help test

help: ## List the targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  %-20s %s\n", $$1, $$2}'

test: ## Run the unit and render tests
	uv run --frozen --project spark pytest spark/tests
```

Append to `.gitignore`:

```gitignore

# local-ai build outputs and local-only files
spark/.venv/
website/_site/
website/.quarto/
stack/models.local.yaml
```

- [ ] **Step 8: Check the front door**

Run: `make help && make test`
Expected: `help` and `test` listed with their descriptions; `2 passed`.

- [ ] **Step 9: Commit**

```bash
git add spark/pyproject.toml spark/uv.lock spark/src spark/tests Makefile .gitignore
git commit -m "feat(spark): 🤖 add the spark package skeleton and Makefile front door" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 2 [Mac]: leak check — patterns and a private denylist

**Files:**
- Create: `spark/src/spark/leakcheck.py`, `spark/tests/test_leakcheck.py`
- Modify: `spark/src/spark/cli.py` (register the subcommand)

**Interfaces:**
- Consumes: `spark.cli.build_parser` registration pattern from Task 1.
- Produces:
  - `Finding(source: str, line: int, kind: str, excerpt: str)` (frozen dataclass)
  - `DenylistMissing(RuntimeError)`
  - `load_denylist(path: Path) -> list[re.Pattern[str]]`
  - `scan_text(source: str, text: str, denylist: list[re.Pattern[str]]) -> list[Finding]`
  - `staged_texts(cwd: Path | None = None) -> list[tuple[str, str]]`
  - `tracked_texts(cwd: Path | None = None) -> list[tuple[str, str]]`
  - CLI: `spark leakcheck (--staged | --message FILE | --tracked) [--denylist PATH] [--ci]`,
    exit 0 clean, 1 findings, 2 setup error (missing denylist, `--ci` outside CI).

- [ ] **Step 1: Write the failing tests**

`spark/tests/test_leakcheck.py`:

```python
import re
import subprocess
from pathlib import Path

import pytest

from spark import leakcheck
from spark.leakcheck import DenylistMissing, load_denylist, scan_text


def ip(*parts: int) -> str:
    """Build addresses at run time so this file never contains a literal private address."""
    return ".".join(str(p) for p in parts)


def mac(*parts: str) -> str:
    return ":".join(parts)


def kinds(text: str, denylist=()) -> list[str]:
    return [f.kind for f in scan_text("t.md", text, list(denylist))]


@pytest.mark.parametrize("address", [ip(10, 1, 2, 3), ip(172, 20, 0, 5), ip(192, 168, 77, 5)])
def test_private_ipv4_is_flagged(address):
    assert kinds(f"server at {address} today") == ["private IPv4 address"]


def test_tailnet_address_is_flagged():
    assert kinds(f"node {ip(100, 101, 102, 103)}") == ["tailnet IPv4 address"]


@pytest.mark.parametrize(
    "harmless",
    [
        "reserved as .201 on the wired NIC",
        f"docs example {ip(192, 0, 2, 7)} and {ip(203, 0, 113, 9)}",
        f"public {ip(100, 12, 1, 1)}",
        "uv 0.12.18 and CUDA 13.0.2",
        "at 12:34:56 it froze",
        "patterns for MACs and `ts.net` names",
    ],
)
def test_harmless_text_is_not_flagged(harmless):
    assert kinds(harmless) == []


def test_mac_address_is_flagged():
    assert kinds(f"nic {mac('a4', 'bb', '6d', '01', '02', 'ef')}") == ["MAC address"]


def test_tailnet_hostname_is_flagged():
    name = "brightroar." + "tail" + "1a2b" + ".ts.net"
    assert kinds(f"https://{name}/") == ["tailnet hostname"]


def test_denylisted_term_is_flagged_case_insensitively():
    deny = [re.compile("secret-project", re.IGNORECASE)]
    assert kinds("about the Secret-Project plan", deny) == ["denylisted term"]


def test_allow_marker_skips_a_line():
    text = f"example {ip(192, 168, 0, 1)}  <!-- leakcheck: allow -->"
    assert kinds(text) == []


def test_excerpt_is_redacted():
    finding = scan_text("t.md", ip(10, 9, 8, 7), [])[0]
    assert finding.excerpt == "10.…"


def test_missing_denylist_fails_closed(tmp_path):
    with pytest.raises(DenylistMissing):
        load_denylist(tmp_path / "nope")


def test_denylist_skips_blank_lines_and_comments(tmp_path):
    path = tmp_path / "denylist"
    path.write_text("# a comment\n\nsecret-project\n")
    assert [p.pattern for p in load_denylist(path)] == ["secret-project"]


def test_staged_file_with_private_address_is_found(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "notes.md").write_text(f"box at {ip(192, 168, 50, 7)}\n")
    subprocess.run(["git", "add", "notes.md"], cwd=tmp_path, check=True)
    texts = leakcheck.staged_texts(cwd=tmp_path)
    assert texts == [("notes.md", f"box at {ip(192, 168, 50, 7)}\n")]
    assert kinds(texts[0][1]) == ["private IPv4 address"]


def test_cli_refuses_ci_mode_outside_ci(monkeypatch, capsys):
    monkeypatch.delenv("CI", raising=False)
    from spark import cli

    assert cli.main(["leakcheck", "--tracked", "--ci"]) == 2
    assert "only in CI" in capsys.readouterr().err


def test_cli_missing_denylist_exits_2(tmp_path, capsys):
    from spark import cli

    message = tmp_path / "msg"
    message.write_text("hello\n")
    assert cli.main(["leakcheck", "--message", str(message), "--denylist", str(tmp_path / "none")]) == 2
    assert "denylist not found" in capsys.readouterr().err
```

*Superseded 2026-09-25 — the code now differs; see commits 8e0a5e7 (sentence-final and IPv6
addresses, bare tailnet names, the denylist on allowed lines, a bad denylist line) and cbd7ca3 (type
changes, file names, UTF-16 and UTF-32 text, binaries named, an empty denylist refused).*

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run --frozen --project spark pytest spark/tests/test_leakcheck.py`
Expected: FAIL — `ImportError: cannot import name 'leakcheck'`.

- [ ] **Step 3: Implement**

`spark/src/spark/leakcheck.py`:

```python
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
```

*Superseded 2026-09-25 — the code now differs; see commits 8e0a5e7 (sentence-final and IPv6
addresses, bare tailnet names, the denylist on allowed lines, a bad denylist line exits 2), cbd7ca3
(type changes, file names, UTF-16 and UTF-32 text, binaries named, an empty denylist refused) and
bad1b55 (the docstring and the `--message` help name CI's history scan).*

In `spark/src/spark/cli.py`, replace the `parser.add_subparsers(...)` line with:

```python
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")
    from spark import leakcheck

    leakcheck.register(subparsers)
```

- [ ] **Step 4: Run the tests — they pass**

Run: `uv run --frozen --project spark pytest spark/tests`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add spark/src/spark/leakcheck.py spark/src/spark/cli.py spark/tests/test_leakcheck.py
git commit -m "feat(spark): 🤖 add the leak check for private addresses and denylisted terms" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 3 [Mac]: hook wrappers, gitleaks config, `make hooks`

**Files:**
- Create: `.githooks/pre-commit`, `.githooks/commit-msg`, `.githooks/gitleaks.toml`,
  `spark/tests/test_hooks.py`
- Modify: `Makefile` (add `hooks`, `lint`)

**Interfaces:**
- Consumes: `spark leakcheck --staged | --message FILE` from Task 2.
- Produces: hooks enabled per clone by `make hooks` (`git config core.hooksPath .githooks`).

- [ ] **Step 1: Install the tools (Dan approves the command)**

Run: `brew install gitleaks shellcheck && gitleaks version && shellcheck --version | head -2`
Expected: both print a version. Record the gitleaks version in Task 4's `versions.yaml`.

- [ ] **Step 2: Write the failing tests**

`spark/tests/test_hooks.py`:

```python
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOOKS = ROOT / ".githooks"


def test_hooks_are_executable():
    for name in ("pre-commit", "commit-msg"):
        assert os.access(HOOKS / name, os.X_OK), name


def test_pre_commit_refuses_without_gitleaks(tmp_path):
    # A PATH with git and bash but no gitleaks: the hook must refuse, not pass.
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for tool in ("git", "bash", "dirname", "cat"):
        found = shutil.which(tool)
        assert found, tool
        (bindir / tool).symlink_to(found)
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    result = subprocess.run(
        ["bash", str(HOOKS / "pre-commit")],
        cwd=repo,
        env={"PATH": str(bindir), "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "gitleaks is not installed" in result.stderr
```

*Superseded 2026-09-25 — the code now differs; see commits 783a619 (a clear refusal when uv is
missing), cbd7ca3 (`make hooks` refuses an empty denylist) and f581016 (both hooks, and
`make hooks`, need gitleaks' `git` command).*

- [ ] **Step 3: Run them and watch them fail**

Run: `uv run --frozen --project spark pytest spark/tests/test_hooks.py`
Expected: FAIL — hook files missing.

- [ ] **Step 4: Write the hooks and the gitleaks config**

`.githooks/pre-commit`:

```bash
#!/usr/bin/env bash
# Leak guard for this public repo — runs on every commit. Never bypass it with --no-verify.
set -euo pipefail
root="$(git rev-parse --show-toplevel)"
if ! command -v gitleaks >/dev/null 2>&1; then
  echo "leak guard: gitleaks is not installed, so this commit is refused (website/how-to/leak-guards.md)." >&2
  exit 1
fi
if ! gitleaks git --help >/dev/null 2>&1; then
  echo "leak guard: this gitleaks has no 'git' command — it needs 8.19 or later (Ubuntu's archive ships 8.16); see website/how-to/leak-guards.md." >&2
  exit 1
fi
gitleaks git --pre-commit --staged --redact --no-banner --config "$root/.githooks/gitleaks.toml" "$root"
exec uv run --frozen --quiet --project "$root/spark" spark leakcheck --staged
```

*Superseded 2026-09-25 — the code now differs; see commit 783a619 (a clear refusal when uv is
missing from the hook's `PATH`).*

`.githooks/commit-msg`:

```bash
#!/usr/bin/env bash
# Leak guard for commit messages. Never bypass it with --no-verify.
set -euo pipefail
root="$(git rev-parse --show-toplevel)"
if ! command -v gitleaks >/dev/null 2>&1; then
  echo "leak guard: gitleaks is not installed, so this commit is refused (website/how-to/leak-guards.md)." >&2
  exit 1
fi
if ! gitleaks git --help >/dev/null 2>&1; then
  echo "leak guard: this gitleaks has no 'git' command — it needs 8.19 or later (Ubuntu's archive ships 8.16); see website/how-to/leak-guards.md." >&2
  exit 1
fi
gitleaks stdin --redact --no-banner --config "$root/.githooks/gitleaks.toml" < "$1"
exec uv run --frozen --quiet --project "$root/spark" spark leakcheck --message "$1"
```

*Superseded 2026-09-25 — the code now differs; see commit 783a619 (a clear refusal when uv is
missing from the hook's `PATH`).*

`.githooks/gitleaks.toml`:

```toml
# gitleaks config for local-ai: the default rules, nothing removed.
[extend]
useDefault = true
```

Then: `chmod +x .githooks/pre-commit .githooks/commit-msg`

- [ ] **Step 5: Makefile targets**

Add to `Makefile` (and add `hooks lint` to `.PHONY`):

```make
hooks: ## Turn on the leak-check hooks in this clone (needs gitleaks and your denylist)
	@command -v gitleaks >/dev/null || { echo "install gitleaks first (website/how-to/leak-guards.md)"; exit 1; }
	@test -f "$${LOCAL_AI_DENYLIST:-$$HOME/.config/local-ai/denylist}" || { echo "create your denylist first (website/how-to/leak-guards.md)"; exit 1; }
	git config core.hooksPath .githooks
	@echo "leak-check hooks on for this clone"

lint: ## Shellcheck the hooks and host scripts
	shellcheck .githooks/pre-commit .githooks/commit-msg
```

*Superseded 2026-09-25 — the code now differs; see commits cbd7ca3 (`make hooks` runs the hooks'
own denylist check) and f581016 (it checks gitleaks' `git` command).*

- [ ] **Step 6: Run the tests — they pass; lint is clean**

Run: `uv run --frozen --project spark pytest spark/tests && make lint`
Expected: all pass; shellcheck prints nothing.

- [ ] **Step 7: Turn the hooks on and prove they bite**

Dan creates the denylist first (`website/how-to/leak-guards.md`, written in Task 8 — until then,
follow its steps from this task: `mkdir -p ~/.config/local-ai && touch ~/.config/local-ai/denylist`
and add his own private terms with an editor; Claude never reads or writes this file).

Run:

```bash
make hooks
printf 'box at %s\n' "$(printf '%s.%s.%s.%s' 192 168 50 7)" > leak-drill.md
git add leak-drill.md
git commit -m "test: leak drill" ; echo "exit=$?"
git restore --staged leak-drill.md && rm leak-drill.md
```

Expected: the commit is refused (`private IPv4 address`), `exit=1`, nothing committed.

- [ ] **Step 8: Commit**

```bash
git add .githooks spark/tests/test_hooks.py Makefile
git commit -m "build(repo): 🤖 add pre-commit and commit-msg leak guards" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

Expected: this commit itself passes through the new hooks.

***

### Task 4 [Mac]: `stack/versions.yaml` and the generated Stack page

**Files:**
- Create: `stack/versions.yaml`, `spark/src/spark/versions.py`, `spark/src/spark/docs.py`,
  `spark/tests/test_versions.py`, `website/reference/stack.md` (generated)
- Modify: `spark/src/spark/cli.py` (register `docs`), `Makefile`

**Interfaces:**
- Produces:
  - `Component(name, version, where: tuple[str, ...], pin: str | None, deployed: bool, docs: str,
    context7: str | None, changelog: str, advisories: str | None)` (frozen dataclass)
  - `VersionsError(ValueError)`
  - `load_versions(path: Path) -> dict[str, Component]`
  - `unpinned(components: dict[str, Component]) -> list[str]` — deployed components without a pin
  - `render_stack_page(components: dict[str, Component]) -> str`
  - CLI: `spark docs stack (--write | --check)`; `spark docs check-scenarios` (Task 5)

- [ ] **Step 1: Write the failing tests**

`spark/tests/test_versions.py`:

```python
import textwrap

import pytest

from spark.versions import VersionsError, load_versions, render_stack_page, unpinned

GOOD = textwrap.dedent(
    """
    components:
      llama-swap:
        version: v257
        where: [spark]
        pin: null
        deployed: true
        docs: https://github.com/mostlygeek/llama-swap
        context7: null
        changelog: https://github.com/mostlygeek/llama-swap/releases
        advisories: null
      quarto:
        version: 1.10.3
        where: [mac, ci]
        pin: null
        deployed: false
        docs: https://quarto.org/docs/
        context7: /quarto-dev/quarto-web
        changelog: https://github.com/quarto-dev/quarto-cli/releases
        advisories: null
    """
)


def write(tmp_path, text):
    path = tmp_path / "versions.yaml"
    path.write_text(text)
    return path


def test_loads_components(tmp_path):
    components = load_versions(write(tmp_path, GOOD))
    assert components["llama-swap"].version == "v257"
    assert components["quarto"].where == ("mac", "ci")


def test_unpinned_lists_only_deployed_components(tmp_path):
    assert unpinned(load_versions(write(tmp_path, GOOD))) == ["llama-swap"]


def test_rejects_a_malformed_pin(tmp_path):
    with pytest.raises(VersionsError, match="pin"):
        load_versions(write(tmp_path, GOOD.replace("pin: null\n    deployed: true", "pin: abc\n    deployed: true", 1)))


def test_rejects_an_unknown_machine(tmp_path):
    with pytest.raises(VersionsError, match="where"):
        load_versions(write(tmp_path, GOOD.replace("[spark]", "[toaster]")))


def test_stack_page_is_a_table_marked_generated(tmp_path):
    page = render_stack_page(load_versions(write(tmp_path, GOOD)))
    assert "generated from `stack/versions.yaml`" in page
    assert "| llama-swap | v257 | spark | not yet |" in page
```

*Superseded 2026-09-25 — the code now differs; see commit fe3d8b7 (a test that uv runs the Python
minor version `spark/.python-version` pins).*

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run --frozen --project spark pytest spark/tests/test_versions.py`
Expected: FAIL — `ModuleNotFoundError: spark.versions`.

- [ ] **Step 3: Implement**

`spark/src/spark/versions.py`:

```python
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
```

`spark/src/spark/docs.py`:

```python
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
```

In `cli.py`, after `leakcheck.register(subparsers)`:

```python
    from spark import docs

    docs.register(subparsers)
```

- [ ] **Step 4: Write `stack/versions.yaml`**

Pins stay `null` until the task that downloads the component records its digest or checksum.

```yaml
# Every pinned component. `spark apply` refuses to deploy a component with deployed: true and no pin.
# pin: sha256:<64 hex> — a release file's checksum, or a container image's digest.
components:
  uv:
    version: 0.12.18
    where: [mac, spark]
    pin: null
    deployed: false
    docs: https://docs.astral.sh/uv/
    context7: /astral-sh/uv
    changelog: https://github.com/astral-sh/uv/releases
    advisories: https://github.com/astral-sh/uv/security/advisories
  quarto:
    version: 1.10.3
    where: [mac, ci]
    pin: null
    deployed: false
    docs: https://quarto.org/docs/
    context7: /quarto-dev/quarto-web
    changelog: https://github.com/quarto-dev/quarto-cli/releases
    advisories: null
  gitleaks:
    version: 8.30.1
    where: [mac, spark, ci]
    pin: null
    deployed: false
    docs: https://github.com/gitleaks/gitleaks
    context7: null
    changelog: https://github.com/gitleaks/gitleaks/releases
    advisories: https://github.com/gitleaks/gitleaks/security/advisories
  llama-swap:
    version: v257
    where: [spark]
    pin: null
    deployed: true
    docs: https://github.com/mostlygeek/llama-swap
    context7: null
    changelog: https://github.com/mostlygeek/llama-swap/releases
    advisories: null
  llama.cpp:
    version: v0.5.0
    where: [spark]
    pin: null
    deployed: true
    docs: https://github.com/ggml-org/llama.cpp/tree/master/tools/server
    context7: /ggml-org/llama.cpp
    changelog: https://github.com/ggml-org/llama.cpp/releases
    advisories: https://github.com/ggml-org/llama.cpp/security/advisories
  whisper.cpp:
    version: v1.9.4
    where: [spark]
    pin: null
    deployed: true
    docs: https://github.com/ggml-org/whisper.cpp/tree/master/examples/server
    context7: /ggml-org/whisper.cpp
    changelog: https://github.com/ggml-org/whisper.cpp/releases
    advisories: null
  open-webui:
    version: v0.11.4
    where: [spark]
    pin: null
    deployed: true
    docs: https://docs.openwebui.com/
    context7: /open-webui/docs
    changelog: https://github.com/open-webui/open-webui/releases
    advisories: https://github.com/open-webui/open-webui/security/advisories
```

*Superseded 2026-09-25 — the code now differs; see commit 7d819bb (Docker and earlyoom, with the
versions the Spark recorded).*

If `gitleaks version` (Task 3) reports something other than 8.30.1, use what it reports.

- [ ] **Step 5: Makefile, generate, run the tests**

Add to `Makefile` (and `docs` to `.PHONY`):

```make
docs: ## Regenerate the Stack page, check scenario pages, render the site
	$(SPARK) docs stack --write
	quarto render website
```

Run: `uv run --frozen --project spark spark docs stack --write && uv run --frozen --project spark pytest spark/tests`
Expected: `website/reference/stack.md` exists; all tests pass.

- [ ] **Step 6: Commit**

```bash
git add stack/versions.yaml spark/src/spark/versions.py spark/src/spark/docs.py spark/src/spark/cli.py spark/tests/test_versions.py website/reference/stack.md Makefile
git commit -m "feat(stack): 🤖 add pinned versions and the generated Stack page" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 5 [Mac]: the docs site and all 22 scenario pages

**Files:**
- Create: `website/_quarto.yml`, `website/index.qmd`, `website/scenarios/index.qmd`,
  `website/scenarios/s01-morning-start.md` … `website/scenarios/s22-another-tailnet.md`,
  `spark/tests/test_scenarios.py`
- Modify: `spark/src/spark/docs.py` (add `check-scenarios`), `Makefile` (`docs` runs the check)

**Interfaces:**
- Consumes: `spark docs` subparser from Task 4.
- Produces: `check_scenarios(directory: Path) -> list[str]` (problems, empty when clean);
  CLI `spark docs check-scenarios` (exit 0 clean, 1 with problems listed).

- [ ] **Step 1: Write the failing tests**

`spark/tests/test_scenarios.py`:

```python
from spark.docs import check_scenarios

GOOD = """---
title: "S01 · Morning start"
scenario-id: S01
phase: 2
status: planned
---

**Situation.** x
"""


def test_clean_page_passes(tmp_path):
    (tmp_path / "s01-morning-start.md").write_text(GOOD)
    assert check_scenarios(tmp_path) == []


def test_id_must_match_the_filename(tmp_path):
    (tmp_path / "s02-big-job.md").write_text(GOOD)
    assert any("s02-big-job.md" in p and "S02" in p for p in check_scenarios(tmp_path))


def test_status_must_be_known(tmp_path):
    (tmp_path / "s01-morning-start.md").write_text(GOOD.replace("planned", "done-ish"))
    assert any("status" in p for p in check_scenarios(tmp_path))


def test_verified_needs_a_date(tmp_path):
    (tmp_path / "s01-morning-start.md").write_text(GOOD.replace("planned", "verified"))
    assert any("verified" in p for p in check_scenarios(tmp_path))


def test_broken_front_matter_is_reported_by_name(tmp_path):
    (tmp_path / "s03-doesnt-fit.md").write_text("no front matter here\n")
    assert any("s03-doesnt-fit.md" in p for p in check_scenarios(tmp_path))
```

*Superseded 2026-09-25 — the code now differs; see commit 5036aca (tests for front matter that isn't
valid YAML, or isn't a mapping).*

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run --frozen --project spark pytest spark/tests/test_scenarios.py`
Expected: FAIL — `ImportError: cannot import name 'check_scenarios'`.

- [ ] **Step 3: Implement the check**

Add these imports to the top of `spark/src/spark/docs.py` (with the others):

```python
import datetime as _dt
import re as _re

import yaml as _yaml
```

and these definitions below `STACK_PAGE`:

```python
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
```

*Superseded 2026-09-25 — the code now differs; see commit 5036aca (a YAML error names its page, and
front matter that isn't a mapping reads as missing).*

and in `register`, after the `stack` parser:

```python
    check = sub.add_parser("check-scenarios", help="validate website/scenarios/*.md front matter")
    check.set_defaults(func=run_check_scenarios)
```

with:

```python
def run_check_scenarios(args: argparse.Namespace) -> int:
    problems = check_scenarios(SCENARIOS)
    for problem in problems:
        print(f"docs: {problem}", file=sys.stderr)
    return 1 if problems else 0
```

Makefile `docs` target becomes:

```make
docs: ## Regenerate the Stack page, check scenario pages, render the site
	$(SPARK) docs stack --write
	$(SPARK) docs check-scenarios
	quarto render website
```

- [ ] **Step 4: Run the tests — they pass**

Run: `uv run --frozen --project spark pytest spark/tests`
Expected: all pass.

- [ ] **Step 5: Site configuration and landing pages**

`website/_quarto.yml`:

```yaml
project:
  type: website
  output-dir: _site

website:
  title: "local-ai"
  description: "One DGX Spark serving open models — the plan, the scenarios, the runbooks."
  site-url: https://chendaniely.github.io/local-ai/
  repo-url: https://github.com/chendaniely/local-ai
  repo-subdir: website
  navbar:
    left:
      - text: Plan
        href: design/plan.md
      - text: Scenarios
        href: scenarios/index.qmd
      - text: How-to
        href: how-to/index.qmd
      - text: Stack
        href: reference/stack.md

format:
  html:
    theme: cosmo
    toc: true
```

`website/index.qmd`:

```markdown
---
title: "local-ai"
---

A single NVIDIA DGX Spark — `brightroar`, a GIGABYTE AI TOP ATOM — serving open models to my own
pipelines, my coding harnesses and my homelab, with a web UI on top. Claude Code and Claude Desktop
stay exactly as they are; this is a separate mode.

- **[The plan](design/plan.md)** — goals, constraints, decisions, design and phases. Start here.
- **[Scenarios](scenarios/index.qmd)** — what happens in the situations that matter, and whether
  each is built and verified yet.
- **[How-to](how-to/index.qmd)** — runbooks for the steps a person has to do.
- **[Stack](reference/stack.md)** — every pinned component, with docs and advisories.
```

`website/scenarios/index.qmd`:

```markdown
---
title: "Scenarios"
listing:
  contents: "s*.md"
  type: table
  sort: "title"
  fields: [scenario-id, title, phase, status]
  field-display-names:
    scenario-id: "ID"
    phase: "Phase"
    status: "Status"
---

Each scenario says what the stack does in a situation that matters, what I see, and how to
override it. **planned** → designed only; **built** → implemented; **verified** → checked by
`spark doctor` (or a dated drill), with the date. A change in behaviour updates the page and its
check in the same commit.
```

- [ ] **Step 6: Write the 22 scenario pages**

Every page follows this template exactly (the check in Step 3 enforces the front matter):

```markdown
---
title: "S01 · Morning start"
scenario-id: S01
phase: 2
status: planned
---

**Situation.** …

**What happens.** …

**What I see.** …

**How to override.** …
```

Pages, in order — filename · title · phase · then the four sections:

1. `s01-morning-start.md` · "S01 · Morning start" · 2 — *Situation:* 9 am; the coder unloaded
   overnight; I start pi. *What happens:* the first request loads the coder if it fits (tens of
   seconds with llama.cpp); an optional weekday preload has it ready first; a "stay loaded while I
   work" pin stops it idle-unloading. *What I see:* "loading" on the menu bar, a low-priority
   "loaded" notification, a slower first reply. *Override:* `spark load <coder>`, the menu bar's
   Load, `spark pin <coder>`.
2. `s02-big-job.md` · "S02 · Big job while an agent works" · 2 — *Situation:* pi is mid-task with
   the coder loaded and I start a cuDF job that needs ~70 GiB. *What happens:* `spark make-room 70G`
   lists what would have to unload, with sizes, and unloads only what I confirm; pi's next request
   is refused with a reason, never quietly swapped. *What I see:* the list; headroom on the menu
   bar. *Override:* decline and nothing unloads; the brake remains the backstop.
3. `s03-doesnt-fit-interactive.md` · "S03 · Doesn't fit (interactive)" · 2 — *Situation:* I ask for
   a model that doesn't fit in free memory. *What happens:* nothing is evicted or substituted; the
   request is refused with memory needed against available, the top holders and my options.
   *What I see:* an error in the client — the explanation inline from Phase 3, on the menu bar and
   ntfy before that. *Override:* free memory (`spark make-room`, stop a job), pick a model that
   fits, or retry.
4. `s04-doesnt-fit-unattended.md` · "S04 · Doesn't fit (unattended)" · 3 — *Situation:* an app or
   agent asks at 2 am for a model that doesn't fit. *What happens:* it waits up to its key's
   `wait_for_fit_s`, never evicting anything, then gets a refusal with a reason and
   `retry_after_s`. *What I see:* a notification if it was refused (quiet hours respected).
   *Override:* the key's wait setting.
5. `s05-memory-critically-low.md` · "S05 · Memory critically low" · 1 — *Situation:* a job keeps
   growing and free memory falls toward the band where the box has been reported to freeze.
   *What happens:* a warning at 28 GiB available; at 20 GiB the brake unloads a loading engine
   first, then idle models of any class, then the least recently used, and holds them; earlyoom is
   the last resort. Phase 1 ships a minimal brake that unloads the on-demand coder first, then the
   always-loaded models. *What I see:* a high-priority "brake" notification and a hold in
   `spark status`. *Override:* `spark brake --release` once memory is back; thresholds live in
   `stack/models.yaml`.
6. `s06-agent-gpu-step.md` · "S06 · An agent's long GPU step" · 2 — *Situation:* pi's test step runs
   a 40-minute GPU job with no model calls. *What happens:* the harness hook registered the session,
   so its model counts as in use and doesn't idle-unload. *What I see:* the session on the menu bar.
   *Override:* end the session or `spark unpin`.
7. `s07-lecture-transcript.md` · "S07 · Lecture → transcript with speakers" · 3 — *Situation:* a
   90-minute lecture recording lands in a NAS folder and my own pipeline picks it up. *What
   happens:* batch speech-to-text (Whisper with a vocabulary prompt and word timestamps) plus
   speaker labels from the diarization endpoint; batch work yields to interactive use; no content is
   logged. *What I see:* the pipeline's output. *Override:* ask for the English-optimised or the
   multilingual model by name.
8. `s08-batch-vs-interactive.md` · "S08 · Batch versus interactive" · 3 — *Situation:* a backlog of
   recordings is processing and I start chatting or coding. *What happens:* batch keys run with low
   concurrency, so my requests keep their slots. *What I see:* chat stays responsive; the batch
   slows. *Override:* per-key concurrency in the registry.
9. `s09-phone-away.md` · "S09 · Phone away from home" · 1 — *Situation:* away from home, Tailscale
   on, I open Open WebUI on my Android phone. *What happens:* HTTPS through `tailscale serve`; it
   installs as an app; image questions go to the always-loaded vision model, voice input to
   speech-to-text. *What I see:* the real model name on every reply. *Override:* none needed.
10. `s10-homelab-3am.md` · "S10 · Homelab app at 3 am" · 3 — *Situation:* a homelab app asks for a
    model that isn't loaded and fits. *What happens:* it loads (the same rule as mine) and
    idle-unloads after 30 minutes; notifications are low priority and quiet at night. *What I see:*
    nothing, unless I look. *Override:* the app key's access group.
11. `s11-new-model.md` · "S11 · Trying a new model" · 2 — *Situation:* a promising open model is
    released. *What happens:* `spark try <hf-repo>` serves it from a separate lab instance through
    an uncommitted overlay, with its footprint estimated then measured, and opens a trial note in my
    vault; `spark promote` after the bake-off; `spark forget` removes it. *What I see:* the trial in
    `spark status`. *Override:* none needed.
12. `s12-long-agent-run.md` · "S12 · Long agent run, laptop closed" · 1 — *Situation:* I start a long
    agent run in tmux on the Spark as `agent` and close the laptop. *What happens:* the run
    continues; I reattach with `ssh agent@brightroar -t tmux a`; from Phase 2, hooks post
    done / needs input / failed. *What I see:* the tmux session, and later notifications and the
    session on the menu bar. *Override:* none needed.
13. `s13-freeze-away.md` · "S13 · The Spark freezes while I'm away" · 2 — *Situation:* the box stops
    responding. *What happens:* the watchdog on the Synology notices within minutes and sends a
    high-priority notification; I fix it at home (remote power via Home Assistant later). Verified by
    a dated drill. *What I see:* the notification; "unreachable" on the menu bar. *Override:* none.
14. `s14-gate-down.md` · "S14 · Gate down" · 2 — *Situation:* `spark-gate` crashes. *What happens:*
    models already loaded keep serving; only new loads are refused; a failure notifier alerts
    without needing the gate. *What I see:* a high-priority notification. *Override:* none needed.
15. `s15-nas-down.md` · "S15 · NAS down" · 4 — *Situation:* the Synology is unreachable. *What
    happens:* automounts time out instead of hanging; model loads are unaffected (cache drops touch
    local filesystems only); jobs that read the NAS fail visibly. *What I see:* failed jobs, not a
    hung box. *Override:* none needed.
16. `s16-postgres-down.md` · "S16 · Postgres down" · 3 — *Situation:* LiteLLM's database stops.
    *What happens:* the API fails closed and a high-priority alert fires; since a full disk is the
    likeliest cause, disk-low alerts come first. *What I see:* refusals and the alert.
    *Override:* none — fix the database or the disk.
17. `s17-changing-models.md` · "S17 · Changing models mid-task" · 2 — *Situation:* I edit
    `stack/models.yaml` while pi is mid-task. *What happens:* `spark apply` shows the diff and waits
    until models are idle, because a llama-swap reload stops every engine — or stops everything
    only after I confirm. *What I see:* the diff and the wait. *Override:* confirm to apply now.
18. `s18-rebuild.md` · "S18 · Rebuild after a factory reset" · 4 — *Situation:* the box has been
    reset. *What happens:* runbooks → delete the old Tailscale device first → bootstrap → restore
    from the Synology → `spark doctor`. Verified by a dated drill. *What I see:* the same stack,
    same names. *Override:* none.
19. `s19-claude-code-pipeline.md` · "S19 · Claude Code building a pipeline elsewhere" · 5 —
    *Situation:* Claude Code in another repo needs to call the Spark. *What happens:* the
    `spark-endpoints` skill points it at the endpoint reference; I run `spark keys create <app>`,
    which stores the key without ever printing it. *What I see:* a working client. *Override:* none.
20. `s20-web-search.md` · "S20 · Web search from the phone" · 1 — *Situation:* I ask Open WebUI about
    something recent. *What happens:* it searches through a self-hosted SearXNG and answers with
    sources; queries go to search engines, never to an AI company. *What I see:* cited answers.
    *Override:* turn web search off per chat.
21. `s21-pixeltable.md` · "S21 · Pixeltable over datasets" · backlog — *Situation:* I want AI
    columns across a dataset. *What happens:* Pixeltable computed columns call the endpoints through
    the OpenAI-compatible API. *What I see:* results in Pixeltable. *Override:* none.
22. `s22-another-tailnet.md` · "S22 · On another tailnet over WireGuard" · 3 — *Situation:* my device
    is logged into a different tailnet and I come home over WireGuard. *What happens:* SSH works
    over the LAN address, and from Phase 3 so does the API; the web UI is Tailscale-only, so it
    waits. *What I see:* pi and my scripts working; the web UI out of reach. *Override:* none
    (HTTPS on the LAN is in the backlog).

All 22 start with `status: planned`.

- [ ] **Step 7: Build the site**

Run: `make docs`
Expected: `docs: …` prints nothing (no scenario problems); Quarto finishes with
`Output created: _site/index.html`; the Scenarios page lists S01–S22.

- [ ] **Step 8: Commit**

```bash
git add website/_quarto.yml website/index.qmd website/scenarios spark/src/spark/docs.py spark/tests/test_scenarios.py Makefile
git commit -m "docs(website): 🤖 add the docs site and the 22 scenario pages" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 6 [Mac]: CI and the manual publish workflow

**Files:**
- Create: `.github/workflows/ci.yml`, `.github/workflows/publish-website.yml`

**Interfaces:**
- Consumes: `make test`, `spark leakcheck --tracked --ci`, `spark docs stack --check`,
  `spark docs check-scenarios`, `quarto render website`.

- [ ] **Step 1: Resolve the two values not pinned below**

Already researched (2026-09-23): `actions/checkout` v6.1.0 =
`d23441a48e516b6c34aea4fa41551a30e30af803` (quarto-actions v2.2.0 handles v6's credential setup;
v7 is untested with it), `quarto-dev/quarto-actions` v2.2.0 =
`8a96df13519ee81fd526f2dfca5962811136661b`, gitleaks 8.30.1. Resolve the remaining two:

```bash
gh api repos/astral-sh/setup-uv/commits/v7 --jq .sha        # setup-uv's SHA for <setup-uv-sha>
curl -fsSL https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_checksums.txt \
  | grep 'linux_x64.tar.gz'                                   # the checksum for <gitleaks-sha256>
```

If `setup-uv` has no `v7` tag, use its latest release (`gh release view -R astral-sh/setup-uv`).
We run the MIT-licensed gitleaks binary directly; the gitleaks GitHub Action carries its own EULA.

- [ ] **Step 2: Write `ci.yml`**

`.github/workflows/ci.yml` (replace `<setup-uv-sha>` and `<gitleaks-sha256>` with the values from
Step 1 before committing — Step 4 checks for leftovers):

```yaml
name: ci
on:
  push:
  pull_request:
permissions:
  contents: read

jobs:
  tests:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6.1.0
      - uses: astral-sh/setup-uv@<setup-uv-sha> # v7
        with:
          version: "0.12.18"
      - run: uv run --frozen --project spark pytest spark/tests
      - run: uv run --frozen --project spark spark docs stack --check
      - run: uv run --frozen --project spark spark docs check-scenarios

  leaks:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6.1.0
        with:
          fetch-depth: 0
      - uses: astral-sh/setup-uv@<setup-uv-sha> # v7
        with:
          version: "0.12.18"
      - name: gitleaks over the whole history
        run: |
          curl -fsSL -o gitleaks.tar.gz "https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_linux_x64.tar.gz"
          echo "<gitleaks-sha256>  gitleaks.tar.gz" | sha256sum -c -
          tar -xzf gitleaks.tar.gz gitleaks
          ./gitleaks git --redact --no-banner -v --config .githooks/gitleaks.toml .
      - name: repo patterns over every tracked file
        env:
          CI: "true"
        run: uv run --frozen --project spark spark leakcheck --tracked --ci

  shellcheck:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6.1.0
      - run: shellcheck .githooks/pre-commit .githooks/commit-msg stack/host/bootstrap.sh

  site:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6.1.0
      - uses: quarto-dev/quarto-actions/setup@8a96df13519ee81fd526f2dfca5962811136661b # v2.2.0
        with:
          version: "1.10.3"
      - run: quarto render website
```

*Superseded 2026-09-25 — the code now differs; see commits bad1b55 (no job token in the checkouts;
gitleaks downloaded, checked and run in three steps; the repo's patterns over every commit's patches
and messages) and ed0e06a (a time limit on every job; the download retries).*

- [ ] **Step 3: Write `publish-website.yml` (manual only)**

```yaml
name: publish-website
# Manual only. Publishing is outward-facing: Dan triggers it. One-time setup by Dan: create an empty
# gh-pages branch (git checkout --orphan gh-pages; git reset --hard; git commit --allow-empty -m
# "Initialising gh-pages branch"; git push origin gh-pages), then Settings → Pages → deploy from
# gh-pages.
on:
  workflow_dispatch:
permissions:
  contents: write

jobs:
  publish:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6.1.0
      - uses: quarto-dev/quarto-actions/setup@8a96df13519ee81fd526f2dfca5962811136661b # v2.2.0
        with:
          version: "1.10.3"
      - uses: quarto-dev/quarto-actions/publish@8a96df13519ee81fd526f2dfca5962811136661b # v2.2.0
        with:
          target: gh-pages
          path: website
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

- [ ] **Step 4: Check the files locally**

Run: `grep -n "<setup-uv-sha>\\|<gitleaks-sha256>" .github/workflows/*.yml ; echo "leftovers=$?"`
Expected: no output and `leftovers=1` (grep found nothing).

- [ ] **Step 5: Commit** (CI runs for the first time when Dan OKs the push at the switch point)

```bash
git add .github/workflows
git commit -m "ci(repo): 🤖 add tests, leak scan, shellcheck and site build; manual site publish" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 7 [Mac]: the host bootstrap script

**Files:**
- Create: `stack/host/bootstrap.sh`, `stack/host/earlyoom.default`, `stack/host/50-local-ai.rules`,
  `spark/tests/test_bootstrap.py`
- Modify: `Makefile` (`bootstrap`, `bootstrap-dry-run`; add the script to `lint`)

**Interfaces:**
- Produces on the Spark: groups `spark`, `spark-users`, `spark-admin`; system user `spark`
  (video, render, spark-users — **not** docker, which is root-equivalent); user `agent` (video,
  render, spark-users — never docker, sudo or spark-admin); Dan in `spark-admin`, `spark-users`, `adm`;
  directories `/opt/local-ai/{app,bin,etc,python}` (root:spark-admin 2775 — Dan deploys there; the
  `spark` user can only read, so a compromised engine can't rewrite the units or the Compose file that
  root runs), `/etc/local-ai`
  (root:spark-admin 0750), `/etc/local-ai/secrets` (root:spark 0750), `/var/lib/local-ai` (spark,
  0751) with `hf/`, `open-webui/`, `searxng/` (0750) and `brake/` (spark:spark-admin 2770 — Dan can
  release a brake hold, `agent` can't); `/home/agent/work`; polkit rule for `local-ai-*` units.
  (Superseded 2026-09-25 by commit 3735420: `/var/lib/local-ai` is root:root 0755, so `spark` can't
  swap a child for a link that the next run hands to it; and bootstrap no longer makes
  `/home/agent/work`, since root never writes inside `agent`'s home. `agent` makes its own `~/work`.)
- Note: Dan's account is effectively root-capable (sudo, and spark-admin can edit what the
  `local-ai-*` units run). The isolation boundary on this box is between Dan and `agent`.

- [ ] **Step 1: Write the failing tests**

`spark/tests/test_bootstrap.py`:

```python
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "stack/host/bootstrap.sh"


def dry_run() -> list[str]:
    out = subprocess.run(
        ["bash", str(SCRIPT), "--dry-run"], check=True, capture_output=True, text=True
    ).stdout
    return out.splitlines()


def test_dry_run_lists_every_step():
    text = "\n".join(dry_run())
    for expected in [
        "useradd --create-home --shell /bin/bash agent",
        "useradd --system",
        "systemctl set-default multi-user.target",
        "apt-mark hold",
        "/etc/default/earlyoom",
        "ufw allow OpenSSH",
        "50-local-ai.rules",
        "/etc/local-ai/secrets",
    ]:
        assert expected in text, expected


def test_ssh_is_allowed_before_the_firewall_turns_on():
    lines = dry_run()
    allow = next(i for i, line in enumerate(lines) if "ufw allow OpenSSH" in line)
    enable = next(i for i, line in enumerate(lines) if "ufw --force enable" in line)
    assert allow < enable


def test_agent_never_gets_docker_sudo_or_admin():
    for line in dry_run():
        if "usermod" in line and line.rstrip().endswith(" agent"):
            for forbidden in ("docker", "sudo", "spark-admin"):
                assert forbidden not in line


def test_the_engine_user_never_joins_docker():
    for line in dry_run():
        if "usermod" in line and line.rstrip().endswith(" spark"):
            assert "docker" not in line


def test_the_engine_user_cannot_change_what_root_runs():
    line = next(line for line in dry_run() if "install -d" in line and "/opt/local-ai/etc" in line)
    assert "-o root -g spark-admin" in line


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed")
def test_shellcheck_is_clean():
    subprocess.run(["shellcheck", str(SCRIPT)], check=True)
```

*Superseded 2026-09-25 — the code now differs; see commits d7a78d3 (tests that fail when their line
is missing), 3cf93e4 (the admin is whoever runs it), cb0ec0b and f77a144 (the whole GPU set held,
and loudly), d0c4d3c (every apt package the box relies on), 3735420 (root stays out of paths
`spark` and `agent` control) and ed0e06a (a pending removal's hint, `make hold-gpu`, a dry run that
ignores the host's packages).*

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run --frozen --project spark pytest spark/tests/test_bootstrap.py`
Expected: FAIL — script missing.

- [ ] **Step 3: Write the script and its two config files**

`stack/host/bootstrap.sh`:

```bash
#!/usr/bin/env bash
# Host setup for brightroar (DGX OS, Ubuntu 24.04, aarch64).
#   Preview (changes nothing):  bash stack/host/bootstrap.sh --dry-run
#   Apply (Dan, once):          sudo bash stack/host/bootstrap.sh
# Safe to re-run: every step checks before it changes anything.
set -euo pipefail

DRY_RUN=0
if [[ "${1:-}" == "--dry-run" ]]; then DRY_RUN=1; fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ADMIN_USER="${SUDO_USER:-dan}"

say() { printf '==> %s\n' "$*"; }
run() {
  if (( DRY_RUN )); then printf '+ %s\n' "$*"; else "$@"; fi
}

preflight() {
  (( DRY_RUN )) && return 0
  [[ "$(id -u)" -eq 0 ]] || { echo "bootstrap: run it with sudo" >&2; exit 1; }
  [[ "$(uname -m)" == "aarch64" ]] || { echo "bootstrap: expected aarch64" >&2; exit 1; }
  grep -q '^ID=ubuntu' /etc/os-release || { echo "bootstrap: expected Ubuntu-based DGX OS" >&2; exit 1; }
  getent group docker >/dev/null || { echo "bootstrap: no docker group — is Docker installed?" >&2; exit 1; }
}

ensure_group() {
  if (( DRY_RUN )); then printf '+ groupadd --system %s   (if missing)\n' "$1"; return; fi
  getent group "$1" >/dev/null || groupadd --system "$1"
}

ensure_service_user() {
  if (( DRY_RUN )); then
    printf '+ useradd --system --gid spark --home-dir /var/lib/local-ai --no-create-home --shell /usr/sbin/nologin spark   (if missing)\n'
    return
  fi
  id -u spark >/dev/null 2>&1 || useradd --system --gid spark --home-dir /var/lib/local-ai --no-create-home --shell /usr/sbin/nologin spark
}

ensure_agent_user() {
  if (( DRY_RUN )); then printf '+ useradd --create-home --shell /bin/bash agent   (if missing)\n'; return; fi
  id -u agent >/dev/null 2>&1 || useradd --create-home --shell /bin/bash agent
}

packages() {
  say "packages"
  run apt-get update
  run apt-get install -y earlyoom ufw tmux cmake build-essential ffmpeg jq
}

hold_gpu_stack() {
  say "hold the NVIDIA driver and CUDA packages — they are upgraded deliberately, on upgrade day"
  if (( DRY_RUN )); then
    printf '+ apt-mark hold <installed packages matching nvidia-*, libnvidia-*, cuda-*>\n'
    return
  fi
  local pkgs
  # dpkg-query exits non-zero when a pattern matches nothing; that must not abort the script.
  pkgs="$( { dpkg-query -W -f='${db:Status-Abbrev}\t${Package}\n' 'nvidia-*' 'libnvidia-*' 'cuda-*' 2>/dev/null || true; } | awk '$1 == "ii" {print $2}' | sort -u)"
  if [[ -n "$pkgs" ]]; then
    # shellcheck disable=SC2086  # one package per word is intended
    apt-mark hold $pkgs
  fi
}

users_and_groups() {
  say "users and groups"
  ensure_group spark
  ensure_group spark-users
  ensure_group spark-admin
  ensure_service_user
  ensure_agent_user
  # spark is deliberately NOT in the docker group: docker is root-equivalent, and model engines run
  # as spark. Containers are started by root-owned units instead (Phase 1).
  run usermod -aG video,render,spark-users spark
  run usermod -aG video,render,spark-users agent
  # adm: read every service's journal, so `make logs` works without sudo.
  run usermod -aG spark-admin,spark-users,adm "$ADMIN_USER"
  run chmod 0700 "/home/$ADMIN_USER" /home/agent
}

directories() {
  say "directories"
  # Code and config are root-owned and group-writable by spark-admin. spark only reads them: it runs
  # the engines, and root runs the units and the Compose file that live in etc/.
  run install -d -o root -g spark-admin -m 2775 /opt/local-ai /opt/local-ai/app /opt/local-ai/bin /opt/local-ai/etc /opt/local-ai/python
  run install -d -o root -g spark-admin -m 0750 /etc/local-ai
  run install -d -o root -g spark -m 0750 /etc/local-ai/secrets
  run install -d -o spark -g spark -m 0751 /var/lib/local-ai
  run install -d -o spark -g spark -m 0750 /var/lib/local-ai/hf /var/lib/local-ai/open-webui /var/lib/local-ai/searxng
  run install -d -o spark -g spark-admin -m 2770 /var/lib/local-ai/brake
  run install -d -o agent -g agent -m 0700 /home/agent/work
}

headless() {
  say "boot to a console; start a desktop on demand with: sudo systemctl start display-manager"
  run systemctl set-default multi-user.target
  if (( DRY_RUN )); then printf '+ systemctl stop display-manager   (if running)\n'; return; fi
  if systemctl is-active --quiet display-manager; then systemctl stop display-manager; fi
}

earlyoom_config() {
  say "earlyoom — the last-resort backstop below spark's own brake"
  run install -m 0644 "$HERE/earlyoom.default" /etc/default/earlyoom
  run systemctl enable earlyoom
  run systemctl restart earlyoom
}

firewall() {
  say "firewall: SSH only (tailnet traffic is governed by Tailscale ACLs, which ufw doesn't see)"
  run ufw default deny incoming
  run ufw default allow outgoing
  run ufw allow OpenSSH
  run ufw --force enable
}

polkit_rule() {
  say "let spark-admin manage the local-ai-* services without sudo"
  run install -m 0644 "$HERE/50-local-ai.rules" /etc/polkit-1/rules.d/50-local-ai.rules
}

main() {
  preflight
  packages
  hold_gpu_stack
  users_and_groups
  directories
  headless
  earlyoom_config
  firewall
  polkit_rule
  say "done — continue with website/how-to/bootstrap.md, 'After bootstrap'"
}

main "$@"
```

*Superseded 2026-09-25 — the code now differs; see commits 3cf93e4 (the admin is whoever runs it),
cb0ec0b (the whole GPU set held, and the real hold line in the dry run), d0c4d3c (every apt package
the box relies on), 3735420 (a root-owned `/var/lib/local-ai`, nothing inside `agent`'s home),
f77a144 (a loud hold, `--hold-gpu`, unknown options refused) and ed0e06a (a pending removal's
hint).*

`stack/host/earlyoom.default`:

```sh
# Installed by stack/host/bootstrap.sh — edit it there, not here.
# earlyoom is the last resort, below spark's own brake (the plan: Admission and memory rules).
#   -M 12582912,9437184  SIGTERM below 12 GiB available, SIGKILL below 9 GiB (values in KiB)
#   -s 100,100           ignore swap, so a full swap can't keep it from acting
#   --prefer / --avoid   kill model engines first; never what keeps the box reachable
# systemd splits this value on spaces and does not interpret quotes, so the regexes contain
# neither.
EARLYOOM_ARGS="-r 3600 -M 12582912,9437184 -s 100,100 --prefer ^(llama-server|whisper-server|VLLM::EngineCor)$ --avoid ^(sshd|systemd|systemd-.*|tmux.*|tailscaled|dockerd|containerd|llama-swap|spark)$"
```

*Superseded 2026-09-25 — the code now differs; see commit 3735420 (`sshd.*` in `--avoid`, which
also covers OpenSSH's `sshd-session`).*

`stack/host/50-local-ai.rules`:

```js
// Installed by stack/host/bootstrap.sh. Lets members of spark-admin reload systemd and manage the
// local-ai-* units (start, stop, restart) without sudo — so `make apply` needs no password.
polkit.addRule(function (action, subject) {
  if (!subject.isInGroup("spark-admin")) {
    return polkit.Result.NOT_HANDLED;
  }
  if (action.id === "org.freedesktop.systemd1.reload-daemon") {
    return polkit.Result.YES;
  }
  if (action.id === "org.freedesktop.systemd1.manage-units") {
    var unit = action.lookup("unit") || "";
    if (unit.indexOf("local-ai-") === 0) {
      return polkit.Result.YES;
    }
  }
  return polkit.Result.NOT_HANDLED;
});
```

`chmod +x stack/host/bootstrap.sh`

- [ ] **Step 4: Makefile**

Add (and put `bootstrap bootstrap-dry-run` in `.PHONY`; add `stack/host/bootstrap.sh` to `lint`):

```make
bootstrap-dry-run: ## Print what bootstrap would do; changes nothing
	bash stack/host/bootstrap.sh --dry-run

bootstrap: ## Host setup on the Spark (Dan; asks for sudo once)
	sudo bash stack/host/bootstrap.sh
```

- [ ] **Step 5: Run the tests — they pass; lint is clean**

Run: `uv run --frozen --project spark pytest spark/tests && make lint`
Expected: all pass; shellcheck prints nothing.

- [ ] **Step 6: Commit**

```bash
git add stack/host spark/tests/test_bootstrap.py Makefile
git commit -m "build(stack): 🤖 add the host bootstrap script, earlyoom config and polkit rule" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 8 [Mac]: runbooks for the steps a person does

**Files:**
- Create: `website/how-to/index.qmd`, `website/how-to/leak-guards.md`,
  `website/how-to/bootstrap.md`, `website/how-to/tailscale.md`, `website/how-to/secret-files.md`,
  `website/how-to/spark-session.md`

Each runbook is short, exact, and never shows how to print a secret. Content:

- [ ] **Step 1: `how-to/index.qmd`** — a listing of the pages in this folder
  (`listing: contents: "*.md"`, `type: table`, fields `title`, `description`).

- [ ] **Step 2: `how-to/leak-guards.md`** — install gitleaks and shellcheck
  (`brew install gitleaks shellcheck` on the Mac; on the Spark, `gitleaks_8.30.1_linux_arm64.tar.gz` from the v8.30.1 release, downloaded into
  a temporary directory (`cd "$(mktemp -d)"`), checked against `gitleaks_8.30.1_checksums.txt` (expected:
  `gitleaks_8.30.1_linux_arm64.tar.gz: OK`), and installed with `sudo install -m 0755 gitleaks /usr/local/bin/`,
  each step chained with `&&` so a failed checksum stops the install — Ubuntu's
  archive copy is 8.16, too old for the hooks, and `/usr/local/bin` wins in every shell); create
  `~/.config/local-ai/denylist` by hand on **each** machine — one case-insensitive regex per line for
  every term that must never appear in this public repo (the tailnet's name, the NAS's names, the LAN
  subnet prefix, anything else private); `make hooks`; the leak drill from Task 3 Step 7; what to do
  when a line is safe but flagged (`leakcheck: allow`, sparingly, visible in review); never
  `--no-verify`. (Added 2026-09-25, after cbd7ca3, f581016 and bad1b55: the denylist needs at least
  one term, since the hooks and `make hooks` refuse an empty one; `make hooks` runs the hooks' own
  checks, gitleaks' `git` command and a denylist with terms that parses; what the hooks check —
  file names, type changes, UTF-16/32 text, and binaries named for a person to check; how to read a
  red CI leaks job, whose log shows whether it holds findings, and how to find a history finding's
  commit at the commit CI checked; and why a Dependabot PR is merged or rebased, never squashed.)

- [ ] **Step 3: `how-to/bootstrap.md`**
  - *Before:* bounce the wired NIC so it takes its reservation — from an SSH session over **Wi-Fi**:
    `sudo nmcli device disconnect <wired-iface> && sudo nmcli device connect <wired-iface>`
    (find the name with `nmcli device status`); confirm the wired address ends in `.201`.
  - *Run:* `make bootstrap-dry-run`, read it, then `make bootstrap`. It stops any running desktop
    session — run it over SSH. (Added 2026-09-25: the dry run's hold step prints
    `GPU set: N packages, M already held`, all held on a bootstrapped Spark; off the Spark, with no
    DGX kernel installed, it says `a real run stops here`; a package that isn't cleanly installed
    stops it with a hint.)
  - *After bootstrap* (the heading bootstrap.sh's last message names): log out and back in (new
    groups); `systemctl get-default` → `multi-user.target`;
    `systemctl is-active earlyoom` → `active`; `sudo ufw status` → OpenSSH allowed;
    `id agent` shows no `docker`, `sudo` or `spark-admin`; `free -g` for the new baseline.
  - *Agent login:* add your Mac's **public** key to `agent`, in two steps — `sudo` inside a command
    piped into `ssh` has no terminal to ask for the password. On the Mac,
    `scp ~/.ssh/<your-key>.pub brightroar:agent-key.pub`; then, in an interactive `ssh brightroar`
    session,
    `sudo -u agent sh -c 'umask 077 && mkdir -p /home/agent/.ssh && cat >> /home/agent/.ssh/authorized_keys' < ~/agent-key.pub && rm ~/agent-key.pub`;
    then `ssh agent@brightroar` and, **as agent** (the installer refuses to run under sudo), install
    Claude Code with `curl -fsSL https://claude.ai/install.sh | bash`. Log in: run `claude`; with no
    browser on the box, press `c` to copy the login URL, open it on the Mac, and paste the code back.
    (Corrected 2026-09-25: the key step was `sudo install -d … && sudo tee -a … && sudo chown … &&
    sudo chmod …` on `/home/agent/.ssh`, which writes as root through a path `agent` controls, so a
    planted link could aim it anywhere. Now Dan's shell reads the key and `agent` writes it into its
    own home; nothing writes as root. `changelog.md` keeps the command the 2026-09-24 run used.)
  - (Added 2026-09-24: `how-to/ssh.md` covers the Mac side: a key per account, the Mac's
    `~/.ssh/config` with the tailnet name first and the LAN as fallback, and keeping NVIDIA Sync's
    own config apart. Added 2026-09-25: a LAN alias for `agent` too, for before Tailscale is
    joined; and keys-only SSH once Tailscale is joined and Dan's own key works — an sshd drop-in,
    `10-local-ai.conf`, that sorts before `50-cloud-init.conf`, with `PasswordAuthentication no`,
    `KbdInteractiveAuthentication no` and, for `agent`, `AllowAgentForwarding no`; `sshd -t` and
    `sshd -T` before the reload; a key login from a second terminal before the first session
    closes; and a one-time check whether the Spark is reachable over public IPv6.)
  - *Live checks as `agent`* (you run these — they need sudo):
    `sudo -u agent sh -c 'if test -x "$1"; then echo "OPEN: stop and fix permissions"; else echo "closed: good"; fi' _ "$HOME"`
    → `closed: good` (`$HOME` expands in your shell; `test -x` asks whether `agent` can enter your
    home, without reading anything; the verdict comes from `agent`'s shell, so a failed `sudo` never
    reads as "good"; plain `-u`, because `-i` would expand `$1` in `agent`'s login shell);
    `sudo -iu agent docker ps` → permission denied;
    `sudo -iu agent nvidia-smi --query-gpu=name --format=csv,noheader` → the GPU's name, without the
    per-unit UUID that `nvidia-smi -L` prints.
  - *Re-run once* (`make bootstrap` again) → finishes with no errors, and no new users, groups or
    config lines.

- [ ] **Step 4: `how-to/tailscale.md`**
  - Install on the Spark: `curl -fsSL https://tailscale.com/install.sh | sh`, then
    `sudo tailscale up`.
  - After joining: admin console → **Machines** → the Spark → **Disable key expiry** — a headless
    server whose key expires silently drops off the tailnet.
  - In the admin console: enable **MagicDNS** and **HTTPS certificates** (the certificate's name
    appears in public certificate-transparency logs — the name only).
  - **ACL grants are the firewall** for tailnet traffic (ufw can't see `tailscale0`); the policy is
    in the admin console, and the real one is kept in the vault. A new tailnet's allow-all grant is
    replaced, keeping today's access: create `tag:spark` (`tagOwners`, owner `autogroup:admin`);
    set three grants from `autogroup:member` — to `autogroup:member` (`*`), to `<home-subnet>/24`
    (`*`), and to `tag:spark` (`22`, `443`) — plus `autogroup:internet` if an exit node is used;
    save; tag the Spark with `sudo tailscale up --advertise-tags=tag:spark` (a tagged key doesn't
    expire); test SSH and home-LAN services from the Mac and phone. Port 22 for SSH; 443 for
    `tailscale serve` (Phase 1). LiteLLM's port is added in Phase 3.
  - Confirm how the tailnet reaches the home LAN (the subnet router and its advertised route) in the
    admin console, and record it in the vault — never paste `tailscale status` output anywhere public.
    (Added 2026-09-24: there is none today, by choice; the runbook gives the steps for a one-address
    subnet route from an always-on home machine, not the Spark, for when one is needed.)
  - Rebuilds: delete the old device in the admin console **before** re-joining, or the box comes back
    as `brightroar-1` and every client breaks.

- [ ] **Step 5: `how-to/secret-files.md`** — every command below runs in **Dan's** terminal; none of
  them displays a value. (Ubuntu's `sh` is dash, which lacks `read -s`, so these use `bash -c`.)
  - Files live in `/etc/local-ai/secrets/` (root:spark, 0640), one per service, `KEY=value` lines,
    no `export`. Dan's own account can't list that folder, so nothing running as Dan reads a secret
    by accident. A new random key:
    `sudo bash -c 'umask 027; printf "LLAMASWAP_KEY_AGENT=%s\n" "$(openssl rand -hex 32)" >> /etc/local-ai/secrets/llama-swap.env; chgrp spark /etc/local-ai/secrets/llama-swap.env'`
  - Phase 1 needs: `llama-swap.env` — `LLAMASWAP_KEY_DAN_MAC`, `LLAMASWAP_KEY_AGENT`,
    `LLAMASWAP_KEY_OPENWEBUI`, `LLAMASWAP_KEY_SPARK` (`LLAMASWAP_KEY_OPENWEBUI` and
    `LLAMASWAP_KEY_SPARK` made with the same pattern as the example; `LLAMASWAP_KEY_OPENWEBUI` must
    exist before the `open-webui.env` step, which copies it); `open-webui.env` — `WEBUI_SECRET_KEY` (its own
    random value — without it, recreating the container logs everyone out), plus `OPENAI_API_KEYS`,
    `RAG_OPENAI_API_KEY` and `AUDIO_STT_OPENAI_API_KEY`, all three holding the value of
    `LLAMASWAP_KEY_OPENWEBUI`; if that key doesn't exist yet, the command refuses and writes nothing:
    `sudo bash -c '. /etc/local-ai/secrets/llama-swap.env; [ -n "$LLAMASWAP_KEY_OPENWEBUI" ] || { echo "create LLAMASWAP_KEY_OPENWEBUI first" >&2; exit 1; }; umask 027; for k in OPENAI_API_KEYS RAG_OPENAI_API_KEY AUDIO_STT_OPENAI_API_KEY; do printf "%s=%s\n" "$k" "$LLAMASWAP_KEY_OPENWEBUI"; done >> /etc/local-ai/secrets/open-webui.env; chgrp spark /etc/local-ai/secrets/open-webui.env'`;
    `searxng.env` — `SEARXNG_SECRET`; `hf.env` — `HF_TOKEN`, pasted without echo:
    `sudo bash -c 'read -rsp "HF token: " t; echo; umask 027; printf "HF_TOKEN=%s\n" "$t" >> /etc/local-ai/secrets/hf.env; chgrp spark /etc/local-ai/secrets/hf.env'`.
  - The Mac's key: generate it on the Mac —
    `printf 'export SPARK_API_KEY=%s\n' "$(openssl rand -hex 32)" >> ~/.secrets` — then send the
    same value to the Spark without displaying it:
    `( . ~/.secrets; printf 'LLAMASWAP_KEY_DAN_MAC=%s\n' "$SPARK_API_KEY" ) | ssh brightroar 'umask 077; cat > ~/.spark-key-in'`
    and on the Spark, with the same `umask` and `chgrp` as the other commands:
    `sudo bash -c 'umask 027 && cat >> /etc/local-ai/secrets/llama-swap.env && chgrp spark /etc/local-ai/secrets/llama-swap.env' < ~/.spark-key-in && rm ~/.spark-key-in`
    (the file arrives on standard input, so no home directory is named, and `&&` throughout keeps
    it until its line is in `llama-swap.env`).
  - Record each secret in the vault **by reference** (file, variable name) — never the value.
  - (Added 2026-09-24: the runbook is now numbered steps, one command each, with the same commands,
    plus a check that lists key names and permissions without values, and a dedupe that keeps a
    key's last copy without displaying it.)
  - (Corrected 2026-09-25: the names check used `cut -d= -f1`, which prints in full any line with no
    `=`. It now prints names with `sed -n 's/=.*//p'` and only counts the other lines, with a command
    that drops them. A second check compares Open WebUI's three copies with
    `LLAMASWAP_KEY_OPENWEBUI` by hash and prints only `match` or `MISMATCH`.)

- [ ] **Step 6: `how-to/spark-session.md`**
  - (Added 2026-09-25: this runbook comes first, before any **[Spark]** task. Task 9 already
    needed the session, but this plan reached the runbook only in Task 10. Before the first session:
    the clone; the Mac's global rules, `~/.claude/CLAUDE.md`; and its secrets guard, the deny rules
    and `PreToolUse` hook from the Mac's user-level settings, copied to the Spark and checked in the
    session with `/hooks`, `/permissions` and a `test -e ~/.secrets` the hook must refuse.)
  - `ssh brightroar`, `tmux new -As spark-build`, `cd ~/git/hub/local-ai`, `claude`.
  - Install the same Claude Code plugins as on the Mac (at least superpowers).
  - GitHub for pushes from the Spark: `gh auth login` in *your* account (never as `agent`).
    (Corrected 2026-09-25: a plain `gh auth login` gives the box a token that can push to every
    repository Dan can. The runbook now uses a fine-grained token for this repository only —
    Contents read and write, Actions read-only, Workflows off, so merges that change workflows are
    made on the Mac — pasted into `gh auth login --with-token` from a prompt that doesn't echo, then
    revokes the broad token on github.com.)
  - Private context: copy this project's private memory folder from the Mac to the Spark —
    `ssh brightroar 'mkdir -p ~/.claude/projects/-home-chendaniely-git-hub-local-ai'` then
    `scp -r ~/.claude/projects/-Users-dan-git-hub-local-ai/memory brightroar:.claude/projects/-home-chendaniely-git-hub-local-ai/`
    (the folder is named after the clone's path, so it carries each machine's login: `dan` on the
    Mac, `chendaniely` on the Spark).
  - The session follows the phase plan's **[Spark]** tasks only; at a switch point it commits and
    you push.

- [ ] **Step 7: Build and commit**

Run: `make docs`
Expected: the How-to menu lists five runbooks; no scenario problems.

```bash
git add website/how-to
git commit -m "docs(website): 🤖 add runbooks for leak guards, bootstrap, Tailscale, secrets and the Spark session" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

## ⇄ Switch point — Mac → Spark

- [ ] Run `make test lint docs` one last time on the Mac; all clean.
- [ ] **Dan OKs the push:** `git push -u origin phase-0`. Watch CI: `gh run watch` — all four jobs
  green. If a job fails, fix it on the Mac before switching.
- [ ] (Added 2026-09-25) **Dan starts the Spark session** with `website/how-to/spark-session.md`
  before Task 9, which runs in it.

***

### Task 9 [Spark]: clone, hooks, inventory, dry-run

**Files:**
- Modify: `stack/versions.yaml` (gitleaks pin for the Spark is recorded only as a version — the
  Spark install is system-wide, in `/usr/local/bin`), `README.md` §Current state

- [ ] **Step 1: Clone and enable the guards**

```bash
git clone https://github.com/chendaniely/local-ai ~/git/hub/local-ai
cd ~/git/hub/local-ai && git switch phase-0
```

gitleaks: Ubuntu's archive copy (8.16.0) is too old for the hooks, so **Dan** installs the 8.30.1
release binary to `/usr/local/bin` per `website/how-to/leak-guards.md` (it needs sudo); check with
`command -v gitleaks` → `/usr/local/bin/gitleaks` and `gitleaks version` → `8.30.1`. Dan creates the Spark's `~/.config/local-ai/denylist`. Then `make hooks`.
Expected: "leak-check hooks on for this clone".

- [ ] **Step 2: Toolchain facts**

Run: `uv --version && /usr/local/cuda/bin/nvcc --version | tail -1 && docker --version && gh --version | head -1 && (cmake --version | head -1 || echo "cmake: bootstrap installs it")`
Expected: uv 0.12.18; CUDA 13.x; Docker present; gh 2.45.0 (Ubuntu's archive — enough for
`gh auth login` and pushing). If `nvcc` isn't under `/usr/local/cuda/bin`, find it
with `ls -d /usr/local/cuda*` and note the path — Phase 1's engine builds need it.

- [ ] **Step 3: Inventory what holds memory**

Run, and read each output privately:

```bash
free -g
systemctl list-units --type=service --state=running --no-pager
snap list 2>/dev/null
docker ps -a --format '{{.Names}}\t{{.Image}}\t{{.Status}}'
systemctl is-active display-manager
```

Write a **public-safe summary** — no addresses, container IDs or unit instance names that carry
identifiers — into `README.md` §Current state: the `free -g` baseline (total/used/available), whether
a desktop session was running, and any service or snap that holds memory (e.g. an Ollama snap).
Private detail goes to `~/local-ai-private/phase-0-inventory.md` (outside the repo) for the vault.

- [ ] **Step 4: Bootstrap dry-run**

Run: `make bootstrap-dry-run`
Expected: the full plan prints; nothing changes.

The dry run skips preflight and prints a placeholder for the packages it would hold, so check what
it can't show. Each check is read-only; read the output privately:

```bash
getent group docker                                     # exists — bootstrap stops without it
ls /etc/ufw/applications.d                              # openssh-server: the file behind ufw's OpenSSH profile
dpkg -l | grep -Ei 'nvidia|cuda|linux-modules-nvidia'   # what the hold would cover
```

`ufw app list` names the profile itself (`OpenSSH`), but ufw needs root even to list, so that one
is Dan's: `sudo ufw app list`. In the `dpkg` list, note any precompiled `linux-modules-nvidia-*`
packages — the hold's patterns (`nvidia-*`, `libnvidia-*`, `cuda-*`) miss them.

*Superseded 2026-09-25 — the code now differs: since commit cb0ec0b the dry run prints the real
`apt-mark hold` line, computed read-only, and the patterns also cover the NVIDIA modules, the kernel
metapackages and CUDA's version-named libraries; since f77a144 it says
`GPU set: N packages, M already held`.*

Note anything that looks wrong for this box (a missing group, a package name) and fix
`stack/host/bootstrap.sh` + its test before Dan runs it.

- [ ] **Step 5: Commit**

```bash
git add README.md stack/host  # only if the script changed
git commit -m "docs(readme): 🤖 record the Spark's pre-bootstrap memory baseline" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

## ⇄ Switch point — Spark → Dan

- [ ] **Dan OKs the push** of the Spark's commits (`git push`), so the runbooks and any script fix are
  on GitHub.

***

### Task 10 [Dan]: the privileged and interactive steps

Follow the runbooks, in the order `website/how-to/index.qmd` lists them:

- [ ] Before the first: use the LAN aliases (`brightroar-lan`, `brightroar-agent-lan`) until
  Tailscale is joined; install Claude Code and uv as Dan with their official installers, and
  `sudo apt install tmux gh`.
- [ ] `website/how-to/spark-session.md` — first, before any **[Spark]** task: the Mac's rules and
  secrets guard on the Spark, plugins, a GitHub token for this repository only, the private memory
  files.
- [ ] `website/how-to/leak-guards.md` — gitleaks on the Spark, the denylist, `make hooks`, the
  drill.
- [ ] `website/how-to/bootstrap.md` — NIC bounce, `make bootstrap`, re-login, agent SSH key, Claude
  Code for `agent`, the live checks, one re-run.
- [ ] `website/how-to/ssh.md` — a key per account, the Mac's `~/.ssh/config`, NVIDIA Sync,
  keys-only SSH, the public IPv6 check.
- [ ] `website/how-to/tailscale.md` — install, join, MagicDNS + HTTPS, ACL grants, the route home
  (into the vault).
- [ ] `website/how-to/secret-files.md` — every Phase 1 secret file, the HF token, the Mac key.
- [ ] After `tailscale.md`: back to `website/how-to/ssh.md` for Keys only and the public IPv6
  check, which need the tailnet name.

Then ongoing, not once: `website/how-to/updates.md` — apt any time, upgrade day on Saturdays.

(Corrected 2026-09-25, in Dan's order: this list first put `spark-session.md` last, after the
session had already run Task 9, and left out `leak-guards.md`, `ssh.md` and `updates.md`. The
first and last items make the order followable on a new box: step 1 needs tmux, gh, Claude Code
and uv, and nothing reaches the tailnet names before Tailscale is joined.)

## ⇄ Switch point — Dan → Spark

***

### Task 11 [Spark]: verify the host and record it

**Files:**
- Modify: `changelog.md`, `README.md` §Current state (same commit — the sync rule)

- [ ] **Step 1: Check each result**

```bash
systemctl get-default                        # multi-user.target
free -g                                      # the headless baseline
systemctl is-active earlyoom                 # active
grep '^EARLYOOM_ARGS' /etc/default/earlyoom  # matches stack/host/earlyoom.default
journalctl -u earlyoom -b --no-pager | grep -i prefer   # regex received with no quotes: ^(llama-server|whisper-server|VLLM::EngineCor)$
swapon --show                                # note whether there is swap (-s 100,100 ignores it either way)
systemctl is-active systemd-oomd             # if active: two OOM killers — decide in Task 12's review
id agent                                     # no docker, sudo or spark-admin
apt-mark showhold | grep -E '^(linux-image-nvidia-hwe|nvidia-driver|cuda-toolkit)-'   # the GPU set is held: kernel, driver, CUDA
stat -c '%a %U:%G %n' "$HOME" /home/agent /etc/local-ai/secrets /opt/local-ai
ls /etc/local-ai/secrets                     # "Permission denied" — Dan's sessions can't list secrets
tailscale status --self --json | jq -r '.Self.Online'   # true
systemd-run --unit=local-ai-probe --wait true   # as Dan, no sudo: a password prompt or a denial
```

Expected: every line as commented; Dan's home (`/home/chendaniely`) and `/home/agent` are `700`; `/opt/local-ai` is
`2775 root:spark-admin`.
earlyoom's `--prefer` only adds 300 to `oom_score`, and GB10's GPU memory may not count toward that
score — Phase 1's launch wrapper sets each engine's own `oom_score_adj` to 1000 to make engines the
first victims regardless.

The `systemd-run` probe checks that the polkit rule doesn't grant *transient* units: a password
prompt (cancel it) or a denial is right. If it runs without a password, anything running as Dan
can start a root unit named `local-ai-*` — tighten the rule at Task 12.

- [ ] **Step 2: earlyoom's victim choice, without killing anything** — a **[Dan]** check (it needs
  sudo). Dan runs, for about five seconds, then Ctrl-C:

```bash
sudo earlyoom --dryrun -r 1 -M 125829120,125829110 -s 100,100 \
  --prefer '^(llama-server|whisper-server|VLLM::EngineCor)$' \
  --avoid '^(sshd|systemd|systemd-.*|tmux.*|tailscaled|dockerd|containerd|llama-swap|spark)$'
```

The `-M` values sit above the box's free memory on purpose, so earlyoom believes it must act.
Expected: it reports the process it *would* kill, and that process is none of the avoided ones.
(Superseded 2026-09-25: `stack/host/earlyoom.default` now avoids `sshd.*`, which also covers
OpenSSH's `sshd-session` — commit 3735420. A re-run of this check uses the file's current
regexes.)

- [ ] **Step 3: Record the machine changes — both files, one commit**

`changelog.md` — a new dated entry at the top: bootstrap applied (headless by default, earlyoom,
ufw SSH-only, users `spark` and `agent`, polkit rule, the GPU set held: kernel, NVIDIA modules,
driver, CUDA), the wired NIC
on `.201`, joined to the tailnet, Claude Code for `agent`. Command lines only, never output.

`README.md` §Current state — the same facts as current state: headless (and the new `free -g`
baseline), on the tailnet, the three identities, earlyoom and ufw active.

```bash
git add changelog.md README.md
git commit -m "docs(machine): 🤖 record the Phase 0 bootstrap of brightroar" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

## ⇄ Switch point — Spark → Mac

- [ ] **Dan OKs the push** from the Spark; on the Mac, `git pull`.

***

### Task 12 [Mac]: vault entry note, council review, forward look, merge

- [ ] **Step 1: Vault entry note** — add a "Private files (from Phase 0)" section to
  `zettelkasten/local-ai/brightroar Local AI Stack.md` in the vault: each secret file's path and the
  variable names it holds (**by reference, never values**), the private values file, the denylists on
  both machines, the Tailscale ACL policy and the route home, and the Spark's private inventory
  (copied from `~/local-ai-private/phase-0-inventory.md` over SSH with Dan's OK).
- [ ] **Step 2: Council review of Phase 0** — four reviewers against the plan: goal-fit and
  scenarios; reliability (bootstrap idempotency, hook failure modes); security and simplicity (leak
  paths, permissions, polkit scope); toolstack (pins, CI actions). Fix what they find, one commit per
  fix.
- [ ] **Step 3: Look forward** — does anything learned change Phase 1 (the CUDA arch flag, uv's
  location, a memory holder that must go, the route home)? If so: update `website/design/plan.md`
  (and a Revisions line) and `website/design/phase-1.md`, in one commit.
- [ ] **Step 4: Merge and push** —

```bash
git switch main && git pull
git merge --no-ff phase-0 -m "chore(repo): 🤖 merge phase 0" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

then **Dan OKs** `git push`.

## Phase 0 is done when

- A `free -g` baseline for the headless box is in `README.md` §Current state.
- A planted fake private address is refused by the pre-commit hook (Task 3 Step 7), on both
  machines.
- `agent` cannot read Dan's files or use Docker, and can see the GPU (Task 10's live checks).
- `make docs` builds the site and CI is green on `main`.

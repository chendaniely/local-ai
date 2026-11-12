---
title: "Tools each machine needs"
description: "What to install on the Mac and the Spark, and what CI installs, for the tests, the leak hooks and the site; and which tests pass only on Linux."
---

This page covers what the repo's own work needs: the tests (`make test`), the leak hooks (`make hooks`)
and the site (`make docs`). The stack's own packages are [Bootstrap the Spark](bootstrap.md)'s, and
each harness's are on its own page. *(Added 2026-11-11, at Dan's request: "if things need to be
installed we should document the brew commands or something that need to be installed for things
to work, in ci, mac, and or the spark (ubuntu) if applicaible".)*

## The Mac

**On the Mac:**

```bash
brew install gitleaks shellcheck coreutils uv gh
```

- **gitleaks** (8.19 or later) and **shellcheck**: the leak hooks and the shell tests ([Leak
  guards](leak-guards.md)).
- **coreutils**: its `timeout`, which the install-units tests and `test_cuda_ceiling.py` run.
  Without it, 27 more tests fail (seen 2026-11-11, when it was missing).
- **uv**: every Python command goes through it, and it brings the pinned Python itself.
- **gh**: pushes, and the notices to other projects.
- **Quarto**, for `make docs`: any install works ([quarto.org](https://quarto.org/docs/get-started/)).
  This Mac's comes from qvm, Quarto's version manager (1.11.6 on 2026-11-11).
- **Optional:** node, which `test_polkit.py` uses and skips without; Docker Desktop, which
  `test_ntfy_records.py`'s compose test uses. perl ships with macOS.

### What fails on the Mac by design

Tests of code that runs only on the Spark need Linux. On 2026-11-11, with everything above
installed, 62 tests failed on the Mac, and all of them pass in CI:

| Tests | Why they fail on the Mac |
|---|---|
| `test_bootstrap.py`, the cache move's (43) | `bootstrap.sh` runs `find -perm /022`, GNU find's syntax; macOS's `find` refuses it ("illegal mode string") |
| `test_brake.py` (14) | the brake reads Linux's `/proc/sys/kernel/random/boot_id` |
| `test_launch.py` and `test_tickets.py` (1 each) | a process's start time, from Linux's `/proc` |
| `test_notifier_script.py`'s environment test (1) | on the Mac, the environment the script hands curl also holds `SHLVL` and `_` |
| `test_ntfy_records.py`'s compose test and `test_tested_against.py`'s import test (1 each) | not yet looked into |

Some timing tests also fail now and then on the Mac, more often while it's busy: one in
`test_gate_room.py` (it passed 2 of 3 runs on its own) and two signal tests in
`test_cuda_ceiling.py` (they failed beside a Quarto render and passed 3 of 3 on their own; both
2026-11-11). The plan's Backlog has these tests skip off Linux, each with its reason, so a
Mac run shows only real failures; until then, judge a Mac run against this table, and CI.

## The Spark

**On the Spark**, the repo's work needs these, each installed by an earlier runbook:

- **uv, Claude Code, tmux and gh:** before step 1 of [How-to](index.qmd)'s *In order*.
- **git, curl, shellcheck and gh:** [Bootstrap the Spark](bootstrap.md) installs them from Ubuntu's
  archive.
- **gitleaks 8.30.1:** the release binary, since Ubuntu's 8.16 is too old for the hooks ([Leak
  guards](leak-guards.md#install-gitleaks-and-shellcheck)).
- **node:** NodeSource's Node 22, for `agent`'s Pi ([Pi](pi.md)). `test_polkit.py` uses it too,
  and skips without it.

GNU coreutils and findutils, and `/proc`, are Ubuntu's own. Quarto isn't on the Spark yet, so the
site renders on the Mac. Tests there run through `make test`, which caps them at 4 GiB.

## CI

`.github/workflows/ci.yml` runs on GitHub's `ubuntu-24.04` runner, which already has bash, GNU
coreutils and findutils, perl, node, Docker and shellcheck. The workflow adds the rest:

- **uv**, with `astral-sh/setup-uv`;
- **gitleaks 8.30.1**, downloaded and checked against its pinned checksum;
- **Quarto**, with `quarto-dev/quarto-actions/setup`.

Nothing is installed by hand. A change to what CI needs is a change to the workflow, which is
made on the Mac, since the Spark's GitHub token can't push `.github/workflows/`.

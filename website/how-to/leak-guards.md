---
title: "Leak guards"
description: "Install gitleaks and shellcheck, create your denylist, turn the hooks on, prove they bite, see what they check, and read a red CI leaks job."
---

## Install gitleaks and shellcheck

On the Mac:

```bash
brew install gitleaks shellcheck
```

On the Spark, Ubuntu's archive copy of gitleaks is 8.16 — too old for the hooks, which need 8.19
or later. Download the pinned release instead, verify it, and install it to `/usr/local/bin`,
which comes first on `PATH` in every shell, so it wins over the archive copy. The block works in
a temporary directory, so nothing lands in your clone, and each step runs only if the one before
it succeeded, so a failed checksum stops the install:

```bash
cd "$(mktemp -d)"
curl -fsSLO https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_linux_arm64.tar.gz &&
  curl -fsSLO https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_checksums.txt &&
  sha256sum --ignore-missing -c gitleaks_8.30.1_checksums.txt &&
  tar xzf gitleaks_8.30.1_linux_arm64.tar.gz gitleaks &&
  sudo install -m 0755 gitleaks /usr/local/bin/
cd -
```

Expected: `gitleaks_8.30.1_linux_arm64.tar.gz: OK`, and only then the install. `cd -` takes you
back to where you started.

If Ubuntu's copy is installed too (`dpkg -s gitleaks` finds it), remove it, so there is only one
gitleaks on the box and nothing depends on `PATH` order:

```bash
sudo apt remove gitleaks
```

No package manager sees a direct install, so gitleaks never updates by itself. The upgrade steps
are in [Updates](updates.md#upgrade-day-gitleaks).

## Create your denylist

`~/.config/local-ai/denylist` is private. You create it by hand, on **each** machine — it is
never generated, and never committed.

```bash
mkdir -p ~/.config/local-ai && touch ~/.config/local-ai/denylist
```

Edit it with your own editor: one case-insensitive regex per line, for every term that must never
appear in this public repo — the tailnet's name, the NAS's names, the LAN subnet prefix, anything
else private. Blank lines and lines starting with `#` are ignored. Add at least one term before the
next step: a denylist with no terms would pass every commit, so the hooks and `make hooks` refuse
it (`denylist has no terms`). They refuse a line that isn't a valid regular expression too, naming
its line number but never showing it.

## Turn the hooks on

```bash
make hooks
```

This makes the hooks' own checks first: gitleaks has the `git` command (8.19 or later), and your
denylist exists, has terms and parses. Then it points this clone's git hooks at `.githooks`.

The hooks also run `uv` (on the Spark it lives in `~/.local/bin`), so both gitleaks and `uv` must
be on the `PATH` of whatever runs `git commit`. A GUI client, or an editor over Remote-SSH, may
not load your shell profile; a hook refuses its commits with a message naming what's missing.

## Prove they bite

```bash
make hooks
printf 'box at %s\n' "$(printf '%s.%s.%s.%s' 192 168 50 7)" > leak-drill.md
git add leak-drill.md
git commit -m "test: leak drill" ; echo "exit=$?"
git restore --staged leak-drill.md && rm leak-drill.md
```

Expected: the commit is refused (`private IPv4 address`), `exit=1`, nothing committed.

## What they check

Every commit's message, and every staged file except a deletion, runs through gitleaks and then
the repo's own patterns plus your denylist:

- **A file's name as well as its contents.** A name counts as one more line of text.
- **Type changes.** A symlink turned into a file is read as the file; a file turned into a symlink
  is read as its target path, which is what git stores.
- **UTF-16 and UTF-32 text** that starts with a byte-order mark is read as text.
- **Binaries can't be read.** A file with a NUL byte and no byte-order mark, such as a screenshot,
  is named on the way through — `leakcheck: not scanned (binary): <path> — check it by eye` — and
  the commit goes ahead. Look at it yourself before you push.

A finding prints its location and a 3-character excerpt. A path in that output has each match cut
to 3 characters too, so a private term in a file name never prints whole.

## When CI's leaks job is red

CI runs gitleaks and the repo's patterns, without a denylist (it has none), on every push and pull
request. **Read the red step's log first.** Only two kinds of line are findings: gitleaks'
`Finding:` blocks, which end in `leaks found`, and the leak check's
`leakcheck: <where>:<line>: <kind> (…)` lines. A red step without them failed for another reason:

- **download gitleaks** — the network. Re-run the job.
- **check gitleaks against its pinned checksum, then unpack it** — the download doesn't match the
  pin: tampered or corrupt. Don't use it. Re-run once; if it fails again, find out why before
  touching the pin.
- **a repo patterns step with no finding lines** — `uv run --frozen` couldn't fetch the project's
  packages, or the leak check stopped on a setup error (exit 2). Its log says which. Re-run the
  job, or fix what the log names.

Finding lines, in **gitleaks over the whole history** or either **repo patterns** step, are a real
finding, and it is already public. Follow `CLAUDE.md`'s procedure for something sensitive that got
pushed: rotate the credential first, then rewrite history.

The history step reports a finding as `commit message:<line>`: a line of
`git log -p --cc --format='%H%n%B'`'s output at the commit CI checked, `<sha>` (the run's page
shows it). To find the commit without printing the line itself, regenerate that output on the Mac
at `<sha>`, and print the hash of the commit the line falls in:

```bash
git log -p --cc --format='%H%n%B' <sha> > "$TMPDIR/history.txt"
awk -v n=<line> 'NR <= n && length($0) == 40 && /^[0-9a-f]+$/ {c = $0} NR == n {print c; exit}' "$TMPDIR/history.txt"
```

A pull request's run, a Dependabot PR's included, checks out GitHub's merge commit, which isn't in
your clone. `git fetch origin pull/<number>/merge` fetches the PR's current one; use `FETCH_HEAD` as
`<sha>`. It matches the run's only while neither branch has moved since.

**Merging Dependabot's PRs.** The allow marker can't excuse a finding in history: it would only
change a new commit, never the old one. So read a Dependabot PR's commit message before you merge
it, and merge with a merge commit or a rebase, not a squash that pastes its release notes into the
message ([Updates](updates.md#upgrade-day-the-automated-prs)).

## When a line is safe but flagged

Add `leakcheck: allow` to that line, as a trailing comment — the scanner then skips its built-in
patterns (addresses, MACs, names) on that line. It never excuses a denylisted term: the denylist
checks every line. Use it sparingly: it is visible in review, so it should only ever mark
something a reviewer can see for themselves is safe.

## Never bypass this

Never `git commit --no-verify` in this repo.

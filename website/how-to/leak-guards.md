---
title: "Leak guards"
description: "Install gitleaks and shellcheck, create your denylist, turn the hooks on, and prove they bite."
---

## Install gitleaks and shellcheck

On the Mac:

```bash
brew install gitleaks shellcheck
```

On the Spark, Ubuntu's archive copy of gitleaks is 8.16 — too old for the hooks, which need 8.19
or later. Download the pinned release instead, verify it, and install it to `/usr/local/bin`,
which comes first on `PATH` in every shell, so it wins over the archive copy:

```bash
curl -fsSLO https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_linux_arm64.tar.gz
curl -fsSLO https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_checksums.txt
sha256sum --ignore-missing -c gitleaks_8.30.1_checksums.txt
tar xzf gitleaks_8.30.1_linux_arm64.tar.gz gitleaks
sudo install -m 0755 gitleaks /usr/local/bin/
```

## Create your denylist

`~/.config/local-ai/denylist` is private. You create it by hand, on **each** machine — it is
never generated, and never committed.

```bash
mkdir -p ~/.config/local-ai && touch ~/.config/local-ai/denylist
```

Edit it with your own editor: one case-insensitive regex per line, for every term that must never
appear in this public repo — the tailnet's name, the NAS's names, the LAN subnet prefix, anything
else private. Blank lines and lines starting with `#` are ignored.

## Turn the hooks on

```bash
make hooks
```

This checks that gitleaks is on `PATH` and that your denylist exists, then points this clone's
git hooks at `.githooks`.

## Prove they bite

```bash
make hooks
printf 'box at %s\n' "$(printf '%s.%s.%s.%s' 192 168 50 7)" > leak-drill.md
git add leak-drill.md
git commit -m "test: leak drill" ; echo "exit=$?"
git restore --staged leak-drill.md && rm leak-drill.md
```

Expected: the commit is refused (`private IPv4 address`), `exit=1`, nothing committed.

## When a line is safe but flagged

Add `leakcheck: allow` to that line, as a trailing comment — the scanner skips any line
containing it. Use it sparingly: it is visible in review, so it should only ever mark something a
reviewer can see for themselves is safe.

## Never bypass this

Never `git commit --no-verify` in this repo.

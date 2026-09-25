---
title: "The Spark session"
description: "Before any [Spark] task: the Mac's rules and secrets guard on the Spark, then Claude Code in tmux with the Mac's plugins, a GitHub token for this repository only, and this project's private context."
---

This runbook comes first: before any **[Spark]** task, and before the other runbooks. On a new or
rebuilt box, do the [How-to](index.qmd) page's *Before step 1* first. It installs what this runbook
uses, and says which SSH aliases work before Tailscale is joined.

## Before the first session

Claude Code on the Spark knows only what is on the Spark. Give it the rules and guardrails your
Mac's Claude Code has before it runs anything.

1. **The clone**, on the Spark. The repo is public, so this needs no credentials, and it does
   nothing if the clone is already there:

   ```bash
   test -d ~/git/hub/local-ai || git clone https://github.com/chendaniely/local-ai ~/git/hub/local-ai
   ```

2. **Your global rules**, from the Mac. `~/.claude/CLAUDE.md` holds your rules for every project,
   the secrets rule among them. This replaces any copy on the Spark, so edit the Mac's and copy it
   again:

   ```bash
   ssh brightroar 'mkdir -p ~/.claude'
   scp ~/.claude/CLAUDE.md brightroar:.claude/CLAUDE.md
   ```

3. **The secrets guard.** Your Mac's user-level Claude Code settings (normally
   `~/.claude/settings.json`) hold deny rules for the secrets files and a `PreToolUse` hook that
   refuses commands naming them. Copy the script the hook runs to the same place under the Spark's
   `~/.claude`, then add the same hook and deny rules to the Spark's `~/.claude/settings.json`,
   changing any `/Users/dan` in a path to `/home/chendaniely`. Copy those entries, not the whole
   file: the rest of it is the Mac's own. You check the guard in the first session, below.

4. **Private context**, from the Mac: this project's private memory folder.

   ```bash
   ssh brightroar 'mkdir -p ~/.claude/projects/-home-chendaniely-git-hub-local-ai'
   scp -r ~/.claude/projects/-Users-dan-git-hub-local-ai/memory brightroar:.claude/projects/-home-chendaniely-git-hub-local-ai/
   ```

   Claude Code names a project's folder after the clone's full path, with each `/` turned into `-`,
   so the name carries each machine's login: `dan` on the Mac, `chendaniely` on the Spark. A folder
   named for the wrong login is never read.

## Start the session

```bash
ssh brightroar
tmux new -As spark-build
cd ~/git/hub/local-ai
claude
```

Check the guard before anything else. `/hooks` lists the hook, and `/permissions` the deny rules.
Then ask the session to run `test -e ~/.secrets && echo present || echo absent`: the hook must
refuse it. The command reads nothing from the file either way. If it runs instead, stop and fix the
guard before you go on.

## Plugins

Install the same Claude Code plugins you use on the Mac — at least `superpowers`.

## GitHub: a token for this repository only

Pushes from the Spark use your own account, never `agent`'s. The box has no keyring, so `gh` keeps
its token in a plain file under `~/.config/gh/`, where anything running as you can read it. Give it
a token that can push to this one repository and nothing else:

1. On github.com: **Settings → Developer settings → Personal access tokens → Fine-grained tokens →
   Generate new token**. Pick an expiry date. Under **Only select repositories**, choose
   `chendaniely/local-ai`. Repository permissions: **Contents** read and write, **Actions**
   read-only (for `gh run watch`), and **Metadata** read-only, which GitHub requires. Leave the rest,
   Workflows included, at no access. That has a cost: GitHub refuses a push from the Spark whose
   new commits change `.github/workflows/`, and a merge of `main` after a Dependabot Actions bump
   can be one. Make those merges, like every CI change, on the Mac. Copy the token.
2. On the Spark, paste it at the prompt. Nothing shows as you paste, and `printf` is a shell
   builtin, so the token never appears in a process list:

   ```bash
   bash -c 'read -rsp "token: " t; echo; printf "%s\n" "$t" | gh auth login --with-token' && gh auth setup-git
   ```

   `gh auth setup-git` makes git push through `gh`. Both are safe to run again: the token you paste
   replaces the one stored. Then clear the Mac's clipboard: `pbcopy < /dev/null`.
3. `gh auth status` shows the account, with the token masked. Never add `--show-token`.
4. Revoke the old login. A plain `gh auth login` issues a token that can push to every repository
   you can, and `gh auth logout` only forgets a token on this machine. Revoke it on github.com:
   **Settings → Applications → Authorized OAuth Apps → GitHub CLI → Revoke**. That may sign out the
   Mac's GitHub CLI too, if it signed in through the browser: if `gh auth status` fails there, run
   `gh auth login` on the Mac again.
5. Record the token in the vault by reference — its name, the repository, its permissions and its
   expiry date — never the value. Before it expires, make a new one and repeat step 2.

## Scope

The session follows the phase plan's **[Spark]** tasks only. At a switch point, it commits, and
you push.

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

The paragraph right above every command block in this runbook says in bold where it runs.
**On the Mac** is a terminal on the Mac. **On the Spark** is a shell on the Spark as you: from the
Mac, `ssh brightroar` first.

1. **The clone.** The repo is public, so this needs no credentials, and it does nothing if the
   clone is already there. **On the Spark:**

   ```bash
   test -d ~/git/hub/local-ai || git clone https://github.com/chendaniely/local-ai ~/git/hub/local-ai
   ```

2. **Your global rules.** `~/.claude/CLAUDE.md` holds your rules for every project, the secrets
   rule among them. This replaces any copy on the Spark, so edit the Mac's and copy it again.
   **On the Mac:**

   ```bash
   ssh brightroar 'mkdir -p ~/.claude'
   scp ~/.claude/CLAUDE.md brightroar:.claude/CLAUDE.md
   ```

3. **The secrets guard.** Your Mac's user-level Claude Code settings, `~/.claude/settings.json`,
   hold deny rules for the secrets files and a `PreToolUse` hook that runs
   `~/.claude/hooks/block-secret-access.sh`, which refuses commands naming them. Copy the script,
   the hook's entry and the deny rules, not the whole file: the rest of it is the Mac's own. First,
   **on the Mac**:

   ```bash
   ssh brightroar 'mkdir -p ~/.claude/hooks' && scp ~/.claude/hooks/block-secret-access.sh brightroar:.claude/hooks/
   jq '{permissions: {deny: [.permissions.deny[] | gsub("//Users/dan/"; "//home/chendaniely/")]}, hooks: {PreToolUse: [.hooks.PreToolUse[] | select(any(.hooks[]; (.command // "") | test("block-secret-access")))]}}' ~/.claude/settings.json | ssh brightroar 'cat > ~/.claude/spark-guard.json'
   ```

   The second command keeps only the guard's hook and the deny rules, with `/Users/dan` in a rule
   turned into `/home/chendaniely`, the Spark's home; the Mac's other hooks stay behind.

   Then, **on the Spark**, merge them into its settings. This keeps every entry the Spark's file
   already has, its plugins among them, saves the old file as `settings.json.bak`, and is safe to
   run again:

   ```bash
   cd ~/.claude && { [ -f settings.json ] || echo '{}' > settings.json; } && cp -p settings.json settings.json.bak
   jq -s '.[0] as $cur | .[1] as $add | $cur | .permissions.deny = ((($cur.permissions.deny // []) + $add.permissions.deny) | unique) | .hooks.PreToolUse = ([($cur.hooks.PreToolUse // [])[] | select(all(.hooks[]?; (.command // "") | test("block-secret-access") | not))] + $add.hooks.PreToolUse)' settings.json spark-guard.json > settings.json.new && mv settings.json.new settings.json && rm spark-guard.json
   jq '{deny: (.permissions.deny | length), pretooluse: [.hooks.PreToolUse[].hooks[].command]}' settings.json
   ```

   The last command shows at least as many deny rules as the Mac's file has, and
   `bash ~/.claude/hooks/block-secret-access.sh` among the hooks. (These commands were run with the
   Mac's jq 1.7.1 and Ubuntu 24.04's 1.7 on stand-in files, 2026-09-25.) The script needs `jq`,
   which bootstrap installs, and lets everything through without it: the check in the first
   session, below, is what shows it working. If Claude Code is already running on the Spark,
   restart it so it loads the hook. Some of the script's rules name paths that exist only on the
   Mac; when Phase 4 mounts the NAS's shares on the Spark, add their paths to the script.

4. **Private context**: this project's private memory folder. **On the Mac:**

   ```bash
   ssh brightroar 'mkdir -p ~/.claude/projects/-home-chendaniely-git-hub-local-ai'
   scp -r ~/.claude/projects/-Users-dan-git-hub-local-ai/memory brightroar:.claude/projects/-home-chendaniely-git-hub-local-ai/
   ```

   Claude Code names a project's folder after the clone's full path, with each `/` turned into `-`,
   so the name carries each machine's login: `dan` on the Mac, `chendaniely` on the Spark. A folder
   named for the wrong login is never read.

## Start the session

**On the Mac**, log in to the Spark:

```bash
ssh brightroar
```

Then, **on the Spark**, start tmux and Claude Code in the clone:

```bash
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
2. **On the Spark**, paste it at the prompt. Nothing shows as you paste, and `printf` is a shell
   builtin, so the token never appears in a process list:

   ```bash
   bash -c 'read -rsp "token: " t; echo; printf "%s\n" "$t" | gh auth login --with-token' && gh auth setup-git
   ```

   `gh auth setup-git` makes git push through `gh`. Both are safe to run again: the token you paste
   replaces the one stored. Then, **on the Mac**, clear the clipboard: `pbcopy < /dev/null`.

   `gh` hands git the token only over https. The clone from step 1 of *Before the first session*
   has an https `origin`, but a clone made another way may push over SSH, around the token. This
   sets it to https whatever it was, and is safe to run again. **On the Spark:**

   ```bash
   git -C ~/git/hub/local-ai remote set-url origin https://github.com/chendaniely/local-ai.git
   ```

3. **On the Spark**, `gh auth status` shows the account, with the token masked. Never add
   `--show-token`.
4. **GitHub must refuse the Spark over SSH**, or a key on the box can push around the token.
   **On the Spark:**

   ```bash
   ssh -T git@github.com
   ```

   It must end in `Permission denied (publickey).` If it greets you by name instead, a key on the
   Spark is on your GitHub account: `gh auth login` can upload one when you pick SSH for git.
   Remove it on github.com, **Settings → SSH and GPG keys**, and check again. To tell which entry
   it is, this prints the key file ssh used and its fingerprint, which GitHub's list shows too.
   **On the Spark:**

   ```bash
   ssh -v -T git@github.com 2>&1 | grep 'Server accepts key'
   ```

   Leave the key file itself on the Spark: other hosts may use it.
5. Revoke the old login. A plain `gh auth login` issues a token that can push to every repository
   you can, and `gh auth logout` only forgets a token on this machine. Revoke it on github.com:
   **Settings → Applications → Authorized OAuth Apps → GitHub CLI → Revoke**. That may sign out the
   Mac's GitHub CLI too, if it signed in through the browser: if `gh auth status` fails **on the
   Mac**, run `gh auth login` there again.
6. Record the token in the vault by reference — its name, the repository, its permissions and its
   expiry date — never the value. Before it expires, make a new one and repeat step 2.

## Scope

The session follows the phase plan's **[Spark]** tasks only. At a switch point, it commits, and
you push.

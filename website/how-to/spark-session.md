---
title: "The Spark session"
description: "Start Claude Code on the Spark in tmux, with the Mac's plugins, your own GitHub login, and this project's private context."
---

## Start the session

```bash
ssh brightroar
tmux new -As spark-build
cd ~/git/hub/local-ai
claude
```

## Plugins

Install the same Claude Code plugins you use on the Mac — at least `superpowers`.

## GitHub

For pushes made from the Spark: `gh auth login`, in **your own** account — never as `agent`.

## Private context

Copy this project's private memory folder from the Mac to the Spark:

```bash
ssh brightroar 'mkdir -p ~/.claude/projects/-home-dan-git-hub-local-ai'
scp -r ~/.claude/projects/-Users-dan-git-hub-local-ai/memory brightroar:.claude/projects/-home-dan-git-hub-local-ai/
```

## Scope

The session follows the phase plan's **[Spark]** tasks only. At a switch point, it commits, and
you push.

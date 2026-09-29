---
title: "S12 · Long agent run, laptop closed"
scenario-id: S12
phase: 1
status: built
---

**Situation.** I start a long agent run in tmux on the Spark as `agent`, and close the
laptop.

**What happens.** The run keeps going. I reattach from the Mac with
`ssh -t brightroar-agent tmux a`. From Phase 2, hooks post done, needs input, or failed.
*(Built 2026-09-28: Phase 1's half was checked that day, in Task 15. As `agent`, pi ran a task on
the coder in tmux, and detaching, logging out and reattaching kept the session. The hooks and the
notifications arrive in Phase 2.)*

**What I see.** The tmux session, and later, notifications and the session on the menu bar.

**How to override.** None needed.

---
title: "S12 · Long agent run, laptop closed"
scenario-id: S12
phase: 1
status: planned
---

**Situation.** I start a long agent run in tmux on the Spark as `agent`, and close the
laptop.

**What happens.** The run keeps going. I reattach with `ssh agent@brightroar -t tmux a`. From
Phase 2, hooks post done, needs input, or failed.

**What I see.** The tmux session, and later, notifications and the session on the menu bar.

**How to override.** None needed.

---
title: "S11 · Trying a new model"
scenario-id: S11
phase: 2
status: planned
---

**Situation.** A promising open model is released.

**What happens.** `spark try <hf-repo>` serves it from a separate lab instance through an
uncommitted overlay. Its footprint is estimated, then measured, and a trial note opens in my
vault. `spark promote` adopts it after the bake-off; `spark forget` removes it.

**What I see.** The trial in `spark status`.

**How to override.** None needed.

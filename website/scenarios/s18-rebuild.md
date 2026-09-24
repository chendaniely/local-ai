---
title: "S18 · Rebuild after a factory reset"
scenario-id: S18
phase: 4
status: planned
---

**Situation.** The box has been factory reset.

**What happens.** I follow the runbooks: delete the old Tailscale device first, then
bootstrap, then restore from the Synology, then run `spark doctor`. This is verified by a
dated drill.

**What I see.** The same stack, the same names.

**How to override.** None.

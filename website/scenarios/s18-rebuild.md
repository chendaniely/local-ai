---
title: "S18 · Rebuild after a factory reset"
scenario-id: S18
phase: 5
status: planned
---

**Situation.** The box has been factory reset.

**What happens.** I follow the runbooks: delete the old Tailscale device first, then
bootstrap, then restore from the Synology, then run `spark doctor` on the Spark. This is verified
by a dated drill.

**What I see.** The same stack, the same names.

**How to override.** None.

*(2026-10-14, Dan's decision: Phases 4 and 5 swapped, the bake-off now 4 and the NAS 5. This page's
`phase` goes from 4 to 5 with NAS and backups, since the restore comes from the Synology.)*

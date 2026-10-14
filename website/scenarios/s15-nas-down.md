---
title: "S15 · NAS down"
scenario-id: S15
phase: 5
status: planned
---

**Situation.** The Synology is unreachable.

**What happens.** Automounts time out instead of hanging. Model loads are unaffected, since
cache drops touch only local filesystems. Jobs that read the NAS fail visibly.

**What I see.** Failed jobs, not a hung box.

**How to override.** None needed.

*(2026-10-14, Dan's decision: Phases 4 and 5 swapped, the bake-off now 4 and the NAS 5. This page's
`phase` goes from 4 to 5 with NAS and backups.)*

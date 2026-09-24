---
title: "S15 · NAS down"
scenario-id: S15
phase: 4
status: planned
---

**Situation.** The Synology is unreachable.

**What happens.** Automounts time out instead of hanging. Model loads are unaffected, since
cache drops touch only local filesystems. Jobs that read the NAS fail visibly.

**What I see.** Failed jobs, not a hung box.

**How to override.** None needed.

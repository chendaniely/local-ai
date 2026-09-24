---
title: "S05 · Memory critically low"
scenario-id: S05
phase: 1
status: planned
---

**Situation.** A job keeps growing, and free memory falls toward the band where this box has
been reported to freeze.

**What happens.** A warning fires at 28 GiB available. At 20 GiB the brake unloads a loading
engine first, then idle models of any class, then the least recently used, and holds them
there. earlyoom is the last resort. Phase 1 ships a minimal brake that unloads the on-demand
coder first, then the always-loaded models.

**What I see.** A high-priority "brake" notification, and a hold shown in `spark status`.

**How to override.** `spark brake --release` once memory is back. Thresholds live in
`stack/models.yaml`.

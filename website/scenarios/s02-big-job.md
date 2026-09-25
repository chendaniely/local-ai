---
title: "S02 · Big job while an agent works"
scenario-id: S02
phase: 2
status: planned
---

**Situation.** pi is mid-task with the coder loaded, and I start a cuDF job that needs about
70 GiB.

**What happens.** `spark make-room 70G` lists what would have to unload, with sizes, and
unloads only what I confirm. pi's next request is refused with a reason, never quietly
swapped.

**What I see.** The list, and headroom on the menu bar.

**How to override.** If I decline, nothing unloads. The brake remains the backstop.

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

*Designed 2026-10-07, for Phase 2a:* `spark make-room 70G` lists everything it could unload, the
always-loaded models included, largest first, and unloads what I confirm; `spark make-room --all`
unloads everything after one confirmation, my clean slate for testing. Nothing it unloads cuts off
a request in flight: it waits for pi's current request to finish first. pi's next request then
waits up to its key's wait, 30 s from the Mac or 10 minutes as `agent`, and if there's still no
room it gets a refusal in the client that says why. The headroom shows in `spark status` until the
menu bar arrives in 2b.

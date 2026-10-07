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

*Revised 2026-10-07, after the design's council:* `spark make-room 70G` frees enough that 70 GiB
are available beyond the reserve and the growth the loaded models are still owed, so the job can
take all of it without reaching the brake. It lists pinned models and those an agent's session
holds too, marked, with each one's requests in flight and how long they've run. Then it **holds
that room for me** until `spark make-room --done`, a duration I give, or the next boot: no reload,
no waiting request (`agent`'s included), no boot preload and no brake release takes it, so pi's
next request waits and is then refused with a reason that names the hold, never quietly loaded into
my job's memory. When the hold ends, the always-loaded models reload one at a time, if they fit,
and the coder waits for a request. `spark status` shows the hold, its size and when it ends.
*(Added after the re-review, the same day:* asked for more than unloading everything could free,
make-room says so, shows the most it can free, and unloads nothing unless I confirm that. An
always-loaded model that doesn't fit when the hold ends waits, loads once it fits, and shows as
waiting in `spark status`.)

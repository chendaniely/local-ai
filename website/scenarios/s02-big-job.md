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

*What I see, worded at the design's UX pass (2026-10-07):* with the always-loaded models and the
coder loaded and nothing else running, 9 GiB is free for a load, so `spark make-room 70G` lists
*the coder 41 GiB, loads when asked* and *Gemma 32 GiB, always loaded*, first, says that unloading
both frees 82 GiB, and asks once. Then: *Unloaded the coder and Gemma. 82 GiB free; 70 GiB held for
you until `spark make-room --done` or a reboot.* pi's next request waits 30 s and then reads: *The
coder didn't load: it needs 41 GiB, and 12 GiB is free after the 24 GiB reserve and the 70 GiB held
for you by make-room. On the Spark, `spark make-room --done` ends the hold; or try again later.*
When I end the hold, a default notification says *make-room's 70 GiB hold ended*, and Gemma
reloads. The plan's *What you see in Phase 2a* has every message.

*Corrected after the design's final re-review, the same day (the session's rulings):*

- **"Available" and "free for a load" are two numbers.** *Available* is the box's free memory;
  *free for a load* is what's left after the reserve, the growth the loaded models are still owed
  and any hold. So `spark make-room 70G` frees until 70 GiB is free for a load (above it said
  "available"), and its message reads *Unloaded the coder and Gemma. 82 GiB is free for a load, and
  70 GiB of it is held for you until `spark make-room --done` or a reboot.*
- **The hold is mine.** My own requests, from pi on the Mac or the web UI, may load into it, and
  it shrinks by what they take from it; only `agent`'s requests and the automatic reloads are kept
  out. So the request that gets refused here is the agent's: it waits its 10 minutes and then
  reads *The coder didn't load: it needs 41 GiB, and 12 GiB is free for a load, after the 24 GiB
  reserve and the 70 GiB make-room holds for Dan. On the Spark, `spark make-room --done` ends the
  hold.* A request of mine would load the coder into the hold, shrinking it, which is my call.

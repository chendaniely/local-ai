---
title: "S02 · Big job while an agent works"
scenario-id: S02
phase: 2
status: planned
---

**Situation.** pi is mid-task with the coder loaded, and I start a cuDF job that needs about
70 GiB.

**What happens.** `spark make-room 70G` lists what would have to unload, with sizes, and unloads
only what I confirm. pi's next request is refused with a reason, never quietly swapped.

**What I see.** The list, and the room it frees and holds for me: in `spark status` from Phase 2a,
and on the menu bar from Phase 2b.

**How to override.** If I decline, nothing unloads. The brake remains the backstop.

**Phase 2a, as designed (2026-10-07): what happens and what I see.** *Free for a load* is the box's
available memory less the 24 GiB reserve, the growth the loaded models are still owed and any hold
that isn't mine. `spark make-room 70G` works out what must unload so that 70 GiB is free for a load,
so my job can take all of it and leave the reserve free, above the brake. It lists everything it
could unload, the always-loaded models included, and pinned models and those an agent's session
holds, each marked, largest first, with each one's requests in flight and how long they've run. It
unloads what I confirm, each once its requests in flight have finished, so nothing is cut off.
Asked for more than unloading everything can free, it says so, shows the most it can free, and
unloads nothing unless I confirm that. `spark make-room --all` unloads everything after one
confirmation, my clean slate for testing.

Then it **holds that room for me** until `spark make-room --done`, a time I give, or the next boot.
No reload, no request of `agent`'s and no brake release takes it, while my own requests, from pi on
the Mac or the web UI, may load into it, and it shrinks by what they take. So the request that gets
refused here is the agent's: it waits its 10 minutes and is then refused with a reason that names
the hold. When the hold ends, the always-loaded models reload one at a time, if they fit, and the
coder waits for a request; an always-loaded model that doesn't fit waits, loads once it fits, and
shows as waiting in `spark status`.

With the always-loaded models and the coder loaded and nothing else running, 9 GiB is free for a
load, so the list puts the coder (41 GiB, loads when asked) and Gemma (32 GiB, always loaded)
first, says that unloading both leaves 82 GiB free for a load, and asks once. The plan words its
answer for 40 GiB as *Unloaded the coder. 50 GiB is free for a load, and 40 GiB of it is held for
you until `spark make-room --done` or a reboot; your own requests can load into it, agent's and
automatic reloads can't.*, and for 70 GiB the same sentence names the coder and Gemma, and 82 GiB.
`agent`'s request, refused before my job starts, reads *The coder didn't load: it needs 41 GiB, and
12 GiB is free for a load (106 GiB available, less the 24 GiB reserve and the 70 GiB make-room holds
for Dan). Using memory now: the embeddings 8 GiB, whisper 3 GiB. The hold ends when Dan runs `spark
make-room --done` on the Spark.* Once my job has taken the room, it reads *The coder didn't load:
it needs 41 GiB, and nothing is free for a load while make-room holds 70 GiB for Dan (36 GiB
available, less the 24 GiB reserve). Using memory now: a process of Dan's, 70 GiB, the embeddings
8 GiB. The hold ends when Dan runs `spark make-room --done` on the Spark.* (both from the [implementation
plan](../design/phase-2a.md)'s words). *(Corrected 2026-10-07, at the implementation plan's Task
6: both ended *On the Spark, `spark make-room --done` ends the hold.*, which didn't say whose step
it is.)* `spark make-room --done` says how much of the hold went
unused and what reloads (*… Reloading Gemma.*), and my phone gets the same as *make-room's hold for
you ended*. The plan's [*What you see in Phase 2a*](../design/plan.md#what-you-see-in-phase-2a) has
every message.

*Rewritten 2026-10-07 as one current account, after the implementation plan's forward-and-back
council. The notes this page gathered while the design moved, and the questions and answers behind
them, are in [Phase 2a — questions and answers](../design/phase-2a-qa.md#how-the-scenario-pages-read-before-the-rewrite).*

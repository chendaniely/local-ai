---
title: "S01 · Morning start"
scenario-id: S01
phase: 2
status: planned
---

**Situation.** It's 9 am. The coder model unloaded overnight, and I start pi.

**What happens.** My first request loads the coder if it fits, which takes tens of seconds with
llama.cpp. A pin I set keeps it loaded for as long as I say, so it doesn't idle-unload under me.

**What I see.** A slower first reply, and word that the coder is loading: on my phone from ~~Phase
2a~~ Phase 5, and on the menu bar from Phase 2b. *(Corrected 2026-10-14, Dan's decision: ntfy
optional, moving with the NAS phase; Phases 4 and 5 swapped, the NAS phase now 5.)*

**How to override.** `spark load coder` or `spark pin coder [duration]`, on the Spark; the menu
bar's Load from Phase 2b.

**Phase 2a, as designed (2026-10-07): what happens and what I see.** The gate's part of this
morning. From 2a the coder is Qwen3.8-27B. It unloads after 60 minutes with no request and no
active session; the always-loaded models never unload for being idle. Pins are mine only, set on
the Spark with no API key; `agent` keeps its model with a session of its own instead. A weekday
preload isn't built: it waits in the plan's Backlog until I ask for it.

pi shows only a slower first reply; nothing is added to its answer. My phone gets two silent
notifications, *Loading the coder for pi on the Mac (24 s last time)…* and then *Loaded the coder
in 24 s.* With the coder not yet loaded, `spark pin coder 8h` says *Loading the coder (24 s last
time)…* as the load starts, then answers *The coder stays loaded until 18:00 (loaded it first,
24 s). `spark unpin coder` ends the pin.* *(Corrected 2026-10-15, at the implementation plan's
Task 30 review: this gave only the pin's sentence, but a pin that loads first says when the load
starts, as plan.md's command table has since Task 10's re-review, and as `spark pin` does.)* When
the pin runs out, a silent *The pin on the coder ended at 18:00; it unloads after 60 min idle.*
arrives. `spark status` lists the coder as *loads when asked* and the others as *always loaded*.
The plan's [*What you see in Phase 2a*](../design/plan.md#what-you-see-in-phase-2a) has every
message.

*(2026-10-14, Dan's decision: ntfy optional, moving with the NAS phase; Phases 4 and 5 swapped, the
NAS phase now 5.)* ntfy is set up in Phase 5, with the NAS, not in 2a, so my phone gets none of
the notifications above until then: 2a's stack runs without it, and `spark status` says *ntfy not
set up*. Until then the loading shows in `spark status` on the Spark, and from 2b on the menu bar.
2a's drill checks the gate's part without the phone; Phase 5 checks the phone's.

After 2a this page stays `planned`, with a dated note recording its gate part, until 2b's menu bar
completes it.

*Rewritten 2026-10-07 as one current account, after the implementation plan's forward-and-back
council. The notes this page gathered while the design moved, and the questions and answers behind
them, are in [Phase 2a — questions and answers](../design/phase-2a-qa.md#how-the-scenario-pages-read-before-the-rewrite).*

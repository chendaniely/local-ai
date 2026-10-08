---
title: "S01 · Morning start"
scenario-id: S01
phase: 2
status: planned
---

**Situation.** It's 9 am. The coder model unloaded overnight, and I start pi.

**What happens.** My first request loads the coder if it fits, which takes tens of seconds with
llama.cpp. A pin I set keeps it loaded for as long as I say, so it doesn't idle-unload under me.

**What I see.** A slower first reply, and word that the coder is loading: on my phone from Phase
2a, and on the menu bar from Phase 2b.

**How to override.** `spark load coder` or `spark pin coder [duration]`, on the Spark; the menu
bar's Load from Phase 2b.

**Phase 2a, as designed (2026-10-07): what happens and what I see.** The gate's part of this
morning. From 2a the coder is Qwen3.8-27B. It unloads after 60 minutes with no request and no
active session; the always-loaded models never unload for being idle. Pins are mine only, set on
the Spark with no API key; `agent` keeps its model with a session of its own instead. A weekday
preload isn't built: it waits in the plan's Backlog until I ask for it.

pi shows only a slower first reply; nothing is added to its answer. My phone gets two silent
notifications, *Loading the coder for pi on the Mac (24 s last time)…* and then *Loaded the coder
in 24 s.* With the coder not yet loaded, `spark pin coder 8h` answers *The coder stays loaded until
18:00 (loaded it first, 24 s). `spark unpin coder` ends the pin.*, and when the pin runs out, a
silent *The pin on the coder ended at 18:00; it unloads after 60 min idle.* `spark status` lists
the coder as *loads when asked* and the others as *always loaded*. The plan's [*What you see in
Phase 2a*](../design/plan.md#what-you-see-in-phase-2a) has every message.

After 2a this page stays `planned`, with a dated note recording its gate part, until 2b's menu bar
completes it.

*Rewritten 2026-10-07 as one current account, after the implementation plan's forward-and-back
council. The notes this page gathered while the design moved, and the questions and answers behind
them, are in [Phase 2a — questions and answers](../design/phase-2a-qa.md#how-the-scenario-pages-read-before-the-rewrite).*

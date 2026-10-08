---
title: "S05 · Memory critically low"
scenario-id: S05
phase: 1
status: built
---

**Situation.** A job keeps growing, and free memory falls toward the band where this box has
been reported to freeze.

**What happens.** A warning fires at 28 GiB available. At 20 GiB the brake unloads a loading
engine first, then idle models of any class, then the least recently used, and holds them
there. earlyoom is the last resort. Phase 1 ships a minimal brake that unloads the on-demand
coder first, then the always-loaded models.

**What I see.** A high-priority "brake" notification, and a hold shown in `spark status`.
*(Added 2026-09-28: that line of `make status` also says whether llama-swap took the brake's own
key when the brake started, and `make doctor`'s `stack units` line fails if it didn't. A brake
whose key llama-swap refuses can unload nothing.)*

*Until Phase 2 (added 2026-09-28, from Phase 1's council):* nothing notifies me. The warning at
28 GiB is a line in the brake's journal (`make logs s=brake`), and the hold shows on `make status`'s
`brake` line, on the Spark. earlyoom, the last resort, chooses among the engines by their RSS,
which leaves out the models' GPU memory, and its dry run in Phase 1's Task 13, with every engine at the same
`oom_score_adj`, picked Gemma, a resident, before the on-demand coder. *(Corrected 2026-09-28, from
Phase 1's council: `spark launch` now gives a resident engine `oom_score_adj` 900 and an on-demand
one 1000, so earlyoom picks the on-demand coder first, as the brake does. Checked after that day's
deploy: earlyoom's dry run picked the coder's engine.)*

**How to override.** `spark brake --release` once memory is back. *(Corrected 2026-09-26:
`make brake-release`, on the Spark, in its clone, from an account in `spark-admin` — `spark` isn't
on my PATH, and the target runs it through uv.)*
Thresholds live in `stack/models.yaml`.

*Status: built, 2026-09-28 (Phase 1). Phase 1's minimal brake unloads the on-demand models first:
Phase 1's Task 16 drill, at raised thresholds, held new loads and unloaded the coder while the residents
stayed. The idle-first order and the notifications arrive in Phase 2.*

**Phase 2a, as designed (2026-10-07): what happens and what I see.** The brake stays a unit of its
own, as today, so it keeps running while the gate is down. Its order is a loading engine first,
then idle models of any class, the longest unused first, then the least recently used, read from
the record the gate writes every second, with Phase 1's order, on-demand first, as its fallback
when that record is missing or stale. It also acts early when memory falls faster than the loads
the gate admitted explain. Each step notifies, and while its hold stands, a request waits for its
key's wait and is then refused with `held_by_brake`. The brake is the only thing that may cut off
a request in flight.

The gate lifts the hold by itself once memory has stayed above 28 GiB available for 5 minutes,
and only if what it would reload fits, at most once an hour: a brake within the hour after an
automatic release, or a hold found after a reboot, likely left by a freeze, waits for me. The
always-loaded models then reload one at a time; on-demand models wait for a request. The model
that was loading when the brake fired doesn't reload by itself: my own request, from pi on the Mac
or the web UI, or `spark load`, loads it if it fits, while `agent`'s requests for it wait, then are
refused with `footprint_suspect`, until I have loaded it once. While the gate is down, the brake
sends its own alert, and the gate, once back, doesn't send it again. `make brake-release` stays.
Admission keeps 24 GiB free.

In the plan's words: a silent *Memory is getting low on brightroar: 27.4 GiB available, under the
28 GiB warning line. The brake acts at 20.*; then a high-priority *Brake on brightroar at 03:12:
19.6 GiB available, under the 20 GiB line. Unloaded the coder, which was loading; new loads are
paused. They resume by themselves after 5 min above 28 GiB available.*, and for a further unload,
*Brake, 03:13: also unloaded Gemma and the embeddings, both idle.* A request meanwhile reads *Not
loading the coder now: memory ran low at 03:12 (19.6 GiB available), and new loads are paused.
They resume by themselves after 5 minutes above 28 GiB available, if what would reload fits; on
the Spark, `make brake-release` resumes them now.*, and `spark status` shows *paused*. On release,
at default priority: *Brake released at 03:40, 64 GiB available. Reloaded Gemma and the
embeddings. The coder was loading when it fired, so it loads again only when you ask.* A hold that
waits for me sends, at high priority, *After the reboot, new loads are still paused from the brake
at 02:58. On the Spark, `make brake-release` resumes them.* `agent`'s request for the coder
afterwards reads *Not loading the coder for agent: it was loading when the brake fired at 03:12, so
only Dan can load it again: `spark load coder` on the Spark, or one of Dan's requests from pi on
the Mac or the web UI, which loads it if it fits.* *(corrected 2026-10-07, at the implementation
plan's Task 6: it read *a request of his*)*, and my phone gets *Didn't load the coder for agent: it
was loading when the brake fired at 03:12. On the Spark, `spark load coder` allows it again; your
own requests load it if it fits.* The brake's own alert, sent while the gate is down, ends *They
resume once the gate is back and memory has stayed above 28 GiB available for 5 min.* The plan's
[*What you see in Phase 2a*](../design/plan.md#what-you-see-in-phase-2a) has every message.

*Rewritten 2026-10-07 as one current account, after the implementation plan's forward-and-back
council. The notes this page gathered while the design moved, and the questions and answers behind
them, are in [Phase 2a — questions and answers](../design/phase-2a-qa.md#how-the-scenario-pages-read-before-the-rewrite).*

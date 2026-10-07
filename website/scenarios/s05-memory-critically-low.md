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
which leaves out the models' GPU memory, and its dry run in Task 13, with every engine at the same
`oom_score_adj`, picked Gemma, a resident, before the on-demand coder. *(Corrected 2026-09-28, from
Phase 1's council: `spark launch` now gives a resident engine `oom_score_adj` 900 and an on-demand
one 1000, so earlyoom picks the on-demand coder first, as the brake does. Checked after that day's
deploy: earlyoom's dry run picked the coder's engine.)*

**How to override.** `spark brake --release` once memory is back. *(Corrected 2026-09-26:
`make brake-release`, on the Spark, in its clone, from an account in `spark-admin` — `spark` isn't
on my PATH, and the target runs it through uv.)*
Thresholds live in `stack/models.yaml`.

*Status: built, 2026-09-28 (Phase 1). Phase 1's minimal brake unloads the on-demand models first:
Task 16's drill, at raised thresholds, held new loads and unloaded the coder while the residents
stayed. The idle-first order and the notifications arrive in Phase 2.*

*Designed 2026-10-07, for Phase 2a:* the brake moves into the gate, with the same thresholds and
order, and a high-priority notification when it fires. It releases by itself once memory has
stayed above 28 GiB for 5 minutes, and a default-priority notification says when it fired and when
it released. The gate then reloads the always-loaded models one at a time, as at boot, and names
them; on-demand models wait for a request. While it holds, a request waits for its key's wait and
is then refused with `held_by_brake`. The brake is the only thing that may cut off a request in
flight. `make brake-release` stays.

*Revised 2026-10-07, after the design's council:* the brake stays a unit of its own, as today, so
it keeps running while the gate is down. "The same order" above means the plan's: a loading engine,
then idle models of any class, then the least recently used, with Phase 1's order, on-demand first,
as its fallback when it can't read which models are idle. It also acts early when memory falls
fast. The gate lifts the hold, with limits:

- only once memory has stayed above 28 GiB for 5 minutes, and only if what it would reload fits;
- at most once an hour: a brake within the hour after an automatic release holds until I release
  it, and its alert says so;
- a hold found after a reboot, likely left by a freeze, waits for me, with a high-priority alert;
- the model that was loading when the brake fired doesn't reload by itself: requests for it are
  refused with `held_by_brake` until I load it again.

Admission keeps 24 GiB free, not 22: the reserve went back to 24 the same day.

*Corrected after the design's re-review, the same day:*

- **The model that was loading** still doesn't reload by itself, but my own request, from pi on the
  Mac or the web UI, loads it as normal if it fits (my choice), as does `spark load`. `agent`'s
  requests for it wait, then get `footprint_suspect`, naming `spark load <model>`, until I have
  loaded it once; no `held_by_brake` without a hold.
- **While the gate is down,** the brake sends its own high-priority "brake fired" to ntfy, by the
  failure notifier's independent path, so I hear about the brake, not only that the gate is down.

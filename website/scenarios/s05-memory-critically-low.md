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
*(Added 2026-09-28: that line of `make status` also says whether llama-swap took the brake's own
key when the brake started, and `make doctor`'s `stack units` line fails if it didn't. A brake
whose key llama-swap refuses can unload nothing.)*

*Until Phase 2 (added 2026-09-28, from Phase 1's council):* nothing notifies me. The warning at
28 GiB is a line in the brake's journal (`make logs s=brake`), and the hold shows on `make status`'s
`brake` line, on the Spark. earlyoom, the last resort, doesn't follow the brake's order: it chooses
among the engines by their RSS, which leaves out the models' GPU memory, and its dry run in Task 13
picked Gemma, a resident, before the on-demand coder.

**How to override.** `spark brake --release` once memory is back. *(Corrected 2026-09-26:
`make brake-release`, on the Spark, in its clone, from an account in `spark-admin` — `spark` isn't
on my PATH, and the target runs it through uv.)*
Thresholds live in `stack/models.yaml`.

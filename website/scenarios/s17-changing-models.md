---
title: "S17 · Changing models mid-task"
scenario-id: S17
phase: 2
status: planned
---

**Situation.** I edit `stack/models.yaml` while pi is mid-task.

**What happens.** `spark apply` shows the diff and waits until models are idle, because a
llama-swap reload stops every engine. It only stops everything sooner if I confirm that.

**What I see.** The diff, and the wait.

**How to override.** Confirm to apply now.

*Until Phase 2 (added 2026-09-26, during Phase 1):* Phase 1's `make apply`, on the Spark, neither
shows the diff nor waits. It lists the files that change, and while models are loaded, or it can't
tell, it refuses: it names the loaded models, changes nothing and exits 1. I run it again once
they're idle, or run `make apply-now` to restart llama-swap anyway. A model that starts loading
after apply's first look puts off only llama-swap's restart: the files are deployed, apply says so
and exits 1, and the next `make apply` makes the restart once the models are idle.

*Designed 2026-10-07, for Phase 2a, correcting the wait above:* `make apply` shows the diff of what
it would change. When the change needs llama-swap restarted, it waits until no request has been in
flight for about 60 s on any engine, not until the models are idle: requests keep being served, and
it shows what it's waiting on. Then it restarts llama-swap; the gate reloads the always-loaded
models one at a time, and on-demand ones reload on their next request. Ctrl-C leaves nothing
changed, and `make apply-now` restarts at once, after asking me to confirm. A change that needs no
llama-swap restart applies at once.

*Revised 2026-10-07, after the design's council:*

- **The front waits too.** A change to the front's own code restarts it only through the same
  quiet moment, since its restart would cut off every request in flight; a change elsewhere in the
  app doesn't restart it at all. The gate and the brake restart at once, which cuts nothing off.
- **The wait has a deadline.** After 15 minutes without a quiet minute, apply offers "drain now",
  which holds new requests and lets those in flight finish, and then `make apply-now`.
- **Nothing is written until then,** so Ctrl-C leaves nothing changed.
- **During the restart,** a request waits for its key's wait, and is refused with `restarting` if
  llama-swap isn't back by then.
- **The coder swap** runs apply, whose restart stops every engine, the old coder included; then the
  pull of the new coder, during which a request for it is refused with `not_downloaded`; then
  `make clients`.

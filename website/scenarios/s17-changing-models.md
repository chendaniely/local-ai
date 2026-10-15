---
title: "S17 · Changing models mid-task"
scenario-id: S17
phase: 2
status: planned
---

**Situation.** I edit `stack/models.yaml` while pi is mid-task.

**What happens.** `make apply` shows the diff and waits for a quiet moment, because a llama-swap
restart stops every engine. It only stops everything sooner if I confirm that.

**What I see.** The diff, and the wait.

**How to override.** Confirm to apply now (`make apply-now`).

*Until Phase 2 (added 2026-09-26, during Phase 1):* Phase 1's `make apply`, on the Spark, neither
shows the diff nor waits. It lists the files that change, and while models are loaded, or it can't
tell, it refuses: it names the loaded models, changes nothing and exits 1. I run it again once
they're idle, or run `make apply-now` to restart llama-swap anyway. A model that starts loading
after apply's first look puts off only llama-swap's restart: the files are deployed, apply says so
and exits 1, and the next `make apply` makes the restart once the models are idle.

**Phase 2a, as designed (2026-10-07): what happens and what I see.** `make apply` shows the diff of
what it would change. A change that needs llama-swap restarted waits until no request has been in
flight for about 60 s on any engine, by the front's counts, not until the models are idle: requests
keep being served, and it says what it is waiting on. After 15 minutes without a quiet minute, it
offers "drain now", which holds new requests and lets those in flight finish; `make apply-now`
restarts at once, after asking. Nothing is written until then, so Ctrl-C leaves nothing changed. A
change that needs no llama-swap restart applies at once.

The restarts then run in a fixed order: the brake, the gate, the front only if its own code
changed, and llama-swap last. *(Added 2026-10-15, at Task 31's review: the front restarts too
when the app's dependencies or its Python version change, when the registry's key groups
change, since it reads them only when it starts, and for `make restart-front`, after a client
key is added or retired.)* A renamed or added model reaches the front through the gate, without
restarting it. New requests meanwhile are held, through the gate's own restart too, and go through
once llama-swap answers again, or are refused with `restarting` when their key's wait runs out. The
gate then reloads the always-loaded models one at a time, and on-demand ones reload on their next
request. The coder swap runs apply, whose restart stops every engine, the old coder included; then
the pull of the new coder, during which a request for it is refused with `not_downloaded`; then
`make clients`.

In the plan's words: the diff, then *Waiting for a quiet moment: the coder answered 20 s ago, and it
needs 60 s with nothing in flight. Ctrl-C leaves everything as it was; `make apply-now` restarts
now.* After 15 minutes: *No quiet minute in 15 minutes. Drain now, holding new requests while the 2
in flight finish? [y/N]* `make apply-now` asks *This restarts the model service now and cuts off
the 2 requests in flight (pi on the Mac, agent). Continue? [y/N]* After the restart, a
default-priority *make apply restarted the model service at 14:02. Gemma, the embeddings and whisper
reloaded; the coder loads on its next request.* A request held through the restart whose wait runs
out reads *The model service on the Spark is restarting for a configuration change, and your 30 s
ran out. Try again in a minute.* During the swap, a request for the new coder reads *The coder
isn't downloaded yet. On the Spark, `make pull` fetches it (16 GiB).*, and one for the old name
*There's no model called qwen3.6-35b-a3b here. The models are the coder (qwen3.8-27b), Gemma
(gemma-4-26b-a4b), the embeddings (qwen3-embedding-0.6b) and whisper (whisper-large-v3-turbo). On
the Mac, `make clients` updates pi's list.* The plan's [*What you see in Phase
2a*](../design/plan.md#what-you-see-in-phase-2a) has every message.

*(2026-10-15, Phase 2a's Task 32: built, not yet deployed or verified; Task 50's drill checks it on
the box.)* `make apply` asks the gate what is in flight, never llama-swap, and says what it waits
on each time that changes. It adds two lines of its own: one when the gate restarted and doesn't
have the front's counts yet, and one when no request has come since the front started. Ctrl-C, or
a dropped SSH session, changes nothing while it waits. "Drain now" holds new requests and waits,
however long, for the requests in flight, naming whose they are. Even after a quiet minute, apply
waits for a request that arrived just before the hold, rather than cut it off. From then until
the restarts are done, every new request is held. Then apply syncs the app, writes the files and
restarts the services, in the order above. Just before each restart of the gate, the front or
llama-swap, it checks with the gate that the hold still stands. If it doesn't, apply restarts
nothing more. It waits up to 30 s for the gate to answer after its
own restart, and stops there if it doesn't. It waits up to 30 s more for the gate to see llama-swap
answer again. Then the gate releases the held requests.

`make apply-now` asks only when it would cut a request off, and holds new requests through the
restart all the same. While the gate isn't answering and llama-swap runs, `make apply` changes
nothing, since it can't see what's in flight; `make apply-now` restarts anyway. `make
restart-front` waits in the same way.
Its words never offer `make apply-now`, which wouldn't restart the front. For the drill, `spark
apply --deadline-s 60` shortens the 15 minutes, and `--help` doesn't list that option. `make
doctor`'s `S17` line checks that the gate answers what the wait reads.

*Rewritten 2026-10-07 as one current account, after the implementation plan's forward-and-back
council. The notes this page gathered while the design moved, and the questions and answers behind
them, are in [Phase 2a — questions and answers](../design/phase-2a-qa.md#how-the-scenario-pages-read-before-the-rewrite).*

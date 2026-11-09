---
title: "S26 · What it cost"
scenario-id: S26
phase: 2
status: planned
---

**Situation.** I have used the Spark for a week from my harnesses, Open WebUI and `agent`, and I
want to know what that work would have cost on Claude, and how far the box is from paying for
itself. Before I trust a number, I want to know the counter is on, and that it keeps nothing it
shouldn't.

**What happens.** The front writes one row of numbers for each request that passes through it, into
this month's `usage-YYYY-MM.jsonl` (the month by UTC) in `/var/lib/local-ai/usage`. The gate writes
its own ledger of loads, unloads, refusals, brake firings and starts into
`/var/lib/local-ai/gate-ledger`. A usage row holds counts, times, the registry's own model name and
the name of the key the request came with, so the totals split by key, and so by harness; it never
holds a prompt, an answer, a file name, a title or any string a client chose. The ledger holds no
key's name: a load or a refusal it records names the request's id, which joins it to that request's
usage row. *(Corrected 2026-10-23, before the meter's deploy: this said a row never holds a key
name, which is true only of the ledger's rows.)*
Both folders are mine to read and no one else's: owned by the front or the gate, group `spark-admin`,
mode `2750` with setgid, and every file `0640`, so `agent`, the engines and a guest can't read them.
Counting never gets in a request's way: if a row can't be written, the request goes through and the
front says counting is off, from when. Totals may lag by minutes. (The design is
[the usage meter](../design/usage-meter.md); this page is what I see of it.)

**What I see.** On the Spark, `make doctor` has three lines for it. *(Built 2026-10-23, Task 44g of
the meter's counter; not yet deployed, so these lines first run at its deploy.)*

- `usage records` and `gate ledger` pass when each folder is as bootstrap makes it: the right owner
  and group, mode `2750` with the setgid bit, no file in it readable by others, and no link. They
  fail naming `make bootstrap` for a folder that is missing or wrongly set, and *log in again* when
  my account is refused the listing, as it is before a login into `spark-admin`: a refused listing
  never passes as an empty folder.
- `counting` passes when this month's usage file holds a row from doctor's own request (the
  end-to-end check's, counted; doctor prints nothing of the row), the gate's ledger has this month's
  file, and the gate's health shows neither the front's counting nor the ledger's off. A gate older
  than the meter says nothing of counting, which doctor reports as *unknown*, a failure: nothing says
  it is on.

On the Spark, `make status` adds a line under *health* for each side that has stopped, *counting off
since 08:52 (the front's usage rows)*, or *(the gate's ledger)*, on the control socket's view only.
It says nothing while both are on.

With `--full`, the privacy canary sends its string as a message, a speech-to-text prompt and a file
name, then looks for it in the journal, the gate's state, its refusal history, launch's records, and
this month's usage and ledger files. A second string goes where a client chooses one that a row
could keep: as a model name (the front answers 404, and the name must not reach a row) and as a
reasoning effort. Doctor looks for that one only in this month's usage and ledger files: the gate's
refusal history keeps the name a client asked for, in the front's words, so it would always be found
there. The canary reports only where a string was found or that neither was, never what the row
holds.

**How to override.** Nothing to override: counting has no switch, and a failure to count is shown,
never fixed by dropping the request. If a folder fails, **On the Spark:** run `make bootstrap`, which
makes both with their owners, groups and modes. If a listing is refused, log in again, so the
`spark-admin` group applies. If `counting` says the front's rows stopped, **On the Spark:** run
`make status` for since when, and `make logs s=front` for why (below 20 GiB free on the disk,
counting turns itself off rather than take the room the weights need). If the canary finds either
string in either folder, stop: something is writing what a client sent, and nothing private goes
through the Spark until it is found.

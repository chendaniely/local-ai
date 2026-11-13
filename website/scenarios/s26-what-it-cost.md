---
title: "S26 · What it cost"
scenario-id: S26
phase: 2
status: built
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
[the usage meter](../design/usage-meter.md), and [its first numbers](../design/usage-meter-now.md);
this page is what I see of it.)

*(2026-11-12: the summary of what it would have cost at hosted prices is built on the `swiftbar-costs` branch, ~~not
yet deployed: it deploys with Task 44l~~ deployed with Task 44l the same day. The fast coder and Flash-Next, which the front can't see, are counted from the
Mac's OpenCode: `spark usage harvest` reads their rows there, and `spark usage import` merges them into
`/var/lib/local-ai/usage-sessions`, a folder of Dan's, group `spark-admin`, mode `2750`, whose only writer is that
import. No row holds a dollar: prices are applied when the summary reads, from the price files in the `spark` project,
so a corrected price or a remapped model reprices the past.)*

**What I see.** On the Spark, `make doctor` has three lines for the counter. *(Built 2026-10-23, Task 44g of
the meter's counter; ~~not yet deployed, so these lines first run at its deploy~~ deployed 2026-11-12, Task 44l, when they
first ran and passed. Two more are built 2026-11-12, below.)*

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

Two more doctor lines come with the first numbers *(built 2026-11-12, ~~not yet deployed~~ deployed the same day with
Task 44l, and passing)*:

- `usage sessions` passes when `/var/lib/local-ai/usage-sessions` is as bootstrap makes it, by the same checks as the
  other two folders, with Dan as its owner.
- `meter prices` passes when the price files load, every model of the deployed registry has a line in the mapping, and
  a summary over the live folders runs. It fails naming the models with no line (add one, or `none` for a model left
  unpriced on purpose; the line also suggests the proxy a model's size points to, and `make prices` lists every model's proxy and price *(2026-11-13, built on the `meter-every-model` branch, ~~not yet deployed~~ deployed the same day with its Task E6)*), or only the error's class when a file or the summary fails; it never prints a figure, a row
  or a private value. An absent private file passes (the share shows as a dash); a malformed or unreadable one fails the check, naming the error's class and never the file's contents.

On the Spark, `make usage` prints the summary: a line each for this month, today, last month and all time, with what
the models would have cost at hosted prices, the tokens in and out and, when there are any, how much of it rests on
an estimated cache or audio length, the audio minutes, and how many rows couldn't be priced; all time also as a share of the Spark's price. Under them,
only when one is set, a line of this month's flags in the menu's words, joined by `·`: `⚠map` and the models with no
mapping line, then the interrupted rows, the errors, the rows left unpriced, the unknown cache counts and the rows the
front dropped. Then a line for each span with counting off that ends this month or is still open, with its date:
`counting off 2026-11-02 13:58–14:01`, or `counting off since` and when, while it is still off. *(2026-11-12, at the
final review: until then `make usage` printed only the four periods' lines, though this page already named the
flags.)* On the Mac, `make usage-push` harvests OpenCode's rows for the by-hand engines and sends them to the Spark by
hand; spark-menu does the same by itself every 5 minutes. When the Spark refuses a row, its import stores the rest,
says how many it refused in its verdict line and exits 3, so `make usage-push` fails and spark-menu keeps its place to
send them again, with a note in the dropdown; and when no assistant message in OpenCode's database holds one of three
paths, `$.model.providerID`, `$.time.completed` or `$.tokens.input`, the harvest refuses, naming the path, rather than
send nothing without a word; a message with a token count missing, or a row the importer's own check would refuse, is
left out and counted in the harvest's `skipped` figure instead *(both 2026-11-12, at the final review)*.
The fast coder's cache is estimated when the summary reads, since its engine reports none, and the summary shows how
much of each total rests on that estimate. *(Built 2026-11-12, ~~not yet deployed~~ deployed the same day.)*
*(2026-11-12: speech is priced by the audio minute (Whisper) or by the token (Qwen3-ASR), and embeddings by the token,
each at a hosted proxy's price; a period's line adds its audio minutes, and an estimated length or cache is marked `~`
in the share that rests on it. Built on the `meter-every-model` branch, ~~not yet deployed~~ deployed 2026-11-13 with its Task E6. A Qwen3-ASR row with no counts, written before the front counted audio or answered without a usage block, has nothing to price it by and is among the unpriced rows.)*

In spark-menu's dropdown on the Mac, a section, *Spark usage at Claude prices*, shows the same numbers a minute or so
behind: this month with its tokens, today, last month, all time as a share of the Spark's price before electricity, the
flags, and a by-model list. It prints the summary's figures as given and prices nothing; when the summary is more than
3 minutes old it greys and says how old, and it is never blanked. The menu bar's title stays the dot. *(Built next, in
Dan's dotfiles, 2026-11-12; ~~not yet deployed: Task U14, on the Mac~~ deployed the same night, Task U14: merged and reloaded on the Mac. spark-menu never contacts the Spark while it is off.)* *(2026-11-13: spark-menu's change, ~~on its unmerged `spark-menu-by-model` branch in the dotfiles and not yet deployed~~ merged from its `spark-menu-by-model` branch into the dotfiles' main and reloaded on the Mac the same morning, renames the section *Spark usage at hosted prices*, leaves out of its by-model list an entry that did no work, and shows a speech model's minutes of audio, since the summary now prices speech and embeddings at other vendors' prices.)*

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
holds. *(2026-11-12, built on the `meter-every-model` branch, ~~not yet deployed~~ deployed 2026-11-13 with its Task E6, and passing: `--full`'s 170 MB upload also checks
that its row has the recording's length, within 1 % of the file's own, which the front learns by asking Whisper for
`verbose_json`; it prints only that verdict.)*

**How to override.** Nothing to override: counting has no switch, and a failure to count is shown,
never fixed by dropping the request. If a folder fails, **On the Spark:** run `make bootstrap`, which
makes both with their owners, groups and modes. If a listing is refused, log in again, so the
`spark-admin` group applies. If `counting` says the front's rows stopped, **On the Spark:** run
`make status` for since when, and `make logs s=front` for why (below 20 GiB free on the disk,
counting turns itself off rather than take the room the weights need). If `meter prices` fails naming a
model, **On the Spark:** add its line to `spark/src/spark/meter/mapping.yaml` and deploy; a price changes by a new dated
line in `prices.yaml`, and no deploy of either restarts the front or llama-swap. *(2026-11-12: either change also
updates the table on [Model prices](../reference/model-prices.md), which lists every model's proxy; `make test` fails
until the two match.)* If `usage sessions` fails, run `make
bootstrap`. The private file, `meter.yaml` in `~/.config/local-ai/` on the Spark, holds the Spark's price and my time
zone; without it the summary uses the Spark's own zone and shows no share. If the canary finds either
string in either folder, stop: something is writing what a client sent, and nothing private goes
through the Spark until it is found.

---
title: "Model prices"
description: "Every model the Spark serves, the hosted model its tokens are priced as, and that model's price: what the usage meter's spend is worked out from."
---

*(Added 2026-11-12.)* The usage meter prices every model the Spark serves as a hosted model it could
stand in for, a proxy, so the spend in spark-menu and `make usage` says what the same work would
have cost there. Nothing stored holds a dollar: the meter keeps tokens and prices them each time it
reads them, from two files in this repo, `spark/src/spark/meter/mapping.yaml` (each model's proxy)
and `prices.yaml` beside it (each proxy's dated prices). So a change to either reprices the past.

The chat models are priced by size (Dan's decision, 2026-11-12, in
[the meter's decisions](../design/usage-meter-qa.md#every-model-priced-by-a-proxy-chosen-by-size-2026-11-12)):
the coder, the fast coder and Flash-Next as Sonnet, and Qwen3.6 and Gemma, with 3–4B parameters
active per token, as Haiku. Until then every chat model was priced as Opus.

## Every model

US dollars per million tokens, at each proxy's newest price. A rate after `;` is for a long prompt:
a request whose prompt is over that many tokens is priced at those rates throughout. *Since* is the
day that price began; the meter prices each request at the price of its own day.

<!-- the table: spark.meter.prices.table -->
| Model | Priced as | Since | Fresh input | Cached input | Output |
|---|---|---|---|---|---|
| `gemma-4-26b-a4b` | Haiku 5.5 | 2026-10-07 | $0.10; $0.50 above 100,000 | $0.01; $0.05 above 100,000 | $0.50; $2.50 above 100,000 |
| `qwen3.8-27b` | Sonnet 5.5 | 2026-10-07 | $2.00 | $0.10 | $10.00 |
| `qwen3.6-35b-a3b` | Haiku 5.5 | 2026-10-07 | $0.10; $0.50 above 100,000 | $0.01; $0.05 above 100,000 | $0.50; $2.50 above 100,000 |
| `qwen3.8-27b-sglang` | Sonnet 5.5 | 2026-10-07 | $2.00 | $0.10 | $10.00 |
| `qwen3.8-flash-next` | Sonnet 5.5 | 2026-10-07 | $2.00 | $0.10 | $10.00 |
| `qwen3.8-flash-next-mtp` | Sonnet 5.5 | 2026-10-07 | $2.00 | $0.10 | $10.00 |
| `qwen3-embedding-0.6b` | not priced yet | — | — | — | — |
| `whisper-large-v3-turbo` | not priced yet | — | — | — | — |
| `whisper-large-v3` | not priced yet | — | — | — | — |
| `qwen3-asr-1.7b-32k` | not priced yet | — | — | — | — |
| `qwen3-asr-1.7b-64k` | not priced yet | — | — | — | — |
| `qwen3-asr-0.6b-32k` | not priced yet | — | — | — | — |
| `qwen3-asr-0.6b-64k` | not priced yet | — | — | — | — |
<!-- end of the table -->

The fast coder (`qwen3.8-27b-sglang`) and Flash-Next (`qwen3.8-flash-next`, and `-mtp` with its
speed-up) run by hand, outside the registry, so their tokens are read from OpenCode on the Mac. The
fast coder reports no cache, so the meter estimates how much of each prompt was cached, and the menu
marks that share `~` ([the first numbers' design](../design/usage-meter-now.md)).

## Speech and embeddings

Anthropic sells neither, so their proxies come from other vendors (Dan's decision, 2026-11-12:
"we're just looking for a reasonable proxy for pricing"). They are chosen, not priced yet:

| Models | Proxy | Priced by |
|---|---|---|
| `whisper-large-v3`, `whisper-large-v3-turbo` and the four `qwen3-asr` models | OpenAI's `whisper-1`, for transcription and translation alike | the audio minute |
| `qwen3-embedding-0.6b` | Voyage's lite embeddings model | the million tokens |

Speech waits on the front, whose rows keep each upload's size but not its length in minutes. The
plan for both comes next, with their prices read from each vendor's own page; until it lands, the
table above says *not priced yet* for them.

## Adding a model

A model in `stack/models.yaml` with no line in `mapping.yaml` fails `make test`, and with it CI, and
on the Spark `make doctor`'s `meter prices` check, so a model can't go in unpriced by mistake. Give
it its line in the same change, `tier: none` if it stays unpriced on purpose. A model run by hand,
outside the registry, needs its line too, for the rows read from the Mac.

Then put the table above back in step: `make test` fails until it matches. **On the Mac or the
Spark,** in the clone, this prints the table to paste between its two markers in this page:

```bash
uv run --frozen --project spark python -c 'from spark.meter.prices import load, table; print(table(load()))'
```

Planned with the speech and embedding prices: the failure suggests the line by size, which Dan
accepts or changes, and a `make` target prints the table.

## Changing a proxy or a price

A remap is an edit to a model's line in `mapping.yaml`, and it reprices the past. A price change is
a new dated line in `prices.yaml`, so the past keeps the price of its day. Either goes in by a
commit on the phase branch, with this page's table. `make usage` in the Spark's clone reads the
clone's files, so it shows the change once the clone has it. spark-menu reads the deployed copy, so
it shows the change after a deploy, **on the Spark:** `make apply`. During Phase 2a's soak that
deploy waits for the soak's end.

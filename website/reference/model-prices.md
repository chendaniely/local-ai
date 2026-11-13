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
| `qwen3-embedding-0.6b` | Voyage voyage-4-lite | 2026-09-01 | $0.02 | $0.02 | $0.00 |
| `whisper-large-v3-turbo` | OpenAI whisper-1 | 2026-09-01 | $0.006 per audio minute | — | — |
| `whisper-large-v3` | OpenAI whisper-1 | 2026-09-01 | $0.006 per audio minute | — | — |
| `qwen3-asr-1.7b-32k` | OpenAI gpt-4o-mini-transcribe | 2026-09-01 | $1.25 | $1.25 | $5.00 |
| `qwen3-asr-1.7b-64k` | OpenAI gpt-4o-mini-transcribe | 2026-09-01 | $1.25 | $1.25 | $5.00 |
| `qwen3-asr-0.6b-32k` | OpenAI gpt-4o-mini-transcribe | 2026-09-01 | $1.25 | $1.25 | $5.00 |
| `qwen3-asr-0.6b-64k` | OpenAI gpt-4o-mini-transcribe | 2026-09-01 | $1.25 | $1.25 | $5.00 |
<!-- end of the table -->

The fast coder (`qwen3.8-27b-sglang`) and Flash-Next (`qwen3.8-flash-next`, and `-mtp` with its
speed-up) run by hand, outside the registry, so their tokens are read from OpenCode on the Mac. The
fast coder reports no cache, so the meter estimates how much of each prompt was cached, and the menu
marks that share `~` ([the first numbers' design](../design/usage-meter-now.md)).

## Speech and embeddings

Anthropic sells neither, so their proxies come from other vendors (Dan's decision, 2026-11-12:
"we're just looking for a reasonable proxy for pricing"). They are priced now, from each vendor's
own page, read on 2026-11-12 (developers.openai.com/api/docs/pricing and
docs.voyageai.com/docs/pricing), each line from 2026-09-01:

| Models | Proxy | Price |
|---|---|---|
| `whisper-large-v3`, `whisper-large-v3-turbo` | OpenAI `whisper-1` | $0.006 per audio minute |
| the four `qwen3-asr` models | OpenAI `gpt-4o-mini-transcribe` | $1.25 per million tokens in, $5.00 out |
| `qwen3-embedding-0.6b` | Voyage `voyage-4-lite` | $0.02 per million tokens |

Embeddings and the Qwen3-ASR models are priced by the token, and they report no cache count, which
doesn't matter: their proxy's cached rate is its fresh rate. Whisper is priced by the audio minute,
from the length the front reports for each recording (once the front does, a later change). Until
then, or whenever a row has none, the length is estimated from the upload's size at 16,000 bytes a
second and marked estimated; a row shows as unpriced (`no_audio_length`) only when neither is known
([the design](../design/usage-meter-every-model.md)). `make speech`'s requests are not counted until its Stage 2 puts it behind the
front.

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

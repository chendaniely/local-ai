---
title: "The usage meter: every model priced"
description: "Speech and embeddings priced by a proxy, the length of every Whisper recording learned by the front, a by-size suggestion for a new model's proxy, and make prices."
---

*(Written 2026-11-12, from Dan's answers that night in [the meter's decisions](usage-meter-qa.md),
sections* Every model priced by a proxy *and* Speech and embeddings priced*. Builds on
[the first numbers](usage-meter-now.md) and the by-size remap, deployed the same night. Its plan is
[usage-meter-every-model-plan.md](usage-meter-every-model-plan.md).)*

## Why

Dan: "any token should be priced", with "a reasonable proxy for pricing" where Anthropic sells no
equivalent. After the remap every chat model has a Claude proxy, but speech and embeddings are
still `none`: the menu's spend leaves them out, and a model added later has no help choosing its
proxy.

## Goals

1. **Every request that passes the front and did work is priced.** Speech by its proxy's price,
   embeddings by theirs, chat as today.
2. **A Whisper recording's length is always known.** The front learns it from the engine's answer,
   and the client still gets the format it asked for.
3. **A new model gets a suggestion.** A model with no mapping line still fails `make test`, CI and
   `make doctor`, and the failure says which proxy the by-size rule gives it.
4. **`make prices` prints the table** that [Model prices](../reference/model-prices.md) holds.

**Non-goals.** The by-hand speech service (`make speech`) doesn't pass the front, so it has no rows
to price, like the by-hand coders before their import; it gets rows when its planned Stage 2 puts it
behind the front. A refused request did no work; a cancelled one never reported its counts; both
stay unpriced, as the meter's design has it.

**Anti-goals.** A client must never get a different answer than before: same format, same text.
Nothing a client sent is kept, as before; a row gains numbers only.

## The proxies (prices read 2026-11-12 from each vendor's own page)

| Models | Proxy | Price |
|---|---|---|
| `whisper-large-v3`, `whisper-large-v3-turbo` | OpenAI `whisper-1` | $0.006 per audio minute |
| the four `qwen3-asr` models | OpenAI `gpt-4o-mini-transcribe` | $1.25 per million tokens in, $5.00 out |
| `qwen3-embedding-0.6b` | Voyage `voyage-4-lite` | $0.02 per million tokens |

*(My calls, within Dan's "a reasonable proxy":)* Whisper keeps the proxy he was shown, the hosted form
of the same model. Qwen3-ASR's engine (llama.cpp) gives only a plain transcript, with no length, but
its answer counts tokens, so it is priced by token, at the token-priced transcription model its size
matches, as the chat models are. Embeddings take Voyage's current lite model, the same price as the
3.5 lite Dan was shown. Each line applies from 2026-09-01, before the first row, so the past is priced
too; a later price change is a new dated line, as for Claude.

## How each part works

**Proxies beyond Claude's tiers.** `prices.yaml` can name any proxy, not only Claude's four tiers. A
proxy's lines are per token (the five rates, as today; a rate it doesn't charge is 0) or per audio
minute (`per_minute`). `mapping.yaml`'s `tier` names a proxy in `prices.yaml`, or `none`; a name that
isn't there is refused, as an unknown tier is now.

**The front learns each Whisper recording's length.** On `/v1/audio/transcriptions` to a model whose
engine is whisper.cpp, by the format the client asked for (whisper.cpp v1.9.4's own formats):

- `verbose_json`: passed through; its `duration` goes on the row.
- `json` (also when no format is given) or `text`: the front asks whisper for `verbose_json`, reads
  `duration`, and answers with what whisper's own `json` (`{"text": …}`) or `text` (the text and a line
  break) would have been. Not when the form sets `detect_language`, whose `json` answer differs.
- `srt` or `vtt`: passed through; the last cue's end time goes on the row, marked as an estimate.
- Anything else, or an answer the front can't read: passed through unchanged, with no length.

*(2026-11-12, at the build, Task E3: a converted form also pins `token_timestamps` to the value whisper gives `json`
and `text`, since it turns them on for `verbose_json`, and with them on its server rewraps the text at 60 characters.
A form whose `max_len` whisper can't read, which it answers 500 whatever the format, or one with a file part named like
a field whisper reads, goes as it came.)*

*(2026-11-13, at Task E3's review: a converted form also asks for no language probabilities, which cost whisper another
encoder pass and which `json` and `text` never carry; ~~only an upload of at most 16 MiB is converted, so a transcript
can't come near the cap~~ (corrected the same day: an upload is converted whatever its size, as doctor's 170 MB `json`
upload needs); an answer that goes as it came and passes the cap or the front's memory budget goes on unheld, with no
length, and only a converted one ends as the front's own 502 `front_error`, which past the cap takes a recording of
several hours at least asked for as `json` or `text` (`verbose_json`, which vid2sub asks for, goes on at any length);
and whisper's answers are never scanned for token counts, since a transcript could hold one. A converted answer is
whisper's own but in two cases: `text`, where whisper leaves segment bytes that aren't UTF-8 (U+FFFD in their place),
and an unknown `language` code, which fails whisper's `verbose_json` (500) where `json` or `text` would have answered.)*

The row gains `audio_s`, the length in seconds, and `audio_est: true` when it is an estimate. A
transcription row with no `audio_s` (rows from before this, and the cases above) is estimated when
read, from its upload's size at 16,000 bytes a second (a 128 kbit/s file) *(my call)*, marked `~` as
the fast coder's cache estimate is. Qwen3-ASR's answers are scanned for their token counts
(`input_tokens`, `output_tokens`), as chat answers are, and its rows priced by them.

**Pricing and the summary.** A per-minute row costs its seconds ÷ 60 × the line's price × the
mapping's factor. The summary's sums gain audio seconds, its periods gain `audio_s` (a new field, so
the contract stays `v` 1; spark-menu's `MENU_READS` is unchanged *(2026-11-13, after the deploy: spark-menu's by-model list now reads `by_model`'s `audio_s`, so `MENU_READS_BY_MODEL` gained it; the menu treats a missing one as 0)*), and `make usage`'s line adds the
minutes of audio when there are any. What rests on an estimate counts in `usd_estimated`, as now.

**The suggestion.** For a registry model with no line, the by-size rule *(Dan: "suggest/recommend by
size so it's almost automatic")*: embeddings → `voyage-4-lite`; transcription on whisper.cpp →
`whisper-1`, on llama.cpp → `gpt-4o-mini-transcribe`; chat by the parameters it uses per token, read
from its name or its file's name (`35B-A3B` uses 3B, `27B` uses 27B): 6B or more → `sonnet`, under
6B → `haiku`, which reproduces Dan's remap. With no size in either name, no suggestion: Dan picks. The
failure of `make test`'s registry check and doctor's `meter prices` both print the suggested line.
Nothing is priced by the suggestion until the line is written.

**`make prices`** runs `spark usage prices`, which prints the table; the page's text says to paste it.

## Risks, and what checks them

- **A converted answer differs from whisper's own.** Tests compare the conversion with answers from
  whisper.cpp v1.9.4's own code; on the Spark, the deploy's check sends one file as `json` and `text`
  before and after and compares the answers byte for byte. *(2026-11-13, at the deploy, Task E6: an
  11-second recording sent as `json`, as `text`, and as `json` with `max_len` set came back
  byte for byte the same after the deploy as before, content type included.)*
- **The front holds an answer to convert it.** The answer is capped (the plan's number), and the
  front's memory bound counts it; past the cap the request ends as an error, as an answer the front
  can't send does now.
- **Asking for `verbose_json` costs whisper a little more:** it also works out language
  probabilities. Small next to transcription; the plan's deploy check times one file. *(2026-11-13,
  at the deploy: the converted form asks for no language probabilities (the plan's Task E3 note), and
  the 11-second file took no longer after the deploy than before; one run of each, not a benchmark,
  so the changelog gives the times.)*
- **Estimates read as facts.** Each is marked: `audio_est`, and `~` in the menu.

## Docs that change with it

[Model prices](../reference/model-prices.md) (every model priced; `make prices`; the by-hand speech
gap), S26 and the speech scenario page (the front's new handling, and doctor's check that a
transcription row has its length), [Projects that use the stack](../reference/projects.md) (the
summary's new field; answers unchanged), `changelog.md` and README §Current state at the deploy.

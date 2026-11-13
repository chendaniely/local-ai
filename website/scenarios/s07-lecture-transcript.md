---
title: "S07 · Lecture → transcript with speakers"
scenario-id: S07
phase: 3
status: planned
---

**Situation.** A 90-minute lecture recording lands in a NAS folder, and my own pipeline picks
it up.

**What happens.** Batch speech-to-text runs — Whisper with a vocabulary prompt and word
timestamps — plus speaker labels from the diarization endpoint. Batch work yields to
interactive use, and no content is logged. *(2026-11-10, Dan's decision, from the next deploy:
the stack's whisper-server runs with `--max-context 0`, against loops on long audio, and so drops a
request's prompt by default. A pipeline that wants its vocabulary prompt sends `max_context` with
each request, at the risk of loops on hard audio.)* *(2026-11-12, built on the `meter-every-model` branch, not yet
deployed: so that each recording's length goes on its usage row, the front asks Whisper for `verbose_json` when a
request asks for `json`, `text` or no format, and answers in the format asked for, byte for byte as Whisper would have
and with its content type; `verbose_json`, `srt` and `vtt` go as they came. `spark doctor --full` checks that its
170 MB upload's row has the recording's length.)* *(2026-11-13, at the task's review: byte for byte but in three
cases. Where Whisper leaves segment text that isn't UTF-8, the `text` answer has U+FFFD in place of those bytes; an
unknown `language` code, which Whisper's `verbose_json` fails on, gets Whisper's 500 where `json` or `text` would have
answered; and a converted answer past 16 MiB or the front's memory budget, or one the front can't read, gets the
front's own 502 `front_error`, with `x-should-retry: false`. Past 16 MiB takes a recording of several hours at least
asked for as `json` or `text`; `verbose_json`, which vid2sub asks for, goes on at any length. ~~An upload over 16 MiB
isn't converted, so its row's length is estimated from its size.~~ Corrected the same day: an upload is converted
whatever its size, so doctor's 170 MB upload, sent as `json`, has its length on its row.)*

**What I see.** The pipeline's output.

**How to override.** Ask for the English-optimised or the multilingual model by name.

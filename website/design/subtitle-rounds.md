---
title: "TV-drama subtitles — round by round"
description: "One Mandarin episode's subtitles on the Spark, from Qwen3-ASR's first loops on 2026-11-09 to Whisper with no carried context and an English translation on 2026-11-10: each round's numbers, what changed, and what's still open."
date: 2026-11-10
---

# TV-drama subtitles — round by round

*Written 2026-11-10, at Dan's request, after he watched the fourth round's subtitles: "this is a drastic improvment to
the first set of subs!"*

Over two days, one Mandarin TV-drama episode went through vid2sub on the Spark five times, to meet the done test of
[the speech service's design](speech-service.md) (GitHub issue #35): a real episode runs through vid2sub on the Spark
from start to finish, with word timings Dan judges right by watching, while he keeps coding on the by-hand SGLang
coder, and vid2sub's parser unchanged. The test names no model. This page puts the rounds side by side; the design's
*The answer*, the plan's Revisions and `changelog.md` hold the same facts as they were found. The episode isn't named
here, and none of its dialogue is quoted. Its audio runs 45 min 35 s.

## The rounds

A *loop* below is a 30-second stretch of the transcript where one line comes 4 or more times, counted from vid2sub's
sidecar. Rounds 1 and 2 count chunks that ran to the model's token limit instead, which is how that service's log
shows a loop.

| Round | Date | What transcribed it | Time for the episode | Transcript | Loops |
|---|---|---|---|---|---|
| 1 | 2026-11-09 | Qwen3-ASR 1.7B and its aligner, the by-hand speech service, with vid2sub's prompt as context | 29:16 | not counted | 12 of 32 chunks |
| 2 | 2026-11-09 | the same, with the prompt ignored | 28:39 | about 2,700 characters | 12 of 32 chunks, 11 of them round 1's |
| 3 | 2026-11-10 | Whisper large-v3-turbo, the stack's own, through the front: vid2sub's `spark-turbo` as it ships | 0:57 | 4,869 characters, 820 cues | 6 |
| 4 | 2026-11-10 | the same, with `max_context=0` | about 1 minute | 4,201 characters, 703 cues | none |
| English | 2026-11-10 | Qwen3.6, translating round 4's transcript, thinking off | 4:24 | 594 cues | — |

Round 3's characters include the loops' repeats; round 4's are dialogue. In every round the brake stayed quiet and the
SGLang coder stayed up.

### Round 1 — Qwen3-ASR, with vid2sub's prompt

The speech service's first real run, started by hand, outside the gate. vid2sub sends the end of the
previous chunk's text as each request's `prompt`, and the service passed it to the model as context. 12 of the 32
chunks ran to the 4,096-token limit, about 80 % of the episode's speech and 28.7 of the run's 29.3 minutes, and came
back invented or nearly empty. On a 161 s test clip, 489 words in 11 s became 5,822 in 132 s with the clip's own first
100 characters as the prompt, so the service was changed to ignore it (`2fbf683`).

### Round 2 — the prompt left out

12 of 32 chunks looped again, 11 of them the same as round 1's, so the prompt was not the main cause (`4b20722`
corrected every sentence that had blamed it). Every chunk still got word timings, but the transcript came to about
2,700 characters for the whole episode.

### Between the rounds — 10 minutes in 30-second windows

Ten minutes of the episode, cut into 20 windows of 30 s, through the same service: 6 of the 20 looped, with text
unrelated to the audio. Left to choose the language, Qwen3-ASR answered "no speech" on 11 of them. The stack's
Whisper, through the front, found dialogue in all 20, in 0.4 to 1.6 s each (`11b1721`). So the audio has speech, and
Qwen3-ASR is what fails on it. Why isn't known: the same audio through the Mac's engine would tell the model from the
Spark's setup, and that comparison is still open.

### Round 3 — the stack's Whisper, as deployed

Dan ran vid2sub on the Mac against the Spark's Whisper with its stock `spark-turbo` model: 57 s for the episode, about
48 times faster than it plays. `MemAvailable` stayed flat near 35.5 GiB with the coder running. But it looped in 6
places, the longest a line repeated 18 times in 30 s, in place of the dialogue there.

### Round 4 — Whisper with no carried context

whisper.cpp carries the text it decoded in a request's earlier 30-second windows into the next window as context, and
one bad window feeds the next. Five runs of the same audio on the Spark, from a scratch copy of vid2sub that could send
whisper-server's own settings:

| Setting | Loops |
|---|---|
| as it ships | 11 |
| vid2sub's prompt left out | 9 |
| `beam_size=5` | 4 |
| `max_context=64`, with `carry_initial_prompt` | 2 |
| **`max_context=0`** | **0** |

The one repeat left with `max_context=0`, a short word 5 times, is the same with the prompt left out, so it may be what
was said. In the stretches compared side by side, `max_context=0` kept the dialogue the other runs had, in shorter
lines and without punctuation. Dan watched it against the episode's burned-in subtitles: both former loop places now
carry the right dialogue, and "some are early/late. not too bad though." His verdict: "i think this is already
worlds better than before. the rest is fine tuning, but i'm okay with somethig liek this as a homegrown project."

Round 3 and the first run in this table used the same settings, yet looped in 6 places and 11. vid2sub cut the audio
into 29 chunks on the Mac and 32 on the Spark, so where a loop lands moves with the chunking; the count isn't a fixed
property of the episode.

### English

vid2sub translated round 4's transcript with Qwen3.6, which the gate loaded on demand beside the coder: 4 min 24 s,
every line in English. The translator joins short lines into whole sentences, so 703 Chinese cues became 594 English
ones, and some of those are long for a subtitle. A line whose timing was off in Chinese is off the same way in English.

## What changed, and where

- **This repo, on `phase-2a`:** the speech service, built by hand on 2026-11-09 (`b733676` to `1db63da`); it ignores
  vid2sub's prompt (`2fbf683`); the corrections as each round showed more (`4b20722`, `11b1721`); and whisper-server
  runs with `--max-context 0` (`d61417c`), for every client, **from the next deploy**. Until then nothing on the box
  has changed for Whisper.
- **vid2sub:** its transcription requests carry the model's `request_extra`, as translation's already did, and its
  `spark-turbo` model sends `max_context = 0`. That change is in vid2sub's own repository, applied by Dan; it isn't a
  parser change.
- **Dan's decision, 2026-11-10:** the setting goes in both places, so vid2sub gets it now and every other client of the
  Spark's Whisper from the deploy.

## What's still open

- **Timing.** Without DTW, whisper.cpp's word times are rough: some lines come early or late, and a line can stretch
  across a silence, as one did over several seconds in round 4. vid2sub flagged 101 of round 4's 703 cues `halluc`,
  about half of them lying outside the speech it detected; the flag marks a cue and drops nothing.
  whisper-server's `--dtw large.v3.turbo` gives better token times, which vid2sub's parser already reads, but it turns
  flash attention off and hasn't been tried here.
- **The prompt.** With `max_context=0` whisper.cpp also drops a request's prompt, so a vid2sub glossary does nothing on
  Whisper. A client that wants its prompt sends `max_context` itself, at the risk of loops.
- **vid2sub's own guard.** vid2sub stops passing a chunk's text on as the next prompt when the server reports a high
  compression ratio. whisper.cpp reports none, so on `spark-turbo` that guard never fires. It's vid2sub's to fix.
- **Qwen3-ASR on drama.** It stays the by-hand service; it isn't reliable on this kind of audio, and why is open.
- **The done test.** Round 4 passes it by Dan's judgment, but from the scratch copy of vid2sub; one run through his own
  vid2sub, with its change in, closes it.

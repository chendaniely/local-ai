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
each request, at the risk of loops on hard audio.)*

**What I see.** The pipeline's output.

**How to override.** Ask for the English-optimised or the multilingual model by name.

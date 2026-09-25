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
interactive use, and no content is logged.

**What I see.** The pipeline's output.

**How to override.** Ask for the English-optimised or the multilingual model by name.

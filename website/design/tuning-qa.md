---
title: "Tuning a model on the fly — questions and answers"
description: "How changing a few of a model's settings on the fly, its sampling and its context, is being decided: the scenario questions Dan answered, and the reasons. A draft, begun 2026-10-23."
date: 2026-10-23
---

# Tuning a model on the fly — questions and answers

*(A draft, being decided. Nothing on this page is built, and the plan doesn't hold these decisions
yet.)*

Dan's request, 2026-10-23: a way to change some of a model's settings on the fly — not every one, as
LM Studio offers, but a few. The context, to get two models to fit at once, such as a transcription
model loaded beside the coder; and the temperature, for Gemma 4's thinking loops. This page keeps
the questions asked about it, his answers and the reasons, in the order they were asked. The
recommendation was option A unless a question says otherwise.

## The two kinds of setting

Read from `stack/models.yaml` and llama-swap v257's config schema
(`stack/llama-swap/config-schema.v257.json`) on 2026-10-23:

- **Sampling** — temperature, top_p, top_k, min_p, presence penalty, max tokens — is set per
  request. llama-server takes it on each call, so a change needs no reload and no memory. The
  registry sets none of it today: each model runs with what the client sends, or llama-server's
  defaults when it sends none. llama-swap v257 can set it per model (`filters.setParams`, where a
  key ending in `?` applies only when the request doesn't set it), and per alias of one loaded
  model (`filters.setParamsByID`). A change to llama-swap's config takes `make apply`, which
  restarts llama-swap once no request has been in flight for a minute: every model stops, and the
  gate reloads the always-loaded ones.
- **Context** is set per load. llama-server reserves it when the model starts, it is part of the
  footprint the gate admits the model by, and pi has to be told the same window (`contextWindow`,
  which `make clients` writes), or its long requests are refused with a `400`. A change means an
  unload and a reload at the new size. By the registry's own estimates, the coder's cache costs
  ~68 KiB a token, ~17 GiB of its 41 at its full 262,144 tokens; Gemma keeps a full-length cache in
  only 5 of its 30 layers, so a shorter context frees much less of its 32.

## Sampling

**Q1. *Gemma is stuck in a thinking loop mid-chat. You want to try temperature 1.0 right now, and
keep it if it helps. What do you expect to do?***

- A: try it in the client — Open WebUI's controls, per chat and per model, already do — and once a
  value works, it becomes Gemma's default in `stack/models.yaml`, sent to every client that doesn't
  set its own (llama-swap's `setParams`, set-if-undefined), deployed by `make apply`.
- B: presets by name: `gemma` and `gemma-loose` are one loaded model with different sampling
  (llama-swap's aliases and `setParamsByID`), picked by model name in any client, pi included. A new
  or changed preset takes `make apply`.
- C: a live setting on the Spark, `spark tune gemma temperature=1.0`, which the front applies to
  every client's requests at once, with no restart, until it is reset. New code in the front's
  request path.

**Dan: A** (2026-10-23). He gave no reason beyond the request's: a few settings, not all of them.
The recommendation's caveat, given with it: if the loops happen in pi rather than in the web UI, pi
may have no per-session temperature (not checked), and B would fit better.

## Context

**Q2. *You need the transcription model loaded beside the coder, and today they don't fit at the
coder's full context. How often does that pairing come up, and what do you expect?***

- A: every day: lower the coder's default context once (to 131,072, say, freeing ~8.5 GiB), so both
  always fit, with nothing on the fly for this case; pi on the coder compacts sooner, at ~115k
  tokens instead of ~246k.
- B: now and then: the registry gains a second, smaller version of the coder, loaded by name when
  the transcription model is needed.
- C: rarely: keep things as they are, and unload the coder by hand while the transcription model
  is needed.

**Dan: none of these** (2026-10-23): "I may want to change the context window of either the coder or
the transcription model." So the context is changed case by case, for either model, not set once for
a pair. The question took whisper large-v3-turbo, which has no context window and already fits
beside the coder, to be some other model; which one isn't settled.

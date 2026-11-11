---
title: "Projects that use the stack"
description: "Every project of mine that relies on the Spark, what each one relies on, and the notices it should read before changing how it calls the Spark."
---

I built several projects before this stack was finished, each as I needed it, and they keep
changing in parallel with it. This page lets them stay aligned and know about each other: what the
stack offers them, which project relies on which part, and dated notices of changes each project
still has to make. The harnesses themselves (Pi, OpenCode, Hermes Agent, Posit Assistant) and the
diagrams are on [the Architecture page](../architecture.qmd#harnesses).

**How it's used.** Each project's own `AGENTS.md` or `CLAUDE.md` points here. A session there reads
its row and its notices before changing how the project calls the Spark, and records a new need
here or in [the plan](../design/plan.md). A session here that changes a part of the contract adds a
notice under every project that relies on it, in the same commit (`CLAUDE.md`'s sync table).
Notices hold contract facts only: what changed, where, and what the project has to do. They never
hold addresses, key values, command output or anything private.

## The contract

What the stack offers its projects, and the file that defines each part. The file is the source of
truth; this table says only what a project must not miss.

| Part | Defined in | What a project must not miss |
|---|---|---|
| **The front**, `http://127.0.0.1:9100/v1`, through `make tunnel` on the Mac | the `Makefile`'s `tunnel`; [Reaching the Spark](../architecture.qmd#reaching-the-spark) | It serves the inference routes, plus `GET /v1/models` (its own list) and `GET /health` (no key). Everything else needs a client key. A form upload is capped at 200 MiB. |
| **Models and roles** | `stack/models.yaml` | A request names a model, or one of the roles `small`, `coder`, `stt`, `embed` and `vision`. Which model holds a role, and which models stay loaded (`resident`), can change at a deploy. |
| **Refusals** | `spark/src/spark/messages.py` | A 409, or a 503 `gate_down`, carries `error.code`, a top-level `retry_after_s`, a `Retry-After` header and `x-should-retry: false`. `RETRY_AFTER_S` gives each code's wait. A client that waits out refusals should know every code in `CODES_409` and `CODES_503`. |
| **Keys** | [Harnesses and clients](../architecture.qmd#harnesses) | Each client has its own key, by reference only. A key's group (`dan`, `agent`) sets how long a request waits for a load before it is refused. |
| **The speech service**, `127.0.0.1:58991`, through `make tunnel` | [The speech service, by hand](../how-to/speech-by-hand.md); `stack/speech/serve.py` | It needs no key and is up only while `make speech` runs on the Spark. It answers only `Host: 127.0.0.1:58991` or `localhost:58991`, and refuses any `Origin`. Languages go as ISO codes. It ignores `prompt`. |
| **`spark status --json`**, on the Spark | `spark/src/spark/gateproto.py`, `STATUS_SCHEMA` | The schema number goes up when a field's meaning changes. |
| **The `spark` CLI and the `Makefile`** | `spark/src/spark/commands.py`; the `Makefile` | spark-menu calls the CLI at `/opt/local-ai/app/.venv/bin/spark` on the Spark, and reads `make -n tunnel`'s `ssh -L` words from the Mac's clone. |

## The projects

| Project | Repository | Runs on | Relies on | Its key |
|---|---|---|---|---|
| **vid2sub** | [chendaniely/vid2sub](https://github.com/chendaniely/vid2sub) | the Mac | the front (transcription and translation), refusals, the speech service through my own config | `SPARK_API_KEY` |
| **recordings** | [chendaniely/recordings](https://github.com/chendaniely/recordings) | a homelab server, in Docker | nothing yet. From its stage 4: the front (transcription, a small model, summaries); later `diarize` and `embed`, which it has asked for (the plan's open item *recordings' requests*) | `RECORDINGS_SPARK_API_KEY`, planned |
| **skills** | [chendaniely/skills](https://github.com/chendaniely/skills) | the Mac | class-notes: the front's models and the by-hand SGLang coder. plan-driven-development: its portability runs drive Pi and OpenCode on the Spark, through the `spark` provider that `make clients` writes, and its leak check reads the same denylist as this repo. capture-conversation transcribes on the Mac today. | `SPARK_API_KEY`, `SPARK_SGLANG_KEY` |
| **spark-menu** (SwiftBar) | my dotfiles (private) | the Mac | SSH to `brightroar`: `spark status --json`, the CLI's verbs, health checks on the Spark's own ports, and `make -n tunnel`'s forwards. It moves into this repo at Phase 2a's close. | none; it goes over SSH |

## Between the projects

- **vid2sub's parser is copied here.** The speech service is tested against a copy of vid2sub's
  subtitle parser; its docstring, in `spark/tests/vid2sub_parser.py`, names the commit it was
  copied from. A change to those functions in vid2sub means recopying them.
- **skills' class-notes calls spark-menu.** Its `local-models.md` has agents run `spark-tunnel
  menu` to see what is loaded, so spark-menu's move keeps that command working.
- **class-notes and recordings follow the same client sketch,** the docstring of class-notes'
  `run_local`. One implementation could serve both.

## Notices

Newest first under each project. A notice stays until the project's session marks it done here.

### vid2sub

- **2026-11-10 · open.** Since the cutover, the front also refuses with `restarting`, `draining` and
  `llama_swap_down` once a key's wait runs out (`CODES_409`). vid2sub knows four codes and stops on
  the others. It should wait these out too.
- **2026-11-10 · open.** The front puts `retry_after_s` at the top level of the error body, and in
  `Retry-After`. vid2sub reads it inside `error`, so it always falls back to its own backoff, even
  against the brake's five minutes.
- **2026-11-10 · open.** At 44l's deploy, Qwen3.6 (`small`) stays loaded and Gemma loads on demand.
  Until then Gemma often can't load beside the by-hand coder, so vid2sub's translation default
  pauses on `no_fit`. Choose its default for after 44l: the `small` role, or Gemma with its load.
- **2026-11-10 · open.** For Qwen3-ASR, vid2sub's Task 20 matches neither path. Behind the front,
  Qwen3-ASR returns text without timestamps. The speech service returns word timings, takes ISO
  codes and needs no key. Re-plan Task 20 against one of them.
- **planned · not scheduled.** `main`'s plan moves 9100 to 59100 in one change. When it is
  scheduled, a notice here gives the date and the new base URL.

### recordings

- **2026-11-10 · open.** Nothing on the Spark listens beyond 127.0.0.1 except SSH, and Phase 3
  plans the LAN for homelab apps. recordings' config expects the tailnet. Settle the way in with this
  repo before stage 4.
- **2026-11-10 · open.** At 44l's deploy the `small` role moves to Qwen3.6 and Gemma loads on
  demand, which affects recordings' `pick_model`.
- **2026-11-10 · note.** The speech service ignores `prompt`, since a prompt made Qwen3-ASR loop. A
  vocabulary sent through `transcribe(vocabulary=…)` doesn't reach it.

### skills

- **2026-11-10 · open.** class-notes' `local-models.md`: 9100 is the front (then the gate, then
  llama-swap); the gate never unloads a model to make room; Qwen3.6 gets two slots at 44l; the SGLang
  coder on 8990 needs its own forward, or spark-menu's.
- **2026-11-10 · open.** From 44l, OpenCode and Hermes use their own keys, `SPARK_OPENCODE_KEY` and
  `SPARK_HERMES_KEY`. The portability harness passes only `SPARK_API_KEY`.
- **planned · not scheduled.** The move from 9100 to 59100, as under vid2sub.

### spark-menu

- **2026-11-10 · open.** 9100 is the front, and its `/health` answers 200 with no key; the menu's
  docs call it llama-swap. This folds into the move at Phase 2a's close.

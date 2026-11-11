---
title: "The projects on the stack: a coordination survey"
description: "2026-11-10: every project that relies on the Spark checked against phase-2a, what had drifted, and who fixes what."
---

**2026-11-10, Dan's request:** "i need to coordinate the work between multiple repos that use the
spark and local-ai. they have been worked on in parallel and lots of things happening." He then
set the goal: "the goal here is to make sure all projects are aligned, and know of each other so
changes do not break other things." He also asked for a list of the projects in this repo, "so all
my local-ai apps can potentially know of each other and coordinate changes". That list is now
[Projects that use the stack](../reference/projects.md). And he asked whether spark-menu should
move here now.

## Method

Six read-only surveys ran in parallel, one for this repo and one for each repository or pair of
them, all from the Mac. Each checked its repository's assumptions against `phase-2a` at `3d314bd`,
in both directions, with `file:line` on each side, and changed nothing. The session re-checked
every finding that breaks something against the code. A council of four (goal fit, reliability,
security, simplicity) then reviewed the first draft, and the session checked its claims too. Its
summary is [`multi-repo-coordination-council.md`](multi-repo-coordination-council.md). Some
repositories are private, so what concerns only them stays out of this public page.

To run it again, start the same surveys from the projects table, one per project, then hold the
council.

## What had drifted

Severity: **breaks** (a run fails or misbehaves today), **44l** (changes at 44l's deploy), **doc**
(a wrong sentence). Each notice for another project is on [the projects
page](../reference/projects.md#notices), where that project's sessions read it.

| # | What | Evidence | Severity | Who fixes it |
|---|---|---|---|---|
| 1 | vid2sub stops on three refusals the front sends after a key's wait: `restarting`, `draining`, `llama_swap_down` | vid2sub `src/vid2sub/engines/clients.py:30`; `spark/src/spark/messages.py:43-49` | breaks (most likely during a deploy) | vid2sub (notice) |
| 2 | vid2sub reads `retry_after_s` inside `error`; the front sends it at the top level and in `Retry-After` | vid2sub `clients.py:150`; `messages.py`, `Refusal.body` | breaks (it ignores the front's waits) | vid2sub (notice) |
| 3 | vid2sub translates with Gemma, which often can't load beside the by-hand coder, and loads on demand after 44l | vid2sub `catalog.py:75`; `stack/models.yaml` (`resident`); `changelog.md` | breaks now, and at 44l | vid2sub (notice) |
| 4 | vid2sub's Task 20 matches neither Qwen3-ASR path | vid2sub's phase-2 plan, Task 20; `stack/models.yaml`; `stack/speech/serve.py` | doc (a plan not yet built) | vid2sub (notice) |
| 5 | `make tunnel` on `phase-2a` doesn't forward SearXNG (58888); `main`'s does. spark-menu adds it itself, so only a bare `make tunnel` lacks it | `Makefile:92-93`; `main`'s `Makefile:76-77` | breaks (a Mac agent's search, outside spark-menu) | this repo, at the merge of `main` into `phase-2a`. `spark/tests/test_speech_target.py:453` and the speech plan's listing assert the exact command, so they change with it |
| 6 | `make clients` defaults OpenCode and Hermes to keys the front accepts only from 44l | `spark/src/spark/clients.py:427,441`; [their runbook](../how-to/opencode-hermes.md) | breaks before 44l (a 401 if run now) | this repo, in 44l |
| 7 | skills' portability harness passes only `SPARK_API_KEY` | skills `plan-driven-development/tests/harness/run.py:343` | 44l | skills (notice) |
| 8 | skills' `local-models.md` calls 9100 llama-swap, says a load can unload others, and gives Qwen3.6 one slot | skills `class-notes/references/local-models.md:12,17,38-39` | doc | skills (notice) |
| 9 | recordings expects the tailnet, and Gemma as its small model | recordings `config.example.toml:131-132`; [the plan](plan.md), Phase 3 | doc now; breaks at its stage 4 | recordings and this repo (notice; open item) |
| 10 | `main`'s plan moves 9100 to 59100 "in one change" but lists only this repo's parts; vid2sub, skills and the Python apps would break | `main`'s `plan.md:769-772` | a future break | this repo, when it merges or schedules it (notice) |
| 11 | The plan said the audio host runs on the Mac, on Pixeltable | `plan.md`, Phase 3; recordings' spec | doc | this repo: corrected |
| 12 | recordings' `diarize` and `embed` request wasn't in the plan | recordings' spec §3; `plan.md`, *Speech* | doc | this repo: an open item |
| 13 | The plan said spark-menu moves "once its build is done"; Dan's ruling of 2026-10-23 says after Phase 2a | `plan.md`, *Mac and agent clients* and Phase 2b | doc | this repo: corrected |
| 14 | The plan and the speech design said vid2sub's `max_context` change was pending; it landed on vid2sub's `phase-2` at `cb29cf8` | `plan.md`, Revisions; `speech-service.md` | doc | this repo: corrected |
| 15 | Issue #35 says vid2sub's starter has a commented `spark-qwen` entry; it never had one | vid2sub's history | doc | this repo: a comment, with Dan's OK |
| 16 | The Architecture page draws Qwen3-ASR on llama.cpp as built; the changelog records no deploy of it | `architecture.qmd:88,313`; `changelog.md` | doc (a diagram) | this repo, Spark session: check the box, then dash it until 44l |
| 17 | The harness bullet says the Mac's harnesses share one key; from 44l OpenCode and Hermes have their own | `architecture.qmd:28`; `:234-237` | doc | this repo, in 44l |
| 18 | `README.md` §Current state, the MacBook, lacks vid2sub, Hermes, the Python apps and the speech forward | `README.md`; `architecture.qmd:27-31` | doc | this repo, Mac session |
| 19 | The vault's entry note is out of date | the vault | doc | Dan |

**Consistent:** vid2sub's base URL, key name and `max_context` field match the front. spark-menu
matches `spark status --json` schema 1, every CLI verb it calls, and the forwards. No project
routes Claude Code or Claude Desktop through the Spark.

## Decisions

- **The projects page,** with the contract, a row per project and dated notices, and a pointer
  block in each project's own `AGENTS.md` or `CLAUDE.md`. Projects pull their notices from the
  page rather than having issues pushed to them. The Spark session's GitHub token reaches only this
  repository, and a notice here works from either machine (Dan's answer: "A", 2026-11-10).
- **Of Dan's audio projects, only recordings is named here,** since it is the one that will call
  the Spark (Dan's answer: "A", 2026-11-10).
- **spark-menu moves at Phase 2a's close, not now.** It isn't private: its tree and history carry
  no address, key or outside name, only a few test lines to make generic before it moves. But Dan's
  ruling of 2026-10-23 keeps it in the dotfiles until Phase 2a finishes. An install from here now
  would come from a branch, because nothing deploys from `main` until Phase 2a merges. Where it goes
  (`clients/swiftbar/` is reserved) and what stays private are Dan's at the move.
- **recordings' hand-over runs in recordings,** from its own checkout and plan, and its next steps
  are Dan's. A refactor across his audio projects now would cut across that plan.
- **`CLAUDE.md`'s sync table gains a row** naming the files that define the contract, so a session
  knows when a change owes the projects a notice.

## Proposed, not built

- **A contract test.** The reliability reviewer suggested one that pins the contract's surface (the
  refusal codes and body, the resident set, the tunnel's forwards, the speech service's hosts, the
  key defaults, `STATUS_SCHEMA`). It would fail with "update the projects page and its notices"
  when any of these changes. It is code and tests, so it is the Spark session's to build, if Dan
  wants it.
- **Merge this branch before 44l deploys,** so 44l's session sees the notices it changes.

---
title: "The coordination survey's council"
description: "2026-11-10: four reviewers on the first draft of the coordination survey and the projects page, and what became of each finding."
---

Four read-only reviewers, one per lens, reviewed the first draft of [the coordination
survey](multi-repo-coordination.md) and of [Projects that use the stack](../reference/projects.md)
on 2026-11-10, before anything was pushed. Partway through, Dan set the goal they were to judge
by: "make sure all projects are aligned, and know of each other so changes do not break other
things". The session checked each claim below against the code before acting on it. The security
review also covered private repositories; those parts went to Dan and are not here.

## Goal fit

- **Critical: no project could find the page.** No other repository's `AGENTS.md` or `CLAUDE.md`
  mentioned this stack. → Each project gets a pointer block (Dan's answer to question 4).
- **Notice went one way only.** Nothing covered a project changing what this repo copies or reads
  (vid2sub's parser, spark-menu reading `make -n tunnel`, class-notes calling spark-menu). → *Between
  the projects*, and the pointer blocks ask each project to record its needs here.
- **The contract table had a false row and gaps.** "There are no aliases" was false: roles are
  aliases. `GET /health` and `GET /v1/models`, the upload cap, and spark-menu's dependencies were
  missing. → Corrected and added.
- **Missed drift:** recordings' way in to the Spark, vid2sub's spec still putting inline refusals in
  Phase 3, the dependency of class-notes on spark-menu. → Findings and notices.
- **The draft settled things that were Dan's** (spark-menu's location and what stays private) and
  **said things not yet true** ("fixed on this branch", a link to this page before it existed). →
  Reworded.

## Reliability

- **Confirmed:** vid2sub's missing refusal codes (severity lowered: they come only after a key's
  wait), the bare `make tunnel` without SearXNG (spark-menu adds it itself), vid2sub's Gemma default
  (already pausing today, not only at 44l), `make clients`' key defaults, the portability harness's
  single key. **Partly refuted:** skills' tunnel claim, since spark-menu forwards 8990 on demand.
  **Evidence misread:** the draft's quote about Qwen3-ASR not being downloaded was about Whisper
  large-v3; the conclusion still holds.
- **Missed breaks:** vid2sub reads `retry_after_s` from the wrong place, and `main`'s plan moves
  9100 to 59100 without the projects. → Findings 2 and 10, with notices.
- **Would the mechanism catch a change in time?** Not by itself. → A contract test is proposed for
  the Spark session, and this branch should merge before 44l deploys.
- **Process:** the Spark session edits `plan.md` too, so this branch rebases onto the latest
  `phase-2a` before its PR, with Dan sequencing the two sessions.

## Security

- **Before any push,** three items went to Dan, and the first draft was rewritten before it left
  the machine (Dan's answers to questions 1 and 2). The draft also carried a commit author the rest
  of the history doesn't use; the rewrite is under Dan's name.
- **What a pointer may say.** From a public repository: the private project's name and role in a
  few generic words, and a link to this page; never its paths, branches, internals or failures. Notices
  on this public page hold contract facts only. → Written into the page's *How it's used*.
- **spark-menu's move:** a test line to make generic, a state folder to tighten, and the standing
  SSH master documented, all at the move. Its fixtures and history carry no real address or name.
- **The checklist:** docs only, secrets by reference, no network change, the Claude path untouched.

## Simplicity

- **Keep only what prevents a break.** The page's "where it stands" column and its restated model
  ids would drift fastest. → Dropped. The page names parts of the contract, and links each to the
  file that defines it.
- **One rule, in one place.** The draft's four rules became one row in `CLAUDE.md`'s sync table,
  naming the files that trigger it, plus the pointer blocks.
- **Private housekeeping doesn't belong in a public record.** → Moved out.
- **A machine-readable list would be worse:** nothing would read it. A markdown table plus the
  pointer blocks is enough.
- **The diagrams don't show spark-menu** (built, over SSH), and the "homelab apps" node could name
  recordings. → Left for spark-menu's move, when the diagrams change anyway.

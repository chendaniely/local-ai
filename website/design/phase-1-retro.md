---
title: "Phase 1 — retrospective"
description: "What Phase 1 built, how it ran, where it departed from the plan, what the reviews found, why it looped, what Phase 2 inherits, and how to start over."
date: 2026-09-28
---

# Phase 1 — retrospective

Phase 1 ran from 2026-09-25 to 2026-09-28: [its implementation plan](phase-1.md), executed on branch
`phase-1`, on the Mac until the switch point and then in a Claude Code session on the Spark. It
took 144 commits before this page, `ebb32da..fa18819`. The page was written during Task 17, before
its last commits (the rest of the council's second fix batch, the scenario statuses and the
forward look) and before Task 18's merge. [The plan](plan.md) stays the place for decisions; its
forward look, 2026-09-28, says what Phase 2 takes up. This page is the history behind them, and
the tracked home of what Phase 1 set aside, which until now lived only in the session's gitignored
ledger.

## What Phase 1 delivered

| Piece | What it is | Where |
|---|---|---|
| The `spark` CLI, grown | The registry loader, the launch check and the brake's hold, a llama-swap client, and `spark brake`, `status`, `render`, `apply`, `models pull`, `clients` and `doctor`. 906 tests at the close, from Phase 0's 109. | `spark/` |
| The registry and render | `stack/models.yaml` renders into llama-swap's config, the systemd units and the Compose project. Render refuses, with the reason, an edit that rebinds an engine off 127.0.0.1, puts a key in a command, downloads a model, overrides a setting it derives, or loads a file outside the pinned snapshot. llama-server always runs `--offline`, `--no-slots` and `--no-webui`. | `stack/` · `spark/src/spark/render.py` |
| Deploy with root's own copies | `make apply` stages; `make install-units` (sudo) shows the diff and installs root's copies; `make apply-now`, `make pull`, `make brake-release`, `make clients`, `make tunnel`. The polkit rule starts, stops and restarts the four units by name, and nothing more. | [Deploy the stack](../how-to/deploy.md) · `Makefile` |
| The minimal brake and the launch check | `spark launch` refuses a load that doesn't fit, or while the brake holds, and says by how much. `spark brake` holds new loads and unloads, on-demand first, below 20 GiB; at start it checks that llama-swap takes its key. | plan.md, *Admission and memory rules* |
| Updates that don't take it down | The needrestart override; `make upgrade-gpu`, with the GRUB check, ending in `sudo -k`; `make doctor`, 15 checks. | [Updates](../how-to/updates.md) |
| The engines | llama-swap v257, llama.cpp b11146 (the build llama.cpp's v0.5.0 release names), and whisper.cpp v1.9.4 built for `121a-real`, installed at their pins. | [Stack](../reference/stack.md) · phase-1.md, Task 11 |
| The stack on the box | Serving since 2026-09-28: Gemma 4 26B (resident vision chat and Open WebUI's task model), Qwen3-Embedding-0.6B and whisper large-v3-turbo (resident), and the Qwen3.6-35B-A3B coder (on demand), each at its full context; Open WebUI and SearXNG under root's Compose unit; Open WebUI on the tailnet over HTTPS through `tailscale serve`. | `README.md` §Current state · `changelog.md` |
| Clients | pi's `spark` provider, written by `spark clients`: on the Mac through the tunnel, and for `agent` in tmux, whose Claude Code gets the secrets guard before its key. | [pi](../how-to/pi.md) |
| Runbooks | Two new ones, deploy.md and pi.md, steps 7 and 8 of *In order* (8 and 9 since 2026-10-07, when Phase 2a's Task 1 put ntfy.md at step 7). Every runbook says where each command runs, in bold above its block. | [How-to](../how-to/index.qmd) |
| Measurements | The brake drill's numbers, the first taken on this box; footprints at two contexts; load times. | `cosmicbboy-local-ai.md` §I · `changelog.md` |
| The lock's window | `spark/uv.lock` takes only releases at least seven days old, and Dependabot's uv PRs wait as long. | [Updates](../how-to/updates.md) · `spark/pyproject.toml` |

Of *Phase 1 is done when*, these hold. pi finished real tasks from the Mac and as `agent`, and a
detached session survived a logout. The brake, at raised thresholds, held, unloaded the coder,
refused the held reload and released it. A load that didn't fit was refused with needed against
available, and nothing was unloaded. A fresh clone with `make bootstrap` and `make apply` changed
nothing. The stack kept serving through a routine upgrade, came back by itself after a reboot, and
`make doctor` passed 15 of 15. S09 and S20 passed on the phone on 2026-09-28, and their pages turn
*verified* in Task 17 Step 1. CI's render step, the Mac check and the merge are Task 18's. The
upgrade half is weak evidence: apt moved no library the engines use and no Docker package.

## How it ran

| When | Where | What happened |
|---|---|---|
| 2026-09-25 | Mac | After Phase 0's merge (`ebb32da`), Dan's two decisions went into the plan: root-owned copies of the units, and `make upgrade-gpu` runs the GRUB check. The pre-flight ran every Task 1–10 listing in a scratch copy on the Mac and in `ubuntu:24.04`, and its review and a scan across the tasks were fixed and run again. Dan's rule that work runs on the Spark moved Tasks 1–10 and 17 there and added Task 18. `ebb32da..2fa6c8f`, 12 commits. |
| 2026-09-25 | Spark | The switch point: the session's guard, keys-only SSH, a GitHub token for this repository only. The clone's `origin` was SSH, with a key on Dan's account that went around the token; it moved to https. Dan's rule that every command says where it runs relabelled the runbooks. `2fa6c8f..3a55a7f`, 6 commits. |
| 2026-09-26 | Spark | Tasks 1–10, subagent-driven: `3a55a7f..1e5152b`, 83 commits, 46 of them `fix`. Task 1 first ran on a smaller model, which took its own harness's trailer; Dan had it redone on Opus, and every subagent since ran on Opus. Per task: an implementer whose first commit is the listing byte for byte, a review, fix rounds, a scoped re-review, then a plan record (*Superseded* notes, dated corrections, a Revisions line). Before Tasks 9 and 10, a read-only scan ran each brief on a scratch copy first, and its rulings went into the dispatch. 15 fix rounds in all, two Criticals (Tasks 4 and 7); 109 tests became 822. |
| 2026-09-27 | Spark | The push point: Dan's seven-day window (`7170a52`); an audit of every command's label, 38 fixes; the first push, with CI green. Task 11, run by the session itself: the engines at their pins, and `agent` reaches the GPU. Task 12: Dan re-ran bootstrap and ran `make apply`, which Claude Code's auto-mode classifier refused the session, then `make install-units` and `make pull`. The GRUB check passed. `1e5152b..f31b84f`. |
| 2026-09-28 | Spark, Dan | Tasks 13–15 (`f31b84f..fb96c8d`): the stack started, first footprints, task calls without thinking; the phone, where a photo aborted Gemma's engine until its micro-batch grew; pi on the Mac and as `agent`. Task 16's drills, then Dan's full-context decision, whose review found 7 Important and took two fix rounds against llama.cpp's source; deployed and checked (`fb96c8d..ce9df60`). |
| 2026-09-28 | Spark | Task 17: a council of four read-only reviewers (0 Critical, 15 Important, 37 Minor, 17 decisions for Dan). Batch A fixed what needed no decision: `ce9df60..8c48549`, 17 commits, 873 tests. Dan decided the rest that evening. Batch B, the code for two of his decisions and two of the session's rulings, followed (`8c48549..9a03b15`), then a follow-up of five minor fixes and the close's own commits. Its logging change was reversed before the deploy, by Dan, once the final review found whisper's file names and metadata would reach the journal. |
| 2026-09-29 | Mac | Task 18: the README records the Mac's coreutils, and CI renders the real registry. `make test lint docs` passed on bash 3.2 and GNU make 3.81, 906 tests with none skipped, and the site rendered with no warnings: no Mac difference turned up. The first push was refused, because the Mac's gh login lacked the `workflow` scope that a change under `.github/workflows/` needs, until Dan added it. CI went green on the branch, the render step included, and the merge followed: 4 commits after `b0a6b52`, then the merge (`ff205f9`). Then Dependabot's PRs #1–#3, one at a time: Dependabot rebased each onto `main`, and each was rebase-merged once its CI was green, with `main` green after each (`12d6383`, `b33c22e`, `16efba8`). pi.md's jq and awk block for `agent`'s guard ran in the Mac's zsh for the first time, and built both files as expected. |

Of the 144 commits, 57 are `fix`, 15 `feat`, 68 `docs` and 4 `build`. The ranges are git's `A..B`:
the commits after `A`, up to and including `B`. *(Recounted 2026-09-29, at the merge: 144 was
counted before the close's last commits. With them and Task 18's, the branch holds 158: 60 `fix`,
16 `feat`, 77 `docs`, 4 `build` and 1 `ci`.)*

## Where the build departed from the plan, and why

| What changed | Why | Commits |
|---|---|---|
| Every subagent runs on Opus, and Task 1 was redone. | Its first run was on a smaller model, which followed its own harness's trailer. Dan chose a redo over an amend. | `d8dd6f2`..`69cc076` |
| Every listing in Tasks 1–10 is marked *Superseded*. | Each review found the listing, byte for byte, short of the spec: refuse with the reason, fail closed, never show a key. The spec won. | Revisions, 2026-09-26 |
| `spark/`'s environment runs only on uv's own Python. | The Spark's had been built on Ubuntu's 3.12.3. Dan's decision. | `be74dff` |
| The lock holds huggingface_hub below 2, and takes only releases a week old. | It had taken 2.0.0, a new major two days old, for code that runs as `spark` with egress. The window is Dan's decision. | `cf21a82`, `7170a52` |
| The brake's advice names `make brake-release`. | `spark brake --release` isn't on Dan's PATH. | `d3481ab` |
| `make bootstrap`, `make hold-gpu` and `make upgrade-gpu` end with `sudo -k`. | It closed early a path the plan had left for the council: the clone's code running under a warm sudo. | `25617b8`, `4122330` |
| No `#` in a shell block that runs on the Mac, and every command says where it runs. | Dan's rules, after the Mac's zsh ran a pasted label as a command. | `718dd7c`, `432dfb3`, `f2258f9` |
| `make doctor` has 15 checks, loads a model, and comes after `make status`. | Three checks joined; its end-to-end load clears the last refusal record. | `2d38442`, `4a6ab28` |
| A Docker upgrade can leave the web services stopped. | Found in Task 10's scan: a stop-and-start of Docker leaves their unit down. Not yet seen, since no upgrade has moved Docker. | `1cea890` |
| Dan ran `make apply` on the box. | The auto-mode classifier refused the session's, and the session didn't work around it. | Tasks 12–16 |
| Open WebUI's task calls run without thinking. | Both chat models think without limit by default, and Gemma is the task model. Dan's decision. | `4279a56` |
| Gemma runs `--ubatch-size 2048 --image-max-tokens 1120`. | llama.cpp decodes an image in one micro-batch, and at the default 512 any photo over about 1.2 MP aborted the engine. | `eae863b` |
| The Mac's pi follows Homebrew; the 0.85.1 pin is `agent`'s. | Dan's decision. | `fb96c8d` |
| Every model at its full context; Gemma's two slots share one pool, keep idle caches and cap their checkpoints. | Dan's decision. Its review, reading b11146's source, corrected the first version. | `41d4e89`, `f312ac8`, `2df8e9e` |
| Footprints of 32, 8 and 33 GiB, the coder's checkpoints capped at 8: 76 of the 78 allowed. | A cold load leaves out what grows after admission. | `7b39fa8` |
| S12 is a Phase 1 page, `built`. | Task 15 checked its tmux half, and Task 17 had left it out. | `72af00a` |
| `make deploy` from the Mac is dropped. | Dan's decision at the close. | plan.md's forward look |

## What the reviews found, by theme

| Theme | What was found | Fixed in |
|---|---|---|
| A key that could reach an error, a log or a stranger | The client sent its key through `http_proxy` and printed one holding CR/LF. A Hugging Face token with a control character printed whole in every `FAILED` line, and the library logged a stored one. A failing test could print the shell's environment, Phase 0's tests included. Doctor's probe could send the key to a proxy or through a redirect, and the client the brake uses followed a redirect with it. | `828fb3e`, `32deab1`, `ae9708f`, `be4c3c3`, `92bcc9c`, `d8c1d4c` |
| Refusals that crashed or misled | A malformed registry was a traceback, not a reason. Whole-GiB rounding could make a refusal read as a fit. A damaged hold crashed the launch check. `spark status` called a wrong key "unreachable", inviting a restart that stops every model, and showed HOLDING to anyone who couldn't read the state folder. | `f0f44c7`, `69cc076`, `e575700`, `548ab5c`, `db4aece` |
| The brake failing when it's needed | A failing hold write stopped every unload (Critical). A slow stop cost one more model every 250 ms. Seven safety mutants survived the tests. Noise in `MemAvailable` ended its wait. The deployed unit had never sent its key. | `c249ce7`, `ebd3ad1`, `faee0a3` |
| `spark apply`'s partial runs | After a failed `uv sync`, later applies reported success (Critical). A partial run lost the restarts it owed. Nothing tested `run()`. A unit whose binary started counted as up, even if it then stopped. | `4e639d0`, `5641842`, `81b8124`, `faee0a3` |
| What a registry edit could do | Rebind an engine, put a key in its command, download at start, override a derived setting, read outside the pinned snapshot, add a line to what root runs. Each engine served `/slots` and a web UI without a key, and nothing pinned Open WebUI's lockdown. | `f2ffd3b`, `b26c6e5`, `a0a8612`, `98f5f56`, `99c3359`, `d066044` |
| Upstream behaviour read from a name | v257's unload was read as answered once scheduled; its source answers after the engine exits. `--batch-size` caps `--ubatch-size`, so the embedding model's raise did nothing. `--ctx-checkpoints` is per slot and fills by turns. `--kv-unified` clears idle slots at every task. An image's tokens must fit one micro-batch. | `c249ce7`, `f312ac8`, `2df8e9e`, `eae863b` |
| Memory the accounting missed | Footprints left out prompt caches, checkpoints and image buffers. GPU memory doesn't count toward RSS, so earlyoom would kill the resident Gemma before the coder. Page cache counts as available. | `7b39fa8`; batch B; plan.md, *Page cache and the launch check* |
| Upgrade day | A signal that killed only the way out's hold subshell left the GPU set released while suggesting a start. | `ecd2264` |
| Docs that had become untrue | "Not yet checked" for GRUB in three places after it passed, and for the first sign-up and doctor's web check; "the three residents stayed" when two were loaded; a dropped "not yet measured"; S09's "always-loaded"; the *Always loaded* requirement exceeded without a note; `agent`'s guard tested but only given in chat. | `d71d285`, `f312ac8`, `d2f1537`, `02ed8ab`, `1dbb3d1` |

## Why there were loops

Tasks 1–10 took 15 fix rounds, the context change two, and the council still found 15 Important.
The causes:

1. **A listing that runs can still be wrong.** Phase 0's rule held: every listing ran before it
   went into the plan, and every implementation matched its listing byte for byte. Yet each review
   of Tasks 1–10 found cases the spec rules out and the listing never met: a hold write that fails,
   a sync that fails, an answer not in v257's shape, a token with a control character. Before Tasks
   9 and 10, a read-only scan ran each brief against the spec first, and its rulings went into the
   dispatch. Task 10, the largest, then passed its first review with no Critical or Important
   finding.
2. **Upstream behaviour taken from a flag's name, or from memory.** The five findings in that theme
   above came from llama.cpp's or llama-swap's source, read only after a name, a default or a first
   reading had been trusted. One reached the phone: the default micro-batch was smaller than an
   image, and a photo killed the engine. The session's own
   note that llama-server "lifts the batch cap for embeddings" was wrong, and the context change's
   first version rested on it.
3. **A drill that didn't run the deployed unit.** Task 16 ran `spark brake --once` as Dan, with
   Dan's key. The unit runs as `spark` with its own key, which it had never sent: its journal held
   no line of its own. The done-when criterion was met by the CLI, not the unit.
4. **Test requests unlike real ones.** The context check's 26-token task call began with the
   chat's template tokens, over 10% of its length, so it took the chat's slot; Open WebUI's task
   calls are hundreds of tokens long. Its first run put a 126,000-token chat on `jq`'s command line
   and sent empty bodies. Task 13's 512-token cap left both thinking models with no reply.
5. **A settled item leaves its markers behind.** GRUB's check passed on 2026-09-27 and *To verify*
   marked it resolved, but the plan's upgrade steps, S23 and `updates.md` still said "not yet
   checked" until the council, and deploy.md and a doctor comment said the same of other checks.
6. **What lived only in chat or the ledger.** Dan's first try at `agent`'s guard lost the end of a
   long `jq | ssh` line in copying. The session rewrote it as short commands and tested them on
   stand-ins, but they reached no runbook until the council. Deferred minors and forward items
   piled up in the gitignored ledger, which noted the risk itself after Task 6.
7. **Rulings built on a wrong premise.** At least four of the controller's own readings or rulings
   were wrong and had to be corrected later: v257's unload, Task 4's literal floor rule, Task 8's
   release dates and Task 9's R16. The floor rule alone cost Task 4 a second round. Phase 0's rule
   to check a tool's behaviour against the tool itself covers it: a ruling is a fix too.
8. **Tests that couldn't fail.** A test server rewrote `//`, so one fake couldn't fail (Task 3), and
   the brake ignored `--key-env` while the suite passed with no unloads (Task 4). Probes and
   mutation runs in the tasks' own rounds found them, as Phase 0's rule asks.
9. **Process slips.** Task 1's trailer. A second trailer as a third `-m` split the trailers into two
   paragraphs (amended with Dan's OK). Three subjects ran past 100 characters by the push point,
   and seven of batch A's past 72. The permission classifier refused an auditor's commits, which
   went in with Dan's OK, and batch A's fixer couldn't change `CLAUDE.md` on an agent's say-so.

## Lessons worth keeping

Proposals: promoting any of them to `CLAUDE.md`, as *Lessons from Phase 0* were, is Dan's call.

- **Read the engine's source at its pin before trusting a flag's name, default or scope,** and cite
  the file: `--batch-size` caps `--ubatch-size`; `--ctx-checkpoints` counts per slot and fills by
  turns; v257 answers an unload once the engine has exited (causes 2 and 7).
- **A drill runs the unit as deployed** — its user, its key, its environment — or reads the unit's
  own record (cause 3).
- **A test request has the shape and size real clients send,** and a large body goes through a
  file, never argv (cause 4).
- **When an item settles, grep for every "not yet" it leaves,** and date each one (cause 5).
- **What's learned goes into a tracked file in the same change:** a procedure tested in chat into
  its runbook, a forward item into the plan, never only the ledger; and a command Dan pastes stays
  short (cause 6).
- **Before a dispatch, a read-only scan runs the brief on a scratch copy against the spec;** commit
  1 is the listing byte for byte, and each ruling is a commit of its own (cause 1).
- **A finding that can expose a key is fixed in the task that finds it:** the redirect with the
  key, found in Task 3, waited for the council. Phase 0's security lesson, applied to findings.
- **Every test runs in an environment it builds,** so a failing assertion can't print the shell's
  (`be4c3c3`).
- **A cold load is not a footprint:** the registry says what each estimate counts, growth after
  admission included (`7b39fa8`; the plan's rule 6).
- **With more than one trailer, the message goes through a file,** the trailers in one paragraph
  (cause 9).

## Carried to Phase 2

**Dan's decisions at Phase 1's close (2026-09-28),** as the session recorded them. plan.md's
forward look, 2026-09-28, records them with their reasons.

- *Now, in Phase 1:* render allows only listed engine options.
- *Reversed before it was deployed:* llama-swap sending the engines' output to the journal. The
  check behind it had missed whisper, which logs each upload's file name and its metadata. Phase 2
  revisits it with a check that covers speech.
- *In Phase 2:* llama-swap's port moves to a Unix socket with the gate; the engines and the pull
  get a user of their own; swap and swappiness are measured at the real thresholds first;
  `agent`'s GPU jobs get an OOM score before `agent` runs GPU work; pi 0.87.1 for `agent` after a
  deliberate test; systemd sandboxing for the stack's units and `cap_drop` for the web containers.
- *Dropped:* `make deploy` from the Mac.
- *Kept:* the seven-day window, which now covers pins set by hand as well.
- *S09* is verified on Task 14's phone pass, with the context change's settings checked in the
  Mac's browser.
- *Dependabot's* PRs #1–#3 merge on the Mac after Phase 1's merge (Task 18 Step 7). *(Done
  2026-09-29.)*
- *Phase 1 closes* on the upgrade drill's weak evidence.
- *Every model stays at its full context,* the embedding model's 32,768 included. The settings that
  would save memory are revisited when more models are fitted.

**Rulings at the close, from the council**, which Dan didn't decide himself: earlyoom follows the
brake, with resident engines at `oom_score_adj` 900 and on-demand ones at 1000; the brake's floor
tolerance becomes 1.0 GiB, and `GRACE_S` stays 15 s; the brake checks at start that llama-swap
takes its key (batch A). The council's other recommendations, that llama.cpp follows its formal
releases and that alerts wait for Phase 2's ntfy, went unchallenged; they were never put to Dan as
decisions.

**Still to do before the merge** (as of the close's last commits). The deploy of the council's
fixes: `make apply`, `make install-units` (the unit comments changed), `make apply-now`; until the
new brake has run its start check, `make doctor` fails its `stack units` line. Then Task 17 Step
2's checks, an earlyoom dry run for the new order among them, and the changelog and README
entries. Task 17 Step 3's private findings, which go to the vault through Dan. Task 18: CI's render
step, and the whole suite's first run on a Mac (the folder fsync on APFS, bash 3.2, make 3.81, the
environment each test builds) along with pi.md's two Mac blocks; the merge; then Dependabot's PRs
#1–#3, and the Spark's and `agent`'s clones move to `main`.

**Pending on the box, for Dan.** `agent`'s uv (0.12.19) onto the 0.12.18 pin, or the pin moves.
Whether the first `gh` login's authorization was revoked, and whether the Mac's npm-installed pi
0.85.1 is still there, are not yet recorded.

**Still unverified** (plan.md, *To verify on the box*, unless noted): an upgrade that moves `libc6`
or `libstdc++6`, and one that moves Docker; the GPU set's move on a real upgrade day, which makes
S23 *verified*; pi's crash range; the coder's checkpoint size, the first thing a soak at full
context measures; the CUDA-allocatable ceiling; whether memory swaps out before `MemAvailable`
reaches the brake; a load that has to reclaim page cache (*Page cache and the launch check*); a
busy engine's stop, and the lag before its memory shows in `MemAvailable` (*The minimal brake's
reach*); S09's app install and reply label, marked not yet recorded (`d2f1537`).

**Recorded in plan.md's open items already:** *Engines share llama-swap's user* (the pull and
hf_xet's logs with them), *Phase 1's launch check is a static fit*, *Anyone on the box can take
127.0.0.1:9100*, *The minimal brake's reach* (llama-swap down or hung, and the measured falls) and
*Page cache and the launch check*.

**Deferred minors and forward items.** Every one the ledger, the four council reports and batch A's
report set aside, one line each. Each is as its source gave it; only the closed ones below were
checked against the code again. The last column names where plan.md or phase-1.md also records it,
as of this writing.

| Theme | What's left | From | Also in |
|---|---|---|---|
| Registry | `Registry` is frozen but holds dicts, so hashing it raises `TypeError` | Task 1 | — |
| | `static_total_gib()` sums every model, resident or not; its docstring should say so | Task 1 | — |
| | YAML 1.1 turns `0042` (octal) and `1:30` (base 60) in `args` into numbers before `str()` | Task 1 | — |
| | `roles` aren't checked against the name pattern: `roles: [""]` loads | Task 1 | — |
| | The duplicate-key check refuses a legal nested merge-and-override; check at `flatten_mapping`'s start | Task 1 | — |
| | A bare `=` key raises YAML's `ConstructorError`, not a `RegistryError` | Task 1 | — |
| | An unquoted all-digit 40-character revision passes, stored as an int | Task 1 | — |
| Launch and status | Any model's start clears the one refusal record, doctor's embedding load included | Task 2; toolstack I3 | — |
| | An unreadable `/proc/meminfo` or a failed exec exits 1, not a recorded refusal | Task 2 | — |
| | A failed refusal write leaves `.last-refusal.json.<pid>` behind | Task 2 | — |
| | A folder named `hold.json` holds, and `release_hold` can't clear it | Task 2 | — |
| | A footprint, or a margin, of 1e27 GiB or more crashes Decimal's quantize, in launch and status | Tasks 2, 5 | — |
| | The folder fsync after `os.replace` is unguarded; make it best effort once the Mac run checks APFS | Task 2 | phase-1.md, Task 18 |
| | `spark launch`'s registry catch lacks `RecursionError` and PyYAML's tagged-scalar errors; status's should catch `Exception`, as the brake's does | Task 5 | — |
| | "this account can't read …" doesn't name the remedy for a login older than the group | Task 5 | — |
| | `(no key in $NAME)` also follows an unreachable error; JSON's `last_refusal` null means none, unknown or damaged alike | Task 5 | — |
| llama-swap client | An `InvalidURL` from a redirect's `Location` reads as an invalid base URL | Task 3 | — |
| | `ssl.SSLError` can escape `_call`, on https only | Task 3 | — |
| | A 401's response is never closed (a `ResourceWarning` under `-W error`) | Task 3 | — |
| | Two never-sent paths, `InvalidURL` and `ValueError`, aren't pinned as "not answered" | Task 7 | — |
| | Three blank lines before a parametrized test | Task 3 | — |
| Brake | No test pins remedy 2's keep-in-asked: a mutant that drops it passes, and a starting coder with no answer is asked six times as memory falls (marked "matters") | Task 4 | — |
| | No in-episode test of the stage rule's `asked.discard` | Task 4 | — |
| | Stale docstrings on `_Returning` and `_Brake` | Task 4 | — |
| | A refused connection is logged as "no answer in 2 s" and counted as unloaded | Task 4 | — |
| | The asked rule can't tell a fresh start within one poll of an aborted one | Task 4 | — |
| | The brief expected `ModuleNotFoundError`; Python raises `ImportError` | Task 4 | — |
| | The hold's two fsyncs come before the first unload: write, rename, unload, then fsync | Task 4; reliability m3 | — |
| | A `starting` engine isn't unloaded first, as rule 5 says | reliability m1 | — |
| | A failed unload is passed over for the whole episode | reliability m4 | — |
| | earlyoom runs at nice 0 and `oom_score_adj` 0; `-p` would raise it | reliability m9 | — |
| | Nothing announces a brake event, a hold that survives a reboot, a stopped unit or a failed restart until ntfy | reliability D4 | Phases, Phase 2 |
| | The gate names the top GPU holder outside the stack from nvidia-smi, since a CUDA job's RSS hides its memory | reliability I3 | — |
| Apply and units | Copy before sync: a failed sync leaves the new code live on the old environment (the message says so) | Task 7 | — |
| | No test pins `_show`'s `--timestamp=us+utc`, or uv sync's `--frozen` and `VIRTUAL_ENV` | Task 7 | — |
| | A deferred llama-swap restart deploys the new `models.yaml` beside the old commands | reliability m10 | — |
| | llama-swap's unit has `Restart=on-failure`, so a stray SIGTERM leaves it down | reliability m6 | — |
| | The web services' unit could be `WantedBy=docker.service`, or Docker held with the GPU set | toolstack M8 | Phases, Phase 1; *To verify* |
| | The stack's units have no systemd sandboxing (`NoNewPrivileges`, `ProtectSystem` and so on) | security M2, beside D1 | — |
| | The web containers run as root with host networking, and no `cap_drop` or `no-new-privileges` | security M3 | *Paths that stay open* |
| | A user of their own for the engines and the pull, with the gate | security D1 | *Engines share llama-swap's user* |
| Render and engines | The `--offline` comment, test and commit overstate: a Docker model is resolved before the gate (`-dr` is refused, so the behaviour holds) | Task 6 | — |
| | `IMAGE` accepts a one-part name with a numeric tag, a bare host:port, and `..` or `_` in parts | Task 6 | — |
| | `load_versions` checks no shapes: a list, or `components: 5`, is a traceback | Task 6 | — |
| | Refuse a vision model whose micro-batch can't hold its image; today only a test on the repo's registry holds it | Task 14 | — |
| | `--alias`, so a reply names the registry's model, not the GGUF path | Task 13 | — |
| | A higher `--slot-prompt-similarity` for Gemma: at 0.10 a short new prompt can take a long chat's slot | context change | phase-1.md, Task 17 |
| | A doctor check of each engine's `/props` `n_ctx` against the registry | context review m10 | — |
| | A doctor check that whisper-server finds its libraries; `updates.md` step 7 does it by hand | Task 11 | — |
| | A `TESTED_AGAINST` test tying version-specific code to `versions.yaml`; each component's upgrade runbook, written with its first bump; doctor v1's version-drift checks | toolstack I1 | Revisions, 2026-09-28 |
| | Check the rendered config against v257's embedded schema, since `-validate` ignores a mistyped `swap:`; weigh v259's split logs with the gate | toolstack | — |
| | *Components* rows say more than was built: `CLAUDE.md` pointers, the schema check, `121` vs `121a-real` | toolstack M7 | — |
| The pull | The pyproject-vs-lock test would fail falsely on a dependency with an extra or a marker | Task 8 | — |
| | A token holding `? # < > ( )` in a URL path could leave part of itself; refuse those six | Task 8 | — |
| | A `(` in a URL path before its `?` leaves that URL's query | Task 8 | — |
| | The held-back token note says "reading its token files" for an OAuth refresh failure too | Task 8 | — |
| | The sanitiser's order shows part of a token holding `#<>()` in a URL query (unreachable: tokens travel in a header) | Task 8 | — |
| Tests and CI | The test fixture should set `UV_PYTHON_DOWNLOADS=never` | Task 7 | — |
| | conftest's docstring overclaims for module and session fixtures; `PYTEST_DEBUG_TEMPROOT` is deleted before `mktemp`; `test_hooks` needs uv to collect; blank lines | Task 7 | — |
| | A stale `test_doctor.py` comment on a set-but-empty `SPARK_LLAMASWAP_URL` | Task 10 | — |
| | CI lacks `uv lock --check`, llama-swap `-validate`, `docker compose config`, a check of its `version:` inputs against `versions.yaml`, a pinned shellcheck and a macOS job | toolstack M6 | — |
| | Dependabot could widen `huggingface_hub<2`: `lockfile-only`, or ignore its majors; test that its cooldown matches `exclude-newer` | toolstack I4 | — |
| Versions | `versions.yaml` lacks containerd.io, Tailscale and shellcheck | toolstack M2 | — |
| | llama.cpp's pin covers the binaries tarball, not its cudart companion | toolstack M4 | — |
| | Quarto 1.10.3 is a prerelease; the newest stable was 1.10.18; and Quarto on the Spark | toolstack M3 | — |
| | pi's npm dependencies float, outside the seven-day rule (`npm --before`) | toolstack M10 | — |
| | `b11146/` took the tarball folder's group and no setgid, unlike its siblings | Task 11 | — |
| Web UI and search | SearXNG's `outgoing.request_timeout`: duckduckgo takes 2.7 s against 3 s, and the cold first search found nothing | Task 13 | — |
| | The emoji task call still thinks; it caps at 4 tokens | Task 13 | — |
| | One `404` on `/v1/chat/completions`, its model logged by neither side | Task 14 | — |
| | Gemma's RSS grew from 1.6 to 5.7 GiB after long generations, unexplained | Task 13; context review | — |
| Security | `make install-units`' screen passes bidi controls and zero-width characters | Task 9; security M1 | — |
| | The `git status` check before a sudo target guards only against accidents, and sudo runs the clone's own scripts: accepted | Task 9; security | *Paths that stay open* |
| | `GET /props`, the engine's read-only settings, still answers without a key | batch A | *127.0.0.1 is not a boundary against `agent`* |
| | DGX OS's own dashboard, part of it as root, and CUPS listen on loopback, outside this repo | security M4 | — |
| Docs wording | tailscale.md: "On that home Linux machine" comes before the machine is introduced; "Tag the Spark, on the Spark:" | docs task | — |
| | updates.md: "**On the Spark**, then the GRUB check" reads clunky, and one bold header ends with its machine | docs task | — |
| | secret-files.md: steps 1–7 each repeat "**On the Spark:**" in a section that says so; step 8 folds it in | docs task | — |
| | updates.md and scratch-model.md each have a step with no block beside bolded siblings | docs task | — |
| | pi.md's two Mac blocks ran only under bash on the Spark, never zsh, BSD awk or the Mac's jq *(2026-09-29, Task 18: the jq and awk block ran in zsh 5.9 with BSD awk and jq 1.7.1, its `scp` left out, and built both files as expected: 35 deny rules, none still naming Dan's home, the one hook, and only the secrets section. The `ssh` and `scp` block, which writes into `agent`'s home, didn't run again.)* | batch A | — |

**Closed since.** Of the ledger's 57 deferred-minor lines, five are closed, checked against the
code: a quoted `"false"` and a test that warn sits above brake (Task 1's redo, `69cc076`),
`fullmatch` and a repo's `org/name` (`a0a8612`), and a registry error at launch as a traceback
(`548ab5c`). A sixth, render's incomplete denylist, gave way to Dan's allowlist. Of the forward
items, the Node entry and the Mac's pi in the records went in with batch A, and pi's `400`s on
Gemma at 16,384 tokens a slot ended with the full context.

**From the final review (2026-09-28),** small, code-level, deferred: a doctor check that reads
earlyoom's order (today the dry run is the only check); a value check for whisper-server's
`--tmp-dir`, which the allowlist lets through and whisper-server passes to `/bin/sh` inside
double quotes; three surviving mutants in the start check's tests (`flush=True`, `daemon=False`,
the log lock); and doctor treating a start check still "waiting" long after `KEY_CHECK_S` as
stopped, so a thread that dies can't leave that state for good.

## Starting over

**On a new or rebuilt box**, start as [Phase 0's page](phase-0-retro.md#starting-over) says, and
follow the How-to page's [*In order*](../how-to/index.qmd#in-order) to its end, now eight steps.
Before step 7, install the engines as [Phase 1's plan](phase-1.md), Task 11, does; there is no
runbook yet, and `updates.md` says each component's is written with its first bump. *(Corrected
2026-10-07: nine steps since Phase 2a's Task 1 put [ntfy on the Synology](../how-to/ntfy.md) at
step 7, so the engines come before step 8, Deploy the stack.)*
[Deploy the stack](../how-to/deploy.md) then covers your own key on the Spark, `make apply`,
`make install-units`, `make pull`, the start, the web UI's first account straight after it (the
first account becomes the admin) and `tailscale serve`. [pi](../how-to/pi.md) gives `agent` its
guard before its key. Then `make status`, and `make doctor` for 15 of 15. Task 16 Step 4's drill,
a fresh clone and `make bootstrap` after which `make apply` changes nothing and
`make install-units-dry-run` finds nothing, is the check that the repo reproduces the box. Record
each step in `changelog.md` and `README.md` §Current state. S18 is the scenario for this.

**On the Mac:** Phase 0's list, plus coreutils, whose `timeout` the install-units tests run, and
Homebrew's pi with `make clients` and `make tunnel` ([pi](../how-to/pi.md)).

**Read first:** `CLAUDE.md`; [the plan](plan.md), with its forward look; this page and Phase 0's;
`README.md` §Current state; then the phase plan being executed. In [Phase 1's plan](phase-1.md),
every listing a review changed is marked *Superseded*, and dated notes say what the box showed.
The code wins.

**A fresh Claude session** reads `CLAUDE.md` by itself; point it at the plan and this page before
it proposes anything. Subagents run on Opus, and their commits carry the phase plan's trailer. On
the Spark, Dan runs the commands Claude Code's auto-mode classifier refuses a session, `make apply`
among them; a session stops and asks rather than work around a refusal.

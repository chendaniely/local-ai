---
title: "Projects that use the stack"
description: "Every project of mine that relies on the Spark, what each one relies on, and where it finds its notices before changing how it calls the Spark."
---

I built several projects before this stack was finished, each as I needed it, and they keep
changing in parallel with it. This page lets them stay aligned and know about each other: what the
stack offers them, which project relies on which part, and where each finds the changes it still
has to make. The harnesses themselves (Pi, OpenCode, Hermes Agent, Posit Assistant) and the
diagrams are on [the Architecture page](../architecture.qmd#harnesses).

**How it's used.** Each project's own `AGENTS.md` or `CLAUDE.md` points here. A session there reads
its row and its open notices before changing how the project calls the Spark, and records a new
need here or in [the plan](../design/plan.md). A session here that changes a part of the contract
updates its row and opens a notice for every project that relies on it, in the same change
(`CLAUDE.md`'s sync table).

**Notices are issues in this repository,** one label per project ([Notices](#notices)). A notice
holds contract facts only: what changed, where, and what the project has to do. It never holds
addresses, key values, command output or anything private. **A security gap is never a notice:**
these issues are public, so a gap that could be exploited goes to me and to the vault's notes for
this stack, and a notice, if one is needed, says only what to change. **The project's own session
closes its notice** when the change lands there, with a comment naming the commit. A notice to
every project is closed by a session here, which then opens one per project for what changed. The
Spark session's GitHub token needs permission to open issues for this; that is not yet checked.

## The contract

What the stack offers its projects, and the file that defines each part. The file is the source of
truth; this table says only what a project must not miss.

**As of 2026-11-10**, checked against `phase-2a` at `3d314bd`. A change to a row sets this line to
its date and the commit it was made on.

| Part | Defined in | What a project must not miss |
|---|---|---|
| **The front**, `http://127.0.0.1:9100/v1`, through `make tunnel` on the Mac | the `Makefile`'s `tunnel`; [Reaching the Spark](../architecture.qmd#reaching-the-spark) | It serves the inference routes, plus `GET /v1/models` (its own list) and `GET /health` (no key). Everything else needs a client key. A form upload is capped at 200 MiB. The base URL may change before Phase 2b's build (the plan's open item *The projects' base URL*), so a project keeps it in config. |
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
| **agent-chat** | private | the Mac, then a homelab server; its design adds a Hermes Agent gateway on the Spark | nothing yet: designed, not built. Its local-model bots chat with the models, nothing more, and it may read recordings' results. Its design's gateway on the Spark is the plan's open item *agent-chat's requests*. | one of its own, planned |
| **My Python apps** | none: scripts written from [Python apps and the API key](../how-to/python-apps.md) | the Mac | the front, through `make tunnel`: chat, and the model names. They have no label: a change to the contract corrects that runbook in the same change. | `SPARK_API_KEY`, or one of their own |

## Between the projects

- **vid2sub's parser is copied here.** The speech service is tested against a copy of vid2sub's
  subtitle parser; its docstring, in `spark/tests/vid2sub_parser.py`, names the commit it was
  copied from. A change to those functions in vid2sub means recopying them.
- **skills' class-notes calls spark-menu.** Its `local-models.md` has agents run `spark-tunnel
  menu` to see what is loaded, so spark-menu's move keeps that command working.
- **Waiting out a refusal is written more than once.** vid2sub has it, class-notes has a stub (the
  docstring of its `run_local`, which recordings follows too), and recordings and the Python apps
  plan it. No one owns a shared one yet.
- **agent-chat may read recordings' results,** through recordings' own interface, not through the
  stack. If it does, recordings' interface becomes something agent-chat relies on, and the two
  repositories keep it in step between themselves.

## Notices

Each project's open notices are the open issues with its label. From any machine with `gh`,
`gh issue list -R chendaniely/local-ai -l notice/<project>` lists them; `--state closed` shows the
done ones.

| Project | Its notices |
|---|---|
| vid2sub | [`notice/vid2sub`](https://github.com/chendaniely/local-ai/issues?q=is%3Aissue+label%3Anotice%2Fvid2sub) |
| recordings | [`notice/recordings`](https://github.com/chendaniely/local-ai/issues?q=is%3Aissue+label%3Anotice%2Frecordings) |
| skills | [`notice/skills`](https://github.com/chendaniely/local-ai/issues?q=is%3Aissue+label%3Anotice%2Fskills) |
| spark-menu | [`notice/spark-menu`](https://github.com/chendaniely/local-ai/issues?q=is%3Aissue+label%3Anotice%2Fspark-menu) |
| agent-chat | [`notice/agent-chat`](https://github.com/chendaniely/local-ai/issues?q=is%3Aissue+label%3Anotice%2Fagent-chat) |

The coordination survey of 2026-11-10 opened the first ones ([its record](../design/multi-repo-coordination.md)).
One of them, under every label, says the base URL and the way refusals arrive may change before
Phase 2b's build, so each project keeps both in config or in one module, not spread through its
code.

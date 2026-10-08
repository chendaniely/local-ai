---
title: "The plan"
description: "Goals, constraints, decisions, design, phases and open items for the brightroar model-serving stack."
date: 2026-09-23
---

# The plan

The living plan for `local-ai`: a single GB10 DGX Spark — `brightroar`, a GIGABYTE AI TOP ATOM —
serving open models to Dan's own code, his coding harnesses and his homelab. It records the goals,
constraints, decisions, design and phases, and it changes as the build teaches us things; every
change gets a line under [Revisions](#revisions).

> **History.** This file replaces `planning.md`, the initial plan written the morning the Spark
> arrived (2026-09-23). A requirements interview later the same day — scenario questions, three
> research passes and a four-reviewer council — settled the questions that plan left open, so it was
> retired. The original stays in git history: `git show f62d2c7:planning.md`.

**Outcome.** The Spark becomes a **model server**: chat, vision, embeddings, speech-to-text and
speaker labels behind OpenAI-style endpoints, plus Open WebUI. Loading is **fit-checked** — a model
loads only if it fits, nothing is ever evicted or substituted, and a refusal always says why — and
status is **visible everywhere**. The box is also a **GPU data-science workstation** and a **host for
coding agents in tmux**, running as a locked-down `agent` user, with Synology folders mounted.
Behaviour is written down as **scenarios** on the docs site and kept current as the build proceeds.

## Goals

In order of how much they constrain the design:

1. **Claude stays exactly as it is.** Claude Code and Claude Desktop talk straight to Anthropic on
   the subscription — no performance loss, no added latency, no plumbing changes. *(Refined
   2026-09-28, with Orca: hooks that only report to apps on Dan's own machines are allowed; the
   *Claude is untouched* constraint below words it.)*
2. **The Spark is a model server.** Endpoints for Dan's own pipelines, his coding harnesses and his
   homelab apps, plus a web UI. In Dan's words: *"I just need the endpoints"* — pipelines are his own
   code, written by Claude Code against the endpoint docs.
3. **Any Hugging Face model, on the engine that suits it** — not limited to Ollama's catalog or
   engine.
4. **Private and unmetered.** Recordings and prompts stay in the house; nothing costs per token.
5. **Learning.** Trying a model or an engine must be cheap and safe, and findings get recorded.
6. **Use the whole box.** It is also a GPU data-science workstation and a host for long-running
   coding agents.

## Constraints

| Constraint | Consequence |
|---|---|
| **Claude is untouched** | No gateway, proxy, `ANTHROPIC_BASE_URL` or globally-installed MCP servers. Claude Code on the Spark talking to Anthropic is just a client. Guard hooks that only block, like the secrets guard, are unchanged. Reporting hooks may copy a session's events — prompts and tool inputs included — to an app on Dan's own machines that passes none of them on (Orca, over loopback on the Mac, with its telemetry off; from Phase 2, the gate, and ntfy messages that carry status, never prompt or tool text), as long as they exit 0 and print nothing or `{}` — no decision, no added context or message, no stopped session, no changed tool input — and change nothing about Claude's endpoint, login, model, permissions or flags. An app that installs such hooks adds only its own hook entries to `~/.claude/settings.json`; any other key it adds or changes breaks this rule. Every Claude that Orca starts or resumes runs as it would from a terminal: no `--dangerously-skip-permissions` or other bypass, and nothing in Orca's per-agent environment setting for Claude; Orca's own `ORCA_*` variables, which only tell its hooks where to report, are the exception. (Dan's decisions, 2026-09-28; worded tighter on 2026-09-30, from the spec's review.) Pointing any Claude Code at a local model would be a separate, deliberate command, outside this repo (Ollama on the Mac). *(Corrected 2026-09-28: this said `claude-dgx`, parked; Dan dropped it, since he doesn't need it here.)* |
| **One GB10, ~121 GiB unified memory** | Weights, KV cache, the OS and data-science jobs share one pool. Overcommitting it can hard-freeze the box without an OOM kill (reported; open NVIDIA driver issue #1358). |
| **1 TB of NVMe** | Weights, the Hugging Face cache and container images share it — single-digit large models on disk. |
| **Secrets by reference only** | Names in the repo, values outside it; nothing printed. See `CLAUDE.md`. |
| **Public repo** | No addresses beyond a last octet, no identifiers, no credentials. History is permanent. |
| **Headless box, several clients** | Tailscale is the primary path; the home LAN serves homelab apps; WireGuard covers a device that is logged into a different tailnet. `claude.ai` web connectors cannot reach the tailnet (Anthropic's servers make those calls), and Tailscale Funnel would only ever sit behind real auth. |

## Requirements (decided 2026-09-23)

| Area | Decision (Dan's words where they matter) |
|---|---|
| Role | Model server + web UI. Pipelines are Dan's own code (an audio pipeline today; Pixeltable later). Replaces the initial plan's `spark-tools` FastAPI with MCP/CLI wrappers. |
| Agents | On the Mac **and on the Spark over SSH in tmux**, as a separate **`agent`** user: no sudo, no docker, no access to Dan's home or secrets; writes only its repos and the NAS work folders; its own API key; commits locally — **Dan pushes**. Background jobs run on mounted drives. |
| Data science | Positron over Remote-SSH as Dan, packages installed ad hoc — outside this repo except that it shares the memory pool. |
| Endpoints | chat: a resident small vision model + **two coders (strongest, lighter) picked per session** · vision · embeddings · speech-to-text · **speaker labels** · self-hosted web search (SearXNG) for Open WebUI. PDF chat later. |
| Speech | *"Optimize for English, but make room for other languages or be able to swap."* Vocabulary prompts, word timestamps, ~170 MB uploads (90 minutes of WAV). Works whether Dan's audio pipeline runs on the Mac or the Spark (decided later). |
| Loading | *"If it fits just load it. If it doesn't, tell me what's happening so I can decide. Don't just auto-load a small model where it might seem like you are talking from a large model."* The same rule applies to unattended requests. Doesn't fit → **wait (per key), then refuse** with a reason. |
| Always loaded | Small vision chat + embeddings + interactive speech-to-text (~20–30 GB) — a setting Dan can change. *(Noted 2026-09-28, from Phase 1's council: at full context the residents measured 33.5 GiB cold that day, about 36 GB, and are budgeted at 43 GiB: Gemma 32, the embeddings 8, whisper 3. With the coder's 33, that leaves 2 GiB of the static budget, 78, for later phases' additions. The requirement stands; it is Dan's.)* *(Corrected 2026-10-07: that budget counted the reserve twice, and Phase 2a corrects it (*Admission and memory rules*, rule 9). The whole set fits the CUDA-allocatable ceiling, 102, and with Phase 2a's coder, estimated at 41 (38 until the council raised it the same day), it comes to about 84, so about 18 GiB of the ceiling is left.)* |
| Idle unload | ~30 min by default; in-flight work counts as use; **an active agent session keeps its model**; a "stay loaded while I work" pin; an optional scheduled weekday preload; one-click load; load progress shown. *(Changed 2026-10-07, Dan's decision for Phase 2a: **60 minutes**, for on-demand models only; residents never unload for being idle. The weekday preload, with a work-hours pin, is a setting that is off by default (Dan, 2026-10-05); rule 3.)* |
| Memory conflicts | **Dan decides.** Before a big job, `spark make-room <size>` shows what would unload and unloads only what he confirms. The brake is the backstop: **idle models first**, whatever their class. Batch versus interactive: **Dan first**. *(Phase 2a, Dan's decision, 2026-10-07: make-room then holds the room it freed for him, so nothing automatic takes it back, and requests from his keys wait ahead of `agent`'s: rule 4 and *The front and the gate*.)* |
| Visibility | A menu-bar status line (*"like Claude Code's… always see what model is being used"*) · ntfy on the Mac and an Android phone, including agent done / needs input / failed · `spark status` · the real model name on every reply. A web UI banner is in the backlog. |
| Web UI | Open WebUI, Dan only (others later: Phase 6, 2026-10-07). HTTPS via `tailscale serve`; **Tailscale stays on for the web UI, at home too**. HTTPS on the LAN is in the backlog. On the Mac, the same address runs as a Safari web app (2026-09-28; `website/how-to/deploy.md`). |
| Orca (decided 2026-09-28) | Orca, the desktop app that runs coding agents in panes, each in its own git worktree, **stays on the Mac in its local mode**: the agents it starts run on the Mac, as Dan, and nothing of Orca's runs on the Spark (neither `brightroar` nor `brightroar-agent` is an Orca target). Dan's Claude Code runs in it with Orca's status hooks kept, **Agent Permissions set to Manual** and telemetry off. **pi runs in it and reaches the Spark's models over `make tunnel`**, as it does in a terminal (tested 2026-09-29). Agents started from Orca stop when the Mac does, which Dan accepts: long unattended runs stay in tmux (S12). OpenCode waits for Phase 5. The Spark as Orca's server, and Orca on the phone, are parked (Backlog). |
| Reach | **Tailscale is primary.** The home LAN serves homelab apps. **WireGuard** into the LAN covers a device logged into a different tailnet — pi and the API work then; the web UI waits. The Spark joins the tailnet. |
| Freeze while away | *"Tell me, I'll fix it at home"* → an off-Spark watchdog on the Synology. A GPU clock cap only if freezes unrelated to memory occur. Remote power via Home Assistant later. |
| Gateway | llama-swap's own keys in Phases 1–2; **LiteLLM, locked down, arrives in Phase 3 with Dan's audio pipeline — the first app that needs its own key** — with agreed swap triggers. *(Changed 2026-10-07, Dan's decision for Phase 2a: from 2a the front checks the same client keys on 127.0.0.1:9100, holding only their digests, and llama-swap takes only internal keys (*The front and the gate*). Phase 3 decides whether LiteLLM replaces the front or sits ahead of it.)* *(Noted 2026-10-07, Dan's decision after the implementation plan's forward-and-back council: a third option, no LiteLLM, the front growing what Phase 3 needs, is Phase 3's too; Phase 3's line says what each costs after 2a.)* |
| Models | Keep a mix: the best that fits, plus a policy-safe option (US/EU origin, permissive licence) per slot. Bake-off: speed + **3–5 real tasks via pi** + memory left free. New models: **`spark try` first**, promoted after the bake-off. **Starter coder: Qwen3.6-35B-A3B.** *(Changed 2026-10-07, Dan's decision: "get Qwen3.8 up and working this round … a default coder". In Phase 2a **Qwen3.8-27B replaces it as the coder**, by Phase 5's route A, ahead of the bake-off, which still runs. Qwen3.6-35B-A3B leaves the registry, and its files stay on disk, so it can come back as a trial in 2c or as a fallback.)* |
| Docs and findings | Findings go to the private vault (`zettelkasten/local-ai/`). **`website/` holds only the stack's documentation** (Quarto → GitHub Pages via Actions); Dan blogs on chendaniely.github.io. **Scenarios are living docs.** |
| Claude Code elsewhere | A user-level skill in github.com/chendaniely/skills points at the endpoint docs. |
| Ops | Headless box. Hybrid runtime (Compose + systemd) behind a `Makefile` and the `spark` CLI (Python via uv); tidy repo root. **Weekly upgrade day**, on Saturdays (monthly until 2026-09-24; a skipped week is fine), from automated PRs (built for GitHub Actions and `spark/uv.lock`; `stack/versions.yaml` still by hand — see Backlog); vLLM from NGC unless a model needs newer. Nightly backups to the Synology. |
| Build | **The Spark by default, one session at a time** (2026-09-25): a Claude Code session on the Spark (as Dan, in tmux) writes and tests the code, config and docs and runs everything touching the GPU, memory, systemd or Docker; the Mac session keeps the Mac clients, CI workflow changes and, until Quarto is on the Spark, the site render; Dan runs sudo, logins, secrets and the Synology's settings. (Until 2026-09-25 the Mac session wrote the code, tests and docs.) |
| Parked | Hermes · a MacBook MLX fallback (so there is one gateway) · ~~`claude-dgx`~~ (dropped 2026-09-28; see *Claude is untouched*) · ~~other users~~ (Phase 6, since 2026-10-07) · the web UI banner · Orca on the Spark, and on the phone (2026-09-28; Backlog). |

## Design

### Request flow

The diagrams live on the [Architecture](../architecture.qmd) page: where everything sits, and the
paths a request takes — reaching the Spark, loading a model, apps and speech — with built parts
solid and planned ones dashed, labelled with their phase. *(Replaced 2026-09-30: a text drawing of
the finished design stood here, with nothing marking what was built. Its parts are all on the
Architecture page now, and git history keeps the drawing.)*

- **Phases 1–2:** Open WebUI and pi reach llama-swap directly with llama-swap keys — pi on the Mac
  through an SSH tunnel, so nothing listens on the LAN yet. A refused load is a plain error in the
  client; the explanation is in `spark status` (Phase 1), for an account in `spark-admin`, which
  can read the brake's state, and on the menu bar and ntfy (Phase 2). *(Corrected 2026-10-07, for
  Phase 2a's design: from 2a they reach the front, at the same address, 127.0.0.1:9100, with the
  same keys, and the front forwards to llama-swap on a private port. A load that doesn't fit waits
  for the key's wait, and a refusal then arrives inline, as a normal API error with its reason
  (*The front and the gate*). Phase 1 works as written above.)*
- **Phase 3 onward:** LiteLLM sits in front. Its hook makes refusals inline (`error.code`,
  `retry_after_s`), adds `x-spark-model: <name>@<revision>`, and applies per-key `wait_for_fit_s`.
  *(Noted 2026-10-07: the front makes refusals inline and applies the per-key waits from Phase 2a,
  so LiteLLM overlaps it. Phase 3 decides whether LiteLLM replaces the front or sits ahead of it, or
  isn't used at all: Dan's decision, 2026-10-07, after the implementation plan's forward-and-back
  council.)*
- **Orca (2026-09-28)** is a client on the Mac, not a hop: pi started from it reaches llama-swap
  over the same tunnel as pi in a terminal, and Claude Code started from it talks straight to
  Anthropic.
- **Why not Ollama:** Dan has hit Hugging Face models that won't load there. Here each model runs on
  the engine that suits it, with the engine version pinnable per model, and start failures are
  explained.

### Components

| Component | Runs as / where | Key settings |
|---|---|---|
| **`stack/models.yaml`** (+ gitignored `models.local.yaml` for trials) | repo | real name, roles, capability, resident, engine + pin reference, source@revision, context, `parallel`, `cache_ram`, footprint {peak, steady, config hash}, cold start, idle policy, key access groups. *(Phase 2a, 2026-10-07: also the notification types, each with its priority or `off`, all on by default: *What you see in Phase 2a*.)* |
| **`stack/versions.yaml`** | repo | every pin (image digest; tag + sha256) plus docs URL, context7 ID, changelog and advisory feed → generates the site's Stack page and the doc pointers in `CLAUDE.md`. |
| **`spark` CLI** | Python — a uv project | `render/apply/--check` · `status` · `load/unload/pin/make-room/stop-all` · `try/promote/forget` · `measure/bench` · `doctor` · `keys create` · `backup` · `logs`. The root `Makefile` is the front door. *(Phase 2a, designed 2026-10-07: `stop-all` is `spark make-room --all`; `spark make-room --done` and `make brake-release` both reach the control socket's *release*, which ends make-room's hold or lifts the brake's (rules 4 and 5). On the Spark these commands reach the gate's sockets, with no API key. Since the implementation plan's forward-and-back council, Dan's decision: `spark session hold`, a session for a process that isn't on the Spark, such as 2b's Mac hooks', which ends when its stdin closes.)* |
| **spark-gate** | Python/FastAPI, system unit `User=spark` *(Starlette on uvicorn, not FastAPI: Dan, 2026-10-07)* | Unix sockets: status + ~~session pins~~ sessions (group `spark-users`, includes `agent`; pins are Dan's alone since 2a's design); control (group `spark-admin` = Dan). Admission, brake, idle policy, resident preload (one at a time), events → ntfy, an `OnFailure=` notifier that works without the gate. Phase 1 ships only a **minimal brake** (a memory watchdog that unloads through llama-swap) plus a **minimal launch check** (the brake's hold flag and a static fit), so llama-swap can't reload a model the brake just unloaded; the gate absorbs both in Phase 2. *(Corrected 2026-10-07, Dan's design for Phase 2a, and revised the same day after the council: the gate is `local-ai-gate.service`, uvicorn serving a small Starlette app, not FastAPI (Dan's decision). It absorbs neither the brake nor the launch check. The brake stays `local-ai-brake`, its own small unit, and the gate reads its hold and lifts it (rule 5); `spark launch` starts a model only with the gate's admission ticket, and keeps a zero-wait fit check and the hold check as backstops. Its two sockets are held by systemd, and it knows each caller by its uid; the status socket carries sessions, and pins are Dan's, on the control socket. It loads and unloads through llama-swap with an internal key, and reads the front's in-flight counts; the `OnFailure=` notifier sits on the front, the gate, the brake and llama-swap. *The front and the gate* holds the design.)* |
| **the front** (Phase 2a) | `local-ai-front.service`, system unit `User=spark-front`, a user of its own; 127.0.0.1:9100, held by `local-ai-front.socket` | A small forwarder in front of llama-swap, raw ASGI on uvicorn, with no policy about loads but a route policy: only the inference routes, so llama-swap's admin routes never reach it. The same client keys as llama-swap takes today (Dan's on the Mac, Open WebUI's, `agent`'s), so no client changes, held only as SHA-256 digests; an internal key of its own toward llama-swap, so clients' keys go no further; requests in flight counted per model, until each response ends; for a model that isn't loaded, asks the gate, which holds the request for its key's wait, then forwards or returns a refusal that says why; while the gate is down, forwards to loaded models and refuses only new loads (`gate_down`); limits on memory, bodies, waiting requests and connections; doesn't restart when llama-swap does, and 9100 stays held while it restarts; systemd sandboxing from the start; `OnFailure=` notifier. (Dan's design, 2026-10-07, revised the same day after the council; *The front and the gate*.) |
| **llama-swap** v257 | system unit `User=spark`, 127.0.0.1 | canonical **`routing:`** config; **`swap: false, exclusive: false` on every group** (the defaults evict; render fails on ungrouped models); `apiKeys`; `captureBuffer: 0`; `logToStdout: proxy`, v257's default, pinned: the engines' output, a refused start's reason and an engine's crash included, stays in an in-memory buffer that every restart wipes, and only llama-swap's own lines reach the journal (Dan's decision, 2026-09-28, reversing that day's `both` before it was deployed: whisper-server logs each upload's file name, and its ffmpeg conversion reports the file's metadata, which the journal would keep; Phase 2 revisits it with a check that covers speech); every `cmd` is `spark-launch <model>` (~~from Phase 2~~ since Phase 1: corrected 2026-10-07); no llama-swap preload; ~~**never reloaded while models are loaded** (a v257 reload stops every engine — `spark apply` waits for idle or asks; in Phase 1 it refuses instead, unless `make apply-now`: *Deploy workflow*)~~ (Phase 1 refuses a reload while models are loaded, unless `make apply-now`; 2a's own rule is below); validated with `-validate` and its schema. A separate lab instance serves `spark try`. *(Phase 2a, designed 2026-10-07 and revised the same day after the council: llama-swap stays at v257 and moves to **127.0.0.1:900**, its engines to **800 and up**, both below 1024, which only its unit, given `CAP_NET_BIND_SERVICE`, can bind (Dan's decision); its `apiKeys` are only the internal keys, one each for the front, the gate and the brake. `ttl: 0` stays, so the gate is the only thing that unloads. Since nothing waits inside `cmd` any more, `healthCheckTimeout` has only a load to cover, and goes from 600 s to **180 s** (the session's ruling). `Restart=always`, and its unit is sandboxed (Dan's decision). It is no longer **never reloaded while models are loaded**: `spark apply` waits until no request has been in flight for ~60 s, from the front's counts, then restarts it with models loaded, and the gate reloads the residents (*Deploy workflow*). The journal question above goes to Phase 2b.)* |
| **llama.cpp** | a formal release tag; prebuilt arm64 CUDA 13 or a source build | `--load-mode none` or `dio` (reported: a 120B model loads in ≈22 s this way against ≈2 min through mmap); explicit `--cache-ram` (defaults to 8 GiB per server) and `--parallel`; MTP where supported. Every model at its native maximum context, and a model with more than one slot gives them one shared KV pool (`--kv-unified`), so any one request can use the whole context (Dan's decision, 2026-09-28); idle slots keep their cache (`--no-cache-idle-slots`), and a model whose context checkpoints are large caps them (`--ctx-checkpoints`), counted in its footprint. No `/slots` and no web UI of its own (`--no-slots`, `--no-webui`): an engine takes no key (2026-09-28). Verify `CMAKE_CUDA_ARCHITECTURES` `121` against NVIDIA's `121a-real`. |
| **vLLM** | NGC 26.08 container (26.09 was current on 2026-10-05; Phase 5 pins the newest one a week old); upstream cu130 only if needed | explicit memory caps (the default claims ~110 GiB); fastsafetensors; persisted caches; `restart: no`; `--oom-score-adj=1000`. |
| **whisper.cpp** v1.9.4 ×2 | interactive (resident) + batch (on demand, Phase 3) | `--inference-path /v1/audio/transcriptions`; `prompt`; `verbose_json` word times; Whisper large-v3-turbo and Parakeet TDT v3 GGUF. Two instances, because each transcribes one file at a time. |
| **diarization** (Phase 3) | a small FastAPI wrapper around pyannote community-1 | OpenAI's shape (`response_format=diarized_json`); waveform input (no aarch64 torchcodec wheel); Hugging Face-gated weights (a runbook step). |
| **Open WebUI** | Compose, the standard `v0.11.4` image pinned by digest (the slim build now requires Postgres + pgvector), 127.0.0.1:3000 → `tailscale serve` | SQLite with its embedded vector store; `ENABLE_PERSISTENT_CONFIG=False`; Direct Connections and code execution off; signup off; task model = the resident small model, with thinking off for task calls (`TASK_MODEL_PARAMS`; Dan's decision, 2026-09-28), while chats keep it; embeddings and speech-to-text → the Spark's endpoints; web search → SearXNG. *(Phase 2a, Dan's choice, 2026-10-07, after the design's re-review: `cap_drop: [ALL]`, with only the capabilities it is found to need added back, checked by the web UI still working end to end.)* |
| **SearXNG** | Compose, pinned, 127.0.0.1 | Open WebUI's web search. *(Phase 2a, the same: `cap_drop: [ALL]`, only what it needs added back, checked by a working search.)* |
| **LiteLLM** (Phase 3) | Compose; Docker image pinned by digest, checked with `cosign verify` | admin UI, MCP, JWT and guardrails off; `NO_DOCS`; `turn_off_message_logging`, `disable_error_logs`; no fallbacks, `num_retries: 0`, cooldowns off; readiness health only (`/health` would load every model); keys by access groups generated from the registry; per-key `max_parallel_requests` (batch keys low); a dependency-free hook that checks every call carrying a `model`; Postgres healthy first; Postgres down → fail closed + alert. **Swap triggers:** another critical auth bug · a needed feature moves to Enterprise · the hook breaks on upgrade. |
| **ntfy + watchdog** (Phase 2) | the Synology (Compose in `stack/synology/`) | deny-all + tokens; priorities + quiet hours; the watchdog pings the Spark and its health endpoints. *(Split 2026-10-07: ntfy arrives in Phase 2a, set up by Dan at its start (Dan, 2026-10-05), and the watchdog in 2b. ntfy is reached over the tailnet or the home LAN only, with no public relay: out of reach means no alerts, for now (Dan, 2026-10-07). Quiet hours run 00:00–05:00. Revised the same day after the council: ntfy's server has no quiet hours, so they are set on Dan's phone (*Visibility and notifications*); its image is pinned at **v2.28.0** by its index digest, `sha256:6ef4b819f722fccdc036af611c4774cfdc2de821ab74fdd48bbf4c9d6f8973da`, since v2.29.0 came out on 2026-10-07, inside the seven-day window (the session's ruling); the server sets no `upstream-base-url` and no Firebase key, so nothing leaves for a public relay; and each publisher has a publish-only token of its own.)* |
| **Host** | `stack/host/` | earlyoom (`-s 100,100`, `--prefer` engine process names — note the 15-character truncation, e.g. `VLLM::EngineCor` — and `--avoid` systemd, `sshd.*` (which covers OpenSSH's `sshd-session`) and tmux); `spark-drop-caches` (root-owned, exact-arguments sudo, local filesystems only, with a deadline); apt holds on the GPU set (kernel, NVIDIA modules, driver, CUDA), moved as one on upgrade day; a needrestart override that leaves the `local-ai-*` units alone (Phase 1); ufw SSH only (+ LiteLLM from Phase 3); one secret file per service, 0640 root:spark. *(Phase 2a, Dan's decision, 2026-10-07: the secret files become 0600 root:root, since nothing reads them as `spark` (*Users, access and security*). `spark-drop-caches` isn't built: 2a's page-cache drill decides whether it is (rule 1).)* |
| **Mac and agent clients** | `clients/` (Phase 1: none yet, see *Repo layout*) | SwiftBar plugin (`ssh brightroar spark status --json`; actions over SSH as Dan); pi and OpenCode configs rendered from the registry (real model names, pinned versions — pi outside its llama-server crash range (`agent`'s; the Mac's follows Homebrew, Dan's decision, 2026-09-28), OpenCode 1.18.x — compat flags, `$VAR` keys); harness hooks (~~session pins~~ sessions + ntfy; pins are Dan's alone since 2a's design) for Claude Code, pi and OpenCode on the Mac and as `agent`. *(Phase 2a, Dan's decision, 2026-10-07: `spark clients` also sets `agent`'s pi to wait about 15 minutes for a response, above its 10-minute wait for a load (*The front and the gate*).)* |
| **Orca** (2026-09-28; `website/how-to/orca.md`) | the Mac, in its local mode; nothing of Orca's on the Spark | Agent Permissions → Manual, so no agent it starts or resumes gets its no-prompt flag (`--dangerously-skip-permissions` for Claude), and Orca's per-agent environment for Claude stays empty; telemetry off. Its status hooks in Dan's `~/.claude/settings.json`, which it rewrites at each start, and its extensions in the Mac's `~/.pi/agent/extensions/` are Orca's own. Recorded in `README.md` §Current state, and not in `stack/versions.yaml`: Dan takes its updates as they come, so a version there would be neither a week old (`CLAUDE.md`'s seven-day rule) nor current for long. `website/how-to/orca.md`'s checks follow each update. |

### The front and the gate (Phase 2a)

*Designed with Dan from 2026-10-05 to 2026-10-07, for Phase 2a; nothing here is built yet.
Revised 2026-10-07, after the design's four-lens council (goal-fit, reliability, security,
toolstack), with Dan's decisions on its questions ("all recommended") and the session's rulings,
which he didn't object to; the Revisions lines of that date list them. As first written, the brake
moved into the gate, the front ran as `spark` and held the client keys, llama-swap moved to 9101,
and the front held requests itself; git history keeps that text.* Dan chose a
**two-part gate in front of llama-swap**, over the gate beside llama-swap that this plan first
described (admission inside `spark launch`: rule 1 as first written) and over bringing LiteLLM
forward from Phase 3. The reason is what llama-swap v257 can't do, read in its source on
2026-10-05, at tag `v257` (nothing of it tested; paths are the repo's at that tag):

- It can't tell an engine's `cmd` which key asked. No environment variable, macro, event or hook
  carries it, and its key check forgets which key matched (`internal/server/auth.go:15-40`).
- Every request for a model joins one start, whatever its key, so one key's wait can't differ from
  another's (`internal/router/scheduler/fifo.go:107-112`).
- A `spark launch` refusal reaches every waiting client as a bare `500`, "upstream command exited
  prematurely"; its reason stays in llama-swap's in-memory log
  (`internal/process/process_command.go:556-559`).
- In-flight counts show only in its `/api/events` stream, which drops frames when a connection's
  buffer fills and starts over at every reload (`internal/server/apigroup.go:514-554`).
- A `cmd` that waits is bounded only by the one global `healthCheckTimeout` (600 s here), which
  would then have to cover `agent`'s ten-minute wait plus a load, and would hold everyone's
  requests as long (`internal/config/load.go:39-53`, 138).
- It listens on TCP only: there is no Unix socket (`llama-swap.go:114-124`, 291-299).

The council's toolstack review re-read these against v262 on 2026-10-07: none has changed, and
v258–v262 add no Unix-socket listener, no per-model `healthCheckTimeout`, no load endpoint and no
drain on unload, so an upgrade wouldn't reopen the choice. llama-swap stays at v257 for 2a (the
session's ruling).

Four services, three sockets that systemd holds, and a notifier:

- **The front** — `local-ai-front.service`, new, a system unit running as **`spark-front`**, a
  system user of its own (Dan, 2026-10-07), out of reach of the engines and of
  `spark models pull`, which run as other users. It serves **127.0.0.1:9100** with the **same client
  keys**, so pi, Open WebUI and `agent` change nothing, but holds only their **SHA-256 digests**,
  compared in constant time: after 2a the keys themselves exist only on the clients and in Open
  WebUI's env file. It knows which key asked; counts requests in flight per model, each until its
  response, a stream included, ends, and counts down on every path out (a response finished, a
  client gone, an upstream error); and forwards to llama-swap with an **internal key of its own**,
  so clients' keys go no further. It has no policy about loads: for a model that isn't loaded it
  asks the gate (*A request that needs a load*, below). It does have a **route policy**. It
  forwards only `GET /v1/models` and `POST` to `/v1/chat/completions`, `/v1/completions`,
  `/v1/responses`, `/v1/messages`, `/v1/embeddings` and `/v1/audio/transcriptions`, and answers
  its own `GET /health`. Every other path gets a 404 that says why: llama-swap's admin routes,
  `/unload`, `/api/*` (its unload, cancel and profile calls), `/logs*`, `/upstream/*`, `/running`,
  `/ui` and `/metrics`, never reach llama-swap through it. It reads the model from the JSON body,
  or from a multipart form (a whisper upload), and resolves the registry's role aliases.
  - **Its limits** (from the council's security review): `MemoryMax=` on its unit, so an
    out-of-memory kill stays inside it; bodies up to about 200 MB, enough for the ~170 MB uploads
    *Speech* allows; uploads spooled to disk under its `PrivateTmp=`, not tmpfs, never held in
    memory, and deleted when the request ends; per-key caps on waiting requests and open
    connections, low for `agent`'s and below llama-swap's own limit of 10 per model; a timeout
    for reading headers; a 400 for a request it can't parse, never a crash. Requests are rebuilt,
    not passed through as raw bytes: one with both `Content-Length` and `Transfer-Encoding` is
    refused, `Authorization`, `x-api-key`, `Cookie` and `Proxy-Authorization` are stripped before
    the internal key is added, and no upstream connection is shared between keys. *(Added
    2026-10-07, after the re-review:)* a JSON body goes on re-serialized from what the front
    parsed, and a form with two `model` fields is refused, since llama-swap takes the first value
    where Python's parser keeps the last, and the two would then disagree on the model. A model
    the registry doesn't list gets a `404`, code `model_not_found`, that names the registry's
    models, the old coder's name after the swap included.
  - **Its journal lines** name the key, the model, the status and the duration, never a key, a
    digest, a body, a header or an upload's file name, so it doesn't undo what the llama-swap row
    keeps out of the journal.
- **The gate** — `local-ai-gate.service`, a system unit as `spark`, on two Unix sockets: the
  **status socket** (group `spark-users`: status and sessions) and the **control socket** (group
  `spark-admin`: load, unload, pin, make-room, release, and the reads Dan's commands make). A
  socket's group says who may connect, not who did, so the gate reads each caller's uid
  (SO_PEERCRED) and authorizes each kind of request. uvicorn's ASGI scope carries no peer
  credentials (its `client` is empty on a Unix socket), so a small uvicorn protocol subclass,
  passed as its `Config(http=…)`, reads SO_PEERCRED from the accepted socket and puts the peer's
  uid and gid into the scope, where the Starlette gate checks them (added 2026-10-07, after the
  re-review). It leans on uvicorn's internals, so uvicorn is pinned, and a test, run again at each
  bump, checks that the caller's uid arrives and that an in-flight report from any uid but
  `spark-front`'s is refused:
  - the front's admission requests and in-flight reports are taken only from `spark-front`, which
    reaches the status socket as a member of `spark-users`;
  - status is open to `spark-users`, without other keys' refusal texts or the names of Dan's own
    processes, which the control socket's status keeps;
  - a session belongs to the uid that registered it and is tied to one of that uid's live
    processes; each uid has a few at most, and each lasts only while it's renewed. A session is
    how `agent` keeps its model;
  - pins are Dan's only, on the control socket;
  - only the owner, or `spark-admin`, ends a session or a pin. make-room and the brake override
    both, and `spark status` says whose they were;
  - every request has a size cap and a timeout, so no caller can hang or crash the gate.

  It makes every decision but the brake's firing: admission and its queue, idle unloading, pins,
  sessions, the residents' preload, make-room and its hold, the brake's release and the reloads
  after it, notifications, and the quiet moment `spark apply` waits for. It loads a model through
  llama-swap's `GET /upstream/<model>/health`, which answers once the engine is ready, after
  issuing that load an **admission ticket**. It unloads one with `POST /api/models/unload/<model>`,
  only once the front has drained it. llama-swap's own `ttl` stays 0, so nothing else unloads. Its
  notifications are queued and sent apart from everything else, each with a short timeout, so an
  ntfy out of reach stalls nothing. It keeps its state across a restart: pins, sessions, make-room
  holds, and each loaded model's footprint and what its load took (rule 9). After a restart it
  counts any model llama-swap shows starting as pending, treats every loaded model's last use as
  now, so a restart can't cause an idle unload, and waits for llama-swap with a backoff.
- **The brake stays its own unit**, `local-ai-brake`, as in Phase 1 (Dan, 2026-10-07): a small
  synchronous loop in a process of its own, sharing no event loop and no fate with the gate, so a
  gate that crashes or hangs never means "no brake". It never waits on the gate, a socket client or
  ntfy. It fires as rule 5 says, writes the hold, unloads through llama-swap with an internal key of
  its own, and records each step, which the gate turns into notifications. *(Ruling 2026-10-07,
  after the re-review, Dan agreeing:* when the gate's record is stale, so the gate is down, the brake
  sends "brake fired" to ntfy itself, by the failure notifier's path: Ubuntu's `/usr/bin/curl`, not
  the app's venv, with `--max-time`, its own publish-only token as a header file from
  `LoadCredential=`, and ntfy's address from the private values file. It starts curl and doesn't
  wait on it, so the alert can't stall the loop. Dan then hears "brake fired", not only "gate
  down".) For its idle-first order
  it reads the in-flight counts and last uses the gate writes to a small file every second; when
  that record is missing or stale, it falls back to Phase 1's order, on-demand models first, then by
  size. The gate reads the hold, refusing loads with `held_by_brake` while it stands, and writes it
  to lift it (rule 5); `make brake-release` lifts it too.
- **llama-swap** v257 moves to **127.0.0.1:900**, and the engines from 5800 to **800 and up**,
  below 1024, so no unprivileged user can bind either, during a restart or at any other time (Dan,
  2026-10-07; both ranges were free on the box that day). Only llama-swap's unit gets
  `CAP_NET_BIND_SERVICE`, through `AmbientCapabilities=` and `CapabilityBoundingSet=`, and the
  engines inherit it across `spark launch`'s exec, which 2a checks on the box. Its `apiKeys` are the
  **internal keys** only, one per caller: the front's, the gate's and the brake's.
  `healthCheckTimeout` goes from 600 s to **180 s** (the session's ruling): the front no longer
  waits inside a load, so it covers only a real load, and one stuck load no longer blocks every
  load for 10 minutes. `Restart=` becomes `always`, since llama-swap answers a stray SIGTERM with a
  clean exit, which `on-failure` left down. Its unit is **sandboxed** (Dan, 2026-10-07):
  `NoNewPrivileges=`, `ProtectSystem=strict` with `ReadWritePaths=` for whisper's `--tmp-dir` and
  the caches, `ProtectHome=`, `PrivateTmp=`, `InaccessiblePaths=/etc/local-ai/secrets`,
  `RestrictSUIDSGID=`, `ProtectKernelTunables=` and `ProtectControlGroups=`, but not
  `PrivateDevices=` or `MemoryDenyWriteExecute=`, which CUDA needs. `NoNewPrivileges=` is tested
  from a cold boot, in case CUDA needs the setuid `nvidia-modprobe` to load `nvidia-uvm`. Otherwise
  llama-swap is unchanged. Every engine still starts through `spark launch`, which now starts a
  model only with a ticket the gate issued for it, and uses the ticket up (the session's ruling):
  nothing starts without the gate, starts stay one at a time, and a crashed or unloaded model that
  the front forwards a request for can't start around it. `spark launch` also keeps its zero-wait
  fit check and the brake's hold check, and refuses with `not_downloaded` a model whose files
  aren't there yet. *(Added 2026-10-07, after the re-review:)* the cold-boot test covers
  `CapabilityBoundingSet=` too, which strips `nvidia-modprobe`'s setuid powers just as
  `NoNewPrivileges=` does; loading `nvidia-uvm` at boot would answer both. A ticket expires at its
  load's deadline, so one issued for a load that never reached llama-swap can't start a model
  later. Tickets and `spark launch`'s refusal records get a folder of their own, the only state
  `ReadWritePaths=` opens to llama-swap's sandbox; the brake's hold folder is read-only there, so
  an engine can no longer delete the hold.
- **Sockets from systemd** (Dan, 2026-10-07): `local-ai-front.socket` holds 127.0.0.1:9100, and
  `local-ai-gate-status.socket` and `local-ai-gate-control.socket` hold the gate's two, each with
  its `SocketUser=`, `SocketGroup=` and `SocketMode=0660`. PID 1 holds them from boot, so 9100 is
  never free, even while the front restarts or crashes: new connections wait in the kernel's
  backlog. A gate restart likewise queues the front's calls rather than failing them. *(Corrected
  2026-10-07, after the re-review: "never free" holds only if neither the socket nor its service
  gives up in a crash loop. A socket unit that passes its trigger limit, 20 activations in 2 s by
  default, fails and closes its socket (`systemd.socket(5)`), and systemd stops a socket whose
  service has hit its start limit. So the three socket units set `TriggerLimitIntervalSec=0`, and
  the front, the gate and the brake set `StartLimitIntervalSec=0` with `RestartSec=2`: a crash
  loop restarts every 2 s, for as long as it lasts, and never gives up. 2a checks it as `agent`.)*
  Each service
  takes its sockets from `LISTEN_FDS` as `socket.socket(fileno=…)` and hands them to uvicorn's
  `Server.run(sockets=…)`; never `--uds`, which leaves a socket file at mode 0666, or `--fd`,
  which treats a TCP socket as a Unix one and so skips `TCP_NODELAY` (uvicorn 0.54.0's source,
  read by the toolstack review).
- **The failure notifier** — `local-ai-notify@.service`, a template unit that `OnFailure=` starts
  for the front, the gate, the brake and llama-swap, and that works when the app doesn't: it runs
  Ubuntu's `/usr/bin/curl`, outside the app's venv, with `--max-time` and `--fail`, as a user of its
  own (`DynamicUser=yes`), with no access to `spark`'s state. Its ntfy token reaches it through
  `LoadCredential=` as a header file, which curl reads with `-H @file`, so it never sits on a command
  line; ntfy's address comes from the private values file. It sends the unit, its result and the
  time, never a journal line, and at most one alert per unit in a set interval, so a crash loop
  doesn't page the phone every few seconds. What fires it: `OnFailure=` runs on each crash, under
  systemd 255's default `RestartMode=normal` (its man page; S14's drill checks it on the box). A
  hang becomes a failure through `Type=notify` and `WatchdogSec=` on the front and the gate, fed
  from their event loops. llama-swap has no watchdog, so the gate alerts when it stops answering. A
  clean exit fires nothing, and `Restart=always` brings the unit back.

**How the units are set.** The front and the gate run `Restart=always` and `OOMScoreAdjust=-900`,
like the brake, as `spark front` and `spark gate`, so their process name stays `spark`, which
earlyoom's `--avoid` matches. The three of them set `StartLimitIntervalSec=0` and `RestartSec=2`,
so none is ever left failed by a crash loop, and the three socket units set
`TriggerLimitIntervalSec=0` (added after the re-review; *Sockets from systemd*). llama-swap's
`RestartSec=5` can't reach the default start limit, 5 starts in 10 s. The front has only `Wants=` and `After=` on llama-swap, never
`Requires=`, `BindsTo=` or `PartOf=`, which would pass a llama-swap restart on to it. Both are
sandboxed from the start. The front gets `IPAddressDeny=any` with `IPAddressAllow=localhost`. The
gate needs ntfy's address, `/dev/nvidia*` and other users' processes, for `nvidia-smi`'s list of
the top holders, so it gets neither `PrivateDevices=` nor `ProtectProc=invisible`. uvicorn's
graceful shutdown is bounded below each unit's `TimeoutStopSec=`.

**What they're built on** (Dan, 2026-10-07): **uvicorn** serves both. The front is about 200 lines
of raw ASGI with no framework, so a framework's Host-header or form-parsing bug can't reach its
routing; the gate is a small **Starlette** app. Both talk to llama-swap with **httpx**. That adds
two packages to `spark/uv.lock`, uvicorn and Starlette, under the seven-day window; h11 and httpx
are already locked, through huggingface_hub, which keeps httpx below 1. FastAPI, which the gate's
row named, would add nine, the compiled pydantic-core among them. httpx runs with
`trust_env=False`, follows no redirect, and sets no read timeout on a forwarded request, since a
long prefill can be silent for minutes. The gate's load call waits at least `healthCheckTimeout`
plus the 5 s llama-swap takes to kill a stuck start, plus a margin, never Phase 1's 10 s; on any
timeout or error it keeps the load counted as starting until `/running` shows it ready or gone, so
it never frees the one-load slot early. Blocking work in the gate, such as `nvidia-smi` and file
reads, runs off its event loop. The CLI talks to the gate with the standard library (`http.client`
over a Unix socket), so `spark status --json`, which 2b's menu bar polls over SSH, and
`spark launch` never import uvicorn or httpx. It stays one uv project and one lock, deployed to
`/opt/local-ai/app` as today.

**The keys** (from the council's security review; Dan's decisions on the users):

- **Client keys:** the front holds only their digests, which reach it through `LoadCredential=`,
  and `llama-swap.env` drops the keys. Dan makes the digests with a sudo one-liner that shows no
  key, like the hash check in `website/how-to/secret-files.md`.
- **Internal keys,** one per caller of llama-swap, the front, the gate and the brake: each reaches
  its caller through `LoadCredential=`, so it is in none of their environments, and llama-swap reads
  all three from its `EnvironmentFile=`, since its config takes `${env.…}`. Their names carry the
  `LLAMASWAP_KEY_` prefix, so `spark launch` keeps them out of the engines' environment.
- **ntfy tokens:** publish-only, one per publisher, the gate and the notifier, through
  `LoadCredential=`; and the brake, for its own alert while the gate is down (added after the
  re-review).
- **The secret files** become `0600 root:root`, in a folder only root reads, since nothing reads
  them as `spark`: PID 1 reads `EnvironmentFile=` and `LoadCredential=`, and root's Compose reads
  `env_file` (Dan, 2026-10-07). Neither the engines nor the pull can then read a secret from disk.
  Doctor's check and `secret-files.md` change with it.
- **Dan's commands need no key.** On the Spark, `spark status`, `spark apply`, `make doctor`,
  make-room, pins, load, unload and release use the gate's sockets, where the socket's group and
  the caller's uid decide, and Dan reads what's loaded, and llama-swap's log, through the control
  socket. `LLAMASWAP_KEY_SPARK` retires. *(Corrected 2026-10-07, after the re-review: `make
  doctor`'s end-to-end checks through the front, the real-client refusals, the privacy canary and
  the 170 MB upload, still use Dan's client key, `SPARK_API_KEY` on the Spark, as today; its other
  checks, and the commands above, need none.)*
- **What stays open:** an engine, running as `spark`, can still read llama-swap's environment,
  which then holds only the internal keys, and can write the gate's and the brake's state, a ticket
  or the hold (*Engines share llama-swap's user*). *(Narrowed after the re-review: the engines run
  inside llama-swap's sandbox, which leaves them only the tickets' folder, whisper's tmp-dir and the
  caches to write, so an engine can still forge a ticket, but can no longer delete the hold or
  change the gate's state.)*

**What each failure costs:**

| Failure | What keeps working | What stops | Who hears |
|---|---|---|---|
| The front crashes or restarts | 9100 stays held, and new connections wait in the backlog, through a crash loop too, since neither the socket nor the front ever gives up | every request in flight, streams and waiting requests included | the notifier, on a crash; a hang trips the watchdog first |
| The gate is down | the models already loaded, through the front; the brake, which then sends its own alerts | every load (`gate_down`), idle unloads, make-room, the brake's release, every notification but the notifier's and the brake's. At boot, or after a restart that stopped every engine, nothing can load, so the API is down in effect (the session's ruling; rule 8) | the notifier; the brake's own "brake fired" |
| llama-swap is down, outside `apply`'s restart | the front and the gate; once it answers again, the gate reloads the residents by themselves, one at a time (Dan's decision, 2026-10-07, after the implementation plan's forward-and-back council; the same for a resident lost to an earlyoom kill or a crash) | every model: requests wait for their key's wait, then are refused with `llama_swap_down`; the brake can hold new loads but not unload (*The minimal brake's reach*) | the notifier, on a crash; the gate, when it stops answering |
| The brake is down | everything else; earlyoom | freeze protection, for the 2 s its restart takes | the notifier |

A deploy restarts each of them only as *Deploy workflow* says, and never cuts off a request in
flight without Dan's say.

**Not in 2a:** a user of their own for the engines. A process that isn't root can't start engines as
another user, and llama-swap runs as `spark`; render's allowlist of engine options stands meanwhile
(*Engines share llama-swap's user*). *(Corrected 2026-10-07, after the council: Phase 2's "not yet
placed" line gave the same reason for `spark models pull`, where it doesn't hold, since the pull is
a systemd oneshot that can run as a user of its own; 2a gives it one, `spark-pull` (Dan's decision).
And once 2a is built, llama-swap holds only internal keys and no state, so llama-swap itself could
run as the engines' user, with the gate and the brake staying `spark`: that is the route when this
comes.)* A private network namespace for llama-swap and its engines was weighed too (the v257
research's option (d), unverified on this box); it stays in view for Phase 3's decision on who can
reach the engines. **For Phase 3:** the front overlaps LiteLLM, which is itself a proxy in front
with per-key limits; Phase 3 decides whether LiteLLM replaces the front or sits ahead of it, or
isn't used at all (Dan's decision, 2026-10-07, after the implementation plan's forward-and-back
council; Phase 3's line).

**A request that needs a load** (revised 2026-10-07 after the council). The front asks the gate
once, *admit this model for this key*, and the gate answers when the model is ready, or with a
refusal; the queue and its rechecks live in the gate alone. Requests from Dan's keys go ahead of
`agent`'s, first come first served within each (since Dan's decision of 2026-10-07, after the
implementation plan's forward-and-back council, a key group's `queue` rank decides, with its four
other settings apart: *Users, access and security*), and the gate admits **one load at a time**
(rule 1), when the model fits by rule 9's formula: its footprint against `MemAvailable`, less the
reserve, the growth the loaded models are still owed, the footprint of any model still starting
and any make-room hold, which Dan's own keys may load into (rule 4).

**The key's wait**, defined here once, and rule 7 points here. It is **30 s for Dan's keys** (pi on
the Mac, the web UI) and **10 minutes for `agent`'s** (Dan, 2026-10-05), set in the registry's key
groups, which the gate reads. It runs from when the front receives the request, and it covers
everything before a load starts: waiting for memory, for the brake's hold to lift, for the one-load
slot while other models load (after `make apply`'s restart or a brake's release, the residents
reload one at a time ahead of it), and for llama-swap to answer again during a restart. A load,
once started, is always waited for, bounded by its deadline, `healthCheckTimeout`, and doesn't
count against the key's wait. A request for a model already loading joins that load and waits for
it the same way. A client that has gone, whose disconnect the front sees, is dropped at once, from
the queue and before any load is admitted for it; a load already started for it finishes, and the
model stays until its idle time. If the key's wait runs out first, the client gets a **refusal that
reads like a normal API error**: a ~~`503` with `Retry-After` and~~ `409` (a `503` only for
`gate_down`), with `x-should-retry: false`, which OpenAI's SDKs read, so a refusal isn't silently
retried, and a `Retry-After` where its code has one; one of rule 7's codes; and text that gives
the memory needed against what's free for a load, after the reserve, the growth owed and any
make-room hold, the top holders, and the options (`spark make-room <size>`, or retry). So S03's explanation shows
inline in the client from 2a, not from Phase 3. Each code's message, word for word, is under *What
you see in Phase 2a*. pi's and Open WebUI's handling of the status, the
retry and the text is checked before `website/design/phase-2a.md` fixes them. *(Checked 2026-10-07,
from their source, for that plan (its *Before Task 1*): Open WebUI v0.11.4 passes the status and
the JSON body on, with no retry, and shows the error's `message`, so the error carries no `detail`;
pi 0.85.1 and 0.87.1 show the error object as JSON after the status, so it carries only `message`
and `code`, and pi's own auto-retry, on by default, retries a turn whose error text holds "503" up
to three times, 2, 4 and 8 s apart, whatever `x-should-retry` says. The S03 drill confirms it on
the box. Ruled the same day, from that check, Dan having left the UX to the session: a refusal that
comes after a wait — `no_fit`, `loading`, `held_by_brake`, `footprint_suspect` — is a `409`, with
the same body and sentence, since pi's list holds "503" but not "409", so Dan's 30 s refusal
would otherwise reach him after about 2¼ minutes; ~~an outage — `gate_down`, `llama_swap_down`,
`restarting`, `draining` — stays a `503`, where a retry makes sense;~~ `load_failed` and
`not_downloaded`, first left `503`s, are `409`s too (ruled again the same day: neither is transient,
since a retry of `load_failed` repeats a full load and `not_downloaded` changes only with
`make pull`). Every `409` and `503` keeps `x-should-retry: false`, since OpenAI's SDKs retry
both by default, and `Retry-After` comes only with a code's retry-after. *Decided by Dan the same
day, after the implementation plan's forward-and-back council:* `restarting`, `llama_swap_down` and
`draining` are `409`s too, since they also come after the key's wait, so pi would hold them as
long; only `gate_down`, which the front answers at once while the gate is down, stays a `503`,
which pi retries.)*

`agent`'s pi has to wait that long. pi 0.85.1, `agent`'s pinned version, gives up on a request that
has no response headers after `httpIdleTimeoutMs`, 300,000 by default, half `agent`'s wait (its
published code, read by the toolstack review). So `spark clients` writes **about 15 minutes,
900,000,** into `agent`'s pi settings, and the refusal reaches it (Dan, 2026-10-07). The Mac's pi
keeps its default, well above 30 s and a load; time to first token at long context is measured
against both (2a's coder item).

If llama-swap answers a forwarded request with "upstream command exited prematurely", the model
crashed or the front's view of what is loaded was stale, and `spark launch` refused it for want of
a ticket. The front then asks the gate, as for any model that isn't loaded, and forwards the
request again from the body it holds. llama-swap's own limit per model, 10 requests waiting or
served, answers a `429` with `Retry-After`, which the front passes on.

*Added 2026-10-07, after the re-review:* a gate restart drops the admission calls it held, so the
front asks again for each, with the request's original deadline, and no key's wait starts over.
Since systemd holds the gate's sockets, the front never sees a refused connection: it counts the
gate as down once its long-lived call to the status socket has dropped and a new one hasn't been
answered within 5 s, and only then refuses new loads with `gate_down`.

**Draining a model.** Idle unloading, make-room and `make apply`'s "drain now" never cut off a
request in flight (rule 4), since llama-swap's unload kills it. The gate asks the front to drain a
model, over a call the front keeps open on the status socket, a Unix socket, so nothing else can
answer for the front. The front, under one lock, checks the model's in-flight count and marks it
draining, so new requests for it wait, as they would for a load; it answers "drained" once the count
reaches 0. The gate then unloads it and tells the front, whose waiting requests go through
admission like any other. A drain the gate doesn't finish within 30 s goes back to serving, and so
does every drain if the front's call to the gate drops. `spark status` shows each model's oldest
request in flight, so a count that leaked would show. *(Corrected 2026-10-07, after the re-review:
the 30 s run from the front's "drained", not from the start of the drain, which waits as long as
the requests in flight take, as rule 4 promises. A request that arrives for a draining model and
outlasts its key's wait is refused with `draining`.)*

The rules for what unloads, and when, are rules 3–5 below; the budget is rule 9; what Dan sees is
under *Visibility and notifications*; and `make apply`'s wait is under *Deploy workflow*.

### Admission and memory rules

1. **Admission happens where engines start.** `spark-launch` asks the gate; a model fits when
   `footprint.peak ≤ available − reserve − pending`, where *available* is `MemAvailable` capped by
   the CUDA-allocatable ceiling (reported near 102 GiB; to be measured). One load at a time; caches
   are dropped (local filesystems, with a deadline) before loading. *(Corrected 2026-10-07, Dan's
   design for Phase 2a, and revised the same day after the council: admission happens before a
   request reaches llama-swap. The front asks the gate, which admits on rule 9's formula, one load
   at a time, issues a ticket for each load it admits, and holds the request for its key's wait.
   `spark launch` starts nothing without a ticket, and keeps a zero-wait fit check and the brake's
   hold check as backstops. llama-swap v257 gives `spark launch` no way to know which key asked, or
   to explain a refusal to the client: *The front and the gate*. Caches aren't dropped before a
   load, and nothing yet builds the helper the Host row names: 2a's page-cache drill decides among
   dropping them before a load that needs more than `MemFree`, through that root-owned helper, which
   `spark` would run by an exact-arguments sudo rule; `--load-mode dio`; or nothing. Until then a
   load's deadline, `healthCheckTimeout`, bounds a stall. Corrected after the re-review: no sudo
   rule can run under `NoNewPrivileges=`, which llama-swap's unit, where `spark launch` runs, and
   the sandboxed gate both have; the route, if the drill needs it, is a root oneshot unit that
   polkit lets `spark` start by its exact name. Noted after the implementation plan's
   forward-and-back council: the drill decides for llama.cpp's GGUF loads only; Phase 5's
   safetensors loads, where E.1 was seen, get a drill of their own (Phase 5's cautions).)*
2. **Never evict, never substitute.** The only automatic unloads are the idle policy and the brake.
3. **Idle policy:** 30 minutes by default; in-flight requests and active agent sessions count as use;
   pins (manual, work hours); scheduled preloads are fit-checked and notify if they don't fit.
   *(Changed for Phase 2a, Dan's decisions: an on-demand model unloads after **60 minutes** with no
   request and no active session, and resident models never unload for being idle (2026-10-07).
   Pins are `spark pin <model> [duration]`. There is no schedule by default: a weekday preload with
   a work-hours pin is a setting, off by default, fit-checked, that notifies if it doesn't fit
   (2026-10-05). Revised the same day after the council: pins are Dan's only, set on the control
   socket; `agent` keeps its model with a session, which is its own, tied to a live process, capped
   and expiring (*The front and the gate*). Ruled 2026-10-07, with 2a's plan: the weekday preload
   isn't built in 2a; it waits in the Backlog until Dan asks for it.)*
4. **make-room** lists candidate unloads with their sizes and unloads only what Dan confirms.
   *(Phase 2a, Dan's decision, 2026-10-07: `spark make-room <size>` lists everything it could
   unload, **residents included**, largest first, and unloads what Dan confirms; **`--all`**
   unloads everything after one confirmation, Dan's clean slate for testing. **No request is cut
   off** by make-room or an idle unload: each waits for the model's requests in flight to finish,
   and new requests for that model wait in the front meanwhile, since llama-swap's unload kills
   requests in flight and briefly stalls every model. Only the brake may cut one off. Revised the
   same day after the council, with Dan's decision on the hold:)*
   - **What it frees.** `spark make-room <size>` works out what must unload so that
     `MemAvailable`, less the reserve and the growth the loaded models are still owed (rule 9),
     reaches `<size>`, that is, until `<size>` is *free for a load*: Dan's job can then take all of it and leave the reserve free, above the
     brake. *(Corrected 2026-10-07, at the implementation plan's Task 6's review, the controller's
     ruling: make-room starts from the gate's own figure for free for a load, rule 9's, which also
     counts the CUDA ceiling and any model still starting. Counting `MemAvailable` alone, where the
     ceiling binds, `spark make-room 41G` could find nothing to unload, and the retry would be
     refused again.)* It lists everything it could unload, pinned models and those an agent's session holds
     included, each marked, largest first, with each one's requests in flight and how long they
     have run. Asked for more than unloading everything can free, it says so and shows the most
     it can free, and unloads nothing unless Dan confirms that (added after the re-review).
   - **What it unloads.** What Dan confirms, each after the front has drained it (*The front and
     the gate*). `--all` is the CLI's `stop-all`. Unloading a pinned model ends its pin, and a
     session loses its model; `spark status` says whose they were.
   - **It holds the room it frees** for Dan until `spark make-room --done`, a duration he gives, or
     the next boot (Dan, 2026-10-07); `--all` holds the whole box. The gate counts the hold as
     reserved in every admission (rule 9), so no reload, waiting request (`agent`'s included), boot
     preload or brake release takes it back (S02), and a refusal it causes says so. `spark status`
     shows the hold, its size and when it ends. It errs safe: once Dan's job has allocated, the hold
     still counts, until he ends it. *(Added 2026-10-07, after the implementation plan's
     forward-and-back council: free for a load can then fall below 0 for `agent`, and its refusal
     says "nothing is free for a load while make-room holds … for Dan", never a negative number.)*
     *(Ruled the same day, after the final re-review, Dan having left the UX to the session: the
     hold is Dan's. A request with one of his keys, or `spark load`, may load into it, and the hold
     shrinks by the part of that load the room outside it couldn't cover; `agent`'s requests and the
     gate's own reloads and preloads still may not. So `no_fit`'s next step, "free space with `spark
     make-room 41G` on the Spark, then try again", loads the model Dan wanted, which it couldn't
     while the hold barred his own requests too. A hold his loads use up ends then, and `--done`
     ends whatever is left.)*
   - **When the hold ends,** the gate reloads the residents one at a time, if they fit; on-demand
     models wait for a request. `spark make-room --done` reaches the control socket's *release*,
     as `make brake-release` does for the brake's hold. A resident that doesn't fit then waits in
     the gate's queue, loads once it fits, and shows as waiting in `spark status`, and ntfy says so
     once (added after the re-review; the same holds after the brake's release).
5. **Brake:** polls every 250 ms and on the rate of fall. Order: a loading engine, then idle models of
   any class, then the least recently used. Each step notifies, and "held by brake" blocks automatic
   reloads. Starting thresholds — tunable, set above the band where freezes have been reported: warn
   at 28 GiB available, brake at 20 GiB, admission keeps ≥24 GiB free, earlyoom at 12/9 GiB. Tuned
   from measurements. *(Phase 2a: ~~the brake moves into the gate, with the same thresholds and
   order.~~ It **releases by itself** once memory has stayed above the warn line, 28 GiB, for
   **5 minutes** (Dan, 2026-10-05: "a few minutes"), and its notification says when it fired and
   when it released. The gate then reloads the resident models one at a time, as at boot (Dan,
   2026-10-05); on-demand models wait for a request. Phase 1's `make brake-release` stays. ~~And
   admission keeps **≥22 GiB** free, not 24: rule 9.~~ Revised the same day after the council, with
   Dan's decisions of 2026-10-07:)*
   - **The brake stays its own unit,** `local-ai-brake`, not inside the gate, so a gate that is
     down never means no brake (*The front and the gate*); while the gate is down, the brake sends
     its "brake fired" to ntfy itself (the session's ruling after the re-review). Its order is the
     plan's, above: a loading engine, then idle models of any class, then the least recently used,
     from the in-flight record the gate writes, or Phase 1's order, on-demand first, then by size,
     when that record is missing or stale. 2a adds the **rate-of-fall watch**: the brake acts
     early when the fall would reach the brake line before an unload can free enough, set from 2a's measurements
     of loads' falls (2.0 to 2.6 GiB/s so far) and engines' stop times (*The minimal brake's
     reach*). The thresholds stay, and admission keeps **≥24 GiB** free: rule 9 records the
     reserve's swing to 22 and back, the same day. *(Corrected 2026-10-07, after the
     implementation plan's review, which found that a watch on the fall alone fires on every
     ordinary coder load, since 2.0 to 2.6 GiB/s is how fast a load falls:)* **a fall the gate
     knowingly admitted is not a crash.** The watch subtracts the expected fall of the loads in
     progress, each admitted footprint from its start, which the gate's ticket and its activity
     record carry, so an admitted load never trips it; it acts on a fall past that, or on one
     nothing explains (the controller's ruling).
   - **The gate lifts the hold** once memory has stayed above the warn line for 5 minutes (5 is the
     plan's figure for Dan's "a few minutes"), and only if the models it would reload fit, by rule
     9's formula, with the reserve, the growth owed and any make-room hold. Each reload goes
     through that same admission.
   - **At most one automatic release an hour.** A brake that fires within the hour after an
     automatic release holds until Dan releases it, and its high-priority alert says so.
   - **A hold found after a reboot waits for Dan,** with a high-priority alert at boot: a freeze is
     the case the fsynced hold exists for.
   - **The model that was loading when the brake fired doesn't reload by itself:** not with the
     residents after a release, and not for one of `agent`'s requests. A request from Dan's own
     keys, or `spark load`, loads it as normal, through admission, if it fits, and that clears its
     mark (Dan's choice, 2026-10-07, after the re-review; this first refused every request for it,
     Dan's included, with `held_by_brake`, though no hold stood, until `spark load`). An `agent`
     request for it waits its key's wait, in case Dan loads it meanwhile, and is then refused with
     `footprint_suspect`, whose text names `spark load <model>`. The mark is kept with the gate's
     state, so a reboot doesn't clear it, and `spark status` shows it, with what the model was seen
     using. *(Dan's decision, 2026-10-07, at the UX pass: keep this for `agent`, its requests
     waiting their key's wait, then refused with `footprint_suspect`, and a default-priority
     notification telling him `spark load <model>` allows it again; his own requests load it if it
     fits. He left the wording to the session's judgement: *What you see in Phase 2a*.)*
   - While the hold stands, a request waits for its key's wait and is then refused with
     `held_by_brake`. A hold the gate can't lift, because the gate is down, stays.
6. **Footprints** are the larger of the load peak (sampled 10×/s) and the steady state after a soak at
   maximum context, keyed to a hash of engine + arguments + model revision. Unmeasured models use a
   gguf-parser estimate with a margin and are flagged.
7. **Structured refusals:** `error.code` is one of `no_fit | loading | gate_down | not_downloaded |
   held_by_brake`; the text gives memory needed against available, the top holders (nvidia-smi's
   per-process list plus names) and the options; plus `retry_after_s`. A request for a model that is
   already loading waits for it. *(From Phase 2a the front returns them inline, after the key's wait
   (*The front and the gate*), and `spark status` keeps a history of recent refusals, not only the
   last. Revised the same day after the council: three codes join, `load_failed` (a start that
   failed or passed its deadline; the text is `spark launch`'s refusal record or the engine's last
   lines, which the gate reads from llama-swap's log with its key), `restarting` (`make apply` is
   restarting llama-swap) and `llama_swap_down` (llama-swap isn't answering); after the
   re-review, `draining` (the model is being unloaded, and its requests in flight outlasted the
   key's wait) and `footprint_suspect` (an `agent` request for the model that was loading when the
   brake fired: rule 5). After the UX pass, the same day: `loading` now means the key's wait ran
   out in the queue for the one-load slot, the front's own refusals gain codes too
   (`model_not_found`, `too_many_requests`, `route_not_served`), and every code's message is
   under *What you see in Phase 2a*. ~~A refusal is a `503` with `Retry-After` and
   `x-should-retry: false`, but for the front's own `404`s and `429`.~~ *(Corrected 2026-10-07, with
   the implementation plan: a refusal after a wait — `no_fit`, `loading`, `held_by_brake`,
   `footprint_suspect` — is a `409`, so pi doesn't retry the wait, and so are `load_failed` and
   `not_downloaded`, which no retry changes; ~~only the outages stay `503`s;~~ and, by Dan's
   decision after the plan's forward-and-back council, `restarting`, `llama_swap_down` and
   `draining` too, so only `gate_down` stays a `503`; `x-should-retry: false` on both;
   `Retry-After` only with a code's retry-after; the front's own `404`s and `429` as before. *The
   front and the gate* says why.)* Its text names the stack's
   models in full, and
   any other process by its user and its short process name, never its command line. How long a
   request waits, a request for a model that is already loading included, is defined once, under
   *The front and the gate*: the key's wait.)*
8. **Gate down ≠ API down:** models llama-swap reports ready keep serving; only new loads are refused.
   *(From Phase 2a the front does this: while the gate is down, it forwards to the models llama-swap
   reports loaded and refuses new loads with `gate_down`. Revised the same day after the council:
   this holds only for what is already loaded. At boot, or after a restart that stopped every
   engine, a gate that is down means nothing loads, so the API is down in effect until it starts
   (the session's ruling). The brake, its own unit, keeps running meanwhile. *The front and the
   gate* has what each failure costs.)*
9. **The budget** (corrected 2026-10-07, for Phase 2a; Dan: "fix the check and lower the reserve a
   little"). Footprints fit the **CUDA-allocatable ceiling**, and **each load leaves the reserve
   free** at the moment it happens, which the gate checks against real `MemAvailable` (rule 1). So
   `spark render` checks ~~that the registry's footprints together fit the ceiling, and that the
   residents leave the reserve free at idle~~ that the residents fit idle `MemAvailable` less the
   reserve, within the ceiling, and each on-demand model beside them (one marked `needs_room`,
   with nothing loaded), and only warns when every model loaded at once would pass the ceiling or
   leave memory under the warn line *(corrected 2026-10-07, at Task 5's review; the bullet on
   `spark render` below has the checks)*; the gate keeps checking each load. Until 2a, render
   summed every model's footprint against `allocatable − reserve` (102 − 24 = 78), which counted
   the reserve twice: it kept the reserve free under the CUDA ceiling, which is itself about 15 GiB
   below idle `MemAvailable` on this box (117 GiB), and then the launch check kept it free again at
   each load. With Phase 2a's coder the set comes to about ~~81~~ 84 GiB of the 102, and the
   residents, 43, plus the reserve fit under both. ~~**The reserve goes from 24 to 22 GiB.**~~ It
   has to exceed the brake line, 20, which the registry enforces, so that a fresh load never trips
   the brake~~, and 22 keeps a 2 GiB margin~~. The reserve exists for freeze protection —
   overcommitting can hard-freeze a GB10 with no OOM kill (NVIDIA's driver issue #1358) ~~— and Dan
   lowered it knowingly~~. **The
   ceiling gets measured** in 2a, carefully, since allocating until failure is how a GB10 freezes,
   and the registry follows the measurement. *(Revised the same day after the council, which found
   that admission ignored the growth still owed to loaded models, and that neither check had a
   formula. The reserve's swing, all on 2026-10-07: the design lowered it from 24 to 22, the
   session's figure for Dan's "a little"; after the council Dan put it back to 24, since the
   corrected check is what lets Qwen3.8-27B fit at f16, and 22 only halved the margin above the
   brake while every footprint is an estimate. It is revisited once 2a has measured them. The
   corrected check stays. The 81 above is now about 84, with the coder's estimate raised to 41.)*
   - **The gate, at each load,** admits when

     `footprint ≤ min(MemAvailable − reserve − owed, ceiling − committed) − starting − held`.

     *owed* is the growth the loaded models are still owed: for each, its footprint less what it
     holds now, never below 0. What it holds now is what its own load took, the fall in
     `MemAvailable` across it, which the gate measures, since it loads one at a time, plus its
     engine's own RSS growth since that load; the gate keeps both with its state. The bulk of a
     model's growth, its context checkpoints and its prompt cache, is host memory, so it should show
     in the engine's RSS, while GPU allocations don't (Phase 1's Task 13 found the engines' RSS at
     0.4–2.1 GiB against 2–25 GiB each on the GPU); growth that doesn't show in RSS stays in *owed*,
     which errs safe. 2a checks that attribution on the box, during the soak, against
     `MemAvailable`'s fall, before the gate relies on it. *(Made precise after the final re-review:
     the growth read is the engine's anonymous RSS, `RssAnon` in `/proc/<pid>/status`, not its total
     RSS. The embeddings engine runs without `--load-mode none`, so it maps its model file, and
     file-backed RSS is page cache that `MemAvailable` still counts as available: a rise in it would
     shrink *owed* with no memory taken. 2a's soak compares, for each engine, its `RssAnon` growth
     with `MemAvailable`'s fall and with `nvidia-smi`'s per-process figure for it, which Phase 1
     read on this box, and records what each shows.)* The load's fall is capped at the model's
     measured cold load plus a margin, once the soak has measured it, and a load during which other
     memory moved is flagged, since an outside allocation would inflate the fall and shrink *owed*.
     A footprint counts what a model holds at its most after admission, so without this term a load
     admitted against the `MemAvailable` of the moment could land below the brake once the others
     grow: the residents load cold at 33.5 GiB against footprints of 43, so 9.5 GiB is still to come
     (the council's reliability review). *committed* is the loaded models' footprints; the ceiling
     term binds only if the measured ceiling comes in below idle `MemAvailable` less the reserve.
     *starting* is the footprint of any model `/running` shows starting: none, while the gate loads
     one at a time and no load is under way, but a model left starting across a gate restart counts.
     *held* is make-room's hold (rule 4), which a request with Dan's keys doesn't count, since he
     may load into it (since the implementation plan's forward-and-back council, a key group's
     `uses_hold` decides). Each loaded model keeps the footprint of the registry that loaded it,
     never a newer one's, and `spark status` shows *owed*.

     Growth that shows in an engine's anonymous RSS leaves *owed* as it enters `MemAvailable`, so
     the two cancel: with nothing outside the stack and no hold, `MemAvailable − owed` is idle
     `MemAvailable` less the loaded models' footprints, however far they have grown, and the
     formula becomes
     `footprint ≤ idle MemAvailable − reserve − the loaded models' footprints`, which is render's
     check below. The coder beside the residents: 117 − 43 − 24 leaves 50, against its ~41, cold
     residents or grown. *(Corrected 2026-10-07, after the re-review: *owed* was first a model's
     footprint less its load's fall alone, which counted growth that had already happened twice,
     once in `MemAvailable` and once in *owed*. It erred safe, but it would refuse the coder once
     memory outside the stack and the residents' growth passed 9 GiB together, with nothing else
     running, against render's promise.)*
   - **`spark render`, on the registry,** checks ~~that the footprints together fit the ceiling;~~
     that the residents and the reserve fit idle `MemAvailable`, a measured value the registry
     records, and the residents the ceiling too *(corrected 2026-10-07, at Task 5's review, the
     controller's ruling: they load together at boot, so they have to fit under both terms,
     `min(idle MemAvailable − reserve, ceiling)`)*; and that each on-demand model fits beside the
     residents with the reserve, so the gate never has to refuse one with nothing else running.
     *(Revised 2026-10-07, Dan's decision after the implementation plan's forward-and-back council:
     the whole set's fit to the ceiling is a warning, since the gate admits each load against live
     memory and later phases' registries (two coders, a fallback, a larger model) won't fit at once;
     and a model marked `needs_room`, which loads only after make-room has freed room for it, is
     checked against idle `MemAvailable` less the reserve and the ceiling, not beside the
     residents.)* That last check is the gate's formula, evaluated at idle with the residents loaded
     and nothing else running, so render and the gate hold one formula between them. *(And since
     Task 5's review, 2026-10-07, the residents' check and a `needs_room` model's are the gate's
     formula too, with nothing loaded.)* It warns when every model loaded at its footprint would
     leave memory under the warn line, 28 GiB. With Phase 2a's coder at ~41, the set comes to ~84 of
     the 102; the coder, the residents and the reserve to 108 of the 117; and everything loaded
     would leave about 33, 5 GiB above the warn line.
   - **The reserve stays ≥24 GiB,** above the brake line, 20, which the registry enforces, so that
     a fresh load never trips the brake.
   - **The ceiling gets measured** in 2a without coming near a freeze: first what CUDA reports with
     nothing allocated (`llama-server --list-devices`); then a standard-library `ctypes` script
     against llama.cpp's bundled CUDA runtime that allocates and touches 1 GiB steps, re-reads
     `MemAvailable` at each and stops at `MemAvailable` less the reserve, with the brake and
     earlyoom running, never "until failure". *(Corrected 2026-10-07, after the implementation
     plan's review: the steps are 0.25 GiB, paced at 0.1 GiB/s so the rate-of-fall watch doesn't
     take the measurement for a crash, and each is confirmed in `MemAvailable` before the next,
     since earlyoom can't see CUDA's allocations. The drills lower memory with one hog sized from
     `MemAvailable` at the time, never a fixed size, at the same pace.)* If it gets there, the
     ceiling is recorded as at least that, and admission needs no CUDA term. Nothing is installed
     for it, and the registry follows the measurement. If the ceiling or the soak comes in under the
     set, what gives is Dan's to choose: the old coder, whose files are kept, or his full-context,
     f16 rule.

### Users, access and security

- **Three identities.** Dan's own account, login `chendaniely` on the Spark — admin, Positron, the
  control socket. (Corrected 2026-09-24: this first said `dan`, the Mac's login, which the runbooks
  then hardcoded as `/home/dan`.) `spark` — the service user that
  runs the gate and llama-swap (so every model engine), and owns the models, the Hugging Face cache
  and state. It is deliberately **not** in the `docker` group, because Docker access is
  root-equivalent; containers start from root-owned units instead. Dan's own account is
  root-capable through sudo, so the isolation boundary on this box is between Dan and `agent`. From
  Phase 1, what root runs is root's own: the `local-ai-*` units and the Compose project are
  root-owned copies that `make install-units` installs with sudo, after showing what changed, so
  nothing running as Dan — the Spark session, Positron's packages, a build — changes what root runs
  without Dan's sudo. The polkit rule lets `spark-admin` start, stop and restart the four units
  by exact name, and nothing more. *(Phase 2a, designed 2026-10-07: ~~`spark` also runs the front,
  and the gate replaces the brake, so the rule's list follows: the front and the gate in place of
  the brake, five units in all.~~ Revised the same day after the council, Dan's decisions: the brake
  stays, and `spark` runs the gate beside it; the front runs as **`spark-front`** and the model
  pull as **`spark-pull`**, system users of their own, so neither the engines nor the pull's
  downloader reaches the front, and the engines can't change the model files; `spark` reads the
  Hugging Face cache, which `spark-pull` owns, through its group. The rule's list grows to six:
  the front and the gate join the four. The socket units and the failure notifier aren't on it.
  Seven since the implementation plan's re-check, 2026-10-07 (the controller's ruling): the S05
  drill's oneshot, `local-ai-brake-drill.service`, which runs `spark brake --once` as `spark`
  against a drill copy of the registry that Dan writes, so the drill needs no sudo.)*
  (Corrected 2026-09-25: this said Dan's account is effectively root-capable through sudo and through `spark-admin`, which could change what the `local-ai-*`
  units run without a password; Dan's decision on the unit-file model, under *Open items and
  risks*, closed the second path. Corrected again 2026-09-25, after Phase 1's pre-flight review:
  this said "without Dan's password", and named none of the paths the next bullet lists.) `agent` —
  tmux agents: the status and session socket only; no sudo, no docker; 0700 homes;
  writes its repos and the NAS work folders; its own key; no GitHub credentials.
- **Paths that stay open** (2026-09-25, from Phase 1's pre-flight review; Phase 1's council, in its
  Task 17, takes them up). Root's own copies close one way from Dan's account to root, not all:
  - `make install-units`, like `make bootstrap`, `make hold-gpu` and `make upgrade-gpu`, runs as
    root whatever the clone's own `Makefile` and `stack/host/bootstrap.sh` say. Dan's account can
    write both, and no diff shows them: `make install-units` shows only the staged files.
  - sudo caches Dan's credential for about 15 minutes in each terminal, and anything running as
    Dan in that terminal meanwhile can use sudo without a password. `make install-units` ends with
    `sudo -k`, so the `make apply` that follows can't use it; `make bootstrap`, `make hold-gpu` and
    `make upgrade-gpu` keep the cache. (Corrected 2026-09-26, from Phase 1 Task 9's review:
    `make bootstrap` and `make hold-gpu` now end with `sudo -k` too, and Task 10 gives
    `make upgrade-gpu` the same, so no sudo target leaves the cache for the clone's code that runs
    next.)
  - Root's containers read `spark`-owned data: Open WebUI's Functions, kept under
    `/var/lib/local-ai/open-webui`, run as the container's root with host networking.
    *(Phase 2a, Dan's choice, 2026-10-07: both web containers drop every capability and add back
    only what each needs, so they can no longer bind a port below 1024, such as llama-swap's or the
    engines'. They still run as root with host networking.)*
  - Dan's account can make `spark` run anything, through `/opt/local-ai`'s `app`, `bin`, `etc` and
    `python` folders, which `spark-admin` writes, and `spark` holds all four llama-swap keys.
    *(Phase 2a, designed 2026-10-07: from 2a `spark` holds only internal keys, and the clients'
    keys stay on the clients, as digests in the front: *The front and the gate*.)*
- **Secrets.** Never `EnvironmentFile=~/.secrets` — systemd ignores `export` lines and has been
  reported logging them with their values. Dan writes one `KEY=value` file per service, 0640
  root:spark, in `/etc/local-ai/secrets/`, a folder his own account can't list, outside any agent
  session (corrected 2026-09-25: this said 0600; `website/how-to/secret-files.md` makes them 0640
  root:spark); the gate token goes in through `LoadCredential=`, Postgres through
  `POSTGRES_PASSWORD_FILE`; client keys live in `~/.secrets` on each client. Agents only test that a
  variable is present. *(Phase 2a, Dan's decision, 2026-10-07: the files become 0600 root:root, in
  a folder only root reads, since nothing reads them as `spark`; the front's, the gate's, the
  brake's and the notifier's keys and tokens go in through `LoadCredential=`, and llama-swap's
  through its `EnvironmentFile=` (*The front and the gate*).)* A Claude Code hook on the Spark
  blocks commands that print values (`docker inspect`, `docker compose config`, `/proc/*/environ`, `systemctl show-environment`).
- **Network.** Tailscale ACL grants are the tailnet's firewall, because ufw does not filter
  `tailscale0` (Tailscale issue #11717). Everything binds 127.0.0.1 except SSH (and LiteLLM from
  Phase 3, and Phase 6's guest web UI during an event); Open WebUI is reachable only through
  `tailscale serve`. The ACL policy lives in the vault.
  Clients reach the Spark by its tailnet name, at home and away: the SSH alias (and so pi's tunnel)
  and Open WebUI. The LAN address, from a private values file, serves the home LAN — homelab apps
  from Phase 3, devices on WireGuard, and SSH when Tailscale is down (`website/how-to/ssh.md`).
  There is no tailnet route home, by choice: the Spark joins the tailnet itself and sits on the LAN,
  so it needs none (`website/how-to/tailscale.md`). (Corrected 2026-09-25: this said client base
  URLs and the SSH alias use the LAN address "through the tailnet's route home", which Phase 0
  would confirm; on 2026-09-24 that route was settled as none.)
- **NAS.** The Synology's NFS exports offer only the data and recordings shares (read-only) and named
  work folders (read-write), to the wired address only, with root squashed. Immutable snapshots
  (DSM 7.2+, Btrfs) protect the work and backup shares. systemd automounts with timeouts.
- **Supply chain.** Digests live in `versions.yaml`. LiteLLM comes only from its signed Docker image
  (its PyPI releases 1.82.7 and 1.82.8 were backdoored on 2026-03-24; the images were not affected
  and have been signed since 1.83.0). GitHub Actions are pinned by commit SHA with minimal
  `permissions:`. The GPU set — kernel, NVIDIA modules, driver and CUDA — is held and upgraded
  deliberately, as one. Its advisory feeds are Ubuntu's security notices for the kernel and NVIDIA's
  GPU display driver security bulletins. A security fix in either is the only reason to move it
  before upgrade day; a driver fix counts because `agent` and `spark` both use the GPU.
- **Leak guards.** `.githooks` pre-commit and commit-msg hooks run gitleaks plus patterns for private
  LAN and tailnet addresses, MACs and `ts.net`, plus a private denylist kept outside the repo (the
  hook fails if it's missing, has no terms or doesn't parse). They read file names as well as
  contents, and type changes; a binary, such as a screenshot, can't be read, so the hook names it
  for a person to check. CI runs gitleaks and the patterns, without the denylist, over every
  tracked file and every commit's patches and messages. Configs render outside the repo; fixtures
  use RFC 5737 addresses and `example.invalid`; no pasted terminal output; Quarto `_freeze` gets
  checked too, like any tracked file. Nothing scans Actions logs: what limits them is what CI
  prints, gitleaks with `--redact` and the leak check's 3-character excerpts. (Corrected
  2026-09-25: this said screenshots and Actions logs get checked; binaries are named, not read, and
  the logs aren't scanned.)

### Visibility and notifications

- **One status source:** `spark status --json` — loaded models (real names), pins and sessions,
  **headroom before the brake**, top memory holders, pending loads, brake state, health. *(Phase 2a,
  designed 2026-10-07: `spark status` shows models, memory, holds, pins, sessions, requests
  waiting and **recent refusals, a history rather than only the last**, and `--json` is there for
  2b's menu bar. `spark doctor` gains the front, the gate and ntfy, and the brake's check at start
  that llama-swap takes its key moves into the gate. Revised the same day after the council:
  status also shows the growth the loaded models are still owed, the holds (the brake's, and
  make-room's with its size and end), each model's oldest request in flight, "unaccounted memory"
  (idle `MemAvailable`, less what is available now and the loaded models' footprints, which tells
  a wrong footprint from a job outside the stack), and "notifications failing since …" when ntfy
  can't be reached. Through the status socket, which `agent` reads, it leaves out other keys'
  refusals and the names of Dan's processes. The brake stays its own unit, so its key check at
  start stays with it, and doctor checks the brake too. Its layout, in plain words, is under *What
  you see in Phase 2a*, below.)*
- **Menu bar (SwiftBar)** polls it over SSH. The title shows the active models and the headroom; the
  dropdown offers load, unload, pin, make-room and stop-all (over SSH as Dan), lists running agent
  sessions, and says "unreachable" when it is.
- **ntfy (on the Synology):** low — loaded, unloaded, idle; default — refused, load failed, agent
  done / needs input / failed; high — brake, gate or Spark down, backup failed. Quiet hours apply to
  low and default. *(Phase 2a, Dan's decisions: ntfy comes in 2a, set up by Dan at its start
  (2026-10-05), so the gate notifies from its first day; the watchdog stays in 2b. It is reached
  over the tailnet or the home LAN only, with no public relay, so out of reach means no alerts, for
  now. **Quiet hours run 00:00–05:00**, when only high-priority alerts make a sound. The priorities
  stay as above, with "brake released, and what reloaded" at default (2026-10-07). Revised the
  same day after the council:
  - **Quiet hours are set on Dan's phone,** since ntfy's server has none (the session's ruling):
    Android's Do Not Disturb runs 00:00–05:00, and the ntfy app's high and max priority channels
    are allowed through it. The publishers send every alert at its own priority, at any hour.
  - **The phone only, in 2a** (Dan's decision): the ntfy Android app, with the NAS as its server
    and instant delivery, over Tailscale or the home LAN. The Mac's alerts come with 2b's menu
    bar.
  - **The Spark publishes over the tailnet,** which encrypts it. On the home LAN, over plain HTTP,
    a token that may only publish, and only to its own topic, bounds what a sniffed one can do.
    Each publisher has its own: the gate and the failure notifier in 2a, and the brake, for its
    own alert while the gate is down (added after the re-review). ntfy's address lives in
    the private values file, never in the repo.
  - A notification names the stack's models in full and anything else by its user and short
    process name, as a refusal does (rule 7), since it shows on the phone's lock screen.
  - 2a's full list of notification types, their priorities and their wording is under *What you
    see in Phase 2a*, below, and supersedes the three-line list above for 2a.)*
- **Harness hooks** register agent sessions with the gate and post their outcomes to ntfy.
  *(Noted 2026-10-07, after Phase 2a's council: `agent`'s hooks, in 2b, post through the gate's
  status socket, or with a publish-only token on a topic of their own, so they can't post as the
  gate.)*
- **Orca (2026-09-28)** shows each agent it started on the Mac — working, waiting for input, done —
  through its own status hooks. It adds to ntfy rather than replacing it: agents in tmux on the
  Spark don't run in Orca.
- **Real model on every reply:** clients use real model names until Phase 3; from then on the
  `x-spark-model` header carries it. *(Phase 2a adds `--alias`, so a reply names the registry's
  model rather than its GGUF file, as Phase 1's Task 13 found it did.)*

#### What you see in Phase 2a

*Designed 2026-10-07, from the session's draft and Dan's guidance the same day; nothing here is
built yet.* Dan: user experience is really important, and there should be no confusion; *"I'd rather
err on more notifications than something not being clear at the moment, and we can handle which
types of notifications get turned on and off later."* He left the wording to the session's
judgement. The principle: **whenever the gate acts, Dan can tell what happened, why, and what to do
next, in the place he is already looking.** No code without words, no silent failure. *(Revised
the same day after the final re-review, with the session's rulings: make-room's hold is Dan's to
load into (rule 4), so `no_fit`'s next step works; "available" and "free for a load" each name one
number; and the examples now agree with their moments.)*

| Where Dan looks | What it's for | In 2a |
|---|---|---|
| The client (pi, the web UI) | the answer to this request, or why there isn't one | the refusal's message |
| The phone (ntfy) | what needs him, or happened while he wasn't looking | every notification type below |
| `spark status`, on the Spark | the whole picture, at any time | the layout below |
| The menu bar on the Mac | the picture without asking | Phase 2b |

**Plain words everywhere Dan reads.** Models by role first, as he knows them: *the coder*, *Gemma*,
*the embeddings*, *whisper*; file names only in `spark status` and make-room's list, in brackets,
and in `model_not_found`, which lists them so a client's list can be fixed. *Always loaded* and
*loads when asked*, not resident and on-demand. *Paused* is the brake's word (new loads paused), and
*held* is make-room's (room held for Dan); `spark status`'s `held` row also names the pins, the
sessions and apply's hold, each by name (added 2026-10-07, after the implementation plan's
forward-and-back council). Sizes in whole GiB, times in his local time on a 24-hour clock, never a
stack trace. Every message and notification names where to act, *on the Spark* or *on your phone*,
and the command. *(Added 2026-10-07, at the implementation plan's Task 6, the controller's rulings:
a duration of a minute or more reads in minutes and seconds, *3 minutes* or *8 minutes 20 s*, so no
deadline prints as a bare 5xx, which pi's retry list matches. In `agent`'s words, a step only Dan
can take says it is Dan's: `agent` is never told to run a command that only Dan can run.)* *(Added
2026-10-07, at the implementation plan's Task 6's fix round 2, the controller's ruling: a size of 400 GiB or more reads *more than 400 GiB*, never altered to dodge pi's retry list.
None can occur on this box, since render keeps every footprint under the ceiling.)*

**Two numbers, two words, everywhere.** *Available* is always `MemAvailable`, the box's free
memory: what the brake's lines (warn at 28 GiB, brake at 20) and the status header measure. *Free
for a load* is always the admission figure: available, less the 24 GiB reserve, the growth the
loaded models are still owed and any make-room hold that isn't the asker's (rule 9). No message,
notification or status line says "free" alone. *(Added 2026-10-07, at the implementation plan's
Task 6, the controller's ruling: or less, where the GPU's ceiling binds or a load is starting: rule
9. A refusal shows the gate's own figure, with the breakdown of the term that gave it, so the sum
adds up. Where the ceiling binds it reads *(the 102 GiB the GPU can allocate, less the 92 GiB the
loaded models may grow to)*; with a model starting, *… and the 27 GiB the model still starting may
take*.)*

**Nothing is injected into a reply stream.** Gate text in a stream would read as the model's own
words, so a request that waits shows in the client only as a slow reply, its usual "thinking", and
the wait is explained on the phone (`waiting`) and in `spark status`. A refusal or the answer ends
it. A cold load looks like a slow first token; 2b's menu bar will show *loading*. llama-swap's
`sendLoadingState` stays off.

**Refusals, in the client.** Each is one sentence a person reads, then the numbers, then one next
step. The code stays in the error's `code` field, for programs; Dan never has to read it. The
examples share one moment: the residents loaded, the coder not, and a 32 GiB python job of Dan's
running, so 48 GiB is available and 18 GiB is free for a load (48 − 24 − 6 owed, or
117 − 43 − 24 − 32; rule 9). The brake fired at 03:12 while the coder was loading, so the coder
carries its mark (rule 5).

| Code | HTTP | The message, as pi or the web UI shows it |
|---|---|---|
| `no_fit` | 409 | *The coder didn't load: it needs 41 GiB, and 18 GiB is free for a load (48 GiB available, less the 24 GiB reserve and the 6 GiB the loaded models may still grow into). Using memory now: python3 (chendaniely) 32 GiB, Gemma 27 GiB. Free space with `spark make-room 41G` on the Spark, then try again.* For `agent`'s key, a hold of Dan's is counted and named: *… less … and the 41 GiB make-room holds for Dan. The hold ends when Dan runs `spark make-room --done` on the Spark.* (Corrected 2026-10-07, at the implementation plan's Task 6's fix round 2, the controller's ruling: it read *On the Spark, `spark make-room --done` ends the hold.*, which didn't say whose step it is.) Once Dan's job has taken the room, below 0 is never shown: *… and nothing is free for a load while make-room holds 70 GiB for Dan (36 GiB available, less the 24 GiB reserve). …* For `agent`'s key with no hold of Dan's counted: *… Only Dan can free memory for it, on the Spark; try again after that.* Where the GPU's ceiling binds, the parenthesis is that term's: *… and 10 GiB is free for a load (the 102 GiB the GPU can allocate, less the 92 GiB the loaded models may grow to). …*; with a model still starting, *… and the 27 GiB the model still starting may take*. (Added 2026-10-07, at the implementation plan's Task 6, the controller's rulings: make-room is Dan's alone, and its hold would bar `agent` anyway; and the breakdown is the term that gave the figure, so it adds up.) |
| `loading` | 409 | *The coder didn't start in time: it was waiting its turn while Gemma loads, since one model loads at a time, and your 30 s ran out. Try again in a minute.* |
| `held_by_brake` | 409 | *Not loading the coder now: memory ran low at 03:12 (19.6 GiB available), and new loads are paused. They resume by themselves after 5 minutes above 28 GiB available, if what would reload fits; on the Spark, `make brake-release` resumes them now.* When the hold waits for Dan (a second brake within the hour, or one found after a reboot): *… new loads stay paused until you release them: on the Spark, `make brake-release`.* For `agent`'s key: *… if what would reload fits, or when Dan runs `make brake-release` on the Spark.*, and when the hold waits for Dan, *… new loads stay paused until Dan releases them: on the Spark, `make brake-release`.* (Added 2026-10-07, at the implementation plan's Task 6, the controller's rulings: only Dan can release the brake's hold.) |
| `gate_down` | 503 | *No new model can load: the gate on the Spark isn't running. Models already loaded still answer. Your phone has the alert; on the Spark, `make doctor` shows what's wrong.* For `agent`'s key: *… Dan's phone has the alert, and `make doctor` on the Spark shows Dan what's wrong.* (Added 2026-10-07, at the implementation plan's Task 6: the phone and `make doctor` are Dan's.) |
| `load_failed` | 409 | *The coder started loading but failed: the engine stopped with "failed to load model". On the Spark, `spark logs coder` shows the engine's last lines.* Past its deadline: *… but didn't finish within 3 minutes.* For `agent`'s key: *… Dan can see why with `spark logs coder` on the Spark.* (Corrected 2026-10-07, at the implementation plan's Task 6's fix round 2, the controller's ruling: the next step named `spark status`, which shows no engine lines; Task 30's `spark logs <model>` does, on the control socket, which `agent` can't use.) (Corrected 2026-10-07, at the implementation plan's Task 6, the controller's rulings: the deadline read *within 180 s*, and a duration of a minute or more now reads in minutes, so none prints as a bare 5xx. `agent`'s form was added, since only Dan reads the engine's lines.) |
| `not_downloaded` | 409 | *The coder isn't downloaded yet. On the Spark, `make pull` fetches it (16 GiB).* For `agent`'s key: *… Dan can fetch it with `make pull` on the Spark (16 GiB).* (Added 2026-10-07, at the implementation plan's Task 6: the pull is Dan's.) |
| `restarting` | 409 | *The model service on the Spark is restarting for a configuration change, and your 30 s ran out. Try again in a minute.* |
| `llama_swap_down` | 409 | *The model service on the Spark isn't answering, and your 30 s ran out. Your phone has the alert; on the Spark, `make doctor` shows what's wrong.* For `agent`'s key: *… Dan's phone has the alert, and `make doctor` on the Spark shows Dan what's wrong.* (Added 2026-10-07, at the implementation plan's Task 6.) |
| `draining` | 409 | *The coder is being unloaded for make-room once its 1 request in flight finishes, and your 30 s ran out. Try again in a minute: your request can load it again, into the room make-room holds for you, if it fits.* For `agent`'s key: *… It won't load for agent while make-room's hold stands. The hold ends when Dan runs `spark make-room --done` on the Spark.* (The last sentence added 2026-10-07, at the implementation plan's Task 6, the controller's ruling: it says what ends the hold, and whose step that is.) |
| `footprint_suspect` | 409 | *Not loading the coder for agent: it was loading when the brake fired at 03:12, so only Dan can load it again: `spark load coder` on the Spark, or one of Dan's requests from pi on the Mac or the web UI, which loads it if it fits.* (Corrected 2026-10-07, at the implementation plan's Task 6's fix round 2, the controller's ruling: it read *a request of his*; no message uses a gendered pronoun for Dan.) |
| `model_not_found` | 404 | *There's no model called qwen3.6-35b-a3b here. The models are the coder (qwen3.8-27b), Gemma (gemma-4-26b-a4b), the embeddings (qwen3-embedding-0.6b) and whisper (whisper-large-v3-turbo). On the Mac, `make clients` updates pi's list.* For `agent`'s key: *… On the Spark, as `agent`, `/opt/local-ai/app/.venv/bin/spark clients pi --write --registry /opt/local-ai/etc/models.yaml` updates pi's list.* (Added 2026-10-07, at the implementation plan's Task 6, the controller's ruling: `agent`'s pi is on the Spark, and the Mac's `make clients` doesn't reach it. Corrected at Task 6's review the same day: the first correction read *… pull its clone and run `spark clients pi --write` to update pi's list.*, the procedure before 2a. After the cutover `agent` has no clone and runs the deployed CLI, as Task 34 sets up.) |
| `too_many_requests` | 429 | *agent already has as many requests waiting or open as its key allows; this one wasn't queued. Try again when one finishes.* |
| `route_not_served` | 404 | *This address isn't served here: the Spark's model API answers only /v1/models, /v1/chat/completions, /v1/completions, /v1/responses, /v1/messages, /v1/embeddings and /v1/audio/transcriptions.* |
| `invalid_api_key` | 401 | *That API key isn't one the Spark knows. Check SPARK_API_KEY on this machine.* (The controller's ruling, 2026-10-07; added to this table after the implementation plan's forward-and-back council.) |

Every `503` carries `Retry-After` and `x-should-retry: false` (rule 7); the `429` carries
`Retry-After` alone, and the `404`s neither. *(Corrected 2026-10-07, with the implementation plan:
the four refusals that come after a wait, `no_fit`, `loading`, `held_by_brake` and
`footprint_suspect`, are `409`s, as the table now gives them, where it gave `503`, because pi
retries a `503` by itself, up to three times, and would hold Dan's 30 s refusal for about 2¼
minutes; `load_failed` and `not_downloaded` are `409`s too, as the table gives them, since no retry
changes either; ~~only the outages, `gate_down`, `llama_swap_down`, `restarting` and `draining`,
stay `503`s~~; and, by Dan's decision after the implementation plan's forward-and-back council, so
are `restarting`, `llama_swap_down` and `draining`, since they also come after the key's wait, so
only `gate_down` stays a `503`. Every `409` and `503` carries `x-should-retry: false`; `Retry-After`
comes only with a code's retry-after, 30 s for `no_fit`, `gate_down` and `llama_swap_down`, 60 for
`restarting` and `draining`, 300 for `held_by_brake` and 10 for the `429`.)* A make-room hold is
Dan's (rule 4): his keys may load into it, so his `no_fit` never counts it, while `agent`'s does and
names it, with `spark make-room --done` as the next step (S02). For `agent`'s key a refusal names
Dan's processes only as *a process of Dan's, 32 GiB*, as the status socket does. *(Added
2026-10-07, at the implementation plan's Task 6, the controller's ruling: a process whose name pi's
retry list matches is named only as *a process of Dan's* or *a process*, with its size. A name that
a client asked for reads *There's no model by that name here.* in that case. pi then never retries
a refusal because of a name it didn't choose.)* `loading` now means
the key's wait ran out in the queue for the one-load slot; a request for a model that has started
loading waits for it instead (*A request that needs a load*). `too_many_requests` and
`route_not_served` are the front's per-key caps and its route list, worded. llama-swap's own `429`,
`concurrency_limit`, can still arise when several keys' caps for one model add up past its 10; the
front passes it on, worded: *Too many requests for the coder at once; try again in a moment.*
*(Corrected after the final re-review: this said it couldn't arise.)*

**Notifications, on the phone.** One list in the registry names every type and its priority, `high`,
`default`, `low` or `off`, and **all are on by default**; turning one off, or changing its priority,
is a one-line edit and `make apply`. `spark render` writes the gate's, the brake's and the failure
notifier's settings from that list, and ~~the table below is generated from it~~ a table generated
from it lives at `website/reference/notifications.md` (the controller's ruling), as the Stack page
is from `versions.yaml`; the table below is the design's, written by hand (corrected 2026-10-07,
after the implementation plan's forward-and-back council). In `stack/models.yaml`, an excerpt (the
registry lists all twenty):

```yaml
notifications:
  brake_fired: high
  refused: default
  load_started: low
```

| Type | Priority | When | Example |
|---|---|---|---|
| `brake_fired` | high | the brake fired: it names what it unloaded, and each further unload in the same episode sends a short follow-up; while the gate is down, the brake sends it itself, and the gate, once back, skips what the brake already sent | *Brake on brightroar at 03:12: 19.6 GiB available, under the 20 GiB line. Unloaded the coder, which was loading; new loads are paused. They resume by themselves after 5 min above 28 GiB available.* Then, if it must unload more: *Brake, 03:13: also unloaded Gemma and the embeddings, both idle.* |
| `brake_needs_release` | high | a brake within the hour after an automatic release, or a hold found after a reboot | *After the reboot, new loads are still paused from the brake at 02:58. On the Spark, `make brake-release` resumes them.* |
| `gate_down` | high | the failure notifier: the gate stopped | *The gate on brightroar stopped at 09:14 (it crashed; it is restarting). Loaded models still answer; new loads are refused until it's back. On the Spark, `make doctor` shows what's wrong.* |
| `front_down` | high | the failure notifier: the front stopped | *The front on brightroar stopped at 09:14 (it crashed; it is restarting). Requests wait for it, and any in flight were cut off. On the Spark, `make doctor` shows what's wrong.* |
| `llama_swap_down` | high | the failure notifier, or the gate when it stops answering | *The model service on brightroar stopped at 09:14. No model answers until it's back; requests wait, then are refused. On the Spark, `make doctor` shows what's wrong.* |
| `brake_down` | high | the failure notifier: the brake stopped | *The memory brake on brightroar stopped at 09:14 (it crashed; it is restarting within 2 s). earlyoom stays the backstop. On the Spark, `make doctor` shows what's wrong.* |
| `back_up` | default | the gate, once it, the front, llama-swap or the brake has run again for 60 s after a crash, so a crash loop doesn't alternate it with the `*_down` alerts | *The gate on brightroar has been running again for a minute, after 12 s down. New loads work again.* |
| `refused` | default | a request was refused, whoever asked | *Refused the coder for pi on the Mac: needs 41 GiB, 18 free for a load; python3 (chendaniely) holds 32. Free space with `spark make-room 41G` on the Spark, then try again.* |
| `footprint_suspect` | default | an `agent` request for the model that was loading when the brake fired | *Didn't load the coder for agent: it was loading when the brake fired at 03:12. On the Spark, `spark load coder` allows it again; your own requests load it if it fits.* |
| `load_failed` | default | a start failed or passed its deadline | *The coder failed to load: the engine stopped with "failed to load model". On the Spark, `spark logs coder` shows the engine's last lines.* (Corrected 2026-10-07, at the implementation plan's Task 6's fix round 2, as the refusal's: it named `spark status`, which shows no engine lines.) |
| `brake_released` | default | the gate lifted the brake's hold | *Brake released at 03:40, 64 GiB available. Reloaded Gemma and the embeddings. The coder was loading when it fired, so it loads again only when you ask.* |
| `room_hold_ended` | default | make-room's hold ended: `--done`, its time, a reboot, or used up by Dan's own loads | *make-room's hold for you ended (`--done`), all 40 GiB of it unused. Nothing to reload: the coder loads on its next request.* When it had unloaded always-loaded models: *… Reloading Gemma.* |
| `resident_waiting` | default | an always-loaded model didn't fit when a hold ended | *Gemma didn't fit after the hold ended: it needs 32 GiB, and 9 GiB is free for a load. It loads by itself once there's room.* |
| `apply_restarted` | default | `make apply` restarted the model service | *make apply restarted the model service at 14:02. Gemma, the embeddings and whisper reloaded; the coder loads on its next request.* |
| `load_started` | low | a cold load started | *Loading the coder for pi on the Mac (24 s last time)…* |
| `loaded` | low | a load finished | *Loaded the coder in 24 s.* |
| `unloaded` | low | an idle unload, make-room or `spark unload` | *Unloaded the coder after 60 min idle.* |
| `waiting` | low | a request started waiting for memory, the brake, the load slot, or Dan (`footprint_suspect`) | *Waiting for memory: the coder for pi on the Mac, up to 30 s. It needs 41 GiB, and 18 GiB is free for a load.* |
| `pin_ended` | low | a pin's time ran out | *The pin on the coder ended at 18:00; it unloads after 60 min idle.* |
| `memory_warning` | low | available memory fell under the warn line, 28 GiB (rule 5's warning), once per fall | *Memory is getting low on brightroar: 27.4 GiB available, under the 28 GiB warning line. The brake acts at 20.* |

The failure notifier's four `*_down` types keep its rules (*The front and the gate*): it sends the
unit, its result and the time, worded as above, never a journal line. 2b adds the watchdog's
"box unreachable" and `agent`'s done, needs-input and failed.

**One notification per event.** The gate never repeats an alert for the same hold or the same
outage. A burst of identical refusals (the same model, key and code) collapses: the first goes at
once, and the repeats within 10 minutes go as one, with their count and the reason (*Didn't load
the coder for agent 4 more times since 09:12: same reason.*). That is for clarity, not suppression:
`spark status`'s *recent* list keeps each one.

**Each refusal sends exactly one notification** (added 2026-10-07, after the implementation plan's
review): `footprint_suspect` and `load_failed` send their own types, and every other code sends
`refused`. The front's own refusals — `model_not_found`, `too_many_requests`, `route_not_served`
and `draining` — reach the gate on its status socket, so they show in *recent* and send `refused`
like the gate's; a `401` (the front's journal has it) and `gate_down` (the failure notifier's alert
says it) don't. The brake's own `brake_fired`, sent while the gate is down, ends *They resume once
the gate is back and memory has stayed above 28 GiB available for 5 min.*, since nothing resumes
without the gate.

**Quiet hours live on the phone** (*Visibility and notifications*, above): Android's Do Not
Disturb runs 00:00–05:00 with the ntfy app's high and max channels let through, so only `high`
types sound then. Everything is still sent, at its own priority.

**`spark status`, on the Spark,** answers in the order Dan asks: how much room, what's loaded, what
is waiting, what is paused or held, what happened, and whether everything is up. At the moment of
the refusal examples above, **on the Spark**, `spark status` shows:

```
brightroar · 48 GiB available of 121 · 28 above the brake's 20 GiB line
free for a load: 18 GiB (48 available, less the 24 GiB reserve and 6 GiB still owed)
used by other processes: 26 GiB
loaded      Gemma (gemma-4-26b-a4b)                up to 32 GiB  always loaded     answering 1
            the embeddings (qwen3-embedding-0.6b)  up to 8 GiB   always loaded
            whisper (whisper-large-v3-turbo)       up to 3 GiB   always loaded
not loaded  the coder (qwen3.8-27b)                up to 41 GiB  loads when asked
            marked: it was loading when the brake fired at 03:12 (seen using 26 GiB);
            only you can load it again
waiting     agent → the coder · 2 min of 10 · waiting for you: spark load coder
paused      no · the brake last fired at 03:12, released at 03:40
held        nothing · pins none · sessions: agent's pi, since 08:40
recent      09:12  refused the coder for pi on the Mac: needs 41 GiB, 18 free for a load
            03:40  brake released; reloaded Gemma and the embeddings
health      front ok · gate ok · brake ok (key checked 09:00) · ntfy ok
```

*Up to* is each model's footprint. Through the status socket, which `agent` reads, the *recent* list
holds only `agent`'s own refusals, and other processes go unnamed. `--json` carries the same, for
2b's menu bar. *Used by other processes* is the unaccounted memory, in plain words: idle
`MemAvailable` (117), less what is available now (48) and the loaded models' footprints (43); here
Dan's python job, less the growth the residents are still owed. It shows only when it is above 0
(corrected 2026-10-07, after the implementation plan's final check: this said "isn't 0", but cold
residents hold less than their footprints, so at idle the formula goes below 0). *(Corrected
2026-10-07, after the implementation plan's re-check: this moment had no such line, though its
formula gives 26.)* *(And after the implementation plan's forward-and-back council: the header said
"of 122"; the total rounds down, as available does, so 121.6 reads 121, as `free -g` says.)*
*(Corrected after the final re-review: the coder's brake mark and `agent`'s wait for Dan were
missing from this moment, and its numbers now use the two words.)*

**Each command says what it did, and how to undo it.** All run **on the Spark**, from Dan's
account.

| Command | What it says | Undo |
|---|---|---|
| `spark load coder` | *Loaded the coder in 24 s. It unloads after 60 min idle; `spark pin coder` keeps it.* Or the refusal's message. | `spark unload coder` |
| `spark unload coder` | *Unloading the coder once its 1 request in flight finishes…*, then *Unloaded the coder.* | `spark load coder` |
| `spark pin coder 8h` | *The coder stays loaded until 18:00 (loaded it first, 24 s). `spark unpin coder` ends the pin.* | `spark unpin coder` |
| `spark unpin coder` | *The pin on the coder ended; it unloads after 60 min idle.* | `spark pin coder` |
| `spark make-room 40G` | the list below, one confirmation, then *Unloaded the coder. 50 GiB is free for a load, and 40 GiB of it is held for you until `spark make-room --done` or a reboot; your own requests can load into it, agent's and automatic reloads can't.* (`--for 8h` sets a time.) Asked for more than it can free, say 70 GiB with the 32 GiB python job running: *Unloading everything leaves 61 GiB free for a load, not 70. Free 61 and hold it? [y/N]* | `spark make-room --done` |
| `spark make-room --all` | the full list, one confirmation, then *Unloaded everything. The whole box is held for you until `spark make-room --done` or a reboot; your own requests can load into it.* | `spark make-room --done` |
| `spark make-room --done` | *Hold ended, all 40 GiB of it unused. Nothing to reload: the coder loads on its next request.* When it had unloaded always-loaded models: *… Reloading Gemma.* | `spark make-room` again |
| `make brake-release` | *New loads resume. Reloading Gemma, then the embeddings…* When the gate isn't answering: *The gate isn't answering, so the hold file was removed directly; nothing reloads until the gate is back.* | none needed: the brake fires again if memory falls |
| `spark session hold --model coder --label "pi in Orca"` | nothing while it runs: the session keeps the coder loaded, and ends when the command's stdin closes, or on SIGTERM or SIGHUP, as when pi exits or the Mac sleeps (2b's Mac hooks run it over SSH; Dan's decision, 2026-10-07, after the implementation plan's forward-and-back council) | end the command |
| `make apply` | the diff, then *Waiting for a quiet moment: the coder answered 20 s ago, and it needs 60 s with nothing in flight. Ctrl-C leaves everything as it was; `make apply-now` restarts now.* After 15 minutes: *No quiet minute in 15 minutes. Drain now, holding new requests while the 2 in flight finish? [y/N]* | revert the change, and `make apply` again |
| `make apply-now` | *This restarts the model service now and cuts off the 2 requests in flight (pi on the Mac, agent). Continue? [y/N]* | as above |

make-room's list, with the residents and the coder loaded and nothing else running (9 GiB free for
a load, 117 − 84 − 24), **on the Spark**:

```
To make 40 GiB free for a load (9 GiB now):
  1  the coder (qwen3.8-27b)      41 GiB  loads when asked · idle 12 min
  2  Gemma (gemma-4-26b-a4b)      32 GiB  always loaded · the web UI and photos use it
  3  the embeddings               8 GiB   always loaded
  4  whisper                      3 GiB   always loaded
Unloading 1 leaves 50 GiB free for a load. Unload 1? [y/N]
```

And `no_fit`'s next step, from the refusal examples' moment (18 GiB free for a load, the coder
not loaded): `spark make-room 41G` unloads Gemma, leaving 50 GiB free for a load, 41 of it held
for Dan; his retry loads the coder into it, and the hold shrinks to the 9 GiB the coder didn't
need from it. `--done` ends that, and Gemma, which needs 32 GiB with 9 free for a load, waits
(`resident_waiting`) until the python job ends.

### Speech

- A whisper.cpp **interactive** instance (resident: voice input, dictation) and a **batch** instance
  (on demand: lectures). Whisper large-v3-turbo (vocabulary `prompt`, ~99 languages) and Parakeet TDT
  v3 GGUF (speed). NeMo's Parakeet with per-request phrase boosting is tested in the Phase 3 speech
  comparison and adopted only if it clearly wins.
- `verbose_json` word timestamps, language detection, and ~170 MB uploads allowed through every hop.
- Speaker labels come from the diarization service in OpenAI's `diarized_json` shape; whether it also
  returns speaker embeddings (for speaker identification in Dan's pipeline) gets confirmed against
  that pipeline.
- Dan's audio pipeline changes in its own repo. Its current Mac baseline — on a 51.6-minute lecture,
  mlx-whisper 253 s, parakeet 80 s, pyannote ~180 s — is the yardstick for the speech comparison.

### Docs site and scenarios

- `website/` (Quarto, documentation only): overview · architecture · how-to runbooks · endpoint
  reference (for Dan's own code; the model table is generated from the registry) · Stack (generated
  from `versions.yaml`) · **Scenarios** · design notes (this plan and the implementation plans, in
  `website/design/`). There is no top-level `docs/`. A SHA-pinned GitHub Actions workflow publishes
  to GitHub Pages — Dan confirms before the first publish.
- **Scenarios** have IDs that `spark doctor` checks and the phases' "done when" criteria refer to.
  Each page gives the situation · what happens · what Dan sees · how to override · status (planned /
  built / verified with a date). A behaviour change updates its scenario page **and** its check in
  the same commit. The seed list:
  **S01** morning start · **S02** big job while an agent works · **S03** doesn't fit, interactive ·
  **S04** doesn't fit, unattended · **S05** memory critically low · **S06** an agent's long GPU step ·
  **S07** lecture → transcript with speakers · **S08** batch versus interactive · **S09** phone away
  from home · **S10** homelab app at 3 am · **S11** trying a new model · **S12** long agent run, laptop
  closed · **S13** Spark freezes while away · **S14** gate down · **S15** NAS down · **S16** Postgres
  down · **S17** changing models mid-task · **S18** rebuild after a factory reset · **S19** Claude Code
  building a pipeline elsewhere · **S20** web search from the phone · **S21** Pixeltable over datasets
  (backlog) · **S22** on another tailnet via WireGuard (pi and the API work; the web UI waits).
  Added since: **S23** upgrade day (2026-09-25).

### Repo layout

```
stack/      models.yaml · versions.yaml · compose.yaml · litellm/ · templates/ · systemd/ · host/ · synology/
spark/      pyproject.toml · uv.lock · src/spark/ (cli, front, gate, launch, registry, render, memory, brake, notify, doctor, bench) · tests/
clients/    pi/ · opencode/ · claude-code/ (hooks) · swiftbar/
website/    Quarto docs site (_quarto.yml, scenarios/, how-to/, reference/, design/)
.githooks/  .github/workflows/
```

The root keeps `README.md`, `CLAUDE.md`, `LICENSE`, `changelog.md` and `cosmicbboy-local-ai.md`,
plus a `Makefile` — the front door.

(2026-09-25: Phase 1 keeps the Compose file and the units as templates in `stack/templates/`, which
`spark render` fills; there is no `stack/compose.yaml` or `stack/systemd/`. There is no `clients/`
yet either: `spark clients pi`, in `spark/src/spark/clients.py`, renders pi's provider.)

### Deploy workflow (Mac ⇄ GitHub ⇄ Spark)

- **Edit on either machine** (as Dan, never as `agent`). Pushes happen only with Dan's explicit OK.
  GitHub credentials exist only in Dan's accounts (a 0700 home on the Spark), and the Spark gets a
  fine-grained token that can push to this repository only (`website/how-to/spark-session.md`).
  `make hooks` turns the leak-guard hooks on in each clone; the private denylist, with at least one
  term, exists on both machines. (Corrected 2026-09-25: this said bootstrap installs the hooks;
  `make hooks` does, in each clone.)
- **First time on the Spark:** `git clone` (a public repo — no credentials needed) → the runbooks,
  in the order `website/how-to/index.qmd` lists them, where `make bootstrap` (sudo once: users,
  directories including the secrets folder, the GPU-set hold, earlyoom, the firewall) comes before
  Dan creates the private files (per-service secrets, in the folder bootstrap made; private values
  such as the NAS and LAN addresses) → from Phase 1, `make apply` stages the systemd units and the
  Compose project, and `make install-units` (sudo) installs root's own copies of them, again
  whenever they change. (Corrected 2026-09-25: this put the private files before bootstrap, which
  creates their folder, and had bootstrap install the units and Compose, which Phase 1's
  `make install-units` does. Corrected again 2026-09-25: it said `make install-units` links the
  units, with sudo once; Dan chose root-owned copies, reinstalled with sudo when they change.)
- **After any change:** `make apply` on the Spark, or `make deploy` from the Mac (SSH, then
  `git pull && make apply`). `spark apply` renders (registry + pins + private values → concrete
  configs in a deploy directory outside the repo), validates with each tool's own checker, shows the
  diff, restarts only what changed (llama-swap waits for idle models or asks), then runs a quick
  `spark doctor`. It warns about uncommitted changes. It never writes what root runs: when the units
  or the Compose project change, it stages them and stops, `make install-units` (sudo) shows the
  staged files root will run and installs them, and the next `make apply` restarts each unit still
  running the older definition, llama-swap only when idle. (2026-09-25: Phase 1 builds less than
  this, for now. Its `spark apply` lists the files that change, not their diff:
  `make install-units` is what shows root's files as diffs. It runs no `spark doctor` itself, so
  `make doctor` follows it. While models are loaded it refuses a llama-swap restart, unless
  `--now`, rather than waiting. And there is no `make deploy` from the Mac yet: whether Phase 1
  builds it is left for its close, Task 17. S17, a Phase 2 scenario, still expects the diff and
  the wait. *Decided 2026-09-28, at that close (Dan):* no `make deploy`. Work runs on the Spark by
  default, so `make apply` there is the path.) (*Phase 2a, Dan's design, 2026-10-05; S17:*
  `make apply` shows the **diff** of what it would change. For a change that needs a llama-swap
  restart, it then **waits until no request has been in flight for ~60 s on any engine**, by the
  front's counts: requests keep being served, and it shows what it's waiting on. Then it restarts
  llama-swap; the gate reloads the residents one at a time, and on-demand models reload on their
  next request. A request that arrives meanwhile waits in the front, like any request that needs a
  load. Ctrl-C leaves nothing changed; `make apply-now` restarts at once, after asking to confirm.
  A change that needs no llama-swap restart applies at once.) (*Revised 2026-10-07 after the
  council; S17:*
  - **What waits for the quiet moment:** a llama-swap restart, and a restart of the front, which
    would cut off every request in flight. The front is restarted only when its own modules change,
    not for every change to the app, and imports all it uses at start. It reads the ~~registry's
    model names and roles, and the~~ key digests through `LoadCredential=`, at start too, so a
    change to ~~either~~ them restarts it, through the same wait (added after the re-review; since
    Dan's decision of 2026-10-07, after the implementation plan's forward-and-back council, the
    front takes the model names and roles from the gate's events, so a registry change no longer
    restarts it, while the key digests and the private key list still do). A change to the gate's or
    the brake's code restarts them ~~at once~~ (at once when nothing waits for the quiet moment; in
    the fixed order below when something does): a gate restart keeps loaded models serving and
    rebuilds its state, the front asks again for the requests it was holding, with their original
    deadlines, and the brake is back within 2 s. *(Clarified 2026-10-07, after the implementation
    plan's re-check, the controller's ruling:* when a llama-swap or front restart waits for the
    quiet moment, the brake and the gate restart with it, after the drain, in a fixed order — the
    brake, the gate, the front only if its own files changed, llama-swap last. The restarting hold
    is kept in the gate's persisted state, so it survives the gate's own restart, and is released
    once llama-swap answers again; a request held through it reads `restarting`, never
    `llama_swap_down`. *(Added the same day, after the final check:* `make apply` renews the hold
    every 15 s and the gate ends one not renewed for 60 s, so an apply that died holds for about a
    minute at most; every end does the same work, the residents' reload queued ahead of anything
    else; and the front drops the hold once the gate has been gone a minute, so rule 8 holds. A
    front restart inside the apply, which comes only when its own files change, cuts the requests it
    holds, as any front restart does.))
  - **The wait has a deadline:** up to 15 minutes for ~60 s with no request in flight, then apply
    offers "drain now", which holds new requests and lets those in flight finish, through the
    front's drain, and then `make apply-now` (the session's ruling). A request that arrives during
    the restart waits for its key's wait, and is refused with `restarting` if llama-swap isn't back
    by then.
  - **Nothing is written until the drain is done:** the registry, llama-swap's config and the app
    go out just before the restart, so Ctrl-C leaves nothing changed, and the gate re-reads its
    registry at that moment.
  - **The coder swap's order:** `make apply`, whose restart stops every engine, the old coder
    included, so the new registry never meets a loaded model it doesn't list; then `make pull`,
    since the pull reads the deployed registry; then `make clients`. Until the pull ends, a
    request for the new coder is refused with `not_downloaded`. The gate and the brake count each
    loaded model at the footprint of the registry that loaded it, never at 0.
  - **The first deploy:** `local-ai-front.socket` owns 9100 from the start, so llama-swap has to
    leave it first, and `website/design/phase-2a.md` writes the rollback, Phase 1's layout with
    llama-swap on 9100 and the client keys, before the cutover.)
- **Hybrid runtime:** Compose for Open WebUI and SearXNG (later LiteLLM and Postgres); systemd for
  llama-swap and the gate; engines are pinned binaries or on-demand containers; host setup happens in
  bootstrap; ntfy and the watchdog run under Compose on the Synology; the Mac pieces install with
  `make clients`. *Kept 2026-10-07 (Dan, asked whether the Spark should move to Docker):* bare metal
  for the engines, llama-swap and 2a's front, gate and brake; Compose for apps that ship as images.
  Docker's own processes held about 160 MiB that day, so memory isn't the reason; the GPU without a
  toolkit in between, systemd's restarts and sandboxing, Docker access being root access, and 10–20
  GB CUDA images on a shared 1 TB disk are. Full containerization gets revisited only if a needed
  engine ships solely as a container or untrusted model code wants stronger isolation; the repo,
  not an image, is what makes the box reproducible (S18). [The Q&A](phase-2a-qa.md) has the reasons.
- **The Makefile is the front door.** `make help` lists `bootstrap, apply, deploy, status,
  logs s=<name>, doctor, test, docs, clients`; the logic lives in the Python CLI. It stays portable to
  macOS's older GNU make. (2026-09-25: after Phase 1, every target here exists but `deploy`; see
  *After any change*. Decided 2026-09-28, at Phase 1's close: there is no `deploy` target.)
- **Python via uv everywhere** (uv is installed on both machines — on the Spark as a per-user install
  in `~/.local/bin`). `spark/` is a uv project (`pyproject.toml` + `uv.lock`; uv's `required-version`
  pinned in `versions.yaml`); the Makefile calls `uv run --frozen spark …`; standalone helper scripts
  carry PEP 723 inline metadata and run with `uv run`. No system Python, no pip. One Python minor
  version everywhere, pinned in `spark/.python-version` (3.12); it lives in `spark/` because uv
  looks for it only in the project directory. `spark apply` builds the gate's environment with
  `uv sync --frozen` somewhere the `spark` user can read, and sets `UV_PYTHON_INSTALL_DIR` so a
  Python uv downloads lands there too (Phase 1). (Corrected 2026-09-25: this said bootstrap checks
  where uv and its managed Pythons live; that is `spark apply`'s job.)

### Where work runs

Work runs on the Spark by default (Dan, 2026-09-25). Before that, the Mac session wrote the repo's
code, tests, CI and docs, and the Spark session ran only what touched the GPU, memory, systemd or
Docker.

- **Spark session** (Claude Code as Dan, in tmux on `brightroar`): everything that doesn't need the
  Mac — repo code (CLI, gate, launcher) with unit tests, config templates and render tests, the
  Makefile, bootstrap, the leak hooks, the docs, and anything touching the GPU, memory, systemd or
  Docker (engine builds, llama-swap, the gate, the brake, `spark doctor`, measurements and
  comparisons).
- **Mac session:** only what needs the Mac — the Mac clients and their config (SwiftBar, pi and
  OpenCode configs, harness hooks on the Mac); changes under `.github/workflows/`, which the Spark's
  repository-only token can't push (a merge that brings one in included, into `main` or from `main`
  into a branch after a Dependabot Actions bump); the site render until Quarto is on the Spark; and
  the Mac check of what the Mac also runs.
- **Dan:** sudo (`make bootstrap`), interactive logins (Tailscale, GitHub on the Spark, Claude Code
  for `agent`, the Hugging Face token), secret values, the Synology's settings, and approving every
  push.
- **Handoff, one session at a time:** the active session owns the phase branch; at a switch it
  commits, the branch is pushed with Dan's OK, and the other machine pulls. Both sessions read this
  plan and the implementation plans from `website/design/`; every task is labelled **[Spark]**,
  **[Mac]** or **[Dan]**. From the Mac, read-only checks on the Spark over SSH happen only with
  Dan's OK.

### Testing

- **Unit tests (TDD):** admission decisions, brake ordering, idle policy, refusal text — pure
  functions with fixtures. They run anywhere: the Mac, the Spark, CI.
- **Render tests:** golden files plus each tool's own validator; CI runs `spark render --check`.
  (2026-09-25: Phase 1's CI runs `spark render --out`, which fails on anything render refuses; there
  is no `--check` yet. Its render tests assert what matters in each rendered file rather than
  comparing golden files, and `spark apply` runs llama-swap's own `-validate`.)
- **`spark doctor`** runs only on the Spark, after any change: one check per scenario, plus — a second
  load evicts nothing; refusals through real clients on chat, streaming, `/v1/responses`, embeddings
  and transcription; direct llama-swap calls need a key; allow-lists hold; idle unload fires; the
  brake fires at raised thresholds; a **privacy canary** (a unique string sent through chat and
  speech-to-text must never appear in any store); a 170 MB upload; `words[]` present; a bypass sweep
  (every load path shows up in the launcher's log); a reload during generation; a 2-hour soak;
  earlyoom `--dryrun`; five reboots (one with the NAS unplugged); a NAS outage during a load; Postgres
  stopped and a full disk; a cold first load through each client; a snapshot restore under an agent;
  a clean-host restore. It refuses to run against an untested llama-swap version. S13 and S18 are
  dated manual drills. *(Phase 2a, noted 2026-10-07, from its council: from 2a these run through
  the front. "Direct llama-swap calls need a key" becomes "a client's key is refused at llama-swap's
  port, and the front refuses every route outside its list"; the bypass sweep checks that every
  load had the gate's ticket; "a reload during generation" becomes `make apply`'s restart through
  the drain; the 170 MB upload goes through the front, and the privacy canary checks its spool,
  which is emptied when each request ends. S14's drill kills each of the four services, freezes the
  front with SIGSTOP to trip its watchdog, and sends llama-swap a clean SIGTERM. Added after the
  re-review: a crash-loop check, run as `agent`, kills the front again and again, or makes it fail
  at start, and confirms that 9100 stays with PID 1, that binding it fails and that it never
  answers as anyone else; and a test that the gate receives each caller's uid.)*

### Backups, recovery and upgrades

- **Nightly:** Open WebUI data, gate state and `agent` repos (and the Postgres dump from Phase 3) go
  to the Synology through DSM's rsync service with a non-admin account (Synology SSH is admin-only);
  14 copies are kept; a failure sends a high-priority ntfy.
- **Recovery runbook:** factory reset → the Phase 0 runbooks → delete the old Tailscale node before
  rejoining (otherwise the box comes back as `brightroar-1` and every client breaks) → restore →
  `spark doctor`. Move the GPU set to current at, or right after, the first bootstrap, so a rebuilt
  box doesn't sit on the factory set until the next upgrade day; leave NVIDIA's web updater alone
  until it is known how it treats apt holds (an open item).
- **Weekly upgrade day, on Saturdays** (monthly until 2026-09-24). Skipping one is fine; the next
  one catches up. Automated PRs collect version bumps: Dependabot proposes GitHub Actions and
  `spark/uv.lock` updates on Fridays, against `main`, merged or rebased but never squashed (CI scans
  every commit message). (Corrected 2026-09-28, from Phase 1's council: its uv PRs can change
  `spark/pyproject.toml` too, as PR #3 raised a floor there, and could widen the `huggingface_hub<2`
  cap; `updates.md` says to read that diff.) The lock takes only releases at least seven days old, a
  rolling window (`exclude-newer = "7 days"` in `spark/pyproject.toml`), and Dependabot's uv PRs
  wait as long (`cooldown` in `.github/dependabot.yml`); an urgent fix gets a per-package exception
  (Dan's decision, 2026-09-27; `updates.md` has the steps). `stack/versions.yaml`'s pins, the
  workflows' `version:` inputs, uv's `required-version` and the gitleaks pin still move by hand
  (Backlog). Bumps are applied one component at a time → render → validate → back up databases →
  deploy → `spark doctor` → changelog entry.
- **Everyday updates:** `sudo apt update && sudo apt upgrade` any time, and snaps refresh
  themselves; neither can move the GPU stack while every member of the set is held.
  `website/how-to/updates.md` says when that stops being true: midway through upgrade day, and for a
  newly installed member of the set until `make hold-gpu`. apt logs every run in
  `/var/log/apt/history.log`.
  After each run needrestart restarts services still on replaced libraries; DGX OS keeps it off its
  dashboard, and Phase 1 keeps it off the stack. Dan runs apt out of habit. Holds are fine as long
  as there's a written plan for when and how the held set moves, and for getting the services back
  after any update (2026-09-24).
- **The GPU set moves only on upgrade day, as one:** the kernel, the NVIDIA modules built for it,
  the driver and CUDA. `make bootstrap` holds them together, because the modules need one exact
  driver version and DGX OS ships all four in one transaction. Holding only part of the set would
  let `apt upgrade` install a kernel with no NVIDIA module. The move, in tmux:

  1. Release the set.
  2. `dpkg --configure -a` and `apt full-upgrade`, answering no if apt would remove the NVIDIA
     modules metapackage with no other in its place, or install a kernel without modules. It is
     also no if apt would swap the metapackage for another driver branch's, a move planned and made
     by hand.
  3. Re-hold with `make hold-gpu` (bootstrap's `--hold-gpu` mode, the hold and nothing else).
  4. Check that the kernel GRUB boots has an NVIDIA module: the newest kernel's, and that GRUB
     will boot it, read from `grub.cfg`'s entry 0 and default and from `grub-editenv`. That GRUB
     boots the newest kernel is not yet checked on this box. (Checked 2026-09-27, in Phase 1's
     Task 12: entry 0 boots the newest kernel, the `default=` lines are the stock two, and
     `grub-editenv list` is empty. Installing a kernel rebuilds the menu, so every upgrade day
     still runs the check.)
  5. Reboot.
  6. Check the GPU and that the running kernel's modules are held, then `spark doctor`.

  From Phase 1, `make upgrade-gpu` runs steps 1 to 4 as one command, and runs the GRUB check itself
  (Dan's decision, 2026-09-25): before it releases the set, where a failure refuses with nothing
  moved, and again after the move, where a failure says not to reboot. It names kernels by version,
  never a GRUB id or UUID. Kernel and NVIDIA driver security fixes wait for upgrade day, or bring it
  forward. Runbook: `website/how-to/updates.md`.

## Phases

Every phase ends by updating scenario statuses, the architecture diagrams (its built parts turn
solid), the docs site, `changelog.md` and `README.md` §Current state. Tasks are labelled by where
they run.

**Phase 0 — Guardrails, prep, docs scaffold** — done, 2026-09-25

- [Mac] `Makefile` + `make bootstrap` skeleton · leak guards (`.githooks`, gitleaks in CI) ·
  `versions.yaml` scaffold · `website/` scaffold with scenario pages marked *planned*; a CI build
  (publishing after Dan's OK) · record the new private locations in the vault's entry note.
- [Spark] check uv (record the version) · inventory memory holders · directory layout · bootstrap
  dry-run · verify `agent` can run CUDA without docker.
- [Dan] bounce the wired NIC to `.201` · run `make bootstrap` (headless, apt holds, earlyoom, ufw SSH
  only, users `spark` and `agent`) · Claude Code install + login for `agent` · Tailscale join, MagicDNS
  + HTTPS, ACL grants that keep today's access working; confirm the tailnet's route home (read
  privately) · per-service secret files + the Hugging Face token · the same Claude Code plugins on
  the Spark as on the Mac · give the first Spark session its private context (kept outside the repo).
- *Done when:* a `free -g` baseline is recorded; a planted fake secret is blocked; `agent` can't read
  Dan's files or use docker; the site builds.
- *Status:* done, 2026-09-25. Its tasks ran from 2026-09-23 to 2026-09-25, and every done-when
  criterion above holds; the last step, merging `phase-0` into `main`, follows this note. What it
  left open is carried to Phase 1 in the retrospective.
- *Retrospective:* [Phase 0 — retrospective](phase-0-retro.md): what was built, where it departed
  from this plan and why, what Phase 1 inherits, and the rules that now prevent rework.

**Phase 1 — First milestone: web UI + pi**

- [Spark] pinned llama.cpp · llama-swap from a minimal registry (`routing:`, `apiKeys`, 127.0.0.1,
  `captureBuffer: 0`) with a static, safe model set — Gemma 4 26B-A4B (resident vision chat),
  Qwen3-Embedding-0.6B, whisper.cpp large-v3-turbo (interactive), and the starter coder
  Qwen3.6-35B-A3B — whose combined footprint `spark render` checks · the **minimal brake** · Open WebUI
  + SearXNG via `tailscale serve` · pi and Claude Code in tmux as `agent` · a basic `spark status`
  · `make upgrade-gpu` (upgrade day's GPU-set steps as one command, its GRUB check included) ·
  updates never take the stack
  down for good: a needrestart override keeps a routine `apt upgrade` from restarting `local-ai-*`
  units (DGX OS does the same for its dashboard); after a Docker upgrade, a reboot or upgrade day the
  stack comes back by itself, and `make doctor` v0 confirms it. v0 checks Phase 0's guardrails (the
  leak hooks, the GPU set held, earlyoom, ufw, the secrets folder closed) and the stack's smoke
  checks: weekly upgrade day needs that before Phase 2, where `spark doctor` proper, one check per
  scenario, arrives. `website/how-to/updates.md` gains the recovery steps. (Corrected 2026-09-26,
  from Phase 1 Task 10's scan: after a Docker upgrade the web services don't always come back by
  themselves. Their unit requires Docker and stops them with `docker compose down`, so a restart of
  Docker restarts them, but an upgrade that stops Docker and starts it again leaves them stopped
  until `systemctl start local-ai-compose`, which `make doctor`'s stack-units check catches. Which
  one DGX OS's Docker upgrade does is not yet tried on this box; Task 16's drills find out.
  *Corrected 2026-09-28:* they couldn't. The routine upgrade Task 16 ran moved no Docker package,
  so this waits for an upgrade that does, under *To verify on the box*.)
- [Mac] pi config + SSH tunnel · CI's render step, the site render and the merge that brings it
  into `main`.
- *Done when:* S09 and S20 work on the phone; pi completes a task from the Mac and from tmux;
  reattaching works; the minimal brake fires at raised thresholds; a fresh clone + `make bootstrap` +
  `make apply` reproduces it; after a routine `apt upgrade` and after a reboot, the stack is serving
  again without a hand on it.
- *Status:* done, 2026-09-29. Tasks 1–17 ran on the Spark from 2026-09-25 to 2026-09-28, and
  every done-when criterion above holds, with the routine upgrade's weaker evidence (Dan's
  decision: *To verify on the box* keeps the upgrades still to come). Task 18 ran on the Mac on
  2026-09-29: CI renders the real registry, and the Mac check and the site render passed. The last
  step, merging `phase-1` into `main`, follows this note; Dependabot's PRs #1–#3 merge after it.
  *(Both done 2026-09-29: the merge is `ff205f9`, and the three PRs were rebase-merged, each once
  its CI was green.)*
- *Retrospective:* [Phase 1 — retrospective](phase-1-retro.md): what was built, where it departed
  from this plan and why, what the reviews found, and what Phase 2 inherits.

*Between Phases 1 and 2, not a phase (Dan, 2026-09-30):* Orca and the web UI on the Mac. Both
already worked with what Phase 1 built — pi started from Orca reaches the Spark over `make tunnel`
(checked 2026-09-29), and the web UI answers the Mac at the phone's address — so what they needed
was the decisions recorded here, Orca's two settings (Manual, telemetry off) and the docs:
`website/how-to/orca.md`, the Mac in `deploy.md`'s *The web UI*, a note in `pi.md`, and Orca in
`README.md` §Current state.

**Phase 2 — Fit check, brake, visibility**, in three sub-phases

Phase 2 is built as three sub-phases, each with its own short plan, review and merge: **2a the gate
→ 2b visibility → 2c the lab**. The hardening and measurement items it inherited fold into whichever
sub-phase touches their code (Dan's decisions, 2026-10-05). *(Until 2026-10-07 this was a single
phase, whose done-when was S01, S02, S03, S05, S06, S11, S12, S13, S14 and S17 verified. Its items,
those from Phase 1's close and from Orca on the Mac included, now sit in the sub-phase that takes
each, below; git history keeps the line as it stood.)* The scenario pages keep `phase: 2`, since the
scenario check knows only whole phases; each page's text names its sub-phase. *(Corrected
2026-10-07, after the council: that holds for the Phase 2 pages; S05, which 2a changes, and S12,
which 2b verifies, stay `phase: 1`, `built`.)*

**Phase 2a — The gate** (designed 2026-10-07: *The front and the gate*, and rules 1–9; revised the
same day after the council, with Dan's decisions and the session's rulings; for Dan's approval,
after which `website/design/phase-2a.md` is written. As first written, its items had the brake in
the gate, llama-swap on 9101, the reserve at 22, the coder at ~38 and a done-when of scenarios
only; git history keeps them.)
*Approved by Dan, 2026-10-07. Its implementation plan: [Phase 2a — implementation
plan](phase-2a.md), written the same day, and revised the same day after its forward-and-back
council, with Dan's fourteen decisions (Revisions).*

- [Dan] **ntfy** on the Synology, in Container Manager, at the start of 2a (moved up from 2b; Dan,
  2026-10-05), pinned at v2.28.0 by its index digest, with no public relay (the ntfy row), over the
  tailnet or the home LAN only. With it, so that every 2a alert has a way to reach Dan:
  - the Synology on the tailnet, if it isn't, and the ACL grant that lets the Spark and Dan's phone
    reach ntfy's port, reviewed in the task that adds it;
  - the ntfy app on the Android phone, with the NAS as its server and instant delivery, and quiet
    hours set on the phone (*Visibility and notifications*);
  - a publish-only token for each publisher, the gate and the failure notifier, each in a secret
    file, and one for the brake's own alert while the gate is down (added after the re-review);
  - the **private values file**, holding ntfy's address, which `CLAUDE.md`, `README.md` §My
    environment and the vault's entry note record when it is created.

  So the gate notifies from its first day, and S14's alert is verified in 2a. The Mac's alerts come
  with 2b's menu bar (Dan, 2026-10-07).
- [Dan] `make bootstrap` again, for 2a's users and permissions: `spark-front` and `spark-pull`, and
  the secret files `0600 root:root`. Then the client keys' digests, made with a sudo one-liner that
  shows no key. *(Added after the re-review:)* the existing Hugging Face cache moves to
  `spark-pull`, hf_xet's logs included, with a setgid group so that `spark` reads the model files.
  *(Added after the final re-review:)* in the same task, whisper's `--tmp-dir`, today
  `/var/lib/local-ai/hf/tmp` inside that cache, moves to a folder of `spark`'s outside it, so
  whisper can still write its temporary files and the pull's user can't read uploaded audio;
  llama-swap's `ReadWritePaths=` names the new folder.
- [Spark] ntfy's records, in one commit: `stack/synology/compose.yaml` with the digest, a
  `stack/versions.yaml` row (`where: [synology]`), the Stack page generated again, an `updates.md`
  row, and a test that the compose file's digest is the pin. No address, token or hostname goes in.
  *(Dan, 2026-10-07: the file is `stack/synology/ntfy/compose.yaml`, deployed with any Compose
  helper, Portainer's stacks today, or plain `docker compose up -d`; the NAS's address, the topic
  names and the tokens arrive as variables it names. 2b's watchdog follows the same pattern.)*
- [Spark] **the front and the gate** (*The front and the gate*): the front as `spark-front` on the
  socket-activated 127.0.0.1:9100, with the key digests, the route list and its limits; the gate on
  its two socket-activated sockets, with SO_PEERCRED; `local-ai-brake` kept, reading the gate's
  in-flight record, falling back to Phase 1's order, with the **rate-of-fall watch** (rule 5);
  llama-swap at 127.0.0.1:900 and the engines at 800 and up, with `CAP_NET_BIND_SERVICE`, internal
  keys only, `healthCheckTimeout` 180 s, `Restart=always` and its sandboxing, `NoNewPrivileges=`
  tested from a cold boot; `spark launch`'s tickets, backstops and file check; the failure notifier
  on the four services; systemd sandboxing for the front and the gate; uvicorn and Starlette in the
  lock; the polkit rule's six services (seven since 2026-10-07, with the S05 drill's oneshot).
  Checked as `agent`: binding 9100 while the front restarts, or 900 while llama-swap restarts,
  fails; the control socket refuses it; the front refuses every route outside its list. The docs and
  code that talk to 9100 move with it: `deploy.md`'s log step, doctor's probes, `status.py` and
  `apply.py`. *(Added after the re-review:)* the start and trigger limits set so that no crash loop
  frees a socket, and a **crash-loop check, run as `agent`**: kill the front again and again, or
  make it fail at start, and 9100 stays with PID 1, binding it fails, and it never answers as anyone
  else; the uvicorn protocol subclass that gives the gate each caller's uid, with its test; and the
  brake's own alert while the gate is down.
- [Spark] **`cap_drop` for Open WebUI and SearXNG** (Dan's choice, 2026-10-07, after the
  re-review): both containers drop every capability and add back only what each is found to need,
  checked by the web UI working end to end and a search working, so root's containers with host
  networking can no longer bind llama-swap's or the engines' ports. *(Before the cutover, made and
  deployed from `main`, since nothing deploys from the phase's branch before then: Dan's decisions,
  2026-10-07, after the implementation plan's forward-and-back council.)*
- [Spark] **in the gate:** admission, one load at a time, on rule 9's formula, with the queue
  (Dan's keys first), the tickets, the per-key waits and inline refusals, and the top GPU holder
  taken from `nvidia-smi`'s per-process list (from Phase 1's close); the drain; idle unloading at
  60 minutes; pins; sessions; the residents preloaded one at a time; make-room, with its hold and
  `--all`; `spark load` and `spark unload`, S01's override; *release*; the brake's release, with
  its bounds, and the reloads after it; notifications, from the registry's list of types, with
  every refusal's message and each command's confirmation as *What you see in Phase 2a* words them
  (added at the UX pass). The weekday preload's setting, off by
  default, too, unless Dan leaves it until he turns it on, as the council's security review
  suggests. *(Ruled 2026-10-07, with the plan: not in 2a; the Backlog, until Dan asks for it.)*
- [Spark] `spark status` (requests waiting, recent refusals, the growth owed, the holds,
  unaccounted memory, `--json`, in *What you see in Phase 2a*'s plain words and layout) · `spark doctor` with the front, the gate, the brake and ntfy, a
  check for each of 2a's scenarios, doctor's version-drift checks, and the rendered llama-swap
  config checked against v257's embedded schema (both from Phase 1's close) · `make apply`'s diff,
  quiet wait, deadline and drain, and `make apply-now` (*Deploy workflow*).
- [Spark] **the budget** (rule 9): render's check corrected; the reserve stays 24 GiB (lowered to
  22 and put back, the same day); the CUDA-allocatable ceiling measured by rule 9's method, and the
  registry following the measurement; and, during the soak, a check that each engine's RSS growth
  accounts for its growth in `MemAvailable`, which *owed* relies on (added after the re-review;
  after the final re-review, its anonymous RSS, `RssAnon`, compared with `MemAvailable`'s fall and
  with `nvidia-smi`'s per-process figure).
- [Spark] **the coder** (Dan, 2026-10-07): Qwen3.8-27B replaces Qwen3.6-35B-A3B, by Phase 5's route
  A — llama.cpp b11146 as deployed, Unsloth's GGUF, MTP. The repo is `unsloth/Qwen3.8-27B-GGUF`, at
  revision `4ca720788d1e01f1bff70c033e0d0028fd02e502` (last modified 2026-08-20, so past the
  seven-day rule), and the file `Qwen3.8-27B-UD-Q4_K_XL.gguf` (17.6 GB): Dan's pick, speed first,
  since a dense model is bound by memory bandwidth here. Its header: architecture `qwen35`, 65
  blocks (64 and one MTP layer; `nextn_predict_layers = 1`, so `--spec-type draft-mtp`, as the
  coder has today), `full_attention_interval` 4 (16 attention layers, 4 KV heads × 256), and a
  native context of 262,144. Every model stays at its full context with an f16 KV cache (Dan).
  - **Its footprint is estimated at ~41 GiB** until a soak measures it (raised after the council
    from ~38): weights ~16.4; the KV cache ~17.0 at 68 KiB a token, 64 for the 16 attention layers
    and 4 for the MTP layer; and about 7.5 for the rest, the compute and long-input buffers, a
    2 GiB prompt cache and 8 context checkpoints at about 150 MiB each (48 recurrent layers, by the
    council's arithmetic from the header). The set then comes to ~84 of the 102 ceiling. Its
    registry entry sets `--ctx-checkpoints 8` and the 2 GiB prompt cache explicitly, as today's
    coder's does, with comments written fresh for it.
  - **What b11146 supports,** read in its source by the council's toolstack review on 2026-10-07,
    not run: `qwen35` maps to a model of its own, and 64 trunk layers read as 27B; `--spec-type
    draft-mtp` loads the MTP block from the main file, which carries every tensor that needs; and
    f16 is the KV cache's default. The repo's separate MTP head and its `mmproj` files (Qwen3.8-27B
    reads images) aren't needed, and aren't in its registry entry.
  - **What 2a measures first, in order, before it becomes the default:** (1) b11146 loads it and
    drafts with MTP, and the server's log gives the draft's acceptance rate; (2) the load's peak,
    sampled 10×/s, and its cold fall in `MemAvailable` at full context, `MemFree` recorded first;
    (3) a soak to about 250K tokens with edits, regenerations, pi's tool calls and thinking, for its
    checkpoints, prompt cache and long-input buffers, after which its footprint is marked measured;
    (4) time to first token at 32K, 128K and 250K, against pi's timeouts, and decode at long
    context; (5) its load time, cold and warm, for the load's deadline; (6) a busy coder's stop
    time, for `GRACE_S`. *(Clarified 2026-10-07, after the implementation plan's review: "the
    default" means for real work, Dan's and `agent`'s first real tasks with it, which come after
    these. Both pis list it from the swap on, since `make clients` follows the pull, as below, so
    these measurements run in pi.)*
  - **Route A's speed** — decode, time to first token and prefill, in pi, in Phase 5's order — is
    recorded against Phase 5's table. Its reported decode is 15–27 tok/s, against today's coder's
    93 measured; Dan accepts the trade, with route B (38–50 reported) in 2c and routes C and D in
    Phase 5.
  - Qwen3.6-35B-A3B leaves the registry, and its files stay on disk. The swap runs in *Deploy
    workflow*'s order: apply, then the pull, then `make clients`.
- [Spark] **the page-cache drill,** before the coder becomes the default (for real work, as above;
  Qwen3.6's way back stays written down until then): fill the page cache, only until `MemFree` is
  under the coder's cold load, load the coder, and watch for E.1's slow reclaim. Its result settles
  rule 1's cache drop (*Page cache and the launch check*).
- [Spark] **from Phase 1's close (2026-09-28):** `--alias`, so a reply names its model rather than
  its file; a soak at full context that measures every footprint, checkpoints and prompt caches
  included (rule 6); the brake's timings measured on a busy engine (`GRACE_S`,
  `FLOOR_TOLERANCE_GIB`), and the lag before an unloaded engine's memory shows in `MemAvailable`;
  earlyoom's order, and swap and swappiness, at the real thresholds (swap down to 24 GiB available
  only: corrected 2026-10-07, after the implementation plan's re-check); SearXNG's request timeout;
  a higher `--slot-prompt-similarity` for Gemma, weighed, with the registry's other changes; a test
  that ties the code's version assumptions — the front's and the gate's on llama-swap v257 among
  them — to `stack/versions.yaml`; and, by Dan's decision after the council, the pull's own user,
  `spark-pull`, before the coder's pull, the secret files root-only, and llama-swap's sandboxing.
  Phase 1's close also moved llama-swap behind a Unix socket with the gate, since anyone on the box
  can take 127.0.0.1:9100 while it restarts; v257 can't listen on one, so socket activation and the
  ports below 1024 close that risk instead (*Open items and risks*).
- [Spark] **`agent`'s own GPU jobs** (Dan, 2026-10-07): root sets `agent`'s processes an
  `oom_score_adj` that `agent` can't lower, at login (a root-owned `pam_exec` line for sshd and a
  `user@` drop-in, for instance), with the engines' values reviewed so that `agent`'s job is killed
  first. In 2a if it holds when tested as `agent`; otherwise in 2b (*Open items and risks*).
- [Spark] `spark clients` writes `agent`'s pi `httpIdleTimeoutMs`, 900,000, beside its provider.
- [Mac] pi's provider is rendered again after the deploy — `make clients` on the Mac, and the same
  for `agent` on the Spark — so pi lists the new coder.
- *Scenarios:* S01 (the gate's part), S02, S03, S05, S14, S17.
- *Done when:* S01's gate part, S02, S03, S05, S14 and S17 are verified (S01's page stays
  `planned`, with a dated note recording its gate part, until 2b); **Qwen3.8-27B is the registry's
  coder, and pi completes a task with it from the Mac and from tmux as `agent`**; the soak has
  measured every footprint, the coder's included, and the registry holds the numbers; the
  CUDA-allocatable ceiling is measured (recorded as *at least* the figure it reaches when the method
  stops at the reserve: Dan's decision, 2026-10-07); the brake's timings, `MemAvailable`'s lag, swap
  (down to 24 GiB available) and
  earlyoom's order are recorded; and route A's decode, time to first token and prefill are recorded
  against Phase 5's table.

**Phase 2b — Visibility**

- [Dan] the watchdog on the Synology, beside 2a's ntfy (S13).
- [Mac] the SwiftBar menu bar, polling `spark status --json` over SSH · harness hooks on the Mac,
  which live beside Orca's status hooks, which Orca rewrites at each start (from Orca on the Mac,
  2026-09-28).
- [Spark] harness hooks for `agent` · a pi session started from Orca counts as an active agent
  session and keeps its model until its pi process exits (Dan's decision, 2026-09-28), and SwiftBar
  lists those sessions *(2a builds `spark session hold`, which the Mac's hook runs over SSH, since
  Orca's pi has no process on the Spark: Dan's decision, 2026-10-07, after the implementation plan's
  forward-and-back council)* · ~~`agent`'s GPU jobs get an OOM score before `agent` runs GPU work,
  with S06 (from Phase 1's close)~~ (only if 2a's test as `agent` fails, below) · the engines'
  output to the journal, only after a check that covers speech shows what it would keep (from Phase
  1's close) · pi 0.87.1 for `agent` after a deliberate test (from Phase 1's close; placed here,
  with `agent`'s hooks).
- *Added 2026-10-07, after 2a's council:* [Mac] ntfy's alerts on the Mac, with the menu bar (Dan's
  decision; the council's toolstack review suggests the ntfy CLI as a launchd agent, which needs no
  HTTPS and no relay) · [Spark] `agent`'s hooks post through the gate's status socket, or with a
  publish-only token on a topic of their own · `agent`'s GPU jobs' OOM score here only if 2a's test
  as `agent` fails · llama-swap v259 or later weighed with the journal question, since it can send
  each of its log streams to the journal apart (the toolstack review) · S06's drill runs its GPU
  step longer than the 60-minute idle time, or with the idle time lowered for the drill, and is
  seen to fail once without the hook: at 40 minutes it would pass with the hook broken.
- *Scenarios:* S06, S12, S13, and S01's menu-bar part.
- *Done when:* S06, S12, S13 and S01's menu-bar part are verified.

**Phase 2c — The lab**

- [Spark] `spark try`, `spark promote` and `spark forget`, with the lab instance (S11) · then
  Qwen3.8-27B's route B, DFlash2 on llama.cpp, as the speed step after 2a's route A; routes C and D
  stay in Phase 5. (Until 2026-10-07, routes A and B were both to run on `spark try` here: Dan,
  2026-10-05.) Qwen3.6-35B-A3B can come back here as a trial.
- *Added 2026-10-07, after 2a's council (the session's rulings):* route B is measured in Phase 5's
  order, decode and time to first token in pi, then quality per GB, then long context; whether it
  becomes the coder is 2c's call, with those measurements. S11's trial note is written on the Spark,
  shown in `spark status`, and filed in the vault by Dan, since the vault isn't reachable from the
  Spark. The lab instance's ports sit below 1024 too. *(Noted after the implementation plan's
  forward-and-back council: llama-swap-lab on 901 and its engines on 902–999 would sit clear of 2a's
  800–899; the front and the gate are written for one upstream, so the lab needs them to learn a
  second; and route B's adoption gains two checks, a two-key canary — Dan's and `agent`'s distinct
  strings, back to back and, with `parallel` above 1, together, neither in the other's reply, the
  coder staying `parallel: 1` unless it passes — and time to first token at 128K and 250K against
  the Mac pi's 300 s, since llama.cpp's DFlash path has open reports of trouble under concurrency
  and on long prefills.)*
- *Scenarios:* S11.
- *Done when:* S11 is verified and route B is measured.

*Not yet placed in a sub-phase* (2026-10-07), from Phase 1's close: a user of their own for the
engines and `spark models pull` — not in 2a, since a process that isn't root can't start engines as
another user, and render's allowlist of engine options stands meanwhile; systemd sandboxing for
llama-swap's and the web services' units, and `cap_drop` for the web containers (2a sandboxes only
its two new units); and each deployed component's upgrade runbook, written with its first bump. The
retrospective lists the rest. *(Revised the same day after the council, Dan's decisions:
llama-swap's sandboxing, the pull's own user and root-only secret files move into 2a. Still
unplaced: the engines' own user, whose reason is corrected under *The front and the gate*, with
llama-swap as that user the route; the web services' sandboxing; `cap_drop` for the web
containers, which run as root with host networking; and the upgrade runbooks. After the re-review,
the same day, Dan moved `cap_drop` into 2a too. Noted after the implementation plan's
forward-and-back council: systemd sandboxing for the brake's and the pull's units is unplaced too,
2a sandboxing only the front, the gate, llama-swap and the notifier; the pull, which runs
huggingface_hub with network access, matters most.)*

**Phase 3 — App API + speech** (Dan's audio pipeline is the first app with its own key)

- [Spark] LiteLLM, locked down, + Postgres + hook (inline refusals, `x-spark-model`,
  `wait_for_fit_s`) · keys by access group, per-key concurrency (batch keys low) · LiteLLM's LAN port
  (ufw + ACL) · batch whisper.cpp · the diarization endpoint · the **speech comparison** on Dan's
  51.6-minute lecture against the Mac baseline: Whisper large-v3-turbo vs Parakeet TDT v3 GGUF vs
  NeMo Parakeet with boosting, plus non-English, language-switching and two-speaker samples; pyannote
  community-1 · the endpoint reference page (chat + speech; the site render stays on the Mac until
  Quarto is on the Spark).
- [Dan] `spark keys create` for the audio pipeline, whose own changes happen in its repo.
- *Dan's audio host (decided 2026-10-05; designed at this phase, not before):* it succeeds the
  audio pipeline, runs on the Mac, is built on Pixeltable, and has its design in a private repo of
  its own. Until this phase it uses the Mac's own speech models; here it moves to the Spark's
  endpoints, with its own key, by changing a base URL.
- *The front and LiteLLM (noted 2026-10-07, from Phase 2a's design):* from 2a the front already
  checks keys, holds a request for its key's wait and returns refusals inline, so LiteLLM, itself a
  proxy in front with per-key limits, overlaps it. This phase decides whether LiteLLM replaces the
  front or sits ahead of it. *(Noted 2026-10-07, Dan's decision after the implementation plan's
  forward-and-back council: a third option, **no LiteLLM**, the front growing what this phase needs.
  What each costs after 2a: in its place, LiteLLM rebuilds what only the front does — in-flight
  counts to each stream's end, the drain under one lock, apply's hold, the gate's events — and
  reaches the status socket as `spark-front`'s uid from a root-run container; ahead of it, the front
  sees one key, LiteLLM's, so every app gets one wait, one queue rank and one cap, refusals name
  LiteLLM, and LiteLLM wraps the front's `409`s and sentences, chosen for pi, in its own errors,
  unless the front trusts an identity header from LiteLLM's key only and passes its own refusals
  through unchanged; with none, the front already has the digests, the per-key waits, the caps, the
  route list and the inline refusals, and lacks a per-group model list, `x-spark-model` and a
  listener beyond loopback. Keys then reload without a restart: since 2a they live in a private file
  the front and the gate read at start, which this phase makes reloadable.)*
- *Done when:* S04, S07, S08, S10, S16 and S22 are verified and the privacy canary passes.

**Phase 4 — NAS and backups**

- [Dan] Synology exports (read-only data and recordings, read-write work folders, root squash, the
  wired address) + immutable snapshots + the non-admin rsync account.
- [Spark] systemd automounts · nightly backups · one restore test.
- *Done when:* S15 and S18 are verified.

**Phase 5 — Model bake-off, clients, Claude Code docs**

- [Spark] coders: Qwen3.8-27B (its four routes, below; until 2026-10-05 this said "GGUF + MTP; vLLM
  FP8/NVFP4 only if needed"), Laguna-S-2.1, gpt-oss-120b
  (policy-safe); lighter: Qwen3.6-35B-A3B, gpt-oss-20b (policy-safe). Resident vision model: Gemma 4
  26B-A4B (policy-safe) vs Qwen3.6-35B-A3B. Embeddings: Qwen3-Embedding-0.6B vs Granite embedding r2
  (policy-safe); TEI only if llama.cpp falls short. Metrics: footprint (peak, steady), cold start,
  TTFT, prefill at 4K/32K/64K, decode at 1/2/4/8 streams, tool-call reliability, Dan's 3–5 real tasks
  via pi, memory left free · client configs for the picks, rendered by the `spark` CLI (pi pinned
  outside its crash range, OpenCode 1.18.x).
- *Optimizing Qwen3.8-27B (Dan, 2026-10-05: "that was the entire point of this process"). Its
  numbers come from that day's research, reported by others on single Sparks, not measured here.*
  Dan's order: **speed in pi** first — decode and time to first token on his real tasks — then
  **quality per GB**, then **usable long context** (native 262K); its footprint doesn't matter.
  Four routes, each run:

  | Route | Engine | Checkpoint | Speculation | Reported decode | Needs first |
  |---|---|---|---|---|---|
  | A | llama.cpp b11146, as deployed | Unsloth Dynamic GGUF: UD-Q4/5/6_K_XL, Q8_0 (17.6–31.5 GB) | MTP, in the GGUF | 15–27 tok/s | nothing |
  | B | llama.cpp b11146 | the same GGUF and a DFlash2 drafter (1–2 GB) | DFlash2 | 38–50 tok/s | nothing |
  | C | vLLM, from NGC (*Components*), or upstream ≥ 0.30 | NVFP4: Unsloth's (best quality) or NVIDIA's (fastest) | MTP, DFlash2 | 18–39 tok/s | a container engine, a memory cap |
  | D | SGLang ≥ v0.5.19 | NVFP4 (RadixArk's) | DFlash2, DSpark | 48–72 code, ~25 prose | as C, and its open issues |

  - **Routes A and B need nothing new,** so they run early, on Phase 2's `spark try` lab instance
    (Dan, 2026-10-05); C and D wait for this phase. *(Changed 2026-10-07, Dan's decision: route A
    becomes the deployed coder in Phase 2a, with `Qwen3.8-27B-UD-Q4_K_XL.gguf`, replacing
    Qwen3.6-35B-A3B, so it is measured there rather than as a trial; route B runs on Phase 2c's lab
    instance. The rest of this phase's comparison stands.)* TensorRT-LLM is out for now: it doesn't yet
    load Qwen3.8-27B NVFP4 on this GPU (its issue #17723).
  - **Measured in that order.** Speed: decode and time to first token in pi, and prefill — vLLM
    and SGLang are reported 2–5× faster than llama.cpp at reading long prompts, which agent runs
    are full of. Quality per GB: KLD against the BF16 original (55.6 GB, which fits as a
    reference) and Dan's tasks. Long context: speed at 32K, 96K and 262K, with f16 against FP8
    KV cache (one report found f16 20% faster at 96K).
  - **Cautions for C and D.** vLLM's `--gpu-memory-utilization` is a fraction of the whole pool,
    like SGLang's `--mem-fraction-static` below, so each is set from the registry's footprint.
    vLLM with MTP hard-rebooted a Spark twice at 16K context with two requests, and SGLang run
    outside a container froze one: each starts behind the gate, after a soak. vLLM on this GPU
    has an open prefill regression (#55397) and runs FP8 KV cache only on `triton_attn`.
  - **Since 2a's design** (noted 2026-10-07, after the implementation plan's forward-and-back
    council): a container engine can't start from llama-swap's sandbox (`NoNewPrivileges=`, no
    docker group for `spark`), and root may not claim a ticket through `spark`'s folder, so its
    start route is still to design, and with it how "nothing starts without the gate" holds; the
    gate's pid scan finds only `llama-server` and `whisper-server` run as `spark`, so a container's
    engine needs launch's recorded pid or another way to be seen; a cold vLLM or SGLang start can
    pass the 180 s deadline, so these starts come from persisted compile caches, measured, or the
    gate waits for their readiness itself; vLLM's own start check reads CUDA's free figure, not
    `MemAvailable`; and 2a's page-cache drill settled llama.cpp's GGUF loads, so it runs again with
    a safetensors load, where E.1 was seen, before routes C and D.
- *Qwen3.8-27B on SGLang (notes added 2026-09-28, from
  [MiaAI-Lab/Qwen3.8-27B-SGLang-DGX-Spark](https://github.com/MiaAI-Lab/Qwen3.8-27B-SGLang-DGX-Spark),
  read at its 2026-09-12 state; its numbers, not measured here):* on one GB10 it serves the NVFP4
  checkpoint (~24 GB, dense BF16 `lm_head`) at native 262K context with an FP8 KV cache (~32.8 KB
  per token), and reports code decode at about 51–55 tokens a second with DSpark or DFlash2
  against 24–35 with MTP. What this stack must account for before the bake-off runs it:
  - **A container engine under llama-swap.** SGLang runs in Docker, and llama-swap runs as
    `spark`, which is kept out of the `docker` group; starting an engine container needs a design
    that keeps root's containers root-started (like the web services' unit), for vLLM too.
  - **Its memory claim.** `--mem-fraction-static` takes a fraction of the whole pool (its 0.90
    here is ~109 GiB, and its KV pool alone measured ~81 GB), so it would overrun the residents
    and the reserve. Set it from the registry's footprint and budget it like any engine; its own
    history includes hard reboots at 0.95, and earlyoom killing its scheduler.
  - **Its speculative decoders.** DSpark and DFlash2 are SGLang's, not llama.cpp's. DFlash2 needs a
    dev image (no released tag had it), pinned by digest under the seven-day rule, and two of its
    upstream issues were open: cross-request context bleed under concurrency (sglang #36548),
    which matters with more than one key, and output diverging with thinking on (#38009).
    *(Corrected 2026-10-05, from that day's research: DFlash2 is llama.cpp's too — its PR #27816,
    merged 2026-08-27, is in the b11146 release this stack runs — and SGLang's own releases carry
    it from v0.5.19, 2026-09-05, though no report yet runs those releases on a Spark. Both issues
    were still open.)*
  - **Smaller things:** its default port, 8888, is SearXNG's here; it pins to the ten Cortex-X5
    cores (`--cpuset-cpus 5-9,15-19`, +2–7% decode); `--shm-size 32g`; it downloads through
    `HF_TOKEN`, where this stack pulls pinned revisions with `spark models pull`; and render
    would need an SGLang engine with its own option allowlist.
- [Mac] those client configs installed on the Mac · the `spark-endpoints` skill for
  chendaniely/skills (Dan pushes).
- *Done when:* the registry has a primary and a policy-safe pick per slot; S19 is verified; findings
  are in the vault; the docs are updated.

**Phase 6 — Other people: family over Tailscale, guests at events** (designed 2026-10-07; needs
Phases 2, 3 and 5)

- *Family and friends, from anywhere (Dan's dad, say).* Tailscale node sharing puts only the Spark
  into their own tailnet; the ACL grants shared-in users the web UI's port 443 and LiteLLM's port,
  and nothing else — none of Dan's other devices, nor the home LAN. Each gets an Open WebUI account
  Dan creates (sign-up stays off) and, if they want one, a key of their own (`spark keys create
  <name>`) in a `family` access group with its own concurrency. To check when this phase is
  designed: the ACL syntax for shared-in users, and what an Open WebUI admin can see of other
  users' chats. ZeroTier is the alternative for someone who can't use Tailscale, built only then:
  a second overlay and interface; ufw rules for it (ufw does see ZeroTier's interface, unlike
  Tailscale's); no automatic HTTPS for the web UI (that needs HTTPS on the LAN, in the backlog, or
  HTTP inside the ZeroTier network); and its network ID kept out of the repo, like the tailnet's
  name.
- *Guests at an event, on the venue's network only, never the tailnet.* An event mode, on for the
  event and off after: ufw opens LiteLLM's port and a guest web UI's port to the venue's subnet
  only, and closes SSH from that LAN for the duration (Dan reaches the box over Tailscale). The
  address is given out on the day, never written in the repo. A key per guest, in an `event` access
  group that reaches only the event menu, with low per-key concurrency, expiring when the event
  ends, each revocable. The menu: models Dan picks beforehand, pulls, measures and fits like any
  registry model, larger ones included; his residents can be unloaded to free memory; the gate
  loads one at a time and explains every refusal. A request for an off-menu model: Dan tries it
  with `spark try` on the lab instance, and adds it if it fits and behaves. A guest web UI: a
  separate Open WebUI instance with its own data, port and accounts Dan creates, over HTTP on a
  network he trusts, or HTTPS once LAN HTTPS exists; Dan's own web UI and chats never meet it.
- *Throughout:* nothing anyone sends is logged, as for every key; their requests share the memory
  pool under the same admission rule as Dan's. *(Noted 2026-10-07, after the implementation plan's
  forward-and-back council: the web UI's one key is in Dan's key group, so family members using
  Dan's Open WebUI would get his privileges — the queue, his make-room hold, the marked model, his
  processes named in their chat; they need an Open WebUI and a key of their own, or a front that
  trusts Open WebUI's forwarded-user header from that key only (to check at the pinned version). Key
  groups carry those five settings apart since 2a, and the key list is private, so per-person keys
  stay out of the repo. Guests use instances Dan's keys don't, or shared models run with no prompt
  cache, since a shared engine's isolation is the engine's business and a shared prompt cache is a
  timing channel on other people's prompts.)*
- *Done when (planned):* a family member chats and calls the API over a shared node, and reaches
  nothing else; an event dry run on a spare LAN — a guest key loads a menu model, an off-menu
  request goes through `spark try`, the guest web UI works — and event mode closes cleanly after.
  Its scenario pages (a family member chatting from home; a guest loading a large menu model; a
  guest asking for an off-menu one) come with its implementation plan, when the scenario check
  learns `phase: 6`.

## Working practice

- **Commit along the way:** a small checkpoint commit after each task, on a branch per phase, so every
  step has a fallback. Conventional commits with 🤖 and a Co-Authored-By trailer; the leak hooks run
  on every commit. A phase merges to `main` after its review and Dan's OK; **pushes only with Dan's
  explicit OK**.
- **Check work often:** every task ends with its tests plus a check against this plan and its
  scenarios. Every phase ends with a **council review** of what was built against the plan —
  goal-fit and scenarios, reliability, security and simplicity, toolstack.
- **Look forward:** after each review, ask whether anything learned (a measured footprint, an engine
  quirk, a changed upstream default) affects later steps. If it does, update this plan (and its
  [Revisions](#revisions)), the scenario pages and, if the box changed, `changelog.md` — before
  continuing.
- **Execution method:** subagent-driven development — an implementer, then spec and quality reviewers,
  for each task.

## Backlog

Each item gets its own design pass when its turn comes.

- **Audio:** text-to-speech · a Home Assistant voice pipeline · subtitles and translation · a
  code-switching speech model if Whisper falls short.
- **Documents:** PDF chat in Open WebUI (Docling) · vision-model OCR (GLM-OCR; policy-safe
  LightOnOCR-2) · a reranker · structured-output recipes · notes search.
- **Images and video:** image generation and editing (ComfyUI) · video understanding · photo-library
  machine learning · batch photo processing.
- **Data science:** Pixeltable — computed columns over NAS datasets calling the endpoints; its database
  setup later.
- **Dev and learning:** FIM autocomplete · a private eval suite (growing from the bake-off tasks) ·
  LoRA fine-tuning and serving · overnight batch inference · speculative-decoding tuning · a class mode.
- **Ops:** a usage and utilization dashboard · weights on the Synology (measure NAS read throughput
  first; and a model's `models--…` folder doesn't hold its bytes — huggingface_hub 1.32 and 1.33
  keep Xet-stored files in a store shared across repos, under `hf/hub/blobs`, unless
  `HF_HUB_DISABLE_SHARED_BLOBS` is set, and the repo's files are links into it, so moving one
  folder, or `du` on one, misleads; Phase 1 Task 8's review, 2026-09-26) · HTTPS on the LAN (which
  would also bring the web UI to WireGuard) · the web UI banner (if Open WebUI gains a status API)
  · automatic restarts from the watchdog · Home Assistant with remote power (check the UEFI
  "restore on AC power loss" setting) · a GPU clock cap (only if needed) · a pi
  footer extension · suggest a pre-start admission hook upstream (llama-swap #1127) · automated
  update proposals for what Dependabot can't read — `stack/versions.yaml`'s pins, the workflows'
  `version:` inputs, uv's `required-version`, the gitleaks pin (Renovate's regex manager, or a
  `spark` check against each changelog) · tag the tailnet's always-on devices that aren't Dan's
  own (the NAS, if it runs Tailscale as Dan), so `autogroup:member`, which the ACL's grants to the
  Spark use, means only Dan's personal devices; Phase 2's watchdog needs a grant of its own anyway
  · the weekday preload with a work-hours pin, a setting off by default (from Phase 2a,
  2026-10-07, until Dan asks for it) · Ansible for the homelab as a whole, as a project of its own
  — users, SSH keys, Tailscale, Docker and Compose stacks kept the same across the Synology and the
  other machines — which could take in the Spark through a small role that runs this repo's `make
  bootstrap` and `make apply`, never replacing them; not for the Spark now (Dan, 2026-10-07: "later";
  the [Q&A](phase-2a-qa.md) has the reasons).
- **Model settings, when more models are fitted** (Dan, 2026-09-28: every model stays at its full
  context for now, and these are the levers to look at when memory gets tight; each saving is an
  estimate): the embedding model's context back to 8,192 (about 3 GiB); a quantized KV cache
  (`--cache-type-k`/`-v q8_0` about halves it: roughly 2.5 GiB for Gemma and 2.8 for the coder at
  full context, at a quality cost to measure); Gemma on one slot (its task calls would then queue
  behind a chat); tighter checkpoint caps (about 0.6 GiB per Gemma checkpoint); smaller prompt
  caches (1 GiB for Gemma, 2 for the coder); a context below the maximum where a model never needs
  it; and fewer image tokens (`--image-max-tokens`, less detail).
- **Orca on the Spark, and on the phone** (Dan, 2026-09-28: *"i want a way for the spark to act as
  the main orca server so i can also use orca on my phone"*; parked the same day to wait for the
  upstream fixes below). Two routes, each with its costs, from that day's research and council on
  Orca 1.4.216:
  - *SSH mode*, where Orca on the Mac drives agents on the Spark as `agent`. Orca installs a relay
    in `agent`'s home and builds it from public npm with install scripts on (`node-pty`,
    `@parcel/watcher`, no lockfile). It rewrites `agent`'s `~/.claude/settings.json` on every
    connect; that version kept the secrets guard, but **root-owned managed settings for `agent`'s
    guard come first** (Dan, 2026-09-28). Updating Orca strands the relay's running agents
    (stablyai/orca #13852). The `orca` command in a remote pane proxies back to the Mac's Orca, so a
    test that it can't reach the Mac's own panes comes before any agent runs there. It doesn't
    reach the phone.
  - *Server mode*, `orca serve` on the Spark, the phone's route: the whole Electron app run
    headless, with Xvfb and about 20 Chromium runtime libraries (Orca's headless Linux server
    guide lists them); no bind option, so it listens beyond
    loopback, on 6768; a unit running as `agent`, a tailnet rule for 6768, a needrestart exception
    (it ignores SIGTERM, #18186) and a measured footprint. The Android app (0.0.50, a sideloaded
    beta) waits on #21808 (its APK is reported signed with a public debug key), #16086 (headless
    pairing), #20706 (no push from a headless server) and #20673 (connections dropping on an arm64
    host; a fix is proposed in #20844).
- **Parked:** Hermes · ~~`claude-dgx`~~ (dropped 2026-09-28: pointing Claude Code at a local model,
  if ever wanted, is Ollama on the Mac, outside this repo) · a MacBook MLX fallback · ~~other
  users~~ (Phase 6, since 2026-10-07).

## Open items and risks

- **GB10 hard freezes**, memory-related and not → conservative thresholds, the minimal brake from
  Phase 1, the off-box watchdog, a clock cap if needed, Home Assistant power later; incidents are
  logged in the vault.
- **llama-swap** has one main maintainer and fast config churn → pin, validate, adopt no optional
  features; the gate keeps the boundary thin enough to swap it out. *(Noted 2026-10-07, after the
  implementation plan's forward-and-back council: since 2a's design the front and the gate rely on
  v257's `/upstream/<model>/health` as the load call, `/api/models/unload`, its "exited prematurely"
  text, `/logs/stream`, `/running`'s shape and its `429`, so an upgrade past v257, 2b's v259 idea
  included, re-reads each of them; the `TESTED_AGAINST` test makes that loud.)*
- **LiteLLM's 2026 security record** → it arrives in Phase 3 locked down, with swap triggers; the gate
  could take over keys (~600 lines) if a trigger fires. *(Since 2a's design the front holds the
  keys, and Phase 3 weighs no LiteLLM at all: noted 2026-10-07.)*
- **Open WebUI churn** → a pinned minor version, env-only config, a database dump before upgrades.
- **vLLM** start can abort when free memory rises during profiling (#56830), and NGC lags upstream →
  llama.cpp first.
- **Engines share llama-swap's user** (found 2026-09-26, in Phase 1 Task 2's review). Every engine
  runs as `spark`, so a compromised one can read every llama-swap key — from `llama-swap.env`, which
  group `spark` can read although only root needs to (systemd's `EnvironmentFile=` and root's
  Compose read the secret files), and from llama-swap's `/proc/<pid>/environ`, which any process of
  the same user can read (checked on the box) — and can delete the brake's hold, since `spark` owns
  the hold folder. In Phase 1 the keys gate only llama-swap on 127.0.0.1. A registry edit can also
  point an engine at files `spark` can read without compromising it, the secret files included:
  llama-server's `--chat-template-file`, `--path` and `--media-path`, and whisper-server's
  `--public` and its `POST /load` (Task 6's review, 2026-09-26; `spark render`'s denylist doesn't
  cover them). → Phase 1's Task 17 security review decides whether engines get a user of their own
  (Dan's decision, 2026-09-26), and whether render allows only listed engine options. (Decided
  2026-09-28, Dan's decision from Phase 1's council: render allows only listed engine options. A
  registry's `args` may set only the options on its list, `ALLOWED` in `spark/src/spark/render.py`,
  in every spelling each engine's `--help` gives, and it refuses anything else, naming the flag and
  saying to check what it does before adding it. The list holds what the registry and the test
  fixture set that day, so `--chat-template-file`, `--path`, `--media-path`, `--agent`, `--tools`,
  the logging options and whisper-server's `--public` are all refused; the refusals render had keep
  their own reasons. whisper-server's `POST /load` is a request, not an option, and the list doesn't
  reach it. A user of their own for the engines and the pull is Phase 2's, with the gate.
  *Corrected 2026-10-07, from Phase 2a's design:* not in 2a, since a process that isn't root can't
  start engines as another user, and llama-swap and the gate run as `spark`; it isn't yet placed in
  a sub-phase, and the allowlist stands meanwhile. *Revised the same day after the council, Dan's
  decisions:* the pull gets its own user, `spark-pull`, in 2a, and the secret files become 0600
  root:root, so neither the engines nor the pull can read one from disk. The engines' own user
  stays for later; once 2a is built, llama-swap itself can become that user (*The front and the
  gate*). Until then an engine can still read llama-swap's environment, which from 2a holds only
  internal keys, and write the gate's and the brake's state: forge an admission ticket, or delete
  the hold. *Narrowed after the re-review:* the engines run inside llama-swap's sandbox, where the
  hold's folder and the gate's state are read-only and only the tickets' folder, whisper's tmp-dir
  and the caches can be written, so an engine can still forge a ticket, but no longer delete the
  hold.) The same
  reach belongs to `spark models pull`, which runs as `spark` with network egress by design: its
  Python dependencies, huggingface_hub and the packages it brings, run with it (found 2026-09-26, in
  Phase 1 Task 8's review). → Task 17 decides the same for the pull (decided 2026-09-28, at Phase 1's
  close: the pull, like the engines, gets a user of its own in Phase 2; it gets it in 2a, Dan's
  decision of 2026-10-07). The pull's journal lines never
  carry a token (Task 8), but hf_xet keeps a log of its own per run under
  `/var/lib/local-ai/hf/xet/logs`, which that redaction doesn't reach. Against a stand-in Hub it
  wrote the authorization header as `[REDACTED]`; what a real download records is unmeasured.
  Phase 1 sets no token (its repos aren't gated); before one is set, for a gated model, those logs
  get checked.
- **Phase 1's launch check is a static fit** (2026-09-26). It has no pending term and doesn't
  serialize loads, so two engines started close together can both pass while memory outside the
  stack is in use. `spark render` refuses a model set that doesn't fit, so the stack alone can't
  open the gap. → The brake is the backstop until the gate adds both in Phase 2 (*Admission and
  memory rules*, rule 1). *(Phase 2a's design, 2026-10-07: the gate admits with the pending term,
  one load at a time, and `spark launch` keeps its check as a zero-wait backstop, for anything that
  reaches llama-swap without the front. Revised the same day after the council: `spark launch`
  starts nothing without the gate's admission ticket, so nothing reaches an engine's start around
  the gate, and the gate's formula also holds back the growth the loaded models are still owed
  (rule 9).)*
- **Anyone on the box can take 127.0.0.1:9100** (found 2026-09-26, in Phase 1 Task 3's review).
  Ports from 1024 up are open to every user (checked on the box), so while llama-swap isn't holding
  9100 — after a crash, or in `spark apply`'s restart window — any local user, `agent` included, can
  listen there and receive the key the brake sends every 250 ms, and the keys `spark status` and
  `spark apply` send. The client also follows redirects with the key. → Task 17's security review
  decides; the options include a port below 1024 with `CAP_NET_BIND_SERVICE` for llama-swap's unit,
  refusing redirects, and a cap on what the client reads. (Added 2026-09-26, from Phase 1 Task 10's
  scan: `make doctor` sends Dan's key too, once an unkeyed `/health` answers, which a squatter can
  make it do; it follows no redirect.) (Corrected 2026-09-28, from Phase 1's council: the client
  that the brake, `spark status` and `spark apply` use now refuses a redirect when it sends a key,
  as doctor's probe does, so a squatter can't send the key on. The port itself stays open to any
  user, for Dan to decide.) *Decided 2026-09-28, at Phase 1's close (Dan):* the port stays for Phase
  1, with the redirect refused; in Phase 2, llama-swap moves behind a Unix socket with the gate.
  *Mostly closed by Phase 2a's design (2026-10-07), once built:* llama-swap v257 can't listen on a
  Unix socket, so the front takes 9100 instead and doesn't restart when llama-swap does, so a
  llama-swap restart or crash no longer leaves 9100 free; and clients' keys stop at the front (*The
  front and the gate*). What remains: llama-swap's private port, ~~9101~~ (900 since the council),
  can still be taken while llama-swap restarts, and a squatter there would get the front's or the
  gate's internal key, never a client's, and could be sent requests if it answered as llama-swap
  does; 9100 itself is free while the front restarts, after a crash or a deploy of its own; and the
  engines' ports take no key (*127.0.0.1 is not a boundary against `agent`*). *Closed as designed
  (2026-10-07, after the council, Dan's decisions), once built.* The council found that what stayed
  open was more than the internal key: a squatter on 9100 while the front restarted would get Dan's
  key and every prompt, and one on llama-swap's port or on a starting engine's would be sent the
  requests held for it, whisper uploads included, with the internal key. Now systemd's socket units
  hold 127.0.0.1:9100 and the gate's two sockets from boot, so 9100 is never free, even while the
  front restarts or crashes; llama-swap moves to 127.0.0.1:900 and the engines to 800 and up, below
  1024, which only llama-swap's unit, given `CAP_NET_BIND_SERVICE`, can bind; and clients' keys stop
  at the front, which holds only their digests. 2a checks, as `agent`, that binding 9100 while the
  front restarts and 900 while llama-swap restarts both fail. What remains: anything that runs as
  root can still bind those ports, the web containers among them, which run as root with host
  networking until their `cap_drop` (not yet placed), and so can Dan's account through sudo; the
  engines, which inherit the capability, could bind a free low port, though they already run as
  `spark` (*Engines share llama-swap's user*); and the engines' ports still take no key, so any
  local user can call a loaded engine directly (*127.0.0.1 is not a boundary against `agent`*). A
  private network namespace for llama-swap and its engines, which would close the last, is kept in
  view for Phase 3's decision. *(Corrected the same day, after the re-review:* "never free" needed a
  condition. A socket unit that passes its trigger limit fails and closes its socket, and systemd
  stops one whose service has hit its start limit, so a front crashing at start after a bad deploy
  would have freed 9100. The socket units now set `TriggerLimitIntervalSec=0`, and the front, the
  gate and the brake `StartLimitIntervalSec=0`, so neither ever gives up, and 2a's crash-loop check,
  run as `agent`, confirms that 9100 never answers as anyone else. And the web containers lose their
  capabilities in 2a, Dan's choice, so they can no longer bind those ports either. What remains of
  the root-level path is root itself: Dan's account through sudo, and anything else that runs as
  root on the box.)
- **The minimal brake's reach** (found 2026-09-26, in Phase 1 Task 4's reviews). It unloads through
  llama-swap, so while llama-swap is down or hung with engines loaded it can hold new loads but not
  unload. And llama-swap v257 answers an unload only once the engine has exited, one unload at a
  time (read in its source), so a fast fall — about 2 GiB/s with a 6 s stop, 1 GiB/s with a 10 s
  one, in the reviews' simulations — reaches earlyoom's 12 GiB line before the brake has freed
  enough. (Added 2026-09-28, from Phase 1's council: measured cold loads fall faster than those
  simulations. In Task 13, Gemma took 17.6 GiB in 9 s and the coder 26.4 GiB in 10 s, about 2.0
  and 2.6 GiB/s; at full context Gemma took 24.7 GiB in about 5 s, timed to the whole second. So
  the 8 GiB between the brake and earlyoom can go in about 1.6 to 4 s, which Phase 2's rate-of-fall
  design starts from.) →
  earlyoom (12/9 GiB) stays the backstop; Phase 1's brake drill (Task 16) measures stop times and
  `MemAvailable`'s noise, which set the brake's unmeasured `GRACE_S` (15 s) and
  `FLOOR_TOLERANCE_GIB` (0.5 GiB); the gate's rate-of-fall watch (Phase 2) is the fuller answer.
  (Decided 2026-09-28, at Phase 1's close, from its council: `GRACE_S` stays 15 s, and
  `FLOOR_TOLERANCE_GIB` goes to 1.0 GiB, so one Gemma context checkpoint, about 0.59 GiB, allocated
  during a slow stop no longer unloads a second model; it is still 1/8 of the 8 GiB between the
  brake and earlyoom. Two things stay unmeasured, for Phase 2's drill: a busy engine's stop, and how
  long after an engine leaves `/running` its memory shows in `MemAvailable`. The brake stops
  counting that memory the moment the engine leaves, so a lag would unload a second model.)
  (Added 2026-09-28, from Phase 1's council: the brake asks llama-swap nothing above the warn line,
  so a key llama-swap refused would first show in an emergency, as unloads that fail. At start it
  now asks once, with its own key, what runs, logs the answer, and records it in its state folder.
  `make status` shows the result on its `brake` line, `make doctor`'s `stack units` line fails
  unless it passed, and `spark apply` checks that the brake is still running 3 s after it restarts
  it.) (*Phase 2a, designed 2026-10-07:* ~~the brake moves into the gate, which still unloads
  through llama-swap, so this reach stays; 2a's drill measures the two numbers left above, and the
  key check at start moves into the gate.~~ *Revised the same day after the council, Dan's decision:*
  the brake stays its own unit, with its key check, so this reach is unchanged; 2a adds the
  rate-of-fall watch, set from its measured falls and stop times (rule 5), and llama-swap's
  `Restart=always`, so a stray SIGTERM no longer leaves it down.)
- **`spark render`'s budget check counted the reserve twice** (found 2026-10-07, in Phase 2's
  brainstorm). It summed every registry model's footprint against `allocatable − reserve`
  (102 − 24 = 78), so Qwen3.8-27B, at an estimated 38 GiB, couldn't join the set, which then comes
  to about 81. The plan's rule is that footprints fit the CUDA-allocatable ceiling and that each
  load leaves the reserve free at the moment it happens. → Phase 2a corrects the check ~~and lowers
  the reserve to 22 GiB, which Dan chose knowingly,~~ and measures the ceiling (rule 9). *(Revised
  the same day after the council: the reserve stays 24, Dan's decision, and the coder's estimate
  rises to ~41, so the set comes to about 84; the corrected check, now with its formulas, is what
  lets the coder fit, and the gate's formula also holds back the growth the loaded models are
  still owed: rule 9.)*
- ~~**Fresh releases in the lock — Dan's decision**~~ **Resolved 2026-09-27: a rolling seven-day
  window.** Dan chose it while the stack is still early, to see how it works in practice: uv
  0.12.18 takes `exclude-newer = "7 days"` and records the span in the lock (`exclude-newer-span =
  "P7D"`), so no date moves by hand, and Dependabot's uv PRs get `cooldown: default-days: 7`. The
  lock now holds huggingface_hub 1.32.0 and filelock 4.0.1; *Weekly upgrade day*, above, has the
  decision, and `updates.md` the steps, a per-package exception for an urgent fix included. Phase
  1's Task 17 looks back at how it went. What was open (found 2026-09-26, in Phase 1 Task 8's
  reviews):
  `uv lock` takes the newest release that fits, even one uploaded that day: Task 8's lock holds
  huggingface_hub 1.33.0 and filelock 4.0.4, both uploaded that week, and the pull runs them as
  `spark`, with network egress. A release's first days are when a bad or compromised upload is most
  likely still undetected. `[tool.uv] exclude-newer`, set to a date a week back, would make the
  lock take only releases at least that old (a dry run picked huggingface_hub 1.32.0 and filelock
  4.0.0), at the cost of moving that date whenever a dependency moves. → Dan decides at the push
  after Phase 1's Task 10.
- **Two machines, one branch** → one session at a time; handoff by push and pull with Dan's OK.
- ~~**The unit-file model — Dan's decision, before Phase 1's Task 6 writes the unit templates.**~~
  **Resolved 2026-09-25: option 2, root-owned copies.** Dan chose it, and Phase 1's pre-flight built
  it into `website/design/phase-1.md` (Tasks 6, 7, 9 and 10): `make install-units` reads what
  `make apply` staged as Dan, shows what would change, asks, and installs root's copies; the polkit
  rule allows `start`, `stop` and `restart` on the four units by exact name, and no longer
  `reload-daemon`; `make doctor` checks that root's copies are root's own. *Users, access and
  security* and *Deploy workflow* record it. The item as it stood:
  `make apply` renders the `local-ai-*` units and the Compose file as Dan, so Dan owns them;
  `make install-units` links them, and the polkit rule lets Dan reload systemd and restart the units.
  So from Phase 1, anything running as Dan — the Spark session, Positron's packages, a build — can
  change what root runs, and become root without a password. Phase 0's security review offered two
  options:

  1. **Keep the accepted model.** Then *Users, access and security* says plainly that Dan's
     account can become root without a password, not only through sudo.
  2. **Root-owned copies.** `make install-units` installs root-owned copies of the four units and
     the Compose file, re-run with sudo only when they change. The polkit rule then becomes a real
     boundary: restarts need no password but give no say over what root runs, and exact unit names
     and verbs in the rule become worth adding. This changes Phase 1's Task 6 (the Compose unit's
     folder), Task 7 (a changed unit needs `sudo make install-units`, not a restart) and Task 9
     (`make install-units`).

  `website/design/phase-1.md` builds option 1 and stops at Task 6 for this decision. (Superseded
  2026-09-25: it builds option 2.)
- **127.0.0.1 is not a boundary against `agent`** → llama-swap checks keys, but the engines it
  starts listen on 5800 and up with none, so any local user, `agent` included, can call a loaded
  model directly, around llama-swap's keys. In Phases 1–2 that costs nothing: `agent` has a key of
  its own, and a direct call can't load a model. The gate doesn't change it, since it decides loads,
  not who reaches an engine. Phase 3's per-key allow-lists and concurrency limits don't hold against
  a direct call, so that phase decides how to close it. (Corrected 2026-09-28, from Phase 1's
  council: a direct call reached more than a model. Each llama-server also served `/slots`, every
  slot's in-flight request with its prompt's size, its sampling settings and the token it last
  sampled, and its own web UI, both without a key. Render now passes `--no-slots` and `--no-webui`.
  `GET /props`, the engine's read-only settings, still answers.)
- **Page cache and the launch check** (added 2026-09-27, from Phase 1's Task 12) → the launch check
  admits against `MemAvailable`, which counts reclaimable page cache as available. On GB10 the
  driver has been seen to reclaim page cache too slowly while a model loads: loading a large
  safetensors checkpoint stalled for about 20 minutes with `MemFree` pinned at 8 GB
  (`cosmicbboy-local-ai.md` §E.1, `[verified]` on his Sparks; not yet seen here, or with
  llama.cpp). Phase 1's pull left 43 GiB of page cache on this box, with 75 GiB free (`free -g`,
  2026-09-27). At the registry's estimates, Phase 1's four models fit in the free part, so its
  loads are unlikely to test it. Still to decide, once a load on a full cache has been watched: nothing, E.1's drop-caches
  loop during a load, or a check against `MemFree` plus what can be dropped. (Added 2026-09-28,
  from Phase 1's council: a new reading, with Gemma, the embedding model and the coder loaded,
  shows `MemFree` at 14.1 GiB and `MemAvailable` at 53.2, 38.2 GiB of it inactive file pages, most
  likely the model files, which the engines read through the page cache. The loads recorded so
  far started with more free memory than they took, Task 13's with 70 GiB free, so E.1's slow
  reclaim is still untested here. Three more options: `--load-mode dio`, which keeps the model
  files out of the page cache at the cost of slower reloads; `spark launch` noting on stderr when
  a footprint exceeds `MemFree`, so the first load that needs reclaim is on record; and a Phase 2
  drill that fills the page cache with a large file, then loads the coder.) *(Placed 2026-10-07,
  after Phase 2a's council: the drill runs in 2a, before Qwen3.8-27B becomes the default coder,
  since every coder load after the first then needs about 41 GiB, more than `MemFree` usually
  holds with the residents loaded. Its result settles rule 1, whose promise to drop caches before a
  load is corrected meanwhile; until then the load's deadline, 180 s, bounds a stall.)*
- **`agent`'s own GPU jobs are outside the gate** (added 2026-10-07, from Phase 2a's council). The
  gate admits only the stack's loads, and `agent` has had CUDA access since 2026-09-27, so its jobs
  take from the same pool unasked. The brake can only unload models, and earlyoom picks the engines
  (`oom_score_adj` 900 and 1000) before `agent`'s processes (0), so a runaway or prompt-injected
  `agent` job can drive the box toward the freeze band, whatever the reserve. Phase 1's close gave
  `agent`'s GPU jobs an OOM score "before `agent` runs GPU work", which is a promise, not a
  control. → Dan's decision, 2026-10-07: in 2a, root
  gives `agent`'s processes an `oom_score_adj` that `agent` can't lower, if it holds when tested as
  `agent`; otherwise in 2b, with S06.
- **To verify on the box:** ~~`121` vs `121a-real`~~ (resolved 2026-09-28: `121a-real` builds
  native `sm_121a` code that runs here; no `121` build was made; see Revisions); ~~`agent`'s CUDA access~~ (resolved 2026-09-27:
  as `agent`, llama-server lists the GB10 as a CUDA device, without docker; see Revisions);
  Parakeet quality on whisper.cpp; NeMo boosting and pyannote on aarch64; that Open WebUI's embedding and speech-to-text
  base URLs are set explicitly (unset, they fall back to OpenAI's); pi's crash range; ~~the tailnet's route home~~ (resolved 2026-09-24: none, by choice; see
  Revisions); the
  UEFI AC-restore setting; Btrfs for immutable snapshots; the CUDA-allocatable ceiling (Phase 2a
  measures it, carefully, and the registry follows: rule 9; added 2026-10-07); from Phase 2a's
  council (2026-10-07), each checked in 2a: that `OnFailure=` fires on a crash that `Restart=`
  recovers (systemd 255's man page says so), that `NoNewPrivileges=` lets CUDA start from a cold
  boot, and that the engines bind ports below 1024 with the capability they inherit; and, added
  after the re-review, that 9100 stays held through a crash loop, that the gate's uvicorn
  subclass passes it each caller's uid, and that an engine's anonymous RSS growth (`RssAnon`)
  accounts for its growth in `MemAvailable` (rule 9's *owed*); how NVIDIA's
  web updater treats apt holds; the GPU-set move and its recovery (the first upgrade day); ~~that GRUB
  boots the newest kernel, which the move's check before the reboot relies on~~ (resolved
  2026-09-27: Phase 1's Task 12 ran the check, and it passed; see Revisions); whether GIGABYTE
  ships this box's firmware through fwupd; ~~whether a model's GPU memory counts toward its engine's
  RSS and `oom_score`, which decides whether earlyoom's choice among engines follows the
  brake's order (Phase 1 measures it)~~ (resolved 2026-09-28: it doesn't, and earlyoom's pick
  didn't follow the brake's order; Phase 1's Task 17 takes it up; see Revisions; since Phase 1's
  council, 2026-09-28, residents start at `oom_score_adj` 900 and on-demand engines at 1000, an
  order earlyoom's dry run confirmed after that day's deploy); whether memory swaps out before `MemAvailable` reaches the
  brake (the 16 GiB swap file; earlyoom ignores swap), which sets swap size and swappiness (Phase
  2a measures it at the real thresholds; added 2026-10-07; corrected the same day, after the
  implementation plan's re-check: down to 24 GiB available only, the drills' floor, with the 20–24
  band left unmeasured by choice); that
  the stack keeps serving through a routine upgrade that moves `libc6` or `libstdc++6`, and through
  one that moves Docker (`docker-ce`, `containerd.io`), and which of the two Docker's restart does
  to the web services (the 2026-09-28 upgrade moved none of them; added 2026-09-28); how much host
  memory context checkpoints take over a long conversation (estimated at about 0.6 GiB each for
  Gemma, 4 at most per slot; the coder's, its recurrent state plus its MTP draft's, not yet
  estimated, 32 at most; added 2026-09-28. Corrected the same day, from Phase 1's council: the
  coder's are capped at 8; their recurrent state is about 63 MiB each by arithmetic, and the MTP
  draft's share isn't estimated, so their size is the first thing a soak at full context
  measures).
- **Orca on the Mac** (2026-09-28). Its status hooks copy whole Claude events, prompts and tool
  inputs included, to Orca over loopback. A failed send of an event other than a tool call's,
  prompts included, is appended to a spool file for its Orca pane, up to 5 MiB, which is emptied
  only after 7 days with no new failure, so it can hold prompts indefinitely (corrected
  2026-09-30, from the spec's review: this said "for up to 7 days"). Its scrollback and session
  data sit on the Mac's disk too, so a leaked value would sit there as well. Its pi extensions load in every pi session on the Mac, in Orca or not. It gives every agent
  a no-prompt flag by default (`--dangerously-skip-permissions` for Claude), so Manual, and
  telemetry off, are checked again after each Orca update. And it moves fast: about a release a
  day, with around 7,000 open issues. Its hooks went into `~/.claude/settings.json` on 2026-09-24
  and its pi extensions on 2026-09-28, before this repo recorded either.
- **Accepted gaps:** homelab apps reach the Spark only from Phase 3 (nothing listens on the LAN until
  per-app keys exist); Open WebUI chat history isn't backed up until Phase 4; the web UI is out of
  reach over WireGuard; agents started from Orca stop when the Mac does (Dan, 2026-09-28).

## Revisions

- **2026-09-23** — Written from the requirements interview; replaces `planning.md`.
- **2026-09-23** — While planning Phases 0–1: the `spark` user stays out of the `docker` group
  (containers start from root-owned units; the Dan ↔ `agent` boundary is the one that matters);
  Open WebUI uses the standard image, since the slim build now requires Postgres + pgvector; Phase 1
  adds a minimal launch check beside the minimal brake; the brake's hold folder is writable by
  `spark-admin` only, so Dan can release a hold and `agent` can't.
- **2026-09-24** — Phase 0, Task 9 on `brightroar`: Dan's login on the Spark is `chendaniely`, not
  `dan`. *Users, access and security* is corrected. `make bootstrap` already took the admin from
  `sudo`, but its dry run showed `dan`; it now shows whoever runs it. The runbooks and Task 11 no
  longer hardcode `/home/dan`. Under the wrong name, two runbook steps failed silently: the check
  that `agent` can't read Dan's files always passed, and the Mac-key step deleted the key it had
  failed to copy. The check now tests whether `agent` can enter Dan's home, since `~/.secrets`
  doesn't exist on the Spark.
- **2026-09-24** — Dan's decision on updates. Everyday `apt upgrade` and snap refreshes run any
  time; the GPU stack is held as one set and moves only on upgrade day (`website/how-to/updates.md`).
  The set is the kernel, the NVIDIA modules built for it, the driver, and CUDA with its
  version-named libraries. Bootstrap had held `nvidia-*`, `libnvidia-*` and `cuda-*` only, which
  left the kernel and the modules free to move. The box's apt log shows DGX OS shipping all four in
  one transaction, and the modules need one exact driver version. Kernel security fixes now wait for
  upgrade day. Dan runs `apt upgrade` out of habit, so updates must never take the stack down for
  good. Phase 1 gains `make upgrade-gpu`, a needrestart override for `local-ai-*` units, and a
  done-when: the stack serves again after a routine upgrade and after a reboot. gitleaks stays a
  direct install, since it has no snap; the runbook has its upgrade steps.
- **2026-09-24** — Dan: upgrade day is weekly, on Saturdays, instead of monthly. Skipping one is
  fine; the next one catches up. The GPU set, gitleaks and uv move then.
- **2026-09-24** — The tailnet's route home, settled: there is none, by choice. Every device Dan
  uses runs Tailscale and reaches the others directly, and the Spark sits on the LAN. The only gap
  is devices that can't run Tailscale, such as the router's admin page, which is reachable only from
  home for now. When one is needed from away, one always-on home machine (not the Spark) advertises
  that device's address alone, never the whole LAN. The steps are in `website/how-to/tailscale.md`.
- **2026-09-25** — Phase 0's council review, where Phase 0 made the plan untrue. *Network*: clients
  reach the Spark by its tailnet name, and the LAN address serves the home LAN; there is no route
  home. Secret files are 0640 root:spark, not 0600. `make hooks`, not bootstrap, turns the hooks on
  in each clone. `spark apply`, not bootstrap, sets where uv's Pythons live, and
  `spark/.python-version` pins one Python minor version. earlyoom avoids `sshd.*`. *Leak guards*:
  a denylist with no terms fails, file names are read, binaries are named for a person rather than
  read, and CI reads every commit's patches and messages. Everyday apt can't move the GPU set, but
  only while every member is held. The GPU set's move gains tmux, an answer-no rule,
  `make hold-gpu` and a check before the reboot; its advisory feeds are Ubuntu's notices and
  NVIDIA's driver bulletins. A rebuild moves the set to current at the first bootstrap. Dependabot
  now proposes Actions and `spark/uv.lock` updates; the pins it can't read join the Backlog. The
  Spark gets a GitHub token for this repository only.
- **2026-09-25** — S23, upgrade day, joins the scenarios (the goal-fit council's forward look). Phase
  1's promise that the stack serves again after a routine upgrade, a reboot and upgrade day now has
  an ID for `make doctor` and the done-when to point at.
- **2026-09-25** — From the review of those corrections. The first time on the Spark follows the
  runbooks' order: bootstrap, which creates the secrets folder, comes before the private files, and
  Phase 1's `make install-units`, not bootstrap, links the units. The leak guards no longer claim
  that Actions logs are checked; nothing scans them.
- **2026-09-25** — Phase 0's lessons carried into Phase 1 (`website/design/phase-1.md`, its forward
  look). A new Task 10 builds the needrestart override, `make upgrade-gpu` on bootstrap's hold, and
  `make doctor` v0: Phase 0's guardrails and the stack's smoke checks, because weekly upgrade day
  needs a check before Phase 2's `spark doctor`. The tasks after it are renumbered. The Spark
  session starts before any [Spark] task. Bootstrap runs again before anything runs as `spark`, so
  `/var/lib/local-ai` is root's; the units that run engines or pull models cache in folders `spark`
  owns. Engines no longer inherit llama-swap's keys, root never writes in `agent`'s home, and Open
  WebUI's admin is created through a tunnel before the tailnet can reach the page. Open items gain
  the unit-file model, which is Dan's decision, and 127.0.0.1 as no boundary against `agent`. The
  to-verify list gains whether GPU memory counts toward an engine's RSS, and swap before the brake.
  The Backlog gains tagging the tailnet's non-personal devices.
- **2026-09-25** — From the reviews of that forward look: moving the GPU set refuses a change of
  driver branch, which is a move planned and made by hand. The check before the reboot reads the
  newest kernel, and that GRUB boots it (`GRUB_DEFAULT=0`) is not yet checked on this box.
  (Superseded 2026-09-25 by the entry-0 check: see the next line.)
- **2026-09-25** — Before the first upgrade day. Answering no covers a removed modules metapackage
  only when no other takes its place; a swap to another branch's is the driver-branch case. The
  runbook checks that GRUB will boot the newest kernel, before anything moves and again before the
  reboot, by reading what GRUB will do: entry 0's first `linux` line in `grub.cfg` against the
  newest kernel, the `default=` lines, and `grub-editenv` for a `saved_entry`, `next_entry` or
  `prev_entry` with a value. (Corrected 2026-09-25: this said GRUB boots the newest kernel only
  while `GRUB_DEFAULT=0` and no `GRUB_TOP_LEVEL` is set, and that the runbook reads those two
  settings. That missed `GRUB_FLAVOUR_ORDER`, which Ubuntu's kernel sort reads, and settings
  written indented or with `export`.)
- **2026-09-25** — Phase 0 is done: its tasks ran from 2026-09-23 to 2026-09-25, and the last
  step, merging `phase-0` into `main`, follows this line. `website/design/phase-0-retro.md`
  records what it built, where it departed from this plan and why, what the reviews found, and what
  Phase 1 inherits. `CLAUDE.md` gains the rules that prevent the loops it took (*Lessons from
  Phase 0*).
- **2026-09-25** — *To verify on the box* gains that GRUB boots the newest kernel. The move's step 4
  already marked it unchecked, but the list, which the retrospective points to for what is still
  unverified, left it out.
- **2026-09-25** — Dan's decision on the unit-file model: root-owned copies (the open item's option
  2, now resolved). The `local-ai-*` units and the Compose project that root runs are root's own
  files, in `/etc/systemd/system` and `/etc/local-ai/compose`, and nothing running as Dan changes
  them without his sudo. `spark apply` only stages them; `make install-units` (a bootstrap mode)
  reads what is staged as Dan, refuses a link or a file he can't read, shows the diff and asks
  before it installs, and changes nothing when nothing changed. After an install, `spark apply`
  restarts each unit still running its older definition, llama-swap only when no model is loaded.
  The polkit rule narrows to `start`, `stop` and `restart` on the four units, and `make doctor`
  gains a check that root's copies are root's own. *Users, access and security* and *Deploy
  workflow* say so; `website/design/phase-1.md` builds it (Tasks 6, 7, 9, 10, 12 and 16), with every
  Task 1–10 listing run first on the Mac and in an `ubuntu:24.04` container.
- **2026-09-25** — Dan's decision: `make upgrade-gpu` runs the GRUB check itself (Phase 1's Task 10
  open item, now resolved), the one `updates.md` step 5 describes: entry 0's first `linux` line
  names the newest kernel, the `default=` lines are the stock two, and grubenv picks no other
  entry. It runs twice, before the release and after the move, since step 2 runs the same check
  before anything moves: a failure before the release refuses with the set still held, and one
  after the move says `DON'T REBOOT` and sends Dan to *If it goes wrong*, never to a reboot or a
  restart of the stack. It prints kernel versions only, never a GRUB id or UUID, and fails a missing
  grubenv, which `grub-editenv` run as root would create. The manual GRUB check stays for the steps
  by hand, and that GRUB boots the newest kernel on this box is still unchecked (Task 12 Step 1).
  `updates.md` step 1 and S23 no longer say the check is left to Dan.
- **2026-09-25** — Phase 1's pre-flight review and a scan across its tasks, fixed in
  `website/design/phase-1.md` before Task 1, with every Task 1–10 listing run again on the Mac and
  in an `ubuntu:24.04` container. `make install-units` refuses a staged file holding one of ASCII's
  control characters other than tab and newline, which could hide a line of the diff it shows, and
  gives each read 10 s and 64 KiB; it ends with `sudo -k`. (Corrected 2026-09-25: this said it
  refuses "control characters"; UTF-8's C1 controls passed until the re-review, next line.)
  `spark apply` counts a llama-swap whose unit runs but that doesn't answer as
  having models loaded, judges a unit outdated by when its start began, to the microsecond, lists
  as restarts only those it makes, and says how to finish when a restart fails after its files are
  deployed. `admit()` caps `MemAvailable` at the allocatable ceiling, as *Admission and memory
  rules* says, and the brake waits 2 s at most for llama-swap. *Users, access and security* names
  the paths that stay open, for Phase 1's council, and no longer says "without Dan's password".
  Dated notes in *Components*, *Repo layout*, *Deploy workflow* and *Testing* say what Phase 1
  builds where this plan says more. Every push in the phase plan starts with the pre-push scan, and
  README §Contents changes with each task that makes it untrue.
- **2026-09-25** — The re-review of those fixes, its minors closed in `website/design/phase-1.md`.
  `make install-units` also refuses the UTF-8 encoding of a C1 control (C2 80 to C2 9F), such as
  CSI; other UTF-8 passes. `spark apply` names a running unit whose start time it can't read, with
  the command to restart it by hand, instead of skipping it silently, and Task 7 names a model that
  starts loading between apply's check and its llama-swap restart as a known limit. llama-swap's
  client turns an answer it can't read into an error, not a crash. Task 17's forward look decides
  whether Phase 1 builds `make deploy`.
- **2026-09-25** — Work runs on the Spark by default (Dan's rule). The Mac keeps its own clients and
  their config, changes under `.github/workflows/` (the Spark's repository-only token can't push
  them, a merge bringing one into `main` included) and the site render until Quarto is on the
  Spark. *Requirements* (Build), *Where work runs* and the phases' labels follow; CLAUDE.md and
  README §Conventions changed with it. Phase 1's plan relabels Tasks 1–10 and 17 `[Spark]`, moves
  the Mac → Spark switch point ahead of Task 1, and ends with Task 18, a `[Mac]` task: CI's render
  step, the site render and the merge.
- **2026-09-25** — Before the push, the final review's fixes. Phase 1's plan pushes after Task 10,
  so the Mac has the code for Task 15 and CI runs the two of Task 9's polkit tests that skip on the
  Spark without Node; Task 12 installs the rule only after that run is green. Task 18 pushes its CI
  change and sees CI green before the merge. Task 17's private findings go to the vault through
  Dan. The rule, here and in CLAUDE.md, also names a merge of `main` into a branch after a
  Dependabot Actions bump, which the Spark's token can't push either.
- **2026-09-25** — On the Spark, before Task 1: Dan's rule that every command in the docs says
  where it runs, in bold in the paragraph right above its block, never as a `#` comment inside
  it, which the Mac's zsh tries to run as a command (CLAUDE.md, *Conventions*; README
  §Conventions). The runbooks were relabelled the same day. Phase 1's plan: Task 9's two new
  runbooks and Task 10's runbook edits follow the rule, and Task 10's new paragraph in
  `updates.md` goes above the tmux block's labelled paragraph, not between it and the block.
- **2026-09-26** — Phase 1, Task 1's review: the registry loader refuses more than the plan's
  listing did. A missing, misspelled, repeated or wrongly typed field, a single value where a list
  goes, a boolean or null in `args`, a number that isn't finite, and a budget or brake value that
  isn't above 0 are each a `RegistryError` naming the model or section and the field, where the
  listing crashed with another exception or loaded them silently (commits f0f44c7, 828bee5,
  69cc076). Task 1's listing is marked *Superseded*. Since unknown keys are refused, the phase that
  adds the registry fields *Components* lists beyond Phase 1's (a footprint's peak, steady and
  config hash, cold start, idle policy, key access groups) extends the loader in the same change.
- **2026-09-26** — Phase 1, Task 2's review. The launch check decides the fit on exact decimals,
  and a refusal's numbers show its shortfall and never read as a fit ("needs 28.0 GiB, 51.6 GiB
  available, 24 GiB reserve kept: 0.4 GiB short"); the hold is fsynced so a freeze can't empty it;
  a damaged hold or a registry that won't load is a recorded refusal, not a crash (commits e575700,
  548ab5c; Task 2's listing is marked *Superseded*). Two open risks are new, *Engines share
  llama-swap's user* and *Phase 1's launch check is a static fit*: engines can read llama-swap's
  keys and delete the brake's hold, so the 2026-09-23 line's hold folder "writable by `spark-admin`
  only" keeps `agent` out, not engines, and Phase 1's plan now says "no engine's environment holds
  a key". Dan's decision: Task 17's security review decides whether engines get their own user.
- **2026-09-26** — Phase 1, Task 3's review. The llama-swap client reads `/running` strictly in
  v257's shape (read in v257's source: `handleRunning` always sends `{"running": [...]}`, `[]` when
  idle), so a stray answer can't read as "nothing loaded" and let `spark apply` restart llama-swap
  over loaded models. It refuses a key that isn't printable ASCII without sending or showing it,
  and never uses a proxy (commit 828fb3e; Task 3's listing is marked *Superseded*). Task 10's
  `Probe.http` gets the same two fixes (a dated note on its listing). One new open risk, *Anyone on
  the box can take 127.0.0.1:9100*, goes to Task 17's security review.
- **2026-09-26** — Phase 1, Task 4's reviews (two fix rounds). The brake never crashes on a failing
  hold write, a release mid-tick, an unreadable `/proc/meminfo` or a registry that won't load (it
  then brakes on the plan's thresholds), and it still unloads when it can't write the hold. It asks
  llama-swap only when memory is low. It counts memory on its way back — an engine `stopping`, or an
  unload with no answer in 2 s — before unloading another, so a slow stop no longer costs every
  model; its grace (15 s) and noise tolerance (0.5 GiB) are unmeasured (commits c249ce7, ebd3ad1;
  Task 4's listing is marked *Superseded*). Read in llama-swap v257's source: an unload is answered
  after the engine exits, and unloads run one at a time. A new open risk, *The minimal brake's
  reach*, and Task 16's brake drill now measures the brake's numbers.
- **2026-09-26** — Phase 1, Task 5's review. `spark status` exits 0 whatever it meets and says
  only true things to whoever runs it: an account outside `spark-admin`, such as `agent`, is told
  the brake's state is unknown to it rather than HOLDING, and "unreachable" means nothing answered,
  so a wrong key no longer invites a restart that stops every model. Refusal records are read only
  whole, and a release says who can run it (commits 36ed81d, 88f98aa, db4aece; the listings of
  Tasks 2, 4 and 5 note them). *Components*' Phase 1–2 line now says the refusal's explanation is
  for `spark-admin`. Phase 1's plan: Task 9's Makefile adds `make brake-release`, since the advice
  `spark brake --release` names a command that isn't on Dan's PATH; the brake's and llama-swap's
  units keep systemd's default `UMask`, so `spark-admin` can read the hold and refusal records.
- **2026-09-26** — Phase 1, Task 6's reviews (two fix rounds). The real registry pins the four
  models' current Hugging Face commits, resolved on the box. `spark render` now refuses, with the
  reason, a registry edit that would rebind an engine off 127.0.0.1, put a key in its command,
  download a model at start, override a setting render derives from the registry, or load a file
  outside the pinned snapshot; llama-server always runs `--offline`; a version or image can't add a
  line to what root runs; the Compose test pins each service's exact bind (commits f2ffd3b, 1e7e022,
  ed6195a, b26c6e5, a0a8612, 98f5f56; Task 6's and Task 1's listings note them). Refusals print
  `spark <command>: <reason>`. The open risk *Engines share llama-swap's user* adds the engine
  options that read or serve files, and Task 17 also decides an allowlist of engine options.
  Phase 1's plan:
  Task 11 checks each engine's path against its pinned version, and Task 13 checks that SearXNG can
  write its folder.
- **2026-09-26** — Phase 1, Task 7's reviews (two fix rounds). `spark apply` now finishes what a
  partial run left: a failed `uv sync` is retried, since the app counts as changed until a sync
  finishes, and a unit that started before apply wrote its files counts as outdated. It restarts
  llama-swap first, after a second look at `/running`, and puts that restart off if a model loaded
  meanwhile; after it, it waits up to 30 s for llama-swap to answer. The llama-swap and brake units
  run as `Type=exec`, so a restart whose binary can't start fails. Deployed files are written whole,
  and the dry run says what the real run would do, a refusal included (commits 4e639d0, 5641842,
  2801644, 81b8124; Tasks 3, 6 and 7's listings note them). Every test now runs in an environment
  it builds, so a failing test can't print a value from Dan's shell (be4c3c3). S17, a Phase 2
  scenario, notes what Phase 1's apply does until then. Phase 1's plan: Task 9's deploy.md gains
  the put-off restart and the wait, Task 12's staging stop lists only root's files, and Task 18's
  Mac check names what in the suite has never run on a Mac.
- **2026-09-26** — Phase 1, Task 8's reviews (two fix rounds). `spark models pull` refuses, before
  any download and without showing it, a Hugging Face token that a header or an error message would
  carry altered — one with a control character had printed whole in every FAILED line, into the
  journal — and every line it and the library log goes through one sanitiser: the token becomes a
  marker, a URL's query (a presigned URL's signature) is dropped, and the text stays on one line.
  The registry loads through render's `_load`. huggingface_hub is held below 2, since the lock had
  taken 2.0.0, a new major on a new HTTP stack two days old; the pull unit sets
  `HF_HUB_DISABLE_TELEMETRY=1` (commits cf21a82, 32deab1, e40372d, ae9708f; Tasks 6 and 8's
  listings note them). New open item: *Fresh releases in the lock*, Dan's decision. The open risk
  *Engines share llama-swap's user* adds the pull and hf_xet's own logs, and the Ops backlog's
  weights-on-the-Synology item notes the shared blob store. Phase 1's plan: Task 12 Step 4's FAILED
  line gets its other causes, the planned deploy.md's pull line says what follows it, the Global
  Constraints say when `/var/lib/local-ai` becomes root's on the box, and Task 17's security review
  names the open risks it decides.
- **2026-09-26** — Phase 1, Task 9's pre-dispatch scan and review (one fix round). Task 9 built
  pi's provider, `make install-units` with root's copies, the polkit rule, the deploy targets and
  two runbooks. Its fixes: the brake's advice names `make brake-release`, since `spark` isn't on
  Dan's PATH; `make bootstrap` and `make hold-gpu` end with `sudo -k`, as `make install-units`
  does — *Paths that stay open* is corrected, and Task 10 does the same for `make upgrade-gpu`; in
  CI a missing Node fails the polkit rule's tests instead of skipping them; `spark clients` keeps a
  backup's mode and names a file it can't read; `make pull` shows only its own run's journal; and
  deploy.md sets up Dan's own key on the Spark before the first deploy (commits 414ddaf to 914d6d4;
  Tasks 2, 4, 5 and 9's listings note them). A separate commit (432dfb3) keeps `#` comments out of
  shell blocks that run on the Mac, whose zsh passes them to the command; CLAUDE.md's labels rule
  says so. Phase 1's plan: Task 12 Step 4's `FAILED` line is corrected again (a repo that doesn't
  exist is fixed in the registry, and `make apply` comes before `make pull`), Task 15 checks
  whether pi 0.85.1 expands `${SPARK_API_KEY}` and its Mac block loses its comments, Task 16's
  brake drill expects `make brake-release`, and Task 17's security review gains three smaller
  items.
- **2026-09-26** — Phase 1, Task 10's pre-dispatch scan and review (one fix round). Task 10 built
  the needrestart override, `make upgrade-gpu` and `make doctor`. Its fixes: doctor sends Dan's key
  only to llama-swap — never through a proxy or a redirect, never shown, never to a URL that isn't
  one — and every FAIL says what to do; three checks joined (spark's folders, the engines' config
  files, the needrestart override: fifteen in all); doctor isn't read-only, since its end-to-end
  check loads the embeddings model and so clears the last refusal record, so `make status` comes
  first; `make upgrade-gpu` ends with `sudo -k`; and a signal that kills only the way out's hold
  now says to run `make hold-gpu` instead of suggesting the stack be started while the GPU set
  stays released (commits 748a093 to 1e5152b; Task 10's listings note them). The Phase 1 line above
  is corrected: a Docker upgrade that stops Docker and starts it again leaves the web services
  stopped, which Task 16's drill checks. The open risk *Anyone on the box can take 127.0.0.1:9100*
  adds `make doctor` as a sender. Phase 1's plan: Task 12 Step 1 expects doctor's three
  bootstrap-related lines to pass after the re-run, Tasks 13 and 16 expect 15 of 15 checks with
  `make status` first, and Task 18's Mac run gains the new Makefile and bootstrap code.
- **2026-09-27** — Dan's decision on *Fresh releases in the lock*, now resolved: `spark/uv.lock`
  takes only releases at least seven days old, a rolling window (`exclude-newer = "7 days"`; uv
  records the span in the lock, so no date moves by hand), and Dependabot's uv PRs wait as long.
  The lock moved to huggingface_hub 1.32.0 and filelock 4.0.1; 1.32.0's source has the token
  reader, the telemetry switch and the shared blob store Task 8's work relies on, and the suite
  passes. *Weekly upgrade day* records it, `updates.md` gains the steps
  and a per-package exception for an urgent fix, CLAUDE.md's uv rule and README's summary say so,
  and Phase 1's Task 17 looks back at how it went. The same day, Dan made the no-`#`-in-Mac-blocks
  rule (432dfb3) his own.
- **2026-09-27** — Phase 1's Task 11 installed the engines on the box, at their pins. Resolved from
  *To verify on the box*: `agent`'s CUDA access. Dan ran
  `sudo -u agent /opt/local-ai/bin/llama.cpp/b11146/llama-server --list-devices`, and it listed the
  GB10 as `CUDA0`, with `agent` outside the docker group. Learned: whisper.cpp, built on the box,
  loads the system's CUDA 13.0 runtime, which moves with the held GPU set, while the prebuilt
  llama.cpp carries its own 13.4. `make doctor` never loads whisper-server, so `updates.md`'s
  upgrade-day check (step 7) now also checks that it finds its libraries, and says to rebuild it if
  not. Both engines' registry paths name their pinned versions, checked by hand. `spark render`
  still doesn't cross-check them.
- **2026-09-27** — Phase 1's Task 12 deployed the config and pulled the models, and its forward
  look changed two things. A new open risk, *Page cache and the launch check*: the pull left
  43 GiB of page cache, which `MemAvailable` counts as available, and GB10 has been seen to reclaim
  it too slowly while a model loads. Task 13 Step 4 now records `free -g` before its first reading.
  And Task 13 Step 5's SearXNG check is corrected: the image's entrypoint runs SearXNG as root in
  its container and warns at every start about each mount its own user doesn't own, so the
  warning is expected. A working search and no permission error in its log are the check.
- **2026-09-28** — Dan's decision: Open WebUI's task calls run without thinking, and chats keep
  it. Phase 1's Task 13 found that both chat models think by default, with no limit: Gemma took
  about 2,200 tokens, some 28 seconds, before a five-word reply. Gemma is the task model, so
  titles, tags and search queries would be slow, or empty once a cap ran out. Dan keeps thinking
  at its highest in chats and turns it down there by hand. So Open WebUI gets
  `TASK_MODEL_PARAMS`, whose keys v0.11.4 adds to every task request:
  `chat_template_kwargs` with `enable_thinking` false, and a 1000-token cap, since setting it
  replaces the title task's own. Per request, that switch took both models from thousands of
  tokens to 7 or 8. A render test pins the setting; `make doctor` can't see task calls, which need
  an Open WebUI login, so Task 13 checks one on the box. S20, web search, records the behaviour.
- **2026-09-28** — Phase 1's Task 13 started the stack on the box and settled three items from
  *To verify on the box*. **`121` vs `121a-real`:** the whisper.cpp build for `121a-real`
  (Task 11) holds only `sm_121a` code, and it transcribed on the GPU, where whisper-server held
  about 2 GiB. ggml's own CMake turns a detected `121-real` into `121a-real`. So `121a-real` builds
  native code that runs here; no `121` build was made, so nothing says `121` fails. CLAUDE.md's
  `sm_121` gotcha records it. **GRUB boots the newest kernel:** Task 12's check passed on
  2026-09-27. Entry 0 boots the newest kernel, the `default=` lines are the stock two (GRUB starts
  entry 0), and GRUB's saved environment is empty. **A model's GPU memory and its engine's RSS:**
  the engines' RSS was 0.4–2.1 GiB, against 2–25 GiB each on the GPU (`nvidia-smi`), and
  their `oom_score`s sat within 9 of each other (1334–1343). earlyoom's dry run picked Gemma,
  which is resident, over the on-demand coder, the opposite of the brake's order. Task 17 decides
  whether resident models get a lower `oom_score_adj`.
- **2026-09-28** — Phase 1's Task 14 found that any photo aborted Gemma's engine. llama.cpp
  decodes an image's tokens in one micro-batch, and an image with more tokens than
  `--ubatch-size`, 512 by default, trips an assertion (`llama-context.cpp`) that kills the whole
  engine, not just the request. llama.cpp b11146 gives a Gemma 4 image up to 1120 tokens, about
  2.6 MP (`set_limit_image_tokens(70, 1120)` in `clip.cpp`), and scales bigger ones down to that.
  So every photo over about 1.2 MP failed, and every later message in its chat too, since each
  resends it. The registry now gives Gemma `--ubatch-size 2048` and `--image-max-tokens 1120`,
  Gemma 4's own maximum, and a test on the repo's registry holds every vision model to a
  micro-batch that fits its image budget. The engine's footprint is measured again once deployed.
  Measured the same day: 18.7 GiB on a cold load, against 17.6 at a micro-batch of 512, and about
  1.2 GiB more once it has read its first image, so the registry's estimate rose from 19 to 20 GiB.
- **2026-09-28** — Dan's decision: the Mac's pi comes from Homebrew and follows it, unpinned. It
  was 0.87.1 that day, inside the range reported to crash llama-server, and holding a Homebrew
  install at one version is more trouble than that risk. The pin, 0.85.1, stays for `agent`'s pi on
  the Spark: `stack/versions.yaml` now lists pi for the Spark only, and `pi.md` says so. So far,
  0.87.1's requests from the Mac have crashed no engine. Its only errors were four `400`s, from a
  45,822-token conversation sent to Gemma, whose requests top out at 16,384 tokens (32,768 of
  context over 2 slots). *To verify on the box* keeps pi's crash range open; Task 17 decides
  whether a deliberate test comes before the pin moves.
- **2026-09-28** — Dan's decision: every model runs at its native maximum context, which the
  pinned GGUF headers give as 262,144 tokens for Gemma 4 26B and Qwen3.6-35B-A3B and 32,768 for
  Qwen3-Embedding-0.6B. Whisper has no context window. Gemma's two slots now share one KV pool
  (`--kv-unified`, which render passes to any model with more than one slot), so one request can
  use all 262,144 tokens. Giving each slot a full window instead would cost twice the KV memory. Two
  long requests at once can exhaust the shared pool, which a single user rarely does. pi's
  `contextWindow` is now the model's whole `ctx`, not `ctx` over the slots. Only 5 of Gemma's 30
  layers keep a full-length cache, and 10 of the coder's 40 main layers plus its MTP block, so the
  longer caches cost little. Revised the same day, after the change's review and before it was
  deployed, from llama.cpp b11146's source:
  - The embedding model's micro-batch stays at the 2048 that actually runs. It pools its last
    token, so llama-server splits a long input, and `--batch-size` (2048 by default) caps
    `--ubatch-size`, so the 8192 it had, and the 32,768 the first version gave it, changed
    nothing.
  - With a shared pool, llama-server saves idle slots to the prompt cache and clears them whenever
    a task starts. A long chat outgrows Gemma's 1 GiB prompt cache, so each of Open WebUI's task
    calls, the search queries, follow-ups and tags it asks for around each turn, would have cost it
    a full re-read. Render also passes `--no-cache-idle-slots`: an idle slot keeps its cache until
    the pool runs short.
  - Context checkpoints, which llama-server keeps in host memory, are added a few per chat turn,
    about 0.6 GiB each for Gemma, and a slot's are thinned only once it holds the cap, 32 by
    default. So a dozen turns can fill a slot, about 19 GiB, at any context: the risk predates this
    change. Task 16's 0.58 GiB steps, taken while a pi session was using Gemma, probably were
    checkpoints; not verified. The full context only keeps the slot near the cap once it's
    full. A continuing chat restores its latest checkpoint, and an edit further back falls back to
    re-reading the whole prompt, so Gemma keeps at most 4 per slot (`--ctx-checkpoints 4`, a
    per-slot cap), 8 in all, counted in its footprint.
  The footprints rise to estimates, from 20, 4 and 29 GiB to 31, 7 and 32: 73 GiB for all four,
  within the 78 the budget allows, measured once deployed. (Corrected 2026-09-28, from Phase 1's
  council: once deployed, the cold loads took 24.7, 6.3 and 29.8 GiB, under those estimates, but a
  cold load leaves out what grows after admission. The footprints are now 32, 8 and 33, with the
  coder's checkpoints capped at 8: 76 of the 78; see the Revisions line below.) The phone can't
  change the context, since Open WebUI's `num_ctx` is for Ollama. `pi.md` and S09 say what each
  client can change.
- **2026-09-28** — From the review of Task 16's record. The routine upgrade moved neither `libc6`
  nor `libstdc++6` nor Docker, so Phase 1's Docker line, which said Task 16 would find out what a
  Docker upgrade does to the web services, gains a dated correction, and *To verify on the box*
  gains an upgrade that moves the C libraries and one that moves Docker. It also gains the memory
  that context checkpoints take over a long conversation. Task 17's forward look gains the brake's
  two numbers that Task 16 measured, `GRACE_S` and `FLOOR_TOLERANCE_GIB`: only an idle engine's
  stop was measured, and the one step above 0.5 GiB was probably a Gemma checkpoint, not noise.
- **2026-09-28** — From Phase 1's council (security). Each llama-server served `/slots`, every
  slot's in-flight request, and its own web UI to any account on the box, since an engine takes no
  key. Render now passes `--no-slots` and `--no-webui` to every llama-server, and a registry's
  `args` can't turn either back on. The llama.cpp row and *127.0.0.1 is not a boundary against
  `agent`* say so.
- **2026-09-28** — From Phase 1's council (security). The llama-swap client that the brake,
  `spark status` and `spark apply` use followed a redirect with the key, to wherever it pointed. It
  now refuses one, with the handler doctor's probe already used. *Anyone on the box can take
  127.0.0.1:9100* gains the correction; the port stays open to any user, for Dan to decide.
- **2026-09-28** — From Phase 1's council (reliability). The deployed brake had never sent its key:
  it asks llama-swap only below the warn line, so a key llama-swap refused would have shown first
  in an emergency. At start the brake now checks that llama-swap takes its key, logs the answer, and
  records it where `make status` shows it and `make doctor`'s `stack units` line reads it (still 15
  checks). `spark apply` checks that the brake is still running 3 s after restarting it, since
  `Type=exec` counts a brake that stops once it runs as started. *The minimal brake's reach* says
  so.
- **2026-09-28** — From Phase 1's council (reliability). Rule 6 counts a model's steady state after
  a soak at maximum context, and the footprints counted the load and the longer KV cache, plus
  Gemma's checkpoints and its first image's buffer (its old 20 GiB was the cold load with one image
  read), but not the rest of what grows after admission: the prompt caches, the coder's
  checkpoints and the embedding model's long-input buffer. At the
  admission edge, that growth could put a load on the brake line, and the hold would then block
  every load. The registry's comments now say what each estimate counts, from the cold loads
  measured that day (24.7, 6.3 and 29.8 GiB): Gemma 32, the embedding model 8, and the coder 33 with
  `--ctx-checkpoints 8`, since its default, 32, wasn't counted in its footprint. The set sums to 76
  of the 78 allowed, all still `footprint_measured: false`. The coder's checkpoints in *To verify*
  are capped now; their size is the first thing a soak measures.
- **2026-09-28** — From Phase 1's council (goal-fit). Step 4 of upgrade day, in *Backups, recovery
  and upgrades*, still said that GRUB booting the newest kernel was not yet checked on this box,
  though Task 12 checked it on 2026-09-27 and *To verify* had marked it resolved. It gains the
  dated result, as do S23 and `updates.md`; the check itself stays, since installing a kernel
  rebuilds the menu.
- **2026-09-28** — From Phase 1's council (goal-fit). The Requirements' *Always loaded* row says
  ~20–30 GB, and at full context the residents measured 33.5 GiB cold and are budgeted at 43. The
  row gains a dated note, not a new figure, since the requirement is Dan's: the static budget has
  2 GiB left for later phases' additions, such as Phase 3's batch whisper and speaker labels.
- **2026-09-28** — From Phase 1's council (toolstack, goal-fit). `updates.md` gains a row for each
  of the stack's own pinned components, llama.cpp, llama-swap, whisper.cpp, Open WebUI and SearXNG,
  which move by hand on upgrade day, one at a time; each one's runbook is written with its first
  bump, and Open WebUI's data is copied before any upgrade, since its database migrations can't be
  undone. It also corrects what Dependabot's uv PRs change: `spark/pyproject.toml` as well as the
  lock, so a PR could widen the `huggingface_hub<2` cap. *Weekly upgrade day* gains the correction.
  A test that ties the code's version-specific assumptions to `stack/versions.yaml` waits.
- **2026-09-28** — From Phase 1's council (reliability, goal-fit), doc truth only. *Page cache and
  the launch check* gains a new reading and three options; *The minimal brake's reach* gains the
  measured rates of fall; the llama-swap row says Phase 1's apply refuses rather than waits.
- **2026-09-28** — From Phase 1's council (reliability), decided at Phase 1's close: the brake's
  `GRACE_S` stays 15 s and `FLOOR_TOLERANCE_GIB` goes from 0.5 to 1.0 GiB, since at 0.5 one Gemma
  checkpoint during a slow stop would unload a resident too. *The minimal brake's reach* says so,
  and leaves a busy engine's stop, and how long an unloaded engine's memory takes to show in
  `MemAvailable`, to Phase 2's drill.
- **2026-09-28** — From Phase 1's council (reliability), decided at Phase 1's close: earlyoom
  follows the brake's order. `spark launch` gives a resident engine `oom_score_adj` 900 and an
  on-demand one 1000, since a model's GPU memory isn't in its engine's RSS, and says on stderr when
  it can't set it. *To verify* notes the order, untested on the box until the next deploy's
  earlyoom dry run. (Checked after that deploy, 2026-09-28: the dry run picked the coder.)
- **2026-09-28** — From Phase 1's council (security), Dan's decision: render allows only listed
  engine options. A registry's `args` may set only what the registry and the test fixture set that
  day, in every spelling each engine's `--help` gives, and anything else is refused, naming the
  flag, after the refusals render already had, which keep their reasons. *Engines share
  llama-swap's user* gains the decision, and a user of their own for the engines moves to Phase 2.
- **2026-09-28** — From Phase 1's council (toolstack), Dan's decision: llama-swap logs the engines
  to the journal. Render sets `logToStdout: both`, v257's value for its own lines and the engines'
  together, so a refused start's reason and an engine's crash outlive the in-memory buffer every
  restart wiped. That evening's check found no prompt text in the engines' output at default
  verbosity, and the allowlist keeps logging options out. The llama-swap row says so.
- **2026-09-28** — Reversed the same evening, before it was deployed (Dan's decision): llama-swap
  stays at v257's default, `logToStdout: proxy`, pinned by a render test. The check behind `both`
  had read only the chat engines' output: whisper wasn't loaded. whisper-server v1.9.4 logs each
  upload's file name, and its ffmpeg conversion reports the file's metadata tags, which the journal
  would keep; the transcript itself isn't logged. A refused start's reason stays in `make status`
  (the last one) and llama-swap's in-memory buffer. Phase 2 revisits the journal with a check that
  covers speech. The allowlist still refuses the engines' logging options.
- **2026-09-28** — Phase 1's forward look (Task 17 Step 5), after its council. *Readings against
  the budget:* at full context the cold loads took 24.7 GiB (Gemma), 6.3 (embeddings) and 29.8
  (the coder), and whisper 2.5 (Task 13); the footprints, which now count what grows after
  admission, sum to 76 of the 78 allowed. Loads took 5 s, 1 s and 21 s, and prefill ran at about
  1,900 tokens a second on Gemma and 1,600 on the coder (the context change's checks); decode ran
  at 78 and 93 (Task 13). The CUDA-allocatable ceiling is still unmeasured. *A refused start's
  reason* doesn't reach llama-swap's journal at v257's default logging, which stays (the reversal
  above). *v257's surprises:* an unload is answered only once the engine has exited, one at a time;
  unloading a starting engine answers at once and fails the request that started it; `${env.…}`
  is filled in anywhere in the config, and `cmd` is split as a shell would; `-validate` ignores
  keys it doesn't know; a 404 is logged without its model; the binary embeds a Tailscale-protocol
  peer server, off unless configured; and its embedded guide shows a `logToStdout` value its own
  schema rejects. *Dan's decisions:* no `make deploy` from the Mac (*Deploy workflow*); render
  allows only listed engine options, and the engines and the pull get a user of their own in
  Phase 2; llama-swap's port stays for Phase 1, with the redirect refused, and moves behind a Unix
  socket with Phase 2's gate; llama-swap's logging stays at its default (reversed from `both`);
  swap and swappiness wait for Phase 2's measurement; `agent`'s GPU jobs get an OOM score before
  `agent` runs GPU work; pi 0.87.1 for `agent` after a deliberate test in Phase 2; systemd
  sandboxing for the stack's units and `cap_drop` for the web containers in Phase 2; the seven-day
  window stays and now also covers pins set by hand (`CLAUDE.md`); Dependabot's PRs #1–#3 merge on
  the Mac after Phase 1's merge; Phase 1 closes on the routine upgrade's weaker evidence; S09 is
  verified on Task 14's phone pass, its newer settings checked in the Mac's browser; every model
  keeps its full context, with the settings that would save memory in the Backlog. *Rulings at
  the close, from the council, which Dan didn't decide himself:* residents at `oom_score_adj` 900
  and on-demand models at 1000, so earlyoom follows the brake; `GRACE_S` stays 15 s and
  `FLOOR_TOLERANCE_GIB` becomes 1.0 GiB; the brake checks at start that llama-swap takes its key.
  Phase 2's line lists what it inherits, and [the retrospective](phase-1-retro.md) holds the
  deferred minors.
- **2026-09-28** — Phase 5 gains notes for serving Qwen3.8-27B with SGLang, from
  MiaAI-Lab's DGX Spark repo (Dan's pointer): the engine, the speculative decoders DSpark and
  DFlash2, and what this stack must account for to run it, above all a container engine under
  llama-swap and SGLang's memory claim. The README's Reference section lists the repo.
- **2026-09-29** — Phase 1 is done: its tasks ran from 2026-09-25 to 2026-09-29, and the last
  step, merging `phase-1` into `main`, follows this line. [The retrospective](phase-1-retro.md)
  records what it built, where it departed from this plan and why, what the reviews found, and
  what Phase 2 inherits.
- **2026-09-30** — Orca and the web UI on the Mac, recorded between Phases 1 and 2, not as a
  phase. A Mac session designed it on 2026-09-28 and 2026-09-29, from research on Orca, a
  four-lens council, Dan's answers to scenario questions and a test of pi in Orca over the tunnel.
  Orca stays on the Mac, in its local mode; goal 1 allows reporting hooks, worded under *Claude is
  untouched*, and no Claude that Orca starts skips permissions; `claude-dgx` is dropped; the Spark
  as Orca's server, and the phone, go to the Backlog with their costs; Phase 2 gains a line. The
  design first ran agents on the Spark through Orca's SSH mode, as `agent`, and was cut back when
  Dan deferred the phone and asked why it was so complicated; SSH mode's findings are in the
  Backlog item. A review of this plan's changes the same day (three reviewers: faithfulness, rule
  wording and security, accuracy) tightened the hook wording — exit 0 and print nothing or `{}`,
  only hook entries in Claude's settings file, apps that pass nothing on, resumed sessions and
  Orca's environment setting covered — and corrected the spool's retention. It was first written as
  a Phase 1b with a plan of its own (two scenario pages, a scenario-check change, a `make doctor`
  check for Orca's relay folder, a closing review); Dan dropped that the same day, since pi in Orca
  and the web UI already worked, and kept the decisions, a runbook and the notes. The plan was
  never pushed.
- **2026-09-30** — The site gains an [Architecture](../architecture.qmd) page: where everything
  sits, the three paths a request takes, and every scenario's path, with built parts solid and
  planned ones dashed. The text drawing under *Request flow* gives way to it, so the diagrams live
  in one place, and every phase now ends by updating them. Dan's rule: the architecture diagrams
  are kept true first, since they are the quickest way for him to see what is happening
  (`CLAUDE.md`, *Docs must be true*).
- **2026-10-05** — Phase 5 gains how Qwen3.8-27B gets optimized, which Dan called the point of the
  whole exercise: four routes (Dynamic GGUF with MTP, the same with a DFlash2 drafter, NVFP4 on
  vLLM, NVFP4 on SGLang), measured in his order — speed in pi, quality per GB, long context — with
  the cautions for engines that claim memory up front. Routes A and B need nothing new, so Phase 2
  runs them on `spark try` once it exists; the rest stays in Phase 5. The SGLang notes are
  corrected: DFlash2 is in llama.cpp's b11146 and SGLang's releases from v0.5.19. Phase 3 records
  Dan's audio host — on the Mac, built on Pixeltable, designed in a private repo of its own when
  Phase 3 comes. The research behind the numbers was web-only; none of them is measured here.
- **2026-10-05** — Dan's first answers in Phase 2's brainstorm (recorded 2026-10-07, with the rest
  of Phase 2a's design, in the lines below). Phase 2 splits into three sub-phases, 2a the gate, 2b
  visibility and 2c the lab, each with its own short plan, review and merge, and the hardening and
  measurement items it inherited fold into whichever touches their code. A load that doesn't fit
  waits per key, 30 s for Dan's keys and 10 minutes for `agent`'s, then is refused with its reason.
  The brake's hold lifts by itself once memory has stayed above the warn line for a few minutes
  (written as 5), its notification saying when it fired and released, and the gate then reloads the
  residents one at a time. ntfy moves up from 2b to the start of 2a, set up by Dan, so the gate
  notifies from its first day; the watchdog stays in 2b. `make apply` shows the diff, then waits
  until no request has been in flight for ~60 s before it restarts llama-swap. Pins have no
  schedule by default; a weekday preload with a work-hours pin is a setting, off by default.
  (Bounded on 2026-10-07, after the council: the brake's release and `apply`'s wait, below.)
- **2026-10-07** — Phase 6, other people, joins the end of the plan: family and friends over a
  shared Tailscale node (only the Spark visible to them; a web UI account Dan creates, and a key if
  wanted), with ZeroTier recorded as the alternative for someone who can't use Tailscale; and
  guests at an event on the venue's network only, by address, port and a key each, picking from a
  menu of models Dan prepares, with off-menu requests going through `spark try`, and a guest web UI
  of its own. "Other users", parked since 2026-09-23, now points here. Dan may take the Spark to a
  data-science and AI retreat, a long way off, which is why. Written on the Mac as a pull request
  while the Spark session worked on Phase 2.
- **2026-10-07** — Dan's decision on Phase 2a's architecture: a two-part gate in front of
  llama-swap, chosen over the gate beside llama-swap that this plan described and over bringing
  LiteLLM forward from Phase 3. A new front, `local-ai-front.service`, takes over 127.0.0.1:9100
  with the same client keys, counts requests in flight per model and forwards with an internal key;
  the gate, `local-ai-gate.service`, replaces `local-ai-brake` on the planned Unix sockets and makes
  every decision; llama-swap moves to 127.0.0.1:9101 with internal keys only, and `spark launch`
  keeps a zero-wait fit check as a backstop; an `OnFailure=` notifier on all three sends the ntfy
  alert without the gate. The reason is llama-swap v257, read in its source on 2026-10-05: nothing
  tells `cmd` which key asked, every key shares one start, a launch refusal reaches the client as a
  bare `500`, in-flight counts come only through a lossy event stream, a waiting `cmd` is bounded
  by one global timeout, and it listens on TCP only. A new Design subsection, *The front and the
  gate*, holds it. Rule 1 gains a dated correction, since admission moves to the front; rules 7
  and 8, *Request flow*, the *Gateway* requirement, the Components rows (a new front row, the gate
  and llama-swap) and *Users, access and security* follow. The engines' own user isn't in 2a.
  *Anyone on the box can take 127.0.0.1:9100* is mostly closed by the front once it is built, with
  what remains listed, and Phase 3 gains the question of LiteLLM against the front. (Revised the
  same day after the council: the lines below.)
- **2026-10-07** — Phase 2a's memory rules, Dan's decisions (the earlier ones are in the
  2026-10-05 line). An on-demand model idle-unloads after 60 minutes, not 30, and a resident never
  does. `spark make-room` offers everything, residents included, largest first, and `--all`
  unloads everything after one confirmation. No idle unload or make-room cuts off a request in
  flight, since llama-swap's unload would kill it; only the brake may. Rules 3–5 and the *Idle
  unload* requirement gain dated notes. (Revised the same day after the council: the lines below.)
- **2026-10-07** — The budget, Dan's decision: "fix the check and lower the reserve a little".
  `spark render`'s check summed every footprint against `allocatable − reserve`, 78, which counted
  the reserve twice; from 2a it checks the set against the CUDA-allocatable ceiling and the
  residents against the reserve at idle, while the gate checks each load. The reserve goes from 24
  to 22 GiB, still above the brake's 20; Dan lowered it knowingly, since freeze protection is why it
  exists. 2a measures the ceiling, carefully. Rule 9 is new; rule 5, the *Always loaded*
  requirement, *To verify* and a new open item follow, and so does `CLAUDE.md`'s GB10 gotcha. The
  registry and the code keep 24 until 2a builds it. (Revised the same day after the council: the
  reserve went back to 24, Dan's decision, and the corrected check stays; the lines below.)
- **2026-10-07** — Dan's decision: Qwen3.8-27B becomes the coder in Phase 2a, replacing
  Qwen3.6-35B-A3B, by Phase 5's route A (`unsloth/Qwen3.8-27B-GGUF`, `UD-Q4_K_XL`, 17.6 GB, with
  MTP), at its full context with an f16 KV cache, like every model. Its footprint is estimated at
  ~38 GiB, which brings the set to ~81; its reported decode, 15–27 tok/s against today's 93, is a
  trade Dan accepts, with route B in 2c. Qwen3.6-35B-A3B leaves the registry, and its files stay on
  disk. The *Models* requirement and Phase 5's routes gain dated notes, and pi's provider is
  rendered again after the deploy. (Revised the same day after the council: the estimate rose to
  ~41 GiB and the set to ~84; the lines below.)
- **2026-10-07** — What Dan sees from Phase 2a, his decisions. ntfy is reached over the tailnet or
  the home LAN only, with no public relay; quiet hours run 00:00–05:00, when only high-priority
  alerts make a sound; the priorities stay, with "brake released, and what reloaded" at default.
  `spark status` adds requests waiting, a history of recent refusals and `--json`, and
  `spark doctor` gains the front, the gate and ntfy. A refusal shows inline in the client from 2a,
  so S03, which said Phase 3, is corrected. *Visibility and notifications* and the ntfy row follow.
  (Revised the same day after the council: quiet hours are set on the phone, and the Mac's alerts
  wait for 2b; the lines below.)
- **2026-10-07** — Phase 2 becomes Phases 2a, 2b and 2c, each with its items, its scenarios and a
  done-when: 2a, S01's gate part, S02, S03, S05, S14 and S17 verified; 2b, S06, S12, S13 and S01's
  menu bar; 2c, S11, and route B measured. The items Phase 2 had from Phase 1's close and from Orca
  are distributed among them, and the three not yet placed are named. The Architecture page's
  planned parts follow the design: the front on 9100, llama-swap's move to 9101, the gate beside the
  front, the failure notifier, ntfy apart from the watchdog, the brake folding into the gate, and a
  planned path in *Loading a model*. Scenario pages S01, S02, S03, S04, S05, S10, S14 and S17 gain
  dated notes, their statuses unchanged. Written on the Spark, for the council's review and Dan's
  approval, before `website/design/phase-2a.md`. (Revised the same day after the council: the
  lines below.)
- **2026-10-07** — Phase 2a's design council: four reviewers, read-only, on the design commit
  (goal-fit and scenarios; reliability; security and simplicity; toolstack). Together they found
  1 Critical, 36 Important and 49 Minor findings, and put their questions to Dan, who answered the
  same day: "all recommended". The design is revised in place, each revised part saying so, and
  the three lines below record what changed. The minor findings not taken up go to the notes of
  `website/design/phase-2a.md`.
- **2026-10-07** — Dan's decisions after the council. (1) **The reserve goes back to 24 GiB**,
  revisited once 2a has measured the footprints: the corrected check is what lets Qwen3.8-27B fit
  at f16, and 22 only halved the margin above the brake while every footprint is an estimate. It
  went 24 → 22 → 24 the same day; the corrected check stays. (2) **The brake stays its own unit,**
  `local-ai-brake`, not inside the gate; the gate reads and writes its hold, so a gate crash never
  means no brake. (3) **The brake's automatic release:** at most one an hour, then the hold waits
  for Dan; a hold found after a reboot waits for him, with an alert; it releases only if the
  reloads fit; and the model that was loading when it fired doesn't reload by itself. (4)
  **make-room holds the room it frees** until `spark make-room --done`, a duration he gives, or
  the next boot, and the gate counts it as reserved. (5) **`agent`'s pi waits about 15 minutes**
  for a response, since pi 0.85.1's `httpIdleTimeoutMs` defaults to 300,000, shorter than
  `agent`'s 10-minute wait; `spark clients` writes it. (6) **Systemd's socket units hold
  127.0.0.1:9100 and both gate sockets,** and **llama-swap and the engines move below 1024**, to
  900 and to 800 and up, with `CAP_NET_BIND_SERVICE`; the 9100 open item is closed as designed,
  with what remains. (7) **The front runs as `spark-front`.** (8) **Into 2a:** llama-swap's
  sandboxing, secret files `0600 root:root`, and the pull's own user, `spark-pull`; the engines'
  own user stays for later, its reason corrected. (9) **`agent`'s own GPU jobs:** a root-set
  `oom_score_adj` in 2a if it holds when tested as `agent`, otherwise 2b, with a new open item.
  (10) **The stack:** uvicorn, a raw-ASGI front, a Starlette gate and httpx, two new packages, in
  place of FastAPI's nine. (11) **Alerts reach the phone only in 2a,** the Mac with 2b's menu bar.
- **2026-10-07** — The session's rulings after the council, which Dan didn't object to: ntfy is
  pinned at v2.28.0 by its index digest, since v2.29.0, out that day, is inside the seven-day
  window; llama-swap stays at v257, with `healthCheckTimeout` 180 s, not 600; admission tickets,
  so nothing starts without the gate; a gate that is down at boot means new loads are refused;
  `apply`'s quiet wait gets a 15-minute deadline, then "drain now", then `apply-now`; quiet hours
  are set on Dan's phone; the architecture decision stays dated 2026-10-07; S11's trial note is
  written on the Spark and filed in the vault by Dan; and whether route B becomes the coder is
  2c's call, with its measurements.
- **2026-10-07** — The council's findings, taken into the design. The gate's admission holds back
  the growth the loaded models are still owed, and any model still starting (the reliability
  Critical), and rule 9 gives both checks their formulas, the gate's at each load and render's
  three, with a warning. The front forwards only the inference routes, holds the client keys as
  digests, and has limits on its memory, bodies, uploads (spooled to disk), waiting requests and
  connections. The gate knows each caller by its uid: pins are Dan's, and `agent`'s sessions are
  its own, tied to a live process, capped and expiring. One internal key per caller, through
  `LoadCredential=`, nothing secret in argv or a log, and no key for Dan's commands, which use the
  sockets. The queue puts Dan's keys first, and the key's wait is defined once, the one-load slot
  and a client that has gone included. *The front and the gate* gains what each failure costs, the
  drain between the gate and the front, the watchdogs, and a notifier that runs without the app's
  venv, its token in a header file. Rule 7 gains `load_failed`, `restarting` and
  `llama_swap_down`, and a refusal is a `503` that clients don't retry by themselves. `make apply`
  restarts the front only for its own code, writes nothing until the drain, and swaps the coder in
  an order that never leaves a loaded model the registry doesn't list. The gate's load call waits
  past `healthCheckTimeout`, and the brake shares no event loop. Rule 1's promise to drop caches is
  corrected, and 2a gains the page-cache drill and the rate-of-fall watch. The coder's estimate
  rises to ~41 GiB, with what 2a measures first, and 2a's done-when gains Qwen3.8-27B as the
  working coder and the measurements. ntfy's reach becomes 2a items: the phone, the Synology on the
  tailnet and its ACL grant, the private values file, a publish-only token per publisher.
  `CLAUDE.md`'s gotchas on the reserve, the brake and reloading llama-swap gain dated notes, and
  the Architecture page and scenario pages S01–S06, S11, S14 and S17 follow.
- **2026-10-07** — Phase 2a's design re-review, read-only, on the revision: every decision,
  ruling and Critical or Important finding addressed, and 3 new Important findings and 15 Minor.
  **Socket activation through a crash loop:** a socket unit past its trigger limit, or one whose
  service has hit its start limit, closes its socket, so the socket units set
  `TriggerLimitIntervalSec=0` and the front, the gate and the brake `StartLimitIntervalSec=0`, and
  2a checks as `agent` that a crash loop never frees 9100. **Owed growth counted twice:** a
  model's *owed* is now its footprint less what it holds now, its load's fall plus its engine's
  RSS growth since, which 2a checks on the box; render and the gate then hold one formula, and the
  2a set passes with nothing else running, as promised. **SO_PEERCRED under uvicorn:** a small
  uvicorn protocol subclass puts the caller's uid and gid in the ASGI scope, with a test. Dan's
  choices: the model that was loading when the brake fired may be loaded by his own request,
  through admission, while `agent`'s requests get `footprint_suspect` (it had been every request,
  with `held_by_brake`); and `cap_drop` for Open WebUI and SearXNG joins 2a, which corrects the
  9100 item's "what remains". The session's ruling, which Dan agreed: while the gate is down the
  brake sends its own "brake fired" to ntfy, by the failure notifier's path, with a token of its
  own. The minors are all taken: the drain's 30 s and a `draining` code, the front re-asking after
  a gate restart and when it counts the gate down, tickets and refusals in a folder of their own
  with the hold read-only to llama-swap's sandbox, the front's re-serialized body and duplicate
  `model` fields, a cap on a load's measured fall, a polkit-started unit for any cache drop, the
  bounding set in the cold-boot test, ticket expiry, `model_not_found`, the front restarted for
  registry and key changes, a resident that doesn't fit after a hold, doctor's key, make-room
  asked for too much, the Hugging Face cache's move to `spark-pull`, and the `launch` node's label.
  S02, S05, S14 and the Architecture page follow.
- **2026-10-07** — What Dan sees in 2a, written into the design (*What you see in Phase 2a*), from
  the session's draft and Dan's guidance: user experience matters, there should be no confusion,
  and he would "rather err on more notifications than something not being clear", with the types
  switched on and off later; he left the wording to the session's judgement. Every refusal code has
  a message, one sentence, the numbers and one next step, with the code in the error's `code`
  field; `loading` now means a wait that ran out in the queue for the load slot, and the front's
  own refusals gain `model_not_found`, `too_many_requests` and `route_not_served`. Every
  notification type has a name and a priority in one list in the registry, all on by default, the
  table generated from it; new are `load_started`, `waiting` and `pin_ended` (low), and
  `room_hold_ended`, `apply_restarted`, `resident_waiting` and `back_up` (default). One
  notification per event, and a burst of identical refusals collapses into one with a count.
  Quiet hours stay on the phone; `spark status` speaks in plain words (*always loaded*, *loads
  when asked*, *paused*, models by role first); each command says what it did and how to undo it;
  nothing is injected into a reply stream. Dan's decision: `agent`'s requests for the model that
  was loading when the brake fired keep waiting, then `footprint_suspect`, with a notification
  naming `spark load <model>`. S01, S02, S03, S05, S14 and S17 gain dated notes.
- **2026-10-07** — The design's final re-review, on the second fix pass and the UX pass, found 2
  new Important findings and 14 Minor; Dan having left the UX to the session, these are its
  rulings. **make-room's hold is Dan's:** his keys may load into it, and it shrinks by what they
  take from it, while `agent`'s requests and the automatic reloads may not, so `no_fit`'s "free
  space with `spark make-room 41G` on the Spark, then try again" loads the model he wanted (rule
  4). **One word per number:** *available* is always `MemAvailable`, *free for a load* always the
  admission figure, in every message, notification, status line and example. **Owed growth reads
  anonymous RSS** (`RssAnon`), since the embeddings engine maps its model file, and 2a's soak
  compares it with `MemAvailable`'s fall and `nvidia-smi`'s per-process figure. The minors, all
  taken: whisper's tmp-dir leaves the Hugging Face cache before it moves to `spark-pull`; the
  status example shows the coder's brake mark and `agent`'s wait for Dan; examples reload only
  what was unloaded; `brake_fired` names every unload, with follow-ups, and isn't sent twice;
  `back_up` covers the brake and waits a minute; the `*_down` alerts name where to act; a low
  `memory_warning` joins the list; `agent`'s refusals don't name Dan's processes; "only Dan can
  load it"; llama-swap's `429` is passed on, worded; "leaves 50 GiB free for a load"; and *paused*
  is the brake's word, *held* make-room's, with `model_not_found`'s names an exception. S02, S03,
  S05, S14 and the Architecture page follow.
- **2026-10-07** — Phase 2a's implementation plan, [`phase-2a.md`](phase-2a.md), written from the
  approved design: 50 tasks, 46 on the Spark, 2 Dan's and 2 on the Mac, as contracts — files,
  interfaces, each test and what it asserts — rather than code (the controller's ruling; every
  command block in it run first, on stand-ins where the real thing would change the box). The
  controller's rulings on the plan's questions, Dan having left them to the session: the weekday
  preload isn't built in 2a, and waits in the Backlog (rule 3, the Phase 2a line and S01 say so);
  the private values file is `/etc/local-ai/values.env`, its topic names private too; the values
  the spec left open (caps, timeouts, the notifier's interval, each code's retry-after) are the
  plan's Global Constraints; the notifications table is generated at
  `website/reference/notifications.md`; `spark` and `spark-front` hold no sessions;
  `make brake-release` falls back to Phase 1's direct release while the gate doesn't answer;
  `llama-swap.env` stays, root-only, for the rollback until 2a's close; and `spark doctor`'s
  upload and privacy canary run only with `--full`. Dan's additions the same day: ntfy is a Docker
  Compose file deployed with any Compose helper, Portainer's stacks today (the Phase 2a line's
  ntfy item), and a questions-and-answers page, [`phase-2a-qa.md`](phase-2a-qa.md), records why
  the design is what it is. pi's and Open WebUI's handling of a refusal was checked from their
  source (*The front and the gate*): pi retries a refused turn by itself, up to three times.
- **2026-10-07** — The Spark stays a hybrid (Dan): bare metal for the engines and the stack's own
  services, Compose for apps that ship as images; Docker's own processes measured at about 160 MiB.
  *Hybrid runtime* says why, and when it gets revisited.
- **2026-10-07** — Refusals that come after a wait (`no_fit`, `loading`, `held_by_brake`,
  `footprint_suspect`) are `409`s, not `503`s (the controller's ruling, Dan having left the UX to
  the session): pi retries any error whose text holds "503" or "429" by itself, up to three
  times, so Dan's 30 s refusal would have reached him after about 2¼ minutes, and `agent`'s after
  about 40. The outages keep `503`, `x-should-retry: false` stays on both, and `Retry-After` comes
  only with a code's retry-after. *The front and the gate*, rule 7, *What you see in Phase 2a*, the
  Architecture page, S03, S04, the implementation plan and the Q&A page follow.
- **2026-10-07** — `load_failed` and `not_downloaded` are `409`s too (the controller's ruling):
  neither is transient, since a retry of `load_failed` repeats a full load and `not_downloaded`
  changes only with `make pull`. A `503` is now only for `gate_down`, `llama_swap_down`,
  `restarting` and `draining`.
- **2026-10-07** — The implementation plan's review (1 Critical, 18 Important, 24 Minor) corrected
  the design in four places: rule 5's rate-of-fall watch subtracts the fall of loads the gate
  admitted, since it would have fired on every coder load; the CUDA ceiling's steps and the drills'
  memory hog are paced under that watch, sized from `MemAvailable` at the time and confirmed step
  by step; "before it becomes the default" means before real work, with both pis listing the
  coder from the swap on; and each refusal sends exactly one notification, the front's own
  included. The implementation plan carries the rest.
- **2026-10-07** — The implementation plan's re-check (the controller's rulings): `spark status`'s
  example gains *used by other processes: 26 GiB*, which its formula gives; `make apply` restarts
  in a fixed order, its restarting hold kept in the gate's persisted state until llama-swap answers
  again; the polkit rule's list grows to seven, with the S05 drill's oneshot unit, which runs
  `spark brake --once` against a drill copy of the registry; and swap is measured down to 24 GiB
  available only.
- **2026-10-07** — The implementation plan's final check: apply's hold ends one way, whatever ends
  it, with the residents' reload first; `make apply` renews it and the gate ends one that lapses;
  the front drops it once the gate has been gone a minute; and *used by other processes* shows only
  above 0.
- **2026-10-07** — Phase 2a's design approved by Dan the same day; the implementation plan written
  from it (`CLAUDE.md`'s notes said "awaiting Dan's approval" until the forward-and-back council
  found them).
- **2026-10-07** — Ansible: later, for the homelab as a whole, not for the Spark now (Dan); the
  Backlog has the line, and the [Q&A](phase-2a-qa.md) the reasons.
- **2026-10-07** — The implementation plan's forward-and-back council (four reviewers: the docs,
  the box and its code, executing the plan, the later phases; 0 Critical, 40 Important, 57
  Minor), and Dan's fourteen decisions on it ("all recommended"): (1) `restarting`,
  `llama_swap_down` and `draining` are `409`s too, so only `gate_down` stays a `503`; (2) nothing
  deploys from `phase-2a` until the cutover, and an urgent fix goes from `main`; (3) `cap_drop`
  for the web containers comes before the cutover; (4) Dependabot's `spark/` bumps wait until 2a
  merges; (5) the residents reload by themselves after an unplanned llama-swap restart or an
  earlyoom kill; (6) the CUDA ceiling is recorded as *at least* the figure measured when the method
  stops at the reserve; (7) values a task sets from a measurement are the controller's rulings,
  Dan told; (8) the gate-down drills make the gate fail at start, with Dan's sudo; (9) the
  implementation plan's Tasks 6, 17 and 23 split, 43 and 44 merge, 52 tasks in all; (10) render's
  whole-set check becomes a warning, with a `needs_room` flag for a model that loads only after
  make-room; (11) the design's one `dan` flag splits into five settings of a key group, and the
  key list moves to a private file; (12) the front takes its model list from the gate's events;
  (13) Phase 3 weighs no LiteLLM as a third option; (14) `spark session hold`, in 2a. This plan's
  superseded design clauses are struck through, their dated notes kept; the 2a scenario pages read
  as one current account each, their history moved to the [Q&A](phase-2a-qa.md); and the later
  phases' lines carry what 2a's design changes for them (2b, 2c, 3, 5, 6).
- **2026-10-07** — Phase 2a's Task 1 and its review corrected the implementation plan, with dated
  notes there: ntfy's address, port, data folder and topic names may go in the vault, but its
  hashes and tokens never do, only in its Compose helper and (the tokens) the Spark's root-only
  header files, the vault recording where (`CLAUDE.md`'s rule; Task 1's §3 and Task 2's
  `CLAUDE.md` change had said "the helper's variables and the vault"); Task 2's grant check reads
  "the Spark's tag reaches only ntfy's port, and the members' access is unchanged", since §2 keeps
  every member device's access to the tagged NAS; its Step 3 expects ntfy's web page to load,
  with only a topic asking for a login; and Compose, given no values, names whichever missing
  variable it reaches first, not always `NTFY_BASE_URL`.
- **2026-10-07** — Phase 2a's Task 5 review: two rulings by the session, at that review, each
  keeping what rule 4's hold and the residents' load at boot intend, with dated notes in rule 9
  and in the implementation plan's Task 5. Render's check of the residents gains the ceiling term:
  they load together at boot, so together they fit `min(idle MemAvailable − reserve, ceiling)`,
  where the first check left the ceiling out. And a load of Dan's into make-room's hold shrinks it
  only by the part the room outside it couldn't cover, that room counting as none once his job has
  taken it, `max(0, hold − max(0, footprint − max(0, free_outside_hold)))`, so the hold keeps
  counting what his job allocated (rule 4); the first formula shrank a 70 GiB hold to 7 for a
  5 GiB load. Rule 9's head, which still said render checks the footprints together against the
  ceiling, is corrected too.
- **2026-10-07** — Phase 2a's Task 6, the controller's rulings on its concerns. Dated notes are in
  *What you see in Phase 2a* and in the implementation plan's Tasks 6, 15 and 20.
  - `model_not_found` gains `agent`'s next step, *On the Spark, as `agent`: pull its clone and run
    `spark clients pi --write` to update pi's list.*, since `agent`'s pi is on the Spark. *(Corrected
    at Task 6's review, the same day: see the next entry.)*
  - A refusal names a process whose name pi's retry list matches only as *a process*, so pi never
    retries a refusal because of a process's name.
  - A refusal's *free for a load* is the gate's own figure, ceiling term included, never one the
    words work out.
  - `held_by_brake`'s warn line and release time come from the registry and the gate, not from
    the text.
- **2026-10-07** — Phase 2a's Task 6 review, the controller's rulings. Dated notes are in *What you
  see in Phase 2a*, in rule 4's *What it frees*, and in the implementation plan's Tasks 6, 15, 20 and
  22 and its *Deferred notes for implementers*.
  - `agent`'s `model_not_found` names the deployed CLI, `/opt/local-ai/app/.venv/bin/spark clients
    pi --write --registry /opt/local-ai/etc/models.yaml`. The entry above named the clone procedure
    from before 2a, and after the cutover `agent` has no clone.
  - In `agent`'s words, a step only Dan can take says it is Dan's. `agent`'s `no_fit` with no hold
    of Dan's reads *Only Dan can free memory for it, on the Spark; try again after that.*, since
    make-room's hold would bar `agent` anyway (rule 4). `held_by_brake`, `draining`, `gate_down`,
    `llama_swap_down`, `load_failed` and `not_downloaded` gain `agent`'s forms.
  - A refusal's breakdown is the term of rule 9 that gave the gate's figure, so it adds up where the
    CUDA ceiling binds or a model is starting. *Free for a load* says it can be less for those, and
    make-room starts from the gate's figure too.
  - A duration of a minute or more reads in minutes and seconds, so `load_failed`'s deadline reads
    *3 minutes*, not *180 s*. No refusal may make pi retry it: render refuses registry text pi's
    list matches.
- **2026-10-07** — Phase 2a's Task 6, fix round 2, the controller's rulings. Dated notes are in
  *What you see in Phase 2a*, in the implementation plan's Task 6 and its *Deferred notes for
  implementers*, and in S02 and S05.
  - `load_failed`'s next step, in the refusal and the notification, is `spark logs coder` (Task
    30's), since `spark status` shows no engine lines. `agent`'s reads *Dan can see why with
    `spark logs coder` on the Spark.*
  - `agent`'s `no_fit` with Dan's hold counted ends *The hold ends when Dan runs `spark make-room
    --done` on the Spark.*, and so does `agent`'s `draining` for make-room. `footprint_suspect` says
    *one of Dan's requests*, not *a request of his*.
  - A size of 400 GiB or more reads *more than 400 GiB*, and no number a message shows is altered.
  - Whichever task first loads the private key list at run time refuses a key label pi's retry list
    matches.

## Sources

Three research passes (serving components; clients, DGX OS and operations; models per slot) and a
four-reviewer council (goal-fit; a reliability red team that read the llama-swap v257, LiteLLM
1.102.1 and vLLM source; simplicity and security; toolstack health), all on 2026-09-23. Key
citations: llama-swap v257's config schema and group defaults · LiteLLM's security advisories and its
March 2026 incident report · NVIDIA's DGX Spark known issues (`MemAvailable`, per-process nvidia-smi)
· open-gpu-kernel-modules #1358 (freezes) · Tailscale #11717 (ufw bypass) · the whisper.cpp server
source · NeMo's word-boosting docs. Hardware facts carried over from the initial plan come from
[`cosmicbboy-local-ai.md`](../../cosmicbboy-local-ai.md). Phase 2a's architecture rests on a
reading of llama-swap's source at tag `v257` (commit `f00d375`) on 2026-10-05; *The front and the
gate* cites its files. Its council, on 2026-10-07, read llama-swap at `v262` too, llama.cpp at
`b11146`, uvicorn 0.54.0, Starlette 1.7.0, pi 0.85.1's published package, ntfy's docs at v2.28.0
and `systemd.service(5)` for systemd 255; the design cites what it took from each.

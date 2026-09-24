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
   the subscription — no performance loss, no added latency, no plumbing changes.
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
| **Claude is untouched** | No gateway, proxy, `ANTHROPIC_BASE_URL` or globally-installed MCP servers. Claude Code on the Spark talking to Anthropic is just a client. Pointing any Claude Code at a local model would be a separate, deliberate command (`claude-dgx`, parked). |
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
| Always loaded | Small vision chat + embeddings + interactive speech-to-text (~20–30 GB) — a setting Dan can change. |
| Idle unload | ~30 min by default; in-flight work counts as use; **an active agent session keeps its model**; a "stay loaded while I work" pin; an optional scheduled weekday preload; one-click load; load progress shown. |
| Memory conflicts | **Dan decides.** Before a big job, `spark make-room <size>` shows what would unload and unloads only what he confirms. The brake is the backstop: **idle models first**, whatever their class. Batch versus interactive: **Dan first**. |
| Visibility | A menu-bar status line (*"like Claude Code's… always see what model is being used"*) · ntfy on the Mac and an Android phone, including agent done / needs input / failed · `spark status` · the real model name on every reply. A web UI banner is in the backlog. |
| Web UI | Open WebUI, Dan only (others later). HTTPS via `tailscale serve`; **Tailscale stays on for the web UI, at home too**. HTTPS on the LAN is in the backlog. |
| Reach | **Tailscale is primary.** The home LAN serves homelab apps. **WireGuard** into the LAN covers a device logged into a different tailnet — pi and the API work then; the web UI waits. The Spark joins the tailnet. |
| Freeze while away | *"Tell me, I'll fix it at home"* → an off-Spark watchdog on the Synology. A GPU clock cap only if freezes unrelated to memory occur. Remote power via Home Assistant later. |
| Gateway | llama-swap's own keys in Phases 1–2; **LiteLLM, locked down, arrives in Phase 3 with Dan's audio pipeline — the first app that needs its own key** — with agreed swap triggers. |
| Models | Keep a mix: the best that fits, plus a policy-safe option (US/EU origin, permissive licence) per slot. Bake-off: speed + **3–5 real tasks via pi** + memory left free. New models: **`spark try` first**, promoted after the bake-off. **Starter coder: Qwen3.6-35B-A3B.** |
| Docs and findings | Findings go to the private vault (`zettelkasten/local-ai/`). **`website/` holds only the stack's documentation** (Quarto → GitHub Pages via Actions); Dan blogs on chendaniely.github.io. **Scenarios are living docs.** |
| Claude Code elsewhere | A user-level skill in github.com/chendaniely/skills points at the endpoint docs. |
| Ops | Headless box. Hybrid runtime (Compose + systemd) behind a `Makefile` and the `spark` CLI (Python via uv); tidy repo root. **Monthly upgrade day** from automated PRs; vLLM from NGC unless a model needs newer. Nightly backups to the Synology. |
| Build | **Split by machine, one session at a time:** the Mac session writes code, tests, docs and Mac clients; a Claude Code session on the Spark (as Dan, in tmux) builds and tests everything touching the GPU, memory, systemd or Docker; Dan runs sudo, logins, secrets and the Synology's settings. |
| Parked | Hermes · a MacBook MLX fallback (so there is one gateway) · `claude-dgx` · other users · the web UI banner. |

## Design

### Request flow

```
 Mac (pi, OpenCode, apps) · Spark tmux (agent) · Open WebUI (+SearXNG, via tailscale serve)
                      │  API key
                      ▼
      LiteLLM — Phase 3+: per-app keys, allow-lists, concurrency, usage (never content)
                      │                  ╲  hook: refusal text · x-spark-model · wait_for_fit
                      ▼                   ╲
      llama-swap (127.0.0.1, apiKeys)      ▶ spark-gate (Unix sockets): fit check · load lock ·
                      │ cmd = spark-launch ─▶  brake · idle policy · session pins · status · ntfy
                      ▼
  llama.cpp (chat, VLM, embed) · vLLM (NGC, only if a coder needs it) ·
  whisper.cpp ×2 (interactive, batch) · diarization (pyannote wrapper, diarized_json)
```

- **Phases 1–2:** Open WebUI and pi reach llama-swap directly with llama-swap keys — pi on the Mac
  through an SSH tunnel, so nothing listens on the LAN yet. A refused load is a plain error in the
  client; the explanation is in `spark status` (Phase 1) and on the menu bar and ntfy (Phase 2).
- **Phase 3 onward:** LiteLLM sits in front. Its hook makes refusals inline (`error.code`,
  `retry_after_s`), adds `x-spark-model: <name>@<revision>`, and applies per-key `wait_for_fit_s`.
- **Why not Ollama:** Dan has hit Hugging Face models that won't load there. Here each model runs on
  the engine that suits it, with the engine version pinnable per model, and start failures are
  explained.

### Components

| Component | Runs as / where | Key settings |
|---|---|---|
| **`stack/models.yaml`** (+ gitignored `models.local.yaml` for trials) | repo | real name, roles, capability, resident, engine + pin reference, source@revision, context, `parallel`, `cache_ram`, footprint {peak, steady, config hash}, cold start, idle policy, key access groups. |
| **`stack/versions.yaml`** | repo | every pin (image digest; tag + sha256) plus docs URL, context7 ID, changelog and advisory feed → generates the site's Stack page and the doc pointers in `CLAUDE.md`. |
| **`spark` CLI** | Python — a uv project | `render/apply/--check` · `status` · `load/unload/pin/make-room/stop-all` · `try/promote/forget` · `measure/bench` · `doctor` · `keys create` · `backup` · `logs`. The root `Makefile` is the front door. |
| **spark-gate** | Python/FastAPI, system unit `User=spark` | Unix sockets: status + session pins (group `spark-users`, includes `agent`); control (group `spark-admin` = Dan). Admission, brake, idle policy, resident preload (one at a time), events → ntfy, an `OnFailure=` notifier that works without the gate. Phase 1 ships only a **minimal brake** (a memory watchdog that unloads through llama-swap) plus a **minimal launch check** (the brake's hold flag and a static fit), so llama-swap can't reload a model the brake just unloaded; the gate absorbs both in Phase 2. |
| **llama-swap** v257 | system unit `User=spark`, 127.0.0.1 | canonical **`routing:`** config; **`swap: false, exclusive: false` on every group** (the defaults evict; render fails on ungrouped models); `apiKeys`; `captureBuffer: 0`; every `cmd` is `spark-launch <model>` (from Phase 2); no llama-swap preload; **never reloaded while models are loaded** (a v257 reload stops every engine — `spark apply` waits for idle or asks); validated with `-validate` and its schema. A separate lab instance serves `spark try`. |
| **llama.cpp** | a formal release tag; prebuilt arm64 CUDA 13 or a source build | `--load-mode none` or `dio` (reported: a 120B model loads in ≈22 s this way against ≈2 min through mmap); explicit `--cache-ram` (defaults to 8 GiB per server) and `--parallel`; MTP where supported. Verify `CMAKE_CUDA_ARCHITECTURES` `121` against NVIDIA's `121a-real`. |
| **vLLM** | NGC 26.08 container; upstream cu130 only if needed | explicit memory caps (the default claims ~110 GiB); fastsafetensors; persisted caches; `restart: no`; `--oom-score-adj=1000`. |
| **whisper.cpp** v1.9.4 ×2 | interactive (resident) + batch (on demand, Phase 3) | `--inference-path /v1/audio/transcriptions`; `prompt`; `verbose_json` word times; Whisper large-v3-turbo and Parakeet TDT v3 GGUF. Two instances, because each transcribes one file at a time. |
| **diarization** (Phase 3) | a small FastAPI wrapper around pyannote community-1 | OpenAI's shape (`response_format=diarized_json`); waveform input (no aarch64 torchcodec wheel); Hugging Face-gated weights (a runbook step). |
| **Open WebUI** | Compose, the standard `v0.11.4` image pinned by digest (the slim build now requires Postgres + pgvector), 127.0.0.1:3000 → `tailscale serve` | SQLite with its embedded vector store; `ENABLE_PERSISTENT_CONFIG=False`; Direct Connections and code execution off; signup off; task model = the resident small model; embeddings and speech-to-text → the Spark's endpoints; web search → SearXNG. |
| **SearXNG** | Compose, pinned, 127.0.0.1 | Open WebUI's web search. |
| **LiteLLM** (Phase 3) | Compose; Docker image pinned by digest, checked with `cosign verify` | admin UI, MCP, JWT and guardrails off; `NO_DOCS`; `turn_off_message_logging`, `disable_error_logs`; no fallbacks, `num_retries: 0`, cooldowns off; readiness health only (`/health` would load every model); keys by access groups generated from the registry; per-key `max_parallel_requests` (batch keys low); a dependency-free hook that checks every call carrying a `model`; Postgres healthy first; Postgres down → fail closed + alert. **Swap triggers:** another critical auth bug · a needed feature moves to Enterprise · the hook breaks on upgrade. |
| **ntfy + watchdog** (Phase 2) | the Synology (Compose in `stack/synology/`) | deny-all + tokens; priorities + quiet hours; the watchdog pings the Spark and its health endpoints. |
| **Host** | `stack/host/` | earlyoom (`-s 100,100`, `--prefer` engine process names — note the 15-character truncation, e.g. `VLLM::EngineCor` — and `--avoid` systemd/sshd/tmux); `spark-drop-caches` (root-owned, exact-arguments sudo, local filesystems only, with a deadline); apt holds on the driver and CUDA; ufw SSH only (+ LiteLLM from Phase 3); one 0600 secret file per service. |
| **Mac and agent clients** | `clients/` | SwiftBar plugin (`ssh brightroar spark status --json`; actions over SSH as Dan); pi and OpenCode configs rendered from the registry (real model names, pinned versions — pi outside its llama-server crash range, OpenCode 1.18.x — compat flags, `$VAR` keys); harness hooks (session pins + ntfy) for Claude Code, pi and OpenCode on the Mac and as `agent`. |

### Admission and memory rules

1. **Admission happens where engines start.** `spark-launch` asks the gate; a model fits when
   `footprint.peak ≤ available − reserve − pending`, where *available* is `MemAvailable` capped by
   the CUDA-allocatable ceiling (reported near 102 GiB; to be measured). One load at a time; caches
   are dropped (local filesystems, with a deadline) before loading.
2. **Never evict, never substitute.** The only automatic unloads are the idle policy and the brake.
3. **Idle policy:** 30 minutes by default; in-flight requests and active agent sessions count as use;
   pins (manual, work hours); scheduled preloads are fit-checked and notify if they don't fit.
4. **make-room** lists candidate unloads with their sizes and unloads only what Dan confirms.
5. **Brake:** polls every 250 ms and on the rate of fall. Order: a loading engine, then idle models of
   any class, then the least recently used. Each step notifies, and "held by brake" blocks automatic
   reloads. Starting thresholds — tunable, set above the band where freezes have been reported: warn
   at 28 GiB available, brake at 20 GiB, admission keeps ≥24 GiB free, earlyoom at 12/9 GiB. Tuned
   from measurements.
6. **Footprints** are the larger of the load peak (sampled 10×/s) and the steady state after a soak at
   maximum context, keyed to a hash of engine + arguments + model revision. Unmeasured models use a
   gguf-parser estimate with a margin and are flagged.
7. **Structured refusals:** `error.code` is one of `no_fit | loading | gate_down | not_downloaded |
   held_by_brake`; the text gives memory needed against available, the top holders (nvidia-smi's
   per-process list plus names) and the options; plus `retry_after_s`. A request for a model that is
   already loading waits for it.
8. **Gate down ≠ API down:** models llama-swap reports ready keep serving; only new loads are refused.

### Users, access and security

- **Three identities.** `dan` — admin, Positron, the control socket. `spark` — the service user that
  runs the gate and llama-swap (so every model engine), and owns the models, the Hugging Face cache
  and state. It is deliberately **not** in the `docker` group, because Docker access is
  root-equivalent; containers start from root-owned units instead. Dan's own account is effectively
  root-capable (sudo, and `spark-admin` can change what the `local-ai-*` units run), so the
  isolation boundary on this box is between Dan and `agent`. `agent` — tmux agents: the status and session socket only; no sudo, no docker; 0700 homes;
  writes its repos and the NAS work folders; its own key; no GitHub credentials.
- **Secrets.** Never `EnvironmentFile=~/.secrets` — systemd ignores `export` lines and has been
  reported logging them with their values. Dan writes one 0600 `KEY=value` file per service, outside
  any agent session; the gate token goes in through `LoadCredential=`, Postgres through
  `POSTGRES_PASSWORD_FILE`; client keys live in `~/.secrets` on each client. Agents only test that a
  variable is present. A Claude Code hook on the Spark blocks commands that print values
  (`docker inspect`, `docker compose config`, `/proc/*/environ`, `systemctl show-environment`).
- **Network.** Tailscale ACL grants are the tailnet's firewall, because ufw does not filter
  `tailscale0` (Tailscale issue #11717). Everything binds 127.0.0.1 except SSH (and LiteLLM from
  Phase 3); Open WebUI is reachable only through `tailscale serve`. The ACL policy lives in the vault.
  Client base URLs and the SSH alias use the Spark's LAN address from a private values file, which
  works at home, through the tailnet's route home and over WireGuard — Phase 0 confirms that route.
- **NAS.** The Synology's NFS exports offer only the data and recordings shares (read-only) and named
  work folders (read-write), to the wired address only, with root squashed. Immutable snapshots
  (DSM 7.2+, Btrfs) protect the work and backup shares. systemd automounts with timeouts.
- **Supply chain.** Digests live in `versions.yaml`. LiteLLM comes only from its signed Docker image
  (its PyPI releases 1.82.7 and 1.82.8 were backdoored on 2026-03-24; the images were not affected
  and have been signed since 1.83.0). GitHub Actions are pinned by commit SHA with minimal
  `permissions:`. Driver and CUDA packages are held and upgraded deliberately.
- **Leak guards.** `.githooks` pre-commit and commit-msg hooks run gitleaks plus patterns for private
  LAN and tailnet addresses, MACs and `ts.net`, plus a private denylist kept outside the repo (the
  hook fails if it's missing). Configs render outside the repo; fixtures use RFC 5737 addresses and
  `example.invalid`; no pasted terminal output; Quarto `_freeze`, screenshots and Actions logs get
  checked too.

### Visibility and notifications

- **One status source:** `spark status --json` — loaded models (real names), pins and sessions,
  **headroom before the brake**, top memory holders, pending loads, brake state, health.
- **Menu bar (SwiftBar)** polls it over SSH. The title shows the active models and the headroom; the
  dropdown offers load, unload, pin, make-room and stop-all (over SSH as Dan), lists running agent
  sessions, and says "unreachable" when it is.
- **ntfy (on the Synology):** low — loaded, unloaded, idle; default — refused, load failed, agent
  done / needs input / failed; high — brake, gate or Spark down, backup failed. Quiet hours apply to
  low and default.
- **Harness hooks** register agent sessions with the gate and post their outcomes to ntfy.
- **Real model on every reply:** clients use real model names until Phase 3; from then on the
  `x-spark-model` header carries it.

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

### Repo layout

```
stack/      models.yaml · versions.yaml · compose.yaml · litellm/ · templates/ · systemd/ · host/ · synology/
spark/      pyproject.toml · uv.lock · src/spark/ (cli, gate, launch, registry, render, memory, brake, notify, doctor, bench) · tests/
clients/    pi/ · opencode/ · claude-code/ (hooks) · swiftbar/
website/    Quarto docs site (_quarto.yml, scenarios/, how-to/, reference/, design/)
.githooks/  .github/workflows/
```

The root keeps `README.md`, `CLAUDE.md`, `LICENSE`, `changelog.md` and `cosmicbboy-local-ai.md`,
plus a `Makefile` — the front door.

### Deploy workflow (Mac ⇄ GitHub ⇄ Spark)

- **Edit on either machine** (as Dan, never as `agent`). Pushes happen only with Dan's explicit OK.
  GitHub credentials exist only in Dan's accounts (a 0700 home on the Spark). Bootstrap installs the
  leak-guard hooks in each clone; the private denylist exists on both machines.
- **First time on the Spark:** `git clone` (a public repo — no credentials needed) → Dan creates the
  private files (per-service secrets; private values such as the NAS and LAN addresses) →
  `make bootstrap` (sudo once: users, firewall, earlyoom, systemd units, Compose).
- **After any change:** `make apply` on the Spark, or `make deploy` from the Mac (SSH, then
  `git pull && make apply`). `spark apply` renders (registry + pins + private values → concrete
  configs in a deploy directory outside the repo), validates with each tool's own checker, shows the
  diff, restarts only what changed (llama-swap waits for idle models or asks), then runs a quick
  `spark doctor`. It warns about uncommitted changes.
- **Hybrid runtime:** Compose for Open WebUI and SearXNG (later LiteLLM and Postgres); systemd for
  llama-swap and the gate; engines are pinned binaries or on-demand containers; host setup happens in
  bootstrap; ntfy and the watchdog run under Compose on the Synology; the Mac pieces install with
  `make clients`.
- **The Makefile is the front door.** `make help` lists `bootstrap, apply, deploy, status,
  logs s=<name>, doctor, test, docs, clients`; the logic lives in the Python CLI. It stays portable to
  macOS's older GNU make.
- **Python via uv everywhere** (uv is installed on both machines — on the Spark as a per-user install
  in `~/.local/bin`). `spark/` is a uv project (`pyproject.toml` + `uv.lock`; uv's `required-version`
  pinned in `versions.yaml`); the Makefile calls `uv run --frozen spark …`; standalone helper scripts
  carry PEP 723 inline metadata and run with `uv run`. No system Python, no pip. `spark apply` builds
  the gate's environment with `uv sync --frozen` somewhere the `spark` user can read; bootstrap checks
  where uv and its managed Pythons live.

### Where work runs

- **Mac session:** repo code (CLI, gate, launcher) with unit tests, config templates and render tests,
  the Makefile, leak hooks, CI, the docs site, and the Mac clients (SwiftBar, pi and OpenCode configs,
  harness hooks).
- **Spark session** (Claude Code as Dan, in tmux on `brightroar`): anything touching the GPU, memory,
  systemd or Docker — engine builds, llama-swap, the gate, the brake, `spark doctor`, measurements and
  comparisons, bootstrap dry-runs.
- **Dan:** sudo (`make bootstrap`), interactive logins (Tailscale, GitHub on the Spark, Claude Code
  for `agent`, the Hugging Face token), secret values, the Synology's settings, and approving every
  push.
- **Handoff, one session at a time:** the active session owns the phase branch; at a switch it
  commits, the branch is pushed with Dan's OK, and the other machine pulls. Both sessions read this
  plan and the implementation plans from `website/design/`; every task is labelled **[Mac]**,
  **[Spark]** or **[Dan]**. From the Mac, read-only checks on the Spark over SSH happen only with
  Dan's OK.

### Testing

- **Unit tests (TDD):** admission decisions, brake ordering, idle policy, refusal text — pure
  functions with fixtures. They run anywhere: the Mac, the Spark, CI.
- **Render tests:** golden files plus each tool's own validator; CI runs `spark render --check`.
- **`spark doctor`** runs only on the Spark, after any change: one check per scenario, plus — a second
  load evicts nothing; refusals through real clients on chat, streaming, `/v1/responses`, embeddings
  and transcription; direct llama-swap calls need a key; allow-lists hold; idle unload fires; the
  brake fires at raised thresholds; a **privacy canary** (a unique string sent through chat and
  speech-to-text must never appear in any store); a 170 MB upload; `words[]` present; a bypass sweep
  (every load path shows up in the launcher's log); a reload during generation; a 2-hour soak;
  earlyoom `--dryrun`; five reboots (one with the NAS unplugged); a NAS outage during a load; Postgres
  stopped and a full disk; a cold first load through each client; a snapshot restore under an agent;
  a clean-host restore. It refuses to run against an untested llama-swap version. S13 and S18 are
  dated manual drills.

### Backups, recovery and upgrades

- **Nightly:** Open WebUI data, gate state and `agent` repos (and the Postgres dump from Phase 3) go
  to the Synology through DSM's rsync service with a non-admin account (Synology SSH is admin-only);
  14 copies are kept; a failure sends a high-priority ntfy.
- **Recovery runbook:** factory reset → the Phase 0 runbooks → delete the old Tailscale node before
  rejoining (otherwise the box comes back as `brightroar-1` and every client breaks) → restore →
  `spark doctor`.
- **Monthly upgrade day:** automated PRs collect version bumps; they are applied one component at a
  time → render → validate → back up databases → deploy → `spark doctor` → changelog entry.

## Phases

Every phase ends by updating scenario statuses, the docs site, `changelog.md` and `README.md`
§Current state. Tasks are labelled by where they run.

**Phase 0 — Guardrails, prep, docs scaffold**

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

**Phase 1 — First milestone: web UI + pi**

- [Spark] pinned llama.cpp · llama-swap from a minimal registry (`routing:`, `apiKeys`, 127.0.0.1,
  `captureBuffer: 0`) with a static, safe model set — Gemma 4 26B-A4B (resident vision chat),
  Qwen3-Embedding-0.6B, whisper.cpp large-v3-turbo (interactive), and the starter coder
  Qwen3.6-35B-A3B — whose combined footprint `spark render` checks · the **minimal brake** · Open WebUI
  + SearXNG via `tailscale serve` · pi and Claude Code in tmux as `agent` · a basic `spark status`.
- [Mac] pi config + SSH tunnel.
- *Done when:* S09 and S20 work on the phone; pi completes a task from the Mac and from tmux;
  reattaching works; the minimal brake fires at raised thresholds; a fresh clone + `make bootstrap` +
  `make apply` reproduces it.

**Phase 2 — Fit check, brake, visibility**

- [Spark] the gate + `spark-launch` + sockets (absorbing the minimal brake) · residents move under the
  gate (preloaded one at a time) · idle policy, pins, sessions, scheduled preload · make-room ·
  `spark try` with the lab instance · `spark doctor` v1.
- [Mac] SwiftBar plugin · harness hooks (on the Mac and for `agent`).
- [Dan] ntfy + watchdog in Container Manager on the Synology.
- *Done when:* S01, S02, S03, S05, S06, S11, S12, S13, S14 and S17 are verified.

**Phase 3 — App API + speech** (Dan's audio pipeline is the first app with its own key)

- [Spark] LiteLLM, locked down, + Postgres + hook (inline refusals, `x-spark-model`,
  `wait_for_fit_s`) · keys by access group, per-key concurrency (batch keys low) · LiteLLM's LAN port
  (ufw + ACL) · batch whisper.cpp · the diarization endpoint · the **speech comparison** on Dan's
  51.6-minute lecture against the Mac baseline: Whisper large-v3-turbo vs Parakeet TDT v3 GGUF vs
  NeMo Parakeet with boosting, plus non-English, language-switching and two-speaker samples; pyannote
  community-1.
- [Mac] the endpoint reference page (chat + speech).
- [Dan] `spark keys create` for the audio pipeline, whose own changes happen in its repo.
- *Done when:* S04, S07, S08, S10, S16 and S22 are verified and the privacy canary passes.

**Phase 4 — NAS and backups**

- [Dan] Synology exports (read-only data and recordings, read-write work folders, root squash, the
  wired address) + immutable snapshots + the non-admin rsync account.
- [Spark] systemd automounts · nightly backups · one restore test.
- *Done when:* S15 and S18 are verified.

**Phase 5 — Model bake-off, clients, Claude Code docs**

- [Spark] coders: Qwen3.8-27B (GGUF + MTP; vLLM FP8/NVFP4 only if needed), Laguna-S-2.1, gpt-oss-120b
  (policy-safe); lighter: Qwen3.6-35B-A3B, gpt-oss-20b (policy-safe). Resident vision model: Gemma 4
  26B-A4B (policy-safe) vs Qwen3.6-35B-A3B. Embeddings: Qwen3-Embedding-0.6B vs Granite embedding r2
  (policy-safe); TEI only if llama.cpp falls short. Metrics: footprint (peak, steady), cold start,
  TTFT, prefill at 4K/32K/64K, decode at 1/2/4/8 streams, tool-call reliability, Dan's 3–5 real tasks
  via pi, memory left free.
- [Mac] client configs rendered for the picks (pi pinned outside its crash range, OpenCode 1.18.x) ·
  the `spark-endpoints` skill for chendaniely/skills (Dan pushes).
- *Done when:* the registry has a primary and a policy-safe pick per slot; S19 is verified; findings
  are in the vault; the docs are updated.

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
  first) · HTTPS on the LAN (which would also bring the web UI to WireGuard) · the web UI banner (if
  Open WebUI gains a status API) · automatic restarts from the watchdog · Home Assistant with remote
  power (check the UEFI "restore on AC power loss" setting) · a GPU clock cap (only if needed) · a pi
  footer extension · suggest a pre-start admission hook upstream (llama-swap #1127).
- **Parked:** Hermes · `claude-dgx` · a MacBook MLX fallback · other users.

## Open items and risks

- **GB10 hard freezes**, memory-related and not → conservative thresholds, the minimal brake from
  Phase 1, the off-box watchdog, a clock cap if needed, Home Assistant power later; incidents are
  logged in the vault.
- **llama-swap** has one main maintainer and fast config churn → pin, validate, adopt no optional
  features; the gate keeps the boundary thin enough to swap it out.
- **LiteLLM's 2026 security record** → it arrives in Phase 3 locked down, with swap triggers; the gate
  could take over keys (~600 lines) if a trigger fires.
- **Open WebUI churn** → a pinned minor version, env-only config, a database dump before upgrades.
- **vLLM** start can abort when free memory rises during profiling (#56830), and NGC lags upstream →
  llama.cpp first.
- **Two machines, one branch** → one session at a time; handoff by push and pull with Dan's OK.
- **To verify on the box:** `121` vs `121a-real`; `agent`'s CUDA access; Parakeet quality on
  whisper.cpp; NeMo boosting and pyannote on aarch64; that Open WebUI's embedding and speech-to-text
  base URLs are set explicitly (unset, they fall back to OpenAI's); pi's crash range; the tailnet's route home; the
  UEFI AC-restore setting; Btrfs for immutable snapshots; the CUDA-allocatable ceiling.
- **Accepted gaps:** homelab apps reach the Spark only from Phase 3 (nothing listens on the LAN until
  per-app keys exist); Open WebUI chat history isn't backed up until Phase 4; the web UI is out of
  reach over WireGuard.

## Revisions

- **2026-09-23** — Written from the requirements interview; replaces `planning.md`.
- **2026-09-23** — While planning Phases 0–1: the `spark` user stays out of the `docker` group
  (containers start from root-owned units; the Dan ↔ `agent` boundary is the one that matters);
  Open WebUI uses the standard image, since the slim build now requires Postgres + pgvector; Phase 1
  adds a minimal launch check beside the minimal brake; the brake's hold folder is writable by
  `spark-admin` only, so Dan can release a hold and `agent` can't.

## Sources

Three research passes (serving components; clients, DGX OS and operations; models per slot) and a
four-reviewer council (goal-fit; a reliability red team that read the llama-swap v257, LiteLLM
1.102.1 and vLLM source; simplicity and security; toolstack health), all on 2026-09-23. Key
citations: llama-swap v257's config schema and group defaults · LiteLLM's security advisories and its
March 2026 incident report · NVIDIA's DGX Spark known issues (`MemAvailable`, per-process nvidia-smi)
· open-gpu-kernel-modules #1358 (freezes) · Tailscale #11717 (ufw bypass) · the whisper.cpp server
source · NeMo's word-boosting docs. Hardware facts carried over from the initial plan come from
[`cosmicbboy-local-ai.md`](../../cosmicbboy-local-ai.md).

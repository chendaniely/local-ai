---
title: "Phase 2a — implementation plan"
description: "The gate: a front on 127.0.0.1:9100 and a gate on two Unix sockets in front of llama-swap, admission with refusals in Dan's words, make-room with its hold, the brake's release, ntfy on the phone, and Qwen3.8-27B as the coder."
date: 2026-10-07
---

# Phase 2a — The gate — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (Dan's
> choice) to implement this plan task by task. Steps use checkbox (`- [ ]`) syntax. Every task is
> labelled **[Spark]**, **[Mac]** or **[Dan]**; a step that Dan runs, or that runs elsewhere, names
> it in bold (**[Dan, on the Spark]**, **[Dan, on the Mac]**, **[Dan, as `agent`]**, **[Dan, on the
> phone]**). ⇄ marks a push or a machine switch (one session at a time). Phase 1 is done and merged;
> this plan's branch is `phase-2a`, cut from `main` at `a7b14ca`, and holds the approved design
> (`2e8b3f5`).

> **How this plan differs from Phase 1's.** It carries contracts, not code (the controller's
> ruling, 2026-10-07): each code task gives its files, its interfaces (names, signatures, types,
> formats), every test by name with what it asserts, and its steps. The implementer writes the
> code test first, and each task gets its reviews as in Phase 1. Phase 1's complete listings meant
> writing the code twice; `CLAUDE.md`'s rule that a plan holds no untested code still holds, since
> this one holds none. Every command block in it was run before it went in, on stand-ins wherever
> the real thing would change the box: [*How the command blocks were checked*](#how-the-command-blocks-were-checked),
> at the foot, says which and how.

> **Progress (2026-10-07).** The design is approved (Dan, 2026-10-07), and this plan is written.
> No task has started. **Next: Task 1.**

**Goal:** Requests reach a new front on 127.0.0.1:9100, which checks the same client keys (held
only as digests), counts the requests in flight and forwards to llama-swap on 127.0.0.1:900 with an
internal key. For a model that isn't loaded it asks a gate on two Unix sockets, which admits one
load at a time on rule 9's formula, Dan's keys first, holds the request for its key's wait, and
otherwise returns a refusal in Dan's words. The gate idle-unloads at 60 minutes, pins, keeps
`agent`'s sessions, runs make-room with its hold, lifts the brake's hold within bounds and reloads
the residents, and sends every notification to Dan's phone through ntfy on the Synology. The brake
stays its own unit, unloading idle models first and acting on the rate of fall. `make apply` waits
for a quiet minute and restarts llama-swap under loaded models. Qwen3.8-27B becomes the coder, and
the soak, the CUDA ceiling and the brake's timings are measured.

**Architecture:** Four services and a notifier, each a system unit that root runs from its own
copy. `local-ai-front.service` (`spark-front`) is about 200 lines of raw ASGI on uvicorn, on
127.0.0.1:9100, held by `local-ai-front.socket`. `local-ai-gate.service` (`spark`) is a Starlette
app on uvicorn, on `local-ai-gate-status.socket` (group `spark-users`) and
`local-ai-gate-control.socket` (group `spark-admin`), each caller known by its uid through a
uvicorn protocol subclass that reads `SO_PEERCRED`. `local-ai-brake.service` stays a synchronous
loop of its own. llama-swap v257 moves to 127.0.0.1:900 and its engines to 800 and up, with
`CAP_NET_BIND_SERVICE`, internal keys only and a sandbox; every engine still starts through
`spark launch`, which now needs the gate's admission ticket. `local-ai-notify@.service` runs
Ubuntu's `/usr/bin/curl` when any of the four fails. The registry (`stack/models.yaml`) gains each
model's label, the client keys and their groups, the notification list and the gate's settings;
`spark render` writes every unit from it, and `make install-units` installs root's copies. The CLI
reaches the gate with the standard library only.

**Tech Stack:** Python 3.12 via uv · PyYAML · huggingface_hub · **uvicorn 0.54.0** (pinned
exactly, no `[standard]` extras) · **Starlette 1.x** · **httpx 0.28** (already locked) · anyio's
pytest plugin · the standard library's `http.client`, `socket` and `ctypes` · pytest · llama-swap
v257 · llama.cpp b11146 · whisper.cpp v1.9.4 · Open WebUI v0.11.4 · SearXNG · ntfy v2.28.0 (on the
Synology) · systemd 255 (socket activation, `LoadCredential=`, `OnFailure=`, `Type=notify`
watchdogs) · polkit · pi 0.85.1 (`agent`'s).

**Spec:** [`website/design/plan.md`](plan.md) — Phase 2a; *The front and the gate (Phase 2a)*;
*Admission and memory rules* 1–9; *Components* (the front, spark-gate, llama-swap, Host, ntfy +
watchdog, Open WebUI, SearXNG, Mac and agent clients); *Users, access and security*; *Visibility
and notifications* with *What you see in Phase 2a*; *Deploy workflow*; *Testing*; *Open items and
risks*. The design's five commits are `git diff a7b14ca..2e8b3f5`.

**Why the design is what it is:** [Phase 2a — questions and answers](phase-2a-qa.md), the
brainstorm's scenario questions and the council's decisions, as questions and answers.

## Global Constraints

- Everything in Phase 0's and Phase 1's Global Constraints still applies (public repo, private
  context, secrets by reference, uv only, make 3.81, commits with 🤖 and the trailer, a checkpoint
  commit per task on branch `phase-2a`, push only with Dan's explicit OK, the docs-must-be-true sync
  rule, a key never on a command line, root never writes through a path `spark` or `agent`
  controls), except where a line below changes one of Phase 1's values (ports, budget, units,
  keys).
- **Trailer, on every commit this plan makes:**
  `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- **The front:** "`local-ai-front.service`, new, a system unit running as **`spark-front`**, a
  system user of its own … It serves **127.0.0.1:9100** with the **same client keys** … but holds
  only their **SHA-256 digests**, compared in constant time."
- **llama-swap and its engines:** "llama-swap v257 moves to **127.0.0.1:900**, and the engines from
  5800 to **800 and up**, below 1024 … Only llama-swap's unit gets `CAP_NET_BIND_SERVICE`, through
  `AmbientCapabilities=` and `CapabilityBoundingSet=`."
- **Unchanged ports:** Open WebUI `127.0.0.1:3000` · SearXNG `127.0.0.1:8888` · `tailscale serve`
  maps HTTPS 443 → 127.0.0.1:3000.
- **Sockets from systemd:** "`local-ai-front.socket` holds 127.0.0.1:9100, and
  `local-ai-gate-status.socket` and `local-ai-gate-control.socket` hold the gate's two, each with
  its `SocketUser=`, `SocketGroup=` and `SocketMode=0660`." The status socket's group is
  `spark-users` and the control socket's `spark-admin`; their paths are
  `/run/local-ai/gate-status.sock` and `/run/local-ai/gate-control.sock` (this plan's).
- **Taking the sockets:** "Each service takes its sockets from `LISTEN_FDS` as
  `socket.socket(fileno=…)` and hands them to uvicorn's `Server.run(sockets=…)`; never `--uds` …
  or `--fd`."
- **Never giving up:** "the three socket units set `TriggerLimitIntervalSec=0`, and the front, the
  gate and the brake set `StartLimitIntervalSec=0` with `RestartSec=2`."
- **The units:** "The front and the gate run `Restart=always` and `OOMScoreAdjust=-900`, like the
  brake, as `spark front` and `spark gate`, so their process name stays `spark`." "A hang becomes a
  failure through `Type=notify` and `WatchdogSec=` on the front and the gate." "The front has only
  `Wants=` and `After=` on llama-swap, never `Requires=`, `BindsTo=` or `PartOf=`." "The front gets
  `IPAddressDeny=any` with `IPAddressAllow=localhost`." The gate gets neither `PrivateDevices=` nor
  `ProtectProc=invisible`. "uvicorn's graceful shutdown is bounded below each unit's
  `TimeoutStopSec=`."
- **llama-swap's unit:** `Restart=always` (`RestartSec=5`); "`NoNewPrivileges=`,
  `ProtectSystem=strict` with `ReadWritePaths=` for whisper's `--tmp-dir` and the caches,
  `ProtectHome=`, `PrivateTmp=`, `InaccessiblePaths=/etc/local-ai/secrets`, `RestrictSUIDSGID=`,
  `ProtectKernelTunables=` and `ProtectControlGroups=`, but not `PrivateDevices=` or
  `MemoryDenyWriteExecute=`"; the tickets' folder is the only state its `ReadWritePaths=` opens,
  and "the brake's hold folder is read-only there".
- **llama-swap's config:** v257 stays (no upgrade in 2a); "`healthCheckTimeout` goes from 600 s to
  **180 s**"; "`ttl: 0` stays, so the gate is the only thing that unloads"; `logToStdout: proxy`
  stays (the journal question is 2b's); every `cmd` is `spark launch <model> -- …`.
- **The stack:** "**uvicorn** serves both. The front is about 200 lines of raw ASGI with no
  framework …; the gate is a small **Starlette** app. Both talk to llama-swap with **httpx**. That
  adds two packages to `spark/uv.lock`, uvicorn and Starlette." "httpx runs with
  `trust_env=False`, follows no redirect, and sets no read timeout on a forwarded request." "The CLI
  talks to the gate with the standard library (`http.client` over a Unix socket), so
  `spark status --json` … and `spark launch` never import uvicorn or httpx. It stays one uv project
  and one lock, deployed to `/opt/local-ai/app`." uvicorn is pinned, "and a test, run again at each
  bump, checks that the caller's uid arrives".
- **The gate's load call:** it "waits at least `healthCheckTimeout` plus the 5 s llama-swap takes to
  kill a stuck start, plus a margin … on any timeout or error it keeps the load counted as starting
  until `/running` shows it ready or gone". It loads through `GET /upstream/<model>/health` "after
  issuing that load an **admission ticket**", and unloads with `POST /api/models/unload/<model>`,
  "only once the front has drained it". "A ticket expires at its load's deadline."
- **Users:** Dan's account `chendaniely` (`spark-admin`, `spark-users`, `adm`) · `spark` (the gate,
  the brake, llama-swap and every engine; reads the Hugging Face cache through its group) ·
  `spark-front` (the front; a member of `spark-users`) · `spark-pull` (the model pull; owns the
  Hugging Face cache, "with a setgid group so that `spark` reads the model files") · `agent` (the
  status socket only) · the notifier, `DynamicUser=yes`. "The polkit rule's list grows to six: the
  front and the gate join the four. The socket units and the failure notifier aren't on it."
- **The keys:** client keys reach the front only as digests "through `LoadCredential=`, and
  `llama-swap.env` drops the keys"; internal keys, "one per caller of llama-swap, the front, the
  gate and the brake: each reaches its caller through `LoadCredential=` … and llama-swap reads all
  three from its `EnvironmentFile=` … Their names carry the `LLAMASWAP_KEY_` prefix";
  "`LLAMASWAP_KEY_SPARK` retires"; "**Dan's commands need no key**", but `make doctor`'s end-to-end
  checks through the front "still use Dan's client key, `SPARK_API_KEY` on the Spark".
- **The secret files** "become `0600 root:root`, in a folder only root reads". Phase 1's
  `llama-swap.env`, with the client keys and `LLAMASWAP_KEY_SPARK`, stays `0600 root:root` for the
  rollback until the close, Task 49 (the controller's ruling, 2026-10-07).
- **The private values file** (the controller's ruling, 2026-10-07): `/etc/local-ai/values.env`,
  never in the repo, `KEY=value` lines that systemd's `EnvironmentFile=` reads: `NTFY_URL`,
  `NTFY_TOPIC_GATE`, `NTFY_TOPIC_NOTIFY`, `NTFY_TOPIC_BRAKE`. The topic names are private too.
- **ntfy:** "pinned at **v2.28.0** by its index digest,
  `sha256:6ef4b819f722fccdc036af611c4774cfdc2de821ab74fdd48bbf4c9d6f8973da`"; "the server sets no
  `upstream-base-url` and no Firebase key"; "each publisher has a publish-only token of its own" (the
  gate, the failure notifier, the brake), "and only to its own topic"; "reached over the tailnet or
  the home LAN only, with no public relay"; "**Quiet hours run 00:00–05:00**", set on the phone:
  "Android's Do Not Disturb … and the ntfy app's high and max priority channels are allowed through
  it"; alerts reach "**The phone only, in 2a**".
- **ntfy's deploy** (Dan, 2026-10-07): a Docker Compose file, `stack/synology/ntfy/compose.yaml`,
  that deploys with any Compose helper (Portainer's stacks today, from the web editor or this repo
  as a Git-repository stack), with plain `docker compose up -d`, or with another helper; nothing
  depends on Portainer. It holds no secret and no private value: the NAS's address, the topic
  names and the tokens arrive as variables the file names. Phase 2b's watchdog follows the same
  pattern.
- **The failure notifier:** "`local-ai-notify@.service`, a template unit that `OnFailure=` starts
  for the front, the gate, the brake and llama-swap … runs Ubuntu's `/usr/bin/curl`, outside the
  app's venv, with `--max-time` and `--fail`, as a user of its own (`DynamicUser=yes`) … token …
  through `LoadCredential=` as a header file, which curl reads with `-H @file` … It sends the unit,
  its result and the time, never a journal line, and at most one alert per unit in a set interval."
- **The front's routes:** "only `GET /v1/models` and `POST` to `/v1/chat/completions`,
  `/v1/completions`, `/v1/responses`, `/v1/messages`, `/v1/embeddings` and
  `/v1/audio/transcriptions`, and answers its own `GET /health`. Every other path gets a 404 that
  says why."
- **The front's limits:** "`MemoryMax=` on its unit … bodies up to about 200 MB … uploads spooled to
  disk under its `PrivateTmp=`, not tmpfs, never held in memory, and deleted when the request ends;
  per-key caps on waiting requests and open connections, low for `agent`'s and below llama-swap's
  own limit of 10 per model; a timeout for reading headers; a 400 for a request it can't parse,
  never a crash." "one with both `Content-Length` and `Transfer-Encoding` is refused,
  `Authorization`, `x-api-key`, `Cookie` and `Proxy-Authorization` are stripped … no upstream
  connection is shared between keys"; "a JSON body goes on re-serialized … a form with two `model`
  fields is refused".
- **The front's journal lines** "name the key, the model, the status and the duration, never a key,
  a digest, a body, a header or an upload's file name."
- **The key's wait:** "**30 s for Dan's keys** (pi on the Mac, the web UI) and **10 minutes for
  `agent`'s** … It runs from when the front receives the request … A load, once started, is always
  waited for, bounded by its deadline." `agent`'s pi: "`spark clients` writes **about 15 minutes,
  900,000,** into `agent`'s pi settings".
- **Gate down:** the front "counts the gate as down once its long-lived call to the status socket has
  dropped and a new one hasn't been answered within 5 s, and only then refuses new loads with
  `gate_down`." "At boot, or after a restart that stopped every engine, a gate that is down means
  nothing loads."
- **Draining:** "A drain the gate doesn't finish within 30 s goes back to serving, and so does every
  drain if the front's call to the gate drops"; "the 30 s run from the front's 'drained'".
- **Idle:** "an on-demand model unloads after **60 minutes** with no request and no active session,
  and resident models never unload for being idle." Pins are `spark pin <model> [duration]`, Dan's
  only, on the control socket. `spark` and `spark-front` hold no sessions (the controller's ruling,
  2026-10-07: every engine runs as `spark`).
- **The budget:** reserve "**≥24 GiB**" · warn 28 GiB · brake 20 GiB · earlyoom 12/9 GiB · poll
  250 ms · the CUDA-allocatable ceiling "reported near 102 GiB; to be measured" · idle
  `MemAvailable` 117 GiB · "`footprint ≤ min(MemAvailable − reserve − owed, ceiling − committed) −
  starting − held`" · "the growth read is the engine's anonymous RSS, `RssAnon`" · the set "~84 of
  the 102".
- **The brake's release:** "once memory has stayed above the warn line for 5 minutes … and only if
  the models it would reload fit"; "**At most one automatic release an hour.**"; "**A hold found
  after a reboot waits for Dan**"; "**The model that was loading when the brake fired doesn't
  reload by itself**". The brake's order: "a loading engine, then idle models of any class, then
  the least recently used", from the in-flight record the gate writes "every second", falling back
  to Phase 1's order when it is missing or stale. `make brake-release` goes through the gate, and
  falls back to Phase 1's direct release of the hold file when the gate doesn't answer (the
  controller's ruling, 2026-10-07).
- **`make apply`:** "waits until no request has been in flight for ~60 s on any engine, by the
  front's counts"; "**The wait has a deadline:** up to 15 minutes … then apply offers 'drain now' …
  and then `make apply-now`"; "**Nothing is written until the drain is done**"; the front restarts
  "only when its own modules change", or the registry's model names and roles, or the key digests.
- **The coder:** "`unsloth/Qwen3.8-27B-GGUF`, at revision
  `4ca720788d1e01f1bff70c033e0d0028fd02e502` … the file `Qwen3.8-27B-UD-Q4_K_XL.gguf` (17.6 GB)";
  "`--spec-type draft-mtp`"; a native context of 262,144 with an f16 KV cache; "estimated at
  ~41 GiB"; "sets `--ctx-checkpoints 8` and the 2 GiB prompt cache explicitly". Its swap:
  "`make apply` … then `make pull` … then `make clients`."
- **Refusals** (the controller's ruling, 2026-10-07, from *Before Task 1*, correcting the design's
  "a `503` … and `x-should-retry: false`"): a refusal that comes after a wait is a **`409`** —
  `no_fit` · `loading` · `held_by_brake` · `footprint_suspect` — so pi doesn't retry the wait, and
  so are `load_failed` and `not_downloaded`, which no retry changes (a retry of `load_failed`
  repeats a full load; `not_downloaded` changes only with `make pull`); only an outage stays a
  **`503`**, where a retry makes sense — `gate_down` · `llama_swap_down` · `restarting` ·
  `draining`. Every `409` and `503` carries `x-should-retry: false`, since OpenAI's SDKs retry
  both by default. The front's own: `model_not_found` (404); `too_many_requests` (429);
  `route_not_served` (404); llama-swap's own
  `429` `concurrency_limit` "the front passes … on, worded". Each message word for word as *What
  you see in Phase 2a* gives it. The body is OpenAI's error shape, `{"error": {"message": <the
  sentence>, "code": <the code>}, "retry_after_s": <n>}`, with no `detail` key (Task 6 says why).
- **`retry_after_s`, and the `Retry-After` header with it** (the controller's ruling, 2026-10-07):
  `no_fit` 30 · `held_by_brake` 300 · `gate_down` 30 · `restarting` 60 · `draining` 60 ·
  `llama_swap_down` 30 · `too_many_requests` 10; none for the rest, which then carry no
  `Retry-After` (plan.md's dated correction, 2026-10-07).
- **The front's `401`**, for a key it doesn't know (the controller's ruling): "That API key isn't
  one the Spark knows. Check SPARK_API_KEY on this machine."
- **The values the spec left open** (the controller's ruling, 2026-10-07; recorded, changeable):
  waiting requests per key, 8 for each of Dan's keys and 4 for `agent`'s · open connections per
  key, 32 · sessions per uid, 8, each gone 12 h after its last heartbeat or when its process exits
  · the front's `MemoryMax=512M` · bodies up to 200 MiB, spooled to disk above 1 MiB ·
  reading a request's headers, 10 s · the gate's request size cap, 64 KiB, and its per-request
  timeout, 5 s (an admission call is held for its key's wait instead) · `WatchdogSec=30` · the
  gate's load call, 200 s (180 + 20) · the notifier, at most one alert per unit per 5 minutes ·
  the activity record, written every second, stale after 3 s · ntfy's publish timeout, 5 s ·
  `TimeoutStopSec=30`, with uvicorn's graceful shutdown at 20 s · the refusal history, 50 entries.
- **Notification types and priorities, all on by default:** `brake_fired` high ·
  `brake_needs_release` high · `gate_down` high · `front_down` high · `llama_swap_down` high ·
  `brake_down` high · `back_up` default · `refused` default · `footprint_suspect` default ·
  `load_failed` default · `brake_released` default · `room_hold_ended` default · `resident_waiting`
  default · `apply_restarted` default · `load_started` low · `loaded` low · `unloaded` low ·
  `waiting` low · `pin_ended` low · `memory_warning` low. "**One notification per event.**" "A burst
  of identical refusals … the first goes at once, and the repeats within 10 minutes go as one."
  `back_up` after "60 s". The generated table is `website/reference/notifications.md` (the
  controller's ruling).
- **Plain words:** "*Available* is always `MemAvailable` … *Free for a load* is always the admission
  figure … No message, notification or status line says 'free' alone." "*Paused* is the brake's
  word … *held* is make-room's." "Sizes in whole GiB, times in his local time on a 24-hour clock,
  never a stack trace." "**Nothing is injected into a reply stream.**"
- **Not in 2a:** the weekday preload (the controller's ruling, 2026-10-07: to the Backlog until Dan
  asks for it); the engines' own user; a private network namespace.
- **`make doctor`'s heavy checks** — the 170 MB upload and the privacy canary through the front —
  run only with `spark doctor --full` (the controller's ruling).

## Review Focus

1. **A client that goes away, or a gate that restarts, while its request waits or streams** —
   expected: the request leaves the gate's queue at once and no load is admitted for it; a load
   already started finishes, and the model stays until its idle time; the front's count for the
   model goes down on every way out (finished, client gone, upstream error, cancelled); a gate
   restart re-asks each held admission with its original deadline, so no key's wait starts over;
   `spark status` shows each model's oldest request, so a leaked count would show. *(Tasks 14, 17,
   19, 20.)*
2. **A long, silent request under `make apply`, make-room or an idle unload** — a 250K-token prefill
   that sends nothing for minutes. Expected: no read timeout cuts it; apply waits for 60 s of quiet
   for up to 15 minutes, then offers "drain now", which holds new requests and lets this one finish;
   Ctrl-C leaves nothing changed; a change elsewhere in the app never restarts the front; make-room
   and idle unloads wait for it, however long, and never cut it off; only the brake may. *(Tasks 15,
   16, 19, 28, 29.)*
3. **A cold boot under the new sandbox** — `NoNewPrivileges=` and `CapabilityBoundingSet=` on
   llama-swap's unit strip `nvidia-modprobe`'s setuid powers, so the first engine may fail to load
   `nvidia-uvm`; the engines must bind 800 and up with the capability they inherit; the gate must
   preload the residents one at a time; a gate that's down at boot means no model at all. Expected:
   every resident loads after a cold boot, or bootstrap loads `nvidia-uvm` at boot. *(Tasks 22, 24,
   35.)*
4. **Memory the gate reads wrong** — growth that doesn't show in `RssAnon` (GPU allocations), the
   embeddings engine's file-backed RSS (page cache), an outside allocation during a load that
   inflates its fall, the coder's 41 GiB loads against a full page cache, a GPU job of `agent`'s.
   Expected: *owed* errs safe (the gate subtracts RSS growth only once the soak has shown it counts,
   a registry flag), a load's fall is capped and flagged when other memory moved, the page-cache
   drill settles rule 1 before the coder becomes the default, and the brake and earlyoom stay the
   backstops. *(Tasks 7, 14, 25, 37, 40, 42.)*
5. **The first deploy fails, or the front crash-loops at start** — PID 1 holds 9100 with nothing
   behind it, so every client hangs in the backlog. Expected: the rollback to Phase 1's layout
   (llama-swap on 9100 with the client keys) is written and its render checked before anything
   moves; the crash-loop check as `agent` shows 9100 never answers as anyone else, and binding it
   always fails; the notifier pages once per 5 minutes, not every 2 s. *(Tasks 23, 34, 47.)*
6. **A refusal in pi** — pi 0.85.1 (and 0.87.1) retries a failed turn by itself, up to three times,
   2, 4 and 8 s apart, whenever the error's text matches its list, which holds "503" and "429" but
   not "409"; it reads neither `x-should-retry` nor `Retry-After` at that level (*Before Task 1*).
   Expected: a refusal after a wait, a failed load or a model not yet downloaded is a `409`, which
   pi shows at once, its sentence after the status, and doesn't retry, so Dan's 30 s refusal arrives after 30 s, not about 2¼ minutes; an
   outage's `503` is retried, as is sensible there; the S03 drill confirms it on the box.
   *(Tasks 6, 20, 48.)*

***

## File structure

| Path | Responsibility |
|---|---|
| `stack/synology/ntfy/compose.yaml` (new) | ntfy on the Synology as a Compose file, pinned by its index digest; every private value a named variable |
| `stack/synology/ntfy/variables.example` (new) | The six variables by name, with placeholders, never values |
| `stack/models.yaml` | The registry: gains each model's `label`, `keys` and `key_groups`, `notifications`, `gate` settings and the budget's measured values; whisper's tmp-dir moves; the coder becomes Qwen3.8-27B (Task 39) |
| `stack/versions.yaml` | Gains ntfy's row (`where: [synology]`) |
| `stack/llama-swap/config-schema.v257.json` (new) | llama-swap v257's own config schema, vendored from its tag, for the rendered-config check |
| `stack/templates/local-ai-front.socket` (new) | Holds 127.0.0.1:9100 from boot; never gives up |
| `stack/templates/local-ai-front.service` (new) | The front as `spark-front`: sandbox, limits, watchdog, credentials, `OnFailure=` |
| `stack/templates/local-ai-gate-status.socket`, `local-ai-gate-control.socket` (new) | The gate's two Unix sockets, 0660, groups `spark-users` and `spark-admin` |
| `stack/templates/local-ai-gate.service` (new) | The gate as `spark`: sandbox, watchdog, credentials, the values file, `OnFailure=` |
| `stack/templates/local-ai-notify@.service` (new) | The failure notifier: a dynamic user running Ubuntu's curl, with the registry's priorities |
| `stack/templates/local-ai-llama-swap.service` | 127.0.0.1:900, `CAP_NET_BIND_SERVICE`, internal keys only, `Restart=always`, sandboxed |
| `stack/templates/local-ai-brake.service` | Its key and ntfy token by `LoadCredential=`, the values file, never-give-up limits, `OnFailure=` |
| `stack/templates/local-ai-pull.service` | Runs as `spark-pull`, `UMask=0027`, its cache in a folder of its own |
| `stack/templates/compose.yaml`, `searxng-settings.yml` | `cap_drop: [ALL]`, only what each needs added back; SearXNG's request timeout |
| `stack/host/local-ai-notify` (new) | The notifier's script: unit, result and time only; once per unit per 5 minutes; token by header file |
| `stack/host/local-ai-agent-oom`, `stack/host/agent-oom.conf` (new) | Root sets `agent`'s `oom_score_adj` at login, for sshd and its user manager |
| `stack/host/bootstrap.sh` | 2a's users, root-only secrets, the new folders, the cache moved to `spark-pull`, the notifier's script, `agent`'s OOM hook, install-units' new units |
| `stack/host/50-local-ai.rules` | The polkit rule: six services, no sockets, no notifier |
| `stack/measure/cuda_ceiling.py` (new) | PEP 723, standard library: allocates and touches 1 GiB steps, stops at `MemAvailable` less the reserve |
| `stack/measure/sample_memory.py` (new) | PEP 723: `MemAvailable`, `MemFree`, `Cached`, each engine's `RssAnon` and `nvidia-smi`'s figure, 10×/s, to a CSV outside the repo |
| `spark/pyproject.toml`, `spark/uv.lock` | uvicorn (exact), Starlette, httpx explicit; anyio in the dev group |
| `spark/src/spark/paths.py` | The sockets, the gate's and launch's folders, whisper's tmp-dir, the values file, llama-swap's new URL |
| `spark/src/spark/versions.py` | `tested_against_problems`: version assumptions against `versions.yaml` and the lock |
| `spark/src/spark/registry.py` | Loads and checks the new sections |
| `spark/src/spark/budget.py` (new) | Rule 9: the gate's formula, *owed*, the hold's draw, make-room's plan, render's three checks |
| `spark/src/spark/messages.py` (new) | Every refusal, notification and confirmation, word for word |
| `spark/src/spark/procs.py` (new) | Engines' pids by port, `RssAnon`, `nvidia-smi`'s list, the top holders named by rule 7 |
| `spark/src/spark/memory.py` | Gains `MemFree` and the boot id |
| `spark/src/spark/sockets.py` (new) | systemd's named sockets from `LISTEN_FDS` |
| `spark/src/spark/protocols.py` (new) | uvicorn's h11 protocol, subclassed: the caller's uid in the scope, a header-read timeout |
| `spark/src/spark/sdnotify.py` (new) | `READY=1`, `STOPPING=1` and the watchdog, standard library only |
| `spark/src/spark/credentials.py` (new) | Credentials from `$CREDENTIALS_DIRECTORY`, the digests file, constant-time key matching |
| `spark/src/spark/serve.py` (new) | Runs uvicorn servers on given sockets, with the watchdog and a bounded shutdown |
| `spark/src/spark/gateproto.py` (new) | The gate's routes and message shapes, shared by the gate, the front and the CLI |
| `spark/src/spark/gateclient.py` (new) | The CLI's client: `http.client` over a Unix socket |
| `spark/src/spark/tickets.py` (new) | Admission tickets: issued by the gate, used up by `spark launch` |
| `spark/src/spark/launch.py` | Starts a model only with a ticket; keeps the hold check and a zero-wait fit check; `not_downloaded`; refusal records per model |
| `spark/src/spark/admission.py` | Phase 1's static fit, kept as launch's zero-wait backstop |
| `spark/src/spark/llamaswap.py` | `TESTED_AGAINST`; its default URL 127.0.0.1:900 |
| `spark/src/spark/llamaswap_async.py` (new) | The gate's httpx client for llama-swap: running, load, unload, an engine's last lines |
| `spark/src/spark/gate/` (new) | `state.py` (kept across restarts) · `notify.py` (ntfy, one per event) · `units.py` (the four units' restarts) · `admission.py` (the queue, one load at a time, tickets, refusals) · `policy.py` (drain, idle, pins, sessions) · `room.py` (make-room, release, load and unload, preload, the brake's release) · `app.py` (the two sockets' routes, and who may call each) · `main.py` (`spark gate`) |
| `spark/src/spark/front/` (new) | `parse.py` (routes, keys, bodies, caps) · `app.py` (the ASGI app) · `upstream.py` (forwarding, counting) · `gatelink.py` (admission, drain, gate down) · `main.py` (`spark front`) |
| `spark/src/spark/hold.py` | The hold gains the boot id, the episode and the model that was loading |
| `spark/src/spark/brake.py` | Idle-first order from the gate's record, the rate-of-fall watch, its steps recorded for the gate, its own alert while the gate is down, its key by credential |
| `spark/src/spark/schema.py` (new) | A small JSON-schema check of the rendered llama-swap config |
| `spark/src/spark/render.py` | llama-swap at 900, engines from 800, 180 s, internal keys, `--alias`, the new units, the notifier's priorities |
| `spark/src/spark/status.py` | `spark status` through the gate, in the plan's layout; `--json` |
| `spark/src/spark/commands.py` (new) | `spark load`, `unload`, `pin`, `unpin`, `make-room`, `logs`, `session` |
| `spark/src/spark/apply.py` | The diff, the restart plan, the quiet wait, drain now, apply-now, telling the gate |
| `spark/src/spark/doctor.py` | The front, the gate, the brake, ntfy, the sockets, version drift, the schema, a check per 2a scenario, `--full` |
| `spark/src/spark/clients.py` | `agent`'s pi `httpIdleTimeoutMs` |
| `spark/src/spark/models.py` | The pull no longer makes whisper's tmp-dir |
| `spark/src/spark/docs.py` | `spark docs notifications` |
| `spark/src/spark/cli.py` | Registers `front`, `gate` and the new commands |
| `spark/tests/fake_llamaswap.py` (new) | A llama-swap v257 stand-in that behaves as its source does, for the async tests |
| `spark/tests/test_*.py` | One file per new module, named in each task; the existing files change with their modules |
| `Makefile` | `logs` gains `front`, `gate`, `notify`; `restart-front`; help texts |
| `website/how-to/ntfy.md` (new) | Dan's runbook: ntfy, the phone, the tokens, the values file |
| `website/reference/notifications.md` (new, generated) | Every notification type with its priority, from the registry |
| `website/how-to/deploy.md`, `secret-files.md`, `pi.md`, `bootstrap.md`, `updates.md`, `index.qmd` | Runbooks for 2a |
| `website/architecture.qmd` | Parts turn solid as each is deployed |
| `website/scenarios/s01`, `s02`, `s03`, `s05`, `s14`, `s17` | Statuses and dated notes as each is verified |
| `website/design/plan.md` | Resolved items, Revisions, the measured numbers |
| `README.md`, `changelog.md`, `CLAUDE.md`, `cosmicbboy-local-ai.md` | The box's state, its history, the rules; `[verified]` only with a measurement |

### How a code task runs

Every **[Spark]** task that changes code follows the same steps, given in each task as checkboxes:

1. Write the task's tests, as its **Tests** list says, in the files it names.
2. **On the Spark**, run them with `uv run --frozen --project spark pytest <its test files>`, and see
   each fail for the reason expected: a module or name that doesn't exist yet, or the assertion
   the test exists for. A test that passes before the code is written is wrong.
3. Implement to the task's **Interfaces**, nothing more.
4. Run the task's tests again, then `make test lint`: everything passes.
5. Change the docs the task names, in the same commit (`CLAUDE.md`, *Docs must be true*).
6. Commit, staging each path by name, with the message the task gives and the trailer.

Tests run in the environment `spark/tests/conftest.py` builds (no secret, an empty `HOME`). Unix
sockets in tests live under a short `mkdtemp(dir="/tmp")`, since macOS caps an `AF_UNIX` path at
104 bytes. Async tests use anyio's pytest plugin (`@pytest.mark.anyio`, backend `asyncio`), each
bounded by `anyio.fail_after`, and inject a clock so that waits of minutes run in milliseconds.
Streams, holds and disconnects are tested against a real uvicorn on an ephemeral socket, never
httpx's `ASGITransport`, which collects whole responses.

***

## Before Task 1: how pi and Open WebUI show a refusal (checked 2026-10-07)

plan.md requires that "pi's and Open WebUI's handling of the status, the retry and the text is
checked before `website/design/phase-2a.md` fixes them" (the controller's ruling: from their source
now, and on the box in the S03 drill). Read on the Spark on 2026-10-07: pi 0.85.1 and 0.87.1 from
npm, with the `@earendil-works/pi-ai` they lock (0.85.1 and 0.87.1) and `openai` 6.40.0; Open WebUI
at tag `v0.11.4` (`backend/open_webui/routers/openai.py`, `backend/open_webui/utils/middleware.py`,
`src/lib/components/chat/Messages/Error.svelte`).

- **pi, the text.** The OpenAI SDK turns a non-2xx answer into an error whose message is
  `<status> <error.message>`, and keeps the body's `error` object (`openai` 6.40.0,
  `core/error.js`). pi-ai then shows `<status>: <that object as JSON>` (`utils/error-body.js`,
  `formatProviderError`), so a refusal reads in pi as `409: {"message":"The coder didn't load: …",
  "code":"no_fit"}`. The sentence is there, with the code beside it. A body with more keys in
  `error` shows them all, so it carries only `message` and `code`.
- **pi, the retry.** pi-ai's own retry around the SDK honours `x-should-retry: false` and is off
  unless set (`utils/provider-retry.js`; `retry.provider.maxRetries` unset). But pi's agent-level
  auto-retry is on by default (`retry.enabled` true, `maxRetries` 3, `baseDelayMs` 2000:
  `core/settings-manager.js`) and retries whenever the error text matches a list that holds "503",
  "429", "service unavailable" and "timeout" (`utils/retry.js`), whatever the headers. So a refusal
  as first designed, a `503`, would be retried three times, 2, 4 and 8 s apart, each a new request
  with its own key's wait (`auto_retry_start`). The list holds neither "409" nor any word of the
  refusals' sentences (checked against plan.md's table), so the controller ruled, the same day,
  that a refusal after a wait is a `409` (*Global Constraints*), as are a failed load and a model
  not yet downloaded, which no retry changes; only an outage stays a `503`, which pi retries. The
  OpenAI SDK retries a 409 too, unless `x-should-retry: false` says not to (`openai` 6.40.0,
  `client.js`, `shouldRetry`), so every refusal keeps that header. The S03 drill
  (Task 48) confirms it on the box.
- **Open WebUI v0.11.4.** It passes the front's status and JSON body on unchanged, with no retry
  (`routers/openai.py`, the non-streaming branch, since a refusal isn't `text/event-stream`); its
  chat handler takes the body's `error`, then its `detail` if it has one
  (`utils/middleware.py`, `non_streaming_chat_response_handler`), and the chat shows that object's
  `message` (`Error.svelte`). So the error object must have no `detail` key, and the chat then
  shows the sentence alone.

***

### Task 1 [Spark]: ntfy's Compose file and its runbook

ntfy is deployed on the Synology as a Docker Compose file, as Dan deploys every app on the Synology
and his other homelab machines (Dan, 2026-10-07). Portainer's stacks are today's helper, and he may
move to another, so nothing here depends on Portainer: the same file deploys as a Portainer stack
(from its web editor, or from this public repo as a Git-repository stack), with plain
`docker compose up -d`, and with any other Compose helper. Phase 2b's watchdog on the Synology
follows the same pattern, under `stack/synology/watchdog/`.

**Files:**

- Create: `stack/synology/ntfy/compose.yaml`, `stack/synology/ntfy/variables.example`,
  `spark/tests/test_ntfy_records.py`, `website/how-to/ntfy.md`
- Modify: `stack/versions.yaml` (an `ntfy` row), `website/reference/stack.md` (regenerated),
  `website/how-to/updates.md` (a row), `website/how-to/index.qmd` (ntfy in *In order*, after
  *Secret files*)

**Interfaces:**

- `stack/synology/ntfy/compose.yaml`: one service, `ntfy`, with `image:
  binwiederhier/ntfy:v2.28.0@sha256:6ef4b819f722fccdc036af611c4774cfdc2de821ab74fdd48bbf4c9d6f8973da`,
  `command: serve`, `restart: unless-stopped`. It holds no secret and no private value. Its fixed
  environment: `NTFY_AUTH_DEFAULT_ACCESS: deny-all`, `NTFY_AUTH_FILE: /var/lib/ntfy/user.db`,
  `NTFY_CACHE_FILE: /var/lib/ntfy/cache.db`, `NTFY_BEHIND_PROXY: "false"`. What is private arrives
  as a variable, each required with Compose's `${NAME:?set NAME}` so a missing one stops the
  deploy with its name: `NTFY_BASE_URL` (the NAS's address as the phone and the Spark reach it),
  `NTFY_AUTH_USERS`, `NTFY_AUTH_ACCESS` and `NTFY_AUTH_TOKENS` (ntfy v2.28.0's declarative users,
  access and tokens, which hold the topic names and the tokens: `docs/config.md` at that tag),
  `NTFY_DATA_DIR` (the host folder, bound to `/var/lib/ntfy`) and `NTFY_PORT` (published to the
  container's 80). No `NTFY_UPSTREAM_BASE_URL`, no Firebase key, no web push.
- `stack/synology/ntfy/variables.example`: each of the six variables, one per line, as
  `NAME=<what goes here>`, with no value; a comment says the values live in the helper (in
  Portainer, the stack's environment variables) or in a `.env` beside the file, never in the repo,
  and that a value holding a `$` (a bcrypt hash) is single-quoted in a `.env`. (Not
  `.env.example`: the sessions' secrets guard refuses every `.env.*` file, so none could read it.)
- `stack/versions.yaml`, a new `ntfy` component: `version: v2.28.0`, `image:
  docker.io/binwiederhier/ntfy`, `where: [synology]`, `pin: sha256:6ef4…73da` (in full),
  `deployed: true`, `docs: https://docs.ntfy.sh/`, `context7: null`, `changelog:
  https://github.com/binwiederhier/ntfy/releases`, `advisories:
  https://github.com/binwiederhier/ntfy/security/advisories`.
- `website/how-to/ntfy.md`, Dan's runbook for Task 2, written for "any Compose helper; Portainer
  today", its sections in this order, each step naming where it runs (the Synology's DSM,
  Portainer, Tailscale's admin console, the phone, the Spark):
  1. *The NAS on the tailnet*, if it isn't: DSM's Tailscale package, and a tag for the NAS so a
     grant can name it.
  2. *The grant*: the Spark's tag and Dan's phone may reach ntfy's port on the NAS, and nothing else
     is widened; written in the vault's ACL policy, then shown to the session (Task 2).
  3. *The values*: three topic names, one per publisher; a user for each publisher (`spark-gate`,
     `spark-notify`, `spark-brake`) and one for the phone, each password hashed with
     `ntfy user hash`; a token for each publisher from `ntfy token generate`; how each variable is
     written (ntfy's comma-separated `user:hash:role`, `user:topic:permission` and `user:token`
     forms), with publishers write-only to their own topic and the phone read-only to the three.
     Where each value is kept: the helper's variables and the vault, never the repo or a chat.
  4. *Deploy it — Portainer today*: a stack from the web editor (the file pasted in) or from a Git
     repository (this repo's URL, its branch, the compose path `stack/synology/ntfy/compose.yaml`),
     the six variables in the stack's environment, then deploy.
  5. *Deploy it — any other Compose helper*: the file beside a `.env` holding the six variables,
     then `docker compose up -d`; any helper that reads a Compose file and its variables works
     the same way.
  6. *Moving the pin, and rolling back*: the digest changes in `compose.yaml` and
     `versions.yaml` in one commit, a week after the release (`CLAUDE.md`'s seven-day rule), then
     a redeploy (Portainer's *Pull and redeploy*, or `docker compose pull && docker compose up -d`);
     a rollback redeploys the previous commit's digest; ntfy's data folder is kept through both.
  7. *The phone*: the ntfy app, the NAS as its default server, the phone's user, instant delivery,
     the three topics; Android's Do Not Disturb from 00:00 to 05:00, with the ntfy app's high and
     max channels allowed through it.
  8. *On the Spark*: `/etc/local-ai/values.env` (`NTFY_URL`, `NTFY_TOPIC_GATE`,
     `NTFY_TOPIC_NOTIFY`, `NTFY_TOPIC_BRAKE`; `0600 root:root`) and the three header files,
     `/etc/local-ai/secrets/ntfy-gate.header`, `ntfy-notify.header` and `ntfy-brake.header`, each one
     line, `Authorization: Bearer <token>`, `0600 root:root`, written without the token appearing
     on a screen or a command line.
  9. *A test publish*: as root on the Spark, one message per header file, at `high` and at `low`.
  10. *Record it*: the vault's entry note names the values file, the three tokens and where each
      lives, never a value.
- `website/how-to/updates.md`, a row in its table: ntfy, "its container image on the Synology,
  pinned by digest", moved "by hand on upgrade day, in its Compose helper", as `ntfy.md` §6 says.

**Tests** (`spark/tests/test_ntfy_records.py`):

- `test_ntfy_compose_pins_the_versions_digest` — `services.ntfy.image` equals
  `binwiederhier/ntfy:<version>@<pin>` from `load_versions("stack/versions.yaml")["ntfy"]`, and
  that is `binwiederhier/ntfy:v2.28.0@sha256:6ef4…73da`.
- `test_ntfy_compose_holds_no_private_value` — `spark.leakcheck.scan_text` over each of the two
  files, with an empty denylist, finds nothing; neither holds `tk_` or `$2a$`; and every one of
  the six variables appears in `compose.yaml` only as `${NAME:?set NAME}`.
- `test_every_variable_is_named_in_the_example` — the set of `${…}` names in `compose.yaml`
  equals the set of names in `variables.example`, which has no value after any `=` but a
  placeholder in angle brackets.
- `test_ntfy_denies_by_default_and_reaches_no_relay` — `NTFY_AUTH_DEFAULT_ACCESS` is `deny-all`,
  and no key holds `UPSTREAM`, `FIREBASE` or `WEB_PUSH`.
- `test_docker_compose_reads_the_file` — skipped where `docker compose` is missing:
  `docker compose -f stack/synology/ntfy/compose.yaml --env-file <stand-ins> config` exits 0, the
  stand-ins written to `tmp_path` (`NTFY_BASE_URL=http://nas.example.invalid:8090`, a
  single-quoted bcrypt-shaped hash holding `$`, a `tk_` stand-in, `NTFY_DATA_DIR=/volume1/docker/ntfy`,
  `NTFY_PORT=8090`), and its output keeps the hash's `$` (as `$$`); with no values it exits
  non-zero, naming `NTFY_BASE_URL`.

**Steps:**

- [ ] **Step 1: Confirm the digest.** **On the Spark** (an anonymous token for Docker Hub's public
  registry; nothing secret):

```bash
t=$(curl -fsS "https://auth.docker.io/token?service=registry.docker.io&scope=repository:binwiederhier/ntfy:pull" | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')
curl -fsSI -H "Authorization: Bearer $t" -H 'Accept: application/vnd.oci.image.index.v1+json' -H 'Accept: application/vnd.docker.distribution.manifest.list.v2+json' https://registry-1.docker.io/v2/binwiederhier/ntfy/manifests/v2.28.0 | grep -i '^docker-content-digest'
```

  Expected: `docker-content-digest: sha256:6ef4b819f722fccdc036af611c4774cfdc2de821ab74fdd48bbf4c9d6f8973da`.
  Anything else: stop, and tell the controller.

- [ ] **Step 2: The failing tests**, then run them: they fail, the files and the `ntfy` component
  missing.
- [ ] **Step 3: The records**: `compose.yaml`, `variables.example`, the `versions.yaml` row; **on
  the Spark**, `uv run --frozen --project spark spark docs stack --write`.
- [ ] **Step 4:** the tests pass, `test_docker_compose_reads_the_file` among them (Docker's CLI
  reads the file without the daemon, so it runs as you); `make test lint`.
- [ ] **Step 5: The runbook**, `updates.md`'s row and `index.qmd`. Run every command block in
  `ntfy.md` before it goes in: `ntfy user hash`, `ntfy token generate` and a server with the six
  variables, against ntfy v2.28.0's own `linux_arm64` release binary (its checksum checked against
  the release's `checksums.txt`) with stand-in values and a temporary data folder; `docker compose
  config` with stand-ins, as the test does; the Spark's steps against a temporary folder standing
  in for `/etc/local-ai`; the test publish against that binary's `ntfy serve` on 127.0.0.1.
  Portainer's steps are written from Portainer's documentation, since it isn't on the Spark: say
  so in the runbook, and Task 2 checks them. Say in the task's report which ran, and how.
- [ ] **Step 6: Commit.** **On the Spark:**

```bash
git add stack/synology/ntfy/compose.yaml stack/synology/ntfy/variables.example stack/versions.yaml \
  website/reference/stack.md spark/tests/test_ntfy_records.py website/how-to/ntfy.md \
  website/how-to/updates.md website/how-to/index.qmd
git commit -m "build(stack): 🤖 ntfy v2.28.0 as a Compose file for the Synology, with its runbook" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 2 [Dan]: ntfy on the Synology, the phone, the tokens and the private values file

**Files (the session's record, once Dan's steps are done):**

- Modify: `CLAUDE.md` (*Where that material goes instead*: the values file, and that ntfy's
  values live in its Compose helper and the vault), `README.md` (§My environment: the values file;
  §Current state: ntfy on the Synology, its version, its helper), `changelog.md`,
  `website/architecture.qmd` (`ntfy` and `ntfy_phone` solid; the edges into `ntfy` stay dashed
  until each publisher is built), `website/how-to/ntfy.md` (only what Dan's run corrects,
  Portainer's steps above all)

**What later tasks read** (values never in the repo): `/etc/local-ai/values.env` and the three
header files, as `ntfy.md` §8 writes them.

- [ ] **Step 1 [Dan, on the Synology and in Tailscale's console]:** `ntfy.md` §1 and §3.
- [ ] **Step 2: The grant, reviewed in this task** (`CLAUDE.md`, *Review permissions … in the task
  that changes them*). **[Dan]** adds the grant of `ntfy.md` §2, then shows it to the session in
  the chat, never in a file. The session checks that it lets exactly the Spark's tag and Dan's
  phone reach ntfy's port on the NAS, that it widens nothing else, and that the NAS's own node
  reaches nothing new, and says so. The policy stays in the vault.
- [ ] **Step 3 [Dan, in Portainer on the Synology]:** `ntfy.md` §4, as a Git-repository stack or
  from the web editor. Expected: the stack runs, and ntfy's web page answers on the NAS's address
  from the phone, asking for a login.
- [ ] **Step 4 [Dan, on the phone]:** `ntfy.md` §7.
- [ ] **Step 5 [Dan, on the Spark]:** `ntfy.md` §8 and §9. Expected: each test message arrives;
  `high` sounds and `low` arrives silently; with Do Not Disturb switched on by hand, only `high`
  breaks through.
- [ ] **Step 6 [Dan]:** `ntfy.md` §10, in the vault.
- [ ] **Step 7: The record.** The session writes the files above: what was set up, where, the
  version, the helper; never an address, a topic or a token.
- [ ] **Step 8: Commit.** **On the Spark:**

```bash
git add CLAUDE.md README.md changelog.md website/architecture.qmd website/how-to/ntfy.md
git commit -m "docs(machine): 🤖 record ntfy on the Synology, the phone, and the private values file" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 3 [Spark]: the lock — uvicorn and Starlette, and version assumptions tied to versions.yaml

**Files:**

- Create: `spark/tests/test_tested_against.py`
- Modify: `spark/pyproject.toml`, `spark/uv.lock`, `spark/src/spark/versions.py`,
  `spark/src/spark/llamaswap.py`, `spark/src/spark/render.py`, `website/how-to/updates.md`

**Interfaces:**

- `pyproject.toml`'s `dependencies` gain `"uvicorn==0.54.0"` (exact, no extras),
  `"starlette>=1.0.1,<2"` and `"httpx>=0.28,<1"` (explicit; it is already locked through
  huggingface_hub); the `dev` group gains `"anyio>=4"` for its pytest plugin.
- `TESTED_AGAINST: dict[str, str]` — a module-level constant in each module whose code relies on a
  component's version. A key is a `versions.yaml` component name, or `pypi:<distribution>`. This
  task adds `llamaswap.TESTED_AGAINST = {"llama-swap": "v257"}` and `render.TESTED_AGAINST =
  {"llama-swap": "v257", "llama.cpp": "b11146", "whisper.cpp": "v1.9.4"}`; later tasks add
  `protocols`, `llamaswap_async`, `gate.main`, `front.main` and `tests/fake_llamaswap.py`.
- `versions.tested_against_problems(modules: Iterable[ModuleType], components: dict[str,
  Component], installed: Callable[[str], str] = importlib.metadata.version) -> list[str]` — one
  line per entry that disagrees: `"<module>: TESTED_AGAINST <key> <value>, but <where> has
  <actual>"`; a key that is neither a component nor `pypi:` is a problem too.

**Tests** (`spark/tests/test_tested_against.py`):

- `test_every_tested_against_matches_versions_yaml_or_the_lock` — every module under `spark`
  (found with `pkgutil.walk_packages`) and `tests/fake_llamaswap.py` once it exists, given to
  `tested_against_problems` with `stack/versions.yaml`'s components: the result is `[]`.
- `test_a_wrong_assumption_is_named` — a stand-in module with `TESTED_AGAINST = {"llama-swap":
  "v999"}` gives one problem, naming the module, `llama-swap`, `v999` and `v257`; one with
  `{"pypi:uvicorn": "0.1.0"}` gives one naming `0.1.0` and the installed version; one with
  `{"nonesuch": "1"}` gives one naming `nonesuch`.
- `test_uvicorn_is_pinned_exactly` — `pyproject.toml`'s dependencies hold exactly one entry for
  uvicorn, and it is `uvicorn==0.54.0`.
- `test_the_lock_adds_only_uvicorn_and_starlette` — the lock's package names are exactly
  `anyio certifi click colorama filelock fsspec h11 hf-xet httpcore httpx huggingface-hub idna
  iniconfig packaging pluggy pygments pytest pyyaml spark tqdm typing-extensions` plus `starlette`
  and `uvicorn`.
- `test_the_cli_status_and_launch_import_neither_uvicorn_nor_httpx` — a subprocess running
  `import spark.cli, spark.status, spark.launch` leaves no module named `uvicorn`, `starlette` or
  `httpx` (or under them) in `sys.modules`. (Tasks 9 and 27 add `spark.gateclient` and the
  commands to the import.)

**Steps:**

- [ ] **Step 1:** the failing tests; run them: they fail (`tested_against_problems` and the
  constants missing; uvicorn not in `pyproject.toml`).
- [ ] **Step 2:** the dependencies; **on the Spark**, `uv lock --project spark`. Expected: the lock
  gains uvicorn 0.54.0 (uploaded 2026-09-25) and starlette 1.7.0 (2026-09-23), both outside the
  seven-day window on 2026-10-07, and nothing else new. Read both packages' security advisories
  and the changelogs of the locked versions, and say what they found in the task's report.
- [ ] **Step 3:** `tested_against_problems` and the two constants; the tests pass; `make test lint`.
- [ ] **Step 4: Docs.** `updates.md`: uvicorn is pinned exactly, since `protocols.py` subclasses
  its internals; a bump changes the pin and `protocols.TESTED_AGAINST` together, and runs
  `test_protocols.py` before anything else.
- [ ] **Step 5: Commit.** **On the Spark:**

```bash
git add spark/pyproject.toml spark/uv.lock spark/src/spark/versions.py spark/src/spark/llamaswap.py \
  spark/src/spark/render.py spark/tests/test_tested_against.py website/how-to/updates.md
git commit -m "build(spark): 🤖 add uvicorn and Starlette, and tie version assumptions to versions.yaml" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 4 [Spark]: the registry for 2a

**Files:**

- Modify: `spark/src/spark/registry.py`, `stack/models.yaml`, `spark/tests/fixtures/models.yaml`,
  `spark/tests/test_registry.py`, `spark/tests/test_stack_registry.py`

**Interfaces:**

- `Model` gains `label: str` — the plain-words name messages use (*the coder*, *Gemma*, *the
  embeddings*, *whisper*); required and non-empty.
- `Budget(allocatable_gib, reserve_gib, idle_available_gib: float, allocatable_measured: bool)` —
  `idle_available_gib` required; `allocatable_measured` defaults to false.
- `ClientKey(name: str, group: str, label: str)` — `label` as messages name the asker (*pi on the
  Mac*, *the web UI*, *agent*).
- `KeyGroup(name: str, wait_s: int, dan: bool, max_waiting: int, max_open: int)`.
- `GateSettings(idle_unload_min: int, owed_reads_rss: bool)`.
- `NOTIFICATION_TYPES: tuple[str, ...]` — in this order: `brake_fired`, `brake_needs_release`,
  `gate_down`, `front_down`, `llama_swap_down`, `brake_down`, `back_up`, `refused`,
  `footprint_suspect`, `load_failed`, `brake_released`, `room_hold_ended`, `resident_waiting`,
  `apply_restarted`, `load_started`, `loaded`, `unloaded`, `waiting`, `pin_ended`,
  `memory_warning`. `PRIORITIES = ("high", "default", "low", "off")`.
- `Registry` gains `keys: dict[str, ClientKey]`, `key_groups: dict[str, KeyGroup]`,
  `notifications: dict[str, str]`, `gate: GateSettings`; `SECTIONS` gains `keys`, `key_groups`,
  `notifications`, `gate`, each required.
- Checks, each a `RegistryError` naming what's wrong: a key's group exists; at least one group has
  `dan: true`; `wait_s` is a positive whole number; `max_waiting` is from 1 to 9 (below
  llama-swap's 10 per model); `max_open ≥ max_waiting`; every type in `NOTIFICATION_TYPES` is listed
  once, with a priority in `PRIORITIES`, and no other key; `idle_available_gib > reserve_gib`;
  `idle_unload_min` positive.
- `stack/models.yaml` gains: `budget.idle_available_gib: 117`, `budget.allocatable_measured: false`;
  `key_groups: {dan: {wait_s: 30, dan: true, max_waiting: 8, max_open: 32}, agent: {wait_s: 600,
  dan: false, max_waiting: 4, max_open: 32}}`; `keys: {dan-mac: {group: dan, label: pi on the
  Mac}, open-webui: {group: dan, label: the web UI}, agent: {group: agent, label: agent}}`;
  `notifications:` the twenty types at the Global Constraints' priorities; `gate: {idle_unload_min:
  60, owed_reads_rss: false}`; each model's `label` (`Gemma`, `the embeddings`, `whisper`, `the
  coder`). The file's head comment says what each new section is for.
- The fixture gains the same sections, with labels `the vision model`, `the embeddings`,
  `whisper` and `the coder` for `vision-chat`, `embed`, `stt` and `coder`.

**Tests:**

`spark/tests/test_registry.py`:

- `test_the_fixture_loads_its_2a_sections` — `keys["dan-mac"] == ClientKey("dan-mac", "dan", "pi
  on the Mac")`; `key_groups["agent"] == KeyGroup("agent", 600, False, 4, 32)`; `gate ==
  GateSettings(60, False)`; `budget.idle_available_gib == 117`; `notifications["brake_fired"] ==
  "high"`; `models["coder"].label == "the coder"`.
- `test_every_model_needs_a_plain_words_label` — the coder without `label`, and with `label: ""`:
  each refused, naming `coder` and `label`.
- `test_a_key_must_name_a_group_that_exists` — `agent`'s group `robots`: refused, naming `agent`
  and `robots`.
- `test_one_key_group_must_be_dans` — both groups `dan: false`: refused, naming `dan: true`.
- `test_waiting_caps_stay_below_llama_swaps_ten` — `agent`'s `max_waiting` 10 and 0: refused, the
  message naming `max_waiting` and 10; 9 loads.
- `test_open_connections_cover_the_waiting_ones` — `max_open: 3` with `max_waiting: 4`: refused.
- `test_a_wait_must_be_positive` — `wait_s: 0`, and `wait_s: "30"`: refused.
- `test_the_notification_list_names_every_type_once` — `waiting` removed: refused, naming
  `waiting`; `coffee: low` added: refused, naming `coffee`; `refused: loud`: refused, naming `loud`
  and the four priorities.
- `test_notification_types_are_the_twenty_in_the_plans_order` — `NOTIFICATION_TYPES` is the tuple
  above.
- `test_idle_available_must_exceed_the_reserve` — `idle_available_gib: 24` with `reserve_gib: 24`:
  refused.
- `test_a_new_section_may_not_be_missing` — each of `keys`, `key_groups`, `notifications`, `gate`
  removed in turn: refused, naming it.

`spark/tests/test_stack_registry.py`:

- `test_the_stack_registrys_waits_are_30s_for_dan_and_10_minutes_for_agent` — `dan.wait_s == 30`,
  `agent.wait_s == 600`; `dan-mac` and `open-webui` in `dan`, `agent` in `agent`.
- `test_the_stack_registrys_caps_are_the_rulings` — `dan`: 8 and 32; `agent`: 4 and 32.
- `test_on_demand_models_idle_unload_after_60_minutes` — `gate.idle_unload_min == 60`.
- `test_owed_reads_rss_stays_off_until_the_soak` — `gate.owed_reads_rss is False`. (Task 42
  changes this test, with the soak's evidence.)
- `test_every_notification_is_on_at_the_plans_priority` — `notifications` equals the twenty types
  at the Global Constraints' priorities; none is `off`.
- `test_the_stack_registrys_labels_are_the_plans_words` — `Gemma`, `the embeddings`, `whisper`,
  `the coder`, by model.
- `test_the_budget_records_idle_memavailable` — `idle_available_gib == 117`,
  `allocatable_measured is False`.

**Steps:**

- [ ] **Step 1:** the failing tests; run them: they fail (no `label`, no new sections).
- [ ] **Step 2:** `registry.py`, the fixture and `stack/models.yaml`; the tests pass;
  `make test lint`, including `test_the_real_registry_renders`.
- [ ] **Step 3: Commit.** **On the Spark:**

```bash
git add spark/src/spark/registry.py stack/models.yaml spark/tests/fixtures/models.yaml \
  spark/tests/test_registry.py spark/tests/test_stack_registry.py
git commit -m "feat(spark): 🤖 the registry gains labels, keys and their groups, notifications and the gate's settings" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 5 [Spark]: the budget — rule 9's formulas, and render's corrected check

**Files:**

- Create: `spark/src/spark/budget.py`, `spark/tests/test_budget.py`
- Modify: `spark/src/spark/render.py` (`check_budget` uses `budget.check_set`; `run` prints the
  warnings), `spark/tests/test_render.py`, `CLAUDE.md` (the reserve gotcha's dated note)

**Interfaces** (exact `Decimal` arithmetic throughout, from `Decimal(repr(x))`, as
`admission._gib` does):

- `Loaded(name: str, footprint_gib: float, held_now_gib: float)` — `held_now` is what the model's
  load took (the fall in `MemAvailable` across it), plus its engine's `RssAnon` growth since, when
  the registry's `gate.owed_reads_rss` is on.
- `owed_gib(loaded: Iterable[Loaded]) -> Decimal` — Σ max(0, footprint − held_now).
- `free_for_a_load(*, available, reserve, owed, ceiling, committed, starting, held) -> Decimal` —
  `min(available − reserve − owed, ceiling − committed) − starting − held`.
- `hold_after_dans_load(*, free_outside_hold, hold, footprint) -> Decimal` — `max(0, hold −
  max(0, footprint − free_outside_hold))`.
- `ALL` — a sentinel for make-room's `--all`.
- `Candidate(model: str, label: str, gib: float, resident: bool, pinned: bool, session: str |
  None, inflight: int, oldest_s: float | None, idle_min: float | None)`.
- `RoomPlan(candidates: list[Candidate], unload: list[str], free_after_gib: Decimal, enough: bool,
  most_gib: Decimal)`; `make_room_plan(target: Decimal | ALL, free_now_gib, candidates) ->
  RoomPlan` — candidates sorted largest first (ties by name); `unload` is the shortest prefix of
  that order after which `free_now + Σ gib ≥ target` (every candidate for `ALL`); when even all of
  them fall short, `enough` is false and `unload` is all of them; `most_gib` is `free_now + Σ` of
  every candidate.
- `StaticCheck(errors: list[str], warnings: list[str])`; `check_set(registry) -> StaticCheck` —
  errors: Σ footprints > `allocatable_gib`; residents + reserve > `idle_available_gib`; an
  on-demand model whose footprint exceeds `free_for_a_load(available=idle_available,
  reserve=reserve, owed=0, ceiling=allocatable, committed=residents, starting=0, held=0)`. Warning:
  `idle_available − Σ footprints < brake.warn_gib`. Each line names its numbers to one decimal,
  the need rounded up and the room down.
- `render.check_budget(registry) -> list[str]` raises `RenderError` on the first error and returns
  the warnings; `spark render` prints each as `render: warning — <text>` and still exits 0.

**Tests** (`spark/tests/test_budget.py`, unless named):

- `test_free_for_a_load_is_18_at_the_refusal_examples_moment` — available 48, reserve 24, owed 6,
  ceiling 102, committed 43, starting 0, held 0 → `Decimal("18")`.
- `test_owed_counts_rss_growth_once` — Gemma 32 holding 27, the embeddings 8 holding 7, whisper 3
  holding 3 → owed 6; Gemma then holding 31 → owed 2.
- `test_owed_is_never_negative` — footprint 8 holding 9 → 0.
- `test_the_ceiling_term_binds_only_below_idle_less_the_reserve` — available 117, reserve 24,
  owed 0, committed 0, starting 0, held 0: ceiling 102 → 93; ceiling 90 → 90.
- `test_starting_and_held_are_subtracted` — available 117, reserve 24, owed 0, ceiling 102,
  committed 43, starting 41, held 10 → 8.
- `test_dans_load_draws_the_room_outside_the_hold_first` — `free_outside_hold` 9, hold 41:
  footprint 41 → 9; footprint 5 → 41; footprint 50 → 0.
- `test_make_room_40_unloads_the_coder_and_leaves_50` — candidates the coder 41 (on demand),
  Gemma 32, the embeddings 8, whisper 3 (resident); free now 9; target 40 → `unload ==
  ["coder"]`, `free_after_gib == 50`, `enough`.
- `test_make_room_70_unloads_the_coder_and_gemma` — the same, target 70 → `["coder", "gemma"]`,
  82.
- `test_make_room_asked_for_70_with_the_python_job_reports_61` — Gemma 32, the embeddings 8,
  whisper 3; free now 18; target 70 → all three, `enough` false, `most_gib == 61`.
- `test_make_room_all_unloads_everything` — target `ALL` → every candidate, `enough`.
- `test_the_2a_set_passes_with_no_warning` — residents 32, 8, 3, the coder 41; ceiling 102,
  reserve 24, idle 117, warn 28 → no error, no warning.
- `test_a_set_over_the_ceiling_is_refused_with_its_numbers` — the same plus a 20 GiB on-demand
  model → an error holding `104.0` and `102.0`.
- `test_residents_and_the_reserve_must_fit_idle_memavailable` — residents 32, 8, 3 and a 60 GiB
  resident, reserve 24, idle 117 → an error holding `127.0` and `117.0`.
- `test_an_on_demand_model_that_cant_load_beside_the_residents_is_refused` — residents 43 in all,
  an on-demand model of 57 (Σ 100) → an error naming that model, `57.0` and `50.0`.
- `test_render_warns_when_everything_loaded_sits_under_the_warn_line` — residents 43, the coder 41
  and a 10 GiB on-demand model → no error; one warning holding `23.0` and `28`.
- `test_render_and_the_gate_share_one_formula` — `free_for_a_load` replaced with a spy:
  `check_set` calls it once per on-demand model, with available = idle, owed 0, committed = the
  residents' sum, starting 0, held 0.
- `test_the_numbers_add_up_exactly` — available 52.3, reserve 24, owed 0, ceiling 102, committed
  0, starting 0, held 0 → exactly `28.3`.
- In `test_render.py`: Phase 1's budget tests rewritten to the new checks;
  `test_spark_render_prints_its_warnings` — a registry that warns: `spark render` prints
  `render: warning — …` and returns 0.

**Steps:**

- [ ] **Step 1:** the failing tests; run them: they fail (`spark.budget` missing; the old check
  refuses the 2a set).
- [ ] **Step 2:** `budget.py` and render's check; the tests pass; `make test lint`.
- [ ] **Step 3: Docs.** `CLAUDE.md`'s reserve gotcha: its dated note says the design is approved
  and render's corrected check is built (this task's date), the gate's half still to come.
- [ ] **Step 4: Commit.** **On the Spark:**

```bash
git add spark/src/spark/budget.py spark/tests/test_budget.py spark/src/spark/render.py spark/tests/test_render.py CLAUDE.md
git commit -m "feat(spark): 🤖 rule 9: render checks the set against the ceiling and the residents at idle" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 6 [Spark]: the words — every refusal, notification and confirmation

**Files:**

- Create: `spark/src/spark/messages.py`, `spark/tests/test_messages.py`,
  `website/reference/notifications.md` (generated)
- Modify: `spark/src/spark/docs.py`, `spark/tests/test_scenarios.py` (the docs command's tests),
  `website/design/plan.md` (dated notes under *What you see in Phase 2a*, and a Revisions line)

**Interfaces:**

- `CODES_409 = ("no_fit", "loading", "held_by_brake", "footprint_suspect", "load_failed",
  "not_downloaded")` — refusals after a wait, and the two no retry changes; `CODES_503 =
  ("gate_down", "restarting", "llama_swap_down", "draining")` — outages;
  `FRONT_CODES = {"model_not_found": 404, "too_many_requests": 429, "route_not_served": 404}`;
  `CONCURRENCY_LIMIT = "concurrency_limit"`; `RETRY_AFTER_S = {"no_fit": 30, "held_by_brake": 300,
  "gate_down": 30, "restarting": 60, "draining": 60, "llama_swap_down": 30,
  "too_many_requests": 10}`; `UNKNOWN_KEY = "That API key isn't one the Spark knows. Check
  SPARK_API_KEY on this machine."`
- `Refusal(code: str, status: int, message: str, retry_after_s: int | None)`:
  - `body() -> dict` — `{"error": {"message": message, "code": code}}`, plus `"retry_after_s": n`
    at the top level when there is one. No other key in `error`, and never `detail` (*Before
    Task 1*: pi shows every key, and Open WebUI shows `detail` in place of `message`).
  - `headers() -> list[tuple[bytes, bytes]]` — `content-type: application/json`; for a 409 or a
    503, `x-should-retry: false`, and `retry-after: <n>` when there is one; for the 429,
    `retry-after: <n>`; for a 404, neither.
- `PI_RETRY_PATTERNS: tuple[str, ...]` — in the test file, not the module: pi-ai 0.85.1's
  `RETRYABLE_PROVIDER_ERROR_PATTERN` list from `utils/retry.js`, copied, with 0.87.1's additions
  (`currently experiencing high demand`, `520`).
- `Holder(name: str, gib: float, dans: bool)` — a memory holder as messages name it, defined
  here; Task 7's `procs.top_holders` builds them.
- `Moment` — what a message needs: `needed_gib`, `available_gib`, `reserve_gib`, `owed_gib`,
  `held_gib`, `hold_counted: bool`, `holders: list[Holder]`, `wait_s`, `model_label`,
  `model_command` (the name `spark load` takes: the model's first role, else its name),
  `key_label`, `for_agent: bool`, and per code: `loading_label`, `brake_at: datetime`,
  `brake_available_gib`, `release_waits_for_dan: bool`, `engine_said: str | None`,
  `deadline_s`, `download_gib`, `inflight`, `drain_for: "make-room" | None`, `asked_name`,
  `models: list[tuple[label, name]]`.
- `refusal(code: str, m: Moment) -> Refusal` — one sentence a person reads, then the numbers, then
  one next step. The rules every message keeps:
  - a model by its label, and its label's first letter capitalised at the start of a sentence when
    it starts with "the " (*The coder*, *Gemma*, *whisper*); a key by its label as is (*pi on the
    Mac*, *agent*, *The web UI* at a sentence's start);
  - *available* only for `MemAvailable`, *free for a load* only for the admission figure, never
    "free" alone;
  - sizes in whole GiB (a need rounded up; *available* and *free for a load* rounded down; the
    rest to the nearest), but a reading near a line (the brake's, the warn line's) to one decimal
    (*19.6 GiB*);
  - a time as the local 24-hour `HH:MM` of an aware `datetime`;
  - a wait as `<n> s` under a minute, else `<n> minutes` (`1 minute`);
  - *no_fit*'s parenthesis lists the reserve, then the growth owed when it isn't 0, then the hold
    when it is counted, joined "A and B" or "A, B and C";
  - for `agent`'s key, a process of Dan's is named *a process of Dan's, <n> GiB*.
- `Notification(type: str, priority: str, message: str)`; `notification(type: str, registry,
  **fields) -> Notification | None` — None when the registry has the type `off`.
- `NOTIFICATION_DOC: dict[str, tuple[str, str]]` — each type's *When* and *Example*, from plan.md's
  table.
- Confirmations: `loaded(label, seconds, idle_min, command)`, `unloading(label, inflight)`,
  `unloaded(label)`, `pinned(label, until, loaded_s)`, `unpinned(label, idle_min)`,
  `room_list(target_gib, free_now_gib, plan)`, `room_done(...)`, `room_all()`,
  `room_held(...)`, `room_too_much(target_gib, most_gib, python_job…)`, `brake_released_by_dan(reloading)`,
  `apply_waiting(model_label, quiet_for_s, needed_s)`, `apply_no_quiet(inflight)`,
  `apply_now_confirm(inflight: list[tuple[key_label, model_label]])` — each the plan's *Each command
  says what it did, and how to undo it* row, word for word.
- `docs.render_notifications_page(registry) -> str` — front matter (`title: "Notifications"`), a
  line saying it is generated from `stack/models.yaml` by `spark docs notifications --write`, and
  a table *Type · Priority · When · Example*, one row per type in `NOTIFICATION_TYPES` order;
  `spark docs notifications --write | --check` (`--check` exits 1, saying the page is stale, when
  it differs).

**The refusals, word for word.** Each test's expected text is plan.md's (*What you see in Phase
2a*, the refusal table), its italics' asterisks dropped and its backticks kept, with these inputs
(the examples' moment: the residents loaded, the coder not, a 32 GiB python job of Dan's; times in
the test's own time zone):

| Code | Inputs |
|---|---|
| `no_fit` (Dan) | needed 41, available 48, reserve 24, owed 6, hold not counted, holders `python3 (chendaniely)` 32 and Gemma 27, key *pi on the Mac*, command `coder` |
| `no_fit` (`agent`) | needed 41, available 106, reserve 24, owed 0, hold 70 counted, holders the embeddings 8 and whisper 3, key *agent*: *The coder didn't load: it needs 41 GiB, and 12 GiB is free for a load (106 GiB available, less the 24 GiB reserve and the 70 GiB make-room holds for Dan). Using memory now: the embeddings 8 GiB, whisper 3 GiB. On the Spark, `spark make-room --done` ends the hold.* |
| `loading` | loading Gemma, wait 30 s |
| `held_by_brake` | brake at 03:12, 19.6 available, release automatic |
| `held_by_brake` (waits for Dan) | the same, waiting for Dan: *Not loading the coder now: memory ran low at 03:12 (19.6 GiB available), and new loads stay paused until you release them: on the Spark, `make brake-release`.* |
| `gate_down` | none |
| `load_failed` | the engine said `failed to load model` |
| `load_failed` (deadline) | no engine text, deadline 180: *The coder started loading but didn't finish within 180 s. On the Spark, `spark status` shows the engine's last lines.* |
| `not_downloaded` | download 16 |
| `restarting` | wait 30 s |
| `llama_swap_down` | wait 30 s |
| `draining` (Dan) | 1 in flight, for make-room, wait 30 s |
| `draining` (`agent`) | 1 in flight, for make-room, wait 600 s: *The coder is being unloaded for make-room once its 1 request in flight finishes, and your 10 minutes ran out. It won't load for agent while make-room's hold stands.* |
| `footprint_suspect` | brake at 03:12, command `coder` |
| `model_not_found` | asked `qwen3.6-35b-a3b`; models the coder (`qwen3.8-27b`), Gemma, the embeddings, whisper — on-demand first, then residents, each in registry order |
| `too_many_requests` | key *agent* |
| `route_not_served` | none |
| `concurrency_limit` | model the coder: *Too many requests for the coder at once; try again in a moment.* |

**Tests** (`spark/tests/test_messages.py`, unless named):

- `test_each_refusal_reads_word_for_word` — parametrized over the table's fifteen rows and three
  extra: `refusal(code, moment).message` equals the expected text exactly.
- `test_every_code_has_its_status` — each of `CODES_409` gives 409, each of `CODES_503` 503;
  `model_not_found` and `route_not_served` 404; `too_many_requests` 429.
- `test_pi_wont_retry_a_refusal_after_a_wait` — for each of `CODES_409`, at each of the table's
  inputs, the text pi would show, `"409: " + json.dumps(body["error"])`, matches none of
  `PI_RETRY_PATTERNS` (case-insensitive); the same text with `503` matches, so the test can fail.
- `test_retry_after_follows_the_ruling` — `retry_after_s` is `RETRY_AFTER_S.get(code)` for every
  code; `headers()` holds `retry-after` exactly when it is set.
- `test_409s_and_503s_say_dont_retry` — every 409's and 503's `headers()` holds
  `x-should-retry: false`; no 404 or 429 holds it.
- `test_the_body_is_openais_error_shape_with_no_detail` — `body()` for `no_fit` is
  `{"error": {"message": <its sentence>, "code": "no_fit"}, "retry_after_s": 30}`; for `loading`,
  the same with no `retry_after_s`.
- `test_no_message_says_free_alone` — over every refusal, notification and confirmation the
  tests build, "free" never appears but in "free for a load".
- `test_agent_names_dans_processes_only_as_a_process_of_dans` — `no_fit` for `agent` at the Dan
  moment's holders: *Using memory now: a process of Dan's, 32 GiB, Gemma 27 GiB.*
- `test_sizes_round_against_the_load` — needed 40.2 shows *41 GiB*; available 47.9 shows *47 GiB*;
  a holder of 26.6 shows *27 GiB*; a brake reading of 19.64 shows *19.6 GiB*.
- `test_times_are_the_local_24_hour_clock` — 15:07 local, from an aware `datetime` in another
  zone, shows *15:07*.
- `test_each_notification_reads_word_for_word` — parametrized over plan.md's notification table
  (its *Example* column, asterisks dropped), each type with its row's inputs, and the follow-up
  `brake_fired` and the `room_hold_ended` variant (*… Reloading Gemma.*) given in full there:
  `notification(type, registry, …).message` equals it.
- `test_an_off_notification_sends_nothing` — the registry with `loaded: off` → `None`.
- `test_each_confirmation_reads_word_for_word` — parametrized over the plan's command table: each
  confirmation function's text equals its row's *What it says*.
- `test_models_are_named_by_label` — no refusal or notification built in these tests holds a
  model's registry name, but `model_not_found`'s list.
- `test_the_unknown_key_text_is_the_rulings` — `UNKNOWN_KEY` is the ruling's sentence.
- In `test_scenarios.py`: `test_the_notifications_page_is_generated_from_the_registry` —
  `render_notifications_page(the stack registry)` has twenty rows, each type's priority the
  registry's; `test_spark_docs_notifications_check_finds_a_stale_page` — a changed page → exit 1.

**Steps:**

- [ ] **Step 1:** the failing tests; run them: they fail (`spark.messages` missing).
- [ ] **Step 2:** `messages.py` and the docs command; **on the Spark**,
  `uv run --frozen --project spark spark docs notifications --write`; the tests pass;
  `make test lint`.
- [ ] **Step 3: Docs.** plan.md, *What you see in Phase 2a*, dated notes (2026-10-07, this plan's
  rulings): the generated table lives at `website/reference/notifications.md`, which is kept
  current; a refusal's body carries only `message` and `code` in `error`. (The statuses, the
  retry-afters and pi's and Open WebUI's handling are already in plan.md, from 2026-10-07; check
  them against what this task built.) A Revisions line records them.
- [ ] **Step 4: Commit.** **On the Spark:**

```bash
git add spark/src/spark/messages.py spark/tests/test_messages.py spark/src/spark/docs.py spark/tests/test_scenarios.py \
  website/reference/notifications.md website/design/plan.md
git commit -m "feat(spark): 🤖 every refusal, notification and confirmation, in Dan's words" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 7 [Spark]: memory and processes — MemFree, RssAnon, engines' pids, the top holders

**Files:**

- Create: `spark/src/spark/procs.py`, `spark/tests/test_procs.py`
- Modify: `spark/src/spark/memory.py`, `spark/tests/test_launch.py` (where `parse_meminfo`'s tests
  live)

**Interfaces:**

- `MemInfo` gains `free_gib: float | None = None` and `cached_gib: float | None = None`
  (`MemFree`, `Cached`), read when present; Phase 1's two fields unchanged.
- `boot_id(path: Path = Path("/proc/sys/kernel/random/boot_id")) -> str` — its text, stripped.
- `procs.rss_anon_gib(pid: int, *, proc: Path = Path("/proc")) -> float | None` — `RssAnon` from
  `<proc>/<pid>/status`, in GiB; None for a process gone or a file without it. Never `VmRSS`.
- `procs.port_of(proxy: str) -> int | None` — the port of `/running`'s `proxy` field
  (`http://127.0.0.1:801` → 801).
- `procs.engine_pid(port: int, *, spark_uid: int, proc: Path = Path("/proc")) -> int | None` — the
  process whose real uid is `spark_uid`, whose `comm` is `llama-server` or `whisper-server`, and
  whose argv holds `--port` followed by `port`.
- `procs.NVIDIA_APPS = ["nvidia-smi", "--query-compute-apps=pid,used_memory",
  "--format=csv,noheader,nounits"]`; `parse_nvidia_apps(text: str) -> dict[int, float | None]` —
  MiB to GiB; `[N/A]` reads as None.
- `procs.top_holders` returns `messages.Holder`s (Task 6).
- `procs.top_holders(loaded: dict[str, tuple[str, float]], nvidia: dict[int, float | None], *,
  engine_pids: set[int], dan_uids: set[int], proc: Path = Path("/proc"), limit: int = 3) ->
  list[messages.Holder]` — each loaded model by its label at what it holds now; each other process holding
  at least 1 GiB, by `<comm> (<user>)`, at the larger of its `nvidia-smi` figure and its
  `RssAnon`; the engines' own pids left out (they are the models); `dans` true for a process whose
  uid is in `dan_uids`; largest first, `limit` of them.

**Tests** (`spark/tests/test_procs.py`, unless named; `/proc` is a tree the test builds):

- `test_rss_anon_reads_the_anonymous_figure_not_vmrss` — `status` with `VmRSS: 4194304 kB` and
  `RssAnon: 1048576 kB` → 1.0.
- `test_rss_anon_of_a_gone_process_is_none` — no `<pid>` folder → None.
- `test_port_of_reads_runnings_proxy` — `http://127.0.0.1:801` → 801; `nonsense` → None.
- `test_engine_pid_finds_the_engine_by_its_port_among_sparks_processes` — pid 200, `comm`
  `llama-server`, argv `…\0--port\0801\0…`, `Uid:` spark's → `engine_pid(801)` is 200;
  `engine_pid(802)` is None.
- `test_an_engine_of_another_user_is_never_matched` — the same process with Dan's uid → None.
- `test_nvidia_apps_parse_and_na_reads_as_unknown` — `"200, 26624\n300, [N/A]\n"` → `{200: 26.0,
  300: None}`; `""` → `{}`.
- `test_top_holders_name_stack_models_by_label_and_others_by_comm_and_user` — Gemma holding 27;
  a `python3` of `chendaniely` (a Dan uid) with `RssAnon` 32 GiB → `[Holder("python3
  (chendaniely)", 32.0, True), Holder("Gemma", 27.0, False)]`.
- `test_a_cuda_job_counts_at_nvidia_smis_figure` — `RssAnon` 1 GiB, `nvidia-smi` 20 GiB → 20.
- `test_no_holder_is_named_by_its_command_line` — a process whose argv holds `--token-file-here`:
  no holder's name holds it.
- `test_an_engine_is_counted_once_as_its_model` — an engine pid with `nvidia-smi` 25 GiB, its
  model loaded: one holder, the model's label.
- In `test_launch.py`: `test_meminfo_reads_memfree_and_cached` — `MemFree:` and `Cached:` read;
  without them, both None and `parse_meminfo`'s Phase 1 fields still read.
- `test_boot_id_is_read_stripped` — a file `abc\n` → `abc`.

**Steps:**

- [ ] **Step 1:** the failing tests; run them: they fail (`spark.procs` missing; no `free_gib`).
- [ ] **Step 2:** `memory.py` and `procs.py`; the tests pass; `make test lint`.
- [ ] **Step 3: Commit.** **On the Spark:**

```bash
git add spark/src/spark/memory.py spark/src/spark/procs.py spark/tests/test_procs.py spark/tests/test_launch.py
git commit -m "feat(spark): 🤖 read MemFree, each engine's anonymous RSS and the top memory holders" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 8 [Spark]: service plumbing — systemd's sockets, the caller's uid, the watchdog, credentials

**Files:**

- Create: `spark/src/spark/sockets.py`, `spark/src/spark/protocols.py`,
  `spark/src/spark/sdnotify.py`, `spark/src/spark/credentials.py`, `spark/src/spark/serve.py`,
  `spark/tests/test_sockets.py`, `spark/tests/test_protocols.py`, `spark/tests/test_sdnotify.py`,
  `spark/tests/test_credentials.py`

**Interfaces:**

- `sockets.listen_fds(env: MutableMapping[str, str] = os.environ, pid: int | None = None) ->
  dict[str, socket.socket]` — requires `LISTEN_PID` equal to `pid` (default `os.getpid()`),
  `LISTEN_FDS` a count, `LISTEN_FDNAMES` that many colon-separated names; the sockets are fds 3
  upward, each made with `socket.socket(fileno=fd)`, so the family is the socket's own; it removes
  the three variables. `SocketsError` names what is missing or disagrees. Names in use: `front`;
  `status`, `control`.
- `protocols.make_protocol(*, peer_cred: bool, header_timeout_s: float | None) -> type` — a
  subclass of uvicorn's `H11Protocol` (`uvicorn.protocols.http.h11_impl`). With `peer_cred`, on
  `connection_made` it reads `SO_PEERCRED` from an `AF_UNIX` transport's socket and wraps the app,
  so each request's `scope["extensions"]["peer_cred"]` is `{"pid": int, "uid": int, "gid": int}`;
  a TCP connection gets no such key. With `header_timeout_s`, a connection whose first request's
  headers aren't complete in time is closed. `protocols.peer(scope) -> PeerCred | None`, where
  `PeerCred(pid: int, uid: int, gid: int)`. `TESTED_AGAINST = {"pypi:uvicorn": "0.54.0"}`.
- `sdnotify.notify(message: str, env: Mapping[str, str] = os.environ) -> bool` — a datagram to
  `NOTIFY_SOCKET` (a path, or `@name` for an abstract one); False, quietly, when it is unset;
  `ready()`, `stopping()`; `watchdog_interval_s(env) -> float | None` — half `WATCHDOG_USEC`, only
  when `WATCHDOG_PID` is unset or ours; `async watchdog_loop(interval_s: float)` — `WATCHDOG=1`
  every interval, from the event loop, so a stuck loop sends none.
- `credentials.read_credential(name: str, env: Mapping[str, str] = os.environ) -> str` — the file
  `$CREDENTIALS_DIRECTORY/<name>`, one trailing newline dropped; refused when the variable or the
  file is missing, or the value is empty or holds anything but printable ASCII without spaces.
  `CredentialError`'s text names the credential, never its content.
  `read_header_credential(name) -> tuple[str, str]` — exactly one `Name: value` line.
  `parse_digests(text: str) -> dict[str, bytes]` — `<key name> <64 lowercase hex>` lines, `#`
  comments and blank lines skipped; a duplicate name or a malformed line refused by line number,
  its content never shown. `match_key(presented: str, digests: dict[str, bytes]) -> str | None` —
  SHA-256 of the presented key, compared with `hmac.compare_digest` against every digest, in full,
  whatever matches first.
- `serve.run_servers(pairs: list[tuple[ASGIApp, list[socket.socket]]], *, protocol: type,
  graceful_s: float) -> None` — one event loop; one `uvicorn.Server` per pair, each with
  `log_config=None`, `access_log=False`, `server_header=False`, `date_header=False`,
  `proxy_headers=False`, `lifespan="off"`, `timeout_graceful_shutdown=graceful_s`; `READY=1` once
  every server listens; the watchdog loop when `watchdog_interval_s()` gives one; `STOPPING=1` on
  the way out; returns when SIGTERM or SIGINT arrives and the servers have stopped.

**Tests:**

`spark/tests/test_sockets.py`:

- `test_listen_fds_takes_systemds_named_sockets` — a subprocess given a TCP listener and an
  `AF_UNIX` listener as fds 3 and 4, with `LISTEN_PID` its own pid, `LISTEN_FDS=2`,
  `LISTEN_FDNAMES=front:status`: it reports `front` as `AF_INET` and `status` as `AF_UNIX`, and
  none of the three variables left in its environment.
- `test_listen_fds_refuses_another_processs_sockets` — `LISTEN_PID=1` → `SocketsError` naming
  `LISTEN_PID`.
- `test_listen_fds_refuses_a_count_and_names_that_disagree` — `LISTEN_FDS=2`,
  `LISTEN_FDNAMES=front` → refused.
- `test_a_tcp_socket_from_systemd_keeps_tcp_nodelay` — a TCP listener taken through `listen_fds`,
  served with asyncio's `create_server(sock=…)`: the accepted connection's socket has
  `TCP_NODELAY` set.

`spark/tests/test_protocols.py` (a real uvicorn on a short `AF_UNIX` path, and on TCP):

- `test_the_gate_receives_each_callers_uid` — an app that answers the scope's peer uid and pid:
  over the Unix socket, from this process, it answers `os.getuid()` and `os.getpid()`.
- `test_a_tcp_request_carries_no_peer_cred` — the same app over TCP answers `none`.
- `test_a_connection_that_never_sends_its_headers_is_closed` — `header_timeout_s=0.5`; a raw
  client sends `GET / HTTP/1.1\r\n` and nothing more: the server closes the connection within 2 s.
- `test_a_request_that_sends_its_headers_in_time_is_served` — the same timeout, headers sent at
  once: 200.

`spark/tests/test_sdnotify.py`:

- `test_sd_notify_sends_ready_watchdog_and_stopping` — a datagram socket at `NOTIFY_SOCKET`
  receives `READY=1`, `WATCHDOG=1` and `STOPPING=1`, in order.
- `test_notify_without_notify_socket_is_a_quiet_no` — returns False, raises nothing.
- `test_the_watchdog_interval_is_half_watchdog_usec_and_only_for_our_pid` — `WATCHDOG_USEC=30000000`
  → 15.0; with `WATCHDOG_PID=1` → None; unset → None.
- `test_the_watchdog_loop_pings_only_while_the_loop_runs` — interval 0.05 s: at least 3 pings in
  0.3 s; then a coroutine that blocks the loop for 0.3 s: no ping arrives during it.

`spark/tests/test_credentials.py`:

- `test_a_credential_is_read_from_its_directory_and_never_printed` — `llamaswap-key` holding
  `abc123\n` → `abc123`; holding `ab\x07c`, or `ab c`: `CredentialError` whose text holds
  `llamaswap-key` and not the value.
- `test_a_missing_or_empty_credential_is_refused_by_name` — no `CREDENTIALS_DIRECTORY`: the error
  names it; no file: names the credential; an empty file: names the credential.
- `test_the_header_credential_reads_one_header_line` — `Authorization: Bearer tk_x\n` →
  `("Authorization", "Bearer tk_x")`; two lines, or no colon: refused.
- `test_digests_parse_and_a_duplicate_name_is_refused_by_line_number` — a comment, `dan-mac
  <hex>`, `agent <hex>` → two entries; `agent` again on line 4 → refused, the text holding
  `line 4` and no hex; a 63-character digest → refused by its line.
- `test_match_key_compares_every_digest_and_returns_the_name` — the digests of `k1` (`dan-mac`) and
  `k2` (`agent`): `match_key("k2")` → `agent`; `hmac.compare_digest`, spied on, is called twice for
  it and twice for `k1`.
- `test_match_key_refuses_an_unknown_key` — `k3` → None; `""` → None.

`spark/tests/test_sockets.py` (serve):

- `test_run_servers_serves_two_apps_on_two_sockets_and_shuts_down_in_time` — two `AF_UNIX` sockets,
  two apps answering `a` and `b`, run in a subprocess: each answers; with a stream left open that
  never ends, SIGTERM ends the process within `graceful_s` (0.5) plus 1.5 s.
- `test_run_servers_says_ready_once_both_listen` — the `NOTIFY_SOCKET` stand-in receives exactly
  one `READY=1`, after both sockets answer.

**Steps:**

- [ ] **Step 1:** the failing tests; run them: they fail (the five modules missing).
- [ ] **Step 2:** the five modules; the tests pass, also on the Mac's shorter socket paths (Task
  50); `make test lint`, including Task 3's `TESTED_AGAINST` test, which now finds
  `protocols.TESTED_AGAINST`.
- [ ] **Step 3: Commit.** **On the Spark:**

```bash
git add spark/src/spark/sockets.py spark/src/spark/protocols.py spark/src/spark/sdnotify.py \
  spark/src/spark/credentials.py spark/src/spark/serve.py spark/tests/test_sockets.py \
  spark/tests/test_protocols.py spark/tests/test_sdnotify.py spark/tests/test_credentials.py
git commit -m "feat(spark): 🤖 systemd's sockets, each caller's uid, the watchdog and credentials for the new services" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 9 [Spark]: the gate's protocol, and the CLI's standard-library client

**Files:**

- Create: `spark/src/spark/gateproto.py`, `spark/src/spark/gateclient.py`,
  `spark/tests/test_gateclient.py`
- Modify: `spark/src/spark/paths.py`, `spark/tests/test_tested_against.py` (the import check gains
  `spark.gateclient`)

**Interfaces:**

- `paths` gains, each overridable by its variable: `GATE_STATUS_SOCKET`
  (`/run/local-ai/gate-status.sock`, `SPARK_GATE_STATUS`), `GATE_CONTROL_SOCKET`
  (`/run/local-ai/gate-control.sock`, `SPARK_GATE_CONTROL`), `GATE_STATE`
  (`/var/lib/local-ai/gate`, `SPARK_GATE_STATE`), `LAUNCH` (`/var/lib/local-ai/launch`,
  `SPARK_LAUNCH`), `WHISPER_TMP` (`/var/lib/local-ai/whisper-tmp`), `VALUES`
  (`/etc/local-ai/values.env`), `FRONT_URL` (`http://127.0.0.1:9100`); `LLAMASWAP_URL`'s default
  becomes `http://127.0.0.1:900`.
- `gateproto` — the gate's constants (the Global Constraints' values): `MAX_REQUEST_BYTES =
  65536`, `REQUEST_TIMEOUT_S = 5.0`, `LOAD_CALL_TIMEOUT_S = 200.0`, `PING_EVERY_S = 1.0`,
  `GATE_DOWN_AFTER_S = 5.0`, `DRAIN_GRACE_S = 30.0`, `QUIET_S = 60`, `APPLY_DEADLINE_S = 900`,
  `ACTIVITY_EVERY_S = 1.0`, `ACTIVITY_STALE_S = 3.0`, `SESSIONS_PER_UID = 8`, `SESSION_TTL_S =
  43200`, `REFUSAL_HISTORY = 50`, `NTFY_TIMEOUT_S = 5.0`, `BURST_WINDOW_S = 600`,
  `BACK_UP_AFTER_S = 60`, `RELEASE_AFTER_S = 300`, `AUTO_RELEASE_EVERY_S = 3600`,
  `NOTIFIER_EVERY_S = 300`.
- `Route(socket: "status" | "control", method: str, path: str, callers: "front" | "users" |
  "owner" | "admin")` and `ROUTES`, the table below; message shapes as `TypedDict`s. JSON over
  HTTP/1.1 on the Unix sockets.

  | Socket | Route | Callers | Request → answer |
  |---|---|---|---|
  | status | `POST /v1/admit` | front | `{model, key, deadline, request_id}` → held until ready or refused; always `200`, `{ok: true, model}` or `{ok: false, code, status, message, retry_after_s}`; any other status is the gate's own failure |
  | status | `GET /v1/front/events` | front | NDJSON, kept open: `{op: "hello", gate_started_at}`, `{op: "state", ready, starting, draining}`, `{op: "drain", model, drain_id}`, `{op: "undrain", model, drain_id}`, `{op: "unloaded", model}`, `{op: "ping", at}` every second |
  | status | `POST /v1/front/inflight` | front | a whole snapshot, never a delta: `{seq, front_started_at, models: {name: {count, oldest_started_at, last_end_at}}, draining}` |
  | status | `POST /v1/front/drained` | front | `{model, drain_id}` |
  | status | `GET /v1/status` | users | `StatusView`, filtered for the caller |
  | status | `POST /v1/sessions` · `POST /v1/sessions/{id}/renew` · `DELETE /v1/sessions/{id}` | owner | `{model, pid, label}` → `{id, expires_at}`; `spark-admin` may end any |
  | control | `GET /v1/status` | admin | the full `StatusView` |
  | control | `POST /v1/load` · `POST /v1/unload` | admin | `{model}` → the confirmation's fields, or a refusal |
  | control | `POST /v1/pin` · `DELETE /v1/pin/{model}` | admin | `{model, until}` (`until` null for no end) |
  | control | `POST /v1/make-room/plan` · `POST /v1/make-room` | admin | `{size_gib}` or `{all: true}` → a `RoomPlan` with `plan_id`; `{plan_id, for_s}` → `{unloaded, free_gib, hold_gib, until}` |
  | control | `POST /v1/release` | admin | → `{room: bool, brake: bool, reloading: [labels]}` |
  | control | `GET /v1/quiet` | admin | → `{quiet_for_s, last_label, inflight: [{model, model_label, key_label, age_s}]}` (`last_label` the model that answered last) |
  | control | `POST /v1/drain-all` · `POST /v1/undrain-all` | admin | apply's "drain now" |
  | control | `POST /v1/apply/begin` · `POST /v1/apply/end` | admin | `{restarting: [units]}`; *end* re-reads the registry, reloads the residents, sends `apply_restarted` |
  | control | `GET /v1/logs/{model}?n=` | admin | `{lines: [...]}`, the engine's last lines, read with the gate's key |

  There is no cancel route: the front drops its admit call when its client goes, and the gate
  takes the dropped call as the cancel.

- `StatusView` (also `--json`'s shape, for 2b's menu bar): `host`, `at`; `memory` (`total_gib`,
  `available_gib`, `brake_gib`, `warn_gib`, `above_brake_gib`, `reserve_gib`, `owed_gib`,
  `held_gib`, `free_for_a_load_gib`, `unaccounted_gib`); `models` (each: `name`, `label`,
  `resident`, `footprint_gib`, `state` — `ready`, `starting`, `draining`, `not_loaded` — `inflight`,
  `oldest_request_s`, `last_use`, `pinned_until`, `sessions`, `brake_mark` with `at` and
  `seen_gib`); `waiting` (`key_label`, `model`, `waited_s`, `wait_s`, `why` — `memory`, `brake`,
  `slot`, `dan`, `restart`, `llama_swap`); `paused` (`since`, `available_gib`, `releases_at`,
  `waits_for_dan`, `last_fired`, `last_released`) or null; `held` (`size_gib`, `until`) or null;
  `pins`; `sessions`; `recent` (`at`, `text`, `code`, `model`, `key_label`); `health` (`front`,
  `gate`, `brake` with `key_checked_at`, `llama_swap`, `ntfy` with `failing_since`); `problems`.
- `gateclient.GateClient(path: Path, timeout_s: float)`: `get(route) -> dict`, `post(route, body:
  dict) -> dict`, `delete(route) -> dict`, `stream(route) -> Iterator[dict]`. `GateUnavailable` —
  no socket, a refused connection, or no answer within `timeout_s` (a socket systemd holds while
  the gate is down); `GateForbidden` — `EACCES` on connect, its text saying which group may;
  `GateRefused(status: int, body: dict)` — any other non-2xx answer. `http.client` with an
  `AF_UNIX` connect; nothing else imported.

**Tests** (`spark/tests/test_gateclient.py`; a standard-library stand-in server on a short
`AF_UNIX` path):

- `test_the_client_speaks_http_over_a_unix_socket` — `get("/v1/status")` returns the stand-in's
  `{"ok": true}`; `post("/v1/load", {"model": "coder"})` sends that JSON with
  `content-type: application/json` and returns the stand-in's echo.
- `test_a_missing_socket_reads_as_gate_unavailable` — a path that doesn't exist →
  `GateUnavailable` naming it.
- `test_a_socket_nobody_answers_times_out_as_gate_unavailable` — a listening socket that is never
  accepted, timeout 0.5 s → `GateUnavailable` within 2 s.
- `test_a_closed_socket_says_who_may_use_it` — `connect` raising `PermissionError` (patched) →
  `GateForbidden`, its text naming `spark-admin`.
- `test_a_refusal_comes_back_as_gate_refused_with_its_body` — the stand-in answers 403 with
  `{"message": "not yours"}` → `GateRefused(403, {"message": "not yours"})`.
- `test_a_stream_yields_each_line_as_json` — the stand-in streams three NDJSON lines → three dicts.
- `test_every_route_names_its_socket_and_callers` — every `ROUTES` entry's socket and callers are
  from their sets; the `front` routes are exactly the four the table gives, all on `status`.
- In `test_tested_against.py`, the import check now includes `spark.gateclient`.

**Steps:**

- [ ] **Step 1:** the failing tests; run them: they fail (the modules missing).
- [ ] **Step 2:** `paths`, `gateproto`, `gateclient`; the tests pass; `make test lint`.
- [ ] **Step 3: Commit.** **On the Spark:**

```bash
git add spark/src/spark/paths.py spark/src/spark/gateproto.py spark/src/spark/gateclient.py \
  spark/tests/test_gateclient.py spark/tests/test_tested_against.py
git commit -m "feat(spark): 🤖 the gate's protocol, and a standard-library client for the CLI" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 10 [Spark]: tickets, and spark launch starts nothing without one

**Files:**

- Create: `spark/src/spark/tickets.py`, `spark/tests/test_tickets.py`
- Modify: `spark/src/spark/launch.py`, `spark/src/spark/admission.py` (its docstring: the
  zero-wait backstop), `spark/tests/test_launch.py`

**Interfaces:**

- `tickets.issue(folder: Path, model: str, deadline: float, *, now: float) -> None` —
  `<folder>/tickets/<model>.json`, `{model, issued_at, deadline, nonce}`, written whole, mode
  `0600`.
- `tickets.claim(folder: Path, model: str, now: float) -> Claim(ok: bool, code: str | None, why:
  str)` — renames the ticket to a name of its own before reading it, then removes it, so only one
  claim of a ticket can win; codes `no_ticket` (none, damaged, or another model's) and
  `ticket_expired` (`deadline < now`). The ticket is gone whatever the outcome.
- `tickets.withdraw(folder: Path, model: str) -> bool` — the gate removes one it issued that wasn't
  used.
- `launch.main_launch(argv, *, registry: Path = paths.REGISTRY, state: Path = paths.STATE, launch:
  Path = paths.LAUNCH, hf_home: str = render.HF_HOME, clock: Callable[[], float] = time.time) ->
  int` — in this order: the registry loads (else refused `registry`); the model is known (else exit
  2); its ticket is claimed; the brake's hold (`held_by_brake`); Phase 1's zero-wait fit
  (`no_fit`); every file the model's `source` names exists under `hf_home` (`not_downloaded`, its
  reason naming `make pull`); its refusal record cleared; its `oom_score_adj`; exec, with every
  `LLAMASWAP_KEY_*` variable dropped. A refusal exits 3, prints `spark: not starting <model>:
  <reason>` on stderr and records it. Never a sleep or a retry.
- `launch.record_refusal(launch: Path, model, code, reason)`, `read_refusal(launch, model) -> dict
  | None`, `clear_refusal(launch, model)` — `<launch>/refusals/<model>.json`, `{at, model, code,
  reason}`, written whole. Phase 1's single `last-refusal.json` goes.

**Tests** (`spark/tests/test_launch.py` and `spark/tests/test_tickets.py`; `execvpe` replaced):

- `test_launch_refuses_without_a_ticket_and_records_why` — the fixture registry, room to fit, no
  hold, no ticket → exit 3; stderr `spark: not starting coder: no admission ticket from the gate`;
  `refusals/coder.json` with code `no_ticket`; no exec.
- `test_a_ticket_is_used_up_by_one_start` — `issue(…, "coder", deadline=now+60)` → exec called, and
  the ticket gone; a second launch → `no_ticket`.
- `test_two_claims_at_once_win_once` — two processes claim one ticket at once: exactly one `ok`.
- `test_an_expired_ticket_starts_nothing` — deadline `now − 1` → `ticket_expired`; the file gone.
- `test_a_ticket_for_another_model_starts_nothing` — a ticket for `embed`; launching `coder` →
  `no_ticket`; `embed`'s ticket still there.
- `test_a_damaged_ticket_starts_nothing` — `{` in the file → `no_ticket`; the file gone.
- `test_launch_keeps_the_hold_check_as_a_backstop` — a ticket and a hold → `held_by_brake`; the
  ticket used up.
- `test_launch_keeps_a_zero_wait_fit_check` — a ticket, 30 GiB available, the coder's 28, reserve
  24 → `no_fit`, with Phase 1's reason text.
- `test_launch_refuses_not_downloaded_naming_make_pull` — a ticket, room to fit, the model's file
  missing under `hf_home` → `not_downloaded`, the reason holding `make pull`.
- `test_engines_lose_the_internal_keys_too` — `LLAMASWAP_KEY_FRONT`, `LLAMASWAP_KEY_GATE`,
  `LLAMASWAP_KEY_BRAKE` and `PATH` set → the exec'd environment holds `PATH` and none of the three.
- `test_launch_never_waits` — `time.sleep` patched to raise: no launch path calls it.
- `test_refusal_records_are_one_per_model` — refusals for `coder` and `embed` → two files;
  `read_refusal(…, "coder")` is `{at, model: "coder", code, reason}`.
- `test_withdraw_removes_an_unused_ticket` — `issue` then `withdraw` → True, the file gone;
  again → False.

**Steps:**

- [ ] **Step 1:** the failing tests; run them: they fail (`spark.tickets` missing; launch starts
  without a ticket).
- [ ] **Step 2:** `tickets.py` and launch; the tests pass; `make test lint`.
- [ ] **Step 3: Commit.** **On the Spark:**

```bash
git add spark/src/spark/tickets.py spark/src/spark/launch.py spark/src/spark/admission.py \
  spark/tests/test_tickets.py spark/tests/test_launch.py
git commit -m "feat(spark): 🤖 spark launch starts a model only with the gate's ticket" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 11 [Spark]: the gate's async llama-swap client, and a v257 stand-in for the tests

**Files:**

- Create: `spark/src/spark/llamaswap_async.py`, `spark/tests/fake_llamaswap.py`,
  `spark/tests/test_llamaswap_async.py`

**Interfaces:**

- `AsyncLlamaSwap(base_url: str, key: str, *, load_timeout_s: float)` — one `httpx.AsyncClient`,
  `trust_env=False`, `follow_redirects=False`, the key only in `Authorization: Bearer`; the gate
  builds it with `load_timeout_s = load_timeout_for(render.HEALTH_CHECK_TIMEOUT_S)`.
- `load_timeout_for(health_check_timeout_s: float) -> float` — the load's deadline plus 20 s: the
  5 s llama-swap takes to kill a stuck start, and a margin (`gateproto.LOAD_CALL_TIMEOUT_S`, 200,
  for 180).
  - `async running() -> list[Running]` — v257's shape checked, as Phase 1's client does.
  - `async load(model) -> LoadOutcome` — `GET /upstream/<model>/health`, read timeout
    `load_timeout_s`; `LoadOutcome.READY`; `LoadOutcome.failed(status, text)` with the body's
    text; `LoadOutcome.UNKNOWN` on a timeout or a dropped call, so the caller keeps the load
    counted as starting.
  - `async unload(model) -> None` — `POST /api/models/unload/<model>`.
  - `async last_lines(model, n: int = 20, read_s: float = 1.0) -> list[str]` —
    `GET /logs/stream/<model>` read for `read_s`, then closed; its last `n` lines, every
    non-printable character escaped.
  - Errors: Phase 1's `LlamaSwapUnreachable` and `LlamaSwapAnswered`.
  - `TESTED_AGAINST = {"llama-swap": "v257"}`.
- `fake_llamaswap.FakeLlamaSwap(keys: set[str], models: list[str])` — an ASGI app with a
  controller: `script_start(model, delay_s=0.0, outcome="ready" | "exited" | "timeout")`,
  `set_state(model, state)`, `stream(model, chunks, delay_s)`, `log(model, lines)`, and what it
  saw (`requests`, with their headers). It answers as v257's source does: `/running`'s shape and
  states; `GET /upstream/<m>/health` synchronous, ending 200 once ready, or 500 with
  `upstream command exited prematurely` or `health check timed out`; `POST
  /api/models/unload/<m>` returning `OK` once stopped, cutting that model's streams in flight and
  failing its waiters with 500 `group: model unloaded`; the inference routes streaming SSE; ten
  requests per model, the eleventh a 429 with `Retry-After: 1` and code `concurrency_limit`; 401
  without a key it knows; `GET /health` unkeyed. `serve_fake(fake)` — an async context manager
  running it on a real uvicorn at 127.0.0.1:0 and yielding its URL. `TESTED_AGAINST =
  {"llama-swap": "v257"}`; each behaviour cites its source line (`llamaswap-v257-gate-research`'s
  §4–§8) in a comment.

**Tests** (`spark/tests/test_llamaswap_async.py`, against `serve_fake`):

- `test_running_reads_v257s_shape` — Gemma ready, the coder starting →
  `[Running("gemma", "ready"), Running("coder", "starting")]`; a body without `running` →
  `LlamaSwapAnswered`.
- `test_the_load_timeout_is_the_deadline_plus_20` — `load_timeout_for(180)` is 200, which is
  `gateproto.LOAD_CALL_TIMEOUT_S`.
- `test_load_waits_past_every_other_timeout` — a start of 1.0 s, `load_timeout_s` 3 → `READY`.
- `test_a_load_call_that_times_out_reads_as_unknown_not_failed` — a start of 1 s,
  `load_timeout_s` 0.2 → `UNKNOWN`.
- `test_a_failed_start_reads_as_failed_with_its_text` — `outcome="exited"` → `failed(500, …)`, the
  text holding `upstream command exited prematurely`.
- `test_unload_returns_once_the_engine_is_gone` — a stop of 0.3 s → returns after at least 0.3 s,
  and `/running` no longer lists the model.
- `test_last_lines_reads_a_bounded_tail_and_closes` — fifty history lines, then a stream left open
  → the last twenty, within 0.6 s; an escape character arrives escaped.
- `test_no_redirect_is_followed_with_the_key` — the fake answers 302 to a stand-in that records
  headers: the stand-in sees nothing; the client raises `LlamaSwapAnswered`.
- `test_an_environment_proxy_is_ignored` — `HTTP_PROXY` pointing at a recording stand-in: it
  records nothing, and the call reaches the fake.
- `test_the_stand_in_answers_as_v257_does` — no key → 401; an eleventh request for one model →
  429, `Retry-After: 1`, code `concurrency_limit`; an unload during a stream ends the stream early;
  a waiter for a starting model gets 500 `group: model unloaded` when it is unloaded.

**Steps:**

- [ ] **Step 1:** the failing tests; run them: they fail (the modules missing).
- [ ] **Step 2:** the client and the stand-in; the tests pass; `make test lint`.
- [ ] **Step 3: Commit.** **On the Spark:**

```bash
git add spark/src/spark/llamaswap_async.py spark/tests/fake_llamaswap.py spark/tests/test_llamaswap_async.py
git commit -m "feat(spark): 🤖 the gate's async llama-swap client, tested against a v257 stand-in" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 12 [Spark]: the gate's state, kept across restarts

**Files:**

- Create: `spark/src/spark/gate/__init__.py`, `spark/src/spark/gate/state.py`,
  `spark/tests/test_gate_state.py`

**Interfaces:**

- `ModelRecord(name: str, footprint_gib: float, loaded_at: float, load_fall_gib: float | None,
  fall_flagged: bool, rss_anon_at_load_gib: float | None, last_use: float, state: str)` —
  `footprint_gib` is the registry's at load, never a newer one's; `state` is `starting`, `ready` or
  `draining`.
- `Pin(model: str, until: float | None, by_uid: int)`; `Session(id: str, uid: int, pid: int, model:
  str, label: str, started_at: float, renewed_at: float)`; `RoomHold(size_gib: float, until: float
  | None, boot_id: str, created_at: float, unloaded: list[str])`; `BrakeMark(model: str, at: float,
  seen_gib: float)`; `RefusalRecord(at: float, model: str, key: str | None, uid: int | None, code:
  str, message: str)`.
- `GateState(models: dict[str, ModelRecord], pins: dict[str, Pin], sessions: dict[str, Session],
  room_hold: RoomHold | None, brake_marks: dict[str, BrakeMark], last_auto_release_at: float |
  None, brake_events_seq: int, notified: set[str], notify_failing_since: float | None, refusals:
  deque[RefusalRecord] (maxlen REFUSAL_HISTORY), clean_shutdown: bool, saved_at: float, boot_id:
  str)`.
- `STATE_FILE = "state.json"`; `load_state(folder: Path) -> tuple[GateState, str | None]` — a
  missing file gives a fresh state and None; a damaged one, a fresh state and a problem naming the
  file (for `spark status`); never raises. `save_state(folder: Path, state: GateState) -> None` —
  written whole, the file and its folder fsynced, as `hold.write_hold` does.
- `restore(state, running: list[Running], *, now: float, boot_id: str, registry) ->
  tuple[GateState, RoomHold | None]` — a model `/running` shows starting is `starting`, at the
  registry's footprint if it wasn't recorded; every loaded model's `last_use` is `now`; a model
  `/running` doesn't list is dropped; a room hold from another boot ends, and is returned so the
  caller sends `room_hold_ended`; pins, sessions, marks and refusals stay.
- `Emit` — the protocol every gate module notifies through: `emit(type: str, event_key: str,
  **fields) -> None`.

**Tests** (`spark/tests/test_gate_state.py`):

- `test_pins_sessions_holds_and_marks_survive_a_restart` — a state with a pin on the coder until
  T, a session, a 40 GiB room hold with no end, a brake mark (the coder, 03:12, 26), a last
  automatic release and three refusals: `save_state` then `load_state` gives an equal state and
  no problem.
- `test_after_a_restart_every_loaded_models_last_use_is_now` — Gemma's `last_use` T − 3600;
  `restore` with Gemma ready, at T → T.
- `test_a_model_left_starting_counts_as_starting_after_a_restart` — `/running` shows the coder
  starting, the state has no record → a `starting` record at the registry's footprint.
- `test_a_model_gone_from_running_is_dropped` — a record for the coder, `/running` empty → no
  record.
- `test_a_room_hold_ends_at_the_next_boot` — a hold from boot `b1`, restored on `b2` → no hold,
  and the old one returned.
- `test_a_damaged_state_file_starts_fresh_and_says_so` — `{` → a fresh state, and a problem naming
  `state.json`.
- `test_each_loaded_model_keeps_the_footprint_it_was_loaded_with` — a record at 33, the registry
  now 41 → still 33.
- `test_state_is_written_whole_and_on_disk_before_it_returns` — `os.fsync`, spied on, is called
  for the file and its folder; a write that fails half-way leaves the previous file whole.
- `test_the_refusal_history_keeps_the_last_50` — 60 added → the newest 50, oldest first.

**Steps:**

- [ ] **Step 1:** the failing tests; run them: they fail (`spark.gate` missing).
- [ ] **Step 2:** `gate/state.py`; the tests pass; `make test lint`.
- [ ] **Step 3: Commit.** **On the Spark:**

```bash
git add spark/src/spark/gate/__init__.py spark/src/spark/gate/state.py spark/tests/test_gate_state.py
git commit -m "feat(spark): 🤖 the gate's state, kept across restarts" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 13 [Spark]: the gate's notifications

**Files:**

- Create: `spark/src/spark/gate/notify.py`, `spark/src/spark/gate/units.py`,
  `spark/tests/test_gate_notify.py`, `spark/tests/test_gate_units.py`

**Interfaces:**

- `Notifier(publish: Callable[[Notification], Awaitable[None]], registry, state: GateState,
  clock)` implements `Emit`. `emit(type, event_key, **fields)` builds the text with
  `messages.notification` and returns at once; a type the registry has `off` is dropped; an
  `event_key` already in `state.notified` is dropped, so a restart never repeats one. A queue,
  served by a task of its own, publishes each, bounded by `NTFY_TIMEOUT_S`; a failure sets
  `state.notify_failing_since` to the first failure's time, and the next success clears it.
  `refused` collapses: per (model, key, code), the first goes at once, the repeats within
  `BURST_WINDOW_S` are counted and go as one when it closes (*Didn't load the coder for agent 4
  more times since 09:12: same reason.*); a different code goes at once.
- `NtfyPublisher(url: str, topic: str, header: tuple[str, str])` — `POST <url>/<topic>`, the text
  as the body, `Priority: high | default | low`, the token header from `read_header_credential(
  "ntfy-token")`, httpx with `trust_env=False`; it never logs the URL, the topic or the header.
- `ingest_brake_events(path: Path, state) -> list[tuple[str, str, dict]]` — the brake's
  `events.jsonl` lines after `state.brake_events_seq`: an episode's first unload becomes
  `brake_fired` (what was unloaded, and in what state), each later unload in it a short
  `brake_fired` follow-up; a line with `sent_by_brake: true` is skipped, the brake having sent it;
  an unload of a model that was `starting` becomes a `BrakeMark` with what it was seen using;
  `brake_events_seq` advances.
- `MemoryWarning` — `check(available_gib, warn_gib) -> bool`: true once per fall under the warn
  line, re-armed only above it.
- `gate/units.py`: `unit_state(unit: str, run=subprocess.run) -> UnitState(active: bool,
  n_restarts: int, active_since: float | None, inactive_since: float | None, result: str)`, from
  `systemctl show`, called off the event loop. `BackUpWatch(emit, clock)`: `observe(unit, st)` —
  when `n_restarts` rises, a crash is noted; once the unit has stayed active `BACK_UP_AFTER_S`
  since, `back_up` with its downtime (`active_since − inactive_since`); a further crash first
  resets it. `gate_restarted(state, now)` — when the previous run's `clean_shutdown` is false,
  `back_up` for the gate `BACK_UP_AFTER_S` after start, its downtime `now − saved_at`.
  `LlamaSwapWatch` — `llama_swap_down` once per outage, when llama-swap hasn't answered for
  `LLAMA_SWAP_HUNG_S` (10 s, this plan's value) while its unit stays active with no new restart
  (a crash is the notifier's to report); re-armed once it answers again.

**Tests** (injected clock; a recording `publish`):

`spark/tests/test_gate_notify.py`:

- `test_every_type_goes_at_its_registry_priority` — each type the gate sends, emitted once: each
  publish carries the registry's priority.
- `test_an_off_type_sends_nothing` — `loaded: off` → no publish.
- `test_one_notification_per_event_even_across_a_restart` — `brake_released` with event key
  `release:3`, emitted twice → one publish; a new `Notifier` over the saved state, emitting it
  again → none.
- `test_a_burst_of_identical_refusals_collapses_with_a_count` — `refused` for the coder, `agent`,
  `footprint_suspect` at 09:12 → published at once; four more by 09:20 → nothing; at 09:22 one
  publish, *Didn't load the coder for agent 4 more times since 09:12: same reason.*; a `no_fit` for
  the same pair at 09:15 → published at once.
- `test_an_ntfy_out_of_reach_stalls_nothing_and_shows_failing_since` — a `publish` that hangs:
  `emit` returns within 10 ms; after the (injected) timeout, `notify_failing_since` holds the
  first failure's time; a later success clears it.
- `test_the_token_travels_only_in_a_header` — `NtfyPublisher` against a recording stand-in: the
  request's path is `/<topic>`, the token only in `Authorization`; the captured log holds neither.
- `test_brake_events_become_brake_fired_and_its_follow_ups` — events: fired at 03:12 at 19.6;
  unload of the coder while `starting`; at 03:13 unloads of Gemma and the embeddings, `idle` →
  two notifications whose texts are Task 6's `brake_fired` and its follow-up; a `BrakeMark` for the
  coder; `brake_events_seq` at the last line.
- `test_what_the_brake_sent_itself_is_not_sent_again` — the same events marked `sent_by_brake` →
  no publish; the seq advanced.
- `test_memory_warning_once_per_fall` — readings 30, 27.4, 27, 26, 29, 27 → two warnings, at 27.4
  and at the second 27.

`spark/tests/test_gate_units.py`:

- `test_unit_state_reads_systemctl_show` — a stand-in `run` giving `ActiveState=active`,
  `NRestarts=2`, two timestamps and `Result=success` → `UnitState(True, 2, …, "success")`.
- `test_back_up_waits_for_60_seconds_of_running` — `NRestarts` 0 → 1 at T, inactive since T,
  active since T + 12: nothing at T + 30; at T + 73, `back_up`, *… after 12 s down.*; a second
  crash at T + 40 → no `back_up` for the first.
- `test_the_gate_announces_its_own_return_after_an_unclean_stop` — `clean_shutdown` false,
  `saved_at` T − 12, started at T → at T + 60 the gate's `back_up`, *… after 12 s down.*
- `test_llama_swap_down_once_per_outage_when_it_hangs` — unanswered for 10 s with its unit active
  and no new restart → one `llama_swap_down`; still unanswered at 60 s → no second; answering, then
  unanswered again → another; unanswered with `NRestarts` risen → none (the notifier's).

**Steps:**

- [ ] **Step 1:** the failing tests; run them: they fail (the modules missing).
- [ ] **Step 2:** `notify.py` and `units.py`; the tests pass; `make test lint`.
- [ ] **Step 3: Commit.** **On the Spark:**

```bash
git add spark/src/spark/gate/notify.py spark/src/spark/gate/units.py spark/tests/test_gate_notify.py spark/tests/test_gate_units.py
git commit -m "feat(spark): 🤖 the gate's notifications: one per event, at the registry's priorities" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 14 [Spark]: the gate's admission — the queue, one load at a time, tickets, refusals

**Files:**

- Create: `spark/src/spark/gate/admission.py`, `spark/tests/test_gate_admission.py`

**Interfaces:**

- `AdmitRequest(id: str, model: str, key: ClientKey | None, deadline: float)` — `key` None for
  Dan's own commands; `dans` when `key` is None or its group has `dan: true`.
- `Admitted(model: str, loaded_now: bool, seconds: float | None)`.
- `Admitter(registry, state, llamaswap: AsyncLlamaSwap, emit: Emit, clock, *, read_mem:
  Callable[[], MemInfo], read_hold: Callable[[], Hold | None], launch: Path, hf_home: str,
  holders: Callable[[bool], list[Holder]])`:
  - `async admit(req) -> Admitted | Refusal` — a model already `ready`: Admitted at once. One
    already starting: the request joins that load and waits for it, past its key's wait,
    bounded by the load's deadline. A model whose files aren't under `hf_home`: `not_downloaded`
    at once, before any ticket. Otherwise it queues: Dan's requests ahead of `agent`'s, first come
    first served within each; one load at a time; each request is rechecked whenever memory, the
    hold, a load, a drain or the queue changes; at its deadline it is refused with the code for
    what it was waiting on — `no_fit`, `loading` (its turn for the one-load slot never came),
    `held_by_brake`, `footprint_suspect`, `draining`, `restarting`, `llama_swap_down`.
  - When a request fits: `tickets.issue(launch, model, deadline=now + LOAD_CALL_TIMEOUT_S)`;
    `waiting` (once, if it had waited) and `load_started`; `llamaswap.load(model)`. `READY` →
    `loaded`, a `ModelRecord` with the load's fall (`MemAvailable` before less after), capped at
    the model's `cold_load_gib` plus `LOAD_FALL_MARGIN_GIB` (2 GiB, this plan's value) when the
    registry has one, else at its footprint, and flagged when capped. `UNKNOWN` → the slot stays
    held until `/running` shows it ready or gone. `failed` → `load_failed`, its text launch's
    refusal record for the model if there is one, else the engine's last line
    (`llamaswap.last_lines`), or the deadline's variant for `health check timed out`; the ticket
    withdrawn if it wasn't used.
  - *owed* — `budget.owed_gib`, each model's `held_now` its load's fall, plus its engine's
    `RssAnon` growth since the load only when `registry.gate.owed_reads_rss`. *held* — the room
    hold, not counted for Dan's requests; a load of his shrinks it with
    `budget.hold_after_dans_load`, and one that uses it up ends it (`room_hold_ended`, *used up by
    your own loads*).
  - The brake's mark: an `agent` request for a marked model waits its wait, then
    `footprint_suspect`, with its notification; a request of Dan's loads it if it fits, and the
    mark goes.
  - `cancel(request_id)` — a client gone: out of the queue at once, and nothing loads for it; a
    load already started finishes.
  - `set_restarting(on: bool)`, `set_llamaswap_up(up: bool)`, `changed()`.
  - `free_for_a_load(*, dans: bool) -> Decimal`, `owed() -> Decimal`, `starting_gib() -> Decimal`,
    `waiting() -> list[dict]` (`StatusView`'s `waiting` rows).
- `registry.Model` gains `cold_load_gib: float | None = None` (Task 4's module), which the soak
  records (Task 42).

**Tests** (`spark/tests/test_gate_admission.py`; an injected clock; a stand-in llama-swap whose
loads, `/running` and log lines the test scripts; memory from a list; a temporary `launch` and
`hf_home` with the models' files):

- `test_a_model_that_fits_loads_once_with_a_ticket` — residents loaded (footprints 32, 8, 3,
  holding them), 74 GiB available, the coder 41: one ticket for the coder with deadline now + 200;
  one load call; `Admitted`; a record whose fall is 74 less the reading after.
- `test_a_loaded_model_is_admitted_at_once` — the coder ready → `Admitted(loaded_now=False)`, no
  load call.
- `test_one_load_at_a_time` — two models, both fitting, asked together: the second's load call
  starts only after the first's returns.
- `test_dans_keys_go_ahead_of_agents_and_first_come_within_each` — while a load runs: `agent` A,
  Dan B, `agent` C, Dan D, in that order → loads B, D, A, C.
- `test_a_request_for_a_loading_model_joins_its_load_and_waits_past_its_wait` — the coder's load
  takes 50 s; Dan's request (wait 30 s) at 5 s → `Admitted` at 50 s.
- `test_no_fit_after_the_keys_wait_carries_the_moments_numbers` — the examples' moment, Dan's key
  → at 30 s `no_fit`, its message Task 6's Dan text; recorded in `state.refusals`; `refused`
  emitted.
- `test_room_appearing_within_the_wait_loads_the_model` — available 48, then 90 at 10 s → loaded
  at 10 s.
- `test_loading_means_the_wait_ran_out_in_the_queue_for_the_slot` — Gemma's load holds the slot
  for 60 s; Dan's request for the coder, which fits, at 0 → at 30 s `loading`, naming Gemma.
- `test_held_by_brake_while_the_hold_stands` — a hold: Dan's request → `held_by_brake` at 30 s; the
  hold lifted at 10 s instead → loaded.
- `test_footprint_suspect_for_agent_and_dans_load_clears_the_mark` — a mark on the coder: `agent`'s
  request → at 600 s `footprint_suspect`, and its notification; Dan's → loaded, the mark gone.
- `test_a_room_hold_counts_for_agent_and_not_for_dan` — available 106, owed 0, a 70 GiB hold:
  `agent`'s coder → at 600 s `no_fit`, Task 6's `agent` text; Dan's → loaded.
- `test_dans_load_shrinks_the_hold_by_what_outside_couldnt_cover` — 50 free for a load, a 41 GiB
  hold: Dan's coder (41) → the hold is 9; a further Dan load of 9 → the hold ends and
  `room_hold_ended` is emitted.
- `test_a_client_gone_leaves_the_queue_and_nothing_loads_for_it` — `agent` waiting for memory,
  then `cancel` → memory frees and no load starts.
- `test_a_load_started_for_a_client_that_went_finishes` — `cancel` after the load call began → the
  load completes and the model's record is `ready`.
- `test_a_load_call_that_times_out_keeps_the_slot_until_running_says_ready_or_gone` — `UNKNOWN`,
  `/running` showing `starting` for 10 s then `ready`: a second request waits until then.
- `test_load_failed_carries_launchs_record_or_the_engines_last_lines` — `failed`, launch's record
  reason `failed to load model` → `load_failed` with Task 6's text; with no record and the last
  line `failed to load model` → the same text.
- `test_a_load_past_its_deadline_says_so` — `failed` with `health check timed out` → Task 6's
  deadline variant.
- `test_not_downloaded_is_refused_before_a_ticket` — the coder's file missing → `not_downloaded`
  at once; no ticket, no load.
- `test_restarting_during_applys_restart_window` — `set_restarting(True)`: Dan's request →
  `restarting` at 30 s; turned off at 10 s with llama-swap up → loaded.
- `test_llama_swap_down_after_the_keys_wait` — `set_llamaswap_up(False)`: Dan's request →
  `llama_swap_down` at 30 s.
- `test_owed_ignores_rss_growth_until_the_registry_turns_it_on` — Gemma 32, its load's fall 25,
  its `RssAnon` up 5 since: `owed_reads_rss` false → owed 7; true → 2.
- `test_a_loads_fall_is_capped_and_flagged_when_other_memory_moved` — the coder's `cold_load_gib`
  30: a fall of 40 → recorded 32, flagged; with no `cold_load_gib`, a fall of 45 → recorded 41,
  flagged.
- `test_an_unused_ticket_is_withdrawn_after_a_failed_load` — `failed` with the ticket still on
  disk → removed.
- `test_a_starting_model_and_the_ceiling_count` — a model starting at 30, committed 43, ceiling
  102: `free_for_a_load` is `min(available − reserve − owed, 59) − 30`.
- `test_waiting_load_started_and_loaded_are_emitted` — a Dan request that waits, then loads:
  `waiting` once, then `load_started`, then `loaded`.
- In `test_registry.py`: `test_a_cold_load_is_optional_and_positive` — absent → None; `0` or
  `-1` → refused, naming `cold_load_gib`.

**Steps:**

- [ ] **Step 1:** the failing tests; run them: they fail (the module missing).
- [ ] **Step 2:** `gate/admission.py`, and `cold_load_gib` in the registry; the tests pass;
  `make test lint`.
- [ ] **Step 3: Commit.** **On the Spark:**

```bash
git add spark/src/spark/gate/admission.py spark/tests/test_gate_admission.py spark/src/spark/registry.py spark/tests/test_registry.py
git commit -m "feat(spark): 🤖 the gate admits one load at a time, Dan's keys first, and refuses with a reason" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 15 [Spark]: the gate's drain, idle unloading, pins and sessions

**Files:**

- Create: `spark/src/spark/gate/policy.py`, `spark/tests/test_gate_policy.py`

**Interfaces:**

- `FrontChannel` — the gate's side of the front's calls: `snapshot` (the latest whole in-flight
  report), `connected: bool`, `send(event: dict)` (onto the open `/v1/front/events` stream),
  `drained(model, drain_id)`.
- `Drainer(front: FrontChannel, llamaswap, admitter, emit, clock)`: `async drain(model: str, why:
  "make-room" | "unload" | "idle" | "apply") -> bool` — sends `drain`; waits for the front's
  `drained`, however long the requests in flight take; unloads; sends `unloaded`; True. Not done
  `DRAIN_GRACE_S` after `drained` (the unload hung), or the channel drops at any point: sends
  `undrain` (when connected) and returns False. It never unloads before `drained`.
- `idle_due(now, state, registry, snapshot, live: Callable[[Session], bool]) -> list[str]` —
  on-demand models with no request in flight, no live session and no pin, idle at least
  `idle_unload_min`; residents never. The gate drains each, then emits `unloaded` (*after 60 min
  idle*).
- `Pins(state, admitter, emit, clock)`: `pin(model, until, uid)` — Dan's only (a `spark-admin`
  uid); a model not loaded is loaded first, through admission as his; `unpin(model, uid)`;
  `expire(now)` — `pin_ended` for each that ran out.
- `Sessions(state, clock, *, proc: Path, may_hold: Callable[[int], bool])`: `register(uid, pid,
  model, label) -> Session | Refusal` — the pid alive and the caller's (its `/proc` `Uid:`);
  at most `SESSIONS_PER_UID` per uid; `renew(id, uid)`; `end(id, uid, *, admin: bool)` — the
  owner or `spark-admin`; `prune(now)` — a session whose process exited, or not renewed for
  `SESSION_TTL_S`, ends. `may_hold(uid)` is false for `spark` and `spark-front`.

**Tests** (`spark/tests/test_gate_policy.py`; an injected clock; a `/proc` the test builds):

- `test_an_on_demand_model_idle_unloads_after_60_minutes` — the coder last used at T, nothing in
  flight, no session or pin: `idle_due` at T + 59 min → `[]`; at T + 60 min → `["coder"]`.
- `test_a_resident_never_idle_unloads` — Gemma last used a day ago → `[]`.
- `test_requests_in_flight_count_as_use` — the coder last used 2 h ago, 1 in flight → `[]`.
- `test_a_live_session_keeps_its_model` — a session on the coder whose process lives → `[]`.
- `test_a_session_ends_with_its_process` — its process gone → `prune` ends it.
- `test_a_session_expires_12_hours_after_its_last_heartbeat` — renewed at T: kept at T + 12 h − 1 s,
  gone at T + 12 h + 1 s.
- `test_a_uid_holds_8_sessions_at_most` — a ninth → refused, the text naming 8.
- `test_a_session_must_name_a_live_process_of_its_caller` — another uid's pid → refused; a gone pid
  → refused.
- `test_only_the_owner_or_spark_admin_ends_a_session` — `agent` ends its own → ended; another uid
  → refused; `admin=True` → ended.
- `test_spark_and_spark_front_may_not_hold_sessions` — either uid → refused.
- `test_a_pin_keeps_its_model_until_its_time_then_pin_ended` — a pin until T + 8 h: `idle_due` at
  T + 2 h with the coder idle → `[]`; `expire` at T + 8 h → the pin gone, `pin_ended` with Task 6's
  text.
- `test_pins_are_dans_only` — `pin` from `agent`'s uid → refused.
- `test_pinning_a_model_that_isnt_loaded_loads_it_first` — the coder not loaded → admission called
  as Dan's, then the pin set.
- `test_a_drain_waits_for_requests_in_flight_however_long` — 1 in flight for an hour: no unload;
  `drained` → unload, `unloaded` sent, True.
- `test_the_unload_comes_only_after_drained` — no `drained` → no unload call.
- `test_a_drain_not_done_30s_after_drained_goes_back_to_serving` — `drained`, then an unload that
  hangs → at 30 s `undrain` sent, False.
- `test_every_drain_goes_back_if_the_fronts_channel_drops` — two drains under way, the channel
  drops → both False, no unload.

**Steps:**

- [ ] **Step 1:** the failing tests; run them: they fail (the module missing).
- [ ] **Step 2:** `gate/policy.py`; the tests pass; `make test lint`.
- [ ] **Step 3: Commit.** **On the Spark:**

```bash
git add spark/src/spark/gate/policy.py spark/tests/test_gate_policy.py
git commit -m "feat(spark): 🤖 the gate drains before it unloads, and idle-unloads at 60 minutes; pins and sessions" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 16 [Spark]: make-room and its hold, release, load and unload, the residents' preload, the brake's release

**Files:**

- Create: `spark/src/spark/gate/room.py`, `spark/tests/test_gate_room.py`

**Interfaces:**

- `Room(state, registry, admitter, drainer, emit, clock, *, read_mem, read_hold, release_hold,
  boot_id)`:
  - `plan(target: Decimal | ALL, uid) -> RoomPlan` (with a `plan_id`) — every candidate marked:
    its pin, the session using it, its requests in flight and their age, its idle time; from
    `budget.make_room_plan`, the free-now figure the admission one for Dan.
  - `async execute(plan_id, for_s: float | None, uid) -> RoomResult(unloaded: list[str], free_gib,
    hold_gib, until, ended: list[str])` — drains and unloads each, in order; a pin it unloads ends,
    and a session loses its model, each named in `ended` and in `spark status`'s *recent*; sets
    `RoomHold(size = the target, or every GiB free for a load with ALL; until = now + for_s, or
    none; this boot)`.
  - `async release(uid) -> ReleaseResult(room: bool, brake: bool, reloading: list[str])` — ends the
    room hold (`room_hold_ended`, *`--done`*) and lifts the brake's hold (`release_hold`), then
    reloads.
  - `async load(model, uid)`, `async unload(model, uid)` — admission as Dan's; a drain (`why:
    "unload"`).
  - `expire(now)` — a hold whose `until` has passed ends (`room_hold_ended`, *its time ran out*).
- `Preloader(admitter, registry, emit, clock)`: `async run(models)` — the residents, one at a time,
  through admission as the gate's own (never into the hold); at start it waits for llama-swap with
  a backoff of 1, 2, 4 … s up to 30 s; a resident that doesn't fit waits in the queue, shows as
  waiting, and sends `resident_waiting` once. It runs at start, after a hold ends (only the
  residents make-room or the brake unloaded), and after apply's restart (all the residents).
- `brake_release_due(now, above_since: float | None, hold: Hold, state, *, reloads_fit: bool,
  boot_id: str) -> "release" | "wait" | "needs_dan"` — `release` only when memory has been above
  the warn line for `RELEASE_AFTER_S`, the reloads fit, the hold is from this boot, and no
  automatic release came in the last `AUTO_RELEASE_EVERY_S`; `needs_dan` for a hold from another
  boot, or within the hour after an automatic release (`brake_needs_release`, high, once). A
  release removes the hold, sends `brake_released` naming what reloads, and reloads the residents
  the brake unloaded, never the model that was loading when it fired.

**Tests** (`spark/tests/test_gate_room.py`; an injected clock; stand-ins for the admitter's loads
and the drainer):

- `test_make_room_lists_everything_largest_first_with_marks_and_requests_in_flight` — the residents
  and the coder loaded; the coder pinned by Dan and used by `agent`'s pi session, idle 12 min;
  Gemma with 1 request in flight for 3 min → candidates the coder 41 (pinned, *agent's pi*, idle
  12), Gemma 32 (1 in flight, 180 s), the embeddings 8, whisper 3.
- `test_make_room_frees_until_its_size_is_free_for_a_load` — 9 free for a load, plan for 40 →
  unload the coder, 50 after.
- `test_make_room_asked_too_much_shows_the_most_and_unloads_nothing_unconfirmed` — the python job's
  moment, plan for 70 → not enough, the most 61; nothing unloaded until `execute`.
- `test_make_room_holds_the_room_until_done_its_time_or_the_next_boot` — execute for 40 with
  `for_s` 8 h → `RoomHold(40, T + 8 h)`; `expire` at T + 8 h → ended, `room_hold_ended` *its time
  ran out*; without `for_s`, no end.
- `test_make_room_all_holds_the_whole_box` — `ALL` → everything unloaded, the hold every GiB then
  free for a load.
- `test_unloading_a_pinned_model_ends_its_pin_and_status_says_whose` — the pinned coder in the
  plan → its pin gone; `ended` names the pin and *agent's pi*; a *recent* entry names both.
- `test_no_automatic_reload_takes_the_held_room` — 82 free for a load, 70 held: the preloader's
  Gemma (32) waits; `resident_waiting` once.
- `test_done_reloads_what_make_room_unloaded_one_at_a_time_if_they_fit` — make-room unloaded Gemma
  and the coder: `release` → Gemma reloads, the coder doesn't; `room_hold_ended`'s text ends
  *Reloading Gemma.*
- `test_done_with_nothing_to_reload_says_so` — make-room unloaded only the coder → *… Nothing to
  reload: the coder loads on its next request.*
- `test_the_brake_releases_after_5_minutes_above_28_if_the_reloads_fit` — above 28 since T: at
  T + 4 min 59 s `wait`; at T + 5 min `release`; the hold removed; `brake_released`; the reloads
  one at a time.
- `test_the_brake_waits_while_the_reloads_wouldnt_fit` — above 28 for 5 min, Gemma not fitting →
  `wait`.
- `test_at_most_one_automatic_release_an_hour` — released at T, fired again at T + 30 min, above
  28 for 5 min → `needs_dan`, `brake_needs_release` once.
- `test_a_hold_from_a_previous_boot_waits_for_dan` — the hold's `boot_id` `b1`, now `b2` →
  `needs_dan`, *After the reboot, …*.
- `test_the_model_loading_when_the_brake_fired_isnt_reloaded` — the hold's `loading` the coder →
  the release reloads the residents only, and `brake_released` ends *The coder was loading when it
  fired, so it loads again only when you ask.*
- `test_dans_release_lifts_both_holds` — a room hold and a brake hold → `ReleaseResult(True, True,
  …)`.
- `test_the_preload_waits_for_llama_swap_then_loads_one_at_a_time` — llama-swap unreachable three
  times (backoff 1, 2, 4 s), then up → Gemma, the embeddings, whisper loaded in registry order,
  each after the last.

**Steps:**

- [ ] **Step 1:** the failing tests; run them: they fail (the module missing).
- [ ] **Step 2:** `gate/room.py`; the tests pass; `make test lint`.
- [ ] **Step 3: Commit.** **On the Spark:**

```bash
git add spark/src/spark/gate/room.py spark/tests/test_gate_room.py
git commit -m "feat(spark): 🤖 make-room holds the room it frees; the gate releases the brake within bounds and reloads the residents" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 17 [Spark]: the gate's sockets and spark gate

**Files:**

- Create: `spark/src/spark/gate/app.py`, `spark/src/spark/gate/main.py`,
  `spark/tests/test_gate_app.py`
- Modify: `spark/src/spark/cli.py` (`spark gate`)

**Interfaces:**

- `GateCore(registry, state, …)` — ties Tasks 12–16 together. It reads memory every 250 ms and
  llama-swap's `/running` every second, the top holders (`nvidia-smi`) every 5 s, each off the
  event loop where it could block; writes the activity record every second; ingests the brake's
  events every second; watches the four units every 5 s; runs the idle check, the pins' and the
  hold's expiry, and the brake's release check every 5 s; saves the state on every change; sets
  `clean_shutdown` false at start and true on a clean stop.
- The activity record, `GATE_STATE/activity.json`, written whole every second: `{written_at,
  boot_id, models: {name: {state, inflight, last_use}}}` — what the brake reads (Task 21).
- `build_apps(core, *, front_uid: int, admin_uids: Callable[[], set[int]]) -> tuple[Starlette,
  Starlette]` — the status app and the control app, each route authorizing the scope's
  `peer_cred` uid as Task 9's table says: `front` routes only `front_uid`; `users` any caller (the
  socket's group already limits who connects); `owner` the session's uid, or an admin uid to end
  one; `admin` an admin uid or 0. A request without `peer_cred` is refused. A refusal is 403 with a
  sentence saying who may. A body over `MAX_REQUEST_BYTES` → 413, never parsed; every request but
  `/v1/admit` and `/v1/front/events` is bounded by `REQUEST_TIMEOUT_S`. `GET /v1/status` through
  the status socket leaves out other uids' refusals and names Dan's processes only as *a process
  of Dan's*.
- `gate/main.py`: `spark gate` — `listen_fds()["status"]` and `["control"]`; credentials
  `llamaswap-key` and `ntfy-token`; `NTFY_URL` and `NTFY_TOPIC_GATE` from the environment (the
  values file); `spark-front`'s uid and `spark-admin`'s members from the system's databases;
  `AsyncLlamaSwap(paths.LLAMASWAP_URL, key, load_timeout_s=load_timeout_for(
  render.HEALTH_CHECK_TIMEOUT_S))`; `run_servers` with `make_protocol(peer_cred=True,
  header_timeout_s=10)` and `graceful_s=20`. `TESTED_AGAINST = {"llama-swap": "v257"}`.

**Tests** (`spark/tests/test_gate_app.py`; the apps called with a scope whose `peer_cred` the test
sets, or over real sockets where named):

- `test_an_inflight_report_from_any_uid_but_spark_fronts_is_refused` — `front_uid` 990: a report
  from 990 → 200; from 1000 → 403, the snapshot unchanged.
- `test_admit_and_the_front_channel_are_spark_fronts_alone` — `/v1/admit`, `/v1/front/events`,
  `/v1/front/drained` from 1000 → 403.
- `test_a_request_without_peer_cred_is_refused` — no `peer_cred` → 403.
- `test_the_control_socket_serves_only_spark_admin_and_root` — an admin uid → 200; 0 → 200;
  `agent`'s → 403.
- `test_status_through_the_status_socket_leaves_out_other_uids_refusals_and_dans_processes` —
  refusals for Dan's key and `agent`'s: `agent`'s call sees only its own, and Dan's python job as
  *a process of Dan's, 32 GiB*; the control socket's call sees both, and `python3 (chendaniely)`.
- `test_a_request_over_the_size_cap_is_refused_not_crashed` — 65,537 bytes → 413 in words; the
  next request answered.
- `test_a_caller_that_hangs_times_out` — a body that never finishes, the timeout set to 0.3 s → the
  connection closed by 1 s; an admit call held for 2 s isn't cut.
- `test_the_activity_record_is_written_every_second` — three ticks of the injected clock →
  `activity.json`'s `written_at` the last tick, the models' state, in-flight counts and last use
  from the front's snapshot.
- `test_quiet_reports_whats_in_flight_and_for_how_long` — the coder with 1 in flight since
  T − 20 → `quiet_for_s` 0 and the request; nothing in flight since T − 45 → 45.
- `test_apply_begin_and_end_reread_the_registry_and_reload_the_residents` — `begin` → the
  admitter's restarting on; `end` → the registry re-read from disk (a changed label shows), the
  residents' preload started, `apply_restarted` naming them.
- `test_the_gate_keeps_serving_while_ntfy_hangs` — a publish that hangs: `GET /v1/status` answers
  within 0.5 s.
- `test_a_blocking_read_never_holds_a_request` — an `nvidia-smi` stand-in taking 1 s: `GET
  /v1/status` answers within 0.3 s, from the last reading.
- `test_logs_reads_the_engines_last_lines_with_the_gates_key` — the stand-in llama-swap's lines:
  `GET /v1/logs/coder?n=5` → the last five; the stand-in saw the gate's key.
- `test_status_view_has_every_key` — `GET /v1/status`'s JSON has every `StatusView` key.
- `test_spark_gate_takes_its_two_sockets_from_systemd` — `spark gate` in a subprocess, given two
  short Unix sockets as `status:control`, stand-in credentials, a stand-in ntfy and the v257
  stand-in: a `GateClient` gets the status on each; SIGTERM → exit 0, `STOPPING=1` sent, and the
  state file's `clean_shutdown` true.

**Steps:**

- [ ] **Step 1:** the failing tests; run them: they fail (the modules missing).
- [ ] **Step 2:** `gate/app.py`, `gate/main.py`, `spark gate`; the tests pass; `make test lint`.
- [ ] **Step 3: Commit.** **On the Spark:**

```bash
git add spark/src/spark/gate/app.py spark/src/spark/gate/main.py spark/src/spark/cli.py spark/tests/test_gate_app.py
git commit -m "feat(spark): 🤖 spark gate: the status and control sockets, each caller known by its uid" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 18 [Spark]: the front, 1 — routes, keys, bodies and its own refusals

**Files:**

- Create: `spark/src/spark/front/__init__.py`, `spark/src/spark/front/parse.py`,
  `spark/src/spark/front/app.py`, `spark/tests/test_front_parse.py`, `spark/tests/test_front_app.py`

**Interfaces:**

- `parse.ROUTES: frozenset[tuple[str, str]]` — `("GET", "/v1/models")` and `("POST", p)` for the
  six inference paths; `GET /health` is the front's own.
- `parse.authenticate(headers: list[tuple[bytes, bytes]], digests: dict[str, bytes]) -> str | None`
  — the key from `Authorization: Bearer <key>` or `x-api-key: <key>`, through
  `credentials.match_key`; the key's name, or None.
- `parse.read_json(receive, *, max_bytes: int) -> tuple[dict, bytes]` — the body parsed, and the
  bytes to send on, re-serialized from it (`json.dumps`, compact); 400 for a body that isn't a
  JSON object or has no string `model`.
- `parse.read_form(receive, content_type: str, *, spool_dir: Path, max_bytes: int, spool_above:
  int = 1 MiB) -> Form` — `Form(model: str, fields: list[tuple[str, str]], files:
  list[FilePart(name, filename, content_type, path_or_bytes)])`; a part over `spool_above` goes to
  a file in `spool_dir` (the unit's `PrivateTmp=` `/tmp`), never memory; two `model` fields → 400;
  `parse.encode_form(form) -> tuple[str, AsyncIterator[bytes]]` rebuilds it for upstream;
  `Form.close()` deletes every spooled file.
- `parse.resolve(name: str, registry) -> Model | None` — a model's name or one of its roles.
- `parse.clean_headers(headers) -> list[tuple[bytes, bytes]]` — drops `authorization`,
  `x-api-key`, `cookie`, `proxy-authorization`, `host`, `content-length`, `transfer-encoding` and
  the hop-by-hop headers; `parse.both_lengths(headers) -> bool` — both `Content-Length` and
  `Transfer-Encoding` present (400).
- `parse.KeyCaps(registry)` — per key, requests waiting (`max_waiting`) and requests open, waiting
  or answering (`max_open`): `enter_open(key) -> bool`, `enter_waiting(key) -> bool`, their
  `leave_…`; past a cap, `too_many_requests`.
- `parse.journal_line(key: str, model: str, status: int, seconds: float) -> str` — `front: <key>
  <model> <status> <seconds, one decimal>s`, nothing more.
- `app.FrontApp(registry, digests, *, upstream, gate, caps, log)` — the ASGI app. Bodies capped at
  200 MiB (`MAX_BODY_BYTES = 200 * 2**20`). The front's own answers: `GET /health` → 200
  `{"status": "ok"}` with no key needed; an unlisted route → 404 `route_not_served`; no key or an
  unknown one → 401, `{"error": {"message": messages.UNKNOWN_KEY, "code": "invalid_api_key"}}`; an
  unknown model → 404 `model_not_found`; over a cap → 429 `too_many_requests`; a body over the cap →
  413; anything it can't parse → 400; never a traceback to the client. (Task 19 forwards; Task 20
  asks the gate. Here `upstream` and `gate` are stand-ins.)

**Tests** (`spark/tests/test_front_parse.py` and `spark/tests/test_front_app.py`; the app called as
ASGI with a recording upstream stand-in, unless named):

- `test_only_the_listed_routes_go_through_and_the_rest_get_route_not_served` — parametrized: `GET
  /unload`, `POST /api/models/unload/x`, `POST /api/inflight/1/cancel`, `GET /logs/stream`, `GET
  /upstream/x/health`, `GET /running`, `GET /ui`, `GET /metrics`, `GET /v1/chat/completions`, `POST
  /v1/models` → 404 with Task 6's `route_not_served` text; the upstream saw nothing.
- `test_health_is_answered_by_the_front_itself` — `GET /health`, no key → 200 `{"status": "ok"}`;
  nothing upstream.
- `test_an_unknown_key_is_refused_and_never_logged` — `Bearer nope` → 401 with the ruling's text
  and code `invalid_api_key`; the captured log holds neither `nope` nor its digest.
- `test_no_key_is_refused` — no header → 401.
- `test_bearer_and_x_api_key_both_authenticate` — `Bearer k1` → `dan-mac`; `x-api-key: k2` →
  `agent`.
- `test_the_model_is_read_from_json_or_a_form_and_roles_resolve` — JSON `{"model": "coder"}` → the
  coder; a form with `model=stt` → whisper; `{"model": "gemma-4-26b-a4b"}` → Gemma.
- `test_a_form_with_two_model_fields_is_refused` → 400.
- `test_a_json_body_goes_on_reserialized` — `{"model":"coder",  "x" : 1}` → the upstream receives
  exactly `json.dumps(json.loads(body), separators=(",", ":"))`'s bytes.
- `test_content_length_with_transfer_encoding_is_refused` — a raw request over a real uvicorn
  with both headers → 400, from h11 or the front.
- `test_auth_cookie_and_proxy_auth_headers_never_go_upstream` — a request with `Authorization`,
  `x-api-key`, `Cookie`, `Proxy-Authorization`, `Connection` and `X-Custom: 1` → the upstream sees
  `X-Custom: 1` and none of the others (its `Authorization` is Task 19's internal key).
- `test_an_upload_is_spooled_to_disk_and_deleted_when_the_request_ends` — a 3 MiB file part: while
  the request runs, the spool folder holds one file of its bytes; after the response, nothing; a
  100 KiB part writes no file.
- `test_a_body_over_200_mib_is_refused` — `Content-Length` of 200 MiB + 1 → 413 before reading;
  a chunked body that crosses 200 MiB → 413, and its spool file gone.
- `test_an_unparseable_request_gets_400_never_a_crash` — `{`, `[]`, `{"x": 1}` (no model), and a
  form with no boundary → 400 each; the next request is served.
- `test_model_not_found_names_the_registrys_models` — `qwen3.6-35b-a3b` → 404 with Task 6's text.
- `test_too_many_requests_when_a_keys_caps_are_reached` — `agent`'s four requests held waiting →
  the fifth → 429, `Retry-After: 10`; 32 of a key's requests open → the 33rd → 429.
- `test_journal_lines_carry_key_name_model_status_and_duration_only` — one request → one line
  `front: agent qwen3.8-27b 200 1.2s` (its duration the injected clock's); no header value, body
  or file name in it.

**Steps:**

- [ ] **Step 1:** the failing tests; run them: they fail (`spark.front` missing).
- [ ] **Step 2:** `parse.py` and `app.py`; the tests pass; `make test lint`.
- [ ] **Step 3: Commit.** **On the Spark:**

```bash
git add spark/src/spark/front/__init__.py spark/src/spark/front/parse.py spark/src/spark/front/app.py \
  spark/tests/test_front_parse.py spark/tests/test_front_app.py
git commit -m "feat(spark): 🤖 the front: only the inference routes, client keys as digests, bodies parsed and bounded" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 19 [Spark]: the front, 2 — forwarding, counting, streams and spark front

**Files:**

- Create: `spark/src/spark/front/upstream.py`, `spark/src/spark/front/main.py`,
  `spark/tests/test_front_forward.py`
- Modify: `spark/src/spark/front/app.py`, `spark/src/spark/cli.py` (`spark front`)

**Interfaces:**

- `Upstream(base_url: str, internal_key: str)` — an `httpx.AsyncClient` per client key, so no
  upstream connection carries two keys' requests; each with `trust_env=False`,
  `follow_redirects=False`, `Timeout(connect=5, read=None, write=60, pool=5)`; `async
  forward(method, path, headers, body: bytes | AsyncIterator[bytes], key) -> UpstreamResponse` —
  the status, the headers (hop-by-hop dropped) and `aiter_raw()`; `Authorization: Bearer
  <internal_key>` the only credential sent.
- `InFlight` — per model: a count, the oldest request's start, the last end; `enter(model, key) ->
  token`, `leave(token)`, called in a `finally` on every way out; `snapshot() -> dict` (the
  `/v1/front/inflight` shape).
- A disconnect: `receive()` watched for `http.disconnect` beside the stream; the upstream call
  cancelled, and its count left.
- llama-swap's 429 `concurrency_limit` → the client's 429, its `Retry-After`, Task 6's words.
- `front.main.FRONT_MODULES: frozenset[str]` — exactly the `spark` modules the front imports at
  start, all of them before it serves: `spark`, `spark.paths`, `spark.registry`,
  `spark.credentials`, `spark.sockets`, `spark.protocols`, `spark.sdnotify`, `spark.serve`,
  `spark.gateproto`, `spark.messages`, and `spark.front` with its five modules. `spark front` —
  `listen_fds()["front"]`; the registry's names and roles, and the credentials `client-keys` and
  `llamaswap-key`, read once at start; `run_servers` with `make_protocol(peer_cred=False,
  header_timeout_s=10)`, `graceful_s=20`. `TESTED_AGAINST = {"llama-swap": "v257"}`.

**Tests** (`spark/tests/test_front_forward.py`; the front on a real uvicorn, against the v257
stand-in):

- `test_a_stream_is_forwarded_chunk_by_chunk` — five SSE chunks 0.1 s apart: the client has the
  first before the stand-in sends the last.
- `test_the_count_goes_down_when_a_response_ends_a_client_goes_or_upstream_fails` — the model's
  count is 1 during a request and 0 after each of: a full response; the client closing mid-stream;
  the stand-in dropping the connection mid-stream; the handler's task cancelled.
- `test_a_client_gone_mid_stream_cancels_the_upstream_call` — the client closes: the stand-in sees
  its request end within 1 s.
- `test_a_silent_long_prefill_is_never_timed_out` — the stand-in sends its headers, then nothing
  for 3 s, then a chunk: the client gets the chunk.
- `test_upstream_gets_only_the_internal_key` — the stand-in saw `Authorization: Bearer <internal>`
  and no client key.
- `test_no_upstream_connection_is_shared_between_keys` — a request from each of two keys → two
  upstream connections (two client ports at the stand-in).
- `test_llama_swaps_429_is_passed_on_worded_with_its_retry_after` — the stand-in's eleventh
  request → the client's 429, `Retry-After: 1`, *Too many requests for the coder at once; try
  again in a moment.*
- `test_each_models_oldest_request_is_tracked` — requests from T and T + 5 → the snapshot's oldest
  is T; after the first ends, T + 5.
- `test_spark_front_takes_9100_from_systemd` — `spark front` in a subprocess, given a TCP listener
  on 127.0.0.1:0 as `front`, stand-in credentials, the v257 stand-in and a gate stand-in: `GET
  /health` → 200; SIGTERM → exit 0.
- `test_the_front_imports_only_its_listed_modules` — a subprocess importing `spark.front.main`: its
  `spark` modules in `sys.modules` are exactly `FRONT_MODULES`.

**Steps:**

- [ ] **Step 1:** the failing tests; run them: they fail (the modules missing).
- [ ] **Step 2:** `upstream.py`, `main.py`, the app's forwarding, `spark front`; the tests pass;
  `make test lint`.
- [ ] **Step 3: Commit.** **On the Spark:**

```bash
git add spark/src/spark/front/upstream.py spark/src/spark/front/main.py spark/src/spark/front/app.py \
  spark/src/spark/cli.py spark/tests/test_front_forward.py
git commit -m "feat(spark): 🤖 the front forwards with its own key and counts every request until its response ends" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 20 [Spark]: the front, 3 — with the gate

**Files:**

- Create: `spark/src/spark/front/gatelink.py`, `spark/tests/test_front_gatelink.py`
- Modify: `spark/src/spark/front/app.py`

**Interfaces:**

- `GateLink(status_socket: Path, clock)` — httpx over the Unix socket
  (`httpx.AsyncHTTPTransport(uds=…)`, `trust_env=False`): keeps `GET /v1/front/events` open and
  follows its events; `gate_up` turns false only once that call has dropped and a new one hasn't
  been answered within `GATE_DOWN_AFTER_S`; `async admit(model, key, deadline, request_id) -> dict`
  (the call dropped when the client goes); posts a whole `/v1/front/inflight` snapshot on every
  change and at least every second; `drained(model, drain_id)`.
- What's loaded: the gate's `state` events while it's up; llama-swap's `/running`, cached for a
  second, while it's down.
- A request's way: loaded → forwarded; not loaded → `admit`, with `deadline = received_at + the
  key's wait`, nothing sent to the client meanwhile; the gate down → forwarded if loaded, else 503
  `gate_down`; the gate restarting → each held admission asked again with its original deadline;
  llama-swap's 500 `upstream command exited prematurely` → asked again once and forwarded again from
  the held body, a second one reaching the client as 409 `load_failed`.
- The drain, under one lock: on `drain`, the model is marked draining with its count checked, and
  `drained` is posted when the count reaches 0; its new requests wait as for a load, and get
  `draining` at their deadline; on `unloaded`, they go through admission; on `undrain`, they are
  forwarded.

**Tests** (`spark/tests/test_front_gatelink.py`; a scriptable gate stand-in on a short Unix socket,
speaking Task 9's routes):

- `test_a_loaded_model_is_forwarded_without_asking_the_gate` — the gate's `state` says the coder is
  ready → forwarded; the stand-in saw no admit.
- `test_a_model_not_loaded_waits_for_the_gates_answer` — the stand-in holds the admit 1 s, then
  `ok` → forwarded after it; the admit carried the model, `dan-mac`, `deadline = received + 30`
  and a request id.
- `test_a_refusal_reaches_the_client_as_its_status_with_its_words_and_headers` — the stand-in
  answers `no_fit` (409, message M, `retry_after_s` 30) → the client's 409, body `{"error":
  {"message": M, "code": "no_fit"}, "retry_after_s": 30}`, `x-should-retry: false`,
  `retry-after: 30`; `loading` (409, no retry-after) → a 409 without `retry-after`; `restarting`
  (503, 60) → a 503 with `retry-after: 60`.
- `test_the_gate_counts_as_down_only_after_5s_without_an_answer` — the events call drops at T and
  reconnects hang (the socket held, never accepted): a request at T + 3 for a model not loaded
  still waits; at T + 5 it gets `gate_down` (the interval injected small).
- `test_while_the_gate_is_down_loaded_models_answer_and_loads_get_gate_down` — the gate down,
  `/running` showing Gemma ready: Gemma's request is forwarded; the coder's → 503 `gate_down`,
  Task 6's text, `retry-after: 30`.
- `test_a_gate_restart_reasks_with_the_original_deadline` — an admit held, the gate's connection
  dropped at T + 10 of a 30 s wait, the gate back → the admit asked again with `deadline =
  received + 30`.
- `test_exited_prematurely_is_asked_again_and_forwarded_from_the_held_body` — the coder thought
  ready, the stand-in answers 500 `upstream command exited prematurely` → an admit, then the same
  body bytes sent again, the client getting that answer; twice in a row → 409 `load_failed`.
- `test_drain_marks_draining_and_answers_drained_at_zero_under_one_lock` — 1 in flight, `drain`:
  a new request waits; the one in flight ends → one `drained` posted; the count never below 0.
- `test_a_request_for_a_draining_model_waits_then_gets_draining` — never unloaded, Dan's 30 s
  passes → 503 `draining`.
- `test_unloaded_sends_waiting_requests_through_admission` — `unloaded` → the waiting request's
  admit call made.
- `test_undrain_returns_the_model_to_serving` — `undrain` → the waiting request forwarded.
- `test_in_flight_snapshots_are_whole_never_deltas` — every posted snapshot names every model with
  its count; at least one a second, and one on each change.
- `test_nothing_is_written_to_a_waiting_clients_stream` — a streamed request waiting 1 s: the
  client receives no byte, headers included, until it is forwarded.
- `test_a_client_gone_drops_its_admit_call` — the client disconnects while waiting → the stand-in
  sees its admit call close.

**Steps:**

- [ ] **Step 1:** the failing tests; run them: they fail (`gatelink` missing).
- [ ] **Step 2:** `gatelink.py` and the app's use of it; the tests pass; `make test lint`.
- [ ] **Step 3: Commit.** **On the Spark:**

```bash
git add spark/src/spark/front/gatelink.py spark/src/spark/front/app.py spark/tests/test_front_gatelink.py
git commit -m "feat(spark): 🤖 the front asks the gate for each load, and keeps serving what's loaded while the gate is down" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 21 [Spark]: the brake for 2a

**Files:**

- Modify: `spark/src/spark/brake.py`, `spark/src/spark/hold.py`, `spark/tests/test_brake.py`

**Interfaces:**

- `read_activity(path: Path, now: float) -> Activity | None` — `GATE_STATE/activity.json`; None
  when missing, damaged, or older than `ACTIVITY_STALE_S`.
- `brake_order(running: list[tuple[str, str]], activity: Activity | None, registry) -> list[str]` —
  with an activity record: a loading (`starting`) engine first, then models with nothing in flight,
  of any class, the longest unused first, then the rest, least recently used first; without one,
  Phase 1's order (on-demand first, then the largest).
- `predict_breach(readings: deque[tuple[float, float]], *, brake_gib: float, lead_s: float,
  window_s: float) -> bool` — the fall over the last `window_s` (a least-squares slope of
  `MemAvailable` against time), true when `available − rate × lead_s < brake_gib`; a rise or a flat
  line is false. `FALL_WINDOW_S = 2.0` and `UNLOAD_LEAD_S = 10.0` to start (Phase 1's loads fell 2.0
  to 2.6 GiB/s, and v257 gives an engine 10 s to stop); Task 45 sets both from measurements. When
  it is true above the brake line, the brake holds and unloads as at the line.
- The hold gains `boot_id: str | None`, `episode: int | None`, `loading: str | None` (the model
  that was starting when it fired); `read_hold` still reads Phase 1's holds.
- `BrakeEvents(path: Path)` — appends `{seq, at, boot_id, episode, kind: "fired" | "unload" |
  "warn", model, state, available_gib, sent_by_brake}` to `brake/events.jsonl`, each line fsynced;
  `seq` continues from the file's last line across restarts.
- `alert_without_gate(text: str, priority: str)` — only while `read_activity` is None (the gate's
  record missing or stale): `subprocess.Popen(["/usr/bin/curl", "--fail", "--silent",
  "--max-time", "10", "-H", "@" + <credentials>/ntfy-token, "-H", "Priority: " + priority,
  "--data-binary", text, NTFY_URL + "/" + NTFY_TOPIC_BRAKE], start_new_session=True,
  stdout=DEVNULL, stderr=DEVNULL)`, never waited on; finished children reaped each tick; the event
  recorded `sent_by_brake: true`. The text is Task 6's `brake_fired`.
- `spark brake --key-credential llamaswap-key` (the unit's): the key from
  `read_credential(name)`; the start check records `credential llamaswap-key` as its source, never
  the value. `--key-env` stays, for drills.
- `spark brake --release` (`make brake-release`) — through `GateClient(GATE_CONTROL_SOCKET,
  5).post("/v1/release")`, printing Task 6's `brake_released_by_dan`; on `GateUnavailable`, Phase
  1's direct release of the hold file, printing *The gate isn't answering, so the hold file was
  removed directly; nothing reloads until the gate is back.*; on `GateForbidden`, its text, and
  exit 1.

**Tests** (`spark/tests/test_brake.py`; Phase 1's kept):

- `test_a_loading_engine_goes_first_then_idle_models_of_any_class_then_lru` — Gemma ready (0 in
  flight, used T − 10), the embeddings ready (0, T − 100), the coder starting, whisper ready (2 in
  flight, T − 1), the record fresh → `[coder, embed, gemma, whisper]`.
- `test_a_stale_or_missing_activity_record_falls_back_to_phase_1s_order` — written at T − 4 → the
  coder, then Gemma, the embeddings, whisper (on-demand first, then by size); missing → the same.
- `test_the_rate_of_fall_watch_acts_before_the_brake_line` — readings every 250 ms falling
  2.5 GiB/s from 50: the brake holds and unloads at the first reading under 45 (45 − 25 = 20),
  well above 28.
- `test_a_slow_fall_doesnt_trip_the_rate_watch` — 0.2 GiB/s from 40 → nothing early; the brake
  acts at 20 as before.
- `test_noise_doesnt_trip_the_rate_watch` — ±0.5 GiB around 30 for a minute → never.
- `test_the_hold_records_the_boot_the_episode_and_the_loading_model` — fired with the coder
  starting → `hold.json` holds the boot id, episode 1 and `loading: coder`.
- `test_a_phase_1_hold_still_holds` — a `hold.json` without the new fields → `read_hold` gives a
  `Hold`, with the three None.
- `test_each_step_is_recorded_for_the_gate_with_a_rising_seq` — fired and two unloads → three lines,
  `seq` n, n + 1, n + 2; a new brake on the same file continues at n + 3.
- `test_with_the_gate_down_the_brake_sends_brake_fired_itself_without_waiting` — the record stale:
  firing calls `Popen` once, with the arguments above and Task 6's text; a `Popen` stand-in that
  never finishes doesn't slow the tick; the event says `sent_by_brake: true`.
- `test_with_the_gate_up_the_brake_sends_nothing_itself` — the record fresh → no `Popen`;
  `sent_by_brake: false`.
- `test_finished_alerts_are_reaped` — a finished stand-in is polled and dropped on the next tick.
- `test_the_brake_reads_its_key_from_its_credential` — `--key-credential llamaswap-key` with a
  stand-in `CREDENTIALS_DIRECTORY`: the client's key is the file's; the start check's record names
  `credential llamaswap-key` and not the value.
- `test_release_goes_through_the_gate_and_falls_back_when_it_doesnt_answer` — the gate answers → its
  words, the hold left to the gate; `GateUnavailable` → the hold file removed and the fallback's
  words; `GateForbidden` → its words, exit 1, the file kept.
- `test_the_brake_never_waits_on_the_gate_a_socket_or_ntfy` — a hanging `Popen` and a missing
  record: each tick takes under 50 ms of its own.

**Steps:**

- [ ] **Step 1:** the failing tests; run them: they fail for the new behaviour; Phase 1's still
  pass.
- [ ] **Step 2:** the brake and the hold; the tests pass; `make test lint`.
- [ ] **Step 3: Commit.** **On the Spark:**

```bash
git add spark/src/spark/brake.py spark/src/spark/hold.py spark/tests/test_brake.py
git commit -m "feat(spark): 🤖 the brake unloads idle models first, watches the rate of fall, and alerts by itself while the gate is down" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 22 [Spark]: render — llama-swap's config and unit

**Files:**

- Create: `spark/src/spark/schema.py`, `stack/llama-swap/config-schema.v257.json`,
  `spark/tests/test_schema.py`
- Modify: `spark/src/spark/render.py`, `stack/templates/local-ai-llama-swap.service`,
  `stack/models.yaml` (whisper's `--tmp-dir /var/lib/local-ai/whisper-tmp`; Gemma's
  `--slot-prompt-similarity` only if Step 3 adopts it), `spark/tests/test_render.py`,
  `spark/tests/test_stack_registry.py`, `website/design/plan.md` (Step 3's decision, and a
  Revisions line)

**Interfaces:**

- `render.LLAMASWAP_LISTEN = "127.0.0.1:900"`, `ENGINE_START_PORT = 800`, `HEALTH_CHECK_TIMEOUT_S =
  180`, `INTERNAL_KEY_ENVS = ("LLAMASWAP_KEY_FRONT", "LLAMASWAP_KEY_GATE",
  "LLAMASWAP_KEY_BRAKE")`; `KEY_ENVS` retires, and `apply.validation_env` uses the new names.
  Render refuses a registry whose engines' ports (800 upward, one per model) would reach 900.
- `engine_cmd` passes `--alias <the model's name>` to every llama.cpp engine; `SPELLINGS` gains
  `("-a", "--alias")`, so `args` may not set it.
- The config: `healthCheckTimeout: 180`, `startPort: 800`, `apiKeys` the three
  `${env.LLAMASWAP_KEY_…}` references; the rest as Phase 1's.
- The unit: `ExecStart=…/llama-swap -config /opt/local-ai/etc/llama-swap.yaml -listen
  127.0.0.1:900`; `EnvironmentFile=/etc/local-ai/secrets/internal-keys.env`;
  `Environment=SPARK_LAUNCH=/var/lib/local-ai/launch`; `Restart=always`, `RestartSec=5`;
  `AmbientCapabilities=CAP_NET_BIND_SERVICE`, `CapabilityBoundingSet=CAP_NET_BIND_SERVICE`;
  `NoNewPrivileges=yes`, `ProtectSystem=strict`, `ReadWritePaths=/var/lib/local-ai/whisper-tmp
  /var/lib/local-ai/cache /var/lib/local-ai/cuda-cache /var/lib/local-ai/launch`,
  `ProtectHome=yes`, `PrivateTmp=yes`, `InaccessiblePaths=/etc/local-ai/secrets`,
  `RestrictSUIDSGID=yes`, `ProtectKernelTunables=yes`, `ProtectControlGroups=yes`; no
  `PrivateDevices=` or `MemoryDenyWriteExecute=`; `OnFailure=local-ai-notify@%n.service`.
- `schema.check(config: dict, schema: dict) -> list[str]` — the parts of JSON Schema v257's file
  uses (`type`, `properties`, `additionalProperties`, `required`, `enum`, `items`, `minimum`, local
  `$ref`), one line per problem with its path; render refuses a config with any. The vendored file
  is `https://raw.githubusercontent.com/mostlygeek/llama-swap/v257/config-schema.json`, its SHA-256
  recorded in `test_schema.py` when it is vendored.
- `--slot-prompt-similarity`: read b11146's code for what it does to a slot's choice, weigh it for
  Gemma's two slots (Phase 1's council: at 0.10 a short new prompt can take a long chat's slot and
  its cache), and decide; only then does `ALLOWED` gain `("-sps", "--slot-prompt-similarity")` and
  Gemma's `args` the value, with a comment saying why.

**Tests** (`spark/tests/test_render.py`, `test_schema.py`, `test_stack_registry.py`):

- `test_llama_swap_listens_on_900_and_the_engines_start_at_800` — the unit's `ExecStart` ends
  `-listen 127.0.0.1:900`; the config's `startPort` is 800.
- `test_render_refuses_engine_ports_that_would_reach_900` — 101 stand-in models → a `RenderError`
  naming 900; 100 → renders.
- `test_llama_swap_takes_only_the_three_internal_keys` — `apiKeys` is exactly the three references.
- `test_health_check_timeout_is_180` — the config's value is `HEALTH_CHECK_TIMEOUT_S`, 180.
- `test_every_llama_cpp_engine_answers_as_its_registry_name` — each llama.cpp `cmd` holds `--alias
  <name>`; whisper's holds no `--alias`.
- `test_alias_is_render_owned_in_every_spelling` — `args: [-a, x]` or `[--alias, x]` → refused.
- `test_llama_swap_binds_below_1024_with_only_cap_net_bind_service` — `AmbientCapabilities` and
  `CapabilityBoundingSet` are each exactly `CAP_NET_BIND_SERVICE`.
- `test_llama_swap_is_sandboxed_without_private_devices_or_mdwe` — the eight settings above
  present; `PrivateDevices` and `MemoryDenyWriteExecute` absent.
- `test_llama_swap_restarts_always_and_alerts_on_failure` — `Restart=always`, `RestartSec=5`,
  `OnFailure=local-ai-notify@%n.service`.
- `test_only_the_tickets_folder_whispers_tmp_and_the_caches_are_writable_to_llama_swap` —
  `ReadWritePaths` is exactly those four.
- `test_llama_swap_reads_only_internal_keys_env` — its one `EnvironmentFile` is
  `internal-keys.env`.
- `test_whisper_writes_its_temporary_files_outside_the_hf_cache` (stack registry) — its `--tmp-dir`
  is `/var/lib/local-ai/whisper-tmp`.
- `test_the_rendered_config_passes_v257s_schema` — `schema.check` of the real registry's config →
  `[]`.
- `test_a_mistyped_group_key_fails_the_schema_check` — a group with `swapp: false` → a problem
  naming `swapp`.
- `test_a_wrong_enum_fails_the_schema_check` — `logToStdout: everything` → a problem naming it.
- `test_the_vendored_schema_is_v257s` — the file's SHA-256 is the recorded one.

**Steps:**

- [ ] **Step 1:** vendor the schema: **on the Spark**, fetch it from the tag above into
  `stack/llama-swap/`, and record its SHA-256 in the test.
- [ ] **Step 2:** the failing tests; run them: they fail (port 9100, `KEY_ENVS`, no schema check).
- [ ] **Step 3:** weigh `--slot-prompt-similarity`, as above, and record the decision in plan.md
  (the Phase 2a line's item, with a Revisions line).
- [ ] **Step 4:** render, the unit, the registry's tmp-dir; the tests pass; `make test lint`.
- [ ] **Step 5: Commit.** **On the Spark:**

```bash
git add spark/src/spark/schema.py stack/llama-swap/config-schema.v257.json spark/tests/test_schema.py \
  spark/src/spark/render.py spark/src/spark/apply.py stack/templates/local-ai-llama-swap.service stack/models.yaml \
  spark/tests/test_render.py spark/tests/test_stack_registry.py website/design/plan.md
git commit -m "feat(spark): 🤖 llama-swap on 127.0.0.1:900, its engines from 800, internal keys only, sandboxed" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 23 [Spark]: render — the front's, the gate's and the notifier's units; the polkit rule's six

**Files:**

- Create: `stack/templates/local-ai-front.socket`, `local-ai-front.service`,
  `local-ai-gate-status.socket`, `local-ai-gate-control.socket`, `local-ai-gate.service`,
  `local-ai-notify@.service`; `stack/host/local-ai-notify`; `spark/tests/test_units.py`,
  `spark/tests/test_notifier_script.py`
- Modify: `spark/src/spark/render.py` (`UNITS`, `POLKIT_UNITS`, the notifier's priorities),
  `stack/templates/local-ai-brake.service`, `stack/templates/local-ai-pull.service`,
  `stack/host/50-local-ai.rules`, `stack/host/bootstrap.sh` (`ROOT_UNITS`, `ENABLED_UNITS`),
  `Makefile` (`lint` shellchecks the new script), `spark/tests/test_polkit.py`,
  `spark/tests/test_bootstrap.py`, `spark/tests/test_render.py`

**Interfaces:**

- `render.UNITS` — `local-ai-llama-swap.service`, `local-ai-brake.service`,
  `local-ai-compose.service`, `local-ai-pull.service`, `local-ai-front.socket`,
  `local-ai-front.service`, `local-ai-gate-status.socket`, `local-ai-gate-control.socket`,
  `local-ai-gate.service`, `local-ai-notify@.service`. `render.POLKIT_UNITS` — the six services:
  llama-swap, the brake, compose, the pull, the front, the gate.
- `local-ai-front.socket` — `ListenStream=127.0.0.1:9100`, `FileDescriptorName=front`,
  `NoDelay=yes`, `TriggerLimitIntervalSec=0`, `Service=local-ai-front.service`;
  `WantedBy=sockets.target`.
- `local-ai-front.service` — `Type=notify`, `WatchdogSec=30`, `User=spark-front`,
  `Group=spark-front`, `SupplementaryGroups=spark-users`, `ExecStart=/opt/local-ai/app/.venv/bin/spark
  front`, `Environment=SPARK_REGISTRY=/opt/local-ai/etc/models.yaml`,
  `Environment=SPARK_LLAMASWAP_URL=http://127.0.0.1:900`,
  `LoadCredential=client-keys:/etc/local-ai/secrets/client-keys.sha256`,
  `LoadCredential=llamaswap-key:/etc/local-ai/secrets/front.key`, `Restart=always`, `RestartSec=2`,
  `StartLimitIntervalSec=0`, `OOMScoreAdjust=-900`, `MemoryMax=512M`, `TimeoutStopSec=30`,
  `Wants=` and `After=local-ai-llama-swap.service`, `After=local-ai-gate-status.socket`,
  `Requires=local-ai-front.socket`, `OnFailure=local-ai-notify@%n.service`; sandbox:
  `NoNewPrivileges=yes`, `ProtectSystem=strict`, `ProtectHome=yes`, `PrivateTmp=yes`,
  `PrivateDevices=yes`, `ProtectProc=invisible`, `CapabilityBoundingSet=`,
  `RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX`, `IPAddressDeny=any`,
  `IPAddressAllow=localhost`; `WantedBy=multi-user.target`.
- `local-ai-gate-status.socket` — `ListenStream=/run/local-ai/gate-status.sock`, `SocketUser=spark`,
  `SocketGroup=spark-users`, `SocketMode=0660`, `FileDescriptorName=status`,
  `TriggerLimitIntervalSec=0`, `Service=local-ai-gate.service`; `local-ai-gate-control.socket` the
  same with `…/gate-control.sock`, `SocketGroup=spark-admin`, `FileDescriptorName=control`; each
  `WantedBy=sockets.target`.
- `local-ai-gate.service` — `Type=notify`, `WatchdogSec=30`, `User=spark`, `Group=spark`,
  `ExecStart=… spark gate`, `Sockets=` both, `LoadCredential=llamaswap-key:…/gate.key`,
  `LoadCredential=ntfy-token:…/ntfy-gate.header`, `EnvironmentFile=/etc/local-ai/values.env`,
  `Environment=` `SPARK_REGISTRY`, `SPARK_STATE`, `SPARK_GATE_STATE`, `SPARK_LAUNCH`,
  `SPARK_LLAMASWAP_URL=http://127.0.0.1:900`, `HF_HOME`, `Restart=always`, `RestartSec=2`,
  `StartLimitIntervalSec=0`, `OOMScoreAdjust=-900`, `TimeoutStopSec=30`, `After=` llama-swap,
  `OnFailure=local-ai-notify@%n.service`; sandbox: `NoNewPrivileges=yes`, `ProtectSystem=strict`,
  `ReadWritePaths=/var/lib/local-ai/gate /var/lib/local-ai/launch /var/lib/local-ai/brake`,
  `ProtectHome=yes`, `PrivateTmp=yes`; no `PrivateDevices=`, `ProtectProc=` or `ProcSubset=`;
  `WantedBy=multi-user.target`.
- `local-ai-notify@.service` — `Type=oneshot`, `DynamicUser=yes`, `StateDirectory=local-ai-notify`,
  `LoadCredential=ntfy-token:…/ntfy-notify.header`, `EnvironmentFile=/etc/local-ai/values.env`,
  `Environment=NOTIFY_FRONT=<p> NOTIFY_GATE=<p> NOTIFY_BRAKE=<p> NOTIFY_LLAMA_SWAP=<p>` (the
  registry's priorities for `front_down`, `gate_down`, `brake_down`, `llama_swap_down`),
  `ExecStart=/usr/local/libexec/local-ai-notify %i`; `NoNewPrivileges=yes`,
  `ProtectSystem=strict`, `ProtectHome=yes`, `PrivateTmp=yes`, `PrivateDevices=yes`; no
  `[Install]`.
- `stack/host/local-ai-notify` (POSIX `sh`): its argument the failed unit; the type and the words
  from it (Task 6's `*_down` sentences, word for word); the result in words from
  `$MONITOR_SERVICE_RESULT` (`exit-code`, `signal`, `core-dump` → *it crashed*; `watchdog` → *it
  stopped answering*; `oom-kill` → *it ran out of memory*; `timeout` → *it timed out*; anything
  else → *it failed*); the time, `date +%H:%M`; nothing sent for a priority of `off`; at most one
  alert per unit per `NOTIFIER_EVERY_S` (300), kept as a stamp file in `$STATE_DIRECTORY`; then
  `"${CURL:-/usr/bin/curl}" --fail --silent --show-error --max-time 10 -H
  "@$CREDENTIALS_DIRECTORY/ntfy-token" -H "Priority: <p>" --data-binary "<text>"
  "$NTFY_URL/$NTFY_TOPIC_NOTIFY"`; never a journal line. (`CURL` is for the tests; the unit never
  sets it.)
- The brake's unit — `LoadCredential=llamaswap-key:…/brake.key`,
  `LoadCredential=ntfy-token:…/ntfy-brake.header`, `EnvironmentFile=/etc/local-ai/values.env`,
  `ExecStart=… spark brake --key-credential llamaswap-key`,
  `Environment=SPARK_GATE_STATE=/var/lib/local-ai/gate`,
  `Environment=SPARK_LLAMASWAP_URL=http://127.0.0.1:900`, `StartLimitIntervalSec=0`,
  `OnFailure=local-ai-notify@%n.service`; no `llama-swap.env`.
- The pull's unit — `User=spark-pull`, `Group=spark-pull`, `UMask=0027`,
  `XDG_CACHE_HOME=/var/lib/local-ai/pull-cache`, `CUDA_CACHE_PATH` gone.
- `bootstrap.sh`: `ROOT_UNITS` is `render.UNITS`; `ENABLED_UNITS` every unit with an `[Install]`.

**Tests** (`spark/tests/test_units.py` on the rendered units, unless named):

- `test_9100_is_held_by_a_socket_unit_that_never_gives_up` — the front's socket as above.
- `test_the_front_gate_and_brake_never_give_up_restarting` — each: `Restart=always`, `RestartSec=2`,
  `StartLimitIntervalSec=0`.
- `test_the_gates_sockets_are_0660_spark_with_their_groups` — both sockets as above.
- `test_the_front_only_wants_llama_swap_never_requires_it` — `Wants=` and `After=` name it; no
  `Requires=`, `BindsTo=` or `PartOf=` does.
- `test_the_front_and_the_gate_are_notify_units_with_watchdogs` — `Type=notify`, `WatchdogSec=30`.
- `test_the_front_reaches_only_localhost_and_spools_under_private_tmp` — `IPAddressDeny=any`,
  `IPAddressAllow=localhost`, `PrivateTmp=yes`, `MemoryMax=512M`.
- `test_the_front_runs_as_spark_front_in_spark_users` — `User=spark-front`,
  `SupplementaryGroups=spark-users`.
- `test_keys_and_tokens_arrive_by_loadcredential_never_the_environment` — each credential as above;
  no `EnvironmentFile=` of the front, the gate, the brake or the notifier is under
  `/etc/local-ai/secrets`.
- `test_only_llama_swap_reads_internal_keys_env` — across every rendered unit.
- `test_the_gate_keeps_dev_nvidia_and_other_processes_visible` — no `PrivateDevices=yes`,
  `ProtectProc=` or `ProcSubset=` in the gate's unit.
- `test_onfailure_is_on_the_four_services` — the front, the gate, the brake and llama-swap; not
  compose or the pull.
- `test_graceful_shutdown_fits_inside_timeout_stop_sec` — `TimeoutStopSec=30` on the front and the
  gate, above `front.main`'s and `gate.main`'s `GRACEFUL_S`, 20.
- `test_the_new_services_score_minus_900` — `OOMScoreAdjust=-900` on the front and the gate.
- `test_the_notifier_runs_ubuntus_curl_as_a_dynamic_user` — its unit as above.
- `test_the_notifier_carries_the_registrys_priorities` — `front_down: off` in the registry →
  `NOTIFY_FRONT=off`; the others `high`.
- `test_the_pull_runs_as_spark_pull_with_umask_0027` — as above.
- `test_the_brake_reads_its_key_by_credential` — as above.
- `test_every_rendered_unit_passes_systemd_analyze_verify` — skipped where `systemd-analyze` is
  missing: `systemd-analyze verify` on each, with its output holding no complaint but a missing
  binary or user this machine lacks.

`spark/tests/test_notifier_script.py` (the script run with a stand-in `curl` that records its
arguments and data, a stand-in `date`, and temporary credential and state folders):

- `test_the_notifier_sends_unit_result_and_time_only` — `local-ai-gate.service`, `exit-code`, 09:14
  → one `curl`, its arguments as above, `Priority: high`, its data Task 6's `gate_down` text.
- `test_each_result_reads_in_words` — the five results, and an unknown one, give their words.
- `test_the_notifier_sends_at_most_one_alert_per_unit_per_5_minutes` — the gate twice 10 s apart →
  one `curl`; the front then → one; the gate at + 301 s → another.
- `test_the_token_reaches_curl_as_a_header_file_never_argv` — the arguments hold
  `@<credentials>/ntfy-token`, never the token's text.
- `test_an_off_priority_sends_nothing` — `NOTIFY_GATE=off` → no `curl`, exit 0.
- `test_the_script_and_messages_agree` — for each of the four units, the script's text equals
  `messages.notification`'s for the same unit, result and time.

`spark/tests/test_polkit.py`, `test_bootstrap.py`:

- `test_spark_admin_starts_stops_and_restarts_the_six_services` — yes for each of the six, each
  verb.
- `test_the_sockets_and_the_notifier_are_not_on_the_rule` — `start` on each socket and on
  `local-ai-notify@local-ai-gate.service` → not handled.
- `test_the_rule_names_exactly_the_services_render_writes` — the rule's list is
  `render.POLKIT_UNITS`.
- `test_install_units_installs_and_enables_the_sockets` — `ROOT_UNITS` is `render.UNITS`;
  `ENABLED_UNITS` holds the three sockets, the front and the gate, and not the notifier or the
  pull.

**Steps:**

- [ ] **Step 1:** the failing tests; run them: they fail (the templates missing).
- [ ] **Step 2:** the templates, the script, render, the rule, bootstrap's lists, `make lint`'s
  list; the tests pass; `make test lint`.
- [ ] **Step 3: Commit.** **On the Spark:**

```bash
git add stack/templates/local-ai-front.socket stack/templates/local-ai-front.service \
  stack/templates/local-ai-gate-status.socket stack/templates/local-ai-gate-control.socket \
  stack/templates/local-ai-gate.service stack/templates/local-ai-notify@.service stack/host/local-ai-notify \
  stack/templates/local-ai-brake.service stack/templates/local-ai-pull.service stack/host/50-local-ai.rules \
  stack/host/bootstrap.sh spark/src/spark/render.py Makefile spark/tests/test_units.py \
  spark/tests/test_notifier_script.py spark/tests/test_polkit.py spark/tests/test_bootstrap.py spark/tests/test_render.py
git commit -m "feat(stack): 🤖 units for the front, the gate and the failure notifier; the polkit rule's six" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 24 [Spark]: bootstrap for 2a — the users, root-only secrets, the cache moves to spark-pull

**Files:**

- Modify: `stack/host/bootstrap.sh`, `spark/tests/test_bootstrap.py`, `spark/src/spark/doctor.py`
  (`secrets_folder`, `spark_folders`), `spark/tests/test_doctor.py`, `spark/src/spark/models.py`,
  `spark/tests/test_models.py`, `website/how-to/secret-files.md`, `website/how-to/bootstrap.md`

**Interfaces:**

- `users_and_groups` gains `spark-front` (system, its own group, no home, `nologin`, in
  `spark-users`) and `spark-pull` (system, its own group, home `/var/lib/local-ai`, `nologin`).
- `directories`: `/etc/local-ai/secrets` becomes `root:root 0700`, and each file in it `root:root
  0600` (root writing in a folder only root controls); `/var/lib/local-ai/gate`,
  `/var/lib/local-ai/launch`, `…/launch/tickets`, `…/launch/refusals` and
  `/var/lib/local-ai/whisper-tmp` are made `spark:spark 0750`; `/var/lib/local-ai/pull-cache`
  `spark-pull:spark-pull 0750`.
- `hf_cache_to_pull` — `/var/lib/local-ai/hf` becomes `spark-pull:spark`, `2750` at the top and its
  folders setgid, so `spark` reads every model file through its group; hf_xet's logs included. It
  refuses, changing nothing and naming why, while `local-ai-llama-swap`, `local-ai-brake` or
  `local-ai-pull` is active, or `fs.protected_hardlinks` isn't 1. It changes owners with `chown -R
  -P --no-dereference`, so it never follows a link (the lesson: root never writes through a path
  `spark` controls; with every `spark` unit stopped, nothing of `spark`'s runs to race it). It
  leaves `hf/tmp` as it is while the deployed registry still names it as whisper's `--tmp-dir`.
- `notifier` — installs `stack/host/local-ai-notify` as `/usr/local/libexec/local-ai-notify`, `0755
  root:root`.
- `doctor.secrets_folder` — `root:root 700` and closed to Dan; `doctor.spark_folders` — adds the
  gate's, launch's and whisper's folders, and the cache `spark-pull:spark 2750`.
- `models.pull` no longer makes whisper's tmp-dir.
- `secret-files.md` — the 2a files: `internal-keys.env` (`LLAMASWAP_KEY_FRONT`, `_GATE`, `_BRAKE`);
  `front.key`, `gate.key`, `brake.key`, each key generated once and written to its raw file and to
  `internal-keys.env`, never displayed; `client-keys.sha256`, the digests of the existing client
  keys, one `<key name> <hex>` line each; the three ntfy headers (Task 2's); hash checks that print
  only *match*; making a new client key after 2a; and `llama-swap.env` kept, `0600 root:root`, for
  the rollback until the close (Task 49). Every block in it run first against a temporary folder
  standing in for `/etc/local-ai/secrets`, as Phase 1's were.

**Tests** (`spark/tests/test_bootstrap.py`, on dry runs and stand-ins as Phase 1's, unless named):

- `test_bootstrap_adds_spark_front_in_spark_users_and_spark_pull` — the dry run's `useradd` lines
  for both, and `usermod -aG spark-users spark-front`.
- `test_the_secrets_folder_becomes_root_only_and_its_files_0600` — `install -d -o root -g root -m
  0700 /etc/local-ai/secrets`; for a stand-in folder with two files, each `chmod 0600` and `chown
  root:root`.
- `test_the_gate_launch_and_whisper_tmp_folders_are_sparks` — `install -d -o spark -g spark -m 0750`
  for each of the five; `pull-cache` for `spark-pull`.
- `test_the_cache_moves_to_spark_pull_with_a_setgid_group` — a stand-in cache (folders, files, a
  link) and logging `chown`/`chmod` → `chown -R -P --no-dereference spark-pull:spark`, `chmod 2750`
  on the top and `g+s` on each folder.
- `test_the_cache_move_refuses_while_sparks_units_run` — `systemctl` reporting llama-swap active →
  refused, naming it; nothing changed.
- `test_the_cache_move_refuses_without_protected_hardlinks` — `sysctl` reporting 0 → refused.
- `test_hf_tmp_stays_sparks_while_the_registry_names_it` — a deployed registry naming
  `/var/lib/local-ai/hf/tmp` → `hf/tmp` left out of the move; one naming `whisper-tmp` → moved with
  the rest.
- `test_the_notifier_script_is_installed_root_owned` — `install -m 0755 … /usr/local/libexec/local-ai-notify`.
- `test_the_dry_run_prints_every_new_step` — each new step's `==>` line.
- In `test_doctor.py`: `test_secrets_folder_must_be_root_root_700_and_closed` — `root:spark 750`
  now fails, naming `root:root 700`; `test_sparks_folders_include_the_gates_launchs_and_whispers`.
- In `test_models.py`: `test_the_pull_no_longer_makes_whispers_tmp_dir`.

**Steps:**

- [ ] **Step 1:** the failing tests; run them: they fail.
- [ ] **Step 2:** bootstrap, doctor's two checks, the pull; the tests pass; `make test lint`.
- [ ] **Step 3: Docs.** `secret-files.md` and `bootstrap.md` (the 2a re-run: stop llama-swap and the
  brake first, then `make bootstrap`), their blocks run on stand-ins first.
- [ ] **Step 4: Commit.** **On the Spark:**

```bash
git add stack/host/bootstrap.sh spark/tests/test_bootstrap.py spark/src/spark/doctor.py spark/tests/test_doctor.py \
  spark/src/spark/models.py spark/tests/test_models.py website/how-to/secret-files.md website/how-to/bootstrap.md
git commit -m "feat(host): 🤖 bootstrap for 2a: spark-front and spark-pull, root-only secrets, the cache moves to spark-pull" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 25 [Spark]: agent's processes get an OOM score root sets

**Files:**

- Create: `stack/host/local-ai-agent-oom` (the `pam_exec` script), `stack/host/agent-oom.conf` (the
  `user@` drop-in)
- Modify: `stack/host/bootstrap.sh`, `spark/tests/test_bootstrap.py`, `Makefile` (`lint`),
  `website/how-to/bootstrap.md`; and, only if the review below changes them,
  `spark/src/spark/launch.py` (`OOM_ADJ_RESIDENT`, `OOM_ADJ_ON_DEMAND`) and
  `stack/host/earlyoom.default`

**Interfaces:**

- `/usr/local/libexec/local-ai-agent-oom` (`0755 root:root`), run by a root-owned line, `session
  optional pam_exec.so /usr/local/libexec/local-ai-agent-oom`, that bootstrap appends to
  `/etc/pam.d/sshd` once; for `PAM_USER=agent` and `PAM_TYPE=open_session` only, it writes
  `AGENT_OOM_SCORE_ADJ` to the session process's `oom_score_adj` as root, which also sets the floor
  its descendants can't go under.
- `/etc/systemd/system/user@<agent's uid>.service.d/local-ai-oom.conf` — `[Service]`
  `OOMScoreAdjust=<AGENT_OOM_SCORE_ADJ>`, for what runs under `agent`'s user manager, tmux
  included; bootstrap reads the uid from `id -u agent`.
- The value, chosen with the engines' (900 and 1000) and earlyoom's `--prefer`, which adds 300 to an
  engine's score: a job of `agent`'s holding a tenth of memory must score above every engine, so
  earlyoom picks it first. The reasoning goes in a comment beside the value.

**Tests** (`spark/tests/test_bootstrap.py`):

- `test_bootstrap_installs_the_hook_for_sshd_and_agents_user_manager` — the dry run installs the
  script, appends the `pam_exec` line only if it is missing, writes the drop-in with the value,
  and reloads systemd.
- `test_the_hook_acts_only_for_agent` — the script run with a stand-in `/proc`: `PAM_USER=chendaniely`
  → writes nothing; `agent` and `open_session` → writes the value; `agent` and `close_session` →
  nothing.
- `test_the_hook_and_drop_in_are_root_owned_and_not_agents` — the dry run's owners and modes.
- `test_the_values_put_a_large_agent_job_first` — from `launch`'s values, earlyoom's `--prefer`
  bonus and `AGENT_OOM_SCORE_ADJ`: an `agent` process holding 10% of memory scores above every
  engine holding 2%.

**Steps:**

- [ ] **Step 1:** the failing tests; run them: they fail.
- [ ] **Step 2:** the script, the drop-in, bootstrap, the value; the tests pass; `make test lint`.
- [ ] **Step 3: Docs.** `bootstrap.md`: what the hook does, and that Task 37 tests it as `agent`.
- [ ] **Step 4: Commit.** **On the Spark:**

```bash
git add stack/host/local-ai-agent-oom stack/host/agent-oom.conf stack/host/bootstrap.sh spark/tests/test_bootstrap.py \
  Makefile website/how-to/bootstrap.md
git commit -m "feat(host): 🤖 agent's processes get an OOM score that root sets and agent can't lower" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

  (Add `spark/src/spark/launch.py` and `stack/host/earlyoom.default` to the `git add` if the review
  changed them.)

***

### Task 26 [Spark]: spark status for 2a

**Files:**

- Modify: `spark/src/spark/status.py`, `spark/tests/test_status.py`, `Makefile` (`status`'s help)

**Interfaces:**

- `spark status [--json]` — a member of `spark-admin` (by `os.getgroups()`) reads
  `GET /v1/status` on the control socket, anyone else on the status socket, both through
  `GateClient` with a 3 s timeout; no key, and no call to llama-swap. It exits 0 whatever it finds.
- `format_status(view: dict, *, tz) -> str` — the plan's layout (*What you see in Phase 2a*), its
  rows in this order: the header (`<host> · <available> GiB available of <total> · <n> above the
  brake's <brake> GiB line`), `free for a load`, `loaded`, `not loaded` (with a model's brake mark
  under it), `waiting`, `paused`, `held`, `recent`, `health`; and, when it isn't 0, an
  `unaccounted` line under `free for a load`. The words for what the example doesn't show:
  - paused, automatic: `paused      since 03:12 (19.6 GiB available) · resumes by itself after 5
    min above 28 GiB available`; waiting for Dan: `paused      since 03:12 (19.6 GiB available) ·
    waits for you: make brake-release`;
  - held: `held        40 GiB for you until 18:00 · spark make-room --done ends it` (`until
    a reboot` without an end);
  - unaccounted: `unaccounted 5 GiB (idle 117, less 48 available and 64 of footprints)`;
  - the oldest request, in a loaded row: `answering 1 (oldest 3 min)` when it is a minute or more;
  - ntfy failing: `ntfy failing since 08:52`.
- The gate not answering: the header from `/proc/meminfo` (the brake line from the deployed
  registry), then `gate        not answering: what's loaded, waiting, held or paused is unknown
  until it's back · make doctor`.
- `--json` — the `StatusView` as the gate gave it.

**Tests** (`spark/tests/test_status.py`; a gate stand-in on a short Unix socket):

- `test_status_prints_the_plans_example_moment_line_for_line` — the `StatusView` of the plan's
  moment (122 GiB in all, 48 available, the residents loaded with Gemma answering 1, the coder
  marked from 03:12 seen using 26, `agent` waiting 2 min of 10 for Dan, the brake fired at 03:12 and
  released at 03:40, `agent`'s pi session since 08:40, the two *recent* lines, the key checked at
  09:00) → exactly the plan's fourteen lines, copied into the test.
- `test_agents_status_shows_only_its_refusals_and_no_dans_processes` — a view filtered as the
  status socket gives it → no Dan refusal; a holder shown as *a process of Dan's*.
- `test_status_shows_paused_and_held_when_they_stand` — the two `paused` forms and the `held` form
  above, each word for word.
- `test_unaccounted_memory_is_idle_less_available_less_footprints` — idle 117, 48 available, 64 of
  footprints → the `unaccounted 5 GiB` line; 0 → no line.
- `test_the_oldest_request_shows_from_a_minute` — 3 min → `(oldest 3 min)`; 40 s → nothing added.
- `test_notifications_failing_since_shows` — `failing_since` 08:52 → `ntfy failing since 08:52`.
- `test_with_the_gate_down_status_says_so_and_shows_memory` — `GateUnavailable` → the header from
  memory and the `gate` line above; exit 0.
- `test_json_carries_the_same_for_the_menu_bar` — `--json` prints the stand-in's view unchanged.
- `test_status_reads_the_socket_its_account_may` — with `spark-admin`'s gid in `os.getgroups()`, the
  control socket; without, the status socket.
- `test_status_sends_no_key` — nothing read from `SPARK_API_KEY`; no HTTP call but the gate's.

**Steps:**

- [ ] **Step 1:** the failing tests; run them: they fail (Phase 1's status asks llama-swap).
- [ ] **Step 2:** `status.py`; the tests pass; `make test lint`.
- [ ] **Step 3: Commit.** **On the Spark:**

```bash
git add spark/src/spark/status.py spark/tests/test_status.py Makefile
git commit -m "feat(spark): 🤖 spark status in plain words: room, loaded, waiting, paused or held, recent, health" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 27 [Spark]: the commands — load, unload, pin, make-room, release, logs, sessions

**Files:**

- Create: `spark/src/spark/commands.py`, `spark/tests/test_commands.py`
- Modify: `spark/src/spark/cli.py`, `Makefile` (`logs` takes `front`, `gate` and `notify`;
  `brake-release`'s help), `spark/tests/test_makefile.py`, `spark/tests/test_tested_against.py`
  (the import check gains `spark.commands`), `website/how-to/deploy.md` (`spark` on your `PATH`)

**Interfaces:**

- `spark load <model|role>`, `spark unload <model|role>`, `spark pin <model> [duration]`, `spark
  unpin <model>`, `spark make-room <size> [--for <duration>]`, `spark make-room --all`, `spark
  make-room --done`, `spark logs <model> [-n N]` — each on the control socket; `spark session
  start --model M --pid P --label L`, `spark session renew <id>`, `spark session end <id>` — on the
  status socket, for 2b's hooks. Each prints Task 6's confirmation, or the refusal's message (exit
  1).
- A question (`make-room`'s confirmation) is asked only on a terminal: without one it exits 2,
  saying to run it in a terminal or add `--yes`; `--yes` answers yes.
- `parse_size(text) -> Decimal` — `41G`, `41GiB`, `41` → 41; `parse_duration(text) -> int` — `8h`
  → 28800, `90m` → 5400, `2d` → 172800; anything else a `ValueError` naming it.
- `make logs s=front|gate|notify` — `journalctl -u local-ai-front.service`, `-u
  local-ai-gate.service`, and `-u 'local-ai-notify@*'`.
- `deploy.md`: bare `spark` on the Spark is the deployed one, linked once into `~/.local/bin`.

**Tests** (`spark/tests/test_commands.py`; a gate stand-in answering each route as Task 9 gives
it):

- `test_load_says_loaded_in_n_seconds_and_how_to_keep_it` — the stand-in's `{seconds: 24}` →
  *Loaded the coder in 24 s. It unloads after 60 min idle; `spark pin coder` keeps it.*; a refusal →
  its message, exit 1.
- `test_unload_waits_for_requests_in_flight_and_says_so` — *Unloading the coder once its 1 request
  in flight finishes…*, then *Unloaded the coder.*
- `test_pin_with_a_duration_says_until_when_and_how_to_end_it` — `pin coder 8h` at 10:00 → the
  stand-in got `until` 18:00; *The coder stays loaded until 18:00 (loaded it first, 24 s). `spark
  unpin coder` ends the pin.*
- `test_unpin_says_when_it_unloads` — *The pin on the coder ended; it unloads after 60 min idle.*
- `test_make_room_shows_the_list_and_asks_once` — the plan's make-room example → the plan's list
  block, word for word, then `Unload 1? [y/N]`; `y` → `POST /v1/make-room`, then the plan's
  *Unloaded the coder. 50 GiB is free for a load, …* sentence; anything else → nothing posted, exit
  1.
- `test_make_room_asked_too_much_offers_the_most` — *Unloading everything leaves 61 GiB free for a
  load, not 70. Free 61 and hold it? [y/N]*
- `test_make_room_for_sets_its_end` — `--for 8h` → `for_s` 28800.
- `test_make_room_all_and_done` — `--all` → `{all: true}`, the plan's `--all` sentence after one
  question; `--done` → `POST /v1/release`, *Hold ended, all 40 GiB of it unused. Nothing to reload:
  the coder loads on its next request.*
- `test_a_question_without_a_terminal_refuses_without_yes` — stdin not a terminal → exit 2 and the
  sentence; `--yes` → proceeds.
- `test_logs_reads_through_the_control_socket` — `logs coder -n 5` → `GET /v1/logs/coder?n=5`, each
  line printed, escaped.
- `test_session_commands_use_the_status_socket` — `start` → `POST /v1/sessions` on the status
  socket, the id printed; `renew` and `end` likewise.
- `test_no_command_sends_a_key` — no command reads a key from the environment.
- `test_sizes_and_durations_parse` — the cases above, and `x` refused.
- In `test_makefile.py`: `test_make_logs_takes_front_gate_and_notify` — with a stand-in
  `journalctl`, each target's arguments.

**Steps:**

- [ ] **Step 1:** the failing tests; run them: they fail (`spark.commands` missing).
- [ ] **Step 2:** `commands.py`, the CLI, the Makefile; the tests pass; `make test lint`.
- [ ] **Step 3: Docs.** `deploy.md`: the link into `~/.local/bin`, its block run first with a
  temporary `HOME`.
- [ ] **Step 4: Commit.** **On the Spark:**

```bash
git add spark/src/spark/commands.py spark/tests/test_commands.py spark/src/spark/cli.py Makefile \
  spark/tests/test_makefile.py spark/tests/test_tested_against.py website/how-to/deploy.md
git commit -m "feat(spark): 🤖 spark load, unload, pin, make-room, release and logs, through the gate's control socket" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 28 [Spark]: spark apply, 1 — the diff, and what each change restarts

**Files:**

- Modify: `spark/src/spark/apply.py`, `spark/tests/test_apply.py`, `Makefile` (`restart-front`)

**Interfaces:**

- `diff_text(files: dict[str, str], etc: Path, installed: dict[str, str | None]) -> str` — a
  unified diff of each file that changes: what `apply` deploys itself against `etc`, root's staged
  copies against root's installed ones; the app by file name.
- `RestartPlan(now: list[str], after_quiet: list[str])`; `plan_restarts(changed: list[str],
  app_changes: list[str], registry_old, registry_new, outdated: list[str], *, restart_front: bool)
  -> RestartPlan` — llama-swap's config or unit → `after_quiet`; the front → `after_quiet` when a
  file of `FRONT_MODULES` changed, or the registry's model names, roles or keys did, or
  `restart_front`; the gate and the brake → `now` for any change to the app or the registry; the
  compose unit → `now`, as Phase 1. A unit that isn't running is never restarted.
- `RUNNING_UNITS` gains `local-ai-front.service` and `local-ai-gate.service`.
- `spark apply --restart local-ai-front.service` (`make restart-front`) — for a change of the key
  digests, which `apply` can't see; the front restarts through the same wait.

**Tests** (`spark/tests/test_apply.py`):

- `test_apply_shows_a_diff_of_what_it_would_change` — one changed line in `llama-swap.yaml` → the
  output holds a unified diff with that line, as `-` and `+`; a changed unit → its staged-against-
  installed diff.
- `test_a_change_to_the_gates_code_restarts_the_gate_and_the_brake_at_once` —
  `spark/src/spark/gate/admission.py` changed → `now` holds the brake and the gate; `after_quiet`
  is empty.
- `test_a_change_elsewhere_in_the_app_never_restarts_the_front` — `spark/src/spark/leakcheck.py`
  changed → the front in neither list.
- `test_a_change_to_the_fronts_modules_restarts_it_after_the_quiet_moment` —
  `spark/src/spark/front/parse.py`, and `spark/src/spark/messages.py`, each → the front in
  `after_quiet`.
- `test_a_change_to_the_registrys_names_or_roles_restarts_the_front` — a role added → the front in
  `after_quiet`; a footprint changed → not.
- `test_llama_swaps_restart_waits_for_the_quiet_moment` — `llama-swap.yaml` changed → llama-swap in
  `after_quiet`.
- `test_restart_front_waits_too` — `restart_front=True` → the front in `after_quiet`.
- `test_a_unit_that_isnt_running_is_never_restarted` — the front stopped, its module changed → in
  neither list.

**Steps:**

- [ ] **Step 1:** the failing tests; run them: they fail.
- [ ] **Step 2:** `apply.py`'s diff and plan, `make restart-front`; the tests pass; `make test
  lint`.
- [ ] **Step 3: Commit.** **On the Spark:**

```bash
git add spark/src/spark/apply.py spark/tests/test_apply.py Makefile
git commit -m "feat(spark): 🤖 spark apply shows the diff, and restarts the front only for its own modules" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 29 [Spark]: spark apply, 2 — the quiet wait, drain now and apply-now

**Files:**

- Modify: `spark/src/spark/apply.py`, `spark/tests/test_apply.py`, `Makefile` (`apply`'s and
  `apply-now`'s help), `spark/src/spark/doctor.py` (S17's check), `spark/tests/test_doctor.py`,
  `website/scenarios/s17-changing-models.md` (a dated note: built, the drill in Task 48),
  `website/how-to/deploy.md` (*Every later change*)

**Interfaces:**

- `wait_for_quiet(gate: GateClient, *, quiet_s=QUIET_S, deadline_s=APPLY_DEADLINE_S, clock, say,
  ask) -> "quiet" | "drained" | "declined"` — `GET /v1/quiet` every second; while it waits it says,
  once per change, Task 6's *Waiting for a quiet moment: the coder answered 20 s ago, and it needs
  60 s with nothing in flight. Ctrl-C leaves everything as it was; `make apply-now` restarts now.*
  At the deadline it asks Task 6's *No quiet minute in 15 minutes. Drain now, holding new requests
  while the 2 in flight finish? [y/N]*; yes → `POST /v1/drain-all`, then waits for nothing in
  flight ("drained"); no → "declined". Ctrl-C at any point → nothing changed, exit 130.
- The order, when a restart waits for the quiet moment: render and validate; the diff; the wait;
  only then the app's sync and the files written; `POST /v1/apply/begin` (the gate refuses loads
  with `restarting` after their wait meanwhile); the restarts; the gate's `health.llama_swap` `ok`
  within 30 s; `POST /v1/apply/end` (the registry re-read, the residents reloaded,
  `apply_restarted` sent); on any failure after a drain, `POST /v1/undrain-all`.
- `--now` (`make apply-now`) — asks Task 6's *This restarts the model service now and cuts off the
  2 requests in flight (pi on the Mac, agent). Continue? [y/N]*, naming each from `/v1/quiet`;
  without requests in flight it doesn't ask.
- A change that restarts nothing after the quiet moment applies at once.
- With the gate not answering: when llama-swap or the front must restart, it refuses unless
  `--now` (*apply can't see what's in flight while the gate isn't answering; `make apply-now`
  restarts anyway*); when llama-swap isn't running, it deploys without asking the gate (the
  cutover's case, Task 34).
- doctor's S17 check — `GET /v1/quiet` answers.

**Tests** (`spark/tests/test_apply.py`, a gate stand-in; `spark/tests/test_doctor.py`):

- `test_apply_waits_for_60s_with_nothing_in_flight_and_says_what_its_waiting_on` — the coder
  answered 20 s ago → the waiting sentence once; `quiet_for_s` reaching 60 → it goes on.
- `test_ctrl_c_during_the_wait_leaves_nothing_changed` — `KeyboardInterrupt` in the wait → no file
  written, no sync, no restart; exit 130.
- `test_after_15_minutes_apply_offers_drain_now` — never quiet for 900 s (injected clock) → the
  question; `y` → `drain-all`, then on with nothing in flight; `N` → exit 1, nothing changed.
- `test_drain_now_holds_new_requests_until_those_in_flight_finish` — after `drain-all`, it waits
  for `/v1/quiet`'s `inflight` to empty; a failure after it → `undrain-all` posted.
- `test_nothing_is_written_until_the_drain_is_done` — the sync and the writes come after the wait
  returns, never before.
- `test_apply_now_names_the_requests_it_would_cut_off_and_asks` — two in flight → the question
  naming *pi on the Mac, agent*; `y` → restarts; `N` → nothing.
- `test_the_gate_is_told_before_and_after_the_restart` — `begin` before `systemctl restart`, `end`
  after the gate reports llama-swap `ok`.
- `test_a_change_needing_no_restart_applies_at_once` — a footprint changed → no wait; the gate and
  the brake restarted.
- `test_with_the_gate_down_apply_restarts_llama_swap_only_with_now` — `GateUnavailable`, llama-swap
  running and changed → refused with the sentence, exit 1.
- `test_with_llama_swap_stopped_apply_deploys_without_the_gate` — llama-swap inactive, the gate not
  answering → files deployed, nothing waited for, nothing restarted.
- `test_doctor_checks_s17` — S17's check passes when `/v1/quiet` answers, and fails naming the gate
  otherwise.

**Steps:**

- [ ] **Step 1:** the failing tests; run them: they fail.
- [ ] **Step 2:** the wait, the order, `--now`, doctor's S17 check; the tests pass; `make test
  lint`.
- [ ] **Step 3: Docs.** `deploy.md`'s *Every later change*; S17's dated note (built, not yet
  verified).
- [ ] **Step 4: Commit.** **On the Spark:**

```bash
git add spark/src/spark/apply.py spark/tests/test_apply.py Makefile spark/src/spark/doctor.py spark/tests/test_doctor.py \
  website/scenarios/s17-changing-models.md website/how-to/deploy.md
git commit -m "feat(spark): 🤖 make apply waits for a quiet minute, offers drain now, and restarts llama-swap under loaded models" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 30 [Spark]: spark doctor for 2a

**Files:**

- Modify: `spark/src/spark/doctor.py`, `spark/tests/test_doctor.py`; the scenario pages S01, S02,
  S03, S05 and S14 (a dated line each: its doctor check exists)

**Interfaces** (each a `Check`; doctor never prints a key, a URL, a topic or a token):

- `front` — 9100's `/health` answers 200; a request without a key gets 401; `GET /unload`, `POST
  /api/models/unload/x` and `GET /logs` get 404 `route_not_served`.
- `llama-swap` — its unit active; `SPARK_API_KEY` refused at `http://127.0.0.1:900/running` (401).
- `gate` — `GET /v1/status` on the control socket answers; its `health` shows the four services.
- `brake` — its start check passed (Phase 1's, folded into `stack units`).
- `ntfy` — the gate's `health.ntfy`: ok, or *notifications failing since HH:MM: `make logs
  s=gate`*.
- `sockets` — each socket file (`/run/local-ai/gate-status.sock`, `gate-control.sock`) a socket,
  `spark`'s, `0660`, its group.
- `stack units` — the six services and the three sockets active.
- `version drift` — `llama-swap -version` names v257; `llama-server --version` names b11146;
  `whisper-server`'s binary is the one under `v1.9.4`; the deployed venv's uvicorn is the lock's;
  each mismatch named.
- `llama-swap config` — the deployed `/opt/local-ai/etc/llama-swap.yaml` against the vendored
  schema.
- `S01` — the registry's on-demand idle time is 60 and the residents never idle-unload; `S02` —
  `POST /v1/make-room/plan` with 1 GiB answers, unloading nothing; `S03` — a made-up model through
  the front gets 404 with `model_not_found`'s words; `S05` — the brake active, its start check
  passed, the gate's activity record fresh; `S14` — `OnFailure=` and `StartLimitIntervalSec=0` on
  the four, by `systemctl show`, and the notifier's script installed; `S17` (Task 29).
- `a model, end to end` — the embeddings through the front with `SPARK_API_KEY`.
- `spark doctor --full` adds: a 170 MB upload to `/v1/audio/transcriptions` through the front; the
  privacy canary — a unique string sent through chat and speech-to-text, then looked for in the
  journal, the gate's state and records, launch's records and the front's spool (which must be
  empty), found in none.

**Tests** (`spark/tests/test_doctor.py`, a `Probe` stand-in; one pass and one failure per check,
and):

- `test_routes_outside_the_list_must_get_route_not_served` — the front answering 200 for `/unload`
  → FAIL naming `/unload`.
- `test_a_client_key_must_be_refused_at_llama_swaps_port` — 900 answering 200 to your key → FAIL;
  401 → ok.
- `test_ntfy_prints_a_verdict_never_the_url` — failing since 08:52 → FAIL with that time; a stand-in
  URL nowhere in the output.
- `test_socket_modes_and_groups_are_checked` — `0666` → FAIL naming it; another group → FAIL.
- `test_version_drift_names_the_component` — `v256` from llama-swap → FAIL naming v256 and v257;
  uvicorn 0.53.0 deployed → FAIL.
- `test_the_deployed_config_is_checked_against_v257s_schema` — `swapp` in it → FAIL naming it.
- `test_each_2a_scenario_has_its_check` — each of the six, failing on its own fault: idle 30 (S01);
  the plan route refusing (S02); the front answering 200 for a made-up model (S03); a stale activity
  record (S05); no `OnFailure=` on the brake (S14); `/v1/quiet` unanswered (S17).
- `test_full_runs_the_upload_and_the_canary` — without `--full`, neither runs; with it, both, and
  the canary found in the journal stand-in → FAIL naming the journal.
- `test_doctor_still_never_prints_a_key` — Phase 1's, over every new check.

**Steps:**

- [ ] **Step 1:** the failing tests; run them: they fail.
- [ ] **Step 2:** the checks; the tests pass; `make test lint`.
- [ ] **Step 3: Docs.** The five scenario pages' dated lines.
- [ ] **Step 4: Commit.** **On the Spark:**

```bash
git add spark/src/spark/doctor.py spark/tests/test_doctor.py website/scenarios/s01-morning-start.md \
  website/scenarios/s02-big-job.md website/scenarios/s03-doesnt-fit-interactive.md \
  website/scenarios/s05-memory-critically-low.md website/scenarios/s14-gate-down.md
git commit -m "feat(spark): 🤖 spark doctor checks the front, the gate, the brake, ntfy and each of 2a's scenarios" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 31 [Spark]: spark clients — agent's pi waits 15 minutes

**Files:**

- Modify: `spark/src/spark/clients.py`, `spark/tests/test_clients.py`, `website/how-to/pi.md`

**Interfaces:**

- `spark clients pi --write [--http-idle-timeout-ms N]` — with the flag, also merges
  `{"httpIdleTimeoutMs": N}` into `~/.pi/agent/settings.json`: the old file backed up with its
  mode, every other setting kept, a file pi can't read (not JSON, not an object) refused before
  anything is written. Without the flag, `settings.json` is untouched. The Mac's `make clients`
  doesn't change.
- `pi.md`: `agent` runs the deployed CLI against the deployed registry,
  `/opt/local-ai/app/.venv/bin/spark clients pi --write --registry /opt/local-ai/etc/models.yaml
  --http-idle-timeout-ms 900000`; and `agent`'s key, after 2a, comes from secret-files.md's *a new
  client key*, its digest in `client-keys.sha256`.

**Tests** (`spark/tests/test_clients.py`):

- `test_agents_settings_get_the_15_minute_idle_timeout` — no file → `{"httpIdleTimeoutMs": 900000}`.
- `test_other_settings_are_kept_and_backed_up` — `{"theme": "dark"}` at mode 0600 → both keys; the
  backup holds the old text, mode 0600.
- `test_without_the_flag_settings_are_untouched` — a settings file's bytes and time unchanged.
- `test_a_settings_file_that_isnt_an_object_is_refused` — `[]` → refused, naming the file, nothing
  written.

**Steps:**

- [ ] **Step 1:** the failing tests; run them: they fail.
- [ ] **Step 2:** `clients.py`; the tests pass; `make test lint`.
- [ ] **Step 3: Docs.** `pi.md`, its blocks run first as you with a temporary `HOME`.
- [ ] **Step 4: Commit.** **On the Spark:**

```bash
git add spark/src/spark/clients.py spark/tests/test_clients.py website/how-to/pi.md
git commit -m "feat(clients): 🤖 spark clients sets agent's pi to wait 15 minutes for a response" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 32 [Spark]: the deploy runbook — the first deploy, its rollback, the engines' log

**Files:**

- Modify: `website/how-to/deploy.md`, `website/how-to/index.qmd`, `website/how-to/updates.md`

**What the runbook gains:**

- *Before the first deploy*: 2a's secret files (secret-files.md) and the values file (`ntfy.md`).
- *First deploy*, for a new box: the sockets started before the services.
- *Moving a Phase 1 box to 2a*: Task 34's sequence, word for word, and its rollback to Phase 1's
  layout.
- *When something is wrong*: an engine's lines with `spark logs <model>`, in place of Phase 1's
  `curl` to 9100's `/logs/stream/upstream`, which the front now refuses; `make logs
  s=front|gate|notify`.
- *Every later change*: Task 29's.
- `updates.md`: a uvicorn or Starlette bump runs `test_protocols.py` first, and `make apply` then
  restarts the gate and the brake at once and the front at the quiet moment.

**Steps:**

- [ ] **Step 1:** the runbooks; every block in them run first, the sequences with stand-ins for
  `sudo`, `systemctl` and `make` that log their calls, as this plan's own were (*How the command
  blocks were checked*).
- [ ] **Step 2:** `make test lint`; **on the Spark**, `uv run --frozen --project spark spark docs
  check-scenarios`.
- [ ] **Step 3: Commit.** **On the Spark:**

```bash
git add website/how-to/deploy.md website/how-to/index.qmd website/how-to/updates.md
git commit -m "docs(website): 🤖 deploying 2a: the first deploy, its rollback, reading an engine's log" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

## ⇄ Push point — Tasks 1–32 go to GitHub

- [ ] The Spark session runs leak-guards.md's [*Before every push*](../how-to/leak-guards.md#before-every-push)
  with `phase-2a` as `<branch>`; then **Dan OKs the push** (`git push`). `gh run watch`: CI is
  green, and its `tests` job ran the polkit tests that need Node. Task 33 installs the new rule
  only after this run.

***

### Task 33 [Dan]: bootstrap again, and the secret files for 2a

**Files (the session's record):** `changelog.md`, `README.md` §Current state.

- [ ] **Step 1 [Dan, on the Spark]:** `make bootstrap-dry-run`, and read it. Stop the brake and
  llama-swap, which stops every model, since the cache's move refuses while they run:
  `systemctl stop local-ai-brake.service local-ai-llama-swap.service`, which the polkit rule allows
  without sudo. Then `make bootstrap`,
  and start them again: `systemctl start local-ai-llama-swap.service local-ai-brake.service`. The
  stack serves in Phase 1's layout meanwhile, reading the model files through the group.
- [ ] **Step 2 [Spark]: Check it.** **On the Spark:**

```bash
getent passwd spark-front spark-pull | cut -d: -f1,7
id -nG spark-front
stat -c '%U:%G %a %n' /var/lib/local-ai/hf /var/lib/local-ai/gate /var/lib/local-ai/launch /var/lib/local-ai/launch/tickets /var/lib/local-ai/launch/refusals /var/lib/local-ai/whisper-tmp /var/lib/local-ai/pull-cache
test -x /usr/local/libexec/local-ai-notify && echo "notifier: installed" || echo "notifier: missing"
test -x /usr/local/libexec/local-ai-agent-oom && grep -c 'local-ai-agent-oom' /etc/pam.d/sshd || echo "agent's OOM hook: missing"
```

  Expected: both users with `/usr/sbin/nologin`; `spark-front spark-users`;
  `spark-pull:spark 2750 /var/lib/local-ai/hf`; `spark:spark 750` for the gate's, launch's (and
  its two folders) and whisper's; `spark-pull:spark-pull 750` for the pull's cache;
  `notifier: installed`; `1`. **[Dan, on the Spark]**
  `sudo stat -c '%U:%G %a' /var/lib/local-ai/hf/tmp; sudo -k` reads `spark:spark 750`, left for
  whisper until the cutover. Then `make doctor`: its *secrets folder* and *spark's folders* lines
  pass; the lines that need the cutover (the front, the gate, the sockets, the scenarios) fail, as
  expected until Task 34; the stack serves.
- [ ] **Step 3 [Dan, on the Spark]:** secret-files.md's 2a steps: the three internal keys, each
  written once to its raw file and to `internal-keys.env`; `client-keys.sha256` from the existing
  client keys; each file `0600 root:root`. Its hash checks print only *match*.
- [ ] **Step 4 [Dan, on the Spark]:** `sudo ls -l /etc/local-ai/secrets`, read aloud by name and
  mode only, never a value: the 2a files, the ntfy headers (Task 2), `llama-swap.env` still there
  for the rollback.
- [ ] **Step 5: The record and commit.** **On the Spark:**

```bash
git add changelog.md README.md
git commit -m "docs(machine): 🤖 record bootstrap's 2a re-run and the new secret files" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 34 [Spark]: the cutover — the front, the gate, llama-swap on 900

**Files (the record):** `changelog.md`, `README.md` (§Current state; §Conventions if a rule
changes), `CLAUDE.md` (the gotchas *Never reload llama-swap while models are loaded* and the
reserve: 2a's half built and running, with the date; and README §Conventions with it),
`website/architecture.qmd` (the front, the gate, the three sockets, the notifier, llama-swap at
900, the engines at 800 and up, the brake's idle-first order, and the edges from the gate, the brake
and the notifier to ntfy, all solid), `website/design/plan.md` (*Anyone on the box can take
127.0.0.1:9100* closed as built; *To verify*: the engines bind below 1024 with the capability they
inherit, and the gate receives each caller's uid; a Revisions line), `website/how-to/deploy.md`
(only what the cutover corrects)

- [ ] **Step 1: The rollback, before anything moves.** If the cutover can't be fixed in place, the
  way back is Phase 1's layout, llama-swap on 9100 with `llama-swap.env`'s client keys and Phase
  1's brake key, both still on the box (Task 33). Check that `main` renders it. **On the Spark:**

```bash
d=$(mktemp -d) && git worktree add "$d/main" main && (cd "$d/main" && uv run --frozen --quiet --project spark spark render --out "$d/out") && grep -h -e '-listen' -e 'EnvironmentFile' "$d/out/systemd/local-ai-llama-swap.service"; git worktree remove --force "$d/main"
```

  Expected: `render: 8 files → …`, then `EnvironmentFile=/etc/local-ai/secrets/llama-swap.env` and
  an `ExecStart` ending `-listen 127.0.0.1:9100`.

  The rollback itself, only if it comes to that. **On the Spark** (Dan; sudo asks once):

```bash
sudo systemctl disable --now local-ai-front.service local-ai-front.socket local-ai-gate.service local-ai-gate-status.socket local-ai-gate-control.socket
sudo -k
git worktree add ../local-ai-rollback main
cd ../local-ai-rollback
make apply
make install-units
make apply-now
```

  Then `make doctor` from that worktree passes Phase 1's fifteen checks, and a plan revision says
  what failed.

- [ ] **Step 2:** `make apply`. Expected: it lists root's ten unit files as new or changed, stages
  them, and stops.
- [ ] **Step 3 [Dan, on the Spark]:** `make install-units`. Read every file it shows: they are what
  root will run. It installs root's copies and enables the sockets and the services, and starts
  nothing new.
- [ ] **Step 4 [Dan]: the cutover**, as one block. **On the Spark** (Dan; sudo asks once):

```bash
systemctl stop local-ai-brake.service local-ai-llama-swap.service
make apply
sudo systemctl start local-ai-front.socket local-ai-gate-status.socket local-ai-gate-control.socket
sudo -k
systemctl start local-ai-llama-swap.service local-ai-gate.service local-ai-front.service local-ai-brake.service
```

  Stopping llama-swap stops every model and frees 9100; `make apply` then deploys the config, the
  registry and the app with nothing to wait for (Task 29); the front's socket takes 9100, and the
  gate loads the residents one at a time. The polkit rule lets you stop and start the services
  without sudo; only the sockets need it, and `sudo -k` drops it before anything else runs, so the
  clone's own code never runs with sudo's credential cached (*Lessons from Phase 0*). 9100 is free
  for as long as `make apply` takes, the cutover's one window.
- [ ] **Step 5 [Spark]: Check it.** `make doctor` passes every line. `make status` reads as the
  plan's layout. `make logs s=gate` shows the residents loading one after another. Then
  the listening ports, numbers only. **On the Spark:**

```bash
ss -Hltn | awk '{print $4}' | sed 's/.*://' | sort -n | uniq | tr '\n' ' '; echo
```

  Expected among them: 800, 801, 802, 900 and 9100, and no 5800. `make logs s=front` holds no key,
  header or body. **[Dan, on the Mac]**, through `make tunnel`, pi answers; a request for a made-up
  model reads `model_not_found`'s words. **[Dan, on the phone]**, the web UI chats, searches and
  transcribes.
- [ ] **Step 6 [Dan, as `agent`]: what `agent` can't do.** `sudo -iu agent` on the Spark, then, while
  **[Dan]** runs `systemctl restart local-ai-front.service` in another terminal. **On the Spark, as
  `agent`:**

```bash
python3 -c '
import socket, sys, time
port, won = int(sys.argv[1]), 0
for _ in range(300):
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
        won += 1
    except OSError:
        pass
    finally:
        s.close()
    time.sleep(0.1)
print(f"bound {port} {won} times in 30 s")' 9100
```

  Expected: `bound 9100 0 times in 30 s`. The same block with `900` in place of `9100`, while
  **[Dan]** restarts llama-swap: `bound 900 0 times in 30 s`. Then, still as `agent`:
  `/opt/local-ai/app/.venv/bin/spark status` answers through the status socket;
  `/opt/local-ai/app/.venv/bin/spark make-room --done` is refused at the socket, saying who may; a
  request to `http://127.0.0.1:9100/unload` with `agent`'s key reads `route_not_served`'s words;
  the same key at `http://127.0.0.1:900/running` gets 401. (A key goes to curl on stdin, never its
  command line, as Phase 1's Global Constraints say.)
- [ ] **Step 7:** if any check fails and can't be fixed in place, the rollback (Step 1), then a plan
  revision.
- [ ] **Step 8: The record and commit.** **On the Spark:**

```bash
git add changelog.md README.md CLAUDE.md website/architecture.qmd website/design/plan.md website/how-to/deploy.md
git commit -m "docs(machine): 🤖 the front, the gate and llama-swap on 900 are serving" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 35 [Spark]: a cold boot under llama-swap's sandbox

**Files (the record):** `changelog.md`, `README.md`, `website/design/plan.md` (*To verify*:
`NoNewPrivileges=` and `CapabilityBoundingSet=` from a cold boot; a Revisions line); and, only if an
engine can't load `nvidia-uvm`, `stack/host/bootstrap.sh` and `spark/tests/test_bootstrap.py`
(`nvidia-uvm` in `/etc/modules-load.d/`, test first).

- [ ] **Step 1 [Dan, on the Spark]:** power the box off and on (`sudo poweroff`, then the power
  button), so nothing from the last boot has `nvidia-uvm` loaded.
- [ ] **Step 2: Check it.** **On the Spark:**

```bash
lsmod | grep -c '^nvidia_uvm'
```

  Expected: `1`. Then `make status`: the three residents loaded, one after another (`make logs
  s=gate`); `make doctor` passes. If a resident failed to start for want of `nvidia-uvm` (`spark logs
  <model>`), bootstrap loads it at boot: write that change test first, **[Dan]** re-runs `make
  bootstrap`, and this task runs again.
- [ ] **Step 3: The record and commit.** **On the Spark:**

```bash
git add changelog.md README.md website/design/plan.md
git commit -m "docs(machine): 🤖 record a cold boot under llama-swap's sandbox" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

  (With `stack/host/bootstrap.sh` and its test, if Step 2 changed them.)

***

### Task 36 [Spark]: cap_drop for Open WebUI and SearXNG; SearXNG's request timeout

**Files:**

- Modify: `stack/templates/compose.yaml`, `stack/templates/searxng-settings.yml`,
  `spark/tests/test_render.py`, `changelog.md`, `README.md`, `website/architecture.qmd` (both
  labels, built)

**Interfaces:** both services `cap_drop: [ALL]`; `cap_add:` only a capability its container is shown
to need on the box, each with a comment saying what failed without it; SearXNG's
`outgoing.request_timeout` above its slowest engine's measured answer (Phase 1 saw duckduckgo take
2.7 s against 3 s).

**Tests** (`spark/tests/test_render.py`):

- `test_the_web_containers_drop_every_capability` — each service's `cap_drop` is `[ALL]`.
- `test_each_capability_added_back_carries_its_reason` — every `cap_add` entry has a comment on its
  line.
- `test_searxng_waits_longer_than_its_slowest_engine` — `outgoing.request_timeout` is above 2.7.

**Steps:**

- [ ] **Step 1:** the failing tests; run them: they fail.
- [ ] **Step 2:** `cap_drop: [ALL]`, nothing added back yet, and the timeout; the tests pass;
  `make test lint`; `make apply`; **[Dan, on the Spark]** `make install-units`; `make apply`.
- [ ] **Step 3: Check it, adding back only on evidence.** **[Dan, on the phone]** the web UI end to
  end: log in, chat, an image, a document's embeddings, speech; and a search. Each failure is read
  in `make logs s=open-webui` or `s=searxng`, and the one capability it names is added back, with
  its reason, then Step 2 again. **On the Spark** (Dan; sudo), neither container can bind a port
  below 1024:

```bash
for c in local-ai-open-webui-1 local-ai-searxng-1; do sudo docker exec "$c" python3 -c 'import socket; socket.socket().bind(("127.0.0.1", 900))' 2>&1 | tail -1; done
sudo -k
```

  Expected: `PermissionError: [Errno 13] Permission denied`, twice.
- [ ] **Step 4: Commit.** **On the Spark:**

```bash
git add stack/templates/compose.yaml stack/templates/searxng-settings.yml spark/tests/test_render.py \
  changelog.md README.md website/architecture.qmd
git commit -m "feat(stack): 🤖 the web containers drop every capability they don't need" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 37 [Spark]: agent's OOM score, tested as agent

**Files (the record):** `changelog.md`, `README.md`, `website/design/plan.md` (*`agent`'s own GPU
jobs are outside the gate*: resolved in 2a, or moved to 2b, with a Revisions line);
`website/scenarios/s06-agent-gpu-step.md` only if it moves.

- [ ] **Step 1 [Dan, as `agent`]:** a fresh login (`ssh brightroar-agent` from the Mac), then inside
  tmux. **On the Spark, as `agent`**, in each:

```bash
cat /proc/self/oom_score_adj
python3 -c 'v = int(open("/proc/self/oom_score_adj").read()); open("/proc/self/oom_score_adj", "w").write(str(v - 1))' 2>&1 | tail -1
```

  Expected: Task 25's value, then `PermissionError: [Errno 13] Permission denied`. (As you, today,
  the second line succeeds: an ordinary process may lower its own score until root sets a floor.)
- [ ] **Step 2 [Dan, on the Spark]:** a stand-in job started as `agent` that holds a tenth of memory;
  earlyoom's dry run picks it before every engine. Then it stops.
- [ ] **Step 3:** if either check fails, Task 25's hook comes out in a revert commit, and the item
  moves to 2b with a plan revision.
- [ ] **Step 4: The record and commit.** **On the Spark:**

```bash
git add changelog.md README.md website/design/plan.md
git commit -m "docs(machine): 🤖 agent's processes carry a root-set OOM score" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 38 [Spark]: the CUDA-allocatable ceiling, carefully

**Files:**

- Create: `stack/measure/cuda_ceiling.py`, `spark/tests/test_cuda_ceiling.py`
- Modify: `stack/models.yaml` (`allocatable_gib`, `allocatable_measured`), `website/design/plan.md`
  (rule 9's ceiling; *To verify* resolved; a Revisions line), `CLAUDE.md` (the gotcha's "to be
  measured"), `README.md` (§Hardware, where it gives the ceiling), `cosmicbboy-local-ai.md` (a claim
  this measures, `[adapted]` to `[verified]`, and no other), `changelog.md`

**Interfaces:** `cuda_ceiling.py`, PEP 723 (Python ≥3.12, no dependencies), run with `uv run`:
`main(argv)` with `--lib` (default `/opt/local-ai/bin/llama.cpp/b11146/libcudart.so.13`) and
`--reserve` (default 24); `measure(cuda, *, read_available, reserve_gib, step_gib=1) -> Result(
allocated_gib, stopped: "reserve" | "cuda_error", detail)` — it prints `cudaMemGetInfo`'s free and
total first, with nothing allocated; then, before each step, stops when `MemAvailable` less a step
would fall under the reserve; else `cudaMalloc`s one GiB, `cudaMemset`s it (touching every page),
and prints the running total and `MemAvailable`; it stops at the first CUDA error; every pointer is
freed on every way out, Ctrl-C included. It never allocates "until failure".

**Tests** (`spark/tests/test_cuda_ceiling.py`, the script loaded from its path, a stand-in `cuda`):

- `test_it_stops_when_memavailable_would_pass_the_reserve` — `MemAvailable` from 117, falling 1 GiB
  a step, reserve 24 → 93 GiB allocated, stopped `reserve`.
- `test_it_touches_every_step` — `cudaMemset` called once per allocation, with its pointer and size.
- `test_it_frees_everything_on_any_way_out` — an exception at step 5, and `KeyboardInterrupt` at
  step 5 → `cudaFree` for each pointer taken.
- `test_it_stops_at_the_first_cuda_error` — `cudaMalloc` failing at step 10 → stopped
  `cuda_error`, the 9 freed.
- `test_it_reads_memgetinfo_before_allocating` — the first printed line holds the stand-in's free
  and total, before any `cudaMalloc`.

**Steps:**

- [ ] **Step 1:** the failing tests; run them: they fail.
- [ ] **Step 2:** the script; the tests pass; `make test lint`.
- [ ] **Step 3: Measure it.** **[Dan, on the Spark]** `spark make-room --all` (one question), so the
  box is clear and held for you; the brake and earlyoom running. **On the Spark**, in tmux,
  `/opt/local-ai/bin/llama.cpp/b11146/llama-server --list-devices`, then `uv run
  stack/measure/cuda_ceiling.py`, watched. Then `spark make-room --done`: the residents reload.
- [ ] **Step 4:** the registry follows: `allocatable_gib` the measured figure (*at least* it, when
  the script stopped at the reserve) and `allocatable_measured: true`; render still passes.
- [ ] **Step 5: Commit.** **On the Spark:**

```bash
git add stack/measure/cuda_ceiling.py spark/tests/test_cuda_ceiling.py stack/models.yaml website/design/plan.md \
  CLAUDE.md README.md cosmicbboy-local-ai.md changelog.md
git commit -m "feat(stack): 🤖 measure the CUDA-allocatable ceiling, and the registry follows it" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 39 [Spark]: the coder swap — Qwen3.8-27B in the registry, apply, then the pull

**Files:**

- Modify: `stack/models.yaml`, `spark/tests/test_stack_registry.py`, `changelog.md`, `README.md`,
  `website/architecture.qmd` (the coder's label), `website/design/plan.md` (*Models*: the swap
  done)

**Interfaces:** the coder's entry, `qwen3.8-27b`: `capability: chat`, `roles: [coder]`, `label: the
coder`, `resident: false`, `engine: llama.cpp`, `source: {repo: unsloth/Qwen3.8-27B-GGUF, revision:
"4ca720788d1e01f1bff70c033e0d0028fd02e502", file: Qwen3.8-27B-UD-Q4_K_XL.gguf}`, `ctx: 262144`,
`parallel: 1`, `cache_ram_mib: 2048`, `footprint_gib: 41`, `footprint_measured: false`, `args:
[--load-mode, none, --spec-type, draft-mtp, --spec-draft-n-max, "3", --ctx-checkpoints, "8"]`
(today's draft length carried over; Task 41 reads its acceptance), with comments written fresh for
it: the footprint's parts (weights ~16.4, KV ~17.0 at 68 KiB a token, ~7.5 for the rest, 8
checkpoints of ~150 MiB) and that a soak replaces them. `qwen3.6-35b-a3b` leaves the registry; its
files stay on disk.

**Tests** (`spark/tests/test_stack_registry.py`):

- `NATIVE_CTX` gains the coder's file at 262144 (read from its header on 2026-10-07 by the council),
  so `test_every_llama_cpp_model_runs_at_its_full_context` covers it.
- `test_the_coder_is_qwen3_8_27b_by_route_a` — the `coder` role's model has that repo, revision
  and file, `--spec-type draft-mtp`, `--ctx-checkpoints 8`, `cache_ram_mib` 2048.
- `test_the_old_coder_left_the_registry` — no `qwen3.6-35b-a3b`.

**Steps:**

- [ ] **Step 1:** the failing tests; run them: they fail.
- [ ] **Step 2:** the entry; the tests pass; `make test lint`; render passes, 84 of the ceiling and
  50 beside the residents. Commit (Step 5's block, without the record's files).
- [ ] **Step 3: Apply it, S17's way.** `make apply`: the diff shows the coder's change; the quiet
  wait; llama-swap restarts under the loaded residents, which stops every engine, the old coder
  included; the gate reloads the residents; `apply_restarted` on the phone. **[Dan, on the Mac]**,
  in pi, the old name reads `model_not_found`'s words, listing the new one, and the coder by its
  role reads `not_downloaded`'s.
- [ ] **Step 4 [Dan, on the Spark]:** `make pull` — 17.6 GB as `spark-pull`; follow it with
  `make logs s=pull` in another pane. Expected: `pull: qwen3.8-27b: Qwen3.8-27B-UD-Q4_K_XL.gguf → …`
  for the coder, and the residents' files already there.
- [ ] **Step 5: The record and commit.** **On the Spark:**

```bash
git add stack/models.yaml spark/tests/test_stack_registry.py changelog.md README.md website/architecture.qmd website/design/plan.md
git commit -m "feat(stack): 🤖 Qwen3.8-27B becomes the registry's coder, by route A" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 40 [Spark]: the page-cache drill

**Files:**

- Create: `stack/measure/sample_memory.py`, `spark/tests/test_sample_memory.py`
- Modify: `website/design/plan.md` (rule 1's cache drop settled; *Page cache and the launch check*
  resolved; a Revisions line), `cosmicbboy-local-ai.md` (E.1, only if it is seen here),
  `changelog.md`

**Interfaces:** `sample_memory.py`, PEP 723 (no dependencies): `main(argv)` with `--out` (refused
inside the repo), `--hz` (10) and `--seconds`; `row(proc: Path, nvidia: Callable[[], str]) -> dict` —
`t`, `MemAvailable`, `MemFree`, `Cached` (GiB), and per engine (a `spark` process named
`llama-server` or `whisper-server`; keyed by its `--alias`, else `<comm>:<port>`) its `RssAnon` and
`nvidia-smi`'s figure, blank for `[N/A]`; one CSV row per sample.

**Tests** (`spark/tests/test_sample_memory.py`):

- `test_a_row_holds_each_field` — a stand-in `/proc` with `meminfo` and two engines, one with
  `--alias qwen3.8-27b` → a row with the three memory fields and each engine's `RssAnon` and
  `nvidia-smi` figure under its key.
- `test_it_samples_ten_times_a_second` — an injected clock: ten rows per simulated second.
- `test_it_refuses_to_write_inside_the_repo` — `--out` under the repo's root → exit 2.
- `test_nvidia_na_is_blank` — `[N/A]` → an empty cell.

**Steps:**

- [ ] **Step 1:** the failing tests; run them: they fail. The script; the tests pass; `make test
  lint`.
- [ ] **Step 2: Fill the page cache**, the coder not loaded. **On the Spark:**

```bash
f=$(mktemp -p ~ pagecache-fill.XXXXXX)
dd if=/dev/zero of="$f" bs=1M count=40960 status=none
cat "$f" > /dev/null
grep -E '^(MemFree|MemAvailable|Cached):' /proc/meminfo
```

  Expected: `Cached` up by about 40 GiB, `MemFree` down by as much.
- [ ] **Step 3:** in one tmux pane, `uv run stack/measure/sample_memory.py --out
  ~/samples/pagecache.csv --seconds 300`; in another, `spark load coder`. Then `rm -f "$f"`.
- [ ] **Step 4:** read the samples against E.1's slow reclaim (`MemFree` pinned, the load stalled):
  how long the load took, and how `MemFree` and `Cached` moved. **[Dan]** decides rule 1's drop:
  nothing; `--load-mode dio`; or a root oneshot that polkit lets `spark` start by its exact name.
  Either of the last two is a plan revision with a task of its own; the samples stay outside the
  repo, their figures in the record.
- [ ] **Step 5: Commit.** **On the Spark:**

```bash
git add stack/measure/sample_memory.py spark/tests/test_sample_memory.py website/design/plan.md cosmicbboy-local-ai.md changelog.md
git commit -m "docs(plan): 🤖 the page-cache drill settles rule 1's cache drop" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 41 [Spark]: the coder's first measurements, and route A's speed

**Files:** `stack/models.yaml` (the coder's comments; `cold_load_gib`), `website/design/plan.md`
(Phase 5's route A row, measured here; the load's deadline confirmed or revised; a Revisions
line), `cosmicbboy-local-ai.md` (only a claim this measures), `changelog.md`

- [ ] **Step 1, in the design's order** (*What 2a measures first*), each sampled with
  `stack/measure/sample_memory.py`: (1) b11146 loads it and drafts with MTP — the acceptance rate
  from `spark logs coder`; (2) its load's peak, 10×/s, and its cold fall in `MemAvailable` at full
  context, `MemFree` first; (4) time to first token at 32K, 128K and 250K tokens, against pi's
  timeouts (the Mac's default and `agent`'s 900,000), and decode at long context; (5) its load
  time, cold and warm, against the 180 s deadline; (6) a busy coder's stop time, for the brake's
  `GRACE_S`. Step (3), the soak, is Task 42's.
- [ ] **Step 2:** route A's decode, time to first token and prefill in pi, in Phase 5's order,
  recorded against Phase 5's table.
- [ ] **Step 3:** the registry records the coder's `cold_load_gib`; private readings (load times,
  readings in context) go to Dan in the chat, for the vault.
- [ ] **Step 4: Commit.** **On the Spark:**

```bash
git add stack/models.yaml website/design/plan.md cosmicbboy-local-ai.md changelog.md
git commit -m "docs(plan): 🤖 Qwen3.8-27B's first measurements, and route A's speed" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 42 [Spark]: the soak — every footprint, and what RssAnon accounts for

**Files:** `stack/models.yaml` (each footprint, `footprint_measured: true`, `cold_load_gib`;
`gate.owed_reads_rss` as the evidence says), `spark/tests/test_stack_registry.py` (Task 4's
`test_owed_reads_rss_stays_off_until_the_soak`, rewritten to the evidence), `website/design/plan.md`
(rule 9's attribution checked; *To verify* resolved; the reserve's revisit put to Dan; a Revisions
line), `changelog.md`, `README.md`

- [ ] **Step 1:** every model at its full context: the residents through the web UI, the embeddings
  and speech; the coder to about 250K tokens in pi, with edits, regenerations, tool calls and
  thinking (the design's step 3). Peak and steady state per model, from the sampler.
- [ ] **Step 2:** for each engine, its `RssAnon` growth against `MemAvailable`'s fall and
  `nvidia-smi`'s per-process figure. `owed_reads_rss` turns on only if `RssAnon` accounts for the
  growth `MemAvailable` shows.
- [ ] **Step 3:** the registry's footprints become the measured ones; render passes; **[Dan]**
  decides whether the reserve moves.
- [ ] **Step 4: Commit.** **On the Spark:**

```bash
git add stack/models.yaml spark/tests/test_stack_registry.py website/design/plan.md changelog.md README.md
git commit -m "feat(stack): 🤖 every footprint measured by the soak, and what owed reads" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

## ⇄ Push point — the measured registry, for the Mac

- [ ] *Before every push* with `phase-2a`; **Dan OKs** `git push`; CI is green.

***

### Task 43 [Spark]: the coder becomes the default for agent

**Files (the record):** `changelog.md`, `README.md` (§Current state: `agent`'s pi).

- [ ] **Step 1 [Dan, as `agent`]:** Task 31's command, `/opt/local-ai/app/.venv/bin/spark clients pi
  --write --registry /opt/local-ai/etc/models.yaml --http-idle-timeout-ms 900000`. Expected: pi
  lists `qwen3.8-27b` and not `qwen3.6-35b-a3b`; `~/.pi/agent/settings.json` holds
  `httpIdleTimeoutMs` 900000.
- [ ] **Step 2 [Dan, as `agent`]:** a real task with the coder, in tmux, completes.
- [ ] **Step 3: Commit.** **On the Spark:**

```bash
git add changelog.md README.md
git commit -m "docs(machine): 🤖 agent's pi works with Qwen3.8-27B" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 44 [Mac]: the Mac's pi lists the new coder, and completes a task with it

Dan runs this himself, from his clone; no Mac session is needed, and nothing in the repo changes.

- [ ] **Step 1:** **On the Mac**, from the clone:

```bash
git switch phase-2a && git pull && make clients
```

  Expected: `clients: wrote the 'spark' provider to …/.pi/agent/models.json`.
- [ ] **Step 2:** with `make tunnel` running, pi lists `qwen3.8-27b` and not `qwen3.6-35b-a3b`, and a
  real task completes. Dan tells the Spark session, which records it in README's Mac section with
  its next commit.

***

### Task 45 [Spark]: the brake's timings, the lag, earlyoom's order and swap

**Files:**

- Modify: `spark/src/spark/brake.py` (`GRACE_S`, `FLOOR_TOLERANCE_GIB`, `FALL_WINDOW_S`,
  `UNLOAD_LEAD_S`, each with its measurement in its comment), `spark/tests/test_brake.py` (the
  tests that use them), `cosmicbboy-local-ai.md` (`[verified]` only where measured),
  `website/design/plan.md` (*The minimal brake's reach*; *To verify*: swap; a Revisions line),
  `changelog.md`; and, as the readings say, `stack/host/earlyoom.default` or a swappiness setting
  in `stack/host/bootstrap.sh`, each with its test first

- [ ] **Step 1: Measure,** each with the sampler: a busy engine's stop time (an unload while the
  coder answers); how long after an engine leaves `/running` its memory shows in `MemAvailable`;
  `MemAvailable`'s noise while models generate; the falls of Task 41's loads, for the rate-of-fall
  constants; earlyoom's order at the real thresholds, by its dry run; whether memory swaps before
  `MemAvailable` reaches the brake. Every drill that lowers memory uses a stand-in hog that frees
  itself above earlyoom's line, with the brake running.
- [ ] **Step 2:** the constants from the measurements; the tests changed with them, failing first;
  `make test lint`. **[Dan, on the Spark]** `make bootstrap` if earlyoom's arguments or swappiness
  change; `make apply` restarts the brake.
- [ ] **Step 3: Commit.** **On the Spark:**

```bash
git add spark/src/spark/brake.py spark/tests/test_brake.py cosmicbboy-local-ai.md website/design/plan.md changelog.md
git commit -m "feat(spark): 🤖 the brake's timings and rate-of-fall watch, from measurements" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

  (With `stack/host/earlyoom.default`, `stack/host/bootstrap.sh` and their tests, if Step 2
  changed them.)

***

### Task 46 [Spark]: drill — S05, memory critically low

**Files:** `website/scenarios/s05-memory-critically-low.md` (`status: verified`, `verified:` the
date; `phase` stays 1), `changelog.md`, `website/design/plan.md` if anything differs from it.

- [ ] **Step 1:** the brake's thresholds raised for the drill (a registry edit, `make apply`), as
  Phase 1's drill did, and put back after. Each message checked word for word, on the phone, in pi
  and in `spark status`:
  - the fall past the warn line: `memory_warning`, once;
  - the brake fires: `brake_fired`, high, naming the loading engine first, then idle models, with a
    follow-up for each later unload;
  - a request meanwhile waits, then reads `held_by_brake`'s words; `spark status` shows *paused*;
  - memory back above the warn line for 5 minutes: the gate releases, `brake_released`, and the
    residents reload one at a time;
  - a second brake within the hour: no automatic release, `brake_needs_release`;
  - a hold left across a reboot (**[Dan]** reboots while it stands): it waits for Dan, with
    `brake_needs_release` at boot;
  - `agent`'s request for the model that was loading reads `footprint_suspect`'s words, and its
    notification names `spark load coder`; Dan's request loads it;
  - with the gate stopped (**[Dan]** `systemctl stop local-ai-gate.service`), the brake fires and
    sends its own `brake_fired`; the gate, started again, doesn't send it twice.
- [ ] **Step 2: Commit.** **On the Spark:**

```bash
git add website/scenarios/s05-memory-critically-low.md changelog.md website/design/plan.md
git commit -m "docs(website): 🤖 S05 verified: the brake fires, releases within bounds, and alerts without the gate" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 47 [Spark]: drill — S14, and the crash-loop check as agent

**Files:** `website/scenarios/s14-gate-down.md` (`status: verified`, dated),
`website/design/plan.md` (*To verify*: `OnFailure=` under `Restart=`, and 9100 held through a crash
loop; a Revisions line), `changelog.md`, `README.md`

- [ ] **Step 1 [Dan, on the Spark]: each service killed in turn**,
  `sudo systemctl kill --signal=SIGKILL local-ai-<name>.service` for the front, the gate, the brake
  and llama-swap: each alert on the phone, high, once, in Task 6's words; `back_up` a minute after
  each comes back. `sudo kill -STOP` on the front's process: its watchdog fires within 30 s, its
  alert names it, and it restarts. A clean `sudo systemctl kill --signal=SIGTERM
  local-ai-llama-swap.service`: no alert, and it comes back. With the gate stopped, a loaded model
  answers and a load reads `gate_down`'s words; with llama-swap stopped past a key's wait,
  `llama_swap_down`'s. Then `sudo -k`.
- [ ] **Step 2: The crash-loop check.** **[Dan, on the Spark]** (sudo asks once), the front killed
  again and again, then made to fail at start:

```bash
for i in 1 2 3 4 5 6 7 8 9 10; do sudo systemctl kill --signal=SIGKILL local-ai-front.service; sleep 3; done
sudo mkdir -p /run/systemd/system/local-ai-front.service.d
printf '[Service]\nExecStart=\nExecStart=/bin/false\n' | sudo tee /run/systemd/system/local-ai-front.service.d/crash-drill.conf >/dev/null
sudo systemctl daemon-reload
sudo systemctl restart local-ai-front.service
```

  Meanwhile, **on the Spark, as `agent`**, Task 34's bind block for 9100, and this watch of who
  answers on it:

```bash
for i in $(seq 30); do curl -s -m 2 -o /dev/null -w '%{http_code} ' http://127.0.0.1:9100/health; sleep 1; done; echo
```

  Expected: `bound 9100 0 times in 30 s`; the watch reads `000` while the front fails at start, and
  `200` only from the front; the phone gets one `front_down` per 5 minutes, not one per crash. Then,
  **[Dan, on the Spark]**, the front put back:

```bash
sudo rm /run/systemd/system/local-ai-front.service.d/crash-drill.conf
sudo systemctl daemon-reload
sudo systemctl restart local-ai-front.service
sudo -k
```

- [ ] **Step 3: Commit.** **On the Spark:**

```bash
git add website/scenarios/s14-gate-down.md website/design/plan.md changelog.md README.md
git commit -m "docs(website): 🤖 S14 verified: each failure alerts once, and 9100 holds through a crash loop" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 48 [Spark]: drills — S01's gate part, S02, S03 and S17

**Files:** `website/scenarios/s01-morning-start.md` (stays `planned`, with a dated note recording its
gate part), `s02-big-job.md`, `s03-doesnt-fit-interactive.md`, `s17-changing-models.md` (each
`status: verified`, dated), `changelog.md`, `website/design/plan.md` if anything differs from it.

- [ ] **Step 1: S01.** The coder idle-unloads after 60 minutes (or with the idle time lowered for
  the drill and put back): `unloaded`. A request from pi loads it: `load_started` and `loaded`
  arrive silently, and pi shows only a slower first reply. `spark pin coder 8h` answers in its
  words; `pin_ended` when it runs out (a short pin for the drill).
- [ ] **Step 2: S02.** `spark make-room 70G`: the list, one question, the room held. `agent`'s
  request for the coder waits its 10 minutes and reads `no_fit`'s `agent` text, naming the hold; a
  request of Dan's loads into the hold and shrinks it; `spark make-room --done` reloads Gemma, or
  sends `resident_waiting`.
- [ ] **Step 3: S03, and the runtime check of *Before Task 1*.** A stand-in job of Dan's holding
  32 GiB: pi on the Mac shows only its usual thinking for 30 s, nothing added to the stream, then
  the refusal, `409: {"message":"The coder didn't load: …","code":"no_fit"}`, at 30 s and without
  retrying: `make logs s=front` shows one request from `dan-mac`, ending 409. The web UI, asked
  the same, shows the sentence alone, with no retry. The phone gets `refused` once, and a burst
  collapses with its count. Then, with the gate stopped for a moment, a request for the coder
  reads `gate_down`'s words after pi's own retries, each a line in the front's log. The make-room
  walk then loads the coder. What pi and the web UI did is recorded against *Before Task 1* and
  Review Focus 6.
- [ ] **Step 4: S17.** A registry edit with a request in flight: the diff; the quiet wait's words;
  Ctrl-C changes nothing; the 15-minute deadline (shortened for the drill and put back) offers
  "drain now"; `make apply-now` asks, naming the requests; a request during the restart reads
  `restarting`'s words.
- [ ] **Step 5: Commit.** **On the Spark:**

```bash
git add website/scenarios/s01-morning-start.md website/scenarios/s02-big-job.md \
  website/scenarios/s03-doesnt-fit-interactive.md website/scenarios/s17-changing-models.md changelog.md website/design/plan.md
git commit -m "docs(website): 🤖 S02, S03 and S17 verified, and S01's gate part" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 49 [Spark]: close Phase 2a

**Files:** the scenario pages' statuses; `website/architecture.qmd` (every 2a part solid);
`README.md`; `changelog.md`; `CLAUDE.md` and README §Conventions (any rule 2a changed);
`website/design/plan.md` (the forward look, Revisions, Phase 2a's status line);
`website/design/phase-2a-qa.md` (any decision made since it was written);
`website/design/phase-2a-retro.md` (new); this plan's Progress note

- [ ] **Step 1:** **on the Spark**, `uv run --frozen --project spark spark docs check-scenarios`,
  `spark docs stack --check` and `spark docs notifications --check`; the docs true against the box.
- [ ] **Step 2 [Dan, on the Spark]:** `llama-swap.env` removed, the rollback no longer needed, and
  `hf/tmp`, which nothing names any more; `make doctor` passes, and `spark doctor --full`.
- [ ] **Step 3 [Dan]:** the private findings (load times, readings in context, anything about the
  tailnet) listed in the chat for the vault, never in the repo.
- [ ] **Step 4: Council review,** four reviewers against plan.md's Phase 2a and this plan: goal-fit
  and scenarios; reliability; security and simplicity; toolstack. The fixes, one commit each.
- [ ] **Step 5: Forward look:** what 2a teaches 2b, 2c and later — the reserve, the journal
  question, pi's own retries, the engines' own user, the network namespace, LiteLLM against the
  front, the watchdog's Compose file — into plan.md, with a Revisions line; and into
  `phase-2a-qa.md`, any decision made since it was written.
- [ ] **Step 6:** the retrospective.
- [ ] **Step 7: Commit.** **On the Spark:**

```bash
git add website/scenarios website/architecture.qmd README.md changelog.md CLAUDE.md website/design/plan.md \
  website/design/phase-2a-qa.md website/design/phase-2a-retro.md website/design/phase-2a.md
git commit -m "docs(plan): 🤖 close Phase 2a: scenario statuses, the forward look, the retrospective" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

## ⇄ Switch point — Spark → Mac

- [ ] The Spark session runs *Before every push* with `phase-2a`; **Dan OKs** the push; **on the
  Mac**, `git switch phase-2a && git pull`.

***

### Task 50 [Mac]: the Mac check, the site and the merge

- [ ] **Step 1:** `make test lint docs`, on bash 3.2 and GNU make 3.81: the Makefile's new targets,
  the leak hooks, and the socket tests on the Mac's short paths; the site renders with no warnings,
  the notifications page among the reference pages. A Mac difference is fixed here, with its test,
  and in this plan.
- [ ] **Step 2: Merge,** with Dan's OK. **On the Mac:**

```bash
git switch main && git pull
git merge --no-ff phase-2a -m "chore(repo): 🤖 merge phase 2a" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

  Then *Before every push* with `main`, and **Dan OKs** `git push origin main`. **On the Spark**,
  as you and then as `agent`, each clone moves to `main`.

***

## Phase 2a is done when

- [ ] S01's gate part, S02, S03, S05, S14 and S17 are verified: their pages read *verified* with the
  date, but S01, which stays `planned` with a dated note recording its gate part until 2b's menu
  bar, and S05, which stays `phase: 1`.
- [ ] Qwen3.8-27B is the registry's coder, and pi completes a task with it from the Mac and from
  tmux as `agent`.
- [ ] The soak has measured every footprint, the coder's included, and the registry holds the
  numbers.
- [ ] The CUDA-allocatable ceiling is measured.
- [ ] The brake's timings, `MemAvailable`'s lag, swap and earlyoom's order are recorded.
- [ ] Route A's decode, time to first token and prefill are recorded against Phase 5's table.
- [ ] `make test lint` is clean on the Spark and `make test lint docs` on the Mac, and CI is green;
  README §Current state, the changelog and the architecture page are true; the council review is
  done and the forward look applied; `phase-2a` is merged to `main` with Dan's OK.

***

## How the command blocks were checked

Every command block above was run on 2026-10-07, before it went in:

- **Run for real, read-only, on the Spark:** Task 1's digest check (Docker Hub answered the pinned
  digest); Task 33's check block (on the box as it stands, before bootstrap: the users and folders
  missing, as expected); Task 34's ports block (it listed 9100 and 5800–5802 that day); Task 34's
  bind block, as you, for 9100 and 900 (`bound … 0 times in 30 s` for each); Task 35's `lsmod`;
  Task 37's two lines, as you (your score is 0, and the second line succeeded: why the check tells
  a set floor from none); Task 47's watch loop, against 9100 and against a port nobody holds
  (`200` and `000`).
- **In a scratch clone of this repo on the Spark:** Task 34's rollback render check (it rendered
  `main`, `-listen 127.0.0.1:9100` and `llama-swap.env`) and its `git worktree` steps; Task 44's
  Mac block, with a temporary `HOME` (it wrote the provider); every task's commit form; Task 50's
  merge.
- **With stand-ins that log their calls** for `sudo`, `systemctl`, `docker` and `make`, since the
  real ones would change the box: Task 34's rollback and cutover blocks; Task 36's container check;
  Task 47's crash-loop blocks.
- **Smaller, in the scratchpad:** Task 40's fill, with a 64 MiB file in place of 40 GiB.
- **Not blocks:** the commands of the CLI this plan builds (`spark make-room`, `spark load`, `spark
  logs`, `spark clients … --http-idle-timeout-ms`) appear inline, since they can't run before their
  tasks; their tests pin them. So do `make` targets that need sudo (`make bootstrap`, `make
  install-units`), which exist and are run as Phase 1 ran them. Each runbook's blocks are run in the
  task that writes them, on stand-ins, and its report says how.
- **The pytest form** in *How a code task runs* was run on `spark/tests/test_registry.py` (108
  passed).
- **ntfy's Compose file:** `docker compose config` with stand-in values read the file's shape this
  task's test pins (Docker's CLI needs no daemon for it): with the values it printed the service,
  a bcrypt hash's `$` kept; without them it stopped, naming `NTFY_BASE_URL`.

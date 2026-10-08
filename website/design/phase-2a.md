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

> **Progress (2026-10-07).** The design is approved (Dan, 2026-10-07), and this plan is written,
> reviewed, and revised after a forward-and-back council, whose fourteen decisions Dan accepted the
> same day: the *Global Constraints* and the tasks carry them. ~~No task has started. **Next: Task
> 1.**~~ **Done (2026-10-07 and 08):** Tasks 1 and 3–11, each reviewed. **Task 2 [Dan]** runs side by
> side with the Spark tasks: no task before Task 27 reads what it makes, and it has to be done
> before Task 27 starts. **Next: Task 12.** Until the cutover (Task 38), nothing deploys from `phase-2a` (*Deploys during 2a*, below);
> an urgent fix goes from `main`.

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
model's label, ~~the client keys and their groups~~ the key groups, the notification list and the
gate's settings (the keys themselves are in the private `/etc/local-ai/keys.yaml`, by Dan's decision
after the forward-and-back council; *corrected 2026-10-07, at Task 4*);
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
- **Deploys during 2a** (Dan's decision, 2026-10-07, after the forward-and-back council): until
  the cutover (Task 38), nothing runs `make apply`, `make apply-now` or `make install-units` from
  the `phase-2a` clone. From Task 10 its brake would restart onto llama-swap's new port, where
  nothing listens yet, and from Task 11 its `spark launch` would start no model without a ticket.
  Task 36's `make bootstrap` is the one planned exception, and leaves Phase 1's stack serving. An
  urgent fix is committed on `main`, deployed from a `main` worktree (`git worktree add`, as Task
  38's rollback does), pushed with Dan's OK and merged into `phase-2a`; Task 37's `cap_drop`, which
  comes before the cutover, goes the same way. Until the cutover, read the box with the deployed
  app (*How a code task runs*).
- **Dependabot during 2a** (Dan's decision, the same day): its `spark/` bumps, `huggingface-hub`
  2.0.0 among them, wait unmerged until 2a merges, since Task 3 pins the lock's package list. A
  security fix that can't wait goes to `main` as an urgent fix.
- **Values the box decides** (Dan's decision, the same day): a value a task sets from a
  measurement or a reading — Gemma's `--slot-prompt-similarity` (Task 24), the engines' OOM scores
  (Task 28), the brake's constants (Task 47) — is the controller's ruling, and Dan is told. Where a
  step says to stop and ask, Dan decides.
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
  or `--fd`." *(Corrected 2026-10-07, after the forward-and-back council:* `Server.serve(sockets=…)`,
  in the one event loop of Task 9's `run_servers`, since `run` starts a loop of its own, and
  uvicorn's `Server` takes SIGINT and SIGTERM for itself, which Task 9's subclass hands back.)
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
  and one lock, deployed to `/opt/local-ai/app`." Nor does `cli.build_parser()`, which every `spark`
  command runs: `spark front` and `spark gate` import their modules inside their handlers, and Task
  3's test runs that path. uvicorn is pinned, "and a test, run again at each bump, checks that the
  caller's uid arrives".
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
  front and the gate join the four. The socket units and the failure notifier aren't on it." Seven
  since this plan's re-check (the controller's ruling, 2026-10-07; plan.md corrected): the S05
  drill's oneshot, `local-ai-brake-drill.service`, which Dan starts.
- **The keys:** client keys reach the front only as digests "through `LoadCredential=`, and
  `llama-swap.env` drops the keys"; internal keys, "one per caller of llama-swap, the front, the
  gate and the brake: each reaches its caller through `LoadCredential=` … and llama-swap reads all
  three from its `EnvironmentFile=` … Their names carry the `LLAMASWAP_KEY_` prefix";
  "`LLAMASWAP_KEY_SPARK` retires"; "**Dan's commands need no key**", but `make doctor`'s end-to-end
  checks through the front "still use Dan's client key, `SPARK_API_KEY` on the Spark".
- **The key list and the key groups** (Dan's decision, 2026-10-07, after the forward-and-back
  council): each key's name, label, group and Unix account live in a private file,
  `/etc/local-ai/keys.yaml`, never in the repo; the front and the gate get it through
  `LoadCredential=`, and `stack/keys.example.yaml` shows its shape with placeholder names. The
  registry keeps only the key groups. Each group carries, apart, the five things the design's one
  `dan` flag decided: `queue` (lower goes first), `uses_hold` (may load into make-room's hold),
  `reloads_marked` (may load the model the brake marked), `words` (`dan` or `agent`: whose wording
  its refusals use) and `names_processes` (whether its refusals and status name Dan's processes).
  Today's two groups, `dan` and `agent`, give 2a's behaviour exactly.
- **The secret files** "become `0600 root:root`, in a folder only root reads". Phase 1's
  `llama-swap.env`, with the client keys and `LLAMASWAP_KEY_SPARK`, stays `0600 root:root` for the
  rollback until the close, Task 51 (the controller's ruling, 2026-10-07).
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
- **The front's model list** (Dan's decision, 2026-10-07, after the forward-and-back council): the
  front reads the deployed registry's names and roles at start, as its first list, and from then on
  takes the list from the gate's `hello` and `state` events, keeping the last one while the gate is
  down. A change to the registry's names or roles then reaches it without a restart.
- **The residents** reload one at a time, through admission: at boot, after a hold ends, after
  apply's restart, and (Dan's decision, 2026-10-07, after the forward-and-back council) after an
  unplanned llama-swap restart, or when a resident leaves `/running` without the gate or the brake
  unloading it (an earlyoom kill, a crash).
- **Draining:** "A drain the gate doesn't finish within ~~30 s~~ 90 s goes back to serving, and so does
  every drain if the front's call to the gate drops"; "the ~~30 s~~ 90 s run from the front's
  'drained'". *(Corrected 2026-10-08, at Task 12's re-review, the controller's ruling, plan.md with
  it: v257 never takes back an unload it has taken, and a stopping model never returns to ready. So
  `DRAIN_GRACE_S`, 90 s, bounds only a drain whose unload was never sent. Once it is sent, the drain
  ends only when `/running` shows the model gone, and the gate never sends it back to serving. A
  drop of the front's call ends, on the gate's side, only a drain whose unload wasn't sent; the
  front still undrains on its own (Task 22). Task 16 has the rule.)*
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
  "only when its own modules change", ~~or the registry's model names and roles,~~ or the key
  digests (or, since Dan's decision of 2026-10-07, the key list; the registry's names and roles
  reach it through the gate).
- **Apply's hold** (the controller's rulings, 2026-10-07, after this plan's final check): from
  `drain-all` to its end, `make apply` renews the hold every **15 s** (`APPLY_RENEW_S`), and the
  gate ends a hold not renewed for **60 s** (`APPLY_LAPSE_S`), before `begin` as after. Every end
  — `apply/end`, `undrain-all`, llama-swap answering again after its restart, a lapse — does the
  same work, once: the hold released, then the residents' reload queued ahead of anything else.
  `make apply` ends on SIGHUP and SIGTERM as on Ctrl-C, with the same cleanup. The front drops the
  hold once the gate has been gone longer than `APPLY_LAPSE_S`, so a gate that doesn't come back
  never takes the API down with it (rule 8).
- **The coder:** "`unsloth/Qwen3.8-27B-GGUF`, at revision
  `4ca720788d1e01f1bff70c033e0d0028fd02e502` … the file `Qwen3.8-27B-UD-Q4_K_XL.gguf` (17.6 GB)";
  "`--spec-type draft-mtp`"; a native context of 262,144 with an f16 KV cache; "estimated at
  ~41 GiB"; "sets `--ctx-checkpoints 8` and the 2 GiB prompt cache explicitly". Its swap:
  "`make apply` … then `make pull` … then `make clients`."
- **Refusals** (the controller's rulings, 2026-10-07, from *Before Task 1*, correcting the design's
  "a `503` … and `x-should-retry: false`"; and Dan's decision the same day, after the
  forward-and-back council): every refusal is a **`409`**, which pi shows at once and never
  retries — `no_fit` · `loading` · `held_by_brake` · `footprint_suspect` · `load_failed` ·
  `not_downloaded` · `restarting` · `llama_swap_down` · `draining`. pi retries any "503" three
  times, each retry a new request with its own key's wait, so a `503` would reach Dan after about
  2¼ minutes and `agent` after about 40. Only **`gate_down`**, which the front answers at once while
  the gate itself is down, stays a **`503`**, which pi retries: the gate restarts within seconds.
  Every `409` and `503` carries `x-should-retry: false`, since OpenAI's SDKs retry both by
  default. The front's own: `model_not_found` (404); `too_many_requests` (429);
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
  timeout, 5 s, but for the routes that wait: an admission call is held for its key's wait,
  `/v1/load` and `/v1/pin` for the load call's 200 s, and `/v1/unload` and `/v1/make-room` for as
  long as their drains take · `WatchdogSec=30` · the
  gate's load call, 200 s (180 + 20) · the gate's unload call, 60 s (`UNLOAD_CALL_TIMEOUT_S`; the
  controller's ruling at Task 12's review, 2026-10-08: v257 answers once the engine has stopped, a
  stuck one after its 10 s `unloadTimeout` and its kill, and its one run loop queues the stops, so
  room for four) · the notifier, at most one alert per unit per 5 minutes ·
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
  word … *held* is make-room's" (and the status `held` row also carries pins, sessions and apply's
  hold, each by name). "Sizes in whole GiB, times in his local time on a 24-hour clock,
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
   `spark status` shows each model's oldest request, so a leaked count would show. *(Tasks 15, 18,
   19, 21, 22.)*
2. **A long, silent request under `make apply`, make-room or an idle unload** — a 250K-token prefill
   that sends nothing for minutes. Expected: no read timeout cuts it; apply waits for 60 s of quiet
   for up to 15 minutes, then offers "drain now", which holds new requests and lets this one finish;
   Ctrl-C leaves nothing changed; a change elsewhere in the app never restarts the front; make-room
   and idle unloads wait for it, however long, and never cut it off; only the brake may. *(Tasks 16,
   17, 21, 31, 32.)*
3. **A cold boot under the new sandbox** — `NoNewPrivileges=` and `CapabilityBoundingSet=` on
   llama-swap's unit strip `nvidia-modprobe`'s setuid powers, so the first engine may fail to load
   `nvidia-uvm`; the engines must bind 800 and up with the capability they inherit; the gate must
   preload the residents one at a time; a gate that's down at boot means no model at all. Expected:
   every resident loads after a cold boot, or bootstrap loads `nvidia-uvm` at boot. *(Tasks 24, 27,
   39.)*
4. **Memory the gate reads wrong** — growth that doesn't show in `RssAnon` (GPU allocations), the
   embeddings engine's file-backed RSS (page cache), an outside allocation during a load that
   inflates its fall, the coder's 41 GiB loads against a full page cache, a GPU job of `agent`'s.
   Expected: *owed* errs safe (the gate subtracts RSS growth only once the soak has shown it counts,
   a registry flag), a load's fall is capped and flagged when other memory moved, the page-cache
   drill settles rule 1 while Qwen3.6's way back is still written down (Task 42), and the brake and
   earlyoom stay the backstops. *(Tasks 8, 15, 28, 40, 43, 45.)*
5. **The first deploy fails, or the front crash-loops at start** — PID 1 holds 9100 with nothing
   behind it, so every client hangs in the backlog. Expected: the rollback to Phase 1's layout
   (llama-swap on 9100 with the client keys) is written and its render checked before anything
   moves; the crash-loop check as `agent` shows 9100 never answers as anyone else, and binding it
   always fails; the notifier pages once per 5 minutes, not every 2 s. *(Tasks 25, 26, 38, 49.)*
6. **A refusal in pi** — pi 0.85.1 (and 0.87.1) retries a failed turn by itself, up to three times,
   2, 4 and 8 s apart, whenever the error's text matches its list, which holds "503" and "429" but
   not "409"; it reads neither `x-should-retry` nor `Retry-After` at that level (*Before Task 1*).
   Expected: every refusal but `gate_down` is a `409`, which pi shows at once, its sentence after
   the status, and doesn't retry, so Dan's 30 s refusal arrives after 30 s, not about 2¼ minutes;
   `gate_down`'s `503`, answered at once while the gate is down, is retried, as is sensible there;
   the S03 drill confirms it on the box.
   *(Tasks 6, 22, 50.)*

***

## File structure

| Path | Responsibility |
|---|---|
| `stack/synology/ntfy/compose.yaml` (new) | ntfy on the Synology as a Compose file, pinned by its index digest; every private value a named variable |
| `stack/synology/ntfy/variables.example` (new) | The six variables by name, with placeholders, never values |
| `stack/models.yaml` | The registry: gains each model's `label`, the `key_groups`, `notifications`, `gate` settings and the budget's measured values; whisper's tmp-dir moves; the coder becomes Qwen3.8-27B (Task 42) |
| `stack/keys.example.yaml` (new) | The private key list's shape, with placeholder names; the real one is `/etc/local-ai/keys.yaml`, never in the repo |
| `stack/versions.yaml` | Gains ntfy's row (`where: [synology]`) |
| `stack/llama-swap/config-schema.v257.json` (new) | llama-swap v257's own config schema, vendored from its tag, for the rendered-config check |
| `stack/templates/local-ai-front.socket` (new) | Holds 127.0.0.1:9100 from boot; never gives up |
| `stack/templates/local-ai-front.service` (new) | The front as `spark-front`: sandbox, limits, watchdog, credentials, `OnFailure=` |
| `stack/templates/local-ai-gate-status.socket`, `local-ai-gate-control.socket` (new) | The gate's two Unix sockets, 0660, groups `spark-users` and `spark-admin` |
| `stack/templates/local-ai-gate.service` (new) | The gate as `spark`: sandbox, watchdog, credentials, the values file, `OnFailure=` |
| `stack/templates/local-ai-notify@.service` (new) | The failure notifier: a dynamic user running Ubuntu's curl, with the registry's priorities |
| `stack/templates/local-ai-llama-swap.service` | 127.0.0.1:900, `CAP_NET_BIND_SERVICE`, internal keys only, `Restart=always`, sandboxed |
| `stack/templates/local-ai-brake.service` | Its key and ntfy token by `LoadCredential=`, the values file, never-give-up limits, `OnFailure=` |
| `stack/templates/local-ai-brake-drill.service` (new) | The S05 drill's oneshot: the brake's unit, but `spark brake --once` on a drill copy of the registry that Dan writes |
| `stack/templates/local-ai-pull.service` | Runs as `spark-pull`, `UMask=0027`, its cache in a folder of its own |
| `stack/templates/compose.yaml`, `searxng-settings.yml` | `cap_drop: [ALL]`, only what each needs added back; SearXNG's request timeout |
| `stack/host/local-ai-notify` (new) | The notifier's script: unit, result and time only; once per unit per 5 minutes; token by header file |
| `stack/host/local-ai-agent-oom`, `stack/host/agent-oom.conf` (new) | Root sets `agent`'s `oom_score_adj` at login, for sshd and its user manager |
| `stack/host/bootstrap.sh` | 2a's users, root-only secrets, the new folders, the cache moved to `spark-pull`, the notifier's script, `agent`'s OOM hook, install-units' new units |
| `stack/host/50-local-ai.rules` | The polkit rule: seven services (the six, and the S05 drill's oneshot), no sockets, no notifier |
| `stack/measure/cuda_ceiling.py` (new) | PEP 723, standard library: allocates and touches 0.25 GiB steps at the hog's pace, each confirmed in `MemAvailable`, and stops at `MemAvailable` less the reserve |
| `stack/measure/hog.py` (new) | PEP 723, standard library: the drills' one memory hog, sized from `MemAvailable` at the time (`--leave`), paced under the brake's rate watch, each step confirmed, never under its floor |
| `stack/measure/sample_memory.py` (new) | PEP 723: `MemAvailable`, `MemFree`, `Cached`, each engine's `RssAnon` and `nvidia-smi`'s figure, 10×/s, to a CSV outside the repo |
| `spark/pyproject.toml`, `spark/uv.lock` | uvicorn (exact), Starlette, httpx explicit; anyio in the dev group |
| `spark/src/spark/paths.py` | The private key list, the sockets, the gate's and launch's folders, whisper's tmp-dir, the values file, llama-swap's new URL |
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
| `spark/src/spark/tickets.py` (new) | Admission tickets: issued by the gate, used up by `spark launch`, kept under `launch/started/` while the load runs, for the brake |
| `spark/src/spark/launch.py` | Starts a model only with a ticket; keeps the hold check and a zero-wait fit check; `not_downloaded`; refusal records per model |
| `spark/src/spark/admission.py` | Phase 1's static fit, kept as launch's zero-wait backstop |
| `spark/src/spark/llamaswap.py` | `TESTED_AGAINST`; its default URL 127.0.0.1:900 |
| `spark/src/spark/llamaswap_async.py` (new) | The gate's httpx client for llama-swap: running, load, unload, an engine's last lines |
| `spark/src/spark/gate/` (new) | `state.py` (kept across restarts) · `notify.py` (ntfy, one per event) · `units.py` (the four units' restarts) · `admission.py` (the queue, one load at a time, tickets, refusals) · `policy.py` (drain, idle, pins, sessions) · `room.py` (make-room, release, load and unload, preload, the brake's release) · `core.py` (the loops, the activity record, apply's hold, the bypass check) · `app.py` (the two sockets' routes, and who may call each) · `main.py` (`spark gate`) |
| `spark/src/spark/front/` (new) | `__init__.py` (`FRONT_MODULES`, importing nothing) · `parse.py` (routes, keys, bodies, caps, the model list) · `app.py` (the ASGI app) · `upstream.py` (forwarding, counting) · `gatelink.py` (admission, drain, gate down, the gate's model list) · `main.py` (`spark front`) |
| `spark/src/spark/hold.py` | The hold gains the boot id, the episode and the model that was loading |
| `spark/src/spark/brakeevents.py` (new) | The brake's steps for the gate, keyed by boot id and sequence number, so a reset file or a new boot never hides an event |
| `spark/src/spark/brake.py` | Idle-first order from the gate's record, the rate-of-fall watch less the fall of admitted loads, its steps recorded for the gate, its own alert while the gate is down, its key by credential |
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

From Task 4 on, the clone's registry needs sections the deployed one lacks, and from Task 10 its
`paths.LLAMASWAP_URL` is 127.0.0.1:900, so the clone's own `make status` and `make doctor` misread
the box, which still runs Phase 1's layout. Until the cutover (Task 38), read the box with the
deployed app: `/opt/local-ai/app/.venv/bin/spark status` and `…/spark doctor`; and deploy nothing
from the clone (*Deploys during 2a*).

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
  not yet downloaded, which no retry changes; and Dan decided, after the forward-and-back council,
  that `restarting`, `llama_swap_down` and `draining` are `409`s too, since they also come after
  the key's wait. Only `gate_down` stays a `503`, which pi retries. The
  OpenAI SDK retries a 409 too, unless `x-should-retry: false` says not to (`openai` 6.40.0,
  `client.js`, `shouldRetry`), so every refusal keeps that header. The S03 drill
  (Task 50) confirms it on the box.
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
  docker.io/binwiederhier/ntfy:v2.28.0@sha256:6ef4b819f722fccdc036af611c4774cfdc2de821ab74fdd48bbf4c9d6f8973da`
  (the `versions.yaml` row's `image`, `version` and `pin`, in render's `_image` form),
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
     Where each value is kept: the helper's variables ~~and the vault~~, never the repo or a chat;
     the address, the port, the folder and the topic names also in the vault, and the hashes and
     tokens never there, only in the helper and (the tokens) the Spark's root-only header files,
     the vault recording where. *(Corrected 2026-10-07, after Task 1's review: `CLAUDE.md` keeps
     credentials in the vault by reference only, and its rule wins.)*
  4. *Deploy it — Portainer today*: a stack from the web editor (the file pasted in) or from a Git
     repository (this repo's URL, its branch, the compose path `stack/synology/ntfy/compose.yaml`;
     the branch is `main` once 2a merges, since `phase-2a` goes away), the six variables in the
     stack's environment, then deploy.
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
  9. *A test publish*: as root on the Spark, one message per header file, at `high` and at `low`,
     with curl's `--config -` on stdin, as the notifier does, so neither the address nor the topic
     is on a command line.
  10. *Record it*: the vault's entry note names the values file, the three tokens and where each
      lives, never a value.
- `website/how-to/updates.md`, a row in its table: ntfy, "its container image on the Synology,
  pinned by digest", moved "by hand on upgrade day, in its Compose helper", as `ntfy.md` §6 says.

**Tests** (`spark/tests/test_ntfy_records.py`):

- `test_ntfy_compose_pins_the_versions_digest` — `services.ntfy.image` equals
  `f"{c.image}:{c.version}@{c.pin}"` for `c = load_versions("stack/versions.yaml")["ntfy"]`, and
  that is `docker.io/binwiederhier/ntfy:v2.28.0@sha256:6ef4…73da`.
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
  non-zero, naming ~~`NTFY_BASE_URL`~~ one of the six, and with each one left out in turn it
  names that one. *(Corrected 2026-10-07, in Task 1: Compose names whichever missing variable it
  reaches first, which varies from run to run.)*

**Steps:**

- [ ] **Step 1: Confirm the digest.** **On the Spark** (an anonymous token for Docker Hub's public
  registry; nothing secret):

```bash
t=$(curl -fsS "https://auth.docker.io/token?service=registry.docker.io&scope=repository:binwiederhier/ntfy:pull" | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')
curl -fsSI -H "Authorization: Bearer $t" -H 'Accept: application/vnd.oci.image.index.v1+json' -H 'Accept: application/vnd.docker.distribution.manifest.list.v2+json' https://registry-1.docker.io/v2/binwiederhier/ntfy/manifests/v2.28.0 | grep -i '^docker-content-digest'
```

  Expected: `docker-content-digest: sha256:6ef4b819f722fccdc036af611c4774cfdc2de821ab74fdd48bbf4c9d6f8973da`.
  Anything else: stop, and tell the controller. (ntfy v2.29.0 came out on 2026-10-07, with fixes
  but no security advisory, so v2.28.0 stays under the seven-day rule; the task's report says so,
  and `ntfy.md` §6 moves the pin later.)

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
  values live in its Compose helper ~~and the vault~~, its address, port, folder and topic names
  also in the vault, and its hashes and tokens never there, only in the helper and (the tokens)
  the Spark's root-only header files, the vault recording where; *corrected 2026-10-07, after
  Task 1's review: `CLAUDE.md` keeps credentials in the vault by reference only*), `README.md`
  (§My environment: the values file; §Current state: ntfy on the Synology, its version, its
  helper), `changelog.md`,
  `website/architecture.qmd` (`ntfy` and `ntfy_phone` solid; the edges into `ntfy` stay dashed
  until each publisher is built), `website/how-to/ntfy.md` (only what Dan's run corrects,
  Portainer's steps above all)

**What later tasks read** (values never in the repo): `/etc/local-ai/values.env` and the three
header files, as `ntfy.md` §8 writes them.

- [ ] **Step 1 [Dan, on the Synology and in Tailscale's console]:** `ntfy.md` §1 and §3.
- [ ] **Step 2: The grant, reviewed in this task** (`CLAUDE.md`, *Review permissions … in the task
  that changes them*). **[Dan]** adds the grant of `ntfy.md` §2, then shows it to the session in
  the chat, never in a file. The session checks that it lets ~~exactly the Spark's tag and Dan's
  phone reach ntfy's port on the NAS~~ the Spark's tag reach ntfy's port on the NAS and nothing
  else there, that the members' access to the NAS (the phone's among it) is unchanged from before
  the NAS was tagged, that it widens nothing else, and that the NAS's own node reaches nothing
  new, and says so. The policy stays in the vault. *(Corrected 2026-10-07, after Task 1's review:
  §2's first grant keeps every member device's access to the tagged NAS, which is how the phone
  reaches ntfy's port.)*
- [ ] **Step 3 [Dan, in Portainer on the Synology]:** `ntfy.md` §4, from the web editor: Task 1's
  file reaches GitHub only with the push after Task 35, and a Git-repository stack can follow it
  from then on. Expected: the stack runs, and ntfy's web page answers on the NAS's address
  from the phone, ~~asking for a login~~ loading (`200`), and only a topic there asks for a login.
  *(Corrected 2026-10-07, after Task 1's review: the Compose file sets no `NTFY_ENABLE_LOGIN` or
  `NTFY_REQUIRE_LOGIN`, so the page itself loads; `deny-all` refuses a topic without one.)*
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
  `"starlette>=1.3.1,<2"` (1.3.1 fixed the last of the advisories against 1.x) and
  `"httpx>=0.28,<1"` (explicit; it is already locked through
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
- `test_the_cli_status_and_launch_import_neither_uvicorn_nor_httpx` — a subprocess that runs the
  real path, `from spark import cli; p = cli.build_parser()`, then `p.parse_args(["launch", "m",
  "--", "/bin/x"])` and `p.parse_args(["status", "--json"])`, leaves no module named `uvicorn`,
  `starlette` or `httpx` (or under them) in `sys.modules`. `build_parser()` imports every
  command's module, so every command registers with its heavy imports inside its handler: Tasks
  19, 21 and 30 say so for `gate`, `front` and the commands, and this test holds them to it.

**Steps:**

- [ ] **Step 1:** the failing tests; run them: they fail (`tested_against_problems` and the
  constants missing; uvicorn not in `pyproject.toml`).
- [ ] **Step 2:** the dependencies; **on the Spark**, `uv lock --project spark`. Expected: the lock
  gains uvicorn 0.54.0 (uploaded 2026-09-25) and a starlette 1.x at least seven days old (1.7.0,
  uploaded 2026-09-23, on 2026-10-07; a newer 1.x by the day Task 3 runs is as good), and nothing
  else new. Read both packages' security advisories
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

### Task 4 [Spark]: the registry for 2a, and the private key list

**Files:**

- Create: `stack/keys.example.yaml`, `spark/tests/fixtures/keys.yaml`
- Modify: `spark/src/spark/registry.py`, `spark/src/spark/paths.py` (`KEYS`), `stack/models.yaml`,
  `spark/tests/fixtures/models.yaml`, `spark/tests/test_registry.py`,
  `spark/tests/test_stack_registry.py`

**Interfaces:**

- `Model` gains `label: str` — the plain-words name messages use (*the coder*, *Gemma*, *the
  embeddings*, *whisper*); required and non-empty. `used_by: str | None` — what make-room's list
  says uses a resident (Gemma: `the web UI and photos use it`); optional. `needs_room: bool =
  False` — a model that loads only after make-room frees room for it, which Task 5's check reads
  (Dan's decision, 2026-10-07, after the forward-and-back council); only an on-demand model may
  set it.
- `Budget(allocatable_gib, reserve_gib, idle_available_gib: float, allocatable_measured: bool)` —
  `idle_available_gib` required; `allocatable_measured` defaults to false.
- `KeyGroup(name: str, wait_s: int, queue: int, uses_hold: bool, reloads_marked: bool, words:
  "dan" | "agent", names_processes: bool, max_waiting: int, max_open: int)` — the five things the
  design's one `dan` flag decided, apart (Dan's decision, 2026-10-07): `queue`, lower goes first;
  `uses_hold`, may load into make-room's hold; `reloads_marked`, may load the model the brake
  marked; `words`, whose wording its refusals use; `names_processes`, whether its refusals and
  status name Dan's processes.
- `ClientKey(name: str, group: str, label: str, account: str | None)` — `label` as messages name
  the asker (*pi on the Mac*, *the web UI*, *agent*); `account`, the Unix user whose own refusals
  this key's are, for Task 19's status filter (`agent`'s key: `agent`; None for a key no account
  owns).
- `load_keys(path: Path, groups: dict[str, KeyGroup]) -> dict[str, ClientKey]` — the private key
  list at `paths.KEYS` (`/etc/local-ai/keys.yaml`, or `SPARK_KEYS`), `keys: {<name>: {group, label,
  account}}`; a `RegistryError` naming what's wrong for an unknown group, an empty label, a name
  not `[a-z0-9-]+`, or an unknown field. The real list is never in the repo: Task 36 writes it on
  the box, and `stack/keys.example.yaml` shows its shape with placeholder names, saying where the
  real one lives.
- `GateSettings(idle_unload_min: int, owed_reads_rss: dict[str, bool])` — `owed_reads_rss` per
  engine kind (`llama.cpp`, `whisper.cpp`), since the soak may show `RssAnon` following one engine's
  growth and not another's.
- `NOTIFICATION_TYPES: tuple[str, ...]` — in this order: `brake_fired`, `brake_needs_release`,
  `gate_down`, `front_down`, `llama_swap_down`, `brake_down`, `back_up`, `refused`,
  `footprint_suspect`, `load_failed`, `brake_released`, `room_hold_ended`, `resident_waiting`,
  `apply_restarted`, `load_started`, `loaded`, `unloaded`, `waiting`, `pin_ended`,
  `memory_warning`. `PRIORITIES = ("high", "default", "low", "off")`.
- `Registry` gains `key_groups: dict[str, KeyGroup]`, `notifications: dict[str, str]`, `gate:
  GateSettings`; `SECTIONS` gains `key_groups`, `notifications` and `gate`, each required. A `keys`
  section in the registry is refused, naming `/etc/local-ai/keys.yaml`.
- Checks, each a `RegistryError` naming what's wrong: `queue` a whole number from 0; `words` `dan`
  or `agent`; `wait_s` a positive whole number; `max_waiting` from 1 to 9 (below llama-swap's 10
  per model); `max_open ≥ max_waiting`; every type in `NOTIFICATION_TYPES` listed once, with a
  priority in `PRIORITIES`, and no other key; `idle_available_gib > reserve_gib`;
  `idle_unload_min` positive; `owed_reads_rss` naming exactly the registry's engine kinds;
  `needs_room` only on an on-demand model.
- `stack/models.yaml` gains: `budget.idle_available_gib: 117`, `budget.allocatable_measured: false`;
  `key_groups: {dan: {wait_s: 30, queue: 0, uses_hold: true, reloads_marked: true, words: dan,
  names_processes: true, max_waiting: 8, max_open: 32}, agent: {wait_s: 600, queue: 1, uses_hold:
  false, reloads_marked: false, words: agent, names_processes: false, max_waiting: 4, max_open:
  32}}`; `notifications:` the twenty types at the Global Constraints' priorities; `gate:
  {idle_unload_min: 60, owed_reads_rss: {llama.cpp: false, whisper.cpp: false}}`; each model's
  `label` (`Gemma`, `the embeddings`, `whisper`, `the coder`), and Gemma's `used_by: the web UI and
  photos use it`. The file's head comment says what each new section is for, and that the keys
  live in `/etc/local-ai/keys.yaml`.
- `stack/keys.example.yaml`: `example-mac` (group `dan`, label `pi on the Mac`), `example-web-ui`
  (`dan`, `the web UI`) and `example-agent` (`agent`, `agent`, account `agent`), and a head comment:
  the real list's path and owner (`root:spark-admin 0640`), that its names must be the digests
  file's, and that it never goes in the repo.
- The fixtures: `models.yaml` gains the same sections, with labels `the vision model`, `the
  embeddings`, `whisper` and `the coder` for `vision-chat`, `embed`, `stt` and `coder`;
  `fixtures/keys.yaml` holds `dan-mac`, `open-webui` and `agent` as the example does.

**Tests:**

`spark/tests/test_registry.py`:

- `test_the_fixture_loads_its_2a_sections` — `key_groups["agent"] == KeyGroup("agent", 600, 1,
  False, False, "agent", False, 4, 32)`; `gate == GateSettings(60, {"llama.cpp": False,
  "whisper.cpp": False})`; `budget.idle_available_gib == 117`; `notifications["brake_fired"] ==
  "high"`; `models["coder"].label == "the coder"`; `models["coder"].needs_room is False`.
- `test_the_key_list_loads_from_its_own_file` — `load_keys(fixtures/keys.yaml, …)["dan-mac"] ==
  ClientKey("dan-mac", "dan", "pi on the Mac", None)`; `["agent"].account == "agent"`.
- `test_a_key_must_name_a_group_that_exists` — `agent`'s group `robots`: refused, naming `agent`
  and `robots`.
- `test_the_registry_holds_no_key_list` — a `keys:` section in the registry: refused, naming
  `/etc/local-ai/keys.yaml`.
- `test_every_model_needs_a_plain_words_label` — the coder without `label`, and with `label: ""`:
  each refused, naming `coder` and `label`.
- `test_a_groups_words_are_dan_or_agent` — `words: guest`: refused, naming the two.
- `test_a_groups_queue_is_a_whole_number` — `queue: -1` and `queue: "0"`: refused.
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
- `test_used_by_is_optional_text` — absent → None; `used_by: 3` → refused, naming `used_by`.
- `test_needs_room_is_for_on_demand_models_only` — `needs_room: true` on the vision model, a
  resident: refused; on the coder: loads.
- `test_owed_reads_rss_names_each_engine_kind` — `whisper.cpp` missing, or a kind no model uses:
  refused, naming it.
- `test_a_new_section_may_not_be_missing` — each of `key_groups`, `notifications`, `gate` removed
  in turn: refused, naming it.

`spark/tests/test_stack_registry.py`:

- `test_the_stack_registrys_waits_are_30s_for_dan_and_10_minutes_for_agent` — `dan.wait_s == 30`,
  `agent.wait_s == 600`.
- `test_the_stack_registrys_groups_give_2as_behaviour` — `dan`: `queue` 0, `uses_hold`,
  `reloads_marked`, `words` `dan`, `names_processes`; `agent`: `queue` 1, none of the three, `words`
  `agent`.
- `test_the_stack_registrys_caps_are_the_rulings` — `dan`: 8 and 32; `agent`: 4 and 32.
- `test_on_demand_models_idle_unload_after_60_minutes` — `gate.idle_unload_min == 60`.
- `test_owed_reads_rss_stays_off_until_the_soak` — every engine kind `False`. (Task 45 changes this
  test, with the soak's evidence.)
- `test_every_notification_is_on_at_the_plans_priority` — `notifications` equals the twenty types
  at the Global Constraints' priorities; none is `off`.
- `test_the_stack_registrys_labels_are_the_plans_words` — `Gemma`, `the embeddings`, `whisper`,
  `the coder`, by model.
- `test_the_budget_records_idle_memavailable` — `idle_available_gib == 117`,
  `allocatable_measured is False`.
- `test_the_example_key_list_loads_and_names_no_real_key` — `stack/keys.example.yaml` loads
  against the stack's groups, and its names all start `example-`.

**Steps:**

- [ ] **Step 1:** the failing tests; run them: they fail (no `label`, no new sections, no
  `load_keys`).
- [ ] **Step 2:** `registry.py`, `paths.KEYS`, the fixtures, `stack/models.yaml` and
  `stack/keys.example.yaml`; the tests pass; `make test lint`, including
  `test_the_real_registry_renders`. From here the clone's registry is one the deployed app can't
  read: deploy nothing (*Deploys during 2a*).
- [ ] **Step 3: Commit.** **On the Spark:**

```bash
git add spark/src/spark/registry.py spark/src/spark/paths.py stack/models.yaml stack/keys.example.yaml \
  spark/tests/fixtures/models.yaml spark/tests/fixtures/keys.yaml spark/tests/test_registry.py \
  spark/tests/test_stack_registry.py
git commit -m "feat(spark): 🤖 the registry gains labels, key groups, notifications and the gate's settings; the key list goes private" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 5 [Spark]: the budget — rule 9's formulas, and render's corrected check

**Files:**

- Create: `spark/src/spark/budget.py`, `spark/tests/test_budget.py`
- Modify: `spark/src/spark/render.py` (`check_budget` uses `budget.check_set`; `run` prints the
  warnings), `spark/tests/test_render.py`, `CLAUDE.md` (the reserve gotcha's dated note)

**Interfaces** (exact `Decimal` arithmetic throughout, from `Decimal(repr(x))`, as
`admission._gib` does; each public function sets its own precision, 400 digits, on a copy of
its caller's decimal context, whatever the caller's precision, *added 2026-10-07, at Task 5's
review; worded at its re-review*):

- `Loaded(name: str, footprint_gib: float, held_now_gib: float)` — `held_now` is what the model's
  load took (the fall in `MemAvailable` across it), plus its engine's `RssAnon` growth since, when
  the registry's `gate.owed_reads_rss` is on.
- `owed_gib(loaded: Iterable[Loaded]) -> Decimal` — Σ max(0, footprint − held_now).
- `free_for_a_load(*, available, reserve, owed, ceiling, committed, starting, held) -> Decimal` —
  `min(available − reserve − owed, ceiling − committed) − starting − held`.
- `hold_after_dans_load(*, free_outside_hold, hold, footprint) -> Decimal` — ~~`max(0, hold −
  max(0, footprint − free_outside_hold))`~~ `max(0, hold − max(0, footprint − max(0,
  free_outside_hold)))`: the hold shrinks only by the part of the load the room outside it
  couldn't cover (rule 4), and that room, below 0 once Dan's job has taken it, counts as none, so
  the hold keeps counting what his job allocated *(corrected 2026-10-07, at Task 5's review: the
  first formula shrank a 70 GiB hold to 7 for a 5 GiB load with −58 outside it, the controller's
  ruling)*.
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
  errors: ~~residents + reserve > `idle_available_gib`~~ residents >
  `free_for_a_load(available=idle_available, reserve=reserve, owed=0, ceiling=allocatable,
  committed=0, starting=0, held=0)`, that is `min(idle_available − reserve, allocatable)`, 93 for
  the 2a set, since the residents load together at boot and have to fit under both, worded with
  no negative number *(corrected 2026-10-07, at Task 5's review: the first check had no ceiling
  term, the controller's ruling)*; an on-demand model without `needs_room` whose footprint
  exceeds `free_for_a_load(available=idle_available − residents, reserve=reserve, owed=0,
  ceiling=allocatable, committed=residents, starting=0, held=0)` — the gate's formula at idle with
  the residents loaded, `min(117 − 43 − 24, 102 − 43)`, 50 for the 2a set; a `needs_room` model
  whose footprint exceeds `min(idle_available − reserve, allocatable)` (the residents' call, with
  nothing loaded, *since Task 5's review*), 93 for the 2a set, since it loads only after
  make-room has freed room, residents included. Warnings: Σ footprints >
  `allocatable_gib` (Dan's decision, 2026-10-07, after the forward-and-back council: the gate
  admits each load against live memory, so the whole registry needn't fit at once, and later
  phases' registries won't); `idle_available − Σ footprints < brake.warn_gib`, which says by how
  much Σ passes idle when it does, never a negative number (*added 2026-10-07, at Task 5's
  review*). Each line names its numbers to one decimal, the need rounded up and the room down;
  a room that rounds down to 0 (below 0.1 GiB) reads *nothing is free for a load* (*added
  2026-10-07, at Task 5's review; at its re-review the controller ruled that a room under 0.1
  reads as nothing, not "0.0 GiB is free"*).
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
- `test_dans_load_takes_from_the_hold_only_what_the_room_outside_it_cant_cover` — −58, 70, 5 → 65
  (*added 2026-10-07, at Task 5's review*).
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
- `test_a_set_over_the_ceiling_is_a_warning` — the same plus a 20 GiB on-demand model → no error,
  and a warning holding `104.0` and `102.0`.
- `test_residents_and_the_reserve_must_fit_idle_memavailable` — residents 32, 8, 3 and a 60 GiB
  resident, reserve 24, idle 117 → an error holding ~~`127.0` and `117.0`~~ `103.0` and `93.0`
  *(corrected 2026-10-07, at Task 5's review)*.
- `test_the_residents_must_fit_under_the_ceiling_too` — residents 43, ceiling 40, with no on-demand
  model, a `needs_room` one or a plain one → the first error is the residents', holding `43.0` and
  `40.0`, and no line holds a negative number (*added 2026-10-07, at Task 5's review*).
- `test_an_on_demand_model_that_cant_load_beside_the_residents_is_refused` — residents 43 in all,
  an on-demand model of 57 (Σ 100) → an error naming that model, `57.0` and `50.0`.
- `test_a_model_that_needs_room_is_checked_against_the_box_less_the_reserve` — the same model with
  `needs_room: true` → no error; at 95 → an error naming it, `95.0` and `93.0`.
- `test_a_model_that_needs_room_fits_under_the_ceiling_too` — ceiling 85, a `needs_room` model of
  90 → an error naming it, `90.0` and `85.0` (*added 2026-10-07, at Task 5's review*).
- `test_render_warns_when_everything_loaded_sits_under_the_warn_line` — residents 43, the coder 41
  and a 10 GiB on-demand model → no error; one warning holding `23.0` and ~~`28`~~ `28.0`.
- `test_a_set_past_idle_memory_says_by_how_much_never_a_negative` — residents 43, the coder 41 and
  a 45 GiB on-demand model (Σ 129) → no error; a warning holding `12.0` and `117.0`, and none a
  negative number (*added 2026-10-07, at Task 5's review*).
- `test_render_and_the_gate_share_one_formula` — `free_for_a_load` replaced with a spy:
  `check_set` calls it ~~once per on-demand model without `needs_room`, with available = idle less
  the residents' sum,
  owed 0, committed = the residents' sum, starting 0, held 0~~ once for the residents and once per
  on-demand model, in the registry's order: with nothing loaded (available = idle, committed 0) for
  the residents and a `needs_room` model, beside the residents (available = idle less their sum,
  committed = their sum) for any other, owed, starting and held 0 throughout; each check's room is
  the spy's answer *(corrected 2026-10-07, at Task 5's review: all three checks share the
  formula)*.
- `test_the_numbers_add_up_exactly` — available 52.3, reserve 24, owed 0, ceiling 102, committed
  0, starting 0, held 0 → exactly `28.3`.
- `test_each_formula_is_exact_on_its_own` — under the caller's default 28 digits, 1e30 less 0.1
  stays exact in `free_for_a_load`, `owed_gib` and `hold_after_dans_load`, and 1e30 plus 0.1 in
  `make_room_plan`, which adds (*added 2026-10-07, at Task 5's review; "plus" for
  `make_room_plan` at its re-review*).
- `test_residents_exactly_at_the_room_pass` — Gemma 32, the embeddings 8.1 and whisper 3.2, 43.3
  in all, against a ceiling of 43.3, and against an idle of 67.3 less the 24 reserve → no error;
  binary floats would sum them to 43.300000000000004 (*added 2026-10-07, at Task 5's re-review*).
- `test_a_room_under_a_tenth_reads_nothing_is_free` — residents 43, idle 67.05, a 1 GiB
  on-demand model → its error reads *nothing is free for a load*, not "0.0 GiB" (*added
  2026-10-07, at Task 5's re-review*).
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
git commit -m "feat(spark): 🤖 rule 9: render checks each model against the residents at idle, and warns on the set" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 6 [Spark]: the words, 1 — every refusal, its status and its headers

**Files:**

- Create: `spark/src/spark/messages.py`, `spark/tests/test_messages.py`
- Modify: `spark/src/spark/render.py`, `spark/tests/test_render.py` (*added 2026-10-07, at Task 6's
  fix round 1, the controller's rulings: render refuses registry text that pi's retry list matches*)

**Interfaces:**

- `messages.py` imports nothing of `spark` at module level but `spark.registry`, and names
  `budget.Candidate` only under `TYPE_CHECKING`, so the front, which imports it (Task 21's
  `FRONT_MODULES`), never pulls in the budget and never restarts for a change there.
- `CODES_409 = ("no_fit", "loading", "held_by_brake", "footprint_suspect", "load_failed",
  "not_downloaded", "restarting", "llama_swap_down", "draining")` — every refusal the gate or the
  front gives after a wait, or that no retry soon changes (Dan's decision, 2026-10-07, after the
  forward-and-back council, added the last three); `CODES_503 = ("gate_down",)` — the front's
  answer at once while the gate is down; `FRONT_CODES = {"model_not_found": 404,
  "too_many_requests": 429, "route_not_served": 404, "concurrency_limit": 429}`; `RETRY_AFTER_S =
  {"no_fit": 30, "held_by_brake": 300, "gate_down": 30, "restarting": 60, "draining": 60,
  "llama_swap_down": 30, "too_many_requests": 10}`; `UNKNOWN_KEY = "That API key isn't one the
  Spark knows. Check SPARK_API_KEY on this machine."`
- `Refusal(code: str, status: int, message: str, retry_after_s: int | None)`:
  - `body() -> dict` — `{"error": {"message": message, "code": code}}`, plus `"retry_after_s": n`
    at the top level when there is one. No other key in `error`, and never `detail` (*Before
    Task 1*: pi shows every key, and Open WebUI shows `detail` in place of `message`).
  - `headers() -> list[tuple[bytes, bytes]]` — `content-type: application/json`; for a 409 or a
    503, `x-should-retry: false`, and `retry-after: <n>` when there is one; for a 429,
    `retry-after: <n>` when there is one; for a 404, neither.
- `PI_RETRY_PATTERNS: tuple[str, ...]` — in `messages.py`, since `load_failed`'s text uses it, and
  the tests read it from there: pi-ai 0.85.1's `RETRYABLE_PROVIDER_ERROR_PATTERN` list
  (`dist/utils/retry.js`, joined with `|`, matched case-insensitively), one pattern per line, word
  for word, then 0.87.1's two additions (the last two lines):

```text
overloaded
rate.?limit
too many requests
429
500
502
503
504
524
service.?unavailable
server.?error
internal.?error
provider.?returned.?error
exceeded request buffer limit while retrying upstream
network.?error
connection.?error
connection.?refused
connection.?lost
other side closed
fetch failed
getaddrinfo
ENOTFOUND
EAI_AGAIN
upstream.?connect
reset before headers
socket hang up
socket connection was closed
timed? out
timeout
terminated
websocket.?closed
websocket.?error
ended without
stream ended before message_stop
stream ended before a terminal response event
http2 request did not get a response
retry delay
you can retry your request
try your request again
please retry your request
ResourceExhausted
currently experiencing high demand
520
```

- `load_failed`'s refusal quotes `engine_said` only when it matches none of `PI_RETRY_PATTERNS`;
  otherwise it reads *The coder started loading but failed: the engine stopped. On the Spark, `spark
  logs coder` shows the engine's last lines.* (*corrected 2026-10-07, at Task 6's fix round 2, the
  controller's ruling: it named `spark status`, which shows no engine lines; Task 30's `spark logs
  <model>` does, so `load_failed` needs `model_command`*), so an engine's `timeout` or `terminated`
  never makes pi repeat a full load. Its notification (Task 7), which pi never sees, quotes the line
  either way. *(Added 2026-10-07, at Task 6, the controller's ruling: the same guard covers every
  text from outside the registry and the key list. A holder's process name, from `/proc`, that pi's
  list matches is named only as *a process of Dan's, <n> GiB* or *a process, <n> GiB*. An asked name
  that it matches reads *There's no model by that name here.* Each is put on one line first.
  Registry labels are Dan's own text.)*
- `Holder(name: str, gib: float, dans: bool)` — a memory holder as messages name it, defined
  here; Task 8's `procs.top_holders` builds them.
- `Moment` — what a message needs, every field with a default, since the front builds its own
  refusals (`draining`, `model_not_found`, `restarting`, `concurrency_limit`, `gate_down`) with
  none of the memory numbers: `needed_gib`, `available_gib`, `reserve_gib`, `owed_gib`, `held_gib`,
  `hold_counted: bool`, `holders: list[Holder]`, `wait_s`, `model_label`, `model_command` (the name
  `spark load` takes: the model's first role, else its name), `key_label`, `words: "dan" |
  "agent"` and `names_processes: bool` (the asking key's group's, Task 4), and per code:
  `loading_label`, `brake_at: datetime`, `brake_available_gib`, `release_waits_for_dan: bool`,
  `engine_said: str | None`, `deadline_s`, `download_gib`, `inflight`, `drain_for: "make-room" |
  "unload" | "idle"`, `asked_name`, `models: list[tuple[label, name]]`. *(Added 2026-10-07, at
  Task 6, the controller's rulings:*
  - *`free_gib: float | None`, `no_fit`'s figure for free for a load. It is the gate's own,
    what `budget.free_for_a_load` returned for the request, so its ceiling term counts. The words
    never work it out, and `no_fit` without it is a `ValueError`. `available_gib`, `reserve_gib`,
    `owed_gib` and `held_gib` only make up the breakdown in the parenthesis.*
  - *`warn_gib` (28) and `release_after_s` (300), `held_by_brake`'s warn line and release time:
    the registry's `brake.warn_gib` and `RELEASE_AFTER_S`, so the words stay true when either
    changes.*
  - *`words` also picks `model_not_found`'s next step, so the front passes it there too.)*

  *(Added 2026-10-07, at Task 6's fix round 1, the controller's rulings:*
  - *`ceiling_gib: float | None` and `committed_gib`, which the gate fills only when `ceiling −
    committed` was the smaller term of rule 9's `min()`, and `starting_gib`, a model still
    starting. The breakdown is then the term that gave `free_gib`, so it adds up: *(the 102 GiB
    the GPU can allocate, less the 92 GiB the loaded models may grow to)*, and *… and the 27 GiB
    the model still starting may take*. The words still compute nothing.*
  - *Each code's words refuse a `Moment` that lacks a field they use, a `ValueError` naming the
    code and the field (*`no_fit`'s words need free_gib*). So `needed_gib`, `available_gib`,
    `reserve_gib`, `wait_s`, `words`, `drain_for` and `asked_name` default to None. `asked_name`
    may be empty, since a client can send that, and then reads *There's no model by that name
    here.* The front's own refusals need only what the front fills: `words` for `draining`,
    `model_not_found` and `gate_down`, and `key_label` for `agent`'s `draining` for make-room.*
  - *`duration(seconds)` and `pi_retry_match(text)` are public, for render's check.)*
- `refusal(code: str, m: Moment) -> Refusal` — one sentence a person reads, then the numbers, then
  one next step. The rules every message keeps:
  - a model by its label, and its label's first letter capitalised at the start of a sentence when
    it starts with "the " (*The coder*, *Gemma*, *whisper*); a key by its label as is (*pi on the
    Mac*, *agent*, *The web UI* at a sentence's start);
  - *available* only for `MemAvailable`, *free for a load* only for the admission figure, never
    "free" alone;
  - *free for a load* ~~at 0 or below~~ that rounds down to 0 as shown (below 1 GiB in whole GiB,
    below 0.1 GiB at one decimal) reads *nothing is free for a load*, never a negative number or
    a zero (*corrected 2026-10-07, at Task 5's re-review, after the controller's ruling on
    render's words*);
    with a make-room hold counted, *nothing is free for a load while make-room holds <n> GiB for
    Dan*, and the parenthesis then lists the available memory and what else is taken from it (the
    docs reviewer's I-4: once Dan's job runs in his hold, `agent`'s figure is 36 − 24 − 70); the
    figure is `free_gib`, the gate's own, rounded down (*added 2026-10-07, at Task 6*);
  - sizes in whole GiB (a need rounded up; *available* and *free for a load* rounded down; the
    rest to the nearest), but a reading near a line (the brake's, the warn line's) to one decimal
    (*19.6 GiB*);
  - a time as the local 24-hour `HH:MM` of an aware `datetime`;
  - a wait as `<n> s` under a minute~~, else `<n> minutes` (`1 minute`)~~ (*corrected 2026-10-07, at
    Task 6's fix round 1, the controller's ruling: every duration, a deadline included, from a
    minute on, in minutes and seconds, `1 minute 30 s`, `3 minutes`, and from an hour, hours too, so
    none prints as a bare 5xx*);
  - *no_fit*'s parenthesis lists the reserve, then the growth owed when it isn't 0, then the hold
    when it is counted, joined "A and B" or "A, B and C". Where the ceiling binds, it starts from
    the ceiling less the loaded models' footprints instead, and a model still starting comes
    before the hold (*added 2026-10-07, at Task 6's fix round 1*);
  - the wording follows `words`: `dan`'s rows for `dan`, `agent`'s for `agent`; and unless
    `names_processes`, a process of Dan's is named *a process of Dan's, <n> GiB*;
  - in `agent`'s words, a step only Dan can take says it is Dan's, and `agent` is never told to
    run a command only Dan can run (*added 2026-10-07, at Task 6's fix round 1, the controller's
    rulings*). ~~The plan's own *On the Spark, `spark make-room --done` ends the hold.*, in
    `agent`'s `no_fit`, is the one exception~~: there is none, since `agent`'s `no_fit` now ends
    *The hold ends when Dan runs `spark make-room --done` on the Spark.* (*corrected 2026-10-07, at
    Task 6's fix round 2, the controller's ruling*) (*and, corrected at Task 7's review the same day,
    the controller's ruling: only when ending the hold would let the load fit; otherwise "Only Dan
    can free memory for it, on the Spark; try again after that."*);
  - no refusal makes pi retry it. ~~A size pi's list would match (429 GiB and up, past this box's
    memory) moves a GiB or two against the load: a need up, a room down.~~ A size over 400 GiB
    reads *more than 400 GiB*, and a `no_fit` that needs that much ends *The Spark can never free
    that much.* No number a message shows is altered, and none of that size can occur on this box
    (*corrected 2026-10-07, at Task 6's fix round 2, the controller's ruling*). The one exception is
    `concurrency_limit`'s own *Too many requests*, a `429`, which pi retries by its status, as the
    plan intends (*added 2026-10-07, at Task 6's fix round 1, the controller's rulings*);
  - a breakdown adds up as shown: its terms in whole GiB when those add up to the figure shown, else
    to one decimal, or two if one still doesn't. The start rounds down, as *available* does, and the
    rest to the nearest. The figure stays whole, rounded down (*added 2026-10-07, at Task 6's fix
    round 3, the controller's ruling*).

**The refusals, word for word.** Each test's expected text is plan.md's (*What you see in Phase
2a*, the refusal table), its italics' asterisks dropped and its backticks kept, with these inputs
(the examples' moment: the residents loaded, the coder not, a 32 GiB python job of Dan's; times in
the test's own time zone):

| Code | Inputs |
|---|---|
| `no_fit` (Dan) | needed 41, free for a load 18 (the gate's `free_gib`, *added 2026-10-07, at Task 6*), available 48, reserve 24, owed 6, hold not counted, holders `python3 (chendaniely)` 32 and Gemma 27, key *pi on the Mac*, command `coder` |
| `no_fit` (`agent`) | needed 41, free for a load 12 (*added 2026-10-07, at Task 6*), available 106, reserve 24, owed 0, hold 70 counted, holders the embeddings 8 and whisper 3, key *agent*: *The coder didn't load: it needs 41 GiB, and 12 GiB is free for a load (106 GiB available, less the 24 GiB reserve and the 70 GiB make-room holds for Dan). Using memory now: the embeddings 8 GiB, whisper 3 GiB. The hold ends when Dan runs `spark make-room --done` on the Spark.* (*Corrected 2026-10-07, at Task 6's fix round 2, the controller's ruling: it ended "On the Spark, `spark make-room --done` ends the hold."*) |
| `no_fit` (`agent`, Dan's job running in the hold) | needed 41, free for a load −58 (*added 2026-10-07, at Task 6*), available 36, reserve 24, owed 0, hold 70 counted, holders a process of Dan's 70 and the embeddings 8, key *agent*: *The coder didn't load: it needs 41 GiB, and nothing is free for a load while make-room holds 70 GiB for Dan (36 GiB available, less the 24 GiB reserve). Using memory now: a process of Dan's, 70 GiB, the embeddings 8 GiB. ~~The hold ends when Dan runs `spark make-room --done` on the Spark.~~ Only Dan can free memory for it, on the Spark; try again after that.* (*Corrected 2026-10-07, at Task 6's fix round 2, the controller's ruling, as above.*) (*Corrected again at Task 7's review, the same day, the controller's ruling: ending the hold would leave 36 − 24 = 12 GiB, short of 41, so its end isn't the step.*) |
| `loading` | loading Gemma, wait 30 s |
| `held_by_brake` | brake at 03:12, 19.6 available, release automatic |
| `held_by_brake` (waits for Dan) | the same, waiting for Dan: *Not loading the coder now: memory ran low at 03:12 (19.6 GiB available), and new loads stay paused until you release them: on the Spark, `make brake-release`.* |
| `gate_down` | key *pi on the Mac* (Dan's words; ~~none~~, *added 2026-10-07, at Task 6's fix round 1, the controller's rulings: `words` picks its last sentence*) |
| `load_failed` | the engine said `failed to load model`, command `coder` (*added 2026-10-07, at Task 6's fix round 2, the controller's ruling: the next step names `spark logs coder`*) |
| `load_failed` (deadline) | no engine text, deadline 180: *The coder started loading but didn't finish within 3 minutes. On the Spark, `spark logs coder` shows the engine's last lines.* (*Corrected 2026-10-07, at Task 6's fix round 1: it read "within 180 s". Corrected at fix round 2: it named `spark status`.*) |
| `not_downloaded` | download 16 |
| `restarting` | wait 30 s |
| `llama_swap_down` | wait 30 s |
| `draining` (Dan) | 1 in flight, for make-room, wait 30 s |
| `draining` (`agent`) | 1 in flight, for make-room, wait 600 s: *The coder is being unloaded for make-room once its 1 request in flight finishes, and your 10 minutes ran out. It won't load for agent while make-room's hold stands. The hold ends when Dan runs `spark make-room --done` on the Spark.* (*The last sentence added 2026-10-07, at Task 6's fix round 1, the controller's rulings.*) |
| `footprint_suspect` | brake at 03:12, command `coder` |
| `model_not_found` | asked `qwen3.6-35b-a3b`; models the coder (`qwen3.8-27b`), Gemma, the embeddings, whisper — on-demand first, then residents, each in registry order; key *pi on the Mac* (Dan's words) |
| `model_not_found` (`agent`) | the same, key *agent*: *There's no model called qwen3.6-35b-a3b here. The models are the coder (qwen3.8-27b), Gemma (gemma-4-26b-a4b), the embeddings (qwen3-embedding-0.6b) and whisper (whisper-large-v3-turbo). On the Spark, as `agent`, `/opt/local-ai/app/.venv/bin/spark clients pi --write --registry /opt/local-ai/etc/models.yaml` updates pi's list.* (*Added 2026-10-07, at Task 6, the controller's ruling: `agent`'s pi is on the Spark. Corrected at Task 6's fix round 1: the first correction named the procedure before 2a, pulling `agent`'s clone and running `spark clients pi --write`. After the cutover `agent` has no clone and runs Task 34's deployed CLI. The command is Task 34's, and the coder swap's (Task 42, Step 6), byte for byte, without `--http-idle-timeout-ms`, so pi's settings stay as they are.*) |
| `too_many_requests` | key *agent* |
| `route_not_served` | none |
| `draining` (not make-room) | 1 in flight, for an unload, wait 30 s: *The coder is being unloaded once its 1 request in flight finishes, and your 30 s ran out. Try again in a minute.* |
| `concurrency_limit` | model the coder, the only `Moment` field the front fills: *Too many requests for the coder at once; try again in a moment.* |

**Tests** (`spark/tests/test_messages.py`):

- `test_each_refusal_reads_word_for_word` — parametrized over the table's ~~twenty~~ twenty-one rows
  (*`model_not_found` for `agent` added 2026-10-07, at Task 6*):
  `refusal(code, moment).message` equals the expected text exactly.
- `test_every_code_has_its_status` — each of `CODES_409` gives 409, `gate_down` 503;
  `model_not_found` and `route_not_served` 404; `too_many_requests` and `concurrency_limit` 429,
  each with its headers.
- `test_pi_wont_retry_a_refusal` — for each of `CODES_409`, at each of the table's inputs, the
  text pi would show, `"409: " + json.dumps(body["error"])`, matches none of `PI_RETRY_PATTERNS`
  (case-insensitive); the same text with `503` matches, so the test can fail.
- `test_a_load_failed_never_quotes_words_pi_would_retry` — `engine_said` `CUDA error: operation
  timed out` → the variant without the quote, matching none of `PI_RETRY_PATTERNS`; `failed to
  load model` → quoted, as the table's row.
- `test_retry_after_follows_the_ruling` — `retry_after_s` is `RETRY_AFTER_S.get(code)` for every
  code; `headers()` holds `retry-after` exactly when it is set.
- `test_409s_and_503s_say_dont_retry` — every 409's and 503's `headers()` holds
  `x-should-retry: false`; no 404 or 429 holds it.
- `test_the_body_is_openais_error_shape_with_no_detail` — `body()` for `no_fit` is
  `{"error": {"message": <its sentence>, "code": "no_fit"}, "retry_after_s": 30}`; for `loading`,
  the same with no `retry_after_s`.
- `test_nothing_free_for_a_load_never_reads_negative` — the clamp row's inputs give its text, and
  no refusal built from any `free for a load` below 0 holds a minus sign.
- `test_agent_names_dans_processes_only_as_a_process_of_dans` — `no_fit` for `agent` at the Dan
  moment's holders: *Using memory now: a process of Dan's, 32 GiB, Gemma 27 GiB.*
- `test_the_wording_follows_the_groups_words` — the `no_fit` moment with `words: "agent"` and
  `names_processes: True` → `agent`'s sentence with `python3 (chendaniely)` named; with `words:
  "dan"` and `names_processes: False` → Dan's sentence with *a process of Dan's*.
- `test_sizes_round_against_the_load` — needed 40.2 shows *41 GiB*; available 47.9 shows *47 GiB*;
  a holder of 26.6 shows *27 GiB*; a brake reading of 19.64 shows *19.6 GiB*.
- `test_times_are_the_local_24_hour_clock` — 15:07 local, from an aware `datetime` in another
  zone, shows *15:07*.
- `test_the_front_builds_its_own_refusals_with_defaults` — `refusal("restarting",
  Moment(model_label="the coder", wait_s=30))` and the other four front-built codes need no other
  field. (*Corrected 2026-10-07, at Task 6's fix round 1: no field but those the front fills.
  For `draining`, `model_not_found` and `gate_down` that includes `words`, the asking key's
  group's, which picks `agent`'s forms.*)
- `test_messages_imports_no_more_than_the_registry` — a subprocess importing `spark.messages`
  leaves `spark.budget` out of `sys.modules`.
- `test_the_unknown_key_text_is_the_rulings` — `UNKNOWN_KEY` is the ruling's sentence.
- *Added 2026-10-07, at Task 6, with the controller's rulings:*
  - `test_free_for_a_load_is_the_gates_own_figure` — ~~where the ceiling binds, `free_gib` 10 is
    below the breakdown's 48 − 24 − 6, and the message shows 10;~~ 17.99 shows 17~~; `no_fit`
    without `free_gib` is a `ValueError`~~ (*corrected 2026-10-07, at Task 6's fix round 3: the
    ceiling's case is `test_the_breakdown_is_the_term_that_gave_the_figure`'s, its breakdown the
    ceiling's, and the missing field is
    `test_each_code_refuses_a_moment_without_the_fields_its_words_use`'s*).
  - `test_no_outside_text_makes_pi_retry_a_refusal` — holders named `timeout (chendaniely)` and
    `terminated (agent)` read *a process of Dan's, 32 GiB* and *a process, 8 GiB*, and pi's text
    matches nothing. An asked name `gpt-timeout` reads *There's no model by that name here.*
- *Added 2026-10-07, at Task 6's fix round 1, with the controller's rulings:*
  - `test_agents_words_read_word_for_word` — `agent`'s forms in plan.md's table, each from its
    Dan row's inputs with `agent`'s key: `no_fit` with no hold counted, `held_by_brake` (both
    forms), `gate_down`, `load_failed`, `not_downloaded` and `llama_swap_down`;
    `test_pi_wont_retry_a_refusal` covers them too.
  - `test_the_breakdown_is_the_term_that_gave_the_figure` — the ceiling binding (10 GiB, *the 102
    GiB the GPU can allocate, less the 92 GiB …*), a model starting (*… and the 27 GiB the model
    still starting may take*), both, and `agent`'s counted hold under the ceiling, with its clamp.
  - `test_agent_is_never_told_to_run_what_only_dan_can` — every refusal in `agent`'s words: a
    sentence naming `spark make-room`, `make brake-release`, `spark load`, `make pull`, `make
    doctor` or `make clients` names Dan, ~~but the plan's own *On the Spark, `spark make-room
    --done` ends the hold.*~~ every one, `spark logs` among them (*corrected 2026-10-07, at Task 6's
    fix round 2, the controller's ruling*)
  - `test_no_refusal_makes_pi_retry_at_any_boundary` — every row, in both words, at sizes of 429
    to 524 GiB, deadlines and waits of 500, 503, 520 and 529 s and of 500 to 520 minutes, and
    names ending in `-500m`: no message matches pi's list, but `concurrency_limit`'s own *Too many
    requests*, a `429`. ~~`test_a_size_pi_would_match_moves_against_the_load` shows how each
    moves.~~ `test_a_size_over_400_gib_reads_more_than_400_gib` shows each size over 400 GiB as
    *more than 400 GiB*, and each up to it as itself, 400 included (*corrected 2026-10-07, at Task
    6's fix round 2, the controller's ruling*).
  - `test_a_breakdown_adds_up_as_shown` (*added 2026-10-07, at Task 6's fix round 3, the
    controller's ruling*): 48.3 − 24 − 6.2 reads whole, 48 − 24 − 6; 48.9 − 24 − 6.6 reads to one
    decimal, since 48 − 24 − 7 is 17, not the 18 shown; the ceiling's 101.9 − 91.6 the same way;
    48.95 − 24 − 6.95 to two decimals.
  - `test_each_code_refuses_a_moment_without_the_fields_its_words_use` — for each code, each field
    its words use, left at its default → a `ValueError` naming the code and the field;
    `test_some_fields_are_needed_only_where_the_words_use_them` and
    `test_the_front_builds_its_own_refusals_with_defaults` cover the rest.
  - `test_durations_read_in_seconds_under_a_minute_then_in_minutes_and_seconds` — 59 → *59 s*, 90
    → *1 minute 30 s*, 180 → *3 minutes*, 500 → *8 minutes 20 s*.
  - In `test_render.py`: `test_registry_text_pi_would_retry_on_is_refused` — a label holding `500`,
    a role `timeout`, a model named `coder-500m`, a key group named `agent-503`, and a wait that
    reads *429 hours*: each refused by `render.check_words`, naming the field.

**Steps:**

- [ ] **Step 1:** the failing tests; run them: they fail (`spark.messages` missing).
- [ ] **Step 2:** `messages.py`'s refusals; the tests pass; `make test lint`.
- [ ] **Step 3: Commit.** **On the Spark:**

```bash
git add spark/src/spark/messages.py spark/tests/test_messages.py
git commit -m "feat(spark): 🤖 every refusal in Dan's words, with its status and headers" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 7 [Spark]: the words, 2 — every notification and confirmation, and the notifications page

**Files:**

- Create: `website/reference/notifications.md` (generated)
- Modify: `spark/src/spark/messages.py`, `spark/tests/test_messages.py`, `spark/src/spark/docs.py`,
  `spark/tests/test_scenarios.py` (the docs command's tests), `website/design/plan.md` (dated notes
  under *What you see in Phase 2a*, and a Revisions line)

**Interfaces:**

- `Notification(type: str, priority: str, message: str)`; `notification(type: str, registry,
  **fields) -> Notification | None` — None when the registry has the type `off`; its fields, by
  type, are the table *The notifications' fields* below, and every gate module passes exactly
  those. *(Added 2026-10-07, at Task 7, the controller's ruling: a field the type doesn't take,
  or one its words use left out, is a `ValueError` naming it, even for a type that is `off`; a
  time is an aware datetime or Unix seconds, as the gate keeps them; the host the alerts name is
  the box's short host name, read when each is built.)*
- `REFUSAL_NOTIFICATION = {"footprint_suspect": "footprint_suspect", "load_failed":
  "load_failed"}`, every other refusal code → `refused`: each refusal sends exactly one
  notification (plan.md, *What you see in Phase 2a*, 2026-10-07). The front's own refusals,
  `model_not_found`, `too_many_requests`, `route_not_served` and `draining`, reach the gate through
  `POST /v1/front/refused` (Task 10) and are sent the same way; a `401` and a `gate_down` aren't (the
  front's journal has the first, the notifier's alert the second). *(Added 2026-10-07, at Task 7,
  the controller's ruling: `refused` refuses `footprint_suspect`, `load_failed` and `gate_down`,
  naming where each goes, so no refusal is sent twice. A burst goes as one on the refusal's own
  type, `refused`, `footprint_suspect` or `load_failed`, so it keeps that type's priority and its
  `off`: each of the three takes the burst's fields, `model_label`, `key_label`, `count`, `since`
  and `code`, in place of its own when `count` is given; `load_failed`'s burst takes no
  `key_label`, below. A burst's model passes the single form's gate: on one line, and a client's
  name for `model_not_found` only when it reads as a model's, never a web address; without one it
  reads *Refused a request from agent 4 more times since 09:12: same reason.*)* *(Corrected
  2026-10-07, at Task 7's review, the controller's ruling: `refusal_notification(code, *,
  request_id, ticket_id=None) -> (type, event key) | None` — None for `gate_down`; `refusal:<request
  id>` for every refusal but `load_failed`, which is keyed by its load, `load-failed:<ticket id>`,
  so the requests that joined one failed start send one `load_failed`. Its words name no asker, and
  its burst is per model: *The coder failed to load 4 more times since 09:12.*)*
- `NOTIFICATION_DOC: dict[str, tuple[str, str]]` — each type's *When* and *Example*, from plan.md's
  table.
- Confirmations, each the plan's *Each command says what it did, and how to undo it* row, word for
  word: `loaded(label, seconds, idle_min, command)`, `unloading(label, inflight)`,
  `unloaded(label)`, `pinned(label, until: datetime, loaded_s: float | None, command)`,
  `unpinned(label, idle_min)`, `room_list(target_gib, free_now_gib, candidates: list[Candidate],
  unload: list[str], free_after_gib)`, `room_held(unloaded: list[str], free_gib, held_gib, until:
  datetime | None)`, `room_all()`, `room_too_much(target_gib, most_gib)`, `room_done(unused_gib,
  total_gib, reloading: list[str], next_label: str | None)`, `brake_released_by_dan(reloading:
  list[str])`, `apply_waiting(model_label, quiet_for_s, needed_s)`, `apply_no_quiet(inflight:
  int)`, `apply_now_confirm(inflight: list[str])` (the askers' key labels). make-room's list: a
  numbered row per candidate, its label, its name in brackets for a chat model only, its size,
  *always loaded* or *loads when asked*, then ` · idle <n> min` for an idle on-demand model and
  ` · <used_by>` for a resident that has one; the header and the last line as the plan's example.
  *(Added 2026-10-07, at Task 7, the controller's rulings: `room_list(target_gib, free_now_gib,
  candidates, unload, free_after_gib, *, registry)`, since the chat models and `used_by` are the
  registry's, not `Candidate`'s; `target_gib` None for `--all`. A row also marks, after `used_by`,
  ` · pinned`, ` · session: <label>` and ` · <n> request(s) in flight for <age>` (*the oldest for*
  with two or more), as plan.md's rule 4 says, and ` · idle <n> min` only with none in flight.
  When unloading every candidate can't reach the target, the list ends at its rows, and
  `room_too_much` asks the question; with nothing to unload, its last line offers the hold.
  `pinned` and `room_held` take `now` (a keyword, the clock by default): an end more than a day
  away names its day too. `apply_no_quiet(inflight, deadline_s=900)` names the deadline it is
  given, so Task 32's `--deadline-s` reads true. `BRAKE_RELEASED_WITHOUT_GATE` is Task 23's text
  for `make brake-release` with the gate down.)*
- `docs.render_notifications_page(registry) -> str` — front matter (`title: "Notifications"`), a
  line saying it is generated from `stack/models.yaml` by `spark docs notifications --write`, and
  a table *Type · Priority · When · Example*, one row per type in `NOTIFICATION_TYPES` order, and
  under it a line saying that a change to the four `*_down` priorities needs `make install-units`
  after `make apply`, since the notifier's unit carries them (Task 25); `spark docs notifications
  --write | --check` (`--check` exits 1, saying the page is stale, when it differs). *(Added
  2026-10-07, at Task 7, the controller's ruling: `make docs` writes the page too, and
  `test_the_committed_notifications_page_is_current` fails while it is stale; CI's `--check` comes
  with Task 52, *Deferred notes for implementers*.)*

**The notifications' fields.** What `notification` takes for each type; each test builds its
plan.md example from these (local times in the test's zone). *(Added 2026-10-07, at Task 7, the
controller's rulings: the fields marked* added *below, each so that the plan's own example, or a
true sentence in every case the gate sends it, can be built.)*

| Type | Fields |
|---|---|
| `brake_fired` | `at`, `available_gib`, `line_gib`, `unloaded: list[(label, state)]`, `follow_up: bool`, `by_brake: bool` (sent by the brake with the gate down: its last sentence then reads *They resume once the gate is back and memory has stayed above 28 GiB available for 5 min.*). Added: `release_after_s` (gateproto's `RELEASE_AFTER_S`, which `messages` can't import, so the caller passes it: required, with no default to go stale); the 28 GiB is the registry's `brake.warn_gib`; a follow-up needs neither reading nor line; each `state` is `starting` (*loading*), `idle` or `answering`, or None when the gate's record was missing; and `release_waits_for_dan: bool`, required (a brake within the hour after an automatic release, rule 5), with `released_at`, the automatic release's time, whose last sentence is then *It fired within an hour of the automatic release at 03:40, so they stay paused until you release them: on the Spark, `make brake-release`.*, ending in `held_by_brake`'s words for Dan, in place of *They resume …*, `by_brake` or not, and needing no `release_after_s` |
| `brake_needs_release` | `fired_at`~~, `why: "reboot" \| "again"`, `released_at` (for `again`: *The brake fired again at 03:50, within an hour of its automatic release at 03:40, so new loads stay paused until you release them: on the Spark, `make brake-release`.*)~~ — sent only for a hold found after a reboot (corrected 2026-10-07, at Task 7, the controller's ruling: rule 5's alert for a brake within the hour is that brake's own `brake_fired`, with `release_waits_for_dan`, so one event sends one notification) |
| `gate_down`, `front_down`, `llama_swap_down`, `brake_down` | `at`, `result_words` — added: the notifier's result in words (Task 26's, *it crashed* …), to which the words add *; it is restarting* (*within 2 s* for the brake); None, as for llama-swap that stopped answering with its unit up, gives no parenthesis |
| `back_up` | `unit`, `down_s`; its second sentence per unit: the gate *New loads work again.*, the front *Requests go through again.*, llama-swap *Models answer again.*, the brake *Memory is watched again.* Added: `unit` is `gate`, `front`, `llama-swap` or `brake` |
| `refused` | ~~`model_label`, `key_label`, `needed_gib`, `free_gib`, `holders`, `next_step`~~ `code` and `moment`, the `Moment` the refusal was built from, worded for Dan's phone: *Refused <model> for <key>: <why>. <Dan's step, where there is one>.* (`no_fit`'s is the plan's example; `holders` there are those outside the stack); the burst: `model_label`, `key_label`, `count`, `since`, `code` (added 2026-10-07, at Task 7, the controller's ruling: the old fields gave no reason for any code but `no_fit`, and a `next_step` passed in would have put Dan's words in the gate) |
| `footprint_suspect` | `model_label`, `fired_at`, `command`; added: `key_label` (*for agent*) |
| `load_failed` | `model_label`, `engine_said: str \| None`, `deadline_s`; added: `command` (*`spark logs coder`*); sent once per failed load, never with a key (corrected 2026-10-07, at Task 7's review, the controller's ruling: `refusal_notification` keys it by the load's ticket); its burst: `model_label`, `count`, `since`, `code` |
| `brake_released` | `at`, `available_gib`, `reloaded: list[label]`, `loading_label: str \| None` |
| `room_hold_ended` | `why: "--done" \| "time" \| "reboot" \| "used"`, `unused_gib`, `total_gib`, `reloading: list[label]`, `next_label` |
| `resident_waiting` | `label`, `needed_gib`, `free_gib`; added: `after: "hold" \| "brake" \| "apply" \| "boot" \| "restart" \| "crash"`, since *after the hold ended* is false in the other cases Tasks 15 and 17 send it |
| `apply_restarted` | `at`, `reloaded: list[label]`, `on_demand: list[label]` |
| `load_started` | `label`, `key_label`, `last_s: float \| None` (`key_label` None for a resident's reload or one of Dan's commands) |
| `loaded` | `label`, `seconds` |
| `unloaded` | `label`, `why: "idle" \| "make-room" \| "unload"`, `idle_min` |
| `waiting` | `label`, `key_label`, `why: "memory" \| "brake" \| "slot" \| "dan"`, `wait_s`, `needed_gib`, `free_gib` (for `memory`); added: `command`, for `dan` (*`spark load coder`*); `loading_label`, for `slot` (*Gemma is loading, and one model loads at a time.*); `release_waits_for_dan` and, unless it waits for Dan, `release_after_s`, for `brake`, which then says the pause ends by itself, in `held_by_brake`'s words |
| `pin_ended` | `label`, `at`, `idle_min` (None for an always-loaded model) |
| `memory_warning` | `available_gib`, `warn_gib`, `brake_gib` |

**Tests** (`spark/tests/test_messages.py`, unless named):

- `test_each_notification_reads_word_for_word` — parametrized over plan.md's notification table
  (its *Example* column, asterisks dropped), each type with its row's inputs, and the follow-up
  `brake_fired` and the `room_hold_ended` variant (*… Reloading Gemma.*) given in full there:
  `notification(type, registry, …).message` equals it.
- `test_a_load_failed_notification_quotes_the_engine_either_way` — `CUDA error: operation timed
  out` and `failed to load model` are each quoted.
- `test_an_off_notification_sends_nothing` — the registry with `loaded: off` → `None`.
- `test_each_refusal_sends_exactly_one_type` — `REFUSAL_NOTIFICATION.get(code, "refused")` for every
  code: `footprint_suspect` and `load_failed` their own, the rest `refused`.
- `test_the_make_room_list_follows_its_rule` — the plan's make-room example from its inputs → the
  plan's block, word for word, the bracketed names on the coder and Gemma only.
- `test_each_confirmation_reads_word_for_word` — parametrized over the plan's command table: each
  confirmation function's text equals its row's *What it says*.
- `test_no_message_says_free_alone` — over every refusal, notification and confirmation the
  tests build, "free" never appears but in "free for a load" ~~.~~ *(corrected 2026-10-07, at Task
  7's review: and in the verb phrases the plan's and Task 6's own words use, "Free space with
  `spark make-room`", "Only Dan can free memory for it", "The Spark can never free that much" and
  "Free <n> and hold it?", the controller's ruling; "frees" and "freed" count as "free".)*
- `test_models_are_named_by_label` — no refusal or notification built in these tests holds a
  model's registry name, but `model_not_found`'s list ~~.~~ *(corrected 2026-10-07, at Task 7's
  review: and its notification, which may name the name asked for; the confirmations aren't
  checked, since make-room's list brackets the chat models' names.)*
- In `test_scenarios.py`: `test_the_notifications_page_is_generated_from_the_registry` —
  `render_notifications_page(the stack registry)` has twenty rows, each type's priority the
  registry's, and the `make install-units` line for the four `*_down` types;
  `test_spark_docs_notifications_check_finds_a_stale_page` — a changed page → exit 1.

**Steps:**

- [ ] **Step 1:** the failing tests; run them: they fail (`notification` and the confirmations
  missing).
- [ ] **Step 2:** the notifications, the confirmations and the docs command; **on the Spark**,
  `uv run --frozen --project spark spark docs notifications --write`; the tests pass;
  `make test lint`.
- [ ] **Step 3: Docs.** plan.md, *What you see in Phase 2a*, dated notes (2026-10-07, this plan's
  rulings): the generated table lives at `website/reference/notifications.md`, which is kept
  current; a refusal's body carries only `message` and `code` in `error`. (The statuses, the
  retry-afters, the clamp's wording and pi's and Open WebUI's handling are already in plan.md,
  from 2026-10-07; check them against what Tasks 6 and 7 built.) A Revisions line records them.
- [ ] **Step 4: Commit.** **On the Spark:**

```bash
git add spark/src/spark/messages.py spark/tests/test_messages.py spark/src/spark/docs.py spark/tests/test_scenarios.py \
  website/reference/notifications.md website/design/plan.md
git commit -m "feat(spark): 🤖 every notification and confirmation in Dan's words, and the notifications page" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 8 [Spark]: memory and processes — MemFree, RssAnon, engines' pids, the top holders

**Files:**

- Create: `spark/src/spark/procs.py`, `spark/tests/test_procs.py`
- Modify: `spark/src/spark/memory.py`, `spark/tests/test_launch.py` (where `parse_meminfo`'s tests
  live)

**Interfaces:**

- `MemInfo` gains ~~`free_gib: float | None = None`~~ `memfree_gib: float | None = None` and
  `cached_gib: float | None = None` (`MemFree`, `Cached`), read when present; Phase 1's two fields
  unchanged. *(Renamed 2026-10-08, at Task 8's review: `free_gib` means free for a load everywhere
  else.)*
- `boot_id(path: Path = Path("/proc/sys/kernel/random/boot_id")) -> str` — its text, stripped.
- `procs.rss_anon_gib(pid: int, *, proc: Path = Path("/proc")) -> float | None` — `RssAnon` from
  `<proc>/<pid>/status`, in GiB; None for a process gone or a file without it. Never `VmRSS`.
- `procs.port_of(proxy: str) -> int | None` — the port of `/running`'s `proxy` field
  (`http://127.0.0.1:801` → 801).
- `procs.engine_pid(port: int, *, spark_uid: int, recorded: int | None = None, proc: Path =
  Path("/proc")) -> int | None` — a `recorded` pid (launch's, which the gate keeps in
  `state.ticketed`) that is alive and runs as `spark` is taken as it is, whatever its `comm`, so a
  later engine kind isn't counted as an outside holder; otherwise the process whose real uid is
  `spark_uid`, whose `comm` is `llama-server` or `whisper-server`, and whose argv holds `--port`
  followed by `port`.
- `procs.NVIDIA_APPS = ["nvidia-smi", "--query-compute-apps=pid,used_memory",
  "--format=csv,noheader,nounits"]`; `parse_nvidia_apps(text: str) -> dict[int, float | None]` —
  MiB to GiB; `[N/A]` reads as None.
- `procs.top_holders` returns `messages.Holder`s (Task 6).
- `procs.top_holders(loaded: dict[str, tuple[str, float]], nvidia: dict[int, float | None], *,
  engine_pids: set[int], dan_uids: set[int], proc: Path = Path("/proc"), limit: int = 3) ->
  list[messages.Holder]` — each loaded model by its label at what it holds now; each other process
  holding at least 1 GiB, by `<comm> (<user>)`, at the larger of its `nvidia-smi` figure and its
  `RssAnon`; the engines' own pids left out (they are the models); `dans` true for a process whose
  uid is in `dan_uids`; largest first, `limit` of them. *(Added 2026-10-08, at Task 8's review, the
  controller's ruling: every name is put on one line with no control character, as
  `messages._one_line` puts it, since `comm` is the process's own raw text and Task 29 prints the
  holders to a terminal. A process gone mid-read is passed over, but a `PermissionError` from
  `/proc` propagates, so the holders never silently leave out a process that `/proc` lists but
  won't let the gate read.)*

**Tests** (`spark/tests/test_procs.py`, unless named; `/proc` is a tree the test builds):

- `test_rss_anon_reads_the_anonymous_figure_not_vmrss` — `status` with `VmRSS: 4194304 kB` and
  `RssAnon: 1048576 kB` → 1.0.
- `test_rss_anon_of_a_gone_process_is_none` — no `<pid>` folder → None.
- `test_port_of_reads_runnings_proxy` — `http://127.0.0.1:801` → 801; `nonsense` → None.
- `test_engine_pid_finds_the_engine_by_its_port_among_sparks_processes` — pid 200, `comm`
  `llama-server`, argv `…\0--port\0801\0…`, `Uid:` spark's → `engine_pid(801)` is 200;
  `engine_pid(802)` is None; with `recorded=300`, a live `spark` process whose `comm` is
  `python3`, → 300; `recorded` a pid that's gone → the scan's answer.
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

- [ ] **Step 1:** the failing tests; run them: they fail (`spark.procs` missing; no ~~`free_gib`~~
  `memfree_gib`, *renamed 2026-10-08, at Task 8's review*).
- [ ] **Step 2:** `memory.py` and `procs.py`; the tests pass; `make test lint`.
- [ ] **Step 3: Commit.** **On the Spark:**

```bash
git add spark/src/spark/memory.py spark/src/spark/procs.py spark/tests/test_procs.py spark/tests/test_launch.py
git commit -m "feat(spark): 🤖 read MemFree, each engine's anonymous RSS and the top memory holders" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 9 [Spark]: service plumbing — systemd's sockets, the caller's uid, the watchdog, credentials

**Files:**

- Create: `spark/src/spark/sockets.py`, `spark/src/spark/protocols.py`,
  `spark/src/spark/sdnotify.py`, `spark/src/spark/credentials.py`, `spark/src/spark/serve.py`,
  `spark/tests/test_sockets.py`, `spark/tests/test_protocols.py`, `spark/tests/test_sdnotify.py`,
  `spark/tests/test_credentials.py`
- Modify: `website/how-to/updates.md` (a uvicorn bump runs `test_sockets.py` beside
  `test_protocols.py`, and moves `serve.TESTED_AGAINST` with the pin)

**Interfaces:**

- `sockets.listen_fds(env: MutableMapping[str, str] = os.environ, pid: int | None = None) ->
  dict[str, socket.socket]` — requires `LISTEN_PID` equal to `pid` (default `os.getpid()`),
  `LISTEN_FDS` a count, `LISTEN_FDNAMES` that many colon-separated names; the sockets are fds 3
  upward, each made with `socket.socket(fileno=fd)`, so the family is the socket's own; it removes
  the three variables. `SocketsError` names what is missing or disagrees. Names in use: `front`;
  `status`, `control`. *(Added 2026-10-08, at Task 9's review, the controller's ruling: an fd that
  isn't a socket raises `SocketsError` too, naming the fd and its name.)*
- `protocols.make_protocol(*, peer_cred: bool, header_timeout_s: float | None) -> type` — a subclass
  of uvicorn's `H11Protocol` (`uvicorn.protocols.http.h11_impl`). With `peer_cred`, on
  `connection_made` it reads `SO_PEERCRED` (Linux's; on any other system `make_protocol(peer_cred=
  True)` raises at once, since the gate runs only on the Spark) from an `AF_UNIX` transport's socket
  and wraps the app, so each request's `scope["extensions"]["peer_cred"]` is `{"pid": int, "uid":
  int, "gid": int}`; a TCP connection gets no such key. With `header_timeout_s`, ~~a connection
  whose first request's headers aren't complete in time is closed; the body, once the headers are
  in, has no such limit~~ a connection is closed when it doesn't finish a request's head, or a body
  nobody will read, in time (the correction below). `protocols.peer(scope) -> PeerCred | None`,
  where `PeerCred(pid: int, uid: int, gid: int)`. `TESTED_AGAINST = {"pypi:uvicorn": "0.54.0"}`.
  *(Corrected 2026-10-08, at Task 9 and its review, the controller's rulings. The deadline covers
  each request's head on a connection, not only the first: uvicorn's keep-alive timer stops at a
  request's first byte, so a later head sent a little at a time would hold the connection. And "no
  such limit" holds only until the answer is sent. An app that answers before reading the body (the
  front's 401, 413 and 404, the gate's 403) leaves the client sending a body uvicorn drops unread,
  and each byte of it stops the keep-alive timer; so once the answer is sent, what is left of the
  body gets the same deadline, from the next read. A body the app is still reading has no limit.)*
  *(Added 2026-10-08, at Task 9's re-review: after such an early answer, the next request's deadline
  runs from the first read after that answer, not from a fresh start once the body ends.)*
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
  whatever matches first. *(Added 2026-10-08, at Task 9's review, the controller's rulings:
  `match_key` returns None for a key no encoder takes, a lone surrogate, rather than raise an
  error whose text quotes it; and `read_credential_text(name, env) -> str` reads a credential of
  many lines, the key list and the key digests, whole, refused by name when missing, empty or
  blank, not UTF-8, or holding a control character other than a tab or a line break.)*
- `serve.QuietServer(uvicorn.Server)` — its `capture_signals` is a plain context manager that only
  yields: uvicorn 0.54.0's own (`server.py`) replaces any earlier handler for SIGINT and SIGTERM
  with its own, restores it on the way out and re-raises the signal, so with two servers in one
  loop the second's handler replaces the first's and the last re-raise kills the process by
  signal. `serve.TESTED_AGAINST = {"pypi:uvicorn": "0.54.0"}`. *(Qualified 2026-10-08, at Task 9's
  review: that is what two plain uvicorn servers do, seen on the Spark. Inside `run_servers`, whose
  loop handlers are set before any server starts and get every signal through asyncio's wakeup
  fd, uvicorn's own handling turned out harmless, so `QuietServer` is a defence there;
  `test_quiet_server_leaves_sigint_and_sigterm_alone` pins it.)*
- `serve.run_servers(pairs: list[tuple[ASGIApp, list[socket.socket]]], *, protocol: type,
  graceful_s: float) -> None` — one event loop; one `QuietServer` per pair, run with
  `serve(sockets=…)`, each with `log_config=None`, `access_log=False`, `server_header=False`,
  `date_header=False`, `proxy_headers=False`, `lifespan="off"`,
  `timeout_graceful_shutdown=graceful_s`; `loop.add_signal_handler` for SIGTERM and SIGINT, which
  sets `should_exit` on every server; `READY=1` once every server listens; the watchdog loop when
  `watchdog_interval_s()` gives one; `STOPPING=1` on the way out; returns, exit 0, when a signal
  has arrived and every server has stopped. *(Added 2026-10-08, at Task 9's review, the
  controller's ruling: `ws="none"` too, so an upgrade never leaves `protocol` for a WebSocket
  library's, which would serve the request without its uid or the header deadline.)*

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

- `test_the_gate_receives_each_callers_uid` — skipped off Linux, with the reason *SO_PEERCRED is
  Linux's, and the gate runs only on the Spark*: an app that answers the scope's peer uid and pid,
  over the Unix socket, from this process, answers `os.getuid()` and `os.getpid()`.
- `test_peer_cred_refuses_off_linux` — with `sys.platform` patched to `darwin`,
  `make_protocol(peer_cred=True)` raises, naming `SO_PEERCRED`.
- `test_a_tcp_request_carries_no_peer_cred` — the same app over TCP answers `none`.
- `test_forwarded_headers_change_nothing` — through `run_servers`, an app that answers its
  `scope["client"]` and `scope["scheme"]`, sent `X-Forwarded-For: 203.0.113.9` and
  `X-Forwarded-Proto: https` from 127.0.0.1 → it still sees 127.0.0.1 over http. Seen failing
  first with uvicorn's default, which trusts those headers from loopback. *(Added 2026-10-07, at
  Task 3, the controller's ruling: pins `proxy_headers=False`, so no local process, `agent`'s
  included, can make the front or the gate see a forged address or scheme.)*
- `test_a_connection_that_never_sends_its_headers_is_closed` — `header_timeout_s=0.5`; a raw
  client sends `GET / HTTP/1.1\r\n` and nothing more: the server closes the connection within 2 s.
- `test_a_request_that_sends_its_headers_in_time_is_served` — the same timeout, headers sent at
  once: 200.
- `test_a_slow_body_after_timely_headers_is_served` — the same timeout, headers at once, then a
  body trickled over 1.5 s (three times the timeout) → 200 with the whole body read.

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
  never ends, SIGTERM ends the process within `graceful_s` (0.5) plus 1.5 s, with exit 0 (not
  killed by the signal), both servers stopped and `STOPPING=1` sent; SIGINT the same.
- `test_run_servers_says_ready_once_both_listen` — the `NOTIFY_SOCKET` stand-in receives exactly
  one `READY=1`, after both sockets answer.

*(Added 2026-10-08, at Task 9 and its review.)* Beside these:

- **New tests.** `test_listen_fds_refuses_an_fd_that_isnt_a_socket`,
  `test_sd_notify_reaches_an_abstract_socket`,
  `test_a_later_request_on_the_connection_gets_the_same_deadline`,
  `test_an_answered_request_cant_hold_its_connection_with_a_trickled_body`,
  `test_a_websocket_upgrade_stays_a_plain_request_on_its_connection` and
  `test_quiet_server_leaves_sigint_and_sigterm_alone`.
- **Tests that check more.**
  - The uid test also sends a WebSocket upgrade.
  - The forwarded test also finds no `server:` or `date:` header and no lifespan scope.
  - The shutdown test runs for SIGTERM and for SIGINT, with a request in flight that finishes in
    the graceful time.
  - The credentials tests cover a surrogate key and `read_credential_text`.
- **Four tests skip off Linux**, each for a Linux-only fact:
  - the uid test and `test_a_tcp_request_carries_no_peer_cred`: *SO_PEERCRED is Linux's, and the
    gate runs only on the Spark*;
  - `test_a_tcp_socket_from_systemd_keeps_tcp_nodelay`: `socket.socket(fileno=)` reads a socket's
    protocol only where `SO_PROTOCOL` exists, and the front runs only on the Spark;
  - `test_sd_notify_reaches_an_abstract_socket`: abstract Unix sockets are Linux's, as systemd is.

**Steps:**

- [ ] **Step 1:** the failing tests; run them: they fail (the five modules missing).
- [ ] **Step 2:** the five modules; the tests pass, also on the Mac's shorter socket paths, where
  ~~the uid test skips~~ four tests skip (Task 52); `make test lint`, including Task 3's
  `TESTED_AGAINST` test, which
  now finds `protocols.TESTED_AGAINST`. *(Corrected 2026-10-08, at Task 9's review: four tests
  skip on the Mac, not one, each for a Linux-only fact, as the note under the tests lists them;
  and Task 3's test finds `serve.TESTED_AGAINST` too, both of them by name.)*
- [ ] **Step 3: Commit.** **On the Spark:**

```bash
git add spark/src/spark/sockets.py spark/src/spark/protocols.py spark/src/spark/sdnotify.py \
  spark/src/spark/credentials.py spark/src/spark/serve.py spark/tests/test_sockets.py \
  spark/tests/test_protocols.py spark/tests/test_sdnotify.py spark/tests/test_credentials.py \
  website/how-to/updates.md
git commit -m "feat(spark): 🤖 systemd's sockets, each caller's uid, the watchdog and credentials for the new services" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 10 [Spark]: the gate's protocol, and the CLI's standard-library client

**Files:**

- Create: `spark/src/spark/gateproto.py`, `spark/src/spark/gateclient.py`,
  `spark/tests/test_gateclient.py`
- Modify: `spark/src/spark/paths.py`, `spark/tests/test_tested_against.py` (the import check gains
  `spark.gateclient`)
- *(Added 2026-10-08, at Task 10, the controller's rulings:)* Modify also
  `spark/tests/test_doctor.py`, whose stand-in llama-swap now answers at `paths.LLAMASWAP_URL`,
  since 127.0.0.1:9100 is no longer its default; and `spark/src/spark/cli.py` with
  `spark/tests/test_cli.py`, for `cli.main`'s catch of a `GateError` (below).

**Interfaces:**

- `paths` gains, each overridable by its variable: `GATE_STATUS_SOCKET`
  (`/run/local-ai/gate-status.sock`, `SPARK_GATE_STATUS`), `GATE_CONTROL_SOCKET`
  (`/run/local-ai/gate-control.sock`, `SPARK_GATE_CONTROL`), `GATE_STATE`
  (`/var/lib/local-ai/gate`, `SPARK_GATE_STATE`), `LAUNCH` (`/var/lib/local-ai/launch`,
  `SPARK_LAUNCH`), `WHISPER_TMP` (`/var/lib/local-ai/whisper-tmp`), `VALUES`
  (`/etc/local-ai/values.env`), `FRONT_URL` (`http://127.0.0.1:9100`); `LLAMASWAP_URL`'s default
  becomes `http://127.0.0.1:900`.
- `gateproto` — the gate's constants (the Global Constraints' values, and this plan's
  `GRACEFUL_S = 20` and `LLAMA_SWAP_HUNG_S = 10`): `MAX_REQUEST_BYTES =
  65536`, `REQUEST_TIMEOUT_S = 5.0`, `LOAD_CALL_TIMEOUT_S = 200.0`, `PING_EVERY_S = 1.0`,
  `GATE_DOWN_AFTER_S = 5.0`, `DRAIN_GRACE_S = 90.0` *(was ~~`30.0`~~ until 2026-10-08: past the
  unload's 60 s, Task 16's note)*, `QUIET_S = 60`, `APPLY_DEADLINE_S = 900`,
  `ACTIVITY_EVERY_S = 1.0`, `ACTIVITY_STALE_S = 3.0`, `SESSIONS_PER_UID = 8`, `SESSION_TTL_S =
  43200`, `REFUSAL_HISTORY = 50`, `NTFY_TIMEOUT_S = 5.0`, `BURST_WINDOW_S = 600`,
  `BACK_UP_AFTER_S = 60`, `RELEASE_AFTER_S = 300`, `AUTO_RELEASE_EVERY_S = 3600`,
  `NOTIFIER_EVERY_S = 300`, `APPLY_RENEW_S = 15`, `APPLY_LAPSE_S = 60`, `NOTIFIED_KEEP_S = 86400`,
  `SESSION_HOLD_RENEW_S = 60`. *(Added 2026-10-08, at Task 12's review, the controller's ruling:
  `UNLOAD_CALL_TIMEOUT_S = 60.0`, the gate's unload call, which Task 12's client uses.)*
- `Route(socket: "status" | "control", method: str, path: str, callers: "front" | "users" |
  "owner" | "admin")` and `ROUTES`, the table below; message shapes as `TypedDict`s. JSON over
  HTTP/1.1 on the Unix sockets.

  | Socket | Route | Callers | Request → answer |
  |---|---|---|---|
  | status | `POST /v1/admit` | front | `{model, key, deadline, request_id}`, `deadline` in Unix seconds (`time.time()`), as every time in these messages → held until ready or refused; always `200`, `{ok: true, model}` or `{ok: false, code, status, message, retry_after_s}`; any other status is the gate's own failure |
  | status | `GET /v1/front/events` | front | NDJSON, kept open: `{op: "hello", gate_started_at, applying: bool, lapse_s, models}` (a front that gets `applying` true holds every new request, as on `hold_all`, so a restarted front holds again; `lapse_s` is the hold's bound, `APPLY_LAPSE_S`, which `hold_all` carries too; `models` is the gate's model list, `[{name, roles, label, resident}]`, which the front serves from, Dan's decision of 2026-10-07), `{op: "state", ready, starting, draining, models}` (lists of model names, and the model list again; sent with `hello` and on every change, a registry re-read among them), `{op: "drain", model, drain_id, why}` (`why` one of `make-room`, `unload`, `idle`), `{op: "undrain", model, drain_id}`, `{op: "unloaded", model}`, `{op: "hold_all"}` and `{op: "release_all"}` (apply's restart), `{op: "ping", at}` every second |
  | status | `POST /v1/front/inflight` | front | a whole snapshot, never a delta: `{seq, front_started_at, models: {name: {count, oldest_started_at, last_end_at, requests: [{key, started_at}]}}, draining}`, so `/v1/quiet` and apply's question can name each request's asker |
  | status | `POST /v1/front/drained` | front | `{model, drain_id}` |
  | status | `POST /v1/front/busy` | front | `{model, drain_id}` — the front's answer to an idle drain that finds a request in flight; the gate unloads nothing |
  | status | `POST /v1/front/refused` | front | `{at, code, model, key, message}` — a refusal of the front's own (`model_not_found`, `too_many_requests`, `route_not_served`, `draining`), for *recent* and `refused` |
  | status | `GET /v1/status` | users | `StatusView`, filtered for the caller |
  | status | `POST /v1/sessions` · `POST /v1/sessions/{id}/renew` · `DELETE /v1/sessions/{id}` | owner | `{model, pid, label}` → `{id, expires_at}`; `spark-admin` may end any |
  | control | `GET /v1/status` | admin | the full `StatusView` |
  | control | `POST /v1/load` | admin | `{model}` → the confirmation's fields (`label`, `seconds`, `idle_min`, `command`), or a refusal; held up to `LOAD_CALL_TIMEOUT_S`, not `REQUEST_TIMEOUT_S` |
  | control | `POST /v1/unload` | admin | `{model}` → NDJSON in two steps: `{inflight: n}` at once, then `{unloaded: true}` once the drain is done and the model gone, or a refusal; unbounded, since a drain waits for the requests in flight |
  | control | `POST /v1/pin` · `DELETE /v1/pin/{model}` | admin | `{model, until}` (`until` null for no end); a pin that loads first is held up to `LOAD_CALL_TIMEOUT_S` |
  | control | `POST /v1/make-room/plan` · `POST /v1/make-room` | admin | `{size_gib}` or `{all: true}` → `{plan_id, plan}`, `plan` Task 5's `RoomPlan`; `{plan_id, for_s}` → `{unloaded, free_gib, hold_gib, until}`, unbounded, since it drains each model it unloads |
  | control | `POST /v1/release` | admin | `{room: bool, brake: bool}` — `spark make-room --done` sends room only, `make brake-release` brake only → `{room: {unused_gib, total_gib, next_label} \| null, brake: bool, reloading: [labels]}`, what each ended, with the fields its confirmation needs |
  | control | `GET /v1/quiet` | admin | → `{quiet_for_s, last_label, inflight: [{model, model_label, key_label, age_s}]}` (`last_label` the model that answered last) |
  | control | `POST /v1/drain-all` · `POST /v1/undrain-all` | admin | apply's hold, written to the gate's persisted state (`GateState.applying`, Task 13), so it survives the gate's own restart: the front holds every new request (`hold_all`, and `applying` in every `hello`), admission answers `restarting` at their deadlines, never `llama_swap_down`, and nothing unloads; requests in flight finish; ended by any of the ends the next rows name, each doing the same work |
  | control | `POST /v1/apply/renew` | admin | `{since}` → `{ok: true}` while that hold stands, `{ended: true}` once it has ended, so a renewal never starts a hold again; `make apply` sends it every `APPLY_RENEW_S` (15 s) from `drain-all` to its end |
  | control | `POST /v1/apply/begin` · `POST /v1/apply/end` | admin | `{restarting: [units]}`, persisted with the hold, before the first restart; *end*, posted once llama-swap answers again, ends the hold. Every end — *end*, `undrain-all`, the gate seeing llama-swap answer again after a restart `begin` named, and a hold not renewed for `APPLY_LAPSE_S` (60 s), before `begin` as after — takes one path, once: it re-reads the registry, releases the hold (`release_all`), then queues the residents' reload ahead of anything else, in one step of the gate's loop, so no held request takes the load slot first, and sends `apply_restarted` when llama-swap did restart; a second end does nothing. So a `make apply` that died holds new requests for at most `APPLY_LAPSE_S` past its last renewal |
  | control | `GET /v1/logs/{model}?n=` | admin | `{lines: [...]}`, the engine's last lines, read with the gate's key; `{model}` a model's name or one of its roles, resolved as `spark load`'s argument is (*added 2026-10-07, at Task 6's re-review: the refusals name the role*) |
  | control | `POST /v1/canary` | admin | `{needle}` → `{found: [where]}`: whether the string is in the gate's state, its refusal history or launch's records, never what surrounds it (doctor's `--full`) |

  There is no cancel route: the front drops its admit call when its client goes, and the gate
  takes the dropped call as the cancel.

  *(Added 2026-10-08, the controller's rulings, at Task 10's review:)*
  - `POST /v1/make-room` answers as `/v1/unload` does: NDJSON, its head at once. Each model's
    drain sends a line as it begins, `{model, label, inflight}` (`gateproto.MakeRoomProgress`,
    which Task 7's `unloading` words). The result above comes last, so a gate that is down shows
    in time.
  - Every answer with a status other than 2xx, and a refusal sent as a stream's line, is
    `gateproto.GateRefusalBody`: `{message, code}`, never the front's `{"error": …}`.
    `/v1/admit` alone answers a refusal with 200 and `ok: false`.
  - A 2xx answer with nothing in it, such as a 204, reads as `{}`.
  - `/v1/quiet`'s `inflight` items are `gateproto.QuietInflight`.
  - *(Added 2026-10-08, the controller's ruling, at Task 10's re-review:)* `POST /v1/load` answers
    as `/v1/unload` does too: NDJSON, its head at once. As the load starts, it sends
    `{model, label, last_s}` (`gateproto.LoadProgress`, which Task 7's `load_started` words); a
    model already loaded sends none. The confirmation's fields come last. A gate that is down
    then shows within the CLI's 5 s, not after the load call's 210.
  - *(Added 2026-10-08, the controller's ruling, at Task 10's re-review, on its fix round 2:)* `POST /v1/pin` on a model that isn't loaded streams as `/v1/load`
    does: its head at once, the same `LoadProgress` line as the load starts, then
    `gateproto.PinConfirmation` (`{label, until, loaded_s, command}`, which Task 7's `pinned`
    words). A pin on a model already loaded answers once, with `PinConfirmation` alone. The CLI
    reads both through `stream()`, which reads a single answer as its one line.
  - *(Added 2026-10-08, the controller's rulings, at Task 10's second re-review:)* a load, or a pin that loads, that waits for the slot or for
    memory sends a `gateproto.WaitProgress` line as it starts waiting, and again whenever its
    reason changes, before its `LoadProgress`. The line carries the fields Task 7's `waiting` words
    its reason with: `{model, label, why, needed_gib, free_gib, loading_label,
    release_waits_for_dan, release_after_s}`, None where a reason has no use for one. So a queued
    load is never silent. `LoadConfirmation.seconds` is None for a model already loaded, as
    `messages.loaded` takes it.

- `StatusView` (also `--json`'s shape, for 2b's menu bar): `schema` (1, raised when a field's
  meaning changes), `host`, `at`; `memory` (`total_gib`,
  `available_gib`, `brake_gib`, `warn_gib`, `above_brake_gib`, `reserve_gib`, `owed_gib`,
  `held_gib`, `free_for_a_load_gib`, `unaccounted_gib`); `models` (each: `name`, `label`,
  `resident`, `footprint_gib`, `state` — `ready`, `starting`, `draining`, `not_loaded` — `inflight`,
  `oldest_request_s`, `last_use`, `pinned_until`, `sessions`, `brake_mark` with `at` and
  `seen_gib`); `waiting` (`key_label`, `model`, `waited_s`, `wait_s`, `why` — one of `WAITING_WHY =
  ("memory", "brake", "slot", "dan", "restart", "llama_swap")`); `paused` (`since`, `available_gib`,
  `releases_at`, `waits_for_dan`, `last_fired`, `last_released`) or null; `held` (`size_gib`,
  `until`) or null; `pins`; `sessions`; `recent` (`at`, `text`, `code`, `model`, `key_label`);
  `health` (`front`, `gate`, `brake` with `key_checked_at`, `llama_swap`, `ntfy` with
  `failing_since`, `activity_age_s`, `unticketed_engines` — each engine (`{model, port, pid}`)
  that Task 8's `procs` finds on an engine port and whose pid no ticket's start recorded this boot,
  the evidence of a load around the gate — and `no_ticket_refusals`, the count of starts launch
  refused for want of a ticket since this boot, which is the backstop working, never a bypass);
  `applying` (`since`, `restarting`) or null; `problems`. *(Added 2026-10-08, at Task 10, the
  controller's ruling: each model also carries `pinned: bool`, and `pinned_until` is only the pin's
  end, null for a pin with no end, as Task 7's `pinned` confirmation takes it, so a pin with no end
  is never read as no pin.)*
- `gateclient.GateClient(path: Path, timeout_s: float)`: `get(route) -> dict`, `post(route, body:
  dict) -> dict`, `delete(route) -> dict`, `stream(route, body: dict | None = None) ->
  Iterator[dict]` (a `POST` when a body is given, for `/v1/unload`). `GateUnavailable` —
  no socket, a refused connection, or no answer within `timeout_s` (a socket systemd holds while
  the gate is down); `GateForbidden` — `EACCES` on connect, its text saying which group may;
  `GateRefused(status: int, body: dict)` — any other non-2xx answer. `http.client` with an
  `AF_UNIX` connect; nothing else imported.
  *(Added 2026-10-08, at Task 10 and the controller's rulings:)*
  - `GateError` is the three's base, and is raised itself for a 2xx answer, or a stream's line,
    that isn't a JSON object. ~~`timeout_s` may be None, for no timeout, as `spark unload`'s stream
    needs (Task 30).~~ *(Corrected 2026-10-08, the controller's ruling, at Task 10's review: None
    left the connection and the answer's head unbounded too, so `spark unload` would wait for ever,
    silently, on a socket systemd holds while the gate can't start.)* `timeout_s` always bounds the
    connection and the answer's head. `stream(route, body=None, *, then_s=...)` takes its own
    `then_s` for the reads after the head: `timeout_s` by default, and None waits as long as the
    gate streams. It returns a `GateStream`, whose `close()`, or the end of a `with` block, closes
    the connection, even before its first line.
  - Each error's text is one sentence. It says the socket's state and, where there is one, the
    next step, in Task 6's words. Root and `spark-admin`'s members get Dan's words (*On the Spark,
    `make doctor` shows what's wrong.*), and any other login gets `agent`'s (*`make doctor` on the
    Spark shows Dan what's wrong.*).
  - The text never holds what was sent, nor what the gate answered beyond its `message`. That
    `message` is shown only when it is one printable line of at most 2,000 characters, and a
    `GateRefused`'s repr shows only its status.
  - `cli.main` catches a `GateError` as it catches a `ValueError`: one line on stderr, exit 1,
    never a traceback.
  - `paths`' three without a named variable read `SPARK_WHISPER_TMP`, `SPARK_VALUES` and
    `SPARK_FRONT_URL`.
  - *(Added 2026-10-08, the controller's rulings, at Task 10's review:)*
    - A route holding a space, a control character or a character outside ASCII raises
      `GateInputError`, a `ValueError` too, worded as the name given at fault, before any
      connection.
    - The body is made before any connection, so one that can't be sent leaves none open.
    - A single answer over 1 MiB, or a stream's line over 64 KiB, ~~reads as `GateUnavailable`~~
      is a plain `GateError` *(corrected 2026-10-08, at Task 10's re-review, N5: the note below)*.
    - A 2xx answer with nothing in it reads as `{}`.
    - `GateForbidden` names the next step, by whether this account is in the socket's group (the
      group database) and whether this login has the group yet:
      - a member whose login began before it joined: log in again;
      - a member still refused: the socket's permissions are wrong, and `make doctor`;
      - anyone else, on the control socket: *that is Dan's command, which runs as Dan, not as
        agent*;
      - anyone else, on the status socket: *only spark-users' members can use it, and Dan decides
        who they are* (added at Task 10's re-review).
  - *(Added 2026-10-08, the controller's rulings, at Task 10's re-review:)*
    - An answer that stops short of its `Content-Length`, as from a gate that dies after its head,
      is `GateUnavailable` (*… its answer broke off*), never a success, an empty `{}` or a refusal.
      So is a stream cut off partway, its last chunk missing or its length short: `GateStream`
      reads with `read1`, which `http.client` doesn't let take a cut for an end, as its `readline`
      does.
    - An answer or a line past its bound is a plain `GateError`, not `GateUnavailable`: the gate
      is up and answering, so Task 23's direct release of the brake's hold never fires for it.
  - *(Added 2026-10-08, the controller's rulings, at Task 10's second re-review:)* an answer `http.client` can't parse (a status line that isn't
    HTTP, a header or chunk-size line too long) reads *… its answer wasn't one the client could
    read*, never an exception's class name.

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
  from their sets; the `front` routes are exactly the six the table gives, all on `status`.
- In `test_tested_against.py`, the import check now includes `spark.gateclient`.
- *(Added 2026-10-08, at Task 10 and the controller's rulings:)*
  - In `test_gateclient.py`: `test_a_socket_nobody_listens_on_reads_as_gate_unavailable`,
    `test_no_gate_error_carries_a_credential_or_a_response_body`,
    `test_the_next_step_is_dans_only_for_a_login_that_can_take_it`,
    `test_the_status_view_carries_the_plans_fields`,
    ~~`test_a_pin_with_an_end_and_one_without_read_as_spark_pin_words_them`~~
    `test_a_models_pin_round_trips_with_an_end_or_without` (renamed at Task 10's review: it now
    checks `ModelView`'s types and its JSON round trip) and
    `test_the_gates_paths_default_to_the_plans_and_each_variable_overrides_it` (since the review, it
    drops `SPARK_*` from the environment it reads the defaults in).
  - *(Added at Task 10's review:)*
    `test_a_gate_that_accepts_and_never_answers_is_unavailable_even_when_its_stream_may_wait`,
    `test_a_stream_waits_out_a_quiet_gate_only_when_told_to`,
    `test_closing_a_stream_closes_its_connection_even_before_its_first_line`,
    `test_an_empty_2xx_answer_reads_as_an_empty_object`,
    `test_a_route_that_cant_be_sent_is_refused_as_the_callers_before_connecting`,
    `test_a_body_that_cant_be_sent_is_refused_before_connecting` and
    ~~`test_an_answer_or_a_line_past_its_bound_reads_as_gate_unavailable`~~
    `test_an_answer_or_a_line_past_its_bound_is_a_gate_error_not_a_gate_thats_down` (renamed at the
    re-review, N5); and `test_a_closed_socket_says_who_may_use_it` checks each case's next step.
  - *(Added at Task 10's re-review:)*
    `test_an_answer_that_stops_short_of_its_length_reads_as_gate_unavailable` (a head alone, a whole
    object short of its length, half a body, half a refusal) and
    `test_a_stream_cut_off_partway_reads_as_gate_unavailable_not_its_end` (no last chunk, a chunk cut
    short, a length cut short); the routes test pins `MakeRoomProgress`'s and `LoadProgress`'s
    keys, and since the fix for the pin's ruling `PinConfirmation`'s.
  - *(Added 2026-10-08, the controller's rulings, at Task 10's second re-review:)*
    `test_an_answer_the_client_cant_read_is_worded_never_named`; the routes test pins
    `WaitProgress`'s keys and `LoadConfirmation.seconds`' type; the bounds test's over-long line is
    exactly one byte past the bound, so an off-by-one fails it.
  - In `test_cli.py`: `test_a_gate_error_is_one_plain_line_never_a_traceback`, for each of the
    three errors.
  - In `test_doctor.py`: the stand-in's address follows `paths.LLAMASWAP_URL`.

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

### Task 11 [Spark]: tickets, and spark launch starts nothing without one

**Files:**

- Create: `spark/src/spark/tickets.py`, `spark/tests/test_tickets.py`
- Modify: `spark/src/spark/launch.py`, `spark/src/spark/admission.py` (its docstring: the
  zero-wait backstop), `spark/tests/test_launch.py`; and what reads Phase 1's single refusal
  record: `spark/src/spark/status.py` (its `refused` line reads the newest `refusals/<model>.json`
  under `paths.LAUNCH` until Task 29 rewrites it), `spark/tests/test_status.py` (its two refusal
  fixtures written the new way) and `spark/tests/test_cli.py` (its `main_launch` test reads
  `read_refusal(launch, model)`)
- *(Added 2026-10-08, at Task 11's review, the controller's ruling:)* `spark/src/spark/doctor.py`,
  its docstring only: its sentence on "the last refusal record" became untrue with this task's
  per-model records. The rest of doctor stays Phase 1's until Task 33 rewrites it.

**Interfaces:**

- `tickets.issue(folder: Path, model: str, deadline: float, *, now: float, footprint_gib: float,
  available_gib: float, boot_id: str) -> str` — `<folder>/tickets/<model>.json`, `{model, id,
  issued_at, deadline, nonce, boot_id, footprint_gib, available_gib}` (the admitted footprint,
  which rule 9 admitted, as plan.md's rule 5 says, never the cold load; and `MemAvailable` at
  admission), written whole, mode `0600`; it returns the ticket's `id`.
- `tickets.claim(folder: Path, model: str, now: float) -> Claim(ok: bool, code: str | None, why:
  str, ticket: dict | None)` — renames the ticket to a name of its own before reading it, so only
  one claim of a ticket can win; codes `no_ticket` (none, damaged, or another model's) and
  `ticket_expired` (`deadline < now`). The ticket leaves `tickets/` whatever the outcome.
  *(Corrected 2026-10-08, at Task 11's review, the controller's ruling:)* `tickets.claim(folder:
  Path, model: str, now: float, *, boot_id: str)`. `no_ticket` covers none; damaged (not JSON, not
  an object, a field missing or of the wrong type, a number that isn't finite or is an int too large
  for a float, over `RECORD_MAX_BYTES`, nested too deep); not a regular file (a link, a FIFO, a
  folder: read without following a link or waiting on a FIFO); owned by an account other than
  launch's (the gate and launch both run as `spark`); or another model's. `ticket_expired` covers
  `deadline < now` **or another boot's `boot_id`**, since a boot can set the wall clock back. A
  claim first sweeps up the claimed copies (`.<model>.json.claim-<pid>-<hex>`) whose launch is no
  longer running, killed between its rename and its removal, never one whose launch still runs.
- `tickets.mark_started(folder, ticket, now)` — launch, just before its exec, writes the claimed
  ticket with `started_at` and `pid` (launch's own, which the engine keeps across the exec) to
  `<folder>/started/<model>.json`, which the brake reads as a load in progress while the gate's
  record is stale (Task 23), and the gate reads for the bypass check (Task 15)
  *(corrected 2026-10-08, at Task 11's review, the controller's ruling: the brake reads it always,
  in the union with the gate's record, bounded as Task 23 says; the gate counts it for the bypass
  check only as Task 18 bounds it; an engine can write `started/`, so neither trusts a record
  whole)*; `tickets.started(folder, *, now, boot_id) -> list[dict]` — only records of this boot,
  started within `STARTED_EXPIRES_S` (360, a literal here: twice the 180 s `healthCheckTimeout`,
  which Task 12's test ties to `render.HEALTH_CHECK_TIMEOUT_S`), so a stale record can't weaken the
  brake's watch; `tickets.clear_started(folder, model)`. A record is cleared on every outcome:
  launch clears it when its exec fails, and a refused start writes none; the gate clears it when the
  load is ready, has failed, or, after `UNKNOWN`, once `/running` shows it ready or gone (Task 15).
  *(Added 2026-10-08, at Task 11's review, the controller's ruling:)* `started()` passes over a
  record that isn't a whole one of `spark`'s under its own model's name, and one dated after `now`;
  it raises `OSError` only for a folder it can't read, and `clear_started` for any failure but a
  record already gone. Their callers (Tasks 15, 18 and 23) catch `OSError`: the brake reads no
  record as no load in progress, the stricter way; a record left uncleared stops counting 360 s
  after its start.
- `tickets.withdraw(folder: Path, model: str) -> bool` — the gate removes one it issued that wasn't
  used.
- `launch.main_launch(argv, *, registry: Path = paths.REGISTRY, state: Path = paths.STATE, launch:
  Path = paths.LAUNCH, hf_home: str = render.HF_HOME, clock: Callable[[], float] = time.time) ->
  int` — in this order: the registry loads (else refused `registry`); the model is known (else exit
  2); its ticket is claimed; the brake's hold (`held_by_brake`); Phase 1's zero-wait fit (`no_fit`);
  every file the model's `source` names exists under `hf_home` (`not_downloaded`, its reason naming
  `make pull`); its refusal record cleared; `tickets.mark_started`; its `oom_score_adj`; exec, with
  every `LLAMASWAP_KEY_*` variable dropped. A refusal exits 3, prints `spark: not starting <model>:
  <reason>` on stderr and records it. Never a sleep or a retry. *(Corrected 2026-10-08, at Task 11's
  review, the controller's ruling:)* first, run as root, it exits 3 with one stderr line, before
  anything is read or written in launch's folder, and records nothing (root never writes through a
  path `spark` controls; `tickets.write_whole` refuses root too); after `tickets.mark_started`, a
  record that can't be written is refused `start_unrecorded`, since a start neither the brake nor
  the gate could see doesn't happen; at the exec, an exec that raises clears the `started/` record
  and is refused `exec_failed`. A refusal records it (all but the root refusal). The codes launch
  records are eight: `registry`, `no_ticket`, `ticket_expired`, `held_by_brake`, `no_fit`,
  `not_downloaded`, `start_unrecorded` and `exec_failed`; `held_by_brake` and `no_fit` keep Phase
  1's words.
- `launch.record_refusal(launch: Path, model, code, reason)`, `read_refusal(launch, model) -> dict
  | None`, `clear_refusal(launch, model)` — `<launch>/refusals/<model>.json`, `{at, model, code,
  reason}`, written whole. Phase 1's single `last-refusal.json` goes.
- *(Added 2026-10-08, at Task 11's review, the controller's ruling:)* the helpers later tasks reuse
  rather than re-implement: in `tickets`, `write_whole(path, data)` (whole, `0600`, root refused),
  `read_record(path, *, own)` (no link, no waiting, a regular file of at most `RECORD_MAX_BYTES`,
  with `own` one this account owns), `record_path(folder, kind, model)` (a name that isn't a model's
  refused), `RECORD_MAX_BYTES`, `NO_TICKET_WHY`, `TICKETS` and `STARTED`; in `launch`, `REFUSALS`,
  `check_refusal(launch, model)` (Phase 1's, now per model: the record, or what's wrong with it) and
  `newest_refusal(launch)`, status's interim reader, which Task 29 deletes with status's interim
  docstring note.

**Tests** (`spark/tests/test_launch.py` and `spark/tests/test_tickets.py`; `execvpe` replaced):

- `test_launch_refuses_without_a_ticket_and_records_why` — the fixture registry, room to fit, no
  hold, no ticket → exit 3; stderr `spark: not starting coder: no admission ticket from the gate`;
  `refusals/coder.json` with code `no_ticket`; no exec.
- `test_a_ticket_is_used_up_by_one_start` — `issue(…, "coder", deadline=now+60, …)` → exec
  called, the ticket gone from `tickets/` and kept under `started/` with its footprint, its
  `MemAvailable`, its `started_at` and launch's pid; a second launch → `no_ticket`.
- `test_a_refused_start_leaves_nothing_started` — a ticket and a hold → `held_by_brake`, and
  `started/` empty.
- `test_clear_started_removes_the_record` — `mark_started` then `clear_started` → `started()` is
  `[]`.
- `test_a_failed_exec_clears_its_started_record` — `execvpe` raising `OSError` → `started/` empty,
  exit 3.
- `test_started_ignores_old_and_other_boots_records` — a record 361 s old, and one with another
  `boot_id` → neither returned; one 10 s old of this boot → returned, with its `pid` and the
  ticket's `footprint_gib`.
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
  spark/tests/test_tickets.py spark/tests/test_launch.py spark/src/spark/status.py \
  spark/tests/test_status.py spark/tests/test_cli.py spark/src/spark/doctor.py
git commit -m "feat(spark): 🤖 spark launch starts a model only with the gate's ticket" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

*(Corrected 2026-10-08, at Task 11's review, the controller's ruling: the `git add` line gains
`spark/src/spark/doctor.py`, as the commit `e0b1daf` staged it.)*

***

### Task 12 [Spark]: the gate's async llama-swap client, and a v257 stand-in for the tests

**Files:**

- Create: `spark/src/spark/llamaswap_async.py`, `spark/tests/fake_llamaswap.py`,
  `spark/tests/test_llamaswap_async.py`
- Modify: `spark/src/spark/render.py` (`HEALTH_CHECK_TIMEOUT_S = 180`, used by the rendered config,
  so the gate's load timeout has its constant before Task 19), `spark/tests/test_render.py`
- *(Added 2026-10-08, at Task 12's review, the controller's ruling:)* Modify:
  `spark/src/spark/gateproto.py` (`UNLOAD_CALL_TIMEOUT_S`), `spark/tests/test_tested_against.py`
  (its set names `spark.llamaswap_async` and `fake_llamaswap`, and its guard on the stand-in's
  existing goes, so a stand-in moved or renamed fails there rather than leaving the check).
  *(Added 2026-10-08, at Task 12's re-review, the controller's ruling:)* `gateproto.py` gains
  `STOPPING_EVERY_S` and `StoppingProgress` (Task 16's corrected drain), and
  `spark/tests/test_gateclient.py` checks the shape with the other progress lines.

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
  {"llama-swap": "v257"}`; each behaviour cites, in a comment, the line of llama-swap v257's own
  source (at tag `v257`, commit `f00d375`) it copies. The session's notes,
  `.superpowers/sdd/phase-1/llamaswap-v257-gate-research.md` §4–§8, point to those lines, but the
  file is git-ignored, so a comment in the public repo cites llama-swap's source, never the notes.

**Tests** (`spark/tests/test_llamaswap_async.py`, against `serve_fake`):

- `test_running_reads_v257s_shape` — Gemma ready, the coder starting →
  `[Running("gemma", "ready"), Running("coder", "starting")]`; a body without `running` →
  `LlamaSwapAnswered`.
- In `test_render.py`: `test_health_check_timeout_is_180` — the rendered config's
  `healthCheckTimeout` is `render.HEALTH_CHECK_TIMEOUT_S`, 180, and `tickets.STARTED_EXPIRES_S` is
  twice it (Task 11's literal 360).
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

*(Amended 2026-10-08, at Task 12's review and its fix round 1, the controller's rulings. The
interfaces and tests above stand, as these change them:)*

- **`AsyncLlamaSwap(base_url: str, key: str, *, load_timeout_s: float, call_timeout_s: float =
  gateproto.LLAMA_SWAP_HUNG_S)`.**
  - `call_timeout_s` bounds every call's connect, write and pool wait, `running()` as a whole, and
    how long `last_lines` waits for its head.
  - `load_timeout_s` bounds `load()` as a whole, its read included. `unload()` has a bound of its
    own, `gateproto.UNLOAD_CALL_TIMEOUT_S`, 60 s (*Global Constraints*).
  - A key that is empty or isn't printable ASCII is refused before anything is sent, and the error
    doesn't name it.
  - `aclose()` and `async with`. The gate closes the client when it shuts down (Task 19).
- **`load()`.**
  - `failed(status, text)` covers every answer but a 200: a 500 from a start that failed or was
    aborted, and also llama-swap's own refusals (3xx, 401, 404, 429).
  - Its text is the body decoded with `backslashreplace`, every non-printable character escaped,
    then its head, at most `FAILED_TEXT_MAX` (2048) characters, the last of them `…` when it was cut.
  - ~~`LoadOutcome.UNKNOWN`~~ `LoadOutcome.unknown(why)` comes from any timeout or error of httpx's,
    a call never sent included, and from an answer over 1 MiB. `why` is one of `timeout`,
    `refused`, `dropped`, `too big` and `unreadable`, for the gate's journal. Elsewhere in this plan,
    *`UNKNOWN`* means an outcome of that kind.
  - `load()` raises neither of Phase 1's errors. The other calls turn every error of httpx's into
    one of them, raised from None: an answer they can't decode is `LlamaSwapAnswered`.
  - *(Added 2026-10-08, at Task 12's re-review, the controller's ruling:)* `LlamaSwapNotSent`, a
    `LlamaSwapUnreachable`, for a call that never had a connection (httpx's `ConnectError`,
    `ConnectTimeout` or `PoolTimeout`), so nothing was sent. Task 16's drain needs it: an unload
    never sent leaves the model loaded, and one sent is taken to the end by v257.
- **The stand-in's controller** gains:
  - `script_stop(model, delay_s)`;
  - `canned(method, path, status, body, headers)`, a test's hook that answers before any key is
    checked, and is never v257's behaviour;
  - `reserved(model)` and `tails(model)`;
  - `close()`, `serve_fake`'s end: v257's shutdown without its 30 s drain, every engine stopped;
  - `restart(down_s)`: llama-swap restarted on the same port, nothing listening for `down_s`, then
    serving again with every engine stopped and its logs empty, as v257 starts (Tasks 17, 18 and
    32 need it);
  - `stream(model, chunks, delay_s, drop_after=n)`: the connection dropped after `n` chunks, the
    response never finished, a test's hook for a llama-swap that dies mid-stream (Task 21).
- **The stand-in's v257 behaviours** gain:
  - `/running` sorted by model (`internal/server/api.go:363`);
  - the deadline's text in Go's duration, `health check timed out after 3m0s`;
  - a stream whose engine an unload kills ends cleanly, only early, with no `[DONE]`
    (`internal/process/process_command.go:492-507`).

  Its docstring lists what it leaves out, among them the stall of every model's requests while a
  stop runs (`internal/router/base.go:113-133`, 498-505), and the end of a ready engine's streams at
  SIGTERM rather than at the stop's end.
- **Tests:**
  - `test_running_reads_v257s_shape`: `gemma-4-26b-a4b` ready, then `qwen3.6-35b-a3b` starting, in
    that order. v257 sorts the list, so "gemma" and "coder" would come back with the coder first.
  - `test_load_waits_past_every_other_timeout`: `call_timeout_s` is 0.5.
  - `test_no_redirect_is_followed_with_the_key`: `running`, `unload` and `last_lines` raise
    `LlamaSwapAnswered`, and `load` returns `failed(302, "")`.
  - `test_a_failed_start_reads_as_failed_with_its_text` checks the 2 KiB cap too.
  - Added: `test_the_key_goes_only_in_its_header`;
    `test_a_call_nothing_answers_is_unreachable_and_a_load_is_unknown` (`refused`, and `dropped`
    for a call dropped unanswered); `test_unload_waits_its_own_bound` (a timeout leaves the model
    `stopping` in `/running`); `test_a_load_whose_answer_cant_be_read_is_unknown` and
    `test_an_answer_it_cant_read_is_answered_never_httpxs_own` (a body its `Content-Encoding`
    doesn't fit, and one over 1 MiB); `test_a_restart_serves_the_same_port_with_every_engine_stopped`;
    `test_the_stand_in_can_drop_a_stream_mid_way`.
  - *(Added 2026-10-08, at Task 12's re-review:)* `test_unload_waits_its_own_bound` also checks
    `gateproto.DRAIN_GRACE_S > gateproto.UNLOAD_CALL_TIMEOUT_S`, and that a timed-out unload isn't
    `LlamaSwapNotSent`; `test_a_call_nothing_answers_is_unreachable_and_a_load_is_unknown` checks
    that a refused call is.

**Steps:**

- [ ] **Step 1:** the failing tests; run them: they fail (the modules missing).
- [ ] **Step 2:** the client and the stand-in; the tests pass; `make test lint`.
- [ ] **Step 3: Commit.** **On the Spark:** *(Amended 2026-10-08, at Task 12's review: fix round 1
  committed `spark/src/spark/gateproto.py` and `spark/tests/test_tested_against.py` with these,
  under its own message, `fix(spark): 🤖 …`; fix round 2, at the re-review,
  `spark/tests/test_gateclient.py` too.)*

```bash
git add spark/src/spark/llamaswap_async.py spark/tests/fake_llamaswap.py spark/tests/test_llamaswap_async.py \
  spark/src/spark/render.py spark/tests/test_render.py spark/src/spark/gateproto.py \
  spark/tests/test_tested_against.py spark/tests/test_gateclient.py
git commit -m "feat(spark): 🤖 the gate's async llama-swap client, tested against a v257 stand-in" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 13 [Spark]: the gate's state, kept across restarts

**Files:**

- Modify: `spark/src/spark/hold.py` (the hold's new fields, which the brake writes in Task 23 and
  the gate reads from Task 17)
- Create: `spark/src/spark/brakeevents.py`, `spark/tests/test_brakeevents.py`,
  `spark/src/spark/gate/__init__.py`, `spark/src/spark/gate/state.py`,
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
  room_hold: RoomHold | None, brake_marks: dict[str, BrakeMark], last_auto_release_at: float | None,
  brake_events_after: tuple[str, int] | None, notified: dict[str, float], notify_failing_since:
  float | None, refusals: deque[RefusalRecord] (maxlen REFUSAL_HISTORY), applying: ApplyHold | None,
  ticketed: dict[str, Ticketed], clean_shutdown: bool, saved_at: float, boot_id: str)`;
  `ApplyHold(since: float, by_uid: int, renewed_at: float, begun_at: float | None, restarting:
  list[str], ended: bool)` — apply's hold, saved like the rest, so a gate restarted inside an apply
  still holds; `ended` makes a second end do nothing; `Ticketed(model:
  str, pid: int, ticket_id: str, at: float)` — each engine a ticket started this boot.
  *(Added 2026-10-08, at Task 11's review, the controller's ruling: `GateState` also keeps `issued:
  dict[str, str]`, each model's ticket id for the load the gate issued it for this boot, saved
  before the load call (Task 15) and dropped once its `started/` record has moved into `ticketed` or
  the load has failed or gone; `restore` drops another boot's. Task 18's bypass check counts a
  `started/` record as ticketed only when its `id` is there, since an engine can write `started/`.)*
- `STATE_FILE = "state.json"`; `load_state(folder: Path) -> tuple[GateState, str | None]` — a
  missing file gives a fresh state and None; a damaged one, a fresh state and a problem naming the
  file (for `spark status`); never raises. `save_state(folder: Path, state: GateState) -> None` —
  written whole, the file and its folder fsynced, as `hold.write_hold` does.
- `restore(state, running: list[Running], *, now: float, boot_id: str, registry) -> tuple[GateState,
  RoomHold | None]` — a model `/running` shows starting is `starting`, at the registry's footprint
  if it wasn't recorded, and takes Task 15's ready-or-gone path, so its `started/` record moves into
  `ticketed` once it is ready; every loaded model's `last_use` is `now`; a model `/running` doesn't
  list is dropped; a room hold from another boot ends, and is returned so the caller sends
  `room_hold_ended`; an apply hold and `ticketed` from another boot end too; pins, sessions, marks
  and refusals stay. `notified` (~~an event key~~ a type and an event key, and when it was sent)
  drops keys older than `NOTIFIED_KEEP_S` (86,400, this plan's value) on every save, so it never
  grows for the life of the box. *(Corrected 2026-10-07, after Task 7's fix round, the controller's
  ruling: keyed by type and event key together; Task 14 has why.)* *(Added 2026-10-08, at Task
  12's re-review, the controller's ruling:)* a model `/running` shows `stopping` (a drain's unload
  under way when the gate restarted) stays counted as `stopping`, its memory not freed, until
  `/running` shows it gone, and the gate counts that unload as its own. Its leaving is never read
  as a crash, so the residents' rule doesn't reload it.
- `Emit` — the protocol every gate module notifies through: `emit(type: str, event_key: str,
  **fields) -> None`.
- The hold (`hold.py`) gains `boot_id: str | None`, `episode: int | None`, `loading: str | None`
  (the model that was starting when the brake fired) and `available_gib: float | None` (what was
  available when it fired, for `held_by_brake`'s words); its `since` is local time, as Phase 1
  wrote it. `read_hold` reads Phase 1's holds, the four then None, and a hold with no `boot_id`
  counts as another boot's. `hold.release_waits_for_dan(hold, *, boot_id, last_auto_release_at,
  now) -> bool` — true for a hold from another boot, or one that fired within
  `AUTO_RELEASE_EVERY_S` of the last automatic release: the one rule Task 15's words and Task
  17's release both use.
- `brakeevents.BrakeEvent(seq: int, at: float, boot_id: str, episode: int, kind: "fired" | "unload"
  | "warn", model: str | None, state: str | None, available_gib: float, line_gib: float,
  sent_by_brake: bool)` — `line_gib` the brake line the writer acted on, so the drill's raised line
  is worded as itself; `append_event(path, event)` — one JSON line, fsynced, its `seq` taken from
  the file under `fcntl.flock`, so two writers (the brake and the S05 drill's `--once`, Task 25)
  never share one; `episode_for(path, hold, *, boot_id) -> int`, under the same lock — the standing
  hold's episode while a hold of this boot stands, otherwise the last episode in the file for this
  boot plus one (1 for none), so each drill run after a release is an episode of its own;
  `next_seq(path) -> int` — one past the file's last line, 1 for a missing file; `read_events(path,
  after: tuple[str, int] | None) -> list[BrakeEvent]` — the events after `after`, keyed on (boot id,
  seq), so a file that was removed or started again never hides the events written after it
  (`brake/events.jsonl`). It reads without the lock, so it skips a last line with no newline (a
  write in progress), and never returns or advances past it.

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
- `test_a_hold_carries_its_boot_episode_and_loading_model` — written and read back whole.
- `test_a_phase_1_hold_reads_with_none_and_counts_as_another_boots` — a `hold.json` without the new
  fields → a `Hold` with the four None.
- `test_release_waits_for_dan_after_a_reboot_or_a_second_brake_within_the_hour` — another boot's
  hold → true; this boot's, fired 20 minutes after an automatic release → true; 61 minutes after →
  false.
- `test_old_notified_keys_are_pruned` — keys sent 2 days and 1 hour ago, saved → only the second
  kept.
- `test_an_apply_hold_survives_a_restart_but_not_a_reboot` — a state with `applying` saved and
  loaded on the same boot → still `applying`, its `restarting` list whole; restored on another
  boot → None, and `ticketed` empty.
- In `test_brakeevents.py`: `test_events_append_whole_lines_and_continue_their_seq` — three appended
  → seq 1, 2, 3; `next_seq` 4; `test_read_events_after_a_point` — after `(b, 2)` → the third only;
  `test_a_file_started_again_still_reads` — the file removed and one event appended with seq 1 on a
  new boot `b2` → read after `(b1, 3)` gives it; `test_two_writers_never_share_a_seq` — two
  processes appending 50 events each to one file → 100 lines, seq 1 to 100, each once;
  `test_the_episode_is_the_holds_or_the_next` — a hold of this boot in episode 2 → 2; no hold, the
  file's last episode 2 → 3; a hold from another boot → the file's next;
  `test_a_half_written_last_line_is_left_for_next_time` — two whole lines and a third without its
  newline → two events; the third completed → read after the second gives it.

**Steps:**

- [ ] **Step 1:** the failing tests; run them: they fail (`spark.gate` missing).
- [ ] **Step 2:** `gate/state.py`, `brakeevents.py`, the hold's fields; the tests pass; `make test
  lint`.
- [ ] **Step 3: Commit.** **On the Spark:**

```bash
git add spark/src/spark/gate/__init__.py spark/src/spark/gate/state.py spark/tests/test_gate_state.py \
  spark/src/spark/hold.py spark/src/spark/brakeevents.py spark/tests/test_brakeevents.py
git commit -m "feat(spark): 🤖 the gate's state, kept across restarts" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 14 [Spark]: the gate's notifications

**Files:**

- Create: `spark/src/spark/gate/notify.py`, `spark/src/spark/gate/units.py`,
  `spark/tests/test_gate_notify.py`, `spark/tests/test_gate_units.py`

**Interfaces:**

- `Notifier(publish: Callable[[Notification], Awaitable[None]], registry, state: GateState,
  clock)` implements `Emit`. `emit(type, event_key, **fields)` builds the text with
  `messages.notification` and returns at once; a type the registry has `off` is dropped; an
  ~~`event_key` already in `state.notified`~~ event already in `state.notified`, which is keyed by
  the type and the event key together (`<type> <event_key>`), is dropped, so a restart never
  repeats one and two types never share a key *(corrected 2026-10-07, after Task 7's fix round,
  the controller's ruling: keyed by the event key alone, `loaded` would have been dropped as a
  repeat of `load_started`, which sends `load:<ticket id>` for the same load, and every type's key
  would have needed a prefix of its own to stay apart)*. A queue,
  served by a task of its own, publishes each, bounded by `NTFY_TIMEOUT_S`; a failure sets
  `state.notify_failing_since` to the first failure's time, and the next success clears it. Every
  type that carries a refusal (`refused`, `footprint_suspect`, `load_failed`) collapses: per
  (model, key, code), the first goes at once, the repeats within `BURST_WINDOW_S` are counted and go
  as one when it closes (*Didn't load the coder for agent 4 more times since 09:12: same reason.*);
  a different code goes at once. *(Added 2026-10-07, at Task 7, the controller's ruling: the burst
  goes on the refusal's own type, with `model_label`, `key_label`, `count`, `since` and `code`, so
  it keeps that type's priority and its `off`; a refusal that never got as far as a load
  (`FRONT_CODES`) reads *Refused … 4 more times …*.)* *(Corrected 2026-10-07, at Task 7's review,
  the controller's ruling: a front refusal's burst passes as `model_label` the model it named, the
  registry's label for a model the gate knows, else, for `model_not_found`, the name the client
  asked for, which the words show only when it reads as a model's name, and nothing for
  `route_not_served`. `load_failed` bursts per model, not per key.)*
- The event keys, each built from the event's own identity, so one model loading twice sends two
  `loaded` and a restart never repeats one: `load_started` and `loaded`, `load:<ticket id>`;
  `waiting`, `wait:<request id>`; a refusal's three types, `refusal:<request id>` *(corrected
  2026-10-07, at Task 7's review, the controller's ruling: but `load_failed`, `load-failed:<ticket
  id>`, once per failed load, both from `messages.refusal_notification`; a prefix of its own~~, since
  `notified` is keyed by the event key alone and `load_started` has sent `load:<ticket id>` for that
  load~~; `loaded` shares `load:<ticket id>` with `load_started` the same way, which this task
  settles, with a prefix per type or `notified` keyed by type and key; *settled the same day*:
  `notified` is keyed by type and key, above)*; `unloaded`,
  `drain:<drain id>`; `brake_fired`, `brake:<boot id>:<episode>:<seq>`; `brake_needs_release`,
  `brake-needs:<boot id>:<episode>`; `brake_released`, `release:<boot id>:<episode>`;
  `room_hold_ended`, `room:<created_at>`; `resident_waiting`, `resident:<model>:<wait's start>`;
  `pin_ended`, `pin:<model>:<until>`; `apply_restarted`, `apply:<since>`; `memory_warning`,
  `warn:<boot id>:<time of the fall>`; `back_up`, `back:<unit>:<inactive_since>`;
  `llama_swap_down`, `llama-swap:<outage's start>`.
- `NtfyPublisher(url: str, topic: str, header: tuple[str, str])` — `POST <url>/<topic>`, the text
  as the body, `Priority: high | default | low`, the token header from `read_header_credential(
  "ntfy-token")`, httpx with `trust_env=False`; it never logs the URL, the topic or the header.
- `ingest_brake_events(path: Path, state, registry) -> list[tuple[str, str, dict]]` — Task 13's
  `read_events(path, state.brake_events_after)`: an episode's first unload becomes
  `brake_fired` (what was unloaded, by the registry's labels, and in what state, worded with the
  event's own `line_gib`), each later unload in it a short
  `brake_fired` follow-up; a line with `sent_by_brake: true` is skipped, the brake having sent it;
  an unload of a model that was `starting` becomes a `BrakeMark` with what it was seen using;
  `brake_events_after` advances. *(Added 2026-10-07, at Task 7, the controller's ruling: each
  unload's `state` is `starting`, `idle` or `answering`, or None when the gate's record was missing,
  and `back_up`'s `unit` is `gate`, `front`, `llama-swap` or `brake`.)* *(Added 2026-10-07, at
  Task 7, the controller's ruling on rule 5: an episode's `brake_fired` carries
  `release_waits_for_dan`, Task 13's `hold.release_waits_for_dan` for its hold with
  `state.last_auto_release_at`, and that time as `released_at`, so a brake within the hour after
  an automatic release says in that one alert why new loads stay paused until Dan releases them; no
  `brake_needs_release` follows it.)*
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
  (a crash is the notifier's to report), and never while apply's hold stands (a restart it
  expects); re-armed once it answers again.

**Tests** (injected clock; a recording `publish`):

`spark/tests/test_gate_notify.py`:

- `test_every_type_goes_at_its_registry_priority` — each type the gate sends, emitted once: each
  publish carries the registry's priority.
- `test_an_off_type_sends_nothing` — `loaded: off` → no publish.
- `test_one_notification_per_event_even_across_a_restart` — `brake_released` with event key
  `release:3`, emitted twice → one publish; a new `Notifier` over the saved state, emitting it
  again → none.
- `test_a_burst_of_identical_refusals_collapses_with_a_count` — the `footprint_suspect` type for
  the coder and `agent`, code `footprint_suspect`, at 09:12 → published at once; four more by 09:20
  → nothing; at 09:22 one publish, *Didn't load the coder for agent 4 more times since 09:12: same
  reason.*; a `refused` with `no_fit` for the same pair at 09:15 → published at once; a burst of
  `load_failed` collapses the same way.
- `test_one_model_loading_twice_sends_two_loaded` — `loaded` for the coder with tickets `t1` and
  `t2` → two publishes; `t1` again → none.
- `test_two_types_never_share_a_key` — `load_started` then `loaded`, both with `load:t1` → two
  publishes; each again → none. *(Added 2026-10-07, after Task 7's fix round: it fails if
  `notified` drops the type.)*
- `test_one_failed_load_sends_one_alert` — two requests joined to one start that fails, each
  emitting `load_failed` with `load-failed:t1` inside one open burst window → one publish, and the
  burst counts the load once, not per request. *(Added 2026-10-07, after Task 7's re-review.)*
- `test_an_ntfy_out_of_reach_stalls_nothing_and_shows_failing_since` — a `publish` that hangs:
  `emit` returns within 10 ms; after the (injected) timeout, `notify_failing_since` holds the
  first failure's time; a later success clears it.
- `test_the_token_travels_only_in_a_header` — `NtfyPublisher` against a recording stand-in: the
  request's path is `/<topic>`, the token only in `Authorization`; the captured log holds neither.
- `test_brake_events_become_brake_fired_and_its_follow_ups` — events: fired at 03:12 at 19.6;
  unload of the coder while `starting`; at 03:13 unloads of Gemma and the embeddings, `idle` →
  two notifications whose texts are Task 7's `brake_fired` and its follow-up; a `BrakeMark` for the
  coder; `brake_events_after` at the last line.
- `test_what_the_brake_sent_itself_is_not_sent_again` — the same events marked `sent_by_brake` →
  no publish; `brake_events_after` advanced.
- `test_a_drill_lines_event_is_worded_with_its_own_line` — a fired event with `line_gib` 56 at
  52.3 available → *… 52.3 GiB available, under the 56 GiB line …*, not the registry's 20.
- `test_two_episodes_send_two_brake_fired` — episodes 1 and 2 of one boot, a release between → two
  `brake_fired`, neither a follow-up.
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
  unanswered again → another; unanswered with `NRestarts` risen → none (the notifier's);
  unanswered while `state.applying` stands → none.

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

### Task 15 [Spark]: the gate's admission — the queue, one load at a time, tickets, refusals

**Files:**

- Create: `spark/src/spark/gate/admission.py`, `spark/tests/test_gate_admission.py`

**Interfaces:**

- `AdmitRequest(id: str, model: str, kind: "reload" | "command" | "key", key: ClientKey | None,
  deadline: float | None)` — `reload` is the gate's own reload of a resident (Tasks 17 and 18),
  with no key and no deadline; `command` is one of Dan's own commands (`spark load`, `spark pin`),
  with no key; `key` is a request through the front, with its key and its deadline.
  `privileges(req) -> Privileges(queue, uses_hold, reloads_marked, words, names_processes)` — a
  key's group's (Task 4); Dan's commands get the `dan` group's.
- Reloads rank ahead of everything, count every hold (make-room's and the brake's), and never
  time out: a resident that doesn't fit waits in the queue, `resident_waiting` sent once per wait,
  and loads once it fits. *(Added 2026-10-07, at Task 7, the controller's ruling: `reload(model,
  after)` carries why it reloads, `hold`, `brake`, `apply`, `boot`, `restart` or `crash`, for
  `resident_waiting`'s `after`, so its words are true in each case.)*
- `Admitted(model: str, loaded_now: bool, seconds: float | None)`.
- `Admitter(registry, state, llamaswap: AsyncLlamaSwap, emit: Emit, clock, *, read_mem:
  Callable[[], MemInfo], read_hold: Callable[[], Hold | None], launch: Path, hf_home: str,
  holders: Callable[[bool], list[Holder]])`:
  - `async admit(req) -> Admitted | Refusal` — a model already `ready`: Admitted at once. One
    already starting: the request joins that load and waits for it, past its key's wait,
    bounded by the load's deadline. A model whose files aren't under `hf_home`: `not_downloaded`
    at once, before any ticket. Otherwise it queues, in this order: the gate's own reloads of the
    residents, then each request by its group's `queue`, lower first (Dan's commands at 0), first
    come first served within each; when the
    one-load slot is free, the first request in that order that fits takes it, and one that doesn't
    fit keeps its place; one load at a time; each request is rechecked whenever memory, the
    hold, a load, a drain or the queue changes; at its deadline it is refused with the code for
    what it was waiting on — `no_fit`, `loading` (its turn for the one-load slot never came),
    `held_by_brake`, `footprint_suspect`, `draining`, `restarting`, `llama_swap_down`.
  - When a request fits: `tickets.issue(launch, model, deadline=now + LOAD_CALL_TIMEOUT_S,
    footprint_gib=<its footprint, what rule 9 admitted>, available_gib=<MemAvailable now>,
    boot_id=<this boot's>)`; `waiting` (once, if it had waited) and `load_started`;
    `llamaswap.load(model)`. `READY` → `loaded`, a `ModelRecord` with the load's fall
    (`MemAvailable` before less after), capped at the model's `cold_load_gib` plus
    `LOAD_FALL_MARGIN_GIB` (2 GiB, this plan's value) when the registry has one, else at its
    footprint, and flagged when capped. ~~`UNKNOWN` → the slot stays held until `/running` shows it
    ready or gone.~~ *(Corrected 2026-10-08, at Task 12's review, the controller's ruling: the
    ticket settles what an unknown outcome means.)*
    - **`UNKNOWN`, and any `failed` whose status isn't 500.** A 3xx, 401, 404 or 429 is
      llama-swap refusing the call, not a start that ended.
      - The ticket is withdrawn first (`tickets.withdraw`); a withdrawal and launch's claim can't
        both win.
      - If it was still there, no start used it, and the slot is freed.
      - If it had been claimed, a start is under way. The slot stays held until `/running` shows
        the model ready or gone; then the `started/` record is read as for any outcome.
      - A load still `starting` past ~~its ticket's deadline~~ its own start's deadline plus
        `llamaswap_async.PAST_THE_DEADLINE_S` (20 s) is unloaded, which aborts the start
        (`internal/process/process_command.go:384-393`). v257's health poll can hold a start past
        its deadline: the poll has no response-header timeout (`internal/config/model_config.go:181`),
        and the deadline is checked only between polls (`process_command.go:587-596`). The unload
        covers that. *(Corrected 2026-10-08, at Task 12's re-review, the controller's ruling:)*
        the start's deadline counts from the claimed ticket's `started/` record, `started_at +
        render.HEALTH_CHECK_TIMEOUT_S + PAST_THE_DEADLINE_S`, and from the ticket's deadline only
        when there is no record. v257's deadline runs from the start itself (`process_command.go:577`),
        and a start can begin late while its run loop is busy with stops. `/running` is read right
        before the unload. v257's unload has no condition, so a model that turned ready in the
        window that leaves is unloaded too, and counted as a failed load; that window is accepted.
    - **A `failed` with status 500** (the start failed, it was unloaded, or llama-swap is shutting
      down) frees the slot only once the ticket is withdrawn or the `started/` record is read.
    - **The load call runs in a task of its own** that no requester's leaving cancels: a load, once
      started, is always waited for.
    - **`load_failed` goes only to a 500.** llama-swap's own refusals are logged as such, never
      worded as a model that failed to start. *(Added 2026-10-08, at Task 12's re-review, the
      controller's ruling:)* nor every 500. One whose text holds `group: model unloaded` (an unload
      aborted the load: the brake's) or `group is shutting down` (llama-swap restarting) isn't
      `load_failed` either. Each is worded by its cause.

    On every outcome the model's `started/` record is read, then cleared
    (`tickets.clear_started`): ready, its `pid` and ticket go into `state.ticketed`, for the bypass
    check; failed, or gone, nothing is kept. *(Added 2026-10-08, at Task 11's review, the
    controller's ruling: the ticket's id goes into `state.issued` (Task 13), saved before the load
    call; a `started/` record moves into `ticketed` only when its `id` is that one, read with
    `tickets.read_record(…, own=True)`; an `OSError` reading or clearing it is caught and the load's
    outcome stands, since a record left uncleared stops counting 360 s after its start.)* A model
    that a restarted gate's `restore` found `starting` takes the same path once `/running` shows it
    ready or gone. `failed` *(a 500 only: corrected 2026-10-08, at Task 12's review, as above)* →
    `load_failed`, its text launch's refusal record for the model if there
    is one, else the engine's last line (`llamaswap.last_lines`), or the deadline's variant for
    `health check timed out`; the ticket withdrawn if it wasn't used. *(Added 2026-10-07, at Task 7,
    the controller's ruling: every refusal's notification is `REFUSAL_NOTIFICATION.get(code,
    "refused")`, sent with `code` and the refusal's own `Moment` for `refused`; `load_failed`'s with
    `command` (`Moment.model_command`: its first role, else its name); `footprint_suspect`'s with
    `key_label`; `waiting` for Dan with `command`; and `load_started` with no `key_label` for a
    reload or one of Dan's commands.)* *(Corrected 2026-10-07, at Task 7's review, the controller's
    ruling: a failed load sends one `load_failed`, keyed by its ticket
    (`messages.refusal_notification`), however many requests joined it; each of them still gets the
    `load_failed` refusal. `waiting` for the slot names the model loading; for the brake, whether
    the pause waits for Dan and, if not, `RELEASE_AFTER_S`.)*
  - *owed* — `budget.owed_gib`, each model's `held_now` its load's fall, plus its engine's
    `RssAnon` growth since the load only when `registry.gate.owed_reads_rss` is on for that
    engine's kind. *held* — the room hold, not counted for a request whose privileges have
    `uses_hold`; such a load shrinks it with `budget.hold_after_dans_load`, and one that uses it up
    ends it (`room_hold_ended`, *used up by your own loads*). A refusal's `free for a load` is
    passed to Task 6 as it is, which words ~~0 and below~~ anything that rounds down to 0 as shown
    as *nothing is free for a load* (*corrected 2026-10-07, at Task 6's fix round 1, to match Task
    5's correction: 0.5 reads so too*). *(Added 2026-10-07, at Task 6: it goes in
    `Moment.free_gib`, what `free_for_a_load` returned for this request, ceiling term included.
    `available_gib`, `reserve_gib`, `owed_gib`, `held_gib` and `hold_counted` go with it, for the
    breakdown only.)* *(Added at Task 6's fix round 1, the controller's ruling: the breakdown is the
    term that gave the figure. Where `ceiling − committed` was the smaller term of `min()`, the
    gate passes `ceiling_gib` and `committed_gib` too, and the words show that term in place of
    `MemAvailable`'s. It passes `starting_gib` whenever a model is starting. Every field a code's
    words use must be filled, or `refusal` raises a `ValueError` naming it, so this task's tests
    compare each refusal's whole text.)*
  - The brake's mark: a request whose privileges lack `reloads_marked` waits its wait for a marked
    model, then `footprint_suspect`, with its notification; one that has it loads the model if it
    fits, and the mark goes.
  - `held_by_brake`'s words take the hold's `available_gib` and `since`, and whether it waits for
    Dan from `hold.release_waits_for_dan` (Task 13). *(Added 2026-10-07, at Task 6: and
    `Moment.warn_gib`, the registry's `brake.warn_gib`, and `Moment.release_after_s`,
    `RELEASE_AFTER_S`, so the words name the line and the time the release actually uses.)*
  - `cancel(request_id)` — a client gone: out of the queue at once, and nothing loads for it; a
    load already started finishes.
  - `set_restarting(on: bool)` (on while `state.applying` stands), `set_llamaswap_up(up: bool)`,
    `changed()`. While restarting, a request's deadline gives `restarting` even with llama-swap
    down, never `llama_swap_down`, and the gate sends no `llama_swap_down`: the restart is
    expected.
  - `free_for_a_load(*, uses_hold: bool) -> Decimal`, `owed() -> Decimal`, `starting_gib() ->
    Decimal`, `waiting() -> list[dict]` (`StatusView`'s `waiting` rows); `reload(model)` — queues a
    reload, as Tasks 17 and 18 call it.
- `registry.Model` gains `cold_load_gib: float | None = None` (Task 4's module), which the soak
  records (Task 45).

**Tests** (`spark/tests/test_gate_admission.py`; an injected clock; a stand-in llama-swap whose
loads, `/running` and log lines the test scripts; memory from a list; a temporary `launch` and
`hf_home` with the models' files):

- `test_a_model_that_fits_loads_once_with_a_ticket` — residents loaded (footprints 32, 8, 3,
  holding them), 74 GiB available, the coder 41: one ticket for the coder with deadline now + 200;
  one load call; `Admitted`; a record whose fall is 74 less the reading after; the ticket's
  `footprint_gib` 41 (the footprint, even with a `cold_load_gib` of 33 in the registry),
  `available_gib` 74 and this boot's id.
- `test_a_loaded_model_is_admitted_at_once` — the coder ready → `Admitted(loaded_now=False)`, no
  load call.
- `test_one_load_at_a_time` — two models, both fitting, asked together: the second's load call
  starts only after the first's returns.
- `test_dans_keys_go_ahead_of_agents_and_first_come_within_each` — while a load runs: `agent` A,
  Dan B, `agent` C, Dan D, in that order → loads B, D, A, C; with a third group at `queue` 0 whose
  request E comes after D → B, D, E, A, C.
- `test_the_first_request_that_fits_takes_the_slot` — Dan's coder (doesn't fit) queued ahead of
  `agent`'s embeddings (fits) → the embeddings load; Dan's request keeps its place and loads first
  once memory frees.
- `test_a_residents_reload_goes_ahead_of_dans_request` — a reload of Gemma queued behind Dan's
  coder, both fitting → Gemma loads first.
- `test_a_reload_counts_every_hold_and_waits_without_a_deadline` — a 40 GiB room hold and room for
  Gemma only inside it → the reload doesn't take the hold, sends `resident_waiting` once, and loads
  once the hold ends, an hour later on the injected clock; with a brake hold, the same.
- `test_the_groups_parts_decide_apart` — a group with `uses_hold` and without `reloads_marked`:
  it loads into Dan's hold, and gets `footprint_suspect` for a marked model.
- `test_a_request_for_a_loading_model_joins_its_load_and_waits_past_its_wait` — the coder's load
  takes 50 s; Dan's request (wait 30 s) at 5 s → `Admitted` at 50 s.
- `test_no_fit_after_the_keys_wait_carries_the_moments_numbers` — the examples' moment, Dan's key →
  at 30 s `no_fit`, its message Task 6's Dan text; recorded in `state.refusals`; `refused` emitted.
  *(Added 2026-10-07, at Task 6: with a ceiling that binds, the message's figure is the admitter's
  own `free_for_a_load`, passed as `Moment.free_gib`, not available − reserve − owed.)* *(Added
  2026-10-07, at Task 6's fix round 3: and its expected breakdown is the ceiling's, *(the 102 GiB
  the GPU can allocate, less …)*, compared as the whole text, so the admitter must fill
  `ceiling_gib` and `committed_gib` whenever that term is the smaller. A gate that forgot them would
  pass `refusal`'s own checks, since `available_gib` and `reserve_gib` would be there, and the
  breakdown would no longer add up.)*
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
- `test_with_dans_job_in_the_hold_agent_reads_nothing_free` — available 36, owed 0, the 70 GiB hold
  → `agent`'s coder, at 600 s, gets Task 6's clamp row, word for word.
- `test_dans_load_shrinks_the_hold_by_what_outside_couldnt_cover` — 50 free for a load, a 41 GiB
  hold: Dan's coder (41) → the hold is 9; a further Dan load of 9 → the hold ends and
  `room_hold_ended` is emitted.
- `test_a_client_gone_leaves_the_queue_and_nothing_loads_for_it` — `agent` waiting for memory,
  then `cancel` → memory frees and no load starts.
- `test_a_load_started_for_a_client_that_went_finishes` — `cancel` after the load call began → the
  load completes and the model's record is `ready`.
- `test_a_load_call_that_times_out_keeps_the_slot_until_running_says_ready_or_gone` — `UNKNOWN`,
  `/running` showing `starting` for 10 s then `ready`: a second request waits until then. *(Corrected
  2026-10-08, at Task 12's review: with its ticket claimed, as launch claims it.)*
- *(Added 2026-10-08, at Task 12's review, the controller's ruling:)*
  - `test_an_unknown_load_whose_ticket_was_unused_frees_the_slot` — `UNKNOWN` (`refused`), the
    ticket still on disk → withdrawn, and a second request's load starts at once.
  - `test_a_refusal_of_llama_swaps_is_never_load_failed` — `failed(429, …)` and `failed(404, …)`,
    each with its ticket unused → the ticket withdrawn, the slot freed, no `load_failed` sent.
  - `test_a_load_starting_past_its_deadline_and_20_s_is_unloaded` — the ticket claimed,
    `UNKNOWN`, `/running` showing `starting` past the deadline plus 20 s → the model is unloaded
    and the slot freed. *(Corrected 2026-10-08, at Task 12's re-review: the deadline counts from the
    `started/` record. A start claimed 50 s after the ticket was issued is still `starting`, healthy,
    at the ticket's deadline plus 20 s → not unloaded then; it is unloaded at its `started_at` plus
    200 s.)*
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
- `test_while_restarting_a_down_llama_swap_reads_restarting` — `set_restarting(True)` and
  `set_llamaswap_up(False)`: Dan's request → `restarting` at 30 s; no `llama_swap_down` emitted.
- `test_every_outcome_clears_the_started_record` — ready → `started/` empty and the engine's pid in
  `state.ticketed`; failed → empty, nothing kept; `UNKNOWN`, then gone from `/running` → empty.
- `test_a_load_restored_as_starting_is_ticketed_when_ready` — a new admitter on a state `restore`
  marked the coder `starting`, its `started/` record on disk; `/running` shows it ready → its pid
  in `state.ticketed`, the record gone; 400 s later, no `unticketed_engines`.
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

### Task 16 [Spark]: the gate's drain, idle unloading, pins and sessions

**Files:**

- Create: `spark/src/spark/gate/policy.py`, `spark/tests/test_gate_policy.py`

**Interfaces:**

- `FrontChannel` — the gate's side of the front's calls: `snapshot` (the latest whole in-flight
  report), `connected: bool`, `send(event: dict)` (onto the open `/v1/front/events` stream),
  `drained(model, drain_id)` and `busy(model, drain_id)`.
- `Drainer(front: FrontChannel, llamaswap, admitter, emit, clock)`: `async drain(model: str, why:
  "make-room" | "unload" | "idle") -> bool` — sends `drain` with its `why`; waits for the front's
  `drained`, however long the requests in flight take; unloads; sends `unloaded`; True. For `why:
  "idle"`, the front's `busy` (a request was in flight when it marked the drain) ends it at once:
  nothing unloads, the model's last use is now, and False. ~~Not done
  `DRAIN_GRACE_S` after `drained` (the unload hung), or the channel drops at any point: sends
  `undrain` (when connected) and returns False.~~ *(Struck 2026-10-08, at Task 12's re-review: the
  corrected rule below replaces it.)* It never unloads before `drained`. *(Added
  2026-10-08, at Task 12's review, the controller's ruling:)* the unload is `llamaswap.unload`,
  bounded by `gateproto.UNLOAD_CALL_TIMEOUT_S` (60 s). Its timeout, a `LlamaSwapUnreachable`, isn't
  `llama_swap_down`: v257 still answers `/running` during a stop, since it reads the states outside
  its run loop (`internal/router/base.go:366-376`). On a timeout the gate re-reads `/running`, and
  the model stays counted as `stopping`, its memory not freed, until `/running` shows it gone.
  `llama_swap_down` comes only from `/running` itself. ~~How this meets `DRAIN_GRACE_S`, which is
  shorter than the unload's bound, is for the controller to settle before this task.~~ ~~*(Settled
  2026-10-08, the controller's ruling, after Task 12's fix round:)* the gate never sends
  `undrain` while `/running` shows the model `stopping`: a drain whose unload has begun ends
  only once `/running` shows the model gone (then `unloaded`) or back to ready (then `undrain`),
  and `DRAIN_GRACE_S` goes from 30 to 90 s, past the unload's 60 s bound, so it ends only a
  drain whose unload never answered and whose model `/running` no longer lists as stopping.
  Undrained early, a request would reach llama-swap for a stopping model, which v257 holds and
  then starts again; Task 11's tickets refuse that start, so it costs the request, not memory,
  but the request would fail for no reason Dan could see. A test: an unload that times out
  with `/running` showing `stopping` for 100 s sends no `undrain` until it shows the model gone.~~
  *(Corrected 2026-10-08, at Task 12's re-review, the controller's ruling: v257 never takes back an
  accepted unload, and a stopping model never returns to ready.)*
  - **Once the unload call is sent, the drain ends only when `/running` shows the model gone**:
    then `unloaded`, and True. It never ends with `undrain`. v257 runs an unload it has taken to the
    end, whatever happens to the call (`internal/server/apigroup.go:161`,
    `internal/router/base.go:432-440`). And a stop ends only in `stopped`
    (`internal/process/process_command.go:422-434`).
  - **The exception is an unload llama-swap never took**, which undrains, and False:
    - one that never had a connection, refused at connect or not made in time, so nothing was
      sent: Task 12's `LlamaSwapNotSent`;
    - by the same reading (added at Task 12's fix round 2, for the controller to confirm), one
      answered with an error, `LlamaSwapAnswered`. v257's 401 and 404 both come before its `Unload`
      (`internal/server/auth.go:31-34`, `internal/server/apigroup.go:153-160`), so the model stays
      loaded, and a drain that waited for it to go would wait for ever.
  - **`DRAIN_GRACE_S`, 90 s, bounds only a drain whose unload was never sent.** One not sent this
    long after `drained` undrains, and False.
  - **The channel-drop clause applies only before the unload is sent.** After that, a dropped
    channel leaves the drain to end at gone. The gate counts that unload as its own, so the
    residents' rule (Tasks 17 and 18) doesn't reload the model.
  - **While the model stays `stopping`**, `/v1/unload` and `/v1/make-room` send a
    `gateproto.StoppingProgress` every `gateproto.STOPPING_EVERY_S` (15 s), so Dan never waits in
    silence (Tasks 17, 19 and 30). Ctrl-C ends the wait, never the unload.
- `idle_due(now, state, registry, snapshot, live: Callable[[Session], bool]) -> list[str]` —
  on-demand models with no request in flight, no live session and no pin, idle at least
  `idle_unload_min`; residents never. The gate drains each, then emits `unloaded` (*after 60 min
  idle*).
- `Pins(state, admitter, emit, clock)`: `pin(model, until, uid)` — Dan's only (a `spark-admin`
  uid); a model not loaded is loaded first, through admission as one of his commands (`kind:
  "command"`); `unpin(model, uid)`;
  `expire(now)` — `pin_ended` for each that ran out.
  *(Added 2026-10-08, the controller's ruling, at Task 10's re-review, on its fix round 2:)* a pin that loads first reports its load, as `/v1/pin` streams it
  (Task 19): `pin(model, until, uid) -> AsyncIterator[dict]` yields a `gateproto.LoadProgress`
  (`{model, label, last_s}`) as the load starts, then `gateproto.PinConfirmation`'s fields; for a
  model already loaded, `PinConfirmation`'s fields alone; a refusal on the way, as a
  `gateproto.GateRefusalBody`, last. Its test: `test_a_pin_that_loads_yields_its_start_first`, the
  coder not loaded → the `LoadProgress`, then the confirmation with `loaded_s`; loaded → the
  confirmation alone, `loaded_s` None. *(Added 2026-10-08, the controller's rulings, at Task 10's second re-review:)* a pin whose load waits for
  the slot or for memory yields a `gateproto.WaitProgress` as it starts waiting, and again
  whenever its reason changes, before its `LoadProgress`, as Task 17's `Room.load` does.
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
  T + 2 h with the coder idle → `[]`; `expire` at T + 8 h → the pin gone, `pin_ended` with Task 7's
  text.
- `test_pins_are_dans_only` — `pin` from `agent`'s uid → refused.
- `test_pinning_a_model_that_isnt_loaded_loads_it_first` — the coder not loaded → admission called
  with `kind: "command"`, then the pin set.
- *(Added 2026-10-08, at Task 10's re-reviews:)* `test_a_pin_that_loads_yields_its_start_first` —
  the coder not loaded → its `LoadProgress`, then `PinConfirmation` with `loaded_s`; the coder
  loaded → `PinConfirmation` alone, `loaded_s` None; the coder's load waiting for Gemma's → a
  `WaitProgress` with `why: "slot"` and `loading_label: "Gemma"` first.
- `test_a_drain_waits_for_requests_in_flight_however_long` — 1 in flight for an hour: no unload;
  `drained` → unload, `unloaded` sent, True.
- `test_the_unload_comes_only_after_drained` — no `drained` → no unload call.
- ~~`test_a_drain_not_done_30s_after_drained_goes_back_to_serving` — `drained`, then an unload that
  hangs → at 30 s `undrain` sent, False.~~ *(Corrected 2026-10-08, at Task 12's re-review, the
  controller's ruling:)* `test_a_drain_whose_unload_is_never_sent_goes_back_to_serving_at_90s` —
  `drained`, then the unload not sent for 90 s → at 90 s `undrain` sent, False; one refused at
  connect (`LlamaSwapNotSent`), or answered 404 (`LlamaSwapAnswered`) → `undrain` at once, False.
- *(Added 2026-10-08, at Task 12's re-review, the controller's ruling:)*
  - `test_an_unload_that_times_out_waits_for_gone_and_never_undrains` — the unload call times out,
    and `/running` shows the model `stopping` for 100 s → no `undrain` is sent; `unloaded` once
    `/running` shows the model gone, True.
  - `test_a_channel_drop_after_the_unload_was_sent_ends_at_gone` — the channel drops after the
    unload call is sent → no `undrain`; the drain ends True once `/running` shows the model gone,
    and the gate counts the unload as its own.
- `test_every_drain_goes_back_if_the_fronts_channel_drops` — two drains under way, the channel
  drops → both False, no unload. *(Corrected 2026-10-08, at Task 12's re-review: each before its
  unload is sent.)*
- `test_an_idle_drain_that_finds_a_request_unloads_nothing` — `idle_due` names the coder, a request
  lands before the front marks the drain, the front answers `busy` → no unload, False, and the
  coder's last use is now.

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

### Task 17 [Spark]: make-room and its hold, release, load and unload, the residents' preload, the brake's release

**Files:**

- Create: `spark/src/spark/gate/room.py`, `spark/tests/test_gate_room.py`

**Interfaces:**

- `Room(state, registry, admitter, drainer, emit, clock, *, read_mem, read_hold, release_hold,
  boot_id)`:
  - `plan(target: Decimal | ALL, uid) -> tuple[str, RoomPlan]` — a `plan_id` and Task 5's
    `RoomPlan`, as `/v1/make-room/plan` answers them; every candidate marked:
    its pin, the session using it, its requests in flight and their age, its idle time; from
    `budget.make_room_plan`, the free-now figure the admission one for Dan.
  - `async execute(plan_id, for_s: float | None, uid)` ~~`-> RoomResult(unloaded: list[str], free_gib,
    hold_gib, until, ended: list[str])`~~ *(corrected 2026-10-08, at Task 10's re-review: it yields
    its progress, then `RoomResult(unloaded: list[str], free_gib, hold_gib, until, ended: list[str])`'s
    fields as its last line, as the note below says)* — drains and unloads each, in order; a pin it unloads ends,
    and a session loses its model, each named in `ended` and in `spark status`'s *recent*; sets
    `RoomHold(size = the target, or every GiB free for a load with ALL; until = now + for_s, or
    none; this boot)`.
  - `async release(uid, *, room: bool, brake: bool) -> ReleaseResult(room: RoomDone | None,
    brake: bool, reloading: list[str])`, `RoomDone(unused_gib, total_gib, next_label)` — the fields
    `room_done`'s words need — `room` ends Dan's hold (`room_hold_ended`, *`--done`*); `brake` lifts
    the brake's hold (`release_hold`); each only what it is asked, and each answers with its own
    confirmation (`room_done`, `brake_released_by_dan`); then the reloads that one allows.
    `spark make-room --done` asks `room`, `make brake-release` asks `brake`.
  - `async load(model, uid)` — admission as one of Dan's commands (`kind: "command"`), held up to
    the load call's 200 s; `unload(model, uid) -> AsyncIterator[dict]` — a drain (`why: "unload"`),
    yielding `{inflight: n}` at once and the result when the model is gone, which `/v1/unload`
    streams. *(Added 2026-10-08, at Task 12's review, the controller's ruling:)* `execute`'s unloads
    and `unload`'s go through Task 16's drainer, so an unload
    call's timeout (`gateproto.UNLOAD_CALL_TIMEOUT_S`, 60 s) isn't `llama_swap_down`: the gate
    re-reads `/running`, and the model stays counted as `stopping`, its memory not freed, until
    `/running` shows it gone. *(Added 2026-10-08, at Task 12's re-review, the controller's ruling:)*
    while it stays `stopping`, `unload` and `execute` yield a `gateproto.StoppingProgress` (`{model,
    label}`) every `gateproto.STOPPING_EVERY_S` (15 s), so Dan never waits in silence, and their
    result line comes only once `/running` shows the model gone. A caller that goes ends the
    iterator, never the unload. Its test: `test_an_unload_left_stopping_says_so_every_15_s` — the
    unload call times out and `/running` shows the coder `stopping` for 40 s → two
    `StoppingProgress` lines, then the result once it is gone.
  - *(Added 2026-10-08, the controller's rulings, at Task 10's re-review:)* `execute` and `load`
    yield their progress, as `unload` yields its count, since `/v1/make-room` and `/v1/load` now
    stream it (Task 10's notes, Task 19):
    - `execute(plan_id, for_s, uid) -> AsyncIterator[dict]` yields a `gateproto.MakeRoomProgress`
      (`{model, label, inflight}`) as each model's drain begins, then its `RoomResult`'s fields as
      the last line.
    - `load(model, uid) -> AsyncIterator[dict]` yields a `gateproto.LoadProgress`
      (`{model, label, last_s}`) as the load starts (none for a model already loaded), then the
      confirmation's fields.
    - A refusal on the way is the last line, as a `gateproto.GateRefusalBody`.
    - *(Added 2026-10-08, the controller's ruling, at Task 10's re-review, on its fix round 2:)* a
      pin that loads first yields the same `LoadProgress` before its result; Task 16's `Pins.pin`,
      which loads it, has the note, since Task 16 comes first.
    - *(Added 2026-10-08, the controller's rulings, at Task 10's second re-review:)* `load` yields a `gateproto.WaitProgress` as its request starts
      waiting for the slot or for memory, and again whenever its reason changes, before its
      `LoadProgress`. Its test: `test_load_yields_its_wait_before_its_start` — the coder's load
      queued behind Gemma's → `{why: "slot", loading_label: "Gemma", …}` while Gemma loads, then
      the coder's `LoadProgress`, then its result.
  - `expire(now)` — a hold whose `until` has passed ends (`room_hold_ended`, *its time ran out*).
- `Preloader(admitter, registry, emit, clock)`: `async run(models)` — the residents, one at a time,
  each through `admitter.reload(model)` (Task 15: ahead of the queue, every hold counted, no
  deadline); at start it waits for llama-swap with a backoff of 1, 2, 4 … s up to 30 s; a resident
  that doesn't fit waits in the queue, shows as waiting, and sends `resident_waiting` once. It runs
  at start, after a hold ends (only the residents make-room or the brake unloaded), after apply's
  restart (all the residents), and (Dan's decision, 2026-10-07, after the forward-and-back council)
  after an unplanned llama-swap restart — llama-swap answering again with every engine gone, its
  unit started anew, no apply announced — and for a resident that leaves `/running` without the
  gate or the brake unloading it (an earlyoom kill, a crash): that one. *(Added 2026-10-07, at Task
  7, the controller's ruling: each run passes `resident_waiting`'s `after` — `boot`, `hold`,
  `brake`, `apply`, `restart` or `crash` — so a resident that waits reads *Gemma didn't fit at boot:
  …*, not *after the hold ended*.)*
- `brake_release_due(now, above_since: float | None, hold: Hold, state, *, reloads_fit: bool,
  boot_id: str) -> "release" | "wait" | "needs_dan"` — `needs_dan` when Task 13's
  `hold.release_waits_for_dan` says so: a hold from another boot, or within the hour after an
  automatic release ~~(`brake_needs_release`, high, once)~~ (`brake_needs_release`, high, once,
  only for a hold from another boot: *corrected 2026-10-07, at Task 7's review*, as the note below
  says); else `release` only when memory has been above the warn line for `RELEASE_AFTER_S` and the reloads fit; else `wait`. A
  release removes the hold, sends `brake_released` naming what reloads, and reloads the residents
  the brake unloaded, never the model that was loading when it fired. *(Added 2026-10-07, at Task 7,
  the controller's ruling: `brake_needs_release` is sent only for a hold from another boot; a brake
  within the hour after an automatic release has already said it waits for Dan, in its own
  `brake_fired`, Task 14's.)*

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
  28 for 5 min → `needs_dan`, ~~`brake_needs_release` once~~ and no `brake_needs_release`, since that
  brake's `brake_fired` said so *(corrected 2026-10-07, at Task 7, the controller's ruling)*.
- `test_a_hold_from_a_previous_boot_waits_for_dan` — the hold's `boot_id` `b1`, now `b2` →
  `needs_dan`, *After the reboot, …*; a Phase 1 hold, its `boot_id` None → `needs_dan` too.
- `test_the_model_loading_when_the_brake_fired_isnt_reloaded` — the hold's `loading` the coder →
  the release reloads the residents only, and `brake_released` ends *The coder was loading when it
  fired, so it loads again only when you ask.*
- `test_done_ends_only_the_room_hold` — a room hold and a brake hold, `release(room=True)` → the
  room hold ended, the brake's hold still standing, and `room_done`'s words.
- `test_brake_release_ends_only_the_brakes_hold` — the same, `release(brake=True)` → the brake's
  hold lifted, Dan's hold still standing, and the reloads kept out of it.
- `test_the_preload_waits_for_llama_swap_then_loads_one_at_a_time` — llama-swap unreachable three
  times (backoff 1, 2, 4 s), then up → Gemma, the embeddings, whisper loaded in registry order,
  each after the last, each through `admitter.reload`.
- `test_the_residents_come_back_after_an_unplanned_llama_swap_restart` — llama-swap's unit started
  anew, no apply announced, `/running` empty → the three residents reloaded one at a time.
- `test_a_resident_killed_outside_the_gate_reloads` — Gemma leaves `/running` with no drain and no
  brake event → Gemma reloaded; Gemma unloaded by the brake → not, until the release.
- `test_release_answers_the_fields_its_words_need` — `release(room=True)` with 40 GiB held and
  unused, the coder next → `RoomDone(40, 40, "the coder")`.
- `test_unload_yields_the_count_first` — 1 in flight → `{inflight: 1}` at once; the result once the
  drain is done.
- *(Added 2026-10-08, at Task 10's re-review:)* `test_execute_yields_each_drain_as_it_begins` — a
  plan unloading the coder (1 in flight) and Gemma → `{model: "qwen3.8-27b", label: "the coder",
  inflight: 1}` before the coder's drain is done, then Gemma's line, then the result's fields last.
- *(Added 2026-10-08, at Task 10's re-review:)* `test_load_yields_its_start_then_its_result` — the
  coder not loaded, its last load 24 s → `{model: "qwen3.8-27b", label: "the coder", last_s: 24}`
  as the load starts, then the confirmation's fields once it is ready; the coder already loaded →
  the confirmation alone.
- *(Added 2026-10-08, at Task 10's second re-review:)* `test_load_yields_its_wait_before_its_start`
  — the coder's load queued behind Gemma's → `{model: "qwen3.8-27b", label: "the coder", why:
  "slot", loading_label: "Gemma", …}` while Gemma loads, then the coder's `LoadProgress`, then its
  result.

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

### Task 18 [Spark]: the gate's core — its loops, the activity record, apply's hold and the bypass check

**Files:**

- Create: `spark/src/spark/gate/core.py`, `spark/tests/test_gate_core.py`

**Interfaces:**

- `GateCore(registry, state, …)` — ties Tasks 13–17 together. It reads memory every 250 ms and
  llama-swap's `/running` every second, the top holders (`nvidia-smi`) every 5 s, each off the
  event loop where it could block; writes the activity record every second; ingests the brake's
  events every second (with the registry, Task 14); watches the four units every 5 s; runs the
  idle check, the pins' and the hold's expiry, and the brake's release check every 5 s; saves the
  state on every change; sets `clean_shutdown` false at start and true on a clean stop. An
  engine's pid is `procs.engine_pid(port, recorded=<its state.ticketed pid>)`, so the holders and
  the bypass check never count the stack's own engine as an outside process.
- The activity record, `GATE_STATE/activity.json`, written whole every second: `{written_at,
  boot_id, models: {name: {state, inflight, last_use}}}`, and for a model `starting`, its
  `admitted_gib`, `started_at` and `available_at_start` from its ticket — what the brake reads
  (Task 23). A model is `starting` there from the moment its ticket is issued, not from when
  `/running` shows it, so a load's first second is never read as an unexplained fall.
- The residents' return (Dan's decision, 2026-10-07): when the unit watch sees llama-swap started
  anew with no apply announced and `/running` answering with every engine gone, or `/running`
  loses a resident that neither a drain nor a brake event accounts for, the core runs Task 17's
  `Preloader` for those residents.
- The front's model list: `hello` and every `state` carry the registry's models (`[{name, roles,
  label, resident}]`), and a registry re-read (an apply's end) sends a `state` with the new list,
  so a renamed or added model reaches the front without its restart.
- The apply hold: `drain-all` writes `state.applying` and saves it before it answers; `apply/renew`
  moves its `renewed_at`; a gate that starts with it set holds as before (admission restarting,
  `hello` with `applying` true). `end_apply(why)` is its one end, whatever ends it: `apply/end`,
  `undrain-all`, the unit watch seeing llama-swap started after `begun_at` with `/running`
  answering, or the hold not renewed for `APPLY_LAPSE_S` (60 s), checked every second, before
  `begin` as after. In one step of the event loop, with nothing admitted in between, it marks the
  hold ended and saves it, re-reads the registry, sends `release_all`, and queues the residents'
  reloads ahead of anything else (Task 15's order); then `apply_restarted` when llama-swap did
  restart, and a line in *recent* naming why it ended. A second call finds `ended` and does
  nothing.
- The bypass check, every 5 s: Task 8's engines by port, each pid against `state.ticketed` and the
  live `started/` records → `health.unticketed_engines`; launch's `no_ticket` refusal records, each
  counted once as it appears this boot → `health.no_ticket_refusals`. *(Corrected 2026-10-08, at
  Task 11's review, the controller's ruling:)* a live `started/` record counts as ticketed only when
  its ticket's `id` is one the gate issued (`state.issued`, Task 13), since an engine can write
  `started/`, and a forged record must not hide an engine from this check; launch's `ticket_expired`
  refusals count in `no_ticket_refusals` alongside `no_ticket`, since a start around the gate that
  meets a leftover ticket is refused `ticket_expired`; `tickets.started` raising `OSError` is caught
  and reported under `health`, and the check counts no record as ticketed meanwhile.
- `quiet() -> dict` — `/v1/quiet`'s answer, each request's `key_label` from the front's snapshot's
  `requests` (Task 10).

**Tests** (`spark/tests/test_gate_core.py`; an injected clock, stand-ins for llama-swap, the front's
channel, the units and `/proc`):

- `test_the_activity_record_is_written_every_second` — three ticks of the injected clock →
  `activity.json`'s `written_at` the last tick, the models' state, in-flight counts and last use
  from the front's snapshot.
- `test_a_model_is_starting_in_the_record_from_its_ticket` — a ticket issued, `/running` not yet
  showing the model → the record has it `starting`, with its admitted footprint.
- `test_quiet_reports_whats_in_flight_and_who_asked` — the coder with 1 request of `agent`'s in
  flight since T − 20 → `quiet_for_s` 0 and the request, `key_label` `agent`; nothing in flight
  since T − 45 → 45.
- `test_apply_begin_and_end_reread_the_registry_and_reload_the_residents` — `begin` → the
  admitter's restarting on; `end` → the registry re-read from disk (a changed label shows), a
  `state` event carrying the new list, the residents' reloads queued, `apply_restarted` naming
  them.
- `test_the_residents_come_back_after_llama_swap_restarts_unplanned` — llama-swap's unit started
  anew, no apply, `/running` empty → the Preloader runs for the three residents.
- `test_a_blocking_read_never_holds_the_loop` — an `nvidia-smi` stand-in taking 1 s: the status
  view is built within 0.3 s, from the last reading.
- `test_the_holders_never_count_a_ticketed_engine` — a `spark` process of an unknown `comm` on
  801, its pid in `state.ticketed` → not among the holders.
- `test_a_gate_restarted_mid_apply_keeps_holding` — `drain-all` and `apply/begin` on one core; a
  new core on the same state folder → an admit for a loaded model's reload gets `restarting` at
  its deadline, `hello` carries `applying: true`; `apply/end` to the new core → `release_all`, and
  a held admit goes through.
- `test_the_apply_hold_ends_once_llama_swap_answers_again` — `begin` naming llama-swap; the unit
  watch's start time after `begun_at` and `/running` answering → the one end, with no `apply/end`,
  the residents' reload queued; the later `apply/end` → nothing.
- `test_drain_all_holds_and_unloads_nothing` — after `drain-all`: `hold_all` sent to the front, an
  admit call waits and gets `restarting` at its deadline, nothing unloads; after `apply/end`:
  `release_all` sent, and admission resumes.
- `test_every_end_does_the_same_work_once` — parametrized over `apply/end`, `undrain-all`, the
  automatic end and a lapse: Gemma not loaded and Dan's request for the coder held → the registry
  re-read, `release_all`, and Gemma's reload takes the load slot before the coder's; then
  `apply/end` again → nothing sent, nothing queued.
- `test_a_hold_whose_renewal_lapses_ends_before_begin_too` — `drain-all`, no `begin`, no renewal
  for 60 s → ended, its line in *recent*; renewed every 15 s for 10 minutes → still standing;
  `apply/renew` after the end → `{ended: true}`, and no hold.
- `test_an_engine_without_a_ticket_is_reported` — an engine on 801, pid 4242, in neither
  `state.ticketed` nor a live `started/` record → `health.unticketed_engines` names it; with its pid
  in either → empty; three `no_ticket` refusals this boot, each counted as its record appears →
  `no_ticket_refusals` 3. *(Added 2026-10-08, at Task 11's review, the controller's ruling: a
  `started/` record holding pid 4242 whose `id` the gate never issued → still named; two `no_ticket`
  and one `ticket_expired` → 3.)*

**Steps:**

- [ ] **Step 1:** the failing tests; run them: they fail (the module missing).
- [ ] **Step 2:** `gate/core.py`; the tests pass; `make test lint`.
- [ ] **Step 3: Commit.** **On the Spark:**

```bash
git add spark/src/spark/gate/core.py spark/tests/test_gate_core.py
git commit -m "feat(spark): 🤖 the gate's core: its loops, the activity record, apply's hold and the bypass check" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 19 [Spark]: the gate's sockets and spark gate

**Files:**

- Create: `spark/src/spark/gate/app.py`, `spark/src/spark/gate/main.py`,
  `spark/tests/test_gate_app.py`
- Modify: `spark/src/spark/cli.py` (`spark gate`, its module imported inside its handler, so
  Task 3's import test still holds); `spark/src/spark/registry.py` and
  `spark/tests/test_registry.py` (`registry.parse_keys`: added 2026-10-08, at Task 9's re-review)

**Interfaces:**

- `build_apps(core, *, front_uid: int, admin_uids: Callable[[], set[int]]) -> tuple[Starlette,
  Starlette]` — the status app and the control app, each route authorizing the scope's
  `peer_cred` uid as Task 10's table says: `front` routes only `front_uid`; `users` any caller (the
  socket's group already limits who connects); `owner` the session's uid, or an admin uid to end
  one; `admin` an admin uid or 0. A request without `peer_cred` is refused. A refusal is 403 with a
  sentence saying who may. A body over `MAX_REQUEST_BYTES` → 413, never parsed. Every request is
  bounded by `REQUEST_TIMEOUT_S` but the routes that wait: `/v1/admit` (its key's wait),
  `/v1/front/events` (kept open), `/v1/load` and `/v1/pin` (`LOAD_CALL_TIMEOUT_S`), and
  `/v1/unload` and `/v1/make-room` (unbounded: a drain waits for its requests, and `/v1/unload`
  streams its count first).
  *(Added 2026-10-08, the controller's rulings, at Task 10's review:)*
  - `/v1/make-room` streams too: its head at once, a `gateproto.MakeRoomProgress` line as each
    model's drain begins, then its result. So the CLI bounds the head and waits for the drains
    (Task 30).
  - The gate's every answer with a status other than 2xx, the 403 that says who may ~~and a refused
    `/v1/load`~~ among them, and a refusal sent as a stream's line, is
    `gateproto.GateRefusalBody`: a top-level `message`, one plain line the CLI shows as it is,
    and `code`, None where there is none. It is never the front's `{"error": {…}}` shape, whose
    reason the CLI wouldn't show. `/v1/admit` alone answers 200 with `ok: false`.
  - A route with nothing to answer may answer 204, which the client reads as `{}`.
  - *(Added 2026-10-08, the controller's rulings, at Task 10's re-review:)*
    - `/v1/load` streams too: its head at once, a `gateproto.LoadProgress` line as the load starts,
      then the confirmation's fields, from `Room.load`'s iterator (Task 17). It is still held up to
      `LOAD_CALL_TIMEOUT_S` here.
    - Every stream (`/v1/unload`, `/v1/make-room`, `/v1/load`) ends with its result line, or with a
      refusal line in `GateRefusalBody`'s shape (`message` and `code`), and then its last chunk.
    - *(Added 2026-10-08, the controller's ruling, at Task 10's re-review, on its fix round 2:)*
      `/v1/pin` on a model that isn't loaded streams as `/v1/load` does, from `Pins.pin`'s iterator
      (Task 16): its head at once, the `LoadProgress` line as the load starts, then
      `PinConfirmation`, held up to `LOAD_CALL_TIMEOUT_S`. A pin on a model already loaded answers
      once, with `PinConfirmation` alone, and so does `DELETE /v1/pin/{model}` with its own answer.
  - *(Added 2026-10-08, the controller's rulings, at Task 10's second re-review:)*
    - *(Corrected:)* the first note above listed *a refused `/v1/load`* among the answers with a
      status other than 2xx. With its head sent at once, a refusal after a wait (`no_fit`,
      `loading`, `held_by_brake`) is a refusal line in a 200 stream. Only a refusal decided before
      the head, such as the 403 that says who may or a 413, is an error status.
    - `/v1/load`, and a pin that loads, send a `gateproto.WaitProgress` line as the request starts
      waiting for the slot or for memory, and again whenever its reason changes, before its
      `LoadProgress`, from `Room.load`'s and `Pins.pin`'s iterators.
- `GET /v1/status` through the status socket — the refusals the caller may see: those whose key's
  `account` (Task 4) is the caller's user name, and those whose record's `uid` is the caller's;
  Dan's processes only as *a process of Dan's*. Through the control socket, everything.
- `gate/main.py`: `spark gate` — `listen_fds()["status"]` and `["control"]`; credentials
  `llamaswap-key`, `ntfy-token` and `keys` (the private key list, ~~read with `registry.load_keys`~~
  read with `credentials.read_credential_text` and parsed with `registry.parse_keys`, as the note
  below says);
  `NTFY_URL` and `NTFY_TOPIC_GATE` from the environment (the values file); `spark-front`'s uid and
  `spark-admin`'s members from the system's databases, or from `SPARK_FRONT_UID` and
  `SPARK_ADMIN_UIDS` when set, for tests only; `AsyncLlamaSwap(paths.LLAMASWAP_URL, key,
  load_timeout_s=load_timeout_for(render.HEALTH_CHECK_TIMEOUT_S))`; `run_servers` with
  `make_protocol(peer_cred=True, header_timeout_s=10)` and `graceful_s=gateproto.GRACEFUL_S`.
  `TESTED_AGAINST = {"llama-swap": "v257"}`.
- *(Added 2026-10-08, at Task 12's re-review, the controller's rulings.)*
  - **A model that stays stopping.** While an unload sent leaves its model `stopping` (Task 16),
    `/v1/unload` and `/v1/make-room` send a `gateproto.StoppingProgress` line every
    `gateproto.STOPPING_EVERY_S` (15 s), from `Room.unload`'s and `Room.execute`'s iterators (Task
    17). The route stays open until `/running` shows the model gone. A caller that goes, Dan's
    Ctrl-C, ends the stream, never the unload.
  - **Shutdown.** `spark gate` cancels its load tasks (Task 15) before the client's `aclose()`. A
    load on a closed client raises httpx's `RuntimeError` instead of coming to a `LoadOutcome`.
- *(Added 2026-10-08, at Task 9's review, the controller's rulings.)*
  - **The key list:** the `keys` credential is read with `credentials.read_credential_text`
    (Task 9). It refuses a missing, empty or non-text file by name and never quotes it. The text
    then goes to the key list's parser: `registry.load_keys` reads a path today, so this task gives
    it a text form beside it, `registry.parse_keys(text, groups)`, which `load_keys` calls.
  - **uvicorn's logging:** `run_servers` leaves `log_config=None`, so uvicorn's WARNING-and-up
    lines and tracebacks ("Exception in ASGI application") reach stderr, and so the journal,
    through logging's last-resort handler. A traceback carries its exception's text, which code can
    build from a request (`int()` of a header quotes it). So `spark gate` puts a filter or handler
    on `uvicorn.error` that drops `exc_info` and never logs request content: a body, a header, a
    key. `test_a_raising_route_puts_no_request_content_on_stderr` checks it.
- *(Added 2026-10-08, the controller's rulings, at Task 9's re-review.)*
  - **The upgrade warnings.** The same filter drops uvicorn's "Unsupported upgrade request."
    warning and the "No supported WebSocket library detected" line after it. Any local process,
    `agent` included, can cause them, since `ws="none"` (Task 9) turns every upgrade down.
  - **Authorization before the body.** Every route authorizes on what it knows before the body,
    the caller's uid from `peer_cred`, before its first `receive()`. So a refused caller is
    answered at once, and its unread body falls under Task 9's deadline. A body the app is still
    reading has no limit (Task 9), so authorizing after reading would give the hold back.
    `test_an_unauthorized_caller_with_a_trickled_body_is_refused_at_once` checks it.
  - **Startup errors about `keys.yaml`** may name a key's name or group and a line, and PyYAML's
    may quote a snippet of that line: the list holds no key or digest, and the journal stays on
    the box. They never quote a key or a digest from anywhere else.

**Tests** (`spark/tests/test_gate_app.py`; the apps called with a scope whose `peer_cred` the test
sets, or over real sockets where named; those over real sockets read `SO_PEERCRED`, so they skip
off Linux with Task 9's reason, and the rest run everywhere):

- `test_an_inflight_report_from_any_uid_but_spark_fronts_is_refused` — `front_uid` 990: a report
  from 990 → 200; from 1000 → 403, the snapshot unchanged.
- `test_admit_and_the_front_channel_are_spark_fronts_alone` — `/v1/admit`, `/v1/front/events`,
  `/v1/front/drained`, `/v1/front/busy` and `/v1/front/refused` from 1000 → 403; each from 990 →
  not 403.
- `test_a_request_without_peer_cred_is_refused` — no `peer_cred` → 403.
- `test_the_control_socket_serves_only_spark_admin_and_root` — an admin uid → 200; 0 → 200;
  `agent`'s → 403.
- `test_status_through_the_status_socket_shows_only_the_callers_own_refusals` — refusals for Dan's
  key, for `agent`'s key (`account: agent`, no uid) and for one of `agent`'s commands (its uid, no
  key): `agent`'s call sees the second and the third, and Dan's python job as *a process of Dan's,
  32 GiB*; the control socket's call sees all three, and `python3 (chendaniely)`.
- `test_a_request_over_the_size_cap_is_refused_not_crashed` — 65,537 bytes → 413 in words; the
  next request answered.
- `test_a_caller_that_hangs_times_out` — a body that never finishes, the timeout set to 0.3 s → the
  connection closed by 1 s; an admit call held for 2 s isn't cut.
- `test_a_held_load_outlasts_the_request_timeout` — over a real socket, the timeout 0.3 s, a load
  the stand-in llama-swap holds for 1 s → `/v1/load` answers with the confirmation's fields after
  it; `/v1/unload` streams `{inflight: 1}` at once and its result after the drain.
- *(Added 2026-10-08, the controller's rulings, at Task 10's second re-review:)* `test_a_streams_head_goes_at_once_even_behind_another_load` —
  over a real socket, with Gemma's load held by the stand-in llama-swap for 2 s:
  - `/v1/load` for the coder, which queues behind Gemma's, sends its head and its `WaitProgress`
    (`why: "slot"`) within 0.5 s, and its `LoadProgress` only once Gemma's load is done;
  - `/v1/pin` for the coder does the same;
  - `/v1/make-room`, its drain waiting on a request in flight, sends its head and its first
    `MakeRoomProgress` within 0.5 s.
  A handler that sends its head only with its first line, or with its result, fails it: the CLI
  would read a gate queued behind a load as a gate that is down.
- `test_the_gate_keeps_serving_while_ntfy_hangs` — a publish that hangs: `GET /v1/status` answers
  within 0.5 s.
- `test_logs_reads_the_engines_last_lines_with_the_gates_key` — the stand-in llama-swap's lines:
  `GET /v1/logs/coder?n=5` → the last five; the stand-in saw the gate's key.
- `test_status_view_has_every_key` — `GET /v1/status`'s JSON has every `StatusView` key.
- `test_status_view_at_the_examples_moment` — the examples' inputs (121.6 GiB in all, 117 at idle,
  48 available, the residents' footprints 32, 8 and 3 holding 27, 7 and 3, the coder marked at
  03:12 seen using 26, `agent` waiting for Dan, no hold, the brake fired at 03:12 and released at
  03:40) → exactly the `StatusView` dict Task 29's test formats: `free_for_a_load_gib` 18 (Dan's
  view, his hold never counted), `above_brake_gib` 28, `owed_gib` 6, `unaccounted_gib` 26 (117 −
  48 − 43: Dan's python job, less the residents' growth still owed), `waiting[0].why` `dan`; and
  through the status socket, `agent`'s view of the same moment, its *recent* only its own and
  Dan's process unnamed.
- `test_a_dropped_admit_call_cancels_its_queue_entry` — over a real socket, an admit held for
  memory; the client closes; the admitter's `cancel` is called within 1 s and no load starts.
- `test_a_front_refusal_is_recorded_and_sent` — `POST /v1/front/refused` with `model_not_found`
  → in *recent*, and one `refused` notification. *(Added 2026-10-07, at Task 7, the controller's
  ruling: the gate sends it with `code` and a `Moment` it builds from `{code, model, key}` and what
  it knows — the key's label and `words` from the key list, its group's `wait_s`, the model's label
  (for `model_not_found`, `asked_name` the model asked for, and `models` the registry's), and the
  drain's `why` for `draining` — since `refused` checks the moment as the refusal's own words do.)*
- `test_the_canary_route_answers_found_or_not_only` — a needle in a refusal record → `{found:
  ["refusals"]}`, and nothing else of the record in the answer.
- `test_spark_gate_takes_its_two_sockets_from_systemd` — `spark gate` in a subprocess, given two
  short Unix sockets as `status:control`, stand-in credentials (the key list among them), a
  stand-in ntfy and the v257 stand-in, and `SPARK_FRONT_UID` and `SPARK_ADMIN_UIDS` (test-only
  overrides of the system's databases, which `spark gate` reads only when set) naming this test's
  uid: a `GateClient` gets the status on each; SIGTERM → exit 0, `STOPPING=1` sent, and the state
  file's `clean_shutdown` true.
- `test_a_raising_route_puts_no_request_content_on_stderr` — *(added 2026-10-08, at Task 9's
  review)* ~~`spark gate` in a subprocess, as above, with a route that raises:~~ *(corrected the
  same day, the controller's ruling, at Task 9's re-review:)* a subprocess that installs
  `spark gate`'s own logging setup (`gate.main.configure_logging()`, which `spark gate` calls
  before it serves) and serves, through `run_servers`, a stand-in app that raises; production
  code gets no test-only route or switch. A request carrying a body, headers and a key stand-in,
  each a distinct marker, gets its 500, and none of the markers, nor a traceback, is on the
  process's stderr.
- `test_an_unauthorized_caller_with_a_trickled_body_is_refused_at_once` — *(added 2026-10-08, the
  controller's ruling, at Task 9's re-review)* over a real socket, the apps served through
  `make_protocol(peer_cred=True, header_timeout_s=0.5)`: a POST to a control-socket route from a
  uid that isn't an admin, its `Content-Length` 1000, gets its 403 before any body byte is sent;
  with the body then trickled a byte every 0.1 s, the connection is closed within the deadline.
- `test_parse_keys_reads_the_key_list_from_its_text` (`spark/tests/test_registry.py`) — *(added
  2026-10-08, at Task 9's re-review)* `stack/keys.example.yaml`'s text → the same keys
  `load_keys` gives for the file; a text it refuses, `load_keys` refuses the same way.

**Steps:**

- [ ] **Step 1:** the failing tests; run them: they fail (the modules missing).
- [ ] **Step 2:** `gate/app.py`, `gate/main.py`, `spark gate`; the tests pass, Task 3's import test
  among them; `make test lint`.
- [ ] **Step 3: Commit.** *(The `git add` gained `registry.py` and `test_registry.py` on
  2026-10-08, at Task 9's re-review.)* **On the Spark:**

```bash
git add spark/src/spark/gate/app.py spark/src/spark/gate/main.py spark/src/spark/cli.py spark/tests/test_gate_app.py \
  spark/src/spark/registry.py spark/tests/test_registry.py
git commit -m "feat(spark): 🤖 spark gate: the status and control sockets, each caller known by its uid" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 20 [Spark]: the front, 1 — routes, keys, bodies and its own refusals

**Files:**

- Create: `spark/src/spark/front/__init__.py`, `spark/src/spark/front/parse.py`,
  `spark/src/spark/front/app.py`, `spark/tests/test_front_parse.py`, `spark/tests/test_front_app.py`

**Interfaces:**

- `parse.ROUTES: frozenset[tuple[str, str]]` — `("GET", "/v1/models")` and `("POST", p)` for the
  six inference paths; `GET /health` is the front's own.
- `parse.authenticate(headers: list[tuple[bytes, bytes]], digests: dict[str, bytes]) -> str | None`
  — the key from `Authorization: Bearer <key>` or `x-api-key: <key>`, through
  `credentials.match_key`; the key's name, or None. *(Added 2026-10-08, at Task 9's review, the
  controller's ruling: the digests come from the `client-keys` credential, and the key list from
  `keys`. Each is read with `credentials.read_credential_text` (Task 9), which refuses a missing,
  empty or non-text file by name without quoting it. Then `credentials.parse_digests` parses the
  first and the key list's parser the second (Task 19's `registry.parse_keys`). Task 21's
  `spark front` reads both at start.)* *(Added 2026-10-08, the controller's ruling, at Task 9's
  re-review:)* the app authorizes on the headers, the key through `parse.authenticate`, before its
  first `receive()`. So an unknown key is answered at once, and its unread body falls under Task
  9's deadline. A body the app is still reading has no limit, so reading before authorizing would
  give the hold back.
- `parse.read_json(receive, *, max_bytes: int) -> tuple[dict, bytes]` — the body parsed, and the
  bytes to send on, re-serialized from it (`json.dumps`, compact); 400 for a body that isn't a
  JSON object or has no string `model`.
- `parse.read_form(receive, content_type: str, *, spool_dir: Path, max_bytes: int, spool_above:
  int = 1 MiB) -> Form` — `Form(model: str, fields: list[tuple[str, str]], files:
  list[FilePart(name, filename, content_type, path_or_bytes)])`; a part over `spool_above` goes to
  a file in `spool_dir` (the unit's `PrivateTmp=` `/tmp`), never memory; two `model` fields → 400;
  `parse.encode_form(form) -> tuple[str, AsyncIterator[bytes]]` rebuilds it for upstream;
  `Form.close()` deletes every spooled file.
- `parse.ModelList` — the front's current model list, `[{name, roles, label, resident}]`: the
  deployed registry's at start, then the gate's from each `hello` and `state` event (Task 22), the
  last one kept while the gate is down (Dan's decision, 2026-10-07). `parse.resolve(name: str,
  models: ModelList) -> ModelEntry | None` — a model's name or one of its roles.
- `parse.clean_headers(headers) -> list[tuple[bytes, bytes]]` — drops `authorization`,
  `x-api-key`, `cookie`, `proxy-authorization`, `host`, `content-length`, `transfer-encoding` and
  the hop-by-hop headers; `parse.both_lengths(headers) -> bool` — both `Content-Length` and
  `Transfer-Encoding` present (400).
- `parse.KeyCaps(keys, groups)` — per key, by its group (Task 4), requests waiting
  (`max_waiting`) and requests open, waiting or answering (`max_open`): `enter_open(key) -> bool`,
  `enter_waiting(key) -> bool`, their `leave_…`; past a cap, `too_many_requests`.
- `parse.journal_line(key: str, model: str, status: int, seconds: float) -> str` — `front: <key>
  <model> <status> <seconds, one decimal>s`, nothing more.
- `app.FrontApp(models: ModelList, keys, groups, digests, *, upstream, gate, caps, log)` — the ASGI
  app. A form's body,
  spooled, is capped at 200 MiB (`MAX_BODY_BYTES = 200 * 2**20`); a JSON body, parsed in memory, at
  16 MiB (`MAX_JSON_BYTES = 16 * 2**20`), so a few at once stay well inside `MemoryMax=512M`. At
  start it refuses, naming the key and never a digest, a digest whose name the private key list
  (Task 4) lacks and a listed key with no digest. The front's own answers: `GET /health` → 200
  `{"status": "ok"}` with no key needed; an unlisted route → 404 `route_not_served`; no key or an
  unknown one → 401, `{"error": {"message": messages.UNKNOWN_KEY, "code": "invalid_api_key"}}`; an
  unknown model → 404 `model_not_found`; over a cap → 429 `too_many_requests`; a body over the cap →
  413; anything it can't parse → 400; never a traceback to the client. (Task 21 forwards; Task 22
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
  `X-Custom: 1` and none of the others (its `Authorization` is Task 21's internal key).
- `test_an_upload_is_spooled_to_disk_and_deleted_when_the_request_ends` — a 3 MiB file part: while
  the request runs, the spool folder holds one file of its bytes; after the response, nothing; a
  100 KiB part writes no file.
- `test_a_body_over_200_mib_is_refused` — `Content-Length` of 200 MiB + 1 → 413 before reading;
  a chunked body that crosses 200 MiB → 413, and its spool file gone.
- `test_a_json_body_over_16_mib_is_refused` — a JSON body of 16 MiB + 1 → 413; one of 15 MiB is
  forwarded.
- `test_the_digests_must_match_the_key_list` — a digest named `robot`, and a listed key `agent` with
  no digest: each refuses the start, naming the key, the message holding no hex.
- `test_an_unparseable_request_gets_400_never_a_crash` — `{`, `[]`, `{"x": 1}` (no model), and a
  form with no boundary → 400 each; the next request is served.
- `test_model_not_found_names_the_current_models` — `qwen3.6-35b-a3b` → 404 with Task 6's text,
  listing the `ModelList`'s models. *(Added 2026-10-07, at Task 6: in the key's group's words. With
  Dan's key, its next step is the Mac's `make clients`; with `agent`'s, the Spark's
  `spark clients pi --write`, so the front passes `words` to `refusal`. Corrected at Task 6's fix
  round 1: `agent`'s is the deployed CLI's, `/opt/local-ai/app/.venv/bin/spark clients pi --write
  --registry /opt/local-ai/etc/models.yaml`, Task 34's. An empty asked name reads *There's no model
  by that name here.*, never an error.)*
- `test_too_many_requests_when_a_keys_caps_are_reached` — `agent`'s four requests held waiting →
  the fifth → 429, `Retry-After: 10`; 32 of a key's requests open → the 33rd → 429.
- `test_journal_lines_carry_key_name_model_status_and_duration_only` — one request → one line
  `front: agent qwen3.8-27b 200 1.2s` (its duration the injected clock's); no header value, body
  or file name in it.
- `test_an_unknown_key_with_a_trickled_body_is_refused_at_once` — *(added 2026-10-08, the
  controller's ruling, at Task 9's re-review)* the app on a real uvicorn through
  `make_protocol(peer_cred=False, header_timeout_s=0.5)`: a POST with an unknown key, its
  `Content-Length` 1000, gets its 401 before any body byte is sent; with the body then trickled a
  byte every 0.1 s, the connection is closed within the deadline.

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

### Task 21 [Spark]: the front, 2 — forwarding, counting, streams and spark front

**Files:**

- Create: `spark/src/spark/front/upstream.py`, `spark/src/spark/front/main.py`,
  `spark/tests/test_front_forward.py`
- Modify: `spark/src/spark/front/__init__.py` (`FRONT_MODULES`), `spark/src/spark/front/app.py`,
  `spark/src/spark/cli.py` (`spark front`, its module imported inside its handler, so Task 3's
  import test still holds)

**Interfaces:**

- `Upstream(base_url: str, internal_key: str)` — an `httpx.AsyncClient` per client key, so no
  upstream connection carries two keys' requests; each with `trust_env=False`,
  `follow_redirects=False`, `Timeout(connect=5, read=None, write=60, pool=5)`; `async
  forward(method, path, headers, body: bytes | AsyncIterator[bytes], key) -> UpstreamResponse` —
  the status, the headers (hop-by-hop dropped) and `aiter_raw()`; `Authorization: Bearer
  <internal_key>` the only credential sent.
- `InFlight` — per model: a count, the oldest request's start, the last end, and each request's
  key and start; `enter(model, key) -> token`, `leave(token)`, called in a `finally` on every way
  out; `snapshot() -> dict` (the `/v1/front/inflight` shape, with `requests: [{key,
  started_at}]`).
- A disconnect: `receive()` watched for `http.disconnect` beside the stream; the upstream call
  cancelled, and its count left.
- *(Added 2026-10-08, at Task 12's review, the controller's ruling:)* **A stream cut upstream
  reaches the client as a cut.**
  - An upstream SSE stream that ends without its route's last event is a cut: `data: [DONE]` on
    `/v1/chat/completions` and `/v1/completions`, `message_stop` on `/v1/messages`, and
    `response.completed` on `/v1/responses`. Each is checked against llama.cpp b11146's output
    when this task is built. *(Added 2026-10-08, at Task 12's re-review, the controller's
    ruling:)* an engine's own error event is an end, not a cut. llama-server can end a stream with
    the route's error event, `data: {"error": …}`, or `event: error` on `/v1/messages`, and no last
    event after it. The front passes that on as the engine sent it, and its journal line says
    `error`. An SSE comment ping (`:` and a blank line), which llama-server sends to keep a quiet
    stream open, is no event. (Read in a copy of llama.cpp's server source, `server-context.cpp`,
    taken 2026-09-28 and not tied to a release; checked against b11146 when this task is built.)
  - v257 ends a stream whose engine was killed cleanly, only early
    (`internal/process/process_command.go:492-507`). The `openai` 6.40.0 that pi locks ends its
    iteration without an error when a stream stops without `[DONE]` (its `core/streaming.js`,
    `fromSSEResponse`, read 2026-10-08). So pi would show a cut answer as a whole one; the web UI
    isn't checked. A stream is cut so by the brake's unloads, by `make apply-now`, and by an
    engine's crash or earlyoom kill.
  - The front passes a cut on as a cut, never as a finish. It ends the client's response without
    its last chunk, so uvicorn closes the connection and the client's HTTP library reports a body
    that never ended, which that `openai` raises rather than ending quietly (it rethrows any error
    but an abort). Task 50's S03 drill confirms that pi shows it as an error. It injects
    nothing (plan.md: *Nothing is injected into a reply stream*). It does the same for an upstream
    connection that breaks mid-stream.
  - Its journal line says `cut`, and the count goes down as on every other way out.
- llama-swap's 429 `concurrency_limit` → the client's 429, its `Retry-After`, Task 6's words.
- `FRONT_MODULES: frozenset[str]`, in `spark/front/__init__.py`, which imports nothing, so `apply`
  (Task 31) reads it without importing uvicorn — exactly the `spark` modules the front imports at
  start, all of them before it serves: `spark`, `spark.paths`, `spark.registry`,
  `spark.credentials`, `spark.sockets`, `spark.protocols`, `spark.sdnotify`, `spark.serve`,
  `spark.gateproto`, `spark.messages`, and `spark.front` with `parse`, `app`, `upstream` and `main`
  (Task 22 adds `gatelink`). `spark front` — `listen_fds()["front"]`; the deployed registry's key
  groups and its names and roles (the first `ModelList`), and the credentials `client-keys`, `keys`
  and `llamaswap-key`, read once at start; `run_servers` with `make_protocol(peer_cred=False,
  header_timeout_s=10)`, `graceful_s=gateproto.GRACEFUL_S`. `TESTED_AGAINST = {"llama-swap":
  "v257"}`. *(Added 2026-10-08, at Task 9's review, the controller's ruling:)* uvicorn's
  WARNING-and-up lines and tracebacks reach stderr, and so the journal, through logging's
  last-resort handler, since `run_servers` leaves `log_config=None`. So `spark front` puts a filter
  or handler on `uvicorn.error` that drops `exc_info` and never logs request content: a body, a
  header, a key. The front's own journal lines stay as *The front's journal lines* (Global
  Constraints) give them. *(Added 2026-10-08, the controller's ruling, at Task 9's re-review:)*
  the same filter drops uvicorn's "Unsupported upgrade request." warning and the "No supported
  WebSocket library detected" line after it. Any local process can cause them, since `ws="none"`
  (Task 9) turns every upgrade down.

**Tests** (`spark/tests/test_front_forward.py`; the front on a real uvicorn, against the v257
stand-in):

- `test_a_stream_is_forwarded_chunk_by_chunk` — five SSE chunks 0.1 s apart: the client has the
  first before the stand-in sends the last.
- `test_the_count_goes_down_when_a_response_ends_a_client_goes_or_upstream_fails` — the model's
  count is 1 during a request and 0 after each of: a full response; the client closing mid-stream;
  the stand-in dropping the connection mid-stream; the handler's task cancelled. *(Added 2026-10-08,
  at Task 12's review: the stand-in drops it with `stream(…, drop_after=n)`.)*
- *(Added 2026-10-08, at Task 12's review, the controller's ruling:)*
  `test_a_stream_cut_upstream_reaches_the_client_as_a_cut` — an unload in the stand-in mid-stream,
  and the stand-in dropping the connection mid-stream (`drop_after`): each time the client's httpx
  raises `RemoteProtocolError`, nothing is injected, and the journal line says `cut`; a full stream,
  its last event sent, still ends cleanly. *(Added 2026-10-08, at Task 12's re-review:)* and a
  stream whose last event is the engine's error event, pings (`:` lines) before it, ends cleanly,
  its journal line `error`.
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
- `test_the_snapshot_names_each_requests_key` — `dan-mac` from T and `agent` from T + 5 →
  `requests` holds both, with their keys and starts.
- `test_spark_front_takes_9100_from_systemd` — `spark front` in a subprocess, given a TCP listener
  on 127.0.0.1:0 as `front`, stand-in credentials, the v257 stand-in and a gate stand-in: `GET
  /health` → 200; SIGTERM → exit 0.
- `test_a_raising_request_puts_no_request_content_on_stderr` — *(added 2026-10-08, at Task 9's
  review)* ~~`spark front` in a subprocess, as above, with the app made to raise on a request:~~
  *(corrected the same day, the controller's ruling, at Task 9's re-review:)* a subprocess that
  installs `spark front`'s own logging setup (`front.main.configure_logging()`, which `spark front`
  calls before it serves) and serves, through `run_servers`, a stand-in app that raises;
  production code gets no test-only route or switch. A request carrying a body, headers and a
  client key stand-in, each a distinct marker, gets its 500, and none of the markers, nor a
  traceback, is on the process's stderr.
- `test_the_front_imports_only_its_listed_modules` — a subprocess importing `spark.front.main`: its
  `spark` modules in `sys.modules` are exactly `FRONT_MODULES`; one importing only `spark.front`
  loads no third-party module.

**Steps:**

- [ ] **Step 1:** the failing tests; run them: they fail (the modules missing).
- [ ] **Step 2:** `upstream.py`, `main.py`, the app's forwarding, `spark front`; the tests pass;
  `make test lint`.
- [ ] **Step 3: Commit.** **On the Spark:**

```bash
git add spark/src/spark/front/upstream.py spark/src/spark/front/main.py spark/src/spark/front/app.py \
  spark/src/spark/front/__init__.py spark/src/spark/cli.py spark/tests/test_front_forward.py
git commit -m "feat(spark): 🤖 the front forwards with its own key and counts every request until its response ends" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 22 [Spark]: the front, 3 — with the gate

**Files:**

- Create: `spark/src/spark/front/gatelink.py`, `spark/tests/test_front_gatelink.py`
- Modify: `spark/src/spark/front/app.py`, `spark/src/spark/front/main.py` (`gatelink` imported at
  start, and in `FRONT_MODULES`), `spark/tests/test_front_forward.py` (the modules test's set)

**Interfaces:**

- `GateLink(status_socket: Path, clock)` — httpx over the Unix socket
  (`httpx.AsyncHTTPTransport(uds=…)`, `trust_env=False`): keeps `GET /v1/front/events` open and
  follows its events, the `models` of each `hello` and `state` replacing the front's `ModelList`
  (Task 20); `gate_up` turns false only once that call has dropped and a new one hasn't
  been answered within `GATE_DOWN_AFTER_S`; `async admit(model, key, deadline, request_id) -> dict`
  (the call dropped when the client goes); posts a whole `/v1/front/inflight` snapshot on every
  change and at least every second; `drained(model, drain_id)`.
- What's loaded: the gate's `state` events while it's up; llama-swap's `/running`, cached for a
  second, while it's down.
- A request's way: loaded → forwarded; not loaded → `admit`, with `deadline = received_at + the
  key's wait`, nothing sent to the client meanwhile; the gate down → forwarded if loaded, else 503
  `gate_down`, the one refusal that stays a `503`, in the asking key's group's `words` (*added
  2026-10-07, at Task 6's fix round 1: `agent`'s names Dan's phone and Dan's `make doctor`, so the
  front passes `words`, and `key_label`, to every refusal it builds*); the gate restarting → each
  held admission asked again with its original deadline; llama-swap's 500 `upstream command exited
  prematurely` → asked again once and forwarded again from the held body, a second one reaching the
  client as 409 `load_failed`. A refused connection, or one reset before the response's headers
  (llama-swap crashed or restarting), goes the same way: through `admit`, with its original
  deadline, from the held body, so the gate answers it — `restarting` or `llama_swap_down` at the
  deadline, both `409`s, or a load; once the headers have gone, a cut-off stream reaches the client
  as it is. *(Added 2026-10-08, at Task 12's review, the controller's ruling: that is, as a cut,
  Task 21's rule. When the gate's events show the model left `/running` while the stream ran (the
  brake, a crash), the front's journal line for the cut says why.)*
- The drain, under one lock: on `drain`, the model is marked draining with its count checked;
  for `why: "idle"` with a request in flight, the front posts `busy` and keeps serving; otherwise
  `drained` is posted when the count reaches 0, and its new requests wait as for a load, getting
  `draining` at their deadline, worded by the drain's `why`; on `unloaded`, they go through
  admission; on `undrain`, they are forwarded. *(Added 2026-10-08, at Task 12's re-review, the
  controller's ruling:)* when its call to the gate drops, the front undrains on its own, as
  plan.md's *Draining a model* says, though the gate may already have sent that model's unload
  (Task 16).
  - A request the front then forwards reaches llama-swap for a model it is stopping. It waits out
    the stop in v257's run loop (`internal/router/base.go:498-505`), and then starts the model.
  - `spark launch` refuses that start for want of a ticket (Task 11), so llama-swap answers 500
    `upstream command exited prematurely`.
  - The front asks `admit` once, as above, and gets `gate_down` while the gate is down, or an
    admission.

  It costs the request time, never memory.
- `hold_all` (apply's restart), or a `hello` with `applying` true, so a front restarted inside an
  apply holds again: every new request is held, as for a draining model; requests in flight
  finish; on `release_all`, or a `hello` with `applying` false, the held requests go through
  admission; at a held request's deadline it gets `restarting`, from the gate, or from the front
  itself while the gate isn't answering — never `gate_down` or `llama_swap_down`. Once the gate has
  been gone longer than the hold's `lapse_s` (from `hello` or `hold_all`), the front drops the hold
  itself and falls back to rule 8: it forwards requests for loaded models, and answers `gate_down`
  for loads. So a gate that fails at start inside an apply never holds the API.
- A refusal of its own (`model_not_found`, `too_many_requests`, `route_not_served`, `draining`) is
  posted to `/v1/front/refused` when the gate is up; not when it is down.

**Tests** (`spark/tests/test_front_gatelink.py`; a scriptable gate stand-in on a short Unix socket,
speaking Task 10's routes):

- `test_a_loaded_model_is_forwarded_without_asking_the_gate` — the gate's `state` says the coder is
  ready → forwarded; the stand-in saw no admit.
- `test_a_model_not_loaded_waits_for_the_gates_answer` — the stand-in holds the admit 1 s, then
  `ok` → forwarded after it; the admit carried the model, `dan-mac`, `deadline = received + 30`
  and a request id.
- `test_a_refusal_reaches_the_client_as_its_status_with_its_words_and_headers` — the stand-in
  answers `no_fit` (409, message M, `retry_after_s` 30) → the client's 409, body `{"error":
  {"message": M, "code": "no_fit"}, "retry_after_s": 30}`, `x-should-retry: false`,
  `retry-after: 30`; `loading` (409, no retry-after) → a 409 without `retry-after`; `restarting`
  (409, 60) → a 409 with `retry-after: 60`; `gate_down` (503, 30) → a 503 with `retry-after: 30`.
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
  passes → 409 `draining`.
- `test_unloaded_sends_waiting_requests_through_admission` — `unloaded` → the waiting request's
  admit call made.
- `test_undrain_returns_the_model_to_serving` — `undrain` → the waiting request forwarded.
- `test_an_idle_drain_with_a_request_in_flight_answers_busy` — 1 in flight, `drain` with `why:
  "idle"` → `busy` posted, no `drained`, and a new request forwarded at once.
- `test_draining_is_worded_by_its_reason` — `why: "unload"` → Task 6's neutral `draining` sentence;
  `make-room` → the make-room one.
- `test_a_refused_connection_goes_through_admission` — the model `ready` in the gate's state,
  llama-swap stopped → `admit` called with the original deadline, and the client gets the
  stand-in's `llama_swap_down` 409 at it; with the stand-in answering `restarting`, that.
- `test_hold_all_holds_every_new_request_until_release_all` — `hold_all`: a request for a loaded
  model is held, not forwarded; `release_all` → it goes through `admit`; held past its deadline →
  409 `restarting`.
- `test_a_restarted_front_holds_again_from_hello` — a fresh front whose first `hello` says
  `applying: true` → a request is held; the gate's channel then gone for 10 s → at the request's
  deadline, 409 `restarting`, not `gate_down`; a `hello` with `applying: false` → it goes through
  `admit`.
- `test_a_held_front_drops_the_hold_when_the_gate_stays_gone` — holding with `lapse_s` 60, the gate
  failing at start (its channel refused for 61 s) → a request for a loaded model is forwarded, and
  one for a model not loaded gets `gate_down`.
- `test_the_fronts_own_refusals_reach_the_gate` — a `model_not_found` → one `/v1/front/refused`
  post; with the gate down, none.
- `test_a_model_the_gate_adds_is_served_without_a_restart` — a `state` event whose `models` gains
  `qwen3.8-27b` → a request for it goes to `admit`, and `model_not_found` now lists it; the gate
  then down → the last list kept.
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
git add spark/src/spark/front/gatelink.py spark/src/spark/front/app.py spark/src/spark/front/main.py \
  spark/tests/test_front_gatelink.py spark/tests/test_front_forward.py
git commit -m "feat(spark): 🤖 the front asks the gate for each load, and keeps serving what's loaded while the gate is down" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 23 [Spark]: the brake for 2a

**Files:**

- Modify: `spark/src/spark/brake.py`, `spark/tests/test_brake.py`

**Interfaces:**

- `read_activity(path: Path, now: float) -> Activity | None` — `GATE_STATE/activity.json`; None
  when missing, damaged, or older than `ACTIVITY_STALE_S`.
- `brake_order(running: list[tuple[str, str]], activity: Activity | None, registry) -> list[str]` —
  with an activity record: a loading (`starting`) engine first, then models with nothing in flight,
  of any class, the longest unused first, then the rest, least recently used first; without one,
  Phase 1's order (on-demand first, then the largest).
- `LoadInProgress(model: str, admitted_gib: float, available_at_start: float, started_at: float)`;
  `loads_in_progress(activity, launch: Path, *, now, boot_id) -> list[LoadInProgress]` — the union,
  by model, of the activity record's `starting` models (when it is fresh) and the claimed tickets
  under `launch/started/` that `tickets.started` returns: this boot's, under 360 s old (Task 11).
  `admitted_gib` is the admitted footprint, never the cold load. A fall the gate knowingly admitted
  is not a crash (the controller's rulings, 2026-10-07). *(Corrected 2026-10-08, at Task 11's
  review, the controller's ruling:)* the union holds whether or not the gate's record is fresh, and
  what it takes from `started/` is bounded, since an engine runs as `spark` in llama-swap's sandbox
  and can write a record there: `available_at_start` is the brake's own reading nearest the record's
  `started_at`, never the record's `available_gib`; `admitted_gib` is at most that model's registry
  footprint; and a record counts only while its `pid` is alive and is that model's engine, read as
  `procs.engine_pid` reads one (the process on the model's engine port, run as `spark`, an engine's
  `comm`, and from Task 13 its start time). `tickets.started` raising `OSError` is caught: no record
  is no load in progress, the stricter way.
- `expected_floor(loads) -> float | None` — where the loads in progress should leave memory: the
  earliest start's `available_at_start` less every load's `admitted_gib`, less
  `LOAD_FLOOR_MARGIN_GIB` (2 GiB, this plan's value); None with nothing loading.
- `predict_breach(readings: deque[tuple[float, float]], *, brake_gib: float, lead_s: float,
  window_s: float, floor_gib: float | None) -> bool` — the fall over the last `window_s` (a
  least-squares slope of `MemAvailable` against time), extrapolated `lead_s` ahead; with a floor,
  while available is still above it, the extrapolation stops at the floor, since that much fall
  was admitted; true when the extrapolated reading is under `brake_gib`. A rise or a flat line is
  false; a load that runs past its floor counts as an unexplained fall. Since admission leaves the
  reserve (24) and the growth owed free, an admitted load's floor is above the brake line (20), so
  an ordinary load never trips the watch. `FALL_WINDOW_S = 2.0` and `UNLOAD_LEAD_S = 10.0` to start
  (Phase 1's loads fell 2.0 to 2.6 GiB/s, and v257 gives an engine 10 s to stop); Task 47 sets them
  from measurements. When it is true above the brake line, the brake holds and unloads as at the
  line.
- It writes the hold's new fields (its `available_gib` among them) and the events of Task 13's
  `brakeevents` (`append_event`, with `next_seq`), so `seq` continues across restarts; each event
  carries the brake line it acted on (`line_gib`, its own registry's, so a drill copy's raised
  line is recorded as it was), and its episode from `episode_for`. A run unloads one model per
  tick, as Phase 1's does, so a `--once` drill unloads one model per start of its unit.
- `alert_without_gate(text: str, priority: str)` — only while `read_activity` is None (the gate's
  record missing or stale): `subprocess.Popen(["/usr/bin/curl", "--fail", "--silent",
  "--max-time", "10", "--config", "-"], stdin=PIPE, start_new_session=True, stdout=DEVNULL,
  stderr=DEVNULL)`, its config written to stdin and closed — `url`, the token header's file
  (`header = "@<credentials>/ntfy-token"`), `header = "Priority: <priority>"` and `data-binary` —
  so neither ntfy's address, its topic nor the text is on a command line `agent` can read; each
  value in curl's config quoting, `"` and `\` escaped with a backslash; never waited on by the
  running brake; finished children reaped each tick; the event recorded `sent_by_brake: true`. The
  text is Task 7's `brake_fired` with `by_brake`. A `--once` run (the S05 drill's unit, Task 25)
  waits for its alert, which curl's `--max-time 10` bounds, before it exits, since systemd ends a
  oneshot unit's leftover processes. *(Added 2026-10-07, at Task 7, the controller's ruling:
  `unloaded` names each model in its state, `starting`, `idle` or `answering`, or None without the
  gate's record.)* *(Added 2026-10-07, at Task 7, the controller's ruling on rule 5: and
  `release_waits_for_dan`, Task 13's `hold.release_waits_for_dan` for the hold it wrote, with the
  last automatic release read from the gate's saved state, passed as `released_at`, so its own
  alert for a brake within the hour says why new loads stay paused until Dan releases them; and
  `release_after_s`, required, `RELEASE_AFTER_S`.)*
- `spark brake --key-credential llamaswap-key` (the unit's): the key from
  `read_credential(name)`; the start check records `credential llamaswap-key` as its source, never
  the value. `--key-env` stays, for drills.
- `spark brake --release` (`make brake-release`) — through `GateClient(GATE_CONTROL_SOCKET,
  5).post("/v1/release", {"brake": true})`, which ends the brake's pause and never Dan's hold,
  printing Task 7's `brake_released_by_dan`; on `GateUnavailable`, Phase
  1's direct release of the hold file, printing *The gate isn't answering, so the hold file was
  removed directly; nothing reloads until the gate is back.*; on `GateForbidden`, its text, and
  exit 1. *(Added 2026-10-07, at Task 7, the controller's ruling: that text is
  `messages.BRAKE_RELEASED_WITHOUT_GATE`.)*

**Tests** (`spark/tests/test_brake.py`; Phase 1's kept):

- `test_a_loading_engine_goes_first_then_idle_models_of_any_class_then_lru` — Gemma ready (0 in
  flight, used T − 10), the embeddings ready (0, T − 100), the coder starting, whisper ready (2 in
  flight, T − 1), the record fresh → `[coder, embed, gemma, whisper]`.
- `test_a_stale_or_missing_activity_record_falls_back_to_phase_1s_order` — written at T − 4 → the
  coder, then Gemma, the embeddings, whisper (on-demand first, then by size); missing → the same.
- `test_an_admitted_coder_load_never_trips_the_rate_watch` — the coder `starting` in the
  activity record (admitted 41 GiB, 74 available at its start), readings every 250 ms falling
  2.5 GiB/s from 74 to 40 → no hold and no unload.
- `test_an_unexplained_fall_at_the_same_rate_trips_it` — the same readings with nothing starting →
  hold and unload at the first reading under 45 (45 − 25 = 20), well above 28.
- `test_a_load_that_runs_past_its_admission_trips_it` — the coder `starting` from 74, the fall
  going on at 2.5 GiB/s past its floor, 31 (74 − 41 − 2) → hold and unload at the first reading
  under 31.
- *(Added 2026-10-08, at Task 11's review, the controller's ruling:)*
  `test_a_started_record_is_bounded_by_what_the_brake_reads` — a `started/` record claiming 74
  available when the brake's own reading at its start was 60 → the floor from 60; one claiming 90
  GiB for the coder → `admitted_gib` the registry's footprint; one whose `pid` is gone, or isn't the
  coder's engine on its port → no load in progress; the gate's record fresh and a record that passes
  these → still counted. The test below gives the brake its own readings from 74 and a `/proc`
  stand-in with the coder's engine at the record's `pid`.
- `test_with_the_gate_down_the_claimed_ticket_gives_the_load` — the activity record stale and
  `launch/started/coder.json` holding 41 GiB, 74 available and its start → as the first test, no
  hold.
- `test_a_stale_started_record_gives_no_floor` — the activity record stale and a `started/` record
  361 s old, or from another boot, with the same fall → hold and unload under 45, as with nothing
  loading.
- `test_a_load_marked_starting_or_started_counts_once` — the coder in both the fresh record and
  `started/` → one load, 41 GiB.
- `test_two_once_runs_with_a_release_between_are_two_episodes` — `--once` against a raised drill
  registry, the hold released, `--once` again → events in episodes 1 and 2, each with the drill's
  `line_gib`; a second `--once` while the first hold stands → the same episode.
- `test_the_floor_uses_the_admitted_footprint` — the coder admitted at 41 with a `cold_load_gib`
  of 33, 3 GiB of other memory moving during its load, falling 74 to 37 → no hold.
- `test_a_slow_fall_doesnt_trip_the_rate_watch` — 0.2 GiB/s from 40 → nothing early; the brake
  acts at 20 as before.
- `test_noise_doesnt_trip_the_rate_watch` — ±0.5 GiB around 30 for a minute → never.
- `test_the_hold_records_the_boot_the_episode_and_the_loading_model` — fired with the coder
  starting → `hold.json` holds the boot id, episode 1 and `loading: coder`.
- `test_each_step_is_recorded_for_the_gate_with_a_rising_seq` — fired and two unloads → three lines,
  `seq` n, n + 1, n + 2; a new brake on the same file continues at n + 3.
- `test_with_the_gate_down_the_brake_sends_brake_fired_itself_without_waiting` — the record stale:
  firing calls `Popen` once, with the arguments above, and its stdin holds the URL, the header
  file, the priority and Task 7's text, none of which is in its argv; a `Popen` stand-in that never
  finishes doesn't slow the tick; the event says `sent_by_brake: true`.
- `test_with_the_gate_up_the_brake_sends_nothing_itself` — the record fresh → no `Popen`;
  `sent_by_brake: false`.
- `test_finished_alerts_are_reaped` — a finished stand-in is polled and dropped on the next tick.
- `test_a_quote_in_the_text_is_escaped_for_curls_config` — a text holding `"` and `\` → the
  config's `data-binary` line holds them escaped, and reads back as the text.
- `test_once_waits_for_its_alert_before_exiting` — `--once`, the record stale, a `Popen` stand-in
  that finishes after 0.2 s → the run returns after it, not before.
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
- [ ] **Step 2:** the brake; the tests pass; `make test lint`.
- [ ] **Step 3: Commit.** **On the Spark:**

```bash
git add spark/src/spark/brake.py spark/tests/test_brake.py
git commit -m "feat(spark): 🤖 the brake unloads idle models first, watches the rate of fall, and alerts by itself while the gate is down" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 24 [Spark]: render — llama-swap's config and unit

**Files:**

- Create: `spark/src/spark/schema.py`, `stack/llama-swap/config-schema.v257.json`,
  `spark/tests/test_schema.py`
- Modify: `spark/src/spark/render.py`, `stack/templates/local-ai-llama-swap.service`,
  `stack/models.yaml` (whisper's `--tmp-dir /var/lib/local-ai/whisper-tmp`; Gemma's
  `--slot-prompt-similarity` only if Step 3 adopts it), `spark/src/spark/apply.py`
  (`validation_env`), `spark/tests/test_apply.py` (its two tests of `KEY_ENVS` move to
  `INTERNAL_KEY_ENVS`), `spark/tests/test_render.py`, `spark/tests/test_stack_registry.py`,
  `website/design/plan.md` (Step 3's ruling, and a Revisions line)

**Interfaces:**

- `render.LLAMASWAP_LISTEN = "127.0.0.1:900"`, `ENGINE_START_PORT = 800` (Task 12 made
  `HEALTH_CHECK_TIMEOUT_S`, 180), `INTERNAL_KEY_ENVS = ("LLAMASWAP_KEY_FRONT", "LLAMASWAP_KEY_GATE",
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
  `PrivateDevices=` or `MemoryDenyWriteExecute=`; `OnFailure=local-ai-notify@%n.service`;
  `After=nvidia-cdi-refresh.service nvidia-persistenced.service`, since on this box DGX OS's
  `nvidia-cdi-refresh.service` loads `nvidia-uvm`, and under `NoNewPrivileges=` an engine can't
  load it itself (bootstrap's `/etc/modules-load.d/` stays Task 39's fallback).
- `schema.check(config: dict, schema: dict) -> list[str]` — every keyword v257's file uses:
  `type`, `properties`, `additionalProperties`, `required`, `enum`, `items`, `minimum`, local
  `$ref`, `allOf`, `if`/`then`, `not`, `oneOf`, `propertyNames`, `pattern`, `minLength`,
  `maxLength`, `minItems`, `minProperties`, `uniqueItems`, `dependencies` (both its array and its
  schema form), and `format`, read as a note, not checked; the annotations `$schema`, `$id`,
  `title`, `description`, `default` and `definitions` are skipped; any other keyword fails closed,
  naming it. One line per problem with its path; render refuses a config with any. The vendored
  file is `https://raw.githubusercontent.com/mostlygeek/llama-swap/v257/config-schema.json`, whose
  SHA-256 read `d75ebf1e18806194ce674ebd59cb70b6f8bf93cb6ec65c2392e0e17fc9d0b076` on 2026-10-07:
  Step 1 compares it, and records it in `test_schema.py`.
- `--slot-prompt-similarity`: read b11146's code for what it does to a slot's choice, weigh it for
  Gemma's two slots (Phase 1's council: at 0.10 a short new prompt can take a long chat's slot and
  its cache), and put it to the controller, who rules and tells Dan (*Values the box decides*);
  only then does `ALLOWED` gain `("-sps", "--slot-prompt-similarity")` and Gemma's `args` the
  value, with a comment saying why.

**Tests** (`spark/tests/test_render.py`, `test_schema.py`, `test_stack_registry.py`):

- `test_llama_swap_listens_on_900_and_the_engines_start_at_800` — the unit's `ExecStart` ends
  `-listen 127.0.0.1:900`; the config's `startPort` is 800.
- `test_render_refuses_engine_ports_that_would_reach_900` — 101 stand-in models → a `RenderError`
  naming 900; 100 → renders.
- `test_llama_swap_takes_only_the_three_internal_keys` — `apiKeys` is exactly the three references.
- `test_every_llama_cpp_engine_answers_as_its_registry_name` — each llama.cpp `cmd` holds `--alias
  <name>`; whisper's holds no `--alias`.
- `test_alias_is_render_owned_in_every_spelling` — `args: [-a, x]` or `[--alias, x]` → refused.
- `test_llama_swap_binds_below_1024_with_only_cap_net_bind_service` — `AmbientCapabilities` and
  `CapabilityBoundingSet` are each exactly `CAP_NET_BIND_SERVICE`.
- `test_llama_swap_is_sandboxed_without_private_devices_or_mdwe` — the eight settings above
  present; `PrivateDevices` and `MemoryDenyWriteExecute` absent.
- `test_llama_swap_restarts_always_and_alerts_on_failure` — `Restart=always`, `RestartSec=5`,
  `OnFailure=local-ai-notify@%n.service`.
- `test_llama_swap_starts_after_what_loads_nvidia_uvm` — `After=` names
  `nvidia-cdi-refresh.service` and `nvidia-persistenced.service`.
- `test_only_the_tickets_folder_whispers_tmp_and_the_caches_are_writable_to_llama_swap` —
  `ReadWritePaths` is exactly those four. *(Added 2026-10-08, at Task 11's review, the controller's
  ruling: the fourth is launch's whole folder, `/var/lib/local-ai/launch`, never only
  `launch/tickets`: launch, inside the sandbox, renames in `tickets/` and writes `started/` and
  `refusals/`; a narrower path would refuse every start as `start_unrecorded` or leave refusals
  unrecorded. The test asserts the folder, not a subfolder.)*
- `test_llama_swap_reads_only_internal_keys_env` — its one `EnvironmentFile` is
  `internal-keys.env`.
- `test_whisper_writes_its_temporary_files_outside_the_hf_cache` (stack registry) — its `--tmp-dir`
  is `/var/lib/local-ai/whisper-tmp`.
- `test_the_rendered_config_passes_v257s_schema` — `schema.check` of the real registry's config →
  `[]`.
- `test_a_mistyped_group_key_fails_the_schema_check` — a group with `swapp: false` → a problem
  naming `swapp`.
- `test_a_wrong_enum_fails_the_schema_check` — `logToStdout: everything` → a problem naming it.
- `test_an_unknown_keyword_fails_closed` — a schema holding `dependentSchemas` → a problem naming
  it, whatever the config; one holding only the listed keywords and annotations → none.
- `test_dependencies_are_checked_in_both_forms` — `cors` with a `dependencies` array whose named
  property is missing → a problem; a schema form not met → a problem.
- `test_the_vendored_schema_is_v257s` — the file's SHA-256 is the recorded one.

**Steps:**

- [ ] **Step 1:** vendor the schema: **on the Spark**, fetch it from the tag above into
  `stack/llama-swap/`, check its SHA-256 against the one above (anything else: stop, and tell the
  controller), and record it in the test.
- [ ] **Step 2:** the failing tests; run them: they fail (port 9100, `KEY_ENVS`, no schema check).
- [ ] **Step 3:** weigh `--slot-prompt-similarity`, as above; the controller rules, and tells Dan;
  record the ruling in plan.md (the Phase 2a line's item, with a Revisions line).
- [ ] **Step 4:** render, the unit, the registry's tmp-dir; the tests pass; `make test lint`.
- [ ] **Step 5: Commit.** **On the Spark:**

```bash
git add spark/src/spark/schema.py stack/llama-swap/config-schema.v257.json spark/tests/test_schema.py \
  spark/src/spark/render.py spark/src/spark/apply.py spark/tests/test_apply.py stack/templates/local-ai-llama-swap.service \
  stack/models.yaml spark/tests/test_render.py spark/tests/test_stack_registry.py website/design/plan.md
git commit -m "feat(spark): 🤖 llama-swap on 127.0.0.1:900, its engines from 800, internal keys only, sandboxed" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 25 [Spark]: render — the new units, the brake's drill among them; the polkit rule's seven

**Files:**

- Create: `stack/templates/local-ai-front.socket`, `local-ai-front.service`,
  `local-ai-gate-status.socket`, `local-ai-gate-control.socket`, `local-ai-gate.service`,
  `local-ai-notify@.service`, `local-ai-brake-drill.service`; `spark/tests/test_units.py`
- Modify: `spark/src/spark/render.py` (`UNITS`, `POLKIT_UNITS`, the notifier's priorities),
  `stack/templates/local-ai-brake.service`, `stack/templates/local-ai-pull.service`,
  `stack/host/50-local-ai.rules`, `stack/host/bootstrap.sh` (`ROOT_UNITS`, `ENABLED_UNITS`,
  `install_units`), `spark/tests/test_polkit.py`, `spark/tests/test_bootstrap.py`,
  `spark/tests/test_render.py`

**Interfaces:**

- `render.UNITS` — `local-ai-llama-swap.service`, `local-ai-brake.service`,
  `local-ai-compose.service`, `local-ai-pull.service`, `local-ai-front.socket`,
  `local-ai-front.service`, `local-ai-gate-status.socket`, `local-ai-gate-control.socket`,
  `local-ai-gate.service`, `local-ai-notify@.service`, `local-ai-brake-drill.service`.
  `render.POLKIT_UNITS` — the seven services: llama-swap, the brake, compose, the pull, the front,
  the gate, and the brake's drill.
- `local-ai-front.socket` — `ListenStream=127.0.0.1:9100`, `FileDescriptorName=front`,
  `NoDelay=yes`, `TriggerLimitIntervalSec=0`, `Service=local-ai-front.service`;
  `WantedBy=sockets.target`.
- `local-ai-front.service` — `Type=notify`, `WatchdogSec=30`, `User=spark-front`,
  `Group=spark-front`, `SupplementaryGroups=spark-users`, `ExecStart=/opt/local-ai/app/.venv/bin/spark
  front`, `Environment=SPARK_REGISTRY=/opt/local-ai/etc/models.yaml`,
  `Environment=SPARK_LLAMASWAP_URL=http://127.0.0.1:900`,
  `LoadCredential=client-keys:/etc/local-ai/secrets/client-keys.sha256`,
  `LoadCredential=llamaswap-key:/etc/local-ai/secrets/front.key`,
  `LoadCredential=keys:/etc/local-ai/keys.yaml` (the private key list, Task 4), `Restart=always`,
  `RestartSec=2`,
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
  `LoadCredential=ntfy-token:…/ntfy-gate.header`, `LoadCredential=keys:/etc/local-ai/keys.yaml`,
  `EnvironmentFile=/etc/local-ai/values.env`, `Environment=` `SPARK_REGISTRY`, `SPARK_STATE`,
  `SPARK_GATE_STATE`, `SPARK_LAUNCH`, `SPARK_LLAMASWAP_URL=http://127.0.0.1:900`, `HF_HOME`,
  `Restart=always`, `RestartSec=2`, `StartLimitIntervalSec=0`, `OOMScoreAdjust=-900`,
  `TimeoutStopSec=30`, `After=` llama-swap, `OnFailure=local-ai-notify@%n.service`; sandbox:
  `NoNewPrivileges=yes`, `ProtectSystem=strict`, `ReadWritePaths=/var/lib/local-ai/gate
  /var/lib/local-ai/launch /var/lib/local-ai/brake`, `ProtectHome=yes`, `PrivateTmp=yes`; no
  `PrivateDevices=`, `ProtectProc=` or `ProcSubset=`; `WantedBy=multi-user.target`.
- `local-ai-notify@.service` — `Type=oneshot`, `DynamicUser=yes`, `StateDirectory=local-ai-notify`,
  `LoadCredential=ntfy-token:…/ntfy-notify.header`, `EnvironmentFile=/etc/local-ai/values.env`,
  `Environment=NOTIFY_FRONT=<p> NOTIFY_GATE=<p> NOTIFY_BRAKE=<p> NOTIFY_LLAMA_SWAP=<p>` (the
  registry's priorities for `front_down`, `gate_down`, `brake_down`, `llama_swap_down`; so a change
  to one of these four needs `make install-units` after `make apply`, which the generated
  notifications page says beside them), `ExecStart=/usr/local/libexec/local-ai-notify %i` (Task
  26's script, which bootstrap installs, Task 27); `NoNewPrivileges=yes`,
  `ProtectSystem=strict`, `ProtectHome=yes`, `PrivateTmp=yes`, `PrivateDevices=yes`; no
  `[Install]`.
- The brake's unit — `LoadCredential=llamaswap-key:…/brake.key`,
  `LoadCredential=ntfy-token:…/ntfy-brake.header`, `EnvironmentFile=/etc/local-ai/values.env`,
  `ExecStart=… spark brake --key-credential llamaswap-key`,
  `Environment=SPARK_GATE_STATE=/var/lib/local-ai/gate`,
  `Environment=SPARK_LLAMASWAP_URL=http://127.0.0.1:900`, `StartLimitIntervalSec=0`,
  `OnFailure=local-ai-notify@%n.service`; no `llama-swap.env`.
- `local-ai-brake-drill.service` — the S05 drill (Task 48), as Phase 1's drill ran `spark brake
  --once` against a copy of the registry: rendered from the same source as the brake's unit, so
  its `User=`, `Group=`, credentials, values file, environment and sandbox can't drift from the
  brake's, but `Type=oneshot`, `ExecStart=… spark brake --once --key-credential llamaswap-key`,
  `Environment=SPARK_REGISTRY=/opt/local-ai/etc/brake-drill.yaml` and
  `ConditionPathExists=/opt/local-ai/etc/brake-drill.yaml`; no `Restart=`, `OnFailure=` or
  `[Install]`. `/opt/local-ai/etc` is `root:spark-admin 2775` (Phase 0), so Dan writes the copy,
  `spark` reads it, and `agent` can't write it.
- The pull's unit — `User=spark-pull`, `Group=spark-pull`, `UMask=0027`,
  `XDG_CACHE_HOME=/var/lib/local-ai/pull-cache`, `CUDA_CACHE_PATH` gone.
- `bootstrap.sh`: `ROOT_UNITS` is `render.UNITS`; `ENABLED_UNITS` every unit with an `[Install]`;
  `install_units` leaves template names (`…@.service`) out of its `systemctl show
  --property=NeedDaemonReload` call, since systemd 255 refuses one and says nothing for the units
  after it; its polkit `say` line names the seven services.

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
- `test_keys_and_tokens_arrive_by_loadcredential_never_the_environment` — each credential as above,
  the key list's among them; no `EnvironmentFile=` of the front, the gate, the brake or the notifier
  is under
  `/etc/local-ai/secrets`.
- `test_only_llama_swap_reads_internal_keys_env` — across every rendered unit.
- `test_the_gate_keeps_dev_nvidia_and_other_processes_visible` — no `PrivateDevices=yes`,
  `ProtectProc=` or `ProcSubset=` in the gate's unit.
- `test_onfailure_is_on_the_four_services` — the front, the gate, the brake and llama-swap; not
  compose, the pull or the drill.
- `test_the_drill_unit_is_the_brakes_but_once_on_the_drill_copy` — the drill's `User=`, `Group=`,
  `LoadCredential=`, `EnvironmentFile=` and sandbox lines equal the brake's; `Type=oneshot`; its
  `ExecStart=` ends `brake --once --key-credential llamaswap-key`; `SPARK_REGISTRY` and
  `ConditionPathExists=` name `/opt/local-ai/etc/brake-drill.yaml`; no `Restart=`, `OnFailure=` or
  `[Install]`.
- `test_graceful_shutdown_fits_inside_timeout_stop_sec` — `TimeoutStopSec=30` on the front and the
  gate, above `gateproto.GRACEFUL_S`, 20, which both mains pass.
- `test_the_new_services_score_minus_900` — `OOMScoreAdjust=-900` on the front and the gate.
- `test_the_notifier_runs_ubuntus_curl_as_a_dynamic_user` — its unit as above.
- `test_the_notifier_carries_the_registrys_priorities` — `front_down: off` in the registry →
  `NOTIFY_FRONT=off`; the others `high`.
- `test_the_pull_runs_as_spark_pull_with_umask_0027` — as above.
- `test_the_brake_reads_its_key_by_credential` — as above.
- `test_every_rendered_unit_passes_systemd_analyze_verify` — skipped where `systemd-analyze` is
  missing: `systemd-analyze verify` on each, with its output holding no complaint but a missing
  binary or user this machine lacks.

`spark/tests/test_polkit.py`, `test_bootstrap.py`:

- `test_spark_admin_starts_stops_and_restarts_the_seven_services` — yes for each of the seven, the
  drill's included, each verb.
- `test_the_sockets_and_the_notifier_are_not_on_the_rule` — `start` on each socket and on
  `local-ai-notify@local-ai-gate.service` → not handled.
- `test_the_rule_names_exactly_the_services_render_writes` — the rule's list is
  `render.POLKIT_UNITS`.
- `test_install_units_installs_and_enables_the_sockets` — `ROOT_UNITS` is `render.UNITS`;
  `ENABLED_UNITS` holds the three sockets, the front and the gate, and not the notifier, the pull
  or the drill.
- `test_install_units_never_asks_systemctl_show_about_a_template` — the dry run's `systemctl show`
  line names no `@.service`, and the drill's unit is in it.

**Steps:**

- [ ] **Step 1:** the failing tests; run them: they fail (the templates missing).
- [ ] **Step 2:** the templates, render, the rule, bootstrap's lists; the tests pass; `make test
  lint`.
- [ ] **Step 3: Commit.** **On the Spark:**

```bash
git add stack/templates/local-ai-front.socket stack/templates/local-ai-front.service \
  stack/templates/local-ai-gate-status.socket stack/templates/local-ai-gate-control.socket \
  stack/templates/local-ai-gate.service stack/templates/local-ai-notify@.service \
  stack/templates/local-ai-brake-drill.service \
  stack/templates/local-ai-brake.service stack/templates/local-ai-pull.service stack/host/50-local-ai.rules \
  stack/host/bootstrap.sh spark/src/spark/render.py spark/tests/test_units.py \
  spark/tests/test_polkit.py spark/tests/test_bootstrap.py spark/tests/test_render.py
git commit -m "feat(stack): 🤖 units for the front, the gate, the failure notifier and the brake's drill; the polkit rule's seven" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 26 [Spark]: the failure notifier's script

**Files:**

- Create: `stack/host/local-ai-notify`, `spark/tests/test_notifier_script.py`
- Modify: `Makefile` (`lint` shellchecks the new script)

**Interfaces:**

- `stack/host/local-ai-notify` (POSIX `sh`): its argument the failed unit; the type and the words
  from it (Task 7's `*_down` sentences, word for word); the result in words from
  `$MONITOR_SERVICE_RESULT` (`exit-code`, `signal`, `core-dump` → *it crashed*; `watchdog` → *it
  stopped answering*; `oom-kill` → *it ran out of memory*; `timeout` → *it timed out*; anything
  else → *it failed*); the time, `date +%H:%M`; nothing sent for a priority of `off`; at most one
  alert per unit per `NOTIFIER_EVERY_S` (300), kept as a stamp file in `$STATE_DIRECTORY`; then
  `"${CURL:-/usr/bin/curl}" --fail --silent --show-error --max-time 10 --config -`, its config on
  stdin: the `url` (`$NTFY_URL/$NTFY_TOPIC_NOTIFY`), `header = "@$CREDENTIALS_DIRECTORY/ntfy-token"`,
  `header = "Priority: <p>"` and `data-binary`, each value in curl's config quoting with `"` and
  `\` escaped, so neither the address, the topic nor the text is on a command line; never a
  journal line. (`CURL` is for the tests; the unit never sets it.)

**Tests** (`spark/tests/test_notifier_script.py`; the script run with a stand-in `curl` that records
its arguments and data, a stand-in `date`, and temporary credential and state folders):

- `test_the_notifier_sends_unit_result_and_time_only` — `local-ai-gate.service`, `exit-code`, 09:14
  → one `curl`, its arguments as above, `Priority: high`, its data Task 7's `gate_down` text.
- `test_each_result_reads_in_words` — the five results, and an unknown one, give their words.
- `test_the_notifier_sends_at_most_one_alert_per_unit_per_5_minutes` — the gate twice 10 s apart →
  one `curl`; the front then → one; the gate at + 301 s → another.
- `test_a_quote_in_a_value_is_escaped_for_curls_config` — `NTFY_TOPIC_NOTIFY` holding `"` and `\`
  → the config's `url` line holds them escaped.
- `test_the_token_reaches_curl_as_a_header_file_never_argv` — the config on stdin holds
  `@<credentials>/ntfy-token`; neither the arguments nor the config hold the token's text, and the
  arguments hold neither the URL nor the topic.
- `test_an_off_priority_sends_nothing` — `NOTIFY_GATE=off` → no `curl`, exit 0.
- `test_the_script_and_messages_agree` — for each of the four units, the script's text equals
  `messages.notification`'s for the same unit, result and time. *(Added 2026-10-07, at Task 7, the
  controller's ruling: `result_words` is the script's result in words, *it crashed* …, to which the
  words add *; it is restarting*, and *within 2 s* for the brake; and the host is the box's short
  host name.)*

**Steps:**

- [ ] **Step 1:** the failing tests; run them: they fail (the script missing).
- [ ] **Step 2:** the script, `make lint`'s list; the tests pass; `make test lint`.
- [ ] **Step 3: Commit.** **On the Spark:**

```bash
git add stack/host/local-ai-notify spark/tests/test_notifier_script.py Makefile
git commit -m "feat(stack): 🤖 the failure notifier's script: unit, result and time, once per unit per 5 minutes" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 27 [Spark]: bootstrap for 2a — the users, root-only secrets, the cache moves to spark-pull

**Files:**

- Modify: `stack/host/bootstrap.sh`, `spark/tests/test_bootstrap.py`, `spark/src/spark/doctor.py`
  (`secrets_folder`, `spark_folders`), `spark/tests/test_doctor.py`, `spark/src/spark/models.py`,
  `spark/tests/test_models.py`, `website/how-to/secret-files.md`, `website/how-to/bootstrap.md`

**Interfaces:**

- `users_and_groups` gains `spark-front` (system, its own group, no home, `nologin`, in
  `spark-users`) and `spark-pull` (system, its own group, home `/var/lib/local-ai`, `nologin`).
- `directories`: `/etc/local-ai/secrets` becomes `root:root 0700`, and each file in it `root:root
  0600` (root writing in a folder only root controls); `/var/lib/local-ai/gate`,
  `/var/lib/local-ai/launch`, `…/launch/tickets`, `…/launch/started`, `…/launch/refusals` and
  `/var/lib/local-ai/whisper-tmp` are made `spark:spark 0750`; `/var/lib/local-ai/pull-cache`
  `spark-pull:spark-pull 0750`. `/var/lib/local-ai/hf` leaves the `spark` line: `install -d` sets
  the owner and mode of a folder that exists, so every re-run would hand the moved cache back to
  `spark` and drop its setgid bit. `/usr/local/libexec` and the `user@<uid>.service.d` folders
  (Task 28) are made `root:root 0755` before anything is installed into them, since neither exists
  on this box and `install` doesn't make folders.
- `hf_cache_to_pull` — `/var/lib/local-ai/hf` becomes `spark-pull:spark`, `2750` at the top and its
  folders setgid, so `spark` reads every model file through its group; hf_xet's logs included. It
  runs after `users_and_groups` and `directories`. When the cache is already `spark-pull:spark 2750`
  with setgid folders it does nothing and says so, whatever is running, so a later re-run (Task 39's
  or Task 47's, with the stack up) passes it. Only when there is something to move does it refuse,
  changing nothing, naming why and failing the run, while `local-ai-llama-swap`, `local-ai-brake` or
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
  only *match*; the private key list, `/etc/local-ai/keys.yaml` (Task 4: names, labels, groups and
  accounts; private, not secret), written from `stack/keys.example.yaml` with the digests file's
  names, `0640 root:spark-admin`; making a new client key after 2a, which changes both files and
  needs `make restart-front`; and `llama-swap.env` kept, `0600 root:root`, for
  the rollback until the close (Task 51). Every block in it run first against a temporary folder
  standing in for `/etc/local-ai/secrets`, as Phase 1's were.

**Tests** (`spark/tests/test_bootstrap.py`, on dry runs and stand-ins as Phase 1's, unless named):

- `test_bootstrap_adds_spark_front_in_spark_users_and_spark_pull` — the dry run's `useradd` lines
  for both, and `usermod -aG spark-users spark-front`.
- `test_the_secrets_folder_becomes_root_only_and_its_files_0600` — `install -d -o root -g root -m
  0700 /etc/local-ai/secrets`; for a stand-in folder with two files, each `chmod 0600` and `chown
  root:root`.
- `test_the_gate_launch_and_whisper_tmp_folders_are_sparks` — `install -d -o spark -g spark -m 0750`
  for each of the six; `pull-cache` for `spark-pull`.
- `test_the_cache_moves_to_spark_pull_with_a_setgid_group` — a stand-in cache (folders, files, a
  link) and logging `chown`/`chmod` → `chown -R -P --no-dereference spark-pull:spark`, `chmod 2750`
  on the top and `g+s` on each folder.
- `test_the_cache_move_refuses_while_sparks_units_run` — `systemctl` reporting llama-swap active and
  a cache still `spark:spark` → refused, naming it; nothing changed.
- `test_a_re_run_after_the_move_changes_nothing` — a moved cache (`spark-pull:spark 2750`, setgid
  folders), llama-swap active → the step says it's done, changes nothing, and the run exits 0.
- `test_directories_never_name_the_cache` — no `install -d` line of the dry run names
  `/var/lib/local-ai/hf`.
- `test_the_folders_installs_need_are_made_first` — `install -d -o root -g root -m 0755
  /usr/local/libexec` comes before the notifier's `install`.
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
  brake first, keep a second SSH session open while it runs, since it changes sshd's PAM stack, then
  `make bootstrap`), their blocks run on stand-ins first.
- [ ] **Step 4: Commit.** **On the Spark:**

```bash
git add stack/host/bootstrap.sh spark/tests/test_bootstrap.py spark/src/spark/doctor.py spark/tests/test_doctor.py \
  spark/src/spark/models.py spark/tests/test_models.py website/how-to/secret-files.md website/how-to/bootstrap.md
git commit -m "feat(host): 🤖 bootstrap for 2a: spark-front and spark-pull, root-only secrets, the cache moves to spark-pull" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 28 [Spark]: agent's processes get an OOM score root sets

**Files:**

- Create: `stack/host/local-ai-agent-oom` (the `pam_exec` script), `stack/host/agent-oom.conf` (the
  `user@` drop-in)
- Modify: `stack/host/bootstrap.sh`, `spark/tests/test_bootstrap.py`, `Makefile` (`lint`),
  `website/how-to/bootstrap.md`; and, only if the controller's ruling below changes them,
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
  earlyoom picks it first. The reasoning goes in a comment beside the value. The value, and any
  change to the engines' scores or to `earlyoom.default`, is the controller's ruling, and Dan is
  told (*Values the box decides*). A change to `earlyoom.default` is said in the task's report,
  since Task 38's rollback runs Phase 1's doctor, whose `earlyoom` check then fails as well.

**Tests** (`spark/tests/test_bootstrap.py`):

- `test_bootstrap_installs_the_hook_for_sshd_and_agents_user_manager` — the dry run installs the
  script, appends the `pam_exec` line only if it is missing, writes the drop-in with the value,
  and reloads systemd.
- `test_the_hook_acts_only_for_agent` — the script run with a stand-in `/proc`: `PAM_USER=chendaniely`
  → writes nothing and exits 0 at once; `agent` and `open_session` → writes the value; `agent` and
  `close_session` → nothing; and the line bootstrap appends is `session optional`, so a failure of
  the script never stops a login.
- `test_the_hook_and_drop_in_are_root_owned_and_not_agents` — the dry run's owners and modes.
- `test_the_values_put_a_large_agent_job_first` — from `launch`'s values, earlyoom's `--prefer`
  bonus and `AGENT_OOM_SCORE_ADJ`: an `agent` process holding 10% of memory scores above every
  engine holding 2%.

**Steps:**

- [ ] **Step 1:** the failing tests; run them: they fail.
- [ ] **Step 2:** the script, the drop-in, bootstrap, the value; the tests pass; `make test lint`.
- [ ] **Step 3: Docs.** `bootstrap.md`: what the hook does, and that Task 40 tests it as `agent`.
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

### Task 29 [Spark]: spark status for 2a

**Files:**

- Modify: `spark/src/spark/status.py`, `spark/tests/test_status.py`, `Makefile` (`status`'s help)

**Interfaces:**

- `spark status [--json]` — a member of `spark-admin` (by `os.getgroups()`) reads
  `GET /v1/status` on the control socket, anyone else on the status socket, both through
  `GateClient` with a 3 s timeout; no key, and no call to llama-swap. It exits 0 whatever it finds.
- `format_status(view: dict, *, tz) -> str` — the plan's layout (*What you see in Phase 2a*), its
  rows in this order: the header (`<host> · <available> GiB available of <total> · <n> above the
  brake's <brake> GiB line`, the total rounded down like *available*, so 121.6 reads 121, as
  `free -g` does), `free for a load` (at 0 or below, `free for a load: none (…)`, never a negative
  number), `loaded`, `not loaded` (with a model's brake mark
  under it), `waiting`, `paused`, `held`, `recent`, `health`; and, only when `unaccounted_gib` is
  above 0, `used by other processes: <n> GiB` under `free for a load`, as plan.md's example has it
  (`used by other processes: 26 GiB`). Cold residents hold less than their footprints, so the
  formula goes below 0 at idle; then there is no line, and `--json` keeps the formula's value. The
  words for what the example doesn't show:
  - paused, automatic: `paused      since 03:12 (19.6 GiB available) · resumes by itself after 5
    min above 28 GiB available`; waiting for Dan: `paused      since 03:12 (19.6 GiB available) ·
    waits for you: make brake-release`;
  - held: `held        40 GiB for you until 18:00 · spark make-room --done ends it` (`until
    a reboot` without an end);
  - apply's hold, in the `held` row: `held        new requests, for make apply since 14:02 ·
    until the model service answers again`;
  - the oldest request, in a loaded row: `answering 1 (oldest 3 min)` when it is a minute or more;
  - ntfy failing: `ntfy failing since 08:52`;
  - a `waiting` row by its `why`: `memory` → `waiting for memory: needs 41 GiB, 18 free for a
    load`; `brake` → `waiting for the brake's pause to end`; `slot` → `waiting its turn: Gemma is
    loading`; `dan` → the example's `waiting for you: spark load coder`; `restart` → `waiting for
    the model service's restart`; `llama_swap` → `waiting for the model service to answer`;
  - a pin, in the `held` row: `pins the coder until 18:00` (`pins none` without one).
- The gate not answering: the header from `/proc/meminfo` (the brake line from the deployed
  registry), then `gate        not answering: what's loaded, waiting, held or paused is unknown
  until it's back · make doctor`.
- `--json` — the `StatusView` as the gate gave it; with the gate not answering, `{schema: 1, host,
  at, memory: {total_gib, available_gib, brake_gib}, gate: "not answering"}`, so 2b's menu bar has
  a shape for that too.

**Tests** (`spark/tests/test_status.py`; a gate stand-in on a short Unix socket):

- `test_status_prints_the_plans_example_moment_line_for_line` — the `StatusView` of the plan's
  moment (121.6 GiB in all, 48 available, the residents loaded with Gemma answering 1, the coder
  marked from 03:12 seen using 26, `agent` waiting 2 min of 10 for Dan, the brake fired at 03:12 and
  released at 03:40, `agent`'s pi session since 08:40, the two *recent* lines, the key checked at
  09:00, 117 GiB at idle) → exactly the plan's fifteen lines, `used by other processes: 26 GiB`
  among them, copied into the test.
- `test_agents_status_shows_only_its_refusals_and_no_dans_processes` — a view filtered as the
  status socket gives it → no Dan refusal; a holder shown as *a process of Dan's*.
- `test_status_shows_paused_and_held_when_they_stand` — the two `paused` forms and the `held` form
  above, each word for word, a pin in the `held` row, and apply's hold.
- `test_each_waiting_reason_has_its_words` — every reason in `gateproto.WAITING_WHY` → its words
  above, word for word; so a reason added later fails until it has its own.
- `test_unaccounted_memory_is_idle_less_available_less_footprints` — idle 117, 48 available, 64 of
  footprints → `used by other processes: 5 GiB`; 0 → no line.
- `test_cold_residents_print_no_other_processes_line` — idle 117, 83.5 available, the residents'
  43 of footprints holding 33.5, nothing else running → `unaccounted_gib` −9.5 in `--json`, and no
  `used by other processes` line.
- `test_the_oldest_request_shows_from_a_minute` — 3 min → `(oldest 3 min)`; 40 s → nothing added.
- `test_notifications_failing_since_shows` — `failing_since` 08:52 → `ntfy failing since 08:52`.
- `test_with_the_gate_down_status_says_so_and_shows_memory` — `GateUnavailable` → the header from
  memory and the `gate` line above; exit 0; with `--json`, the gate-down shape above.
- `test_nothing_free_for_a_load_reads_none` — `free_for_a_load_gib` −58 → `free for a load: none
  (…)`, and no minus sign anywhere.
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

### Task 30 [Spark]: the commands — load, unload, pin, make-room, release, logs, sessions

**Files:**

- Create: `spark/src/spark/commands.py`, `spark/tests/test_commands.py`
- Modify: `spark/src/spark/cli.py`, `Makefile` (`logs` takes `front`, `gate` and `notify`;
  `brake-release`'s help; `tunnel`'s help, *forward the Spark's model API, 127.0.0.1:9100*, which
  is the front from 2a), `spark/tests/test_makefile.py`, `spark/tests/test_tested_against.py`
  (the import check gains `spark.commands`), `website/how-to/deploy.md` (`spark` on your `PATH`)

**Interfaces:**

- `spark load <model|role>`, `spark unload <model|role>`, `spark pin <model> [duration]`, `spark
  unpin <model>`, `spark make-room <size> [--for <duration>]`, `spark make-room --all`, `spark
  make-room --done`, `spark logs <model|role> [-n N]`, a role resolved as `spark load` resolves it
  (*added 2026-10-07, at Task 6's re-review: the refusals name the role*) — each on the control
  socket; `spark session start --model M --pid P --label L`, `spark session renew <id>`, `spark
  session end <id>` — on the status socket, for 2b's hooks. Each prints Task 7's confirmation, or
  the refusal's message (exit 1). Each registers with `cli.py` with its imports inside its handler,
  as Task 3's test holds it to. *(Added 2026-10-07, at Task 7, the controller's ruling: make-room's
  list is `room_list(…, registry=…)`, the registry the CLI reads, since `Candidate` carries neither
  a model's capability nor its `used_by`; when the plan isn't `enough`, the list ends at its rows
  and `room_too_much` asks.)*
- `spark session hold --model M --label L` (Dan's decision, 2026-10-07, after the forward-and-back
  council) — a session for a process that isn't on the Spark, such as Orca's pi on the Mac: it
  registers its own pid, renews every 60 s, and ends the session when its stdin closes, or on
  SIGTERM or SIGHUP; a Mac hook runs `ssh brightroar spark session hold …` in the background, and
  the session ends when pi exits or the Mac sleeps.
- Each command's client timeout: `GateClient`'s 5 s for `make-room`'s plan, `--done`, `logs`, ~~the
  pins without a load~~ `unpin` *(corrected 2026-10-08, at Task 10's second re-review: every
  `spark pin` now reads `/v1/pin` as a stream, since the CLI can't tell beforehand whether it loads)*
  and the sessions; ~~the load call's 200 s plus 10 for `load` and for a `pin`
  that loads;~~ ~~none for `unload` and for `make-room`'s unloads, whose drains wait for their requests~~
  — `unload` reads `/v1/unload`'s stream, printing the count's sentence at once and the result when
  it comes.
  *(Corrected 2026-10-08, the controller's rulings, at Task 10's review:)*
  - **Why not none.** "None" left the call unbounded from the connect on, so on a socket systemd
    holds while the gate can't start, `spark unload` would wait for ever, silently.
  - **`unload` and `make-room`** call `GateClient(GATE_CONTROL_SOCKET,
    REQUEST_TIMEOUT_S).stream(…, then_s=None)`. The 5 s bound the connect and the answer's head, so
    a gate that is down is `GateUnavailable` within 5 s, and the lines after the head wait as long
    as the drains take.
  - **`make-room`'s stream.** It prints Task 7's `unloading` for each `MakeRoomProgress` line as it
    comes, then `room_held`.
  - *(Added 2026-10-08, at Task 12's re-review, the controller's ruling:)* **A model that stays
    stopping.** `unload` and `make-room` print each `gateproto.StoppingProgress` line as it comes,
    every 15 s while llama-swap hasn't finished its unload. The words are in Task 7's voice, and
    this task adds them to `messages.py`, with their test: *Still stopping the coder: llama-swap
    hasn't finished its unload…*. Ctrl-C ends the wait and the command, never the unload, which
    llama-swap finishes by itself. Its test: `test_unload_says_so_while_the_model_stays_stopping`
    — the stand-in streams two `StoppingProgress` lines before the result → both sentences, then
    *Unloaded the coder.*
  - **Path parameters** — a model, a role or a session id — are quoted with
    `urllib.parse.quote(name, safe="")` before they go in a route.
  - **`parse_size`'s `Decimal`** goes in a body as `float(size)`, since json can't encode a
    `Decimal`.
  *(Added 2026-10-08, the controller's rulings, at Task 10's re-review:)*
  - **`load`** calls `GateClient(GATE_CONTROL_SOCKET, REQUEST_TIMEOUT_S).stream("/v1/load", …,
    then_s=LOAD_CALL_TIMEOUT_S + 10)`. A gate that is down shows within 5 s. As the load starts, it
    prints `load_started`'s words for its `LoadProgress` line, *Loading the coder (24 s last
    time)…*, then `loaded`'s confirmation.
  - **A stream that ends without its result line** is `GateUnavailable`, worded *The gate stopped
    partway through …; on the Spark, `make doctor` shows what's wrong.* It is never a silent
    success. The client already takes a stream cut off partway (no last chunk) as
    `GateUnavailable`; this covers one the gate ended cleanly but short.
  - **A refusal sent as a stream line** is recognized by `GateRefusalBody`'s shape (`message` and
    `code`), raised as `GateRefused`, and printed as the refusal, exit 1.
  *(Added 2026-10-08, the controller's ruling, at Task 10's re-review, on its fix round 2:)*
  - **`pin`** calls `GateClient(GATE_CONTROL_SOCKET, REQUEST_TIMEOUT_S).stream("/v1/pin", …,
    then_s=LOAD_CALL_TIMEOUT_S + 10)`, as `load` does, since it can't know beforehand whether the
    pin loads. A pin that loads streams its `LoadProgress`, and `pin` prints `load_started`'s words,
    *Loading the coder (24 s last time)…*, before `pinned`'s. A pin on a model already loaded
    answers once, which `stream()` reads as its one line, and `pin` prints `pinned`'s words alone.
    Either way a gate that is down shows within 5 s.
  *(Added 2026-10-08, the controller's rulings, at Task 10's second re-review:)*
  - **A queued load says what it waits for.** `load`, and a `pin` that loads, print a line for
    each `WaitProgress`, in Task 7's `waiting` words for its reason, as Dan's own command words
    them (no key, and no wait to give). For example, *The coder is waiting its turn to load: Gemma
    is loading, and one model loads at a time…* or *The coder is waiting for memory: it needs
    41 GiB, and 18 GiB is free for a load…*. The brake's reason takes `held_by_brake`'s words for
    Dan. A `messages` function of Task 30's own holds them, with its test.
  - **The result line ends it.** Each streamed command stops reading at its result line, or a
    refusal line, and closes the stream there. So a cut after the result is never shown as an
    error after a success.
- A question (`make-room`'s confirmation) is asked only on a terminal: without one it exits 2,
  saying to run it in a terminal or add `--yes`; `--yes` answers yes.
- `parse_size(text) -> Decimal` — `41G`, `41GiB`, `41` → 41; `parse_duration(text) -> int` — `8h`
  → 28800, `90m` → 5400, `2d` → 172800; anything else a `ValueError` naming it.
- `make logs s=front|gate|notify` — `journalctl -u local-ai-front.service`, `-u
  local-ai-gate.service`, and `-u 'local-ai-notify@*'`.
- `deploy.md`: bare `spark` on the Spark is the deployed one, linked once into `~/.local/bin`.

**Tests** (`spark/tests/test_commands.py`; a gate stand-in answering each route as Task 10 gives
it):

- `test_load_says_loaded_in_n_seconds_and_how_to_keep_it` — the stand-in's `{seconds: 24}` →
  *Loaded the coder in 24 s. It unloads after 60 min idle; `spark pin coder` keeps it.*; a refusal →
  its message, exit 1. *(Added 2026-10-08, at Task 10's re-review:)* the stand-in streams `{model,
  label, last_s: 24}` first, and *Loading the coder (24 s last time)…* comes before the
  confirmation; a refusal line ends it with its message, exit 1.
- *(Added 2026-10-08, at Task 10's re-review:)*
  `test_a_stream_that_ends_without_its_result_says_the_gate_stopped` — `/v1/unload`'s stream ends
  cleanly after `{inflight: 1}` → *The gate stopped partway …*, exit 1, never *Unloaded the coder.*
- `test_unload_waits_for_requests_in_flight_and_says_so` — *Unloading the coder once its 1 request
  in flight finishes…*, then *Unloaded the coder.*
- `test_pin_with_a_duration_says_until_when_and_how_to_end_it` — `pin coder 8h` at 10:00 → the
  stand-in got `until` 18:00; *The coder stays loaded until 18:00 (loaded it first, 24 s). `spark
  unpin coder` ends the pin.* *(Added 2026-10-08, at Task 10's re-review:)* the stand-in streams
  `{model, label, last_s: 24}` first, and *Loading the coder (24 s last time)…* comes before the
  pin's sentence.
- *(Added 2026-10-08, at Task 10's re-review:)* `test_a_pin_on_a_model_already_loaded_says_only_the_pin`
  — the stand-in answers once, `{label, until, loaded_s: null, command}` → *The coder stays loaded
  until 18:00. `spark unpin coder` ends the pin.*, with no *Loading* line; the gate down →
  `GateUnavailable`'s sentence within 5 s, exit 1.
- `test_unpin_says_when_it_unloads` — *The pin on the coder ended; it unloads after 60 min idle.*
- `test_make_room_shows_the_list_and_asks_once` — the plan's make-room example → the plan's list
  block, word for word, then `Unload 1? [y/N]`; `y` → `POST /v1/make-room`, then the plan's
  *Unloaded the coder. 50 GiB is free for a load, …* sentence; anything else → nothing posted, exit
  1. *(Added 2026-10-08, at Task 10's re-review, the controller's ruling:)* the stand-in streams the
  coder's `MakeRoomProgress` (`inflight: 0`) first, and *Unloading the coder…* comes before the
  *Unloaded the coder. …* sentence, as plan.md's command table now shows.
- `test_make_room_asked_too_much_offers_the_most` — *Unloading everything leaves 61 GiB free for a
  load, not 70. Free 61 and hold it? [y/N]*
- `test_make_room_for_sets_its_end` — `--for 8h` → `for_s` 28800.
- `test_make_room_all_and_done` — `--all` → `{all: true}`, the plan's `--all` sentence after one
  question; `--done` → `POST /v1/release` with `{room: true}` only, and from its answer's `room`
  fields, *Hold ended, all 40 GiB of it unused. Nothing to reload: the coder loads on its next
  request.* *(Added 2026-10-08, the controller's rulings, at Task 10's second re-review:)* the stand-in streams a `MakeRoomProgress` for each
  model `--all` unloads, and an *Unloading …* line comes for each before *Unloaded everything. …*,
  as plan.md's `--all` row now shows.
- *(Added 2026-10-08, the controller's rulings, at Task 10's second re-review:)* `test_a_cut_after_the_result_is_never_shown_as_an_error` — the
  stand-in streams `{inflight: 1}`, `{unloaded: true}`, then cuts the connection with no last chunk
  → *Unloading the coder once its 1 request in flight finishes…*, *Unloaded the coder.*, exit 0,
  and no error line.
- *(Added 2026-10-08, the controller's rulings, at Task 10's second re-review:)* `test_a_queued_load_says_what_it_waits_for` — the stand-in streams a
  `WaitProgress` (`why: "slot"`, `loading_label: "Gemma"`), then a `WaitProgress` (`why:
  "memory"`, 41 needed, 18 free), then the `LoadProgress`, then the result → *The coder is waiting
  its turn to load: Gemma is loading, …*, *The coder is waiting for memory: …*, *Loading the coder
  (24 s last time)…*, *Loaded the coder in 24 s. …*, in that order.
- `test_a_question_without_a_terminal_refuses_without_yes` — stdin not a terminal → exit 2 and the
  sentence; `--yes` → proceeds.
- `test_logs_reads_through_the_control_socket` — `logs coder -n 5` → `GET /v1/logs/coder?n=5`, each
  line printed, escaped.
- `test_session_commands_use_the_status_socket` — `start` → `POST /v1/sessions` on the status
  socket, the id printed; `renew` and `end` likewise.
- `test_session_hold_lives_as_long_as_its_stdin` — `session hold` in a subprocess: a `POST
  /v1/sessions` naming its own pid; a renewal after 60 s (injected); stdin closed → `DELETE`, exit
  0; in another run, SIGHUP → `DELETE`, exit 0.
- `test_each_command_waits_as_long_as_its_route` — `load` against a stand-in holding the load 6 s
  (the client timeout scaled down) → the confirmation, not *the gate isn't answering*; `unload`
  printing its count before the stand-in's result arrives.
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
git commit -m "feat(spark): 🤖 spark load, unload, pin, make-room, release, logs and sessions, through the gate's sockets" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 31 [Spark]: spark apply, 1 — the diff, and what each change restarts

**Files:**

- Modify: `spark/src/spark/apply.py`, `spark/tests/test_apply.py`, `Makefile` (`restart-front`)

**Interfaces:**

- `diff_text(files: dict[str, str], etc: Path, installed: dict[str, str | None]) -> str` — a
  unified diff of each file that changes: what `apply` deploys itself against `etc`, root's staged
  copies against root's installed ones; the app by file name.
- `RestartPlan(now: list[str], after_quiet: list[str])`; `plan_restarts(changed: list[str],
  app_changes: list[str], registry_old, registry_new, outdated: list[str], *, restart_front: bool)
  -> RestartPlan` — llama-swap's config or unit → `after_quiet`; the front → `after_quiet` when a
  file of `FRONT_MODULES` (read from `spark.front`, which imports nothing, Task 21) changed, or
  `restart_front`; never for a change to the registry's names or roles, which reach the front
  through the gate's events (Dan's decision, 2026-10-07, after the forward-and-back council); the
  gate and the brake → `now` for any change to the app or the registry; the compose unit → `now`,
  as Phase 1. A unit that isn't running is never restarted.
- `restart_order(units: list[str]) -> list[str]` — a fixed order: the brake, then the gate, then
  the front, then llama-swap last; the compose unit restarts on its own, as Phase 1. When
  `after_quiet` isn't empty, `now`'s brake and gate restart with it, after the drain, in this
  order, since the app's sync comes after the wait.
- `RUNNING_UNITS` gains `local-ai-front.service` and `local-ai-gate.service`.
- `spark apply --restart local-ai-front.service` (`make restart-front`) — for a change of the key
  digests or the key list, which `apply` doesn't deploy; the front restarts through the same
  wait.

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
- `test_a_change_to_the_registrys_names_or_roles_never_restarts_the_front` — a role added, a model
  renamed, a footprint changed → the front in neither list (the gate and the brake in `now`).
- `test_llama_swaps_restart_waits_for_the_quiet_moment` — `llama-swap.yaml` changed → llama-swap in
  `after_quiet`.
- `test_restart_front_waits_too` — `restart_front=True` → the front in `after_quiet`.
- `test_a_unit_that_isnt_running_is_never_restarted` — the front stopped, its module changed → in
  neither list.
- `test_restarts_follow_a_fixed_order` — llama-swap, the front, the gate and the brake, given in any
  order → the brake, the gate, the front, llama-swap.

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

### Task 32 [Spark]: spark apply, 2 — the quiet wait, drain now and apply-now

**Files:**

- Modify: `spark/src/spark/apply.py`, `spark/tests/test_apply.py`, `Makefile` (`apply`'s and
  `apply-now`'s help), `spark/src/spark/doctor.py` (S17's check), `spark/tests/test_doctor.py`,
  `website/scenarios/s17-changing-models.md` (a dated note: built, the drill in Task 50),
  `website/how-to/deploy.md` (*Every later change*)

**Interfaces:**

- `wait_for_quiet(gate: GateClient, *, quiet_s=QUIET_S, deadline_s=APPLY_DEADLINE_S, clock, say,
  ask) -> "quiet" | "drained" | "declined"` — `GET /v1/quiet` every second; while it waits it says,
  once per change, Task 7's *Waiting for a quiet moment: the coder answered 20 s ago, and it needs
  60 s with nothing in flight. Ctrl-C leaves everything as it was; `make apply-now` restarts now.*
  At the deadline it asks Task 7's *No quiet minute in 15 minutes. Drain now, holding new requests
  while the 2 in flight finish? [y/N]*; yes → `POST /v1/drain-all`, then waits for nothing in
  flight ("drained"); no → "declined". Ctrl-C at any point → nothing changed, exit 130.
  `spark apply --deadline-s <n>`, an option `--help` doesn't list, shortens the 15 minutes for a
  drill (Task 50), so the drill needs no code change and no restart of the front. *(Added
  2026-10-07, at Task 7, the controller's ruling: the question is `apply_no_quiet(inflight,
  deadline_s=<that deadline>)`, so it names the deadline it was given.)*
- Root's files come first, as in Phase 1: when any is pending, apply stages them and stops, exit
  0, before it asks the gate anything (the cutover's Step 2 depends on it).
- The order, when a restart waits for the quiet moment: render and validate; the diff; the wait;
  on the quiet reading, `POST /v1/drain-all` at once (nothing is in flight then, so it costs
  nothing), and on drain now, the same, then the wait for the requests in flight to finish; from
  `drain-all` on, the gate's persisted hold stands: the front holds every new request, admission
  answers `restarting`, never `llama_swap_down`, and nothing unloads; only then the app's sync and
  the files written; `POST /v1/apply/begin` (the units it restarts, saved with the hold); the
  restarts in Task 31's order — the brake, then the gate, then its control socket answering within
  30 s, then the front, only if its own files changed, then llama-swap last; the gate's
  `health.llama_swap` `ok` within 30 s, llama-swap answering again; `POST /v1/apply/end` (the hold
  released, the held requests through admission, the registry re-read, the residents reloaded,
  `apply_restarted` sent); on any failure after `drain-all`, Ctrl-C included, `POST
  /v1/undrain-all`. From `drain-all` to its end, `apply/renew` every `APPLY_RENEW_S` (15 s), from a
  thread of its own, so a long wait or sync never lets it lapse. SIGHUP (a dropped SSH session)
  and SIGTERM end it as Ctrl-C does, with the same cleanup and nothing more written, exiting 129
  and 143. A gate that doesn't answer within 30 s of its restart stops apply, exit 1, naming the
  gate; the hold then lapses at the gate, or the front drops it (Task 22). A hold whose apply died
  some other way lapses 60 s after its last renewal (Task 18).
- `--now` (`make apply-now`) — asks Task 7's *This restarts the model service now and cuts off the
  2 requests in flight (pi on the Mac, agent). Continue? [y/N]*, naming each from `/v1/quiet`;
  without requests in flight it doesn't ask.
- A change that restarts nothing after the quiet moment applies at once.
- With the gate not answering: when llama-swap or the front must restart, it refuses unless
  `--now` (*apply can't see what's in flight while the gate isn't answering; `make apply-now`
  restarts anyway*); when llama-swap isn't running, it deploys without asking the gate (the
  cutover's case, Task 38).
- doctor's S17 check — `GET /v1/quiet` answers.

**Tests** (`spark/tests/test_apply.py`, a gate stand-in; `spark/tests/test_doctor.py`):

- `test_apply_waits_for_60s_with_nothing_in_flight_and_says_what_its_waiting_on` — the coder
  answered 20 s ago → the waiting sentence once; `quiet_for_s` reaching 60 → it goes on.
- `test_ctrl_c_during_the_wait_leaves_nothing_changed` — `KeyboardInterrupt` in the wait → no file
  written, no sync, no restart; exit 130.
- `test_after_15_minutes_apply_offers_drain_now` — never quiet for 900 s (injected clock) → the
  question; `y` → `drain-all`, then on with nothing in flight; `N` → exit 1, nothing changed.
- `test_the_deadline_can_be_shortened_for_a_drill` — `--deadline-s 60`, never quiet → the question
  at 60 s; `--help` doesn't name the option.
- `test_drain_now_holds_new_requests_until_those_in_flight_finish` — after `drain-all`, it waits
  for `/v1/quiet`'s `inflight` to empty; a failure after it → `undrain-all` posted.
- `test_apply_renews_the_hold_while_it_runs` — a drain wait of 100 s (injected clock) →
  `apply/renew` every 15 s from `drain-all` until `apply/end`, then none.
- `test_sighup_and_sigterm_clean_up_like_ctrl_c` — `spark apply` in a subprocess against the gate
  stand-in, sent SIGHUP during the drain wait → `undrain-all` posted, no file written, exit 129;
  SIGTERM → the same, exit 143.
- `test_a_gate_that_fails_at_start_stops_apply` — the gate's socket not answering for 30 s after
  its restart → no further restart, exit 1, the gate named.
- `test_the_quiet_path_holds_new_requests_too` — the quiet reading → `drain-all` posted before the
  sync, the files and the restart.
- `test_root_files_pending_stage_before_any_gate_call` — root's files pending, llama-swap active,
  the gate not answering → staged, exit 0, and no call to the gate.
- `test_nothing_is_written_until_the_drain_is_done` — the sync and the writes come after the wait
  returns, never before.
- `test_apply_now_names_the_requests_it_would_cut_off_and_asks` — two in flight → the question
  naming *pi on the Mac, agent*; `y` → restarts; `N` → nothing.
- `test_the_gate_is_told_before_and_after_the_restart` — `begin` before `systemctl restart`, `end`
  after the gate reports llama-swap `ok`.
- `test_the_restarts_run_in_their_fixed_order` — a change that restarts all four → `systemctl
  restart` for the brake, the gate, the front, llama-swap, in that order; a status call answered
  between the gate's restart and the front's; `begin` before the brake's.
- `test_a_front_whose_files_didnt_change_isnt_restarted` — llama-swap's config and a footprint
  changed → the brake, the gate, llama-swap; no front.
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

### Task 33 [Spark]: spark doctor for 2a

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
- `stack units` — the five services that keep running (llama-swap, the brake, compose, the front,
  the gate) and the three sockets active; the pull and the drill's unit are oneshots that run only
  when asked, and aren't checked.
- `key list` — `/etc/local-ai/keys.yaml` (Task 4) loads, `0640 root:spark-admin`, its groups the
  registry's.
- `version drift` — `llama-swap -version` names v257; `llama-server --version` prints `version:
  0.5.0-dev (build 11146, commit …)`, so b11146 is read as `build 11146`;
  `whisper-server`'s binary is the one under `v1.9.4`; the deployed venv's uvicorn is the lock's;
  each mismatch named.
- `llama-swap config` — the deployed `/opt/local-ai/etc/llama-swap.yaml` against the vendored
  schema.
- `S01` — the registry's on-demand idle time is 60 and the residents never idle-unload; `S02` —
  `POST /v1/make-room/plan` with 1 GiB answers, unloading nothing; `S03` — a made-up model through
  the front gets 404 with `model_not_found`'s words; `S05` — the brake active, its start check
  passed, and the gate's `health.activity_age_s` under 3 (doctor, as you, can't read the gate's
  folder); `S14` — `OnFailure=` and `StartLimitIntervalSec=0` on
  the four, by `systemctl show`, and the notifier's script installed; `S17` (Task 32).
- `a model, end to end` — the embeddings through the front with `SPARK_API_KEY`.
- `bypass` — FAILs only on evidence of a real bypass: the gate's `health.unticketed_engines`, an
  engine running that `spark launch` didn't start with a ticket, named by model, port and pid (the
  design's bypass sweep). `health.no_ticket_refusals`, the starts launch refused for want of a
  ticket since boot, is the backstop working: a note with its count (*3 starts refused for want of
  a ticket since boot: the backstop working*), never a failure.
- `root's copies` — follows `render.UNITS`, all eleven, not Phase 1's four.
- `spark doctor --full` adds: a 170 MB upload to `/v1/audio/transcriptions` through the front; the
  privacy canary — a unique string sent through chat and speech-to-text, then looked for in the
  journal (as you, in group `adm`) and through `POST /v1/canary` (the gate's state, its refusal
  history and launch's records, which only `spark` reads), found in none. The front's spool isn't
  searched: no account but the front sees its private `/tmp`, and Task 20's test pins that each
  upload's file goes when its request ends.

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
  llama-server's `(build 11146, …)` → ok, `(build 11145, …)` → FAIL naming b11145; uvicorn 0.53.0
  deployed → FAIL.
- `test_stack_units_skips_the_oneshots` — the pull and the drill inactive, the five active → ok;
  the front inactive → FAIL naming it.
- `test_the_key_list_must_load` — a key whose group the registry lacks → FAIL naming the key.
- `test_the_deployed_config_is_checked_against_v257s_schema` — `swapp` in it → FAIL naming it.
- `test_each_2a_scenario_has_its_check` — each of the six, failing on its own fault: idle 30 (S01);
  the plan route refusing (S02); the front answering 200 for a made-up model (S03); a stale activity
  record (S05); no `OnFailure=` on the brake (S14); `/v1/quiet` unanswered (S17).
- `test_full_runs_the_upload_and_the_canary` — without `--full`, neither runs; with it, both, and
  the canary found in the journal stand-in → FAIL naming the journal; the canary route answering
  `found: ["refusals"]` → FAIL naming it.
- `test_only_an_engine_without_a_ticket_fails_the_bypass_line` — `unticketed_engines: [{coder, 801,
  4242}]` → FAIL naming the coder, 801 and 4242.
- `test_no_ticket_refusals_are_a_note_never_a_failure` — `no_ticket_refusals` 3, no unticketed
  engine → PASS, with the note and its count.
- `test_roots_copies_follow_render_units` — a missing `local-ai-gate.service` copy → FAIL naming
  it.
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

### Task 34 [Spark]: spark clients — agent's pi waits 15 minutes

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

### Task 35 [Spark]: the deploy runbook — the first deploy, its rollback, the engines' log

**Files:**

- Modify: `website/how-to/deploy.md`, `website/how-to/index.qmd`, `website/how-to/updates.md`

**What the runbook gains:**

- *Before the first deploy*: 2a's secret files (secret-files.md) and the values file (`ntfy.md`).
- *First deploy*, for a new box: the sockets started before the services.
- *Moving a Phase 1 box to 2a*: Task 38's sequence, word for word, and its rollback to Phase 1's
  layout.
- *When something is wrong*: an engine's lines with `spark logs <model>`, in place of Phase 1's
  `curl` to 9100's `/logs/stream/upstream`, which the front now refuses; `make logs
  s=front|gate|notify`.
- *Every later change*: Task 32's.
- *An urgent fix during a phase's build*: nothing deploys from `phase-2a` before the cutover
  (*Deploys during 2a*); the fix is committed on `main`, deployed from a `main` worktree, pushed
  with Dan's OK and merged into `phase-2a` — the block Task 37 runs, on stand-ins first.
- `updates.md`: a uvicorn or Starlette bump runs `test_protocols.py` and `test_sockets.py` first,
  and `make apply` then restarts in Task 31's order; Dependabot's `spark/` bumps wait while a
  phase is being built (*Dependabot during 2a*).

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

## ⇄ Push point — Tasks 1–35 go to GitHub

- [ ] The Spark session runs leak-guards.md's [*Before every push*](../how-to/leak-guards.md#before-every-push)
  with `phase-2a` as `<branch>`; then **Dan OKs the push** (`git push`). `gh run watch`: CI is
  green, and its `tests` job ran the polkit tests that need Node. Task 36 installs the new rule
  only after this run.

***

### Task 36 [Dan]: bootstrap again, and the secret files for 2a

**Files (the session's record):** `changelog.md`, `README.md` (§Current state; §My environment: the
key list), `CLAUDE.md` (*Where that material goes instead*: the key list, `/etc/local-ai/keys.yaml`,
private but not secret); and **[Dan]** the vault's entry note gains it.

- [ ] **Step 1 [Dan, on the Spark]:** `make bootstrap-dry-run`, and read it. Stop the brake and
  llama-swap, which stops every model, since the cache's move refuses while they run:
  `systemctl stop local-ai-brake.service local-ai-llama-swap.service`, which the polkit rule allows
  without sudo. Then `make bootstrap`, with a second SSH session to the box left open until a fresh
  login works afterwards, since bootstrap changes sshd's PAM stack (Task 28; bootstrap.md says so).
  Then start them again: `systemctl start local-ai-llama-swap.service local-ai-brake.service`. The
  stack serves in Phase 1's layout meanwhile, reading the model files through the group.
- [ ] **Step 2 [Spark]: Check it.** **On the Spark:**

```bash
getent passwd spark-front spark-pull | cut -d: -f1,7
id -nG spark-front
stat -c '%U:%G %a %n' /var/lib/local-ai/hf /var/lib/local-ai/gate /var/lib/local-ai/launch /var/lib/local-ai/launch/tickets /var/lib/local-ai/launch/started /var/lib/local-ai/launch/refusals /var/lib/local-ai/whisper-tmp /var/lib/local-ai/pull-cache
test -x /usr/local/libexec/local-ai-notify && echo "notifier: installed" || echo "notifier: missing"
test -x /usr/local/libexec/local-ai-agent-oom && grep -c 'local-ai-agent-oom' /etc/pam.d/sshd || echo "agent's OOM hook: missing"
```

  Expected: both users with `/usr/sbin/nologin`; `spark-front spark-users`; `spark-pull:spark 2750
  /var/lib/local-ai/hf`; `spark:spark 750` for the gate's, launch's (and its three folders) and
  whisper's; `spark-pull:spark-pull 750` for the pull's cache; `notifier: installed`; `1`. **[Dan,
  on the Spark]** `sudo stat -c '%U:%G %a' /var/lib/local-ai/hf/tmp; sudo find /var/lib/local-ai/hf
  -type f ! -perm -g=r | head -3; sudo du -sh /var/lib/local-ai/hf; sudo -k` reads `spark:spark 750`
  (left for whisper until the cutover), no file the group can't read, and about 36 GiB. Then `make
  doctor`: its *secrets folder* and *spark's folders* lines pass; the lines that need the cutover
  (the front, the gate, the sockets, the scenarios) fail, as expected until Task 38; the stack
  serves.
- [ ] **Step 3 [Dan, on the Spark]:** secret-files.md's 2a steps: the three internal keys, each
  written once to its raw file and to `internal-keys.env`; `client-keys.sha256` from the existing
  client keys; each file `0600 root:root`. Its hash checks print only *match*. Then the key list,
  `/etc/local-ai/keys.yaml`, from `stack/keys.example.yaml`, its names the digests file's, `0640
  root:spark-admin`; `spark doctor`'s *key list* line (as the deployed app can't yet run it, the
  clone's `uv run --frozen --project spark spark doctor`) passes for it.
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

### Task 37 [Spark]: cap_drop for Open WebUI and SearXNG, and SearXNG's request timeout — from main, before the cutover

The web containers run as root on the host's network with Docker's default capabilities, so
either could take 127.0.0.1:900 or 800 and up whenever it is free. So this comes before the
cutover (Dan's decision, 2026-10-07, after the forward-and-back council), and since nothing
deploys from `phase-2a` before then (*Deploys during 2a*), it is made in a `main` worktree,
deployed from there as an urgent fix is, pushed with Dan's OK, and merged into `phase-2a`. It
changes only the Compose file and SearXNG's settings, which Phase 1's stack already renders.

**Files** (in the `main` worktree, then merged):

- Modify: `stack/templates/compose.yaml`, `stack/templates/searxng-settings.yml`,
  `spark/tests/test_render.py`, `changelog.md`, `README.md`, `website/architecture.qmd` (both
  labels, built)

**Interfaces:** both services `cap_drop: [ALL]`; `cap_add:` only a capability its container is shown
to need on the box, each with a comment saying what failed without it; SearXNG's
`outgoing.request_timeout` above its slowest engine's measured answer (Phase 1 saw duckduckgo take
2.7 s against 3 s).

**Tests** (`spark/tests/test_render.py`, `main`'s):

- `test_the_web_containers_drop_every_capability` — each service's `cap_drop` is `[ALL]`.
- `test_each_capability_added_back_carries_its_reason` — every `cap_add` entry has a comment on its
  line.
- `test_searxng_waits_longer_than_its_slowest_engine` — `outgoing.request_timeout` is above 2.7.

**Steps:**

- [ ] **Step 1: The worktree.** **On the Spark**, from the `phase-2a` clone:

```bash
git worktree add ../local-ai-main main && git -C ../local-ai-main pull --ff-only
```

- [ ] **Step 2:** in the worktree, the failing tests; run them: they fail.
- [ ] **Step 3:** `cap_drop: [ALL]`, nothing added back yet, and the timeout; the tests pass;
  `make test lint`. **[Dan, on the Spark, in tmux]**, in the worktree: `make apply`; `make
  install-units`; `make apply`.
- [ ] **Step 4: Check it, adding back only on evidence.** **[Dan, on the phone]** the web UI end to
  end: log in, chat, an image, a document's embeddings, speech; and a search. Each failure is read
  in `make logs s=open-webui` or `s=searxng`, and the one capability it names is added back, with
  its reason, then Step 3 again. **On the Spark** (Dan; sudo), neither container can bind a port
  below 1024:

```bash
for c in local-ai-open-webui-1 local-ai-searxng-1; do sudo docker exec "$c" python3 -c 'import socket; socket.socket().bind(("127.0.0.1", 900))' 2>&1 | tail -1; done
sudo -k
```

  Expected: `PermissionError: [Errno 13] Permission denied`, twice.
- [ ] **Step 5: Commit on `main`, push, and merge.** **On the Spark**, in the worktree:

```bash
git add stack/templates/compose.yaml stack/templates/searxng-settings.yml spark/tests/test_render.py \
  changelog.md README.md website/architecture.qmd
git commit -m "feat(stack): 🤖 the web containers drop every capability they don't need" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

  Then *Before every push* with `main` as `<branch>`, and **Dan OKs** `git push origin main`. Back
  in the `phase-2a` clone, **on the Spark**:

```bash
git merge --no-ff main -m "chore(repo): 🤖 merge main's cap_drop into phase-2a" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
git worktree remove ../local-ai-main
```

  The merge can conflict in four files, since `phase-2a` changes each of them too (a stand-in merge
  at 65edda5 conflicted in the first two; the plan review's council-fix check, 2026-10-07). Resolve
  each, **on the Spark**, before committing:
  - `changelog.md`: keep both entries, `main`'s `cap_drop` entry and `phase-2a`'s, newest first.
  - `website/architecture.qmd`: keep `phase-2a`'s text and diagrams, and make the `cap_drop` part
    that `main` built read as built (solid, not planned), so the page stays true.
  - `README.md`: keep both sides' lines in §Current state (likely only once Tasks 2 and 36 have run).
  - `spark/tests/test_render.py`: keep both sides' tests.

  Then stage those files by name, finish the merge without editing its message, and run the tests:

```bash
git add changelog.md website/architecture.qmd README.md spark/tests/test_render.py
git commit --no-edit
make test lint
```

  With no conflict, `git merge` commits by itself and only `make test lint` is needed.

***

### Task 38 [Spark]: the cutover — the front, the gate, llama-swap on 900

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
  1's brake key, both still on the box (Task 36). Check that `main` renders it. **On the Spark:**

```bash
d=$(mktemp -d) && git worktree add "$d/main" main && (cd "$d/main" && uv run --frozen --quiet --project spark spark render --out "$d/out") && grep -h -e '-listen' -e 'EnvironmentFile' "$d/out/systemd/local-ai-llama-swap.service"; git worktree remove --force "$d/main"
```

  Expected: `render: 8 files → …`, then `EnvironmentFile=/etc/local-ai/secrets/llama-swap.env` and
  an `ExecStart` ending `-listen 127.0.0.1:9100`.

  The rollback itself, only if it comes to that. **On the Spark** (Dan; sudo asks twice: for the
  first line, and again at `make install-units`, after `sudo -k`):

```bash
sudo systemctl disable --now local-ai-front.service local-ai-front.socket local-ai-gate.service local-ai-gate-status.socket local-ai-gate-control.socket
sudo -k
git worktree add ../local-ai-rollback main
cd ../local-ai-rollback
make apply
make install-units
make apply-now
```

  Then `make doctor` from that worktree passes every one of Phase 1's fifteen checks but *secrets
  folder*, which reports `root:root 700`, as 2a's bootstrap left it (Task 36), and is expected
  (and *earlyoom* too, if Task 28's ruling changed `earlyoom.default`, since Phase 1's file then
  differs from the running arguments); a plan revision says what failed.

- [ ] **Step 2:** `make apply`. Expected: it lists root's ten new or changed unit files (the
  compose unit is unchanged), stages them, and stops, before it asks the gate anything (Task 32),
  since no gate runs yet.
- [ ] **Step 3 [Dan, on the Spark]:** `make install-units`. Read every file it shows: they are what
  root will run. It installs root's copies and enables the sockets and the services, and starts
  nothing new. Step 4 follows at once, with no reboot between: a boot now would start the front
  and the gate against Phase 1's app, the front's socket and Phase 1's llama-swap both wanting
  9100.
- [ ] **Step 4 [Dan]: the cutover, its deploy.** **On the Spark:**

```bash
systemctl stop local-ai-brake.service local-ai-llama-swap.service
make apply
```

  Stopping llama-swap stops every model and frees 9100; `make apply` then deploys the config, the
  registry and the app with nothing to wait for (Task 32). Expected: it deploys, and exits 0.
  Anything else — render or validation refusing, `uv sync` failing, the network dropping — leaves
  Phase 1's app deployed under the new units root now holds: stop here, and run Step 1's rollback,
  which restarts Phase 1's llama-swap.
- [ ] **Step 5 [Dan]: the cutover, its start**, only after Step 4 exited 0. **On the Spark** (Dan;
  sudo asks once):

```bash
sudo systemctl start local-ai-front.socket local-ai-gate-status.socket local-ai-gate-control.socket
sudo -k
systemctl start local-ai-llama-swap.service local-ai-gate.service local-ai-front.service local-ai-brake.service
```

  The front's socket takes 9100, and the gate loads the residents one at a time. The polkit rule
  lets you stop and start the services without sudo; only the sockets need it, and `sudo -k` drops
  it before anything else runs, so the clone's own code never runs with sudo's credential cached
  (*Lessons from Phase 0*). 9100 is free from Step 4's stop until the socket starts, the cutover's
  one window.
- [ ] **Step 6 [Spark]: Check it.** `make doctor` passes every line. `make status` reads as the
  plan's layout. `make logs s=gate` shows the residents loading one after another. Then
  the listening ports, numbers only. **On the Spark:**

```bash
ss -Hltn | awk '{print $4}' | sed 's/.*://' | sort -n | uniq | tr '\n' ' '; echo
```

  Expected among them: 800, 801, 803, 900 and 9100, and no 5800: llama-swap gives ports by the
  models' sorted names, so Gemma has 800, the embeddings 801 and whisper 803, and 802 is the
  coder's once it loads. `make logs s=front` holds no key, header or body. A request for a made-up
  model, with Dan's key on curl's stdin. **On the Spark:**

```bash
curl -s -H @- -H 'Content-Type: application/json' -d '{"model":"no-such-model","messages":[{"role":"user","content":"hi"}]}' http://127.0.0.1:9100/v1/chat/completions <<<"Authorization: Bearer $SPARK_API_KEY"; echo
```

  Expected: a 404 body with `model_not_found`'s words, listing the models. **[Dan, on the Mac]**,
  through `make tunnel`, pi answers. **[Dan, on the phone]**, the web UI chats, searches and
  transcribes. Bare `spark` on Dan's `PATH`, as `deploy.md` gives it (Task 30), used by the tasks
  after this one. **On the Spark:**

```bash
mkdir -p ~/.local/bin && ln -sf /opt/local-ai/app/.venv/bin/spark ~/.local/bin/spark && command -v spark
```

  Expected: `~/.local/bin/spark`, spelled out as a full path.
- [ ] **Step 7 [Dan, as `agent`]: what `agent` can't do.** `sudo -iu agent` on the Spark, then, while
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
- [ ] **Step 8: Stop and ask Dan** if any check fails: whether it is fixed in place or the rollback
  (Step 1) runs is his call, and a plan revision records it.
- [ ] **Step 9: The record and commit.** **On the Spark:**

```bash
git add changelog.md README.md CLAUDE.md website/architecture.qmd website/design/plan.md website/how-to/deploy.md
git commit -m "docs(machine): 🤖 the front, the gate and llama-swap on 900 are serving" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 39 [Spark]: a cold boot under llama-swap's sandbox

**Files (the record):** `changelog.md`, `README.md`, `website/design/plan.md` (*To verify*:
`NoNewPrivileges=` and `CapabilityBoundingSet=` from a cold boot; a Revisions line); and, only if an
engine can't load `nvidia-uvm`, `stack/host/bootstrap.sh` and `spark/tests/test_bootstrap.py`
(`nvidia-uvm` in `/etc/modules-load.d/`, test first).

- [ ] **Step 1 [Dan, on the Spark]:** power the box off and on (`sudo poweroff`, then the power
  button), so nothing from the last boot has `nvidia-uvm` loaded.
- [ ] **Step 2: Check it.** **On the Spark:**

```bash
lsmod | grep -c '^nvidia_uvm'
pgrep -u spark -x 'llama-server|whisper-server' | sort -n | tr '\n' ' '; echo
nvidia-smi --query-compute-apps=pid --format=csv,noheader | sort -n | tr '\n' ' '; echo
echo "nvidia-cdi-refresh done at $(systemctl show -p ExecMainExitTimestampMonotonic --value nvidia-cdi-refresh.service) us, llama-swap started at $(systemctl show -p ExecMainStartTimestampMonotonic --value local-ai-llama-swap.service) us"
```

  Expected: `1`; the same pids on the second and third lines, so every engine runs on the GPU, not
  on the CPU, which llama.cpp falls back to without `nvidia-uvm`; and, since Task 24's `After=`,
  `nvidia-cdi-refresh.service` done before llama-swap started, both a few seconds after boot. The
  record says which unit loaded `nvidia-uvm`. Then `make status`: the three residents loaded, one
  after another (`make logs s=gate`); `make doctor` passes. If a resident failed to start for want
  of `nvidia-uvm` (`spark logs <model>`), bootstrap loads it at boot: write that change test first,
  **[Dan]** re-runs `make bootstrap`, and this task runs again.
- [ ] **Step 3: The record and commit.** **On the Spark:**

```bash
git add changelog.md README.md website/design/plan.md
git commit -m "docs(machine): 🤖 record a cold boot under llama-swap's sandbox" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

  (With `stack/host/bootstrap.sh` and its test, if Step 2 changed them.)

***

### Task 40 [Spark]: the drills' memory hog, and agent's OOM score, tested as agent

**Files:**

- Create: `stack/measure/hog.py`, `spark/tests/test_hog.py`
- Modify (the record): `changelog.md`, `README.md`, `website/design/plan.md` (*`agent`'s own GPU
  jobs are outside the gate*: resolved in 2a, or moved to 2b, with a Revisions line);
  `website/scenarios/s06-agent-gpu-step.md` only if it moves.

**Interfaces:** `hog.py`, PEP 723 (Python ≥3.12, no dependencies), run with `uv run`: the one way
the drills lower memory (Tasks 40, 47, 48 and 50), sized from `MemAvailable` read at the time,
never a fixed size (the controller's ruling, 2026-10-07).

- `main(argv)` with `--leave` (GiB, required: the `MemAvailable` it brings the box down to, never
  below), `--floor` (GiB, default 24: the reserve, 4 above the brake's line; a `--leave` under it
  is refused, exit 2), `--step-gib` (0.25), `--pause-s` (2.5) and `--hold-s` (how long it holds at
  `--leave` before it frees everything and exits 0).
- `grow(read_available, take, free_all, *, leave_gib, floor_gib, step_gib, pause_s, sleep) ->
  Result(held_gib, stopped: "leave" | "not_reflected" | "floor", detail)` — before each step it
  re-reads `MemAvailable`, and stops growing when that, less a step, would pass under `--leave`; a
  step is anonymous memory with every page written; after each, it re-reads every 250 ms, for up
  to 5 s, until `MemAvailable` has fallen since its start by at least 90% of all it holds, and
  otherwise frees everything and stops `not_reflected`, exit 3. While it holds, a reading under
  `--floor` frees everything at once and stops `floor`, exit 3. It prints each step's total and
  `MemAvailable`, and frees everything on every way out, Ctrl-C and SIGTERM included.
- The pace: 0.25 GiB every 2.5 s is 0.1 GiB/s, and one step inside the rate watch's 2 s window reads
  as under 0.2 GiB/s, which Task 23's watch (10 s ahead, the brake at 20) takes for a breach only
  under 22 GiB available, below the floor. So a drill trips the brake only where it raises the
  brake's thresholds on purpose (Task 48).

**Tests** (`spark/tests/test_hog.py`, the script loaded from its path, a stand-in reader and
allocator):

- `test_it_stops_at_leave` — `MemAvailable` from 74, falling with each step taken, `--leave 48` →
  26 GiB held, stopped `leave`.
- `test_a_leave_under_the_floor_is_refused` — `--leave 20` → exit 2, nothing taken.
- `test_a_step_that_doesnt_show_stops_it` — the reading not falling after the third step →
  `not_reflected`, exit 3, every step freed.
- `test_a_fall_under_the_floor_while_it_holds_frees_everything` — holding, then a reading of 23 →
  `floor`, exit 3, everything freed.
- `test_it_frees_everything_on_any_way_out` — an exception, `KeyboardInterrupt` and SIGTERM at step
  5 → each step freed.
- `test_its_pace_stays_under_the_rate_watch` — the default step and pause, readings every 250 ms
  from 74 down to the floor, through Task 23's `predict_breach` with `brake.FALL_WINDOW_S`,
  `brake.UNLOAD_LEAD_S` and the registry's `brake_gib` → never true; so Task 47's new constants
  re-check it.

**Steps:**

- [ ] **Step 1:** the failing tests; run them: they fail.
- [ ] **Step 2:** the script; the tests pass; `make test lint`. Commit. **On the Spark:**

```bash
git add stack/measure/hog.py spark/tests/test_hog.py
git commit -m "feat(stack): 🤖 one memory hog for the drills, sized from MemAvailable" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 3 [Dan, as `agent`]:** `agent`'s tmux server outlives a login, so one started before
  Task 36's bootstrap keeps the old score in every pane. With Dan's OK — it ends `agent`'s
  sessions — `tmux kill-server` as `agent`; then a fresh login (`ssh brightroar-agent` from the
  Mac), and a new tmux. **On the Spark, as `agent`**, outside tmux and then inside it:

```bash
cat /proc/self/oom_score_adj
python3 -c 'v = int(open("/proc/self/oom_score_adj").read()); open("/proc/self/oom_score_adj", "w").write(str(v - 1))' 2>&1 | tail -1
```

  Expected: Task 28's value, then `PermissionError: [Errno 13] Permission denied`. (As you, today,
  the second line succeeds: an ordinary process may lower its own score until root sets a floor.)
  Inside tmux, also the server's own. **On the Spark, as `agent`**, in tmux:

```bash
cat "/proc/$(tmux display -p '#{pid}')/oom_score_adj"
```

  Expected: Task 28's value.
- [ ] **Step 4 [Dan, on the Spark]: a job of `agent`'s, against earlyoom.** `agent` can't read
  your clone, so leave it a copy it can. **On the Spark**, as you:

```bash
d=$(mktemp -d) && chmod 755 "$d" && install -m 644 stack/measure/hog.py "$d/hog.py" && echo "$d"
```

  Then, **as `agent`**, in tmux: `uv run --no-project <that folder>/hog.py --hold-s 300 --leave
  <N>`, with N the `MemAvailable` that `grep MemAvailable /proc/meminfo` reads just before, in
  GiB, less 12 (about a tenth of memory). While it holds, **[Dan, on the Spark]** (sudo) runs
  Phase 0's earlyoom dry run (phase-0.md, Task 11 Step 2: `sudo earlyoom --dryrun -r 1 -M …`, its
  `-M` above the box's free memory so earlyoom believes it must act), with the `--prefer` and
  `--avoid` that `stack/host/earlyoom.default` holds now, for about five seconds, then Ctrl-C and
  `sudo -k`. Expected: the process it would kill is the hog, `agent`'s, and no engine. Then Ctrl-C
  in `agent`'s pane (the hog frees everything), and, as you, `rm -r` the folder.
- [ ] **Step 5: Stop and ask Dan** if either check fails: whether Task 28's hook comes out in a
  revert commit, and the item moves to 2b with a plan revision, is his call, since a check run in
  a tmux server older than the hook reads a working hook as a broken one.
- [ ] **Step 6: The record and commit.** **On the Spark:**

```bash
git add changelog.md README.md website/design/plan.md
git commit -m "docs(machine): 🤖 agent's processes carry a root-set OOM score" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 41 [Spark]: the CUDA-allocatable ceiling, carefully

**Files:**

- Create: `stack/measure/cuda_ceiling.py`, `spark/tests/test_cuda_ceiling.py`
- Modify: `stack/models.yaml` (`allocatable_gib`, `allocatable_measured`), `website/design/plan.md`
  (rule 9's ceiling; *To verify* resolved; a Revisions line), `CLAUDE.md` (the gotcha's "to be
  measured"), `README.md` (§Hardware, where it gives the ceiling), `cosmicbboy-local-ai.md` (a claim
  this measures, `[adapted]` to `[verified]`, and no other), `changelog.md`

**Interfaces:** `cuda_ceiling.py`, PEP 723 (Python ≥3.12, no dependencies), run with `uv run`:
`main(argv)` with `--lib` (default `/opt/local-ai/bin/llama.cpp/b11146/libcudart.so.13`),
`--reserve` (default 24), `--step-gib` (0.25) and `--pause-s` (2.5); `measure(cuda, *,
read_available, reserve_gib, step_gib, pause_s, sleep) -> Result(allocated_gib, stopped: "reserve"
| "cuda_error" | "not_reflected", detail)` — it prints `cudaMemGetInfo`'s free and total first,
with nothing allocated; then, before each step, stops when `MemAvailable` less a step would fall
under the reserve; else `cudaMalloc`s a step, `cudaMemset`s it (touching every page), and prints
the running total and `MemAvailable`; after each step it re-reads every 250 ms, for up to 5 s,
until `MemAvailable` has fallen since its start by at least 90% of all it holds, and otherwise
frees everything and stops `not_reflected`; it stops at the first CUDA error; every pointer is
freed on every way out, Ctrl-C included. It never allocates "until failure". CUDA's allocations
don't show in a process's RSS, so earlyoom can't pick this script: its own reading, confirmed at
every step, is its guard against a `MemAvailable` that reports late.

The rate watch is accounted for by pace, not paused: 0.25 GiB every 2.5 s is Task 40's hog's pace,
under Task 23's trigger while more than 22 GiB is available, and the script stops at the reserve,
24. So the
measurement sends `memory_warning` once, as it passes 28, and no `brake_fired`. About 16 minutes
for the 93 GiB the box can give.

**Tests** (`spark/tests/test_cuda_ceiling.py`, the script loaded from its path, a stand-in `cuda`):

- `test_it_stops_when_memavailable_would_pass_the_reserve` — `MemAvailable` from 117, falling with
  each step taken, reserve 24 → 93 GiB allocated, stopped `reserve`.
- `test_a_step_that_doesnt_show_stops_it` — the reading not falling after the third step →
  `not_reflected`, every pointer freed.
- `test_its_pace_stays_under_the_rate_watch` — the default step and pause, readings every 250 ms
  from 117 down to the reserve, through Task 23's `predict_breach` with its constants and the
  registry's `brake_gib` → never true.
- `test_it_touches_every_step` — `cudaMemset` called once per allocation, with its pointer and size.
- `test_it_frees_everything_on_any_way_out` — an exception at step 5, and `KeyboardInterrupt` at
  step 5 → `cudaFree` for each pointer taken.
- `test_it_stops_at_the_first_cuda_error` — `cudaMalloc` failing at step 10 → stopped
  `cuda_error`, the 9 taken freed.
- `test_it_reads_memgetinfo_before_allocating` — the first printed line holds the stand-in's free
  and total, before any `cudaMalloc`.

**Steps:**

- [ ] **Step 1:** the failing tests; run them: they fail.
- [ ] **Step 2:** the script; the tests pass; `make test lint`.
- [ ] **Step 3: Measure it** **[Dan, on the Spark, watching in tmux]**. `spark make-room --all`
  (one question), so the box is clear and held for you; the brake and earlyoom running. Then, in
  tmux, `/opt/local-ai/bin/llama.cpp/b11146/llama-server --list-devices`, then `uv run
  stack/measure/cuda_ceiling.py`. Expected on the phone: `memory_warning` once, and no
  `brake_fired`. Then `spark make-room --done`: the residents reload. If the brake fires anyway,
  Dan stops the script with Ctrl-C (it frees everything); the residents then reload only after the
  brake's release, 5 minutes above 28 GiB, not at `--done`; and the reading goes to Task 47, since
  the watch's constants are wrong.
- [ ] **Step 4:** the registry follows. The method stops at the reserve, so on an idle box it
  shows *at least* about 93 GiB (117 available less the 24 reserve), below the reported 102: when
  the script stopped at the reserve, `allocatable_gib` is the figure it reached, recorded as *at
  least* that, with `allocatable_measured: true` and a comment saying it is a lower bound (Dan's
  decision, 2026-10-07, after the forward-and-back council). The ceiling term then equals the
  reserve's at idle and never binds; `cosmicbboy-local-ai.md`'s claim becomes `[verified]` only
  for *at least 93 GiB*. A run stopped by `cuda_error` below that is a real ceiling, recorded as it
  is. A run stopped `not_reflected` proves nothing: **stop and ask Dan.** Render still passes. **If
  render refuses the 2a set at the measured ceiling, stop and ask Dan** before Task 42: the swap
  waits for his choice (the design's: the old coder, still the registry's and on disk, or his
  full-context, f16 rule), and a plan revision records it.
- [ ] **Step 5: Commit.** **On the Spark:**

```bash
git add stack/measure/cuda_ceiling.py spark/tests/test_cuda_ceiling.py stack/models.yaml website/design/plan.md \
  CLAUDE.md README.md cosmicbboy-local-ai.md changelog.md
git commit -m "feat(stack): 🤖 measure the CUDA-allocatable ceiling, and the registry follows it" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 42 [Spark]: the coder swap — Qwen3.8-27B in the registry, apply, then the pull

**Files:**

- Modify: `stack/models.yaml`, `spark/tests/test_stack_registry.py`, `changelog.md`, `README.md`,
  `website/architecture.qmd` (the coder's label), `website/design/plan.md` (*Models*: the swap
  done)

**Interfaces:** the coder's entry, `qwen3.8-27b`: `capability: chat`, `roles: [coder]`, `label: the
coder`, `resident: false`, `engine: llama.cpp`, `source: {repo: unsloth/Qwen3.8-27B-GGUF, revision:
"4ca720788d1e01f1bff70c033e0d0028fd02e502", file: Qwen3.8-27B-UD-Q4_K_XL.gguf}`, `ctx: 262144`,
`parallel: 1`, `cache_ram_mib: 2048`, `footprint_gib: 41`, `footprint_measured: false`, `args:
[--load-mode, none, --spec-type, draft-mtp, --spec-draft-n-max, "3", --ctx-checkpoints, "8"]`
(today's draft length carried over; Task 44 reads its acceptance), with comments written fresh for
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
  50 beside the residents. Commit. **On the Spark:**

```bash
git add stack/models.yaml spark/tests/test_stack_registry.py
git commit -m "feat(stack): 🤖 Qwen3.8-27B becomes the registry's coder, by route A" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 3: Push, so the Mac can follow.** *Before every push* with `phase-2a`; **Dan OKs**
  `git push`; CI is green. Step 6's `make clients` on the Mac needs this commit.
- [ ] **Step 4: Apply it, S17's way.** **[Dan, on the Spark, in tmux]**, since it may wait up to 15
  minutes and then ask: `make apply`. The diff shows the coder's change; the quiet wait; the
  restarts in Task 31's order, the brake, the gate, then llama-swap under the loaded residents,
  which stops every engine, the old coder included, while the front keeps running (the new name
  reaches it through the gate's events); the gate reloads the residents; `apply_restarted` on the
  phone. **[Dan, on the Mac]**,
  pi lists the old name until Step 6, and a request for it reads `model_not_found`'s words, naming
  `qwen3.8-27b`. **[Dan, on the phone]**, the web UI, asked for `qwen3.8-27b`, shows
  `not_downloaded`'s sentence. (pi lists real names only, so it can't ask by the role.)
- [ ] **Step 5 [Dan, on the Spark]:** `make pull` — 17.6 GB as `spark-pull`; follow it with
  `make logs s=pull` in another pane. Expected: `pull: qwen3.8-27b: Qwen3.8-27B-UD-Q4_K_XL.gguf → …`
  for the coder, and the residents' files already there.
- [ ] **Step 6: The clients, at once,** as the design's order has it (apply, the pull, then `make
  clients`), so neither pi goes without a coder for longer than the pull, and Tasks 43–45 measure
  in pi. **[Dan, as `agent`]**, on the Spark, Task 34's command:
  `/opt/local-ai/app/.venv/bin/spark clients pi --write --registry /opt/local-ai/etc/models.yaml
  --http-idle-timeout-ms 900000`. Expected: pi lists `qwen3.8-27b` and not `qwen3.6-35b-a3b`;
  `~/.pi/agent/settings.json` holds `httpIdleTimeoutMs` 900000. **On the Mac**, from the clone, by
  Dan:

```bash
git switch phase-2a && git pull && make clients
```

  Expected: `clients: wrote the 'spark' provider to …/.pi/agent/models.json`; with `make tunnel`
  running, pi lists `qwen3.8-27b` and not `qwen3.6-35b-a3b`.
- [ ] **The way back,** written now, used only if Dan chooses it at Task 44's or Task 45's
  stop-and-ask: a commit that restores Qwen3.6-35B-A3B's entry and its tests (`fix(stack): 🤖
  Qwen3.6-35B-A3B is the coder again`), `make apply` (S17's wait), the push with Dan's OK, and
  Step 6's two clients again. Its files are still on disk, so nothing is pulled. A plan revision
  records why.
- [ ] **Step 7: The record and commit.** **On the Spark:**

```bash
git add changelog.md README.md website/architecture.qmd website/design/plan.md
git commit -m "docs(machine): 🤖 Qwen3.8-27B is the coder, pulled, and both pis list it" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 43 [Spark]: the page-cache drill

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
- [ ] **Step 2: Fill the page cache**, the coder not loaded, only until E.1's case holds: `MemFree`
  below what the coder's cold load takes (up to its 41 GiB footprint) and `Cached` high. The block
  fills what `MemFree` holds above 30 GiB, and nothing when it already holds less — likely, since
  that is the box's usual state (on 2026-10-07, `MemFree` 9 GiB and `Cached` 46 with three models
  loaded), so a fill of nothing is right, not a fault. **On the Spark**, in the tmux pane Step 3's
  `spark load` will run in, since `$f` lives in that shell:

```bash
grep -E '^(MemFree|MemAvailable|Cached):' /proc/meminfo
n=$(awk '/^MemFree:/ { m = int($2 / 1024) - 30 * 1024; print (m > 0 ? m : 0) }' /proc/meminfo)
f=$(mktemp -p ~ pagecache-fill.XXXXXX)
dd if=/dev/zero of="$f" bs=1M count="$n" status=none
cat "$f" > /dev/null
grep -E '^(MemFree|MemAvailable|Cached):' /proc/meminfo
```

  Expected, in the second reading: `MemFree` at about 30 GiB or under, `Cached` up by the fill, and
  `MemAvailable` nearly where it was, since the page cache counts as available.
- [ ] **Step 3:** in another tmux pane, `mkdir -p ~/samples && uv run stack/measure/sample_memory.py
  --out ~/samples/pagecache.csv --seconds 300`; in Step 2's pane, `spark load coder`, then `rm -f
  "$f"` there, so the fill file never stays behind.
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

### Task 44 [Spark]: the coder's first measurements, and route A's speed

**Files:** `stack/models.yaml` (the coder's comments; `cold_load_gib`), `website/design/plan.md`
(Phase 5's route A row, measured here; the load's deadline confirmed or revised; a Revisions
line), `cosmicbboy-local-ai.md` (only a claim this measures), `changelog.md`

- [ ] **Step 1, in the design's order** (*What 2a measures first*), each sampled with
  `stack/measure/sample_memory.py`: (1) b11146 loads it and drafts with MTP — the acceptance rate
  from `spark logs coder`; (2) its load's peak, 10×/s, and its cold fall in `MemAvailable` at full
  context, `MemFree` first; (4) time to first token at 32K, 128K and 250K tokens, against pi's
  timeouts (the Mac's default and `agent`'s 900,000), and decode at long context — **[Dan, on the
  Mac]**, in pi with its `retry.enabled` set to false for the run, since pi retries an error whose
  text holds "timeout", which would start a new 250K prefill and spoil the timing (or with curl,
  the key on its stdin); (5) its load time, cold and warm, against the 180 s deadline; (6) a busy
  coder's stop time, for the brake's `GRACE_S`, by the only way 2a has to unload an engine that
  is answering: `spark unload` each resident (idle, they go at once); **[Dan, on the Mac]** a long
  generation on the coder; then Task 48's drill block, whose brake unloads the busy coder, its only
  model; the stop time read from the drill's journal and the sampler; then `make brake-release`,
  and `spark load` each resident back. (`spark unload` drains, so it waits for the request: timing
  it would time the drain.) Step (3), the soak, is Task 45's.
- [ ] **Stop and ask Dan** if b11146 doesn't load the file or doesn't draft with MTP (1), or if
  time to first token at 250K passes the Mac's pi's 300 s (4): what gives is his choice, the
  design's — Task 42's way back, or his full-context, f16 rule — and a plan revision records it.
  Nothing else in this task runs until he has chosen.
- [ ] **Step 2 [Dan, on the Mac]:** route A's decode, time to first token and prefill in pi, in
  Phase 5's order, recorded against Phase 5's table.
- [ ] **Step 3:** the registry records the coder's `cold_load_gib`; private readings (load times,
  readings in context) go to Dan in the chat, for the vault.
- [ ] **Step 4: Commit.** **On the Spark:**

```bash
git add stack/models.yaml website/design/plan.md cosmicbboy-local-ai.md changelog.md
git commit -m "docs(plan): 🤖 Qwen3.8-27B's first measurements, and route A's speed" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 45 [Spark]: the soak — every footprint, and what RssAnon accounts for

**Files:** `stack/models.yaml` (each footprint, `footprint_measured: true`, `cold_load_gib`;
`gate.owed_reads_rss` as the evidence says), `spark/tests/test_stack_registry.py` (Task 4's
`test_owed_reads_rss_stays_off_until_the_soak`, rewritten to the evidence), `website/design/plan.md`
(rule 9's attribution checked; *To verify* resolved; the reserve's revisit put to Dan; a Revisions
line), `changelog.md`, `README.md`

- [ ] **Step 1:** every model at its full context: **[Dan, on the phone]** the residents through the
  web UI, the embeddings and speech; **[Dan, on the Mac]** the coder to about 250K tokens in pi,
  with edits, regenerations, tool calls and thinking (the design's step 3). Peak and steady state
  per model, from the sampler.
- [ ] **Step 2:** for each engine, its `RssAnon` growth against `MemAvailable`'s fall and
  `nvidia-smi`'s per-process figure. `owed_reads_rss` turns on, for an engine kind, only if
  `RssAnon` accounts for the growth `MemAvailable` shows for that kind.
- [ ] **Step 3:** the registry's footprints become the measured ones; render passes; **[Dan]**
  decides whether the reserve moves. **If render refuses the measured set** (a footprint over what
  the set leaves it), **stop and ask Dan**, as Task 44 does: Task 42's way back, or his
  full-context, f16 rule, with a plan revision.
- [ ] **Step 4: Commit.** **On the Spark:**

```bash
git add stack/models.yaml spark/tests/test_stack_registry.py website/design/plan.md changelog.md README.md
git commit -m "feat(stack): 🤖 every footprint measured by the soak, and what owed reads" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 46 [Spark]: both pis complete a real task with the coder

**Files (the record):** `changelog.md`, `README.md` (§Current state: `agent`'s pi; the Mac's
section: its pi).

The coder becomes the default for real work here, once Tasks 43–45 have measured it; both pis have
listed it since Task 42's Step 6. (Two tasks until the forward-and-back council, one for each pi.)

- [ ] **Step 1 [Dan, as `agent`]:** a real task with the coder, in tmux, completes.
- [ ] **Step 2 [Dan, on the Mac]:** with `make tunnel` running, a real task completes in pi with
  `qwen3.8-27b`; Dan tells the session. No Mac session is needed, and nothing in the repo changes
  on the Mac.
- [ ] **Step 3: The record and commit.** **On the Spark:**

```bash
git add changelog.md README.md
git commit -m "docs(machine): 🤖 both pis work with Qwen3.8-27B" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 47 [Spark]: the brake's timings, the lag, earlyoom's order and swap

**Files:**

- Modify: `spark/src/spark/brake.py` (`GRACE_S`, `FLOOR_TOLERANCE_GIB`, `FALL_WINDOW_S`,
  `UNLOAD_LEAD_S`, each with its measurement in its comment), `spark/tests/test_brake.py` (the
  tests that use them), `cosmicbboy-local-ai.md` (`[verified]` only where measured),
  `website/design/plan.md` (*The minimal brake's reach*; *To verify*: swap; a Revisions line),
  `changelog.md`; and, as the readings say, `stack/host/earlyoom.default` or a swappiness setting
  in `stack/host/bootstrap.sh`, each with its test first

- [ ] **Step 1: Measure,** each with the sampler: a busy engine's stop time, by Task 44's method
  (the drill brake unloading the coder while it answers, the only model loaded); how long after an
  engine leaves `/running` its memory shows in `MemAvailable`; `MemAvailable`'s noise while models
  generate; the falls of Task 44's loads, for the rate-of-fall constants; earlyoom's order at the
  real thresholds, by its dry run; whether memory swaps on the way down to 24 GiB, the hog's
  floor. The record says swap was measured down to 24 GiB available only, the 20–24 band left
  unmeasured by choice, and plan.md's *To verify* entry says the same. Every reading that lowers
  memory uses Task 40's hog, its `--leave` set from `MemAvailable` read just before, never a fixed
  size and never under its floor, 24 GiB, with the brake running and Dan watching in tmux; the
  record says how far below 28 each went. The brake's own firing is drilled at raised thresholds
  (Task 48), not at 20.
- [ ] **Step 2:** the constants from the measurements, the controller's ruling, Dan told (*Values
  the box decides*); the tests changed with them, failing first; `make test lint`, whose pace tests
  for the hog and the ceiling script (Tasks 40 and 41) read the new constants: if one fails, that
  script's pace changes in this commit, with its files. **[Dan, on the Spark]** `make bootstrap` if
  earlyoom's arguments or swappiness change; **[Dan, on the Spark, in tmux]** `make apply`, which
  restarts the brake.
- [ ] **Step 3: Commit.** **On the Spark:**

```bash
git add spark/src/spark/brake.py spark/tests/test_brake.py cosmicbboy-local-ai.md website/design/plan.md changelog.md
git commit -m "feat(spark): 🤖 the brake's timings and rate-of-fall watch, from measurements" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

  (With `stack/host/earlyoom.default`, `stack/host/bootstrap.sh`, `stack/measure/hog.py`,
  `stack/measure/cuda_ceiling.py` and their tests, if Step 2 changed them.)

***

### Task 48 [Spark]: drill — S05, memory critically low

**Files:** `website/scenarios/s05-memory-critically-low.md` (`status: verified`, `verified:` the
date; `phase` stays 1), `changelog.md`, `website/design/plan.md` if anything differs from it.

- [ ] **Step 1: The drill's brake.** The brake is drilled through Task 25's oneshot,
  `local-ai-brake-drill.service`, which runs `spark brake --once` as `spark`, with the brake's
  credential, against a drill copy of the registry whose thresholds sit just above what is
  available, as Phase 1's drill did; the registry, the gate and the running brake keep theirs.
  Each time the drill needs the brake to fire, **[Dan, on the Spark]** writes the copy from
  `MemAvailable` read then and starts the unit, which the polkit rule allows without sudo. **On
  the Spark:**

```bash
a=$(awk '/MemAvailable/ {print int($2/1048576)}' /proc/meminfo)
sed "s/warn_gib: 28/warn_gib: $((a + 10))/; s/brake_gib: 20/brake_gib: $((a + 5))/; s/reserve_gib: 24/reserve_gib: $((a + 6))/" /opt/local-ai/etc/models.yaml > /opt/local-ai/etc/brake-drill.yaml
chmod 0644 /opt/local-ai/etc/brake-drill.yaml
stat -c '%a' /opt/local-ai/etc/brake-drill.yaml
grep -E '(warn|brake|reserve)_gib:' /opt/local-ai/etc/brake-drill.yaml
systemctl start local-ai-brake-drill.service
journalctl -u local-ai-brake-drill.service -n 5 -o cat
```

  Expected: `644`, so `spark` reads the copy whatever Dan's umask; the three thresholds at `a + 10`,
  `a + 5` and `a + 6`; then the drill's lines, `brake: holding new loads …` and `brake: unloaded
  <model> at … GiB available`. Each start unloads one model, as the brake does each tick, so a
  further unload is a further start, while the hold stands. After the drill, `rm
  /opt/local-ai/etc/brake-drill.yaml`, so the unit can't run again (`ConditionPathExists=`).

  The gate down, for the last check below (Dan's decision, 2026-10-07, after the forward-and-back
  council). `systemctl stop local-ai-gate.service` doesn't keep it down: its sockets stay, and the
  next connection starts it again within seconds. So the gate is made to fail at start while its
  sockets stay, the crash loop the design describes. **On the Spark** (Dan; sudo):

```bash
sudo mkdir -p /run/systemd/system/local-ai-gate.service.d
printf '[Service]\nExecStart=\nExecStart=/bin/false\n' | sudo tee /run/systemd/system/local-ai-gate.service.d/fail-drill.conf >/dev/null
sudo systemctl daemon-reload
sudo systemctl restart local-ai-gate.service
sudo -k
```

  And put back after. **On the Spark** (Dan; sudo):

```bash
sudo rm /run/systemd/system/local-ai-gate.service.d/fail-drill.conf
sudo systemctl daemon-reload
sudo systemctl restart local-ai-gate.service
sudo -k
```
- [ ] **Step 2: S05, message by message,** each checked word for word, on the phone, in pi and in
  `spark status`:
  - Task 40's hog, `--leave 26`, takes memory under the real warn line, 28: `memory_warning`, once;
    then Ctrl-C;
  - with the coder loading (`spark load coder` in another pane), the drill's brake: `brake_fired`,
    high, its line the drill's (`a + 5`), naming the loading engine; then one start of the unit per
    further unload, each a follow-up of the same episode naming the idle model it unloads;
  - a request meanwhile waits, then reads `held_by_brake`'s words; `spark status` shows *paused*;
  - memory back above the warn line for 5 minutes: the gate releases, `brake_released`, and the
    residents reload one at a time;
  - a second brake within the hour (the drill's brake again, its copy written afresh): no automatic
    release, ~~`brake_needs_release`~~ its `brake_fired` ending *It fired within an hour of the
    automatic release at <the release's time>, so they stay paused until you release them: on the
    Spark, `make brake-release`.*, and nothing more on the phone *(corrected
    2026-10-07, at Task 7, the controller's ruling)*, and `agent`'s requests meanwhile read
    `held_by_brake`'s words;
  - a hold left across a reboot (**[Dan]** reboots while it stands): it waits for Dan, with
    `brake_needs_release` at boot;
  - **[Dan]** `make brake-release`: `brake_released_by_dan`'s words, and the residents reload;
  - only then, `agent`'s request for the model that was loading reads `footprint_suspect`'s words,
    and its notification names `spark load coder`; Dan's request loads it;
  - with the gate made to fail at start (the block above), the drill's brake fires and sends its own
    `brake_fired`, waiting for curl before it exits; the gate, put back, doesn't send it twice.
- [ ] **Step 3: Commit.** **On the Spark:**

```bash
git add website/scenarios/s05-memory-critically-low.md changelog.md website/design/plan.md
git commit -m "docs(website): 🤖 S05 verified: the brake fires, releases within bounds, and alerts without the gate" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 49 [Spark]: drill — S14, and the crash-loop check as agent

**Files:** `website/scenarios/s14-gate-down.md` (`status: verified`, dated),
`website/design/plan.md` (*To verify*: `OnFailure=` under `Restart=`, and 9100 held through a crash
loop; a Revisions line), `changelog.md`, `README.md`

- [ ] **Step 1 [Dan, on the Spark]: each service killed in turn**, `sudo systemctl kill
  --signal=SIGKILL local-ai-<name>.service` for the front, the gate, the brake and llama-swap: each
  alert on the phone, high, once, in Task 7's words; `back_up` a minute after each comes back;
  after llama-swap's, the residents reload one at a time, by themselves (Dan's decision,
  2026-10-07). `sudo kill -STOP "$(systemctl show -p MainPID --value local-ai-front.service)"`: its
  watchdog fires within 30 s, and it restarts after about 60 s, since the watchdog's SIGABRT waits
  on a stopped process until `TimeoutAbortSec=` (30 s, from `TimeoutStopSec=`) sends SIGKILL; its
  alert names it. A clean `sudo systemctl kill --signal=SIGTERM local-ai-llama-swap.service`: no
  alert, it comes back, and the residents reload. Gemma's engine killed, as earlyoom would: `sudo
  kill -9 "$(pgrep -u spark -f 'llama-server.*--alias gemma')"` → Gemma reloads by itself. With
  the gate made to fail at start (Task 48's block, put back after), a loaded model answers and a
  load reads `gate_down`'s words; with llama-swap stopped past a key's wait, `llama_swap_down`'s.
  Then `sudo -k`.
- [ ] **Step 2: The crash-loop check.** **[Dan, on the Spark]** (sudo asks once), the front killed
  again and again, then made to fail at start:

```bash
for i in 1 2 3 4 5 6 7 8 9 10; do sudo systemctl kill --signal=SIGKILL local-ai-front.service; sleep 3; done
sudo mkdir -p /run/systemd/system/local-ai-front.service.d
printf '[Service]\nExecStart=\nExecStart=/bin/false\n' | sudo tee /run/systemd/system/local-ai-front.service.d/crash-drill.conf >/dev/null
sudo systemctl daemon-reload
sudo systemctl restart local-ai-front.service
```

  Meanwhile, **on the Spark, as `agent`**, Task 38's bind block for 9100, and this watch of who
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

### Task 50 [Spark]: drills — S01's gate part, S02, S03 and S17

**Files:** `website/scenarios/s01-morning-start.md` (stays `planned`, with a dated note recording its
gate part), `s02-big-job.md`, `s03-doesnt-fit-interactive.md`, `s17-changing-models.md` (each
`status: verified`, dated), `changelog.md`, `website/design/plan.md` if anything differs from it.

- [ ] **Step 1: S01.** The coder idle-unloads after 60 minutes, or after the idle time lowered for
      the drill: a registry edit (`gate.idle_unload_min: 2`) and **[Dan, on the Spark, in tmux]**
      `make apply`, then another edit and apply to put it back: `unloaded`. A request from pi loads
      it: `load_started` and `loaded` arrive silently, and pi shows only a slower first reply.
      `spark pin coder 8h` answers in its words; `pin_ended` when it runs out (a short pin for the
      drill).
- [ ] **Step 2: S02.** `spark make-room 70G`: the list, one question, the room held. `agent`'s
  request for the coder waits its 10 minutes and reads `no_fit`'s `agent` text, naming the hold; a
  request of Dan's loads into the hold and shrinks it; `spark make-room --done` reloads Gemma, or
  sends `resident_waiting`.
- [ ] **Step 3: S03, and the runtime check of *Before Task 1*.** Task 40's hog, as Dan, with
  `--leave 48`: S03's moment, 48 GiB available, whatever the hog has to take to get there (about
  26 from the residents' 74). **[Dan, on the Mac]**, pi shows only its usual thinking for 30 s,
  nothing added to the stream, then the refusal, `409: {"message":"The coder didn't load:
  …","code":"no_fit"}`, at 30 s and without retrying: `make logs s=front` shows one request from
  `dan-mac`, ending 409. **[Dan, on the phone]**, the web UI, asked the same, shows the sentence
  alone, with no retry. The phone gets `refused` once, and a burst collapses with its count. Then,
  with the gate made to fail at start (Task 48's block, put back after), a request for the coder
  reads `gate_down`'s words, a `503`, after pi's own retries, each a line in the front's log. The
  make-room walk then loads the coder. What pi and the web UI did is recorded against *Before Task
  1* and Review Focus 6. *(Added 2026-10-08, at Task 12's review, the controller's ruling:)* Then
  a cut. **[Dan, on the Mac]** pi asks the coder for a long reply; while it streams, **[Dan, on the
  Spark]** the coder's engine process is killed with `sudo kill`. pi shows an error, not a
  shorter answer as if whole, and `make logs s=front` shows that request ending `cut`, with why.
  What the web UI shows is recorded beside it.
- [ ] **Step 4: S17.** A registry edit that changes llama-swap's config, with a request in flight;
  **[Dan, on the Spark, in tmux]** `make apply`: the diff; the quiet wait's words; Ctrl-C changes
  nothing; the deadline, shortened for the drill with `spark apply --deadline-s 60` (Task 32),
  offers "drain now"; `make apply-now` asks, naming the requests. Then the
  restarts, in their order (the brake, the gate, then llama-swap; the front only when its own
  files changed), read from `make logs s=gate` and the units' start times; a request sent during
  them, through the gate's own restart, is held, and goes through once llama-swap answers again,
  or reads `restarting`'s words at its deadline, never `llama_swap_down`'s; the phone gets
  `apply_restarted` and no `llama_swap_down`.
- [ ] **Step 5: Commit.** **On the Spark:**

```bash
git add website/scenarios/s01-morning-start.md website/scenarios/s02-big-job.md \
  website/scenarios/s03-doesnt-fit-interactive.md website/scenarios/s17-changing-models.md changelog.md website/design/plan.md
git commit -m "docs(website): 🤖 S02, S03 and S17 verified, and S01's gate part" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 51 [Spark]: close Phase 2a

**Files:** the scenario pages' statuses; `website/architecture.qmd` (every 2a part solid);
`README.md`; `changelog.md`; `CLAUDE.md` and README §Conventions (any rule 2a changed);
`website/design/plan.md` (the forward look, Revisions, Phase 2a's status line);
`website/design/phase-2a-qa.md` (any decision made since it was written);
`website/design/phase-2a-retro.md` (new); this plan's Progress note

- [ ] **Step 1:** **on the Spark**, `uv run --frozen --project spark spark docs check-scenarios`,
  `spark docs stack --check` and `spark docs notifications --check`; the docs true against the box.
- [ ] **Step 2 [Dan, on the Spark]:** `llama-swap.env` removed, the rollback no longer needed, and
  `hf/tmp`, which nothing names any more: emptied as `spark`, its owner, and the empty folder then
  removed as root, so root never deletes through a folder `spark` controls. **On the Spark**
  (Dan; sudo):

```bash
sudo rm /etc/local-ai/secrets/llama-swap.env
sudo -u spark find /var/lib/local-ai/hf/tmp -mindepth 1 -delete
sudo rmdir /var/lib/local-ai/hf/tmp
sudo -k
```

  Then `make doctor` passes, and `spark doctor --full`.
- [ ] **Step 3 [Dan]:** the private findings (load times, readings in context, anything about the
  tailnet) listed in the chat for the vault, never in the repo.
- [ ] **Step 4: Council review,** four reviewers against plan.md's Phase 2a and this plan: goal-fit
  and scenarios; reliability; security and simplicity; toolstack. The fixes, one commit each.
- [ ] **Step 5: Forward look:** what 2a teaches 2b, 2c and later — the reserve, the journal
  question, pi's own retries, the engines' own user, the network namespace, LiteLLM against the
  front, the watchdog's Compose file, and the deferred notes' *For later phases* list at this
  plan's foot — into plan.md, with a Revisions line; and into `phase-2a-qa.md`, any decision made
  since it was written.
- [ ] **Step 6:** the retrospective.
- [ ] **Step 7: Commit.** **On the Spark:**

```bash
git add website/scenarios/s01-morning-start.md website/scenarios/s02-big-job.md \
  website/scenarios/s03-doesnt-fit-interactive.md website/scenarios/s05-memory-critically-low.md \
  website/scenarios/s14-gate-down.md website/scenarios/s17-changing-models.md website/architecture.qmd \
  README.md changelog.md CLAUDE.md website/design/plan.md website/design/phase-2a-qa.md \
  website/design/phase-2a-retro.md website/design/phase-2a.md
git commit -m "docs(plan): 🤖 close Phase 2a: scenario statuses, the forward look, the retrospective" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

## ⇄ Switch point — Spark → Mac

- [ ] The Spark session runs *Before every push* with `phase-2a`; **Dan OKs** the push; **on the
  Mac**, `git switch phase-2a && git pull`.

***

### Task 52 [Mac]: the Mac check, the site and the merge

- [ ] **Step 1:** `make test lint docs`, on bash 3.2 and GNU make 3.81: the Makefile's new targets,
  the leak hooks, and the socket tests on the Mac's short paths; the site renders with no warnings,
  the notifications page among the reference pages. ~~Task 9's uid test and Task 19's tests over
  real sockets skip, with Task 9's reason (*SO_PEERCRED is Linux's, and the gate runs only on the
  Spark*), as expected, beside the skips for a tool the Mac lacks that a test names
  (`systemd-analyze`, say); any other skip is a finding.~~ A Mac difference is fixed here, with its
  test, and in this plan. *(Corrected 2026-10-08, at Task 9's review.)* Task 9 has four expected
  skips, each for a Linux-only fact:
  - the uid test and `test_a_tcp_request_carries_no_peer_cred`, for `SO_PEERCRED`;
  - `test_a_tcp_socket_from_systemd_keeps_tcp_nodelay`, for `SO_PROTOCOL`, without which
    `socket.socket(fileno=)` leaves a TCP socket's protocol 0 and asyncio sets no `TCP_NODELAY`;
  - `test_sd_notify_reaches_an_abstract_socket`, for the abstract socket namespace.

  Those four, Task 19's tests over real sockets (with Task 9's reason, *SO_PEERCRED is Linux's,
  and the gate runs only on the Spark*) and those for a tool the Mac lacks that a test names
  (`systemd-analyze`, say) are the expected skips; any other skip is a finding.
- [ ] **Step 2: CI's parity** (workflows are the Mac's): `.github/workflows/ci.yml` gains `spark docs
  notifications --check` beside `spark docs stack --check`, and shellcheck of
  `stack/host/local-ai-notify` and `stack/host/local-ai-agent-oom` beside bootstrap's. Commit;
  *Before every push*; **Dan OKs** the push; CI is green. **On the Mac:**

```bash
git add .github/workflows/ci.yml
git commit -m "ci(repo): 🤖 check the notifications page and shellcheck 2a's host scripts" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 3: Merge,** with Dan's OK. **On the Mac:**

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
- [ ] The CUDA-allocatable ceiling is measured, and recorded as *at least* the figure reached when
  the method stops at the reserve.
- [ ] The brake's timings, `MemAvailable`'s lag, swap (down to 24 GiB available) and earlyoom's
  order are recorded.
- [ ] Route A's decode, time to first token and prefill are recorded against Phase 5's table.
- [ ] `make test lint` is clean on the Spark and `make test lint docs` on the Mac, and CI is green;
  README §Current state, the changelog and the architecture page are true; the council review is
  done and the forward look applied; `phase-2a` is merged to `main` with Dan's OK.

***

## How the command blocks were checked

Every command block above was run on 2026-10-07, before it went in:

- **Run for real, read-only, on the Spark:** Task 1's digest check (Docker Hub answered the pinned
  digest); Task 36's check block, again after the plan review added `launch/started` (on the box as
  it stands, before bootstrap: the users and folders missing, as expected); Task 38's ports block
  (it listed 9100 and 5800–5802 that day); Task 38's bind block, as you, for 9100 and 900 (`bound …
  0 times in 30 s` for each); Task 39's `lsmod`; Task 40's two lines, as you (your score is 0, and
  the second line succeeded: why the check tells a set floor from none); Task 49's watch loop,
  against 9100 and against a port nobody holds (`200` and `000`). Added with the forward-and-back
  council's fixes: Task 39's new check block (the engines' three pids the same on both lines, and
  the two units' times); Task 38's made-up-model request, against today's 9100, where Phase 1's
  llama-swap answered with its own 404; Task 40's tmux-server line, as you (0).
- **In a scratch clone of this repo on the Spark:** Task 38's rollback render check (it rendered
  `main`, `-listen 127.0.0.1:9100` and `llama-swap.env`) and its `git worktree` steps; Task 42's
  Mac block (the Mac's real-task check's until the plan review moved it), with a temporary `HOME`
  (it wrote the provider); every task's commit form, Task 40's and Task 52's new ones among them;
  Task 52's merge; Task 37's `main` worktree, its commit on `main` and the merge into `phase-2a`,
  and the worktree's removal.
- **With stand-ins that log their calls** for `sudo`, `systemctl`, `docker` and `make`, since the
  real ones would change the box: Task 38's rollback and cutover blocks; Task 37's container check;
  Task 49's crash-loop blocks; Task 48's drill block, against a copy of `stack/models.yaml` (the
  three thresholds written at `a + 10`, `a + 5` and `a + 6`, the copy `644` under a `0077` umask,
  and the copy loaded through the registry's own loader), with the deployed registry's three
  threshold lines read on the box and `/opt/local-ai/etc` read as `root:spark-admin 2775`; Task
  48's gate-down block and its put-back; Task 51's removals, on stand-in folders; and Task 38's
  cutover as two blocks, so a `make apply` that fails stops before the sockets and services start.
  Task 38's `PATH` link ran with a temporary `HOME`.
- **Smaller, in the scratchpad:** Task 43's fill: its `MemFree` arithmetic on the box's own
  `/proc/meminfo` (`MemFree` read 9.3 GiB that day, so it would fill nothing: E.1's case held
  already), and the fill itself with 64 MiB in place of the computed size, and with none. Task
  40's copy for `agent`, with a stand-in `hog.py`: the folder `755`, the file `644`, and `uv run
  --no-project` running it from another folder.
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

***

## Deferred notes for implementers

The plan review (2026-10-07) found 1 Critical, 18 Important and 24 Minor items, and its re-check
5 Important and 11 Minor more, and its final check 2 Important and 5 Minor more. Every one is
fixed above (one Minor of the re-check, n-9, needed no change) but part of one Minor, the first
note here, and two of the final check's, f-2 and f-5, below; the others are what the fixes leave
for the tasks that meet them. The forward-and-back council's (0 Critical, 40 Important, 57 Minor)
are all fixed above, or in plan.md and the pages it names, but the Minors listed last here.

- **`brake/events.jsonl` grows.** The brake appends a few lines per brake episode and never trims
  the file (m-11). Keying on boot id and `seq` (Task 13) makes a removed or restarted file safe, so
  Task 23 may trim it at the brake's start check, keeping this boot's lines, if the file is ever
  large enough to matter; 2a doesn't need it.
- **The hog's and the ceiling script's pace** (Tasks 40 and 41) is set against Task 23's starting
  constants. Their `test_its_pace_stays_under_the_rate_watch` tests import the brake's constants,
  so Task 47's new ones re-check them; if one then fails, the pace changes with the constants, in
  the same commit.
- **A front restart inside apply cuts the requests it holds** (the final check's f-2). They are
  open connections, so when the front itself restarts (only when its own files change), its held
  requests are cut, not answered `restarting`: pi retries a connection error, and the web UI shows
  one. plan.md says so; nothing more is planned for it.
- **Apply's hold beside a make-room hold** (f-5). Task 29 doesn't word a `held` row when both stand;
  put both on the row, apply's first, and add the case to its test.
- **`spark status`'s `waiting` words** (Task 29) cover the six reasons in `gateproto.WAITING_WHY`;
  a reason added later needs its line, and Task 29's test reads the tuple, so it fails until the
  line is written.
- **make-room starts from the gate's own figure** (added 2026-10-07, at Task 6's fix round 1, the
  controller's ruling; plan.md, rule 4's *What it frees*). Task 30's `spark make-room <size>`
  takes *free for a load* from the gate, rule 9's figure with its ceiling and starting terms, as
  `budget.make_room_plan`'s `free_now_gib`. It never works out `MemAvailable` less the reserve and
  the growth owed: where the CUDA ceiling binds, that count could find nothing to unload, and Dan's
  retry would be refused again.
- **A key label pi's retry list matches is refused at startup** (added 2026-10-07, at Task 6's fix
  round 2, the controller's ruling). The private key list's labels go into refusals (*Not loading
  the coder for <label>*, *<label> already has as many requests …*), and render never reads that
  file. Whichever task first loads `keys.yaml` at run time, the front's (Task 20) or the gate's,
  refuses at startup a label that `messages.pi_retry_match` matches, with a plain error naming the
  key, as `render.check_words` does for the registry.
- **A brake with nothing loaded needs words** (added 2026-10-07, at Task 7's review, the
  controller's ruling), for Tasks 13 and 23. `brake_fired` refuses `unloaded=[]`, so a brake that
  pauses new loads with no model to unload (memory taken by something outside the stack) has no
  alert today. Whichever of the two first meets that case words it in `messages.py`, with its test:
  the reading, the line, *nothing of the stack's was loaded*, and the pause.
- **`model_not_found` bursts key on the shown model** (added 2026-10-07, at Task 7's re-review),
  for Task 14. Keyed on the client's raw name, each varied unknown name would go at once and
  never collapse; key the burst on the model the words show (`_shown_model`'s, nothing for a name
  they don't show), so a stream of bad names collapses into one notification with a count.
- **make-room with nothing loaded stops there** (added 2026-10-07, at Task 7's re-review), for
  Task 19. After *Nothing is loaded, so there is nothing to unload.*, the command ends: no
  `room_too_much` question and no `room_all` confirmation (*Unloaded everything …*) follow it.
- **CI checks the notifications page** (added 2026-10-07, at Task 7, the controller's ruling). Task
  7's `website/reference/notifications.md` is generated from the registry; `make docs` writes it,
  and `test_the_committed_notifications_page_is_current` fails while it is stale. Task 52, on the
  Mac (a workflow change the Spark's token can't push), adds `uv run --frozen --project spark spark
  docs notifications --check` to `.github/workflows/ci.yml`, beside the Stack page's check.
- **A recorded pid is checked by its start time** (added 2026-10-08, at Task 8's review, the
  controller's ruling), for Task 13. Task 8's `procs.engine_pid` takes a `recorded` pid that is
  alive and runs as `spark` as it is, so a pid the kernel handed to another `spark` process after
  the engine died would be read as the engine, and its `RssAnon` as the engine's growth. `Ticketed`
  records the engine's start time, `/proc/<pid>/stat` field 22, read after the line's last `)`
  (the name in parentheses before it can hold spaces and brackets; field 3 comes first after it,
  so field 22 is the 20th word, checked on the Spark, 2026-10-08), and `engine_pid` checks that it
  matches, so a reused pid is never read as the engine.
- **An `RssAnon` the gate can't read credits no growth** (added 2026-10-08, at Task 8's review, the
  controller's ruling), for Tasks 15 and 18. `procs.rss_anon_gib` gives None for a process gone or
  a status without the line, and a `PermissionError` from `/proc` propagates. A None at either end
  (`ModelRecord.rss_anon_at_load_gib` at the load, or the reading now), or an exception reading
  it, credits **no** growth: *owed* stays the footprint less the load's fall, which errs safe.
  None is never read as 0, which would count the engine's whole `RssAnon` as growth on top of the
  fall that already measured it. Task 18's background read catches `PermissionError` and `OSError`
  on each read and reports it under `health`, so a failed read never ends its loop.

- **The v257 stand-in restarts on its port** (added 2026-10-08, at Task 12's review, the controller's
  ruling), for Tasks 17, 18 and 32. A test of the residents' return after a restart, apply's
  restart, or llama-swap answering again uses `await fake.restart(down_s)`: the stand-in's
  shutdown, nothing listening for `down_s`, then the same port serving with every engine stopped
  and its logs empty, as v257 starts. A second `serve_fake` would start on a new port, which the
  gate under test wouldn't follow.

- **doctor's example address is the front's from the cutover** (added 2026-10-08, at Task 10's
  review, the controller's ruling), for Task 33. `doctor.py`'s refusal of a `SPARK_LLAMASWAP_URL`
  that isn't an http(s) URL offers `http://127.0.0.1:9100` as an example, and `test_doctor.py`
  pins that string. Since Task 10, `paths.LLAMASWAP_URL` defaults to `http://127.0.0.1:900`, and
  from the cutover 9100 is the front's. Task 33's rewrite offers 900, or no address, and moves the
  test with it.

### Minors the forward-and-back council left for the tasks that meet them

- **`route_not_served` has no next step, and `loading` says "try again in a minute" with no
  `Retry-After`** (the docs reviewer's m8). Both are the design's words; Task 6 keeps them, and the
  close (Task 51) weighs a next step and a retry-after against pi's retry list.
- **A world-writable lock in the deployed tree** (the box reviewer's M-7):
  `/opt/local-ai/python/.lock` is `0666`, uv's. If `uv sync --frozen` takes it, `agent` could stall
  `make apply`'s sync while apply's hold is renewed; Task 35 checks whether it does, and if so
  bootstrap makes it `0664`.
- **`agent`'s two curls in Task 38 Step 7 are prose** (the execution reviewer's m-9): written as a
  block there, run on stand-ins, when Task 38's runbook is.

### For later phases (Task 51's forward look)

From the later-phases reviewer's Minors, each for the phase named; none needs work in 2a:

- **2b:** `ssh brightroar spark status --json` won't find `spark`, since Dan's `.bashrc` returns
  before adding `~/.local/bin` for a non-interactive shell: the menu bar calls the absolute path,
  or bootstrap links a root-owned `/usr/local/bin/spark` (M-2). A menu-bar *stop-all* is
  `make-room --all`, which holds the whole box: the menu needs `--done` beside it, and the hold in
  its title (M-3). The notification list is closed: the watchdog's *box unreachable* and `agent`'s
  hooks need types of their own, the hooks' as typed messages with no free text, since an agent's
  text on the lock screen is a prompt-injection channel (M-4). The Mac's subscriber needs read
  tokens and a priority filter across the three topics (M-5). The watchdog can't reach the front's
  or the gate's health from off the box: an SSH forced command for `spark status --json`, with a
  key and an ACL grant of its own (M-6).
- **Phase 3:** the failure notifier covers four units, and Postgres or LiteLLM down need types and
  units of their own (M-11); a gated model's HF token on `spark-pull`, with the hf_xet log check
  still due (M-13).
- **Phase 5:** a container engine that runs as a non-root uid needs `spark`'s gid to read the
  cache (M-13).
- **Phase 6:** per-person keys make the front's journal, which names the key, a per-person usage
  log, and `GET /v1/models` shows every model to every key (M-9); caps are per key, and an event
  needs them per group (M-10).

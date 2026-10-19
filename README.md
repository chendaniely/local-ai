# local-ai

My local AI stack: a single GB10 DGX Spark — specifically a **GIGABYTE AI TOP ATOM**, hostname
`brightroar` — serving open models to non-Claude agent harnesses and to my own applications,
reached from a MacBook or other devices over Tailscale, WireGuard, or the home LAN.

Started 2026-09-23, on arrival of the Spark. The design was settled the same day — see
[the plan](website/design/plan.md). Its Phase 0 is built: leak guards and CI, the `spark` CLI's
first tools, the host bootstrap and the docs site. Phase 1 is built too (2026-09-29): since
2026-09-28 the box serves its four models through llama-swap, to Open WebUI on the phone and to pi
on the Mac and as `agent`, behind a minimal brake and launch check.
[Its retrospective](website/design/phase-1-retro.md) says what Phase 2 inherits. The Mac can also
run pi and Claude Code in [Orca](website/how-to/orca.md), and the web UI as an app of its own.
*(2026-10-16: Phase 2a's cutover, its Task 38, put a front and a gate in front of llama-swap,
which moved to 127.0.0.1:900, and the brake now unloads idle models first (built; not yet seen
acting: Tasks 47 and 48). The rest of Phase 2a, its drills, measurements and close, is still being
built on the `phase-2a` branch.)*

## Design in one line

**Claude stays untouched** — Claude Code and Claude Desktop talk directly to Anthropic on my
subscription, with nothing in the path. (Hooks that only report to apps on my own machines, such as
Orca's, are allowed; [the plan](website/design/plan.md) words it.) The Spark is a *second* mode: a model server for my own
pipelines, for coding harnesses (pi, OpenCode) on the Mac and on the Spark itself, and for a web UI —
it loads a model only when it fits, and says why when it doesn't. (Hermes, in the original version of
this line, was parked on 2026-09-23.) Since 2026-10-19, Posit Assistant — the TUI `pa` and Positron —
reaches it the same way, as a client like pi and OpenCode.

## Contents

| File | What it is |
|---|---|
| [`website/design/plan.md`](website/design/plan.md) | The plan: goals, constraints, decisions, design, phases, open items. Start here. It replaced `planning.md`, the initial plan, on 2026-09-23. |
| [`changelog.md`](changelog.md) | Dated log of what changed on the machine, newest first. |
| [`cosmicbboy-local-ai.md`](cosmicbboy-local-ai.md) | Notes on Niels Bantilan's stack (below) — GB10 hardware facts, gotchas, and what does and doesn't transfer to a single Spark. |
| [`Makefile`](Makefile) | The front door: `make help` lists the targets — tests, lint, docs, the leak-guard hooks, bootstrap, the GPU-set hold and upgrade day's move (`upgrade-gpu`), deploying the stack (`apply`, `install-units`, `pull`, `status`, `brake-release`, `logs`, `tunnel`, `clients`), and `doctor`, which checks the guardrails and the stack. |
| [`.githooks/`](.githooks) | The leak-guard hooks (pre-commit and commit-msg) and gitleaks' config. `make hooks` turns them on in each clone. |
| [`.github/`](.github) | CI (tests, leak scans, shellcheck, the site build), the site's publish on every push to `main`, and Dependabot's weekly update proposals. |
| [`spark/`](spark) | The `spark` CLI, a uv project with its tests: the leak check, the docs tools and the commands that run the stack (`spark --help` lists them). |
| [`stack/`](stack) | The model registry (`models.yaml`), the private key list's shape with placeholder names (`keys.example.yaml`; the real list is `/etc/local-ai/keys.yaml`, never in the repo), pinned versions (`versions.yaml`), the templates `spark render` fills (`templates/`), the host setup (`host/`: bootstrap, earlyoom's and needrestart's config, the polkit rule) and the Synology's Compose files (`synology/`: ntfy's, with the shape of its private `server.yml`). |
| [`website/`](website) | The Quarto docs site: the plan, the phase plans and Phase 0's retrospective (`design/`), scenarios, how-to runbooks, the generated Stack and Notifications pages, and the Community projects page. |

## Hosts

Machines are named after Valyrian steel swords.

| Name | Role | Previously |
|---|---|---|
| `brightroar` | The DGX Spark — GIGABYTE AI TOP ATOM, see [Hardware](#hardware) | `aitopatom-….local` — factory hostname (product name + a suffix from the wired NIC's MAC), renamed 2026-09-23 |
| `heartsbane` | The MacBook (16 GB M1 Pro), primary client | — |

Both the Spark's factory name and its current name are recorded deliberately: accounts created
during first-time setup get filed under whichever name was current at the time, so the old one
stays worth being able to search for. Searching "aitopatom" finds them.

The factory name's four-character suffix is **deliberately omitted** — it is the last two octets
of the wired NIC's MAC, and per [`CLAUDE.md`](CLAUDE.md) no per-unit hardware identifier goes
into a public repo. These names are lookup keys only; no credentials live here.

## Hardware

> **Terminology — read this once.** Everywhere in this repo, *"the Spark"*, *"DGX Spark"* and
> *"the box"* mean **this exact machine**: `brightroar`, the GIGABYTE AI TOP ATOM specified
> below. They never mean NVIDIA's Founders Edition or any other OEM variant (ASUS GX10, Dell Pro
> Max GB10, …). Anything that applies only to the Founders Edition — recovery media, firmware,
> support channels — is called out as such where it appears.

`brightroar` is a **GIGABYTE AI TOP ATOM**, model `ATAGB10-9002` rev 1.0 — an OEM DGX Spark
variant, not the NVIDIA Founders Edition. Same GB10 silicon, different chassis and support path.

| | |
|---|---|
| **Superchip** | NVIDIA GB10 Grace Blackwell — 20-core Arm (10× Cortex-X925 + 10× Cortex-A725), Blackwell GB20B GPU, 48 SMs, ~1 PFLOP FP4 |
| **Memory** | 128 GB LPDDR5X-8533, **soldered**, unified between CPU and GPU (~121 GiB visible) |
| **Storage** | **1 TB** PCIe Gen4 ×4 NVMe, M.2 **2242** |
| **Networking** | ConnectX-7 with 2× 200 Gb QSFP112 · 10GbE RJ45 (Realtek RTL8127) · Wi-Fi 7 (2×2) · Bluetooth 5.3 |
| **Display / IO** | 1× HDMI 2.1a · 4× USB-C 3.2 Gen 2×2 (20 Gb/s, DP Alt mode); leftmost USB-C is power in |
| **Power** | External 240 W USB-C adapter; GB10 TDP 140 W |
| **Size** | 150 × 150 × 51 mm, ~1.1 L, 1.2 kg |
| **OS** | NVIDIA DGX OS |

Two of these shape the plan more than the rest:

- **1 TB is the tight one.** Model weights, the shared Hugging Face cache and NGC container
  images (10–20 GB each) all come out of it. At 60–110 GB per large model that is single-digit
  models on disk before it fills. [The plan](website/design/plan.md) keeps weights on the local NVMe
  for now; cold storage on the Synology is in its backlog, pending a measurement of NAS read
  throughput. The M.2 is a short **2242**, which narrows the replacement options if it ever gets
  upgraded.
- **Memory is soldered and shared.** 128 GB is the permanent ceiling and the GPU draws from the
  same pool. This is the constraint the whole design turns on.

The ConnectX-7 QSFP ports exist to join a second Spark, which is out of scope here
(`cosmicbboy-local-ai.md` §H). They sit idle on a single-node setup.

*Spec provenance: the retail box and product listings, cross-checked against independent reviews.
Where they disagree the physical box wins — it reads Bluetooth 5.3, against 5.4 in one review.*

## Current state

How things actually stand, as opposed to how they are designed. Kept current with every change to a
machine; [`changelog.md`](changelog.md) records how it got here. Moved here from `planning.md` §8
when that file was retired on 2026-09-23.

### The Spark — `brightroar`

- **GIGABYTE AI TOP ATOM** — full spec under [Hardware](#hardware).
- Renamed from its factory hostname — the product name plus the last two octets of the **wired**
  NIC's MAC. The old name no longer resolves. That makes the factory hostname a partial hardware
  identifier, which is why it is written here in abbreviated form.
- **Wired**, after initially running on the Wi-Fi module. The gap is large enough to matter:
  **1.5 ms** average with 0.3 ms jitter on Ethernet, against **78 ms** with 9 ms of jitter on
  Wi-Fi. Agentic loops pay that on every round trip.
- **Dual-homed, one reserved address per NIC** — Wi-Fi on `.200`, Ethernet on `.201`. The wired
  address is canonical; Wi-Fi is kept deliberately as an out-of-band path for when the box is moved
  or the switch fails. Two NICs must never share a single reservation: they collide if both come up.
  Wi-Fi retries without limit and has power saving off (2026-09-24), so the out-of-band path comes
  back by itself.
- ⚠️ **A DHCP reservation only takes effect on a fresh request.** This bit twice — both times the
  box held an older pool lease and ignored a perfectly correct reservation until the interface was
  bounced or the machine rebooted. Assume a stale lease before assuming the router is wrong. Bounce
  it on the Spark, from an SSH session over the *other* interface so it doesn't sever the session:
  `sudo nmcli device disconnect <iface> && sudo nmcli device connect <iface>`.
- **The wired NIC is on `.201`** (2026-09-24, after a bounce from the Wi-Fi side). It had been
  holding an older pool address until then.
- **On the tailnet** (2026-09-24), tagged `tag:spark`, so its key never expires, with MagicDNS and
  HTTPS certificates on. The ACL policy replaced the allow-all default: Dan's devices reach each
  other, and reach the Spark on 22 and 443 only. There is no route home, by choice. Until then it
  was reachable only over the home LAN.
- **Bootstrapped** (2026-09-24, and re-run cleanly; re-run again on 2026-09-27 by Phase 1's Task 12,
  for the changes listed under *Pending from Phase 0's close*). It boots to a console, and a desktop starts
  on demand. ufw is on with SSH only. earlyoom is the only out-of-memory killer (systemd-oomd is
  inactive). The GPU set (kernel, NVIDIA modules, driver, CUDA) is held, 151 packages. Three
  identities: Dan (`chendaniely`, in `spark-admin`), `spark` (runs the stack, no login, not in
  `docker`) and `agent` (tmux agents, with its own SSH key and Claude Code; no sudo, docker or
  `spark-admin`, can't enter Dan's home, can use the GPU). The polkit rule lets `spark-admin` start,
  stop and restart the four `local-ai-*` units, by exact name, and nothing more; `pkcheck` confirms
  it can't reload systemd. *(Until the 2026-09-27 re-run, it let `spark-admin` manage any
  `local-ai-*` unit, and reload systemd, but not start transient ones.)* Two containers run,
  Open WebUI and SearXNG, under root's Compose unit (2026-09-28; none existed before): Open WebUI
  **v0.11.4** and SearXNG **2026.9.23-3cd69d30e**, each at the digest `stack/versions.yaml` pins,
  which root's Compose file names. *(Since 2026-10-15, Phase 2a's Task 37: both run with
  `cap_drop: [ALL]`, Open WebUI with `DAC_OVERRIDE` added back for its data folder, so neither can
  bind a port below 1024. SearXNG's `request_timeout` is 6.0 s.)* *(Re-run 2026-10-15 for Phase
  2a, Task 36. It added two identities, `spark-front` and `spark-pull`, system users with no
  login. The polkit rule now covers seven `local-ai-*` services. `agent`'s logins and user manager
  get an OOM score of 1000, set by root, and `/etc/cron.deny` names `agent`. earlyoom runs without
  `--prefer`. See the changelog.)*
- **The GPU set**, as the 2026-09-23 DGX OS update left it and bootstrap held it: kernel 7.0, NVIDIA
  driver 580.178, CUDA 13.0.3, the numbers [Updates](website/how-to/updates.md#upgrade-day-the-gpu-set)
  records for that update. `nvcc` reports 13.0, and `/usr/local/cuda` points to CUDA 13.0 (checked
  2026-09-24). The kernel's full release string is `7.0.0-1019-nvidia` (recorded 2026-09-25). The
  set moves only on upgrade day, which updates this line.
- **Secret files** for Phase 1 are in `/etc/local-ai/secrets/` (`llama-swap.env`,
  `open-webui.env`, `searxng.env`, `hf.env`; ~~`640 root:spark`~~), recorded by name in the vault.
  *(Since 2026-10-15, Phase 2a's Task 36: the folder is `root:root 0700`, and every file in it is
  `0600 root:root`. Phase 2a's files are there too: `front.key`, `gate.key`, `brake.key`,
  `internal-keys.env` and `client-keys.sha256`. Phase 1's stay for the rollback. ntfy's come in
  Phase 5. The private key list, `/etc/local-ai/keys.yaml`, is `0640 root:spark-admin`: key names,
  their groups and labels, private but not secret.)* *(Since 2026-10-16, Phase 2a's cutover:
  llama-swap reads `internal-keys.env`; the front reads `client-keys.sha256`, `front.key` and the
  key list; the gate reads `gate.key` and the key list; the brake and its drill read `brake.key`.
  Nothing reads
  `llama-swap.env`, which holds Phase 1's client keys and the brake's Phase 1 key; it stays for the
  rollback until Phase 2a's close, Task 51, removes it.)*
- **SSH takes keys only** (2026-09-25): `/etc/ssh/sshd_config.d/10-local-ai.conf` turns password
  and keyboard-interactive logins off, and `agent`'s sessions never get a forwarded SSH agent
  ([SSH from the Mac](website/how-to/ssh.md#keys-only)). The one-time public IPv6 check is done;
  its result goes in the vault.
- **GitHub** (2026-09-25): `gh` holds a fine-grained token for this repository only, the clone's
  `origin` is https, and `gh` is git's credential helper, so pushes go through that token. GitHub
  refuses the Spark over SSH: the Spark's key came off Dan's account. Whether the first `gh`
  login's *GitHub CLI* authorization was revoked on github.com is not yet recorded.
- **The Claude session** has the Mac's global rules and secrets guard (checked 2026-09-25).
- **The clone's `spark/` environment** runs uv's own CPython **3.12.14** (2026-09-26), the minor
  version `spark/.python-version` pins. Until then uv had built it on Ubuntu's Python 3.12.3.
  `uv python install` also linked `python3.12` in `~/.local/bin`; `python3` is still Ubuntu's.
- **Installed:** Claude Code and **uv 0.12.18**, both in `~/.local/bin` (uv as a per-user
  install; Claude Code was 2.1.281 when installed and updates itself — 2.1.282 on 2026-09-24);
  Google Chrome, through the DGX Dashboard; **R 4.3.3** from Ubuntu's archive. **shellcheck 0.9.0**,
  also from Ubuntu's archive, and **gitleaks 8.30.1**, the release binary in `/usr/local/bin`
  (2026-09-24). Ubuntu's gitleaks 8.16.0 was too old for the repo's hooks (they need 8.19 or later)
  and has been removed. **gh 2.45.0**, from Ubuntu's archive
  too, for pushing from Dan's account (never `agent`'s). Details in the changelog. **Docker 29.6.2**
  came with DGX OS, from NVIDIA's repository; **earlyoom 1.7-2** is Ubuntu's package, installed by
  bootstrap; **Tailscale 1.102.4** came from its own install script (2026-09-24; its version
  recorded 2026-09-25); **OpenSSH 1:9.6p1-3ubuntu13.19** is Ubuntu's `openssh-server` (recorded
  2026-09-25). **Node 22.23.3** comes from NodeSource's apt repository (2026-09-28, for `agent`'s
  pi; Ubuntu's own is 18.19, and pi needs 22.19 or later), so apt updates it. Claude Code here
  talks straight to Anthropic — it is a client like any other, not a change to the Claude path.
  **btop** and NVIDIA's **DCGM 1:3.3.9** (`datacenter-gpu-manager`) were installed together with
  `apt install` (dated 2026-10-13 by PR #9, not checked here; btop's version not recorded). DCGM's
  host engine service, `nvidia-dcgm`, is **masked and stopped** (2026-10-14, Dan's decision): it
  runs as root and takes commands from any account on the box. The GPU is watched with
  `nvidia-smi` instead. Details in the changelog.
- **The engines** (2026-09-27, Phase 1 Task 11), run by llama-swap since 2026-09-28: llama-swap
  **v257** in `/opt/local-ai/bin/llama-swap/v257/`, llama.cpp **b11146** (the prebuilt arm64 +
  CUDA 13.4 build, with its own CUDA runtime beside it) in `/opt/local-ai/bin/llama.cpp/b11146/`,
  and whisper.cpp **v1.9.4**, built here for `121a-real`, in `/opt/local-ai/bin/whisper.cpp/v1.9.4/`.
  whisper-server uses the system's CUDA 13.0, which is part of the held GPU set. `agent` runs
  llama-server and sees the GPU without docker. Checksums, commits and GPU code in the changelog;
  the pins in `stack/versions.yaml`.
- **The fast coder's files** (2026-10-14), outside the stack, and nothing runs them: the image
  `lmsysorg/sglang:v0.5.21`, by hasso5703's digest; Qwen3.8-27B in NVFP4 and its DFlash2 drafter in
  `~/sglang/hf`, 22 GiB; the engine's key file, `~/sglang/secrets/engine.yaml`, Dan's alone; and
  `~/sglang/scratch`, with the boot log. Run once by hand, with llama-swap stopped, and measured on
  [Speed tests](website/reference/speed-tests.md) (on `phase-2a` until Phase 2a merges). Phase 4a
  builds it into the stack; until then the by-hand page's *Clean up* removes these. 591 GiB of disk
  was left after (`df -h /`).
- **Flash-Next's files** (2026-10-15), outside the stack, and nothing runs them: Qwen3.8-Flash-Next
  at `UD-IQ3_XXS` in three shards, its vision projector and its MTP head, in
  `~/scratch/models/qwen3.8-flash-next`, 82 GiB; and llama.cpp **b11330**'s `llama-server`, built in
  `~/scratch/llama.cpp-b11330` for MTP and not installed. Run by hand, with llama-swap stopped, and
  measured on [Speed tests](website/reference/speed-tests.md) (on `phase-2a` until Phase 2a merges).
  Phase 4a puts Flash-Next in the registry, which pulls its own copy; the by-hand page's *Clean up*
  removes these.
- **The stack** (Phase 1, Tasks 12–13: deployed 2026-09-27, running since 2026-09-28). *(The units
  and ports below are Phase 1's, four units, llama-swap on 9100 and the engines from 5800, until
  2026-10-16: the cutover's note, at this bullet's end, has the current ones.)* Root runs its own
  copies: the four units in `/etc/systemd/system/local-ai-*.service` and the Compose project
  in `/etc/local-ai/compose`, `root:root`, installed by `make install-units`. llama-swap, the brake
  and the web services are enabled and running; the pull unit runs only when asked. Everything
  listens on 127.0.0.1 only: llama-swap on 9100, keys required; the engines on 5800 and up;
  Open WebUI on 3000; SearXNG on 8888. Four models: `gemma-4-26b-a4b` (resident; the small vision
  model, and Open WebUI's task model, whose task calls run without thinking),
  `qwen3-embedding-0.6b` and `whisper-large-v3-turbo` (resident), and the coder,
  `qwen3.8-27b` (on demand; `qwen3.6-35b-a3b` until 2026-10-08, its files still on disk). *(Since
  2026-10-16, Dan's decision, five: `qwen3.6-35b-a3b` is back, on demand beside the coder, labelled
  Qwen3.6, with no role; the two don't fit at once beside the residents.)* Their
  files are in `/var/lib/local-ai/hf` *(since 2026-10-15, Task 36: `spark-pull:spark 2750`, read by
  the engines through the group; 55G, `qwen3.8-27b`'s files and `qwen3.6-35b-a3b`'s, which are
  kept)*, with 729 GiB of disk left (2026-10-08; 752 before Qwen3.8's file). Open WebUI has its
  admin account, Dan's, and sign-up is closed.
  `make doctor`: 15 of 15. Since 2026-09-28 (Task 14), `tailscale serve` also serves Open WebUI to
  the tailnet over HTTPS, on the Spark's tailnet name, which stays out of this repo. Gemma runs
  with a micro-batch that holds a whole image (`--ubatch-size 2048`, `--image-max-tokens 1120`),
  since a photo aborted its engine at the default. The stack comes back after a reboot with no hand
  on it, and a routine `apt upgrade` left it running. Both were checked on 2026-09-28 (Task 16).
  That upgrade moved no library the engines use. Since 2026-09-28 every model runs at its full
  context: 262,144 tokens for Gemma and the coder, and 32,768 for the embedding model. Gemma's
  two slots share one pool, keep their cache while idle, and keep at most 4 context checkpoints
  each. Cold loads take about 25, 6 and 30 GiB for Gemma, the embedding model and the coder.
  Since Phase 1's council (deployed 2026-09-28): the engines serve neither `/slots` nor a web UI
  of their own; a registry may pass them only listed options; the resident engines run at
  `oom_score_adj` 900 and the coder at 1000; the coder keeps at most 8 context checkpoints; the
  footprints are 32, 8, 3 and 33 GiB; the brake checks at start that llama-swap takes its key,
  which `make status` and `make doctor` show; and llama-swap's own lines alone reach the journal.
  *(Since 2026-10-08, by Dan's decision: the coder is Qwen3.8-27B at 163,840 of its 262,144 tokens,
  footprint 35 GiB, so the footprints are 32, 8, 3 and 35, all 78 GiB the budget allows; its cold
  load took 30.6 GiB. Phase 2a's Task 42 gives it its full context. The figures above for the coder
  before it, Qwen3.6-35B-A3B, record that model: its full 262,144 context, its ~30 GiB cold load
  and its 33 GiB footprint.)* *(2026-10-08, committed but not yet deployed: by Dan's decision
  `spark render` on `main` has Phase 2a's corrected budget check, so the registry gives the coder
  its full 262,144 tokens, with a footprint estimated at 41 GiB. The footprints come to 84 GiB,
  within the 102 GiB ceiling, and the coder fits beside the residents with the reserve kept. The
  box serves the coder at 163,840 until Dan runs `make apply`, which restarts llama-swap and so
  refuses while a model is loaded (`make apply-now` restarts it anyway), and pi's lists show the
  new window once the clients are written again; this section changes then.)* *(Deployed
  2026-10-15, with Phase 2a's Task 37, from a `main` worktree: the box serves the coder at its full
  262,144 tokens. The footprints are 32, 8, 3 and 41 GiB, the coder's estimated and not yet
  measured. pi's lists show the new window once `make clients` writes them again on the Mac.)*
  The app in `/opt/local-ai/app` was last synced on 2026-10-05, by a `make apply` of Dependabot's
  hatchling bump (16efba8), which also restarted the brake (recorded 2026-10-07). *(Synced again
  2026-10-15, by Task 37's `make apply` from a `main` worktree: `main`'s render, registry and
  clients changes since 2026-10-08, which the changelog lists.)*
  *(Since 2026-10-16, Phase 2a's cutover, its Task 38, deployed from the `phase-2a` clone at
  `b614532`, which also synced the app. Root runs eleven units: Phase 1's four, and seven new, all
  in `/etc/systemd/system`. Requests reach **the front**, `local-ai-front.service`, as `spark-front`,
  on 127.0.0.1:9100, which PID 1 holds through `local-ai-front.socket`, so the port stays held
  while the front restarts. The front checks each client key against `client-keys.sha256`, the
  keys' digests, and forwards with a key of its own. **The gate**, `local-ai-gate.service`, as
  `spark`, serves two Unix sockets that PID 1 holds: `/run/local-ai/gate-status.sock`,
  `spark:spark-users 660`, and `/run/local-ai/gate-control.sock`, `root:spark-admin 660`. It admits
  every load, one at a time, and loads the three always-loaded models itself. **llama-swap** listens
  on 127.0.0.1:900 and takes only the internal keys, the front's, the gate's and the brake's. Its
  engines listen from 800: Gemma on 800, the embedding model on 801, ~~whisper on 803, and the coder
  on 802 when it loads~~ *(since 2026-10-16, 13:17, when Qwen3.6 came back on demand: Qwen3.6 on 802
  and the coder on 803 when each loads, and whisper on 804; llama-swap numbers them by name)*. Only llama-swap's unit has `CAP_NET_BIND_SERVICE`, which the engines
  inherit. **The brake** stays a unit of its own, unloading idle models first (built; not yet seen
  acting: Tasks 47 and 48). **The failure
  notifier**, `local-ai-notify@`, runs when the front, the gate, the brake or llama-swap fails, and
  sends nothing until ntfy is set up, in Phase 5. **The brake's drill**,
  `local-ai-brake-drill.service`, runs only when Dan starts it. The pull runs as `spark-pull`.
  `make doctor`: 29 of 29, with one WARN, `ntfy: not set up (Task 2, Phase 5)`. The changelog has
  the cutover's times and checks.)* *(Since 2026-10-16's cold boot, Phase 2a's Task 39: the stack
  comes back by itself from a power cycle, under llama-swap's sandbox. `nvidia-uvm` is loaded at
  boot by udev's rule from `nvidia-kernel-common-580`, before llama-swap starts, so the engines
  need no setuid helper, and every engine runs on the GPU. Nothing in bootstrap had to change.)*
- **`agent`'s tools** (2026-09-28, Phase 1 Task 15). pi **0.85.1** and uv **0.12.19** in its
  `~/.local/bin`; its own llama-swap key, as `SPARK_API_KEY` in its `~/.secrets`, which its
  `~/.bashrc` loads first; and a clone of `phase-1` in `~/work/local-ai`, used only for
  `spark clients`, which wrote pi's `spark` provider. Its Claude Code got the same secrets guard as
  Dan's Spark session before the key arrived: the hook script, the 35 deny rules, and a `CLAUDE.md`
  holding only the secrets rule. Its pi runs the coder through llama-swap, in tmux, and the session
  survives a detach and a new login. *(Since 2026-10-16, Phase 2a's cutover: the same key is one of
  the client keys the front checks, by its digest, and pi reaches the models through the front on
  9100. llama-swap no longer takes it: at 127.0.0.1:900 it gets a 401, checked as `agent` that day.)*
- **Desktop session:** DGX OS boots to a desktop by default, which would hold 2–3 GiB of the shared
  memory pool. Checked 2026-09-24: the display manager (GDM) was up with only its login screen —
  nobody logged in to a desktop — and that screen held about **0.4 GiB**. The 2–3 GiB figure is for
  a logged-in desktop, so it is still unmeasured here. Headless since the 2026-09-24 bootstrap.
- **Memory, headless** (2026-09-24, `free -g`): **121 GiB total, 2 used, 118 available**. That is
  the same in whole GiB, because the login screen held only about 0.4 GiB. In MiB, 121,715 were
  available, with one Claude Code session in tmux.
- **Memory before bootstrap** (2026-09-24, `free -g`): **121 GiB total, 2 used, 118 available**,
  plus a 16 GiB swap file. Taken with the login screen up, Docker running with no containers, and
  one Claude Code session in tmux (about 1 GiB of the 2). Nothing else holds much: the largest
  services are the DGX Dashboard (~0.4 GiB) and Docker with containerd (~0.15 GiB). No model
  server is installed (no Ollama, as a snap or a service), and the snaps are the desktop's own
  (browser, mail, store, firmware updater), none running as a service.
- **Pending from Phase 0's close** (2026-09-25). Not yet applied on the box, or applied but not yet
  recorded here. A box change gets a dated `changelog.md` entry and a line here when it's done; the
  IPv6 check's result goes in the vault instead:
  - ~~**Pull before the next upgrade day.**~~ **Done 2026-09-25:** Dan switched the Spark's clone
    to `main` and pulled Phase 0's merge (`ebb32da`), so it has the reviewed `updates.md`,
    `make hold-gpu` (`ed0e06a`) and bootstrap's `--hold-gpu` (`f77a144`). Until then the clone had
    the `updates.md` from before Phase 0's council, whose re-hold ran all of `make bootstrap` and
    whose recovery line could leave the box without a GPU.
  - ~~**A bootstrap re-run.**~~ **Done 2026-09-27** by Phase 1's Task 12, Step 1: everything below
    is on the box, and `make doctor`'s three lines pass. Bootstrap changed after its 2026-09-24 runs: `/var/lib/local-ai`
    becomes root's, and earlyoom avoids `sshd.*` (`3735420`); `spark` gets two cache folders,
    `/var/lib/local-ai/cache` and `/var/lib/local-ai/cuda-cache` (`7c6616a`); the polkit rule lets
    `spark-admin` start, stop and restart the four `local-ai-*` units and nothing more (`414ddaf`);
    and a new step installs needrestart's override, `/etc/needrestart/conf.d/local-ai.conf`, so that
    needrestart never restarts a `local-ai-*` unit (`748a093`). Until it runs again, the box keeps
    the first run's owners, earlyoom arguments and polkit rule, and has neither cache folder nor the
    override, so `make doctor`'s *spark's folders*, *needrestart* and *earlyoom* lines fail. Checked
    read-only: `/var/lib/local-ai` is `spark:spark 751` and the override isn't there (2026-09-26),
    and the running earlyoom avoids `sshd`, not `sshd.*` (2026-09-27). Phase 1's Task 12, Step 1
    re-runs it.
  - ~~**Keys-only SSH**~~ **Done 2026-09-25**
    ([SSH from the Mac](website/how-to/ssh.md#keys-only)), with its one-time public IPv6 check,
    whose result goes in the vault.
  - ~~**A GitHub token for this repository only**~~ **Done 2026-09-25**, and pushes go through it
    ([The Spark session](website/how-to/spark-session.md#github-a-token-for-this-repository-only)).
    That the first `gh` login was revoked is not yet recorded.
  - ~~**The secrets guard and the Mac's global rules**~~ **Done**, checked 2026-09-25, for the
    Spark's Claude session
    ([The Spark session](website/how-to/spark-session.md#before-the-first-session), steps 2 and 3).
  - ~~**Three facts to record**~~ **Recorded 2026-09-25**, above: Tailscale 1.102.4, the kernel's
    release string `7.0.0-1019-nvidia`, and OpenSSH 1:9.6p1-3ubuntu13.19, the version the
    keys-only drop-in now runs on.

### The MacBook — `heartsbane`

- **16 GB M1 Pro**, wired.
- Docker Desktop running with a **7.75 GiB** VM — nearly half the machine. Phase 0's Task 12 also
  used it for throwaway `ubuntu:24.04` containers, to test shell and apt behaviour on the Spark's
  userland.
- **The repo's tools:** gitleaks **8.30.1** and shellcheck **0.11.0**, from Homebrew; uv
  **0.12.18**; Quarto **1.10.3**, for `make docs`; gh **2.101.0** (all as of 2026-09-24).
  Homebrew's coreutils **9.12** (2026-09-29): the install-units tests run its `timeout`, and fail
  on a Mac without it. gh's login carries the `workflow` scope (2026-09-29), which a push that
  changes `.github/workflows/` needs; without it GitHub refuses the push.
- **pi** (2026-09-28, Phase 1 Task 15) **0.87.1**, from Homebrew and unpinned, by Dan's decision
  that day. `make clients` wrote its `spark` provider, with Gemma and the coder at 262,144 tokens
  each, and `make tunnel` carries it to the Spark's llama-swap. Task 15 first ran it at the pin,
  0.85.1, installed with npm; whether that copy is still installed is not recorded. *(Since
  2026-10-16, Phase 2a's cutover, the tunnel carries it to the front, on the same port, 9100; pi
  answered through it that day.)*
- **opencode 2.0.25**, from Homebrew (its version recorded 2026-10-14). It runs one background
  service that its windows hand their requests to, which keeps the environment it started with:
  `opencode service restart` after adding a key. `make clients` writes its `spark` provider; the fast
  coder's `spark-sglang` provider was added by hand from the by-hand page, its key in
  `SPARK_SGLANG_KEY`, exported by Dan's shell like the others. Flash-Next's `spark-flash-next`
  provider was added by hand too (2026-10-15), with a 131,072-token limit and a placeholder key,
  since the by-hand server checks none: it reaches the server only through the tunnel on port
  8080, and fails while that tunnel is closed. `make clients` rewrites only the `spark` provider
  and keeps both.
- **Posit Assistant** (2026-10-19): the TUI `pa`, a development build from Posit's install script
  that updates itself, and Positron 1.138.0, which ships the assistant as its `posit.assistant`
  1.7.0 bootstrap extension. Both reach the Spark's models over `make tunnel`, through the
  front's OpenAI-compatible routes, as pi and opencode do. `make clients-posit` writes the
  built-in `openai-compatible` entry in `~/.posit/ai/providers.json` from the registry: the chat
  models, with their windows and capabilities, and an allow list that keeps the embeddings model
  and whisper out of the picker; the built-in form, since a custom entry has no key path that
  works in both the TUI and Positron (its `apiKeyHelper` breaks Positron, which reads the same
  file). The key is `OPENAI_COMPATIBLE_API_KEY` in `~/.secrets` for the TUI and Positron's
  credential store; the file holds none. Positron's side is checked: a first conversation
  answered through it with Gemma on 2026-10-19. The TUI takes its model per session with `-m`
  (`pa --list-models` lists the values); the model picked in Positron's picker is remembered
  in `~/.posit/assistant/settings.json` under its `preferredModel` key (Gemma on 2026-10-19).
  [Posit Assistant on the Mac](website/how-to/posit-assistant.md).
- **This clone** (`~/git/hub/local-ai`) has the leak-check hooks on (`make hooks`, 2026-09-24),
  with the private denylist in `~/.config/local-ai/denylist`; its contents never enter the repo.
  `spark/`'s environment runs a uv-managed Python **3.12.13**, the minor version
  `spark/.python-version` pins (2026-09-24).
- **SSH to the Spark** goes through the `~/.ssh/config` aliases that
  [SSH from the Mac](website/how-to/ssh.md) names: `brightroar` and `brightroar-agent`, by the
  tailnet's full MagicDNS name (2026-09-24). Of the runbook's LAN fallbacks, `brightroar-lan`
  works (2026-09-25, in keys-only SSH's checks); `brightroar-agent-lan` is not recorded as added
  here yet.
- **Orca 1.4.220** on 2026-10-05 (installed 2026-09-24; it updates itself), in its local mode
  ([Orca on the Mac](website/how-to/orca.md)): it runs Claude Code and pi in its panes, and pi
  reaches the Spark over `make tunnel` (checked 2026-09-29). Its status hooks sit on 13 events in
  `~/.claude/settings.json` since 2026-09-24, and its three pi extensions in
  `~/.pi/agent/extensions/` since 2026-09-28; after its updates to 1.4.220 its hook still only
  reports, now with the agent's process number and start time. Its settings: Agent Permissions
  Manual and telemetry off since 2026-10-05, read from its settings database (until then, its
  defaults: telemetry on, and Claude started with `--dangerously-skip-permissions`). It imported the
  Mac's ten `~/.ssh/config` hosts as SSH targets on 2026-09-29, the Spark's five aliases among
  them. It has never connected as `agent`: its home has no `~/.orca-remote` (checked 2026-10-05).
  *(Corrected 2026-10-05: this said it had never connected to the Spark at all, which nobody had
  checked for Dan's own account there.)*
- Podman Desktop installed but with **no machine created**; it costs nothing as it stands.
- **NVIDIA Sync** and **NVIDIA AI Workbench** installed here, not on the Spark. Workbench's prompt
  to set up a container runtime concerned its *local* context — which on macOS has no NVIDIA GPU
  behind it and so is CPU-only. Pointing Workbench at `brightroar` as a remote location would
  install its own daemon and runtime on the Spark.
- **NVIDIA Sync connects over SSH**, per NVIDIA's documentation: key-based auth set up once, then
  port forwards that exist only while Sync is connected — so it is not a separate way into the
  Spark. From the docs (2026-09-23). It keeps its own key, installed for my account on the Spark,
  and its own SSH config file, and it was cut down to one device, at the wired address, on
  2026-09-24 ([SSH from the Mac](website/how-to/ssh.md#nvidia-sync)). That its port forwards exist
  only while it is connected is not yet checked.

## Factory reset

Worth recording as the escape hatch. This is a dev box for data science and model hosting, so
nothing irreplaceable should ever live only on it — a wipe should be a chore, not a crisis. That
is also the argument for keeping every bit of configuration reproducible from this repo.

> ⚠️ **This box is the GIGABYTE variant, so check GIGABYTE first.** The numbered procedure
> below is NVIDIA's, and NVIDIA documents it for the **Founders Edition**. GIGABYTE publishes its
> own restore path for the AI TOP ATOM while also pointing users at NVIDIA's page. Prefer
> GIGABYTE's media if the two differ:
> <https://www.gigabyte.com/us/AI-TOP-PC/GIGABYTE-AI-TOP-ATOM/support>

Reported on GIGABYTE's support pages but **not verified here** — their site blocks automated
retrieval, so confirm by hand: an **AI TOP Utility** package that restores OS and firmware to
factory state, and a DGX OS **ISO** route where the BIOS boot menu offers *DGX Spark Installation
Options* → *Install DGX OS … for DGX Spark*. Note that an ISO route is already a departure from
the Founders Edition `tar.gz` + `CreateUSBKey` flow below, which is the reason to check.

### NVIDIA's procedure (Founders Edition, kept as reference)

> ⚠️ **Recovery completely erases the internal SSD.** There is no data-preserving option — a
> repeated request on the developer forums. The hostname resets too, so the box returns under a
> factory name rather than `brightroar`. See [Hosts](#hosts).

1. **Download** the DGX Spark System Recovery Image (~5–6 GB) from
   <https://www.nvidia.com/en-us/drivers/dgx-spark-recovery-software>. No enterprise account
   required. Versions move, so take the current one rather than pinning a number here.
2. **Write it to a USB drive**, 16 GB or larger, from another machine. Extract the archive and run
   the bundled script for that machine's OS — `CreateUSBKeyMacOS.sh` from `heartsbane`,
   `CreateUSBKey.sh` on Linux, `CreateUSBKey.cmd` on Windows (as Administrator).
   **This erases the USB drive.**
3. **Attach a keyboard and display** to the Spark. This is the one procedure that needs them; the
   box is otherwise headless.
4. **Reset UEFI to defaults.** Power on holding `Esc` or `Del` → *Save & Exit* →
   *Restore Defaults* → *Yes* → *Save Changes and Reset*.
5. **Boot the USB.** Hold `Esc`/`Del` again → *Save & Exit* → *Boot Override* → select the USB
   drive. If it boots into the installed OS instead, disable Secure Boot under *Security*, save,
   and retry the Boot Override.
6. **Run recovery.** `Enter` at the welcome screen → `[START RECOVERY]` at the warning screen →
   wait → `Enter` to reboot when the summary appears.

NVIDIA's page does not state how long recovery takes. A PXE/network recovery path also exists,
but has version-specific failures reported on the forums; USB is the documented route.

Afterwards, expect to redo everything in [Current state](#current-state) — hostname, Tailscale
enrolment, container runtime — and follow the plan's recovery runbook. The DHCP reservation survives, since the MAC is hardware and
doesn't change.

Source: [DGX Spark User Guide — System Recovery](https://docs.nvidia.com/dgx/dgx-spark/system-recovery.html).
**Not yet performed on this box** — transcribed from NVIDIA's documentation, not from experience.

## Reference

**[github.com/cosmicbboy/local-ai](https://github.com/cosmicbboy/local-ai)** — Niels Bantilan's
local AI stack. The primary reference for this repo.

His setup is a **2× DGX Spark** cluster joined by a 200 Gb RoCE fabric, serving DeepSeek-V4-Flash
with vLLM at `TP=2`. Roughly half of it is therefore cluster-specific and doesn't apply to a
single box. The half that does is unusually good: measured GB10 hardware behaviour, an honest
record of what *didn't* work, and the exact deployed config as ground truth.

See [`cosmicbboy-local-ai.md`](cosmicbboy-local-ai.md) for the extracted single-node subset, with
each item labelled `[verified]` (measured on his hardware) or `[adapted]` (my translation to one
node, untested).

**[github.com/MiaAI-Lab/Qwen3.8-27B-SGLang-DGX-Spark](https://github.com/MiaAI-Lab/Qwen3.8-27B-SGLang-DGX-Spark)**
— scripts that serve Qwen3.8-27B with SGLang in Docker on one DGX Spark, with three speculative
decoders (MTP, DSpark and DFlash2), each measured on the box. The notes for ~~Phase 5's~~ Phase 4's
coder bake-off (added 2026-09-28, read at its 2026-09-12 state); plan.md's ~~Phase 5~~ Phase 4 line
says what this stack must account for to run it. Its numbers are its own, not measured here.
*(Renumbered 2026-10-14, Dan's decision: Phases 4 and 5 swapped, the bake-off now 4 and the NAS
5.)*

**[github.com/hasso5703/dgx-spark-qwen38](https://github.com/hasso5703/dgx-spark-qwen38)** — a
one-command SGLang service for Qwen3.8-27B on one DGX Spark: the NVFP4 checkpoint with a DFlash2
drafter, 71.4 tok/s greedy median on its own bench, the combination the SGLang cookbook gives as
its recipe. Read at commit `3849269` (2026-10-13). It installs its own engines, ports, systemd
units and sudoers rules, so this stack reads it for its config rather than running it: the
[by-hand page](website/how-to/qwen3.8-27b-sglang.md) and plan.md's Phase 4a take its checkpoint,
drafter and memory evidence, and its Phase 4 notes record what it adds and corrects. Its numbers
are its own, not measured here. *(2026-10-14: the first run here, by hand, with its image by the
same digest, gave 41.7 tok/s on one prompt whose 1,024 tokens were all thinking, against 23.8 for
the llama.cpp coder, in about 45 GiB: [Speed tests](website/reference/speed-tests.md).)*

**[github.com/bidual/awesome-dgx-spark](https://github.com/bidual/awesome-dgx-spark)** — a curated
list of community projects for the DGX Spark and other GB10 machines. The site's
[Community projects](website/reference/community.md) page triages it for this stack (read
2026-10-13, at its commit `294eaf1`): what each useful entry is, why it matters here and where in
the plan it would come in, and what was left out and why.

## Conventions

- **This repo is public, and git history is permanent.** Removing something in a later commit
  does not unpublish it — it stays in the history, in clones and forks. The check happens before
  `git add`, and nothing identifying goes in: no full LAN or tailnet address, no public address or
  IPv6 prefix of my home connection, no MAC or serial. Full rule in [`CLAUDE.md`](CLAUDE.md).
- **No secrets in this repo.** API keys are referenced by environment variable, expanded at call
  time. Real values live in `~/.secrets` and are never committed or printed.
- **`agent` never gets my credentials** — no sudo, no docker, no GitHub token, no access to my home
  or `~/.secrets`. It holds only its own: its Claude Code login and, from Phase 1, its own
  llama-swap key (since Phase 2a's cutover, 2026-10-16, a client key that the front checks).
- **Docs must be true, and get fixed in the same change that makes them wrong.** This repo is
  mostly documentation, so a wrong doc is worse than a missing one — it gets believed and acted on.
  Correct rather than delete, and keep unverified things marked unverified. Full rule in
  [`CLAUDE.md`](CLAUDE.md).
- **The architecture diagrams are kept true first.** [Architecture](website/architecture.qmd)'s
  diagrams are the quickest way to see what runs and how a request gets through, so any change to a
  part or a path, or a planned part built, updates them in the same commit. Diagrams live only
  there; the overview among them is one file, `website/_overview.qmd`, that the home page includes
  too (2026-10-14). Full rule in [`CLAUDE.md`](CLAUDE.md).
- **Every command in the docs says where it runs**, in bold in the paragraph right above its block:
  **On the Mac:** or **On the Spark:**. Never as a `#` comment inside the block, which the Mac's
  zsh tries to run as a command; and no `#` comment at all in a shell block that runs on the Mac,
  since that zsh passes a trailing one to the command (Dan's rule, 2026-09-27). Full rule in
  [`CLAUDE.md`](CLAUDE.md).
- **`free -g`, never `nvidia-smi`**, for anything memory-related on GB10 — the GPU shares the
  CPU's LPDDR5X pool and `nvidia-smi` reports `[N/A]`.
- **The GPU set moves as one, only on upgrade day.** Kernel, NVIDIA modules, driver and CUDA are
  held together; unholding or upgrading part of them can leave a kernel with no NVIDIA module.
  Runbook: [Updates](website/how-to/updates.md).
- **Nothing loads unless it fits, and a change that restarts llama-swap goes through `make apply`**
  (added 2026-10-16, at Phase 2a's cutover, which built and deployed what these rules rest on). The
  gate admits every load, one at a time, keeping the 24 GiB reserve free and holding back the growth
  the loaded models are still owed. The brake stays a unit of its own, unloading idle models first,
  and the gate lifts its hold only within bounds (built; not yet seen acting: Tasks 47 and 48). A
  llama-swap restart stops every model, so `make apply` restarts it only once a minute has passed
  with nothing in flight; after 15 minutes without one it offers to drain now, and `make apply-now`
  restarts at once, cutting off what's in flight, after asking when the gate lists any (built; the
  quiet wait not yet run on the box: Task 50). The gate then reloads the always-loaded models.
  Until the cutover, `make apply` refused while models were loaded. A secret read only at start,
  such as `internal-keys.env`, takes a restart by hand instead, as [Deploy the
  stack](website/how-to/deploy.md)'s *What `make apply` never restarts* says. Overcommitting this
  box's memory can freeze it, so none of this is loosened casually. Full rules in
  [`CLAUDE.md`](CLAUDE.md)'s *GB10 gotchas*.
- **`***`, never `---`, for a horizontal rule in markdown under `website/`** — Pandoc can read a
  mid-document `---` as a YAML block, and the render fails.
- **Scenarios are living docs.** A change in the stack's behaviour updates its page under
  `website/scenarios/` and its `spark doctor` check in the same commit.
- **Work runs on the Spark by default** (since 2026-09-25). The Mac keeps only its own clients and
  their config, changes under `.github/workflows/` (the Spark's repository-only token can't push
  them) and rendering the site until Quarto is on the Spark; sudo, logins and secrets are mine. One
  session at a time, a checkpoint commit per task, and pushes only with my explicit OK. Nothing
  deploys from a phase branch the running stack can't run: in Phase 2a, nothing from `phase-2a`
  until its cutover, an urgent fix going from `main`, and Dependabot's `spark/` bumps waiting until
  the phase merges (my decision, 2026-10-07). From the cutover it runs the other way: until
  `phase-2a` merges into `main`, nothing deploys from `main` or a `main` worktree but Task 38's
  rollback, and every deploy, an urgent fix's included, is committed on `phase-2a`, deployed from
  its clone, pushed with my OK, and reaches `main` with the phase's merge (my decision, 2026-10-16,
  at the cutover). Sessions
  stage files by explicit path, and use my denylist only through the leak check, never reading it.
- **Phase 0's lessons are rules**: scan everything going out with the denylist before every push,
  run a plan's code before it goes in the plan, test shell and apt behaviour on Ubuntu 24.04 as well
  as the Mac, check box facts on the box, keep root out of paths `spark` and `agent` control, and
  review security in the task that changes it. The story is in the
  [Phase 0 retrospective](website/design/phase-0-retro.md).
- **Python through uv, and the `Makefile` as the front door** — no system Python, no pip, and one
  Python minor version, pinned in `spark/.python-version`. uv uses only its own interpreters
  (`python-preference = "only-managed"` in `spark/pyproject.toml`), and locks only releases at
  least seven days old (`exclude-newer = "7 days"` there, a rolling window, with Dependabot's uv
  PRs waiting as long; Dan's decision, 2026-09-27). The same week applies to every version pinned
  by hand in `stack/versions.yaml` unless an urgent fix needs it sooner (Dan's decision,
  2026-09-28).
  Full rules for all of the above in [`CLAUDE.md`](CLAUDE.md).
- **Test runs on the Spark are capped at 4 GiB.** `make test` runs them under the cap, and
  `make test ARGS="…"` runs some; the tests themselves refuse an uncapped run there, for every
  account, `agent` included, so a runaway test can't take the models' memory (my decision,
  2026-10-15, the day one reached 105 GiB). The Mac and CI run them as before. Full rule in
  [`CLAUDE.md`](CLAUDE.md).

## My environment (personal)

Nothing in this section is part of the stack or reproducible by anyone else. It records where *my*
own material lives, so that future me — and future agent sessions — can find it.

**Obsidian vault.** I keep a personal Zettelkasten in Obsidian: no git remote, synced across my
machines with Synology Drive. The local-AI material this repo deliberately cannot hold lives there,
in `zettelkasten/local-ai/`, entry note `brightroar Local AI Stack.md` — the full factory hostname,
both NIC MACs, the full LAN and tailnet addresses, the serial/service tag, the purchase record, and
the accounts created during first-time setup.

⚠️ **The vault is not a secrets store either.** Credentials are recorded there by reference only:
which secret exists, where its value lives, and the variable it is referenced as — never a value,
never a masked prefix. It syncs to a NAS and across several machines, so a leaked vault must not be
a leaked credential. Full rule in [`CLAUDE.md`](CLAUDE.md).

**Private files (from Phase 0).** Phase 0 added a few more private things, all outside the repo:
one secret file per service on the Spark (2026-09-24), the denylist the leak-check hooks read (one
on each machine), and the Tailscale ACL policy. A private values file holding the NAS and LAN
addresses the configs need is still to come. The vault's entry note records each one — by
location, and by reference for anything secret — when it is created. *(Since 2026-10-15, Phase 2a:
also the private key list on the Spark, `/etc/local-ai/keys.yaml`. It holds each client key's name,
group and label, private but not secret, and never goes in this repo.)*

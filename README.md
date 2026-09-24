# local-ai

My local AI stack: a single GB10 DGX Spark — specifically a **GIGABYTE AI TOP ATOM**, hostname
`brightroar` — serving open models to non-Claude agent harnesses and to my own applications,
reached from a MacBook or other devices over Tailscale, WireGuard, or the home LAN.

Started 2026-09-23, on arrival of the Spark. The design was settled the same day — see
[the plan](website/design/plan.md); the build starts with its Phase 0.

## Design in one line

**Claude stays untouched** — Claude Code and Claude Desktop talk directly to Anthropic on my
subscription, with nothing in the path. The Spark is a *second* mode: a model server for my own
pipelines, for coding harnesses (pi, OpenCode) on the Mac and on the Spark itself, and for a web UI —
it loads a model only when it fits, and says why when it doesn't. (Hermes, in the original version of
this line, was parked on 2026-09-23.)

## Contents

| File | What it is |
|---|---|
| [`website/design/plan.md`](website/design/plan.md) | The plan: goals, constraints, decisions, design, phases, open items. Start here. It replaced `planning.md`, the initial plan, on 2026-09-23. |
| [`changelog.md`](changelog.md) | Dated log of what changed on the machine, newest first. |
| [`cosmicbboy-local-ai.md`](cosmicbboy-local-ai.md) | Notes on Niels Bantilan's stack (below) — GB10 hardware facts, gotchas, and what does and doesn't transfer to a single Spark. |

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
- ⚠️ **A DHCP reservation only takes effect on a fresh request.** This bit twice — both times the
  box held an older pool lease and ignored a perfectly correct reservation until the interface was
  bounced or the machine rebooted. Assume a stale lease before assuming the router is wrong. Bounce
  with `sudo nmcli device disconnect <iface> && sudo nmcli device connect <iface>`, run from the
  *other* interface so it doesn't sever the session.
- **Pending:** the wired NIC has not yet picked up `.201` — it is still holding an older pool
  address. Bounce the interface from the Wi-Fi side, or reboot (Phase 0).
- **Not on the tailnet.** Reachable only over the home LAN today — the one path with no ACL in
  front of it. Joining is Phase 0.
- **Installed:** Claude Code 2.1.281 and **uv 0.12.18**, both in `~/.local/bin` (uv as a per-user
  install); Google Chrome, through the DGX Dashboard; **R 4.3.3** from Ubuntu's archive. Details in
  the changelog. Claude Code here talks straight to Anthropic — it is a client like any other, not a
  change to the Claude path.
- **Desktop session:** DGX OS boots to a desktop by default, which would hold 2–3 GiB of the shared
  memory pool — not yet checked on this box. Going headless is Phase 0.

### The MacBook — `heartsbane`

- **16 GB M1 Pro**, wired.
- Docker Desktop running with a **7.75 GiB** VM — nearly half the machine.
- Podman Desktop installed but with **no machine created**; it costs nothing as it stands.
- **NVIDIA Sync** and **NVIDIA AI Workbench** installed here, not on the Spark. Workbench's prompt
  to set up a container runtime concerned its *local* context — which on macOS has no NVIDIA GPU
  behind it and so is CPU-only. Pointing Workbench at `brightroar` as a remote location would
  install its own daemon and runtime on the Spark.
- **NVIDIA Sync connects over SSH**, per NVIDIA's documentation: key-based auth set up once, then
  port forwards that exist only while Sync is connected — so it is not a separate way into the
  Spark. From the docs (2026-09-23); not yet checked against this setup.

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

## Conventions

- **This repo is public, and git history is permanent.** Removing something in a later commit
  does not unpublish it — it stays in the history, in clones and forks. The check happens before
  `git add`. Full rule in [`CLAUDE.md`](CLAUDE.md).
- **No secrets in this repo.** API keys are referenced by environment variable, expanded at call
  time. Real values live in `~/.secrets` and are never committed or printed.
- **Docs must be true, and get fixed in the same change that makes them wrong.** This repo is
  mostly documentation, so a wrong doc is worse than a missing one — it gets believed and acted on.
  Correct rather than delete, and keep unverified things marked unverified. Full rule in
  [`CLAUDE.md`](CLAUDE.md).
- **`free -g`, never `nvidia-smi`**, for anything memory-related on GB10 — the GPU shares the
  CPU's LPDDR5X pool and `nvidia-smi` reports `[N/A]`.
- **Scenarios are living docs.** A change in the stack's behaviour updates its page under
  `website/scenarios/` and its `spark doctor` check in the same commit.
- **Work runs where it belongs.** Code, tests, docs and Mac clients are written on `heartsbane`;
  anything touching the Spark's GPU, memory, systemd or Docker is built and tested on `brightroar`;
  sudo, logins and secrets are mine. One session at a time, a checkpoint commit per task, and pushes
  only with my explicit OK.
- **Python through uv, and the `Makefile` as the front door** — no system Python, no pip.
  Full rules for all of the above in [`CLAUDE.md`](CLAUDE.md).

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

**Planned private files (from Phase 0).** The design adds a few more private things, all outside
the repo: one secret file per service on the Spark, a private values file holding the NAS and LAN
addresses the configs need, the denylist the leak-check hooks read, and the Tailscale ACL policy.
The vault's entry note records each one — by location, and by reference for anything secret — when
it is created.

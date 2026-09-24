# local-ai

My local AI stack: a single GB10 DGX Spark — specifically a **GIGABYTE AI TOP ATOM**, hostname
`brightroar` — serving open models to non-Claude agent harnesses and to my own applications,
reached from a MacBook or other devices over Tailscale, WireGuard, or the home LAN.

Started 2026-09-23, on arrival of the Spark. Mostly planning at this stage.

## Design in one line

**Claude stays untouched** — Claude Code and Claude Desktop talk directly to Anthropic on my
subscription, with nothing in the path. The Spark is a *second* mode that powers OpenCode, pi and
Hermes, and gives my own apps a free OpenAI-compatible endpoint.

## Contents

| File | What it is |
|---|---|
| [`planning.md`](planning.md) | Goals, constraints, target architecture, open questions. Start here. |
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
  models on disk before it fills — see [`planning.md`](planning.md) §7 #5. The M.2 is a short
  **2242**, which narrows the replacement options if it ever gets upgraded.
- **Memory is soldered and shared.** 128 GB is the permanent ceiling and the GPU draws from the
  same pool. This is the constraint the whole design turns on.

The ConnectX-7 QSFP ports exist to join a second Spark, which is out of scope here
(`cosmicbboy-local-ai.md` §H). They sit idle on a single-node setup.

*Spec provenance: the retail box and product listings, cross-checked against independent reviews.
Where they disagree the physical box wins — it reads Bluetooth 5.3, against 5.4 in one review.*

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

Afterwards, expect to redo everything in [`planning.md`](planning.md) §8 — hostname, Tailscale
enrolment, container runtime. The DHCP reservation survives, since the MAC is hardware and
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
- **`free -g`, never `nvidia-smi`**, for anything memory-related on GB10 — the GPU shares the
  CPU's LPDDR5X pool and `nvidia-smi` reports `[N/A]`.

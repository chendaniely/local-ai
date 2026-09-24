# local-ai — planning

Started 2026-09-23, on arrival of a **single DGX Spark (GB10)**.

Synthesizes a Claude Desktop architecture conversation from earlier the same day with the
hardware findings in [`cosmicbboy-local-ai.md`](cosmicbboy-local-ai.md). Nothing here is built
yet — this is the shared context to build *from*.

---

## 1. Goals

In order of how much they constrain the design:

1. **Claude experience stays exactly as it is.** Claude subscription, Claude Code, Claude Desktop
   — no performance loss, no added latency, no plumbing changes. This is the hard one.
2. **Everything outside Claude gets better.** Other harnesses (OpenCode, pi, Hermes) fill gaps
   Claude can't, using the latest open models.
3. **Not restricted to Ollama's catalog.** Pull GGUFs and safetensors straight from Hugging Face.
4. **A free API for my own applications** — no per-token cost for things I build.
5. **Maximize utilization of an expensive box.** Not just chat inference: GPU dataframes,
   fine-tuning, media work.

---

## 2. Hard constraints

| Constraint | Consequence |
|---|---|
| **Claude Code is untouched** | No gateway, no proxy, no `ANTHROPIC_BASE_URL`, no global MCP servers. It talks straight to Anthropic. |
| **One GB10, ~121 GiB unified memory** | Weights + KV + OS share one pool. See §5.1 — this is the biggest constraint on the whole design. |
| **Secrets by reference only** | Per `~/.claude/CLAUDE.md`: keys live in `~/.secrets`, exported, passed as `${VAR}`. Never committed, never printed. Applies to every config file in this repo. |
| **Headless box, several clients** | No display. Reached over SSH/HTTP from a MacBook or other devices, via Tailscale, WireGuard, or the plain home LAN. Nothing may assume one client machine or one transport. |

---

## 3. Target architecture — two modes that never share plumbing

### Mode 1 — Claude, untouched

- `claude.ai` and the `claude` command talk directly to Anthropic on the subscription.
- **No Spark MCP servers installed globally.** Every MCP tool definition consumes context in
  *every* session. If Claude should call a Spark capability, add it to that one project's
  `.mcp.json` only.
- **Claude Code is also installed on the Spark** (`changelog.md`, 2026-09-23), on the same
  subscription and talking straight to Anthropic. It is there to debug on the box and to work on
  repos cloned onto it. That is a *client* of Anthropic on a second machine, not a change to the
  Claude path — it does not touch this constraint, and future sessions should not "fix" it.
- A separate wrapper command (e.g. `claude-dgx`) if a local-model Claude Code is ever wanted, so
  switching is deliberate and the default is never altered. Pointing **any** Claude Code at a
  local endpoint is that deliberate mode; simply running Claude Code on the Spark is not.

### Mode 2 — open harnesses, Spark-first

- OpenCode, pi, and Hermes point at a **LiteLLM** endpoint and never need reconfiguring again.
- LiteLLM routes to the Spark over whichever path is up — home LAN in the house, Tailscale or
  WireGuard from outside it. The MLX fallback is MacBook-only; other clients simply lose service
  when the Spark is unreachable.
- Stable aliases (`agent`, `fast`, `vision`, …) decouple harness config from whatever model is
  actually loaded.

### The Spark as a free API

Build each capability once as plain HTTP, then wrap it:

```
                    ┌─ OpenAI-compatible  → LiteLLM  (chat, vision, embeddings)
  Spark capability ─┤
                    └─ everything else    → FastAPI "spark-tools"
                                            (ComfyUI workflows, diarization,
                                             media jobs, batch runs)
                                                      │
                                  ┌───────────────────┼───────────────────┐
                              MCP wrapper         CLI wrapper        direct HTTP
                          (OpenCode, Hermes)         (pi)          (my own apps)
```

- Per-app **virtual keys** in LiteLLM, so usage is attributable.
- From my own code it's just `base_url="http://spark:4000/v1"` — `openai`, `chatlas`,
  `chat_openai()` in ellmer.

### Engine preference order

New model architectures land in roughly this order:

1. **vLLM / SGLang** — new architectures first, plus FP8 and NVFP4 quants that actually exploit
   Blackwell.
2. **llama.cpp server** — runs new GGUFs before Ollama catches up.
3. **Ollama** — the convenience option, last to get anything new.
4. **ComfyUI** (image/video), **NeMo** (speech) — separate lanes.

All share **one Hugging Face cache**.

### Beyond inference

RAPIDS / cuDF for GPU pandas, Polars GPU engine, QLoRA fine-tuning, image LoRAs in ComfyUI.
Positron or VS Code over Remote-SSH turns the Spark into a GPU dev box. **NVIDIA AI Workbench**
is a third contender for that role — it manages containerised projects on a remote host over SSH
— but it brings its own daemon and runtime onto the box; see §5.1 and §8.

---

## 4. What the Desktop conversation got right and worth keeping

- **`claude.ai` web connectors cannot reach the tailnet.** Anthropic's servers make those calls
  and they aren't on the tailnet. Only Claude Code and Claude Desktop *running on a device that
  is itself on the LAN or the tailnet* can reach Spark MCP servers. Tailscale Funnel would
  expose them publicly — only ever behind real auth.
- **MCP tool definitions cost context in every session.** Project-scoped `.mcp.json`, never global.
- **pi is not MCP-shaped.** It's built around command-line tools, so it needs a CLI wrapper over
  the same `spark-tools` HTTP API that the MCP wrapper covers for OpenCode and Hermes.
- **Pin container tags.** Trying a new NGC release should be a branch, not a rebuild.
- **MoE models suit this box** — large total parameters, small active set, which is the right
  shape for high memory capacity and modest bandwidth.

---

## 5. Reality checks — where the plan meets GB10

Each of these came out of reading Niels' runbooks and revises the Desktop architecture.

### 5.1 "Run all the engines" collides with one memory pool ⚠️ **biggest issue**

The layered stack implies vLLM, llama.cpp, Ollama, ComfyUI and NeMo all available at once. On
GB10 there is **one unified LPDDR5X pool** shared by CPU and GPU, ~121 GiB total. Niels'
explicit guidance: *"Don't run other GPU workloads on either node while this is up."* He found
`lmstudio.service` silently loading two models at boot and competing for memory, and lists it as
a standing known issue.

**So it is one engine at a time, with swapping — not a buffet.** This has a direct consequence
for LiteLLM: routing to N local aliases only works if *something* loads and unloads models
behind those aliases.

Options to evaluate (undecided):

| Approach | Note |
|---|---|
| `llama-swap` | Proxy that starts/stops llama.cpp servers on demand behind stable aliases. Closest fit to the LiteLLM-alias design. |
| Ollama's auto-unload | Built in and free, but only for Ollama-served models — conflicts with goal #3. |
| Manual / systemd target switching | Simplest, but no automatic alias routing. |
| Small custom supervisor | Most control, most work. |

A realistic middle: one **resident** agent model sized to leave headroom (~40–60 GiB), plus
on-demand engines for burst work that unload after.

**AI Workbench counts as one of the competitors.** A Workbench project container holds GPU memory
for as long as it is up, out of the same pool. If its daemon starts projects at boot, that is
Niels' `lmstudio.service` failure mode exactly — memory silently gone with no obvious cause.

### 5.2 "128GB" is really ~121 GiB, and ~105–110 GiB usable

Keep ≥6 GiB `MemAvailable`. From Niels' inventory: `gpt-oss-120b` (60 GB) is comfortable;
`Qwen3-Next-80B-A3B-Thinking-FP8` (76 GB) fits but leaves little KV headroom; anything above
~110 GB does not fit at all. Full table in [`cosmicbboy-local-ai.md`](cosmicbboy-local-ai.md) §C.

### 5.3 Throughput expectations need calibrating ⚠️

"Fast despite modest memory bandwidth" is optimistic. Measured on real hardware:

- 48 tok/s — llama.cpp, 21 GB Q4 model
- 23.6 tok/s — 111 GB Q4 model across **two** Sparks
- ~75 tok/s — DeepSeek-V4-Flash, but that needs **two** Sparks *and* DSpark speculative decoding

**Tens of tok/s is the realistic band for one Spark on a large MoE.** This matters for goal #2:
agentic harnesses are token-hungry, and a 25 tok/s agent loop feels very different from Claude.
Worth benchmarking a candidate model before committing the whole harness workflow to it.

### 5.4 Two LiteLLM instances, or one?

The Desktop design has LiteLLM on the MacBook (`localhost:4000`, for harnesses + offline
fallback) *and* LiteLLM on the Spark (the app-facing API). That may be deliberate — the Mac one
survives the Spark being off — but it's two configs to keep in sync. Undecided.

**Several client devices sharpen this.** Per-device LiteLLM is N configs to keep in sync, not two,
and only the MacBook can host an MLX fallback anyway. The Spark-side instance is the one every
device can share; a local one is then a MacBook-specific offline convenience rather than part of
the architecture.

### 5.5 Auth from day one, not later

Niels' endpoint has no API key; anyone on his LAN or tailnet can use the model, and he lists it
as a known risk. Since exposing an API for my own apps is an explicit goal, set `VLLM_API_KEY` /
LiteLLM virtual keys **at the start**. Keys by reference from `~/.secrets`, per §2.

**The reach path cannot be the auth layer.** Tailscale and WireGuard each carry an implicit "only
my devices are on this"; the home LAN carries no such promise — a bare `0.0.0.0` bind is reachable
from every phone, TV and IoT device in the house, and from anything that joins the guest network.
With all three paths in play, the endpoint has to authenticate on its own.

### 5.6 The `sm_121` build tax

Anything built from source — llama.cpp, ComfyUI custom nodes, flash-attn variants, vLLM plugins —
needs `CMAKE_CUDA_ARCHITECTURES=121` / `TORCH_CUDA_ARCH_LIST=12.1a` or it silently targets the
wrong arch. Budget 15–25 min per build. Prefer NGC containers where one exists.

### 5.7 Free operational wins to take verbatim

- The **`drop_caches` sidecar** during model load, or large checkpoints wedge for ~20 min with no
  error (§E.1 of the notes).
- **`free -g`, never `nvidia-smi`** — it reports `[N/A]` on GB10.
- **Health-check design**: `active (exited)` ≠ healthy, and HTTP 200 ≠ healthy. A wedged server
  still answers `/v1/models`. Needs a real generation probe.

---

## 6. Decided vs. open

**Decided (from the Desktop conversation, unchanged by the hardware findings):**

- Claude Code stays untouched; Spark never sits in its path.
- LiteLLM provides stable aliases so harness config never chases model changes.
- Hugging Face is the model source; one shared cache.
- `spark-tools` FastAPI carries everything not OpenAI-shaped; MCP and CLI wrappers sit on top.
- Reach over Tailscale, WireGuard, or the home LAN — whichever is up, never relied on for auth;
  per-app virtual keys for attribution.
- The repo is **public**; it carries no addresses, identifiers or credentials (§7 #7).
- Build the **harness path first** — pi on `heartsbane` against a model served from the Spark —
  not the capability path (§9).

**Open — see §7.**

---

## 7. Open questions

1. **Which devices are first-class clients?** ~~Is the MacBook still the 16 GB M1?~~ — answered,
   it is a **16 GB M1 Pro** (§8), so the fallback sizing assumption holds: "keeps working on a
   plane," not a real substitute. Still open is whether a phone, tablet or second machine counts
   as a real client, since none of them can host a fallback at all.
2. ~~**Which gets built first?**~~ — **decided: the harness path.** pi on `heartsbane` talking to
   a model served from the Spark. The capability path (ComfyUI, transcription via MCP) waits.
   See §9.
3. **One LiteLLM or two?** (§5.4)
4. **Model swapping strategy?** (§5.1) This is the decision that shapes everything else.
5. **Where do model weights live** — local NVMe on the Spark, or the Synology NAS? Sharpened by
   the hardware: there is only **1 TB**, shared between weights, the HF cache and NGC images, so
   single-digit large models fit on disk at once. Niels keeps 155 GB checkpoints local and pulling
   those over the network each time would hurt — but with 10GbE to the NAS, cold storage there
   behind a hot local cache looks better than it did — and the box is now wired, so this is live
   rather than blocked (§8). Worth measuring actual NAS read throughput before committing.
6. **Which single model is the daily-driver candidate?** Worth benchmarking two or three for
   real agentic tok/s before the harness work.
7. ~~**Should this repo be public or private?**~~ — **decided: public**
   (`github.com/chendaniely/local-ai`). This is now a standing constraint rather than a question:
   nothing sensitive gets written down here, and because history is permanent, the check happens
   before `git add`. Unlike Niels' repo, **real tailnet IPs are not recorded here.** See
   `CLAUDE.md`.

---

## 8. Current state — 2026-09-23

How things actually stand, as opposed to how they are designed. Update as it changes.

### The Spark — `brightroar`

- **GIGABYTE AI TOP ATOM** (`ATAGB10-9002` rev 1.0) — an OEM DGX Spark variant, not a Founders
  Edition. 128 GB soldered LPDDR5X, **1 TB** NVMe, 10GbE, Wi-Fi 7. Full spec in the
  [README](README.md#hardware). The 1 TB is a constraint §5 does not yet cover: weights, the HF
  cache and container images all share it.
- Renamed from its factory hostname — the product name plus the last two octets of the **wired**
  NIC's MAC. The old name no longer resolves. Note that this makes the factory hostname a partial
  hardware identifier, which is why it is written here in abbreviated form (§7 #7).
- **Wired**, after initially running on the Wi-Fi module. The gap is large enough to matter:
  **1.5 ms** average with 0.3 ms jitter on Ethernet, against **78 ms** with 9 ms of jitter on
  Wi-Fi. Agentic loops pay that on every round trip (§5.3), and it decides §7 #5.
- **Dual-homed, one reserved address per NIC** — Wi-Fi on `.200`, Ethernet on `.201`. The wired
  address is canonical; Wi-Fi is kept deliberately as an out-of-band path for when the box is
  moved or the switch fails. Two NICs must never share a single reservation: they collide if
  both come up.
- ⚠️ **A DHCP reservation only takes effect on a fresh request.** This bit twice — both times the
  box held an older pool lease and ignored a perfectly correct reservation until the interface
  was bounced or the machine rebooted. Assume a stale lease before assuming the router is wrong.
  Bounce with `sudo nmcli device disconnect <iface> && sudo nmcli device connect <iface>`, run
  from the *other* interface so it doesn't sever the session.
- **Pending:** the wired NIC has not yet picked up `.201` — it is still holding an older pool
  address. Bounce the interface from the Wi-Fi side, or reboot.
- **Not on the tailnet.** Reachable only over the home LAN today — so of the three paths in §2,
  exactly one is live, and it is the one with no ACL in front of it. Sharpens §5.5.

### The MacBook — `heartsbane`

- **16 GB M1 Pro**, wired. Confirms the sizing assumption behind the MLX fallback (§7 #1).
- Docker Desktop running with a **7.75 GiB** VM — nearly half the machine, in direct competition
  with any local MLX model.
- Podman Desktop installed but with **no machine created**; it costs nothing as it stands.
- **NVIDIA Sync** and **NVIDIA AI Workbench** installed here, not on the Spark. Workbench's prompt
  to set up a container runtime concerned its *local* context — which on macOS has no NVIDIA GPU
  behind it and so is CPU-only. The configuration that matters is Workbench pointed at
  `brightroar` as a remote location, which installs its own daemon and runtime on the Spark.
- **Unknown:** whether NVIDIA Sync establishes its own connection path to the Spark. If it does,
  that is a fourth reach path carrying its own auth story (§5.5).

---

## 9. Next up — first model serving

**Goal:** a local model running on `brightroar`, reachable from **pi** on `heartsbane` over the
home LAN. This is the first thing actually built in this repo.

Treat it as **architectural**, not a quick install. Whatever is stood up first implicitly settles
the engine, the model, whether anything swaps, and where LiteLLM lives — installing Ollama and
pointing pi at it would quietly answer §7 #4 and give up goal #3.

### The choice to settle first

| Option | What it is | What it defers |
|---|---|---|
| **One resident daily driver** | One model sized to leave headroom, served, with pi pointed at it. §5.1's own "realistic middle". | Swapping (§7 #4), without painting into a corner. |
| **Prove the wire** | Fastest path to pi getting tokens back; explicitly throwaway. | Most things — but answers §7 #6 with measured tok/s instead of estimates. |
| **Durable setup now** | Decide swapping, engine order and LiteLLM topology up front; build once. | Nothing, but nothing is usable until it is all done. |

### Constraints that bear directly on this

- **One engine at a time** (§5.1). Whatever is stood up has to be able to get out of the way.
- **~105–110 GiB usable**, and RAM *is* VRAM — a browser or desktop session on the box eats into
  the same pool.
- **1 TB of disk**, shared by weights, the HF cache and container images (§7 #5).
- **Tens of tok/s is the realistic band** (§5.3). pi is agentic and token-hungry, so a number that
  looks fine in a chat demo may not survive a tool loop.
- **Auth from the start** (§5.5). The LAN is currently the only reach path and has no ACL.
- **`sm_121`** for anything built from source (§5.6).

### Do before building

- Bounce the wired interface so it picks up `.201` (§8).
- Check what DGX OS already ships or has running — an idle engine holding memory is exactly the
  §5.1 failure mode, and it is easier to find now than to debug later.
- Confirm how `pi` expects to be pointed at an OpenAI-compatible endpoint (§4: it is CLI-shaped,
  not MCP-shaped).

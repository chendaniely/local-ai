# local-ai

Personal local-AI stack for a **single DGX Spark (GB10)**, reached from a MacBook or other devices
over Tailscale, WireGuard, or the home LAN.
Planning stage as of 2026-09-23 — almost no code yet; the substance is in the two docs below.

**The machine is a GIGABYTE AI TOP ATOM** (`ATAGB10-9002` rev 1.0), hostname `brightroar` — an OEM
DGX Spark variant, **not** NVIDIA's Founders Edition. In this repo "the Spark" always means this
box. GB10 silicon is identical, so all the GB10 material applies; anything vendor-specific
(recovery media, firmware, support) comes from GIGABYTE, not NVIDIA. Full spec:
[`README.md`](README.md#hardware).

**Read [`planning.md`](planning.md) before proposing any architecture.** It holds the goals, the
constraints, what is already decided (§6), the remaining open questions (§7), the current state of
the box (§8), and the work in flight (§9 — first model serving, which is where to start).
[`cosmicbboy-local-ai.md`](cosmicbboy-local-ai.md) holds the GB10 hardware facts, and
[`changelog.md`](changelog.md) logs what changed on the machine and when.

## Non-negotiable constraints

- **Claude Code and Claude Desktop stay untouched.** No gateway, proxy, `ANTHROPIC_BASE_URL`, or
  globally-installed MCP servers anywhere in the Claude path. The Spark is a *separate* mode for
  OpenCode / pi / Hermes and for my own apps. Violating this defeats the repo's primary goal.
- **One Spark, not two.** The reference repo is a 2-node cluster. Do **not** apply its RoCE, NCCL,
  Ray, or `TP=2` material here — none of it can affect a single node. `cosmicbboy-local-ai.md` §H
  lists exactly what's excluded and why.
- **Secrets by reference only** — `${VAR}` expanded at call time, values in `~/.secrets`, never
  committed or printed. Every config file added here must stay secret-free — see the publishing rule below.

## ⛔ Public repo — history is permanent

`github.com/chendaniely/local-ai` is **public**. Anything committed is world-readable from the
moment it is pushed, and **a later commit that deletes it does not remove it**. It remains in the
history, in every clone and fork, and in GitHub's dangling objects, which stay reachable through
the API long after no branch references them.

**So the check happens before `git add`, never after.** A follow-up "remove secret" commit is not
a fix and must never be treated as one.

Never write into this repo:

- **Credentials of any kind** — keys, tokens, passwords. Names only (`${VLLM_API_KEY}`), never
  values.
- **Tailnet IPs (`100.x`) or full LAN addresses.** A bare last octet (`.200`) is fine where the
  subnet is not also stated.
- **MAC addresses, serial numbers, or other per-unit identifiers.**
- **Personal hostnames or DDNS names that resolve from outside**, including the Synology and
  YouTrack ones.
- **Unread terminal paste.** `env`, `ip addr`, `tailscale status`, `docker inspect` and
  `nvidia-smi` output all carry identifiers. Read it before it goes in.

Internal hostnames (`brightroar`, `heartsbane`) and the GB10 hardware facts are deliberate
exceptions — they are documented because the repo is useless without them.

**If something sensitive does get pushed:** rotate the credential *first* — that is the only step
that actually closes the exposure — then rewrite history with `git filter-repo` and force-push.
Treat the rewrite as cleanup, not remediation; assume the value is already compromised.

## GB10 gotchas that cause wrong work

- **`free -g`, never `nvidia-smi`** — the GPU shares the CPU's LPDDR5X pool and `nvidia-smi`
  reports `[N/A]` for memory.
- **~121 GiB unified memory total**, ~105–110 GiB usable for weights + KV cache. Models over
  ~110 GB do not fit at all. 128 GB is soldered — it is a permanent ceiling, not an upgrade path.
- **Only 1 TB of NVMe**, and weights, the HF cache and NGC container images all share it. That is
  single-digit large models on disk. Don't plan a model zoo; see `planning.md` §7 #5.
- **One memory pool means one engine at a time.** Don't design for vLLM + Ollama + ComfyUI
  resident simultaneously. See `planning.md` §5.1 — the swapping strategy is still undecided and
  it cascades into everything else.
- **Tens of tok/s is the realistic band** on a large MoE. Don't promise more: the reference repo's
  75 tok/s needed *two* Sparks **and** speculative decoding.
- **`sm_121`** — from-source builds need `CMAKE_CUDA_ARCHITECTURES=121` and
  `TORCH_CUDA_ARCH_LIST=12.1a`, or they silently target the wrong arch.

## Conventions

- `cosmicbboy-local-ai.md` labels every claim **`[verified]`** (measured on real GB10 hardware by
  Niels) or **`[adapted]`** (my untested translation to one node). Preserve these labels. Never
  promote `[adapted]` → `[verified]` without an actual measurement on this box.
- Reference repo: <https://github.com/cosmicbboy/local-ai> — Niels Bantilan's 2× Spark stack, read
  at commit `d32fab0`.
- Work here is self-contained (no external stakeholder, no deadline), so it tracks locally in
  `planning.md` — not YouTrack.

## Commits

[Conventional Commits](https://www.conventionalcommits.org/): `type(scope): description`.

Types in use here: `docs`, `chore`, `feat`, `fix`, `refactor`, `build`, `ci`. Scope is the area
touched — `planning`, `readme`, `changelog`, `machine`, `repo`.

**A commit written by an AI/LLM carries a 🤖 immediately after the `type(scope):` prefix:**

```
docs(planning): 🤖 record wired network state and DHCP reservations
chore(repo): 🤖 harden gitignore for public repo
```

Human-written commits use the same format without the 🤖. The marker describes who wrote the
commit, so it applies to anything the model authored — including work a human asked for.

## Environment

- The Bash tool's cwd resets between calls; use absolute paths or `git -C ~/git/hub/local-ai`.

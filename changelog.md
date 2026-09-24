# Machine changelog — `brightroar`

What changed on the box, newest first. [`planning.md`](planning.md) §8 records the *current*
state; this records how it got there.

> This file is public. Per [`CLAUDE.md`](CLAUDE.md): no addresses beyond a last octet, no MACs,
> no serials, no credentials. Paste command lines, not their output.

---

## 2026-09-23 — arrival and first setup

**Hardware.** GIGABYTE AI TOP ATOM (`ATAGB10-9002` rev 1.0) unboxed and powered on. Full
specification in the [README](README.md#hardware).

**Hostname.** Renamed from the factory name — the product name plus a suffix derived from the
wired NIC's MAC — to `brightroar`. The old `.local` name no longer resolves.

**DGX OS update.** Applied from the NVIDIA web interface. The box rebooted and came back with
SSH listening. The reboot also cleared a stale DHCP lease, which is how the first reservation
finally took effect.

**Networking.**

- Started on the Wi-Fi 7 module: ~78 ms RTT from `heartsbane`, ~9 ms jitter.
- Moved to wired 10GbE: **~1.5 ms RTT, ~0.3 ms jitter** — roughly 50× better, and far steadier.
- Router reservations added, one per NIC: Wi-Fi `.200`, Ethernet `.201`. Wi-Fi is kept
  deliberately as an out-of-band path rather than being disabled.
- **A reservation does not dislodge a live lease.** Both NICs held older pool addresses until the
  interface was bounced or the machine rebooted. Assume a stale lease before suspecting the
  router.
- Not yet joined to the tailnet, so the home LAN is currently the only path in.

**Claude Code** installed with the official installer:

```bash
curl -fsSL https://claude.ai/install.sh | bash
```

Version **2.1.281**, installed to `~/.local/bin/claude`. This is the native installer rather than
the npm package, so it self-updates through `claude update`.

The installer warns that `~/.local/bin` is not on `PATH` and suggests appending the export to
`~/.bashrc`. That is correct for the intended use — `ssh brightroar`, then run `claude`
interactively. Ubuntu's `~/.profile` sources `~/.bashrc` for login shells, so the export applies.

Worth knowing only if that ever changes: Ubuntu's default `.bashrc` opens with an early `return`
for non-interactive shells, so an export appended to the *end* of the file does not apply to
`ssh brightroar 'claude ...'` — the one-shot command form would fail with
`claude: command not found`. If Claude Code is ever driven that way from `heartsbane`, or from
cron, move the export above that guard or use the absolute path.

**Why it is there:** to debug directly on the box, and to have Claude on hand when cloning a
repo onto the Spark to investigate something. It runs on the existing subscription, so there is
no reason not to.

This does **not** conflict with the repo's first constraint. That rule protects the Claude setup
on `heartsbane`, which stays pointed straight at Anthropic with nothing in the path; a second
client on another machine, on the same subscription, changes nothing about it. The distinction
that matters: **Claude Code on the Spark talking to Anthropic is just a client.** Claude Code
pointed at a *local* model is the separate, deliberate mode in [`planning.md`](planning.md) §3,
and gets its own command so the default is never altered.

**Google Chrome** installed on the Spark through the DGX Dashboard web interface.

Worth flagging for this box specifically: **RAM is VRAM here.** A browser with a few tabs open
holds 1–3 GiB of the same unified pool that model weights and KV cache come out of (§5.1 in
[`planning.md`](planning.md)). On an ordinary server nobody would think about it; on GB10 it is
worth closing before a large model load, and worth checking with `free -g` if a model that should
fit suddenly does not. The same goes for any desktop session left running.

**On `heartsbane`, not the Spark** — recorded because it was the same day's work: NVIDIA Sync and
NVIDIA AI Workbench installed. Workbench's prompt to set up a container runtime concerned its
local macOS context, which has no NVIDIA GPU behind it. Details in `planning.md` §8.

### Open threads from this day

- Join the tailnet; today the LAN is the only reach path, and it has no ACL in front of it.
- Confirm whether NVIDIA Sync establishes its own connection path (`planning.md` §8).
- Decide the model-swapping strategy *before* installing any inference engine
  (`planning.md` §5.1) — it is the decision everything else hangs off.

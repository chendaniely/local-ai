# Machine changelog — `brightroar`

What changed on the box, newest first. [`README.md`](README.md#current-state) §Current state
records the *current* state; this records how it got there.

> This file is public. Per [`CLAUDE.md`](CLAUDE.md): no addresses beyond a last octet, no MACs,
> no serials, no credentials. Paste command lines, not their output.

---

## 2026-09-24 — gitleaks 8.30.1 replaces the archive build

**gitleaks 8.30.1**, the upstream release binary, installed to `/usr/local/bin` for the repo's
leak-check hooks, following [`website/how-to/leak-guards.md`](website/how-to/leak-guards.md). The
release's checksum is verified before anything is installed:

```bash
cd "$(mktemp -d)"
curl -fsSLO https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_linux_arm64.tar.gz &&
  curl -fsSLO https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_checksums.txt &&
  sha256sum --ignore-missing -c gitleaks_8.30.1_checksums.txt &&
  tar xzf gitleaks_8.30.1_linux_arm64.tar.gz gitleaks &&
  sudo install -m 0755 gitleaks /usr/local/bin/
cd -
```

gitleaks isn't in the Snap Store (checked the same day), so the release binary is the only way to
get a current version on this box.

**Ubuntu's gitleaks 8.16.0 removed**, so only one gitleaks is on the box. It was too old for the
hooks, and with both installed, which one ran depended on `PATH`:

```bash
sudo apt remove gitleaks
```

`gitleaks version` now prints `8.30.1`; shellcheck 0.9.0 stays, from Ubuntu's archive. The Spark's
clone has the leak-check hooks on (`make hooks`), and they refuse the planted private address in
`leak-guards.md`'s drill.

**Bootstrap now installs every apt package the box relies on**, rather than assuming DGX OS ships
it: `git`, `curl`, `openssl`, `shellcheck` and `gh` for the repo and runbooks, and `python3-dev`,
`r-base` and `r-base-dev` for Dan's own work, beside the stack's own packages. All were already
installed here, so adding them to the list changes nothing on this box.

**Wi-Fi always reconnects.** Wi-Fi is the out-of-band path, so it must come back by itself: retry
without limit, and no power saving, which can drop a headless box's link. The connection's name is
private (it is the network's name), so it is a placeholder here:

```bash
sudo nmcli connection modify <wifi-connection> connection.autoconnect-retries 0 802-11-wireless.powersave 2
sudo nmcli connection up <wifi-connection>
```

`autoconnect-retries 0` means retry forever; `powersave 2` turns power saving off.

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
pointed at a *local* model is the separate, deliberate mode that [the plan](website/design/plan.md)
parks as `claude-dgx`, and would get its own command so the default is never altered.

**Google Chrome** installed on the Spark through the DGX Dashboard web interface.

Worth flagging for this box specifically: **RAM is VRAM here.** A browser with a few tabs open
holds 1–3 GiB of the same unified pool that model weights and KV cache come out of (see the
plan's [admission and memory rules](website/design/plan.md#admission-and-memory-rules)). On an
ordinary server nobody would think about it; on GB10 it is
worth closing before a large model load, and worth checking with `free -g` if a model that should
fit suddenly does not. The same goes for any desktop session left running.

**uv** installed with Astral's standalone installer:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Version **0.12.18** (`aarch64-unknown-linux-gnu`), installing `uv` and `uvx` to `~/.local/bin`.
That is the same directory as `claude`, so the `PATH` export above already covers it, along with
the same caveat about non-interactive `ssh brightroar '...'` commands. Like Claude Code, it
self-updates, through `uv self update`.

**R** installed from Ubuntu's own archive:

```bash
sudo apt install r-base r-base-dev
```

That gives **R 4.3.3** (`4.3.3-2build2`), the version frozen into Ubuntu 24.04, not the current
CRAN release. `r-base-dev` brings the compiler toolchain and headers, so packages build from
source. On arm64 that is the normal path anyway, because CRAN publishes no Linux binaries. If a
newer R is ever needed, the route is CRAN's own Ubuntu apt repository (or `rig`), not this
package.

**Python headers** installed from Ubuntu's archive. *(Added 2026-09-24: this was missing here; apt's
own log shows the install on 2026-09-23.)*

```bash
sudo apt install python3-dev
```

**gitleaks** and **shellcheck** installed from Ubuntu's archive, for the repo's leak-check hooks:

```bash
sudo apt install gitleaks shellcheck
```

That gives **gitleaks 8.16.0** (`8.16.0-1ubuntu0.24.04.3`) and **shellcheck 0.9.0**. Ubuntu's gitleaks
build doesn't report its version — `gitleaks version` prints "Version is set by build process" — and 8.16
predates the `gitleaks git` and `gitleaks stdin` commands (added in 8.19) that the hooks use. So the hooks
also need the upstream release binary, 8.30.1, installed to `/usr/local/bin` so it wins in every shell,
interactive or not (Phase 0, Task 9).

**GitHub CLI** installed from Ubuntu's archive, so Dan can push from the Spark:

```bash
sudo apt install gh
```

Ubuntu 24.04 carries **gh 2.45.0** (`2.45.0-1ubuntu0.3` in noble-updates on 2026-09-23), far behind
GitHub's own releases — the MacBook has 2.101.0. That is enough for its one job here: `gh auth login`
in Dan's account, which also sets git up to push over HTTPS (Phase 0, `how-to/spark-session.md`). If
a newer subcommand is ever needed, GitHub's own apt repository carries current releases. With no
keyring on a headless box, gh keeps its token in a plain file under `~/.config/gh/`, guarded only by
Dan's 0700 home — one more reason the `agent` user never shares that account.

**On `heartsbane`, not the Spark** — recorded because it was the same day's work: NVIDIA Sync and
NVIDIA AI Workbench installed. Workbench's prompt to set up a container runtime concerned its
local macOS context, which has no NVIDIA GPU behind it. Details in
[`README.md`](README.md#current-state) §Current state.

### Open threads from this day

- Join the tailnet; today the LAN is the only reach path, and it has no ACL in front of it.
  Scheduled for Phase 0 of the plan.
- Confirm whether NVIDIA Sync establishes its own connection path. *Answered from NVIDIA's
  documentation, 2026-09-23:* it runs over SSH, with key auth set up once and port forwards that
  exist only while it is connected — not a separate path. Not yet checked against this setup.
- Decide the model-swapping strategy *before* installing any inference engine (originally
  `planning.md` §5.1) — it is the decision everything else hangs off. *Decided 2026-09-23:*
  llama-swap plus `spark-gate`, loading a model only when it fits and never evicting or substituting
  ([the plan](website/design/plan.md)).

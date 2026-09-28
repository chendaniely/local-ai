# Machine changelog — `brightroar`

What changed on the box, newest first. [`README.md`](README.md#current-state) §Current state
records the *current* state; this records how it got there.

> This file is public. Per [`CLAUDE.md`](CLAUDE.md): no addresses beyond a last octet, no MACs,
> no serials, no credentials. Paste command lines, not their output.

---

## 2026-09-28 — pi on the Mac and for `agent` (Phase 1, Task 15)

**The Mac's pi** was installed at 0.85.1 (`website/how-to/pi.md`), with `make clients` and
`make tunnel`. Through the tunnel, the coder, `qwen3.6-35b-a3b`, served a real pi task. pi 0.85.1
expands `"${SPARK_API_KEY}"` in its `models.json`, so no key's value sits in either machine's
pi config. Homebrew's pi, 0.87.1, has since taken over the Mac's `pi` command, and it stays:
Dan's decision (plan.md's Revisions).

**`agent`'s Claude Code got the secrets guard first,** before `agent` held any key: the same hook
script and 35 deny rules as Dan's Spark session, paths moved to `/home/agent`, and a `CLAUDE.md`
holding only Dan's secrets rule. Checked: `agent`'s `claude` refused an existence check on its
secrets file.

**Node 22,** by Dan, **on the Spark**: he read NodeSource's `setup_22.x` script, then ran it with
sudo and installed `nodejs` 22.23.3-1nodesource1, with npm 10.9.9. The repository is in
`/etc/apt/sources.list.d/nodesource.sources`, so apt updates it. Ubuntu's own `nodejs` is 18.19,
and pi 0.85.1 needs Node 22.19 or later.

**`agent`'s key and tools.** Root read only `agent`'s llama-swap key from the service secrets and
wrote it, as `agent` and never displayed, into `agent`'s `~/.secrets` as `SPARK_API_KEY`;
`agent`'s `~/.bashrc` loads it first. As `agent`: uv 0.12.19, from its installer, which takes the
latest (the repo records 0.12.18), and pi 0.85.1, both in `~/.local/bin`, and a clone of `phase-1`
in `~/work/local-ai`, used only for `spark clients`. uv built that clone's environment on its own
CPython 3.12.14, and `spark clients pi --write` wrote pi's `spark` provider. Checked: after a new
login, the key loads; pi, in tmux, ran a task on the coder (requests at 12:40–12:43, all `200`);
the session survived a detach, a logout and a reattach; and `claude` starts logged in in a second
window.

**An update took pi to 0.87.1,** the newest release and the last in the range reported to crash
llama-server. `agent`'s pi went back to 0.85.1, and `pi.md` now says to check the version after any
update. The Mac's stays on Homebrew's 0.87.1. Its requests at 12:17–12:18 crashed no engine; its
four `400`s were a 45,822-token conversation sent to Gemma, whose requests top out at 16,384
tokens.

## 2026-09-28 — The web UI on the phone (Phase 1, Task 14)

**Served on the tailnet.** Dan, **on the Spark**:
`sudo tailscale serve --bg --https=443 http://127.0.0.1:3000`. Open WebUI answers over HTTPS on the
Spark's tailnet name, on Dan's phone at home and on mobile data; the address stays out of this
repo. Dan logged in with his admin account. A private tab offers no sign-up at all, and the API
refuses one: a sign-up request to `/api/v1/auths/signup` on the Spark got `403`. The embedding
and speech-to-text models are hidden from the chat picker.

**S09 and S20 passed on 2026-09-28**, on the phone: chat, dictation and voice mode (about 0.25 s
per transcription), a photo question once the fix below was in, and web search answered with its
sources.

**A photo aborted Gemma's engine,** big or small, and so did every later message in that chat,
since each resends the photo. The engine died on `GGML_ASSERT` "non-causal attention requires
n_ubatch >= n_tokens" in `llama-context.cpp`, reached from `mtmd_helper_decode_image_chunk`.
llama.cpp b11146 gives a Gemma 4 image up to 1120 tokens, about 2.6 MP, scaling bigger images
down to that, and decodes an image in one micro-batch, which was the default 512. The registry now
gives Gemma `--ubatch-size 2048` and `--image-max-tokens 1120`. Dan deployed it with
`make apply-now` at 02:30: llama-swap restarted, so every model stopped and loaded again on its
next request. A 3000×2000 test image then came to 1,105 prompt tokens and was described in 4 s.

**Gemma's footprint,** measured again: 18.7 GiB on a cold load, against 17.6 at a micro-batch of
512, and about 1.2 GiB more once it has read its first image. So the registry's estimate rose from
19 to 20 GiB. It reaches the box with the next `make apply`, which restarts only the brake.

## 2026-09-28 — The stack runs (Phase 1, Task 13)

**On the Spark**, in a new Claude session. Dan had logged out and back in, so the session had his
llama-swap key: he sent it from the Mac, and `~/.bashrc` loads it. The session started llama-swap
and the brake at 01:17. It started the web services at 01:20, once Dan's tunnel from the Mac was
open, because the first account made in Open WebUI becomes its admin. Dan made his account at
once, and Open WebUI now reports sign-up closed. Everything listens on 127.0.0.1: llama-swap on
9100 (`/health` answers without a key, `/running` refuses a call without one), the engines on
5800–5803, Open WebUI on 3000, SearXNG on 8888.

**First loads,** one request per model from nothing loaded. `MemAvailable` is given before each
request and at its lowest while it ran. Beforehand, `free -g` showed 70 GiB free and 48 GiB of
page cache, left by the pull.

| Model | Load and answer | Before → lowest (GiB) | Footprint (GiB) | Registry estimate |
|---|---|---|---|---|
| `gemma-4-26b-a4b` | 9 s | 117.8 → 100.2 | 17.6 | 19 |
| `qwen3-embedding-0.6b` | 2 s | 100.2 → 96.9 | 3.3 | 1.5, raised to 4 |
| `whisper-large-v3-turbo` | 2 s | 96.9 → 94.4 | 2.5 | 3 |
| `qwen3.6-35b-a3b` | 10 s | 94.4 → 68.0 | 26.4 | 29 |

The embedding had 1024 dimensions. Whisper transcribed the JFK sample on the GPU, with word
timings. Both chat models think by default, with no limit. On the test's 512-token cap each spent
every token thinking and returned no text; given room, Gemma answered after about 2,200 tokens.
They generated at 78 tokens/s (Gemma) and 93 (the coder). Each reply names its model by the GGUF
file's path, which is what llama-server does without `--alias`.

**What runs, as whom.** Each engine runs as `spark`, with `oom_score_adj` 1000. Their RSS was 1.6,
0.6, 0.4 and 2.1 GiB (Gemma, embeddings, whisper, coder). `nvidia-smi`, which does report
per-process memory here, gave 16.3, 2.8, 2.0 and 24.5 GiB. Their `oom_score`s were 1340, 1336, 1334
and 1343. So a model's GPU memory doesn't count toward its engine's RSS. Dan's earlyoom dry run
would kill Gemma's engine. That's an engine, as intended, but a resident model ahead of the
on-demand coder, the opposite of the brake's order. By then Gemma's RSS had grown to 5.7 GiB.
`make doctor`: 15 of 15. llama-swap's and the pull's journals hold no permission error. SearXNG
searches: 26 results, then 35 once warm; duckduckgo times out, taking 2.7 s from here against a
3 s limit. Its log holds no permission error, and its two ownership warnings are expected.

**Two changes at 01:51.** Dan ran `make apply`, `make install-units` and `make apply` again. The
embedding model's `footprint_gib` rose from 1.5 to 4. And Open WebUI's task calls now run without
thinking: `TASK_MODEL_PARAMS` in root's Compose file, Dan's decision (plan.md's Revisions). The
brake and the web services restarted; llama-swap and the loaded models didn't. A new chat's title
then came back in 1.2 s, 997 bytes, while the chat itself streamed 7.4 s of thinking and answer.

## 2026-09-27 — Deployed, and the models pulled (Phase 1, Task 12)

**Bootstrap re-run,** by Dan, **on the Spark**, from the clone on `phase-1`:
`make bootstrap-dry-run`, then `make bootstrap`. `/var/lib/local-ai` is now root's
(`root:root 755`), and `spark` has two cache folders, `/var/lib/local-ai/cache` and
`/var/lib/local-ai/cuda-cache` (`spark:spark 750`). earlyoom avoids `sshd.*`:
`/etc/default/earlyoom` matches the repo's. needrestart's override is in
`/etc/needrestart/conf.d/local-ai.conf`. The polkit rule is the narrowed one: `spark-admin` starts,
stops and restarts the four `local-ai-*` units, by exact name, and nothing more. It has no
`reload-daemon`, which `pkcheck` confirmed by answering `2`. The GPU set stayed held, 151 packages
of 151, and upgrade day's release list is exactly the hold list. `make doctor`: 10 of 15, its three
bootstrap lines now passing; the other five waited for the deploy.

**The GRUB check,** Dan's, with sudo (`updates.md` step 5): passed. The newest kernel is
`7.0.0-1019-nvidia`, and entry 0's `linux` line boots it. The `default=` lines are the stock two,
so GRUB starts entry 0, and `grub-editenv list` is empty. The installed kernels are
`linux-image-6.11.0-1016-nvidia` and `linux-image-7.0.0-1019-nvidia`, each named with the version
`uname -r` prints.

**Root's own copies.** Dan ran `make apply`, which staged them, and `make install-units`. The four
units are in `/etc/systemd/system/local-ai-*.service` and the Compose project in
`/etc/local-ai/compose`, `root:root`, files 644 and folders 755. llama-swap, the brake and the web
services are enabled; the pull unit is `static`. A second `make apply` deployed `llama-swap.yaml`,
`models.yaml` and the app in `/opt/local-ai/app`, and started nothing.

**The models.** `make pull` ran 11 min 38 s as `spark` and downloaded five files, about 36 GiB,
into `/var/lib/local-ai/hf`. Each is at its pinned revision and at the path the config gives its
engine, and none of the repos is gated. 752 GiB of disk is left. The pull's cgroup peaked at
39.9 GiB, which is page cache: `free -g` then showed 43 GiB of it (plan.md, *Page cache and the
launch check*).

## 2026-09-27 — The engines, at their pins (Phase 1, Task 11)

Installed **on the Spark**, as Dan, following Task 11 of
[the Phase 1 plan](website/design/phase-1.md). Nothing runs them yet; Task 12 deploys the config
and starts the stack. The driver was **580.178.04** and `nvcc` **13.0**, the GPU set as bootstrap
held it.

- **llama-swap v257** → `/opt/local-ai/bin/llama-swap/v257/llama-swap`. The release tarball
  matched the checksum already pinned in `stack/versions.yaml`; `-version` reports `v257 (f00d375)`.
- **llama.cpp b11146**, the prebuilt `ubuntu-cuda-13.4-arm64` build and its `cudart-` companion,
  → `/opt/local-ai/bin/llama.cpp/b11146/`. Both tarballs matched the sha256 digests GitHub
  publishes for the release's assets; the binaries' tarball is the pin
  (`sha256:4e00496a…`, in full in `stack/versions.yaml`). `--version` reports build 11146, commit
  `7fe450e19`; `--list-devices` shows the GB10 as `CUDA0`. `libggml-cuda.so` loads the CUDA
  runtime (it reports 13.4) and cuBLAS copied beside it; of its CUDA libraries, only the driver's
  `libcuda.so.1` comes from the system. Dan then checked that `agent` reaches the GPU without
  docker: `sudo -u agent /opt/local-ai/bin/llama.cpp/b11146/llama-server --list-devices` lists the
  same `CUDA0`, the GB10.
  `cuobjdump --list-elf libggml-cuda.so` lists `sm_86`, `sm_89`, `sm_120a` and **`sm_121a`** —
  native code for this GPU, so a first load needn't compile PTX.
- **whisper.cpp v1.9.4**, built from source (there's no CUDA prebuilt) at commit `927cfce3…`
  (the pin, in full in `stack/versions.yaml`), → `/opt/local-ai/bin/whisper.cpp/v1.9.4/whisper-server`.
  CMake ran with `-DGGML_CUDA=1 -DCMAKE_CUDA_ARCHITECTURES=121a-real -DBUILD_SHARED_LIBS=OFF`
  and reported `Using CMAKE_CUDA_ARCHITECTURES=121a-real`, with no warning about an unsupported
  architecture (its three warnings were no ccache, `-mcpu=native`, and no NCCL, which matters
  only with several GPUs). Checked with `cuobjdump --list-elf` on `whisper-server`: it lists
  **`sm_121a`** alone, and `--list-ptx` lists nothing. That it *runs* on the GPU is Task 13's
  check. Unlike llama.cpp it uses the **system's CUDA 13.0 runtime**
  (`/usr/local/cuda-13.0`, found through `ld.so.conf` and the binary's runpath), so it moves with
  the held GPU set: `updates.md`'s upgrade-day check (step 7) now checks that it still finds its
  libraries, and says to rebuild it if not.
  The clone stays in `~/src/whisper.cpp`; its `samples/jfk.wav` is Task 13's speech test.

Each engine's path in `stack/models.yaml` names the version pinned in `stack/versions.yaml`
(checked by hand; `spark render` doesn't cross-check them).

## 2026-09-26 — `spark/`'s environment on uv's own Python

The clone's `spark/.venv` had been built on Ubuntu's Python 3.12.3. `spark/pyproject.toml` now
sets `python-preference = "only-managed"`, so uv uses only its own interpreters. uv installed
CPython 3.12.14 for Dan, which also linked `python3.12` in `~/.local/bin`, and the environment was
rebuilt on it. **On the Spark**, in the clone:

```bash
uv python install 3.12
rm -rf spark/.venv && uv sync --frozen --project spark
```

The tests, lint and the leak check then passed on it.

## 2026-09-25 — Before Phase 1: keys-only SSH, a GitHub token for this repo, the session's guard

Phase 0 left these for the box
([its retrospective](website/design/phase-0-retro.md#carried-to-phase-1)), and Phase 1's switch
point wants them before anything faces the network.

**Keys-only SSH**, following [SSH from the Mac](website/how-to/ssh.md#keys-only), steps 1 to 5.
The drop-in `/etc/ssh/sshd_config.d/10-local-ai.conf` matches the runbook's byte for byte. sshd
accepts keys only, and `agent`'s sessions never get a forwarded SSH agent. Step 4's checks passed
from the Mac: the three aliases logged in with keys, over the tailnet and the LAN, and a login
without a key ended in `Permission denied (publickey).` The one-time public IPv6 check is done
too; its result goes in the vault.

**GitHub: a token for this repository only**, following
[The Spark session](website/how-to/spark-session.md#github-a-token-for-this-repository-only).
`gh` holds a fine-grained token for `chendaniely/local-ai` alone. But the clone's `origin` was
SSH, and the Spark's SSH key (`~/.ssh/id_ed25519`) was on Dan's GitHub account, so `git push`
went around the token and could reach every repository Dan can. `origin` moved to https, where
`gh` hands git the token. **On the Spark:**

```bash
git -C ~/git/hub/local-ai remote set-url origin https://github.com/chendaniely/local-ai.git && gh auth setup-git
```

Checked on the Spark after the switch: a dry-run push to this repository passes, and one to
another of Dan's repositories is refused with a 403. Dan then removed the Spark's key from Dan's
GitHub account, on github.com, and `ssh -T git@github.com` from the Spark now ends in
`Permission denied (publickey).`

**The Spark's Claude session** has the Mac's global rules and secrets guard
([The Spark session](website/how-to/spark-session.md#before-the-first-session), steps 2 and 3):
`~/.claude/CLAUDE.md` and `~/.claude/hooks/block-secret-access.sh` match the Mac's copies by
SHA-256, `~/.claude/settings.json` carries the hook and 35 deny rules, and the session is refused
`test -e ~/.secrets && echo present || echo absent`.

**Recorded:** Tailscale **1.102.4**, the kernel's release string `7.0.0-1019-nvidia`, and
OpenSSH **1:9.6p1-3ubuntu13.19** (Ubuntu's `openssh-server`). **On the Spark:**

```bash
tailscale version
uname -r
dpkg-query -W openssh-server
```

## 2026-09-24 — Phase 0 bootstrap: headless, users, firewall, tailnet, secrets

**Wired NIC on `.201`**, bounced from a session on the Wi-Fi address:

```bash
sudo nmcli device disconnect <wired-iface> && sudo nmcli device connect <wired-iface>
```

**Bootstrap applied, then re-run once** (no errors, nothing duplicated), following
[`website/how-to/bootstrap.md`](website/how-to/bootstrap.md):

```bash
make bootstrap
```

- Packages installed or confirmed: the stack's, plus the repo's tools and Dan's own (the list is in
  `stack/host/bootstrap.sh`).
- The GPU set held: kernel, NVIDIA modules, driver and CUDA, 151 packages.
- Users `spark` and `agent`, and groups `spark`, `spark-users` and `spark-admin`. Dan joined
  `spark-admin`, `spark-users` and `adm`. Both homes are 0700.
- `/opt/local-ai`, `/etc/local-ai`, `/etc/local-ai/secrets`, `/var/lib/local-ai` and its subfolders,
  and `/home/agent/work`.
- Boots to a console (`multi-user.target`); the display manager is stopped.
- earlyoom installed, configured from `stack/host/earlyoom.default`, and running. systemd-oomd is
  inactive, so there's one out-of-memory killer.
- ufw on: deny incoming, allow OpenSSH (IPv4 and IPv6).
- The polkit rule for `local-ai-*` units.

**agent**: its own SSH key from the Mac, and Claude Code installed as agent:

```bash
sudo install -d -m 700 -o agent -g agent /home/agent/.ssh && sudo tee -a /home/agent/.ssh/authorized_keys < ~/agent-key.pub >/dev/null && sudo chown agent:agent /home/agent/.ssh/authorized_keys && sudo chmod 600 /home/agent/.ssh/authorized_keys && rm ~/agent-key.pub
curl -fsSL https://claude.ai/install.sh | bash    # as agent
```

The live checks passed. agent can't enter Dan's home, is refused by Docker, and `nvidia-smi` as
agent names the GPU (`NVIDIA GB10`). `sudo docker ps -a` lists no containers.

**Tailscale** installed and joined, following
[`website/how-to/tailscale.md`](website/how-to/tailscale.md). In the admin console, MagicDNS and
HTTPS certificates are on. The allow-all policy was replaced: Dan's devices reach each other, and
reach `tag:spark` on 22 and 443. There is no route home, by choice. The Spark is tagged, so its
key doesn't expire:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
sudo tailscale up --advertise-tags=tag:spark
```

**Secret files** for Phase 1 created in `/etc/local-ai/secrets/` (`llama-swap.env`,
`open-webui.env`, `searxng.env`, `hf.env`; `640 root:spark`), following
[`website/how-to/secret-files.md`](website/how-to/secret-files.md). The Hugging Face token is a
fine-grained, read-only one. Values are never displayed; the vault records the names.

**Verified afterwards (Phase 0, Task 11)**:
- Memory, headless (`free -g`): 121 total, 2 used, 118 available.
- earlyoom received its regexes without quotes.
- A dry run of earlyoom picks a desktop helper to kill, never anything on the avoid list.
- Starting a transient `local-ai-probe` unit without a password is refused, so the polkit rule
  doesn't reach transient units.
- Dan's sessions can't list `/etc/local-ai/secrets`.

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

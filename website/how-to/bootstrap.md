---
title: "Bootstrap the Spark"
description: "Bounce the wired NIC, run make bootstrap, check the results, set up agent's SSH and Claude Code login, and run Phase 2a's re-run."
---

Everything here runs **on the Spark**, as you, in an SSH session from the Mac, except where a step
says the Mac or `agent`.

## Before you start

1. **On the Mac**, SSH into the Spark over **Wi-Fi** (its `.200` address) — not the wired
   interface. Bouncing the interface you're connected through would drop your own session.
2. **On the Spark**, find the wired interface's name: `nmcli device status`.
3. **On the Spark**, bounce it so it takes its DHCP reservation:

   ```bash
   sudo nmcli device disconnect <wired-iface> && sudo nmcli device connect <wired-iface>
   ```
4. **On the Spark**, confirm the wired address now ends in `.201`.

## Run it

**On the Spark**, as you, from the clone (`cd ~/git/hub/local-ai`), in an SSH session. Type
`make bootstrap`, never `sudo make bootstrap`: the target asks for your password itself, and under
an outer `sudo` it would give your groups to root.

1. `make bootstrap-dry-run` — read the output before doing anything else. Its hold step prints
   `GPU set: N packages, M already held`: on a bootstrapped Spark, all of them held (151 at the
   2026-09-24 bootstrap). Off the Spark (the Mac, CI), with no DGX kernel installed, it says
   `a real run stops here` instead. A package that isn't cleanly installed stops the dry run too,
   with a hint for finishing it.
2. `make bootstrap` — run it over SSH; it stops any running desktop session.

## After bootstrap

- Log out and back in — new group membership only takes effect on a fresh login.
- `systemctl get-default` → `multi-user.target`
- `systemctl is-active earlyoom` → `active`
- `sudo ufw status` → OpenSSH allowed
- `id agent` → no `docker`, `sudo` or `spark-admin` in the list
- `apt-mark showhold | grep -E '^(linux-image-nvidia-hwe|nvidia-driver|cuda-toolkit)-'` → the
  kernel, the driver and CUDA among the held GPU set. From here on, updates follow
  [Updates](updates.md).
- `free -g` → record this as the new baseline

## Agent's SSH and Claude Code login

1. **On the Mac**, make agent its own key and copy the **public** half to the Spark. Keys and
   the Mac's `~/.ssh/config` are in [SSH from the Mac](ssh.md):

   ```bash
   ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519-brightroar_agent -C "agent@brightroar"
   scp ~/.ssh/id_ed25519-brightroar_agent.pub brightroar:agent-key.pub
   ```
2. **On the Spark**, install it for `agent`, from an interactive `ssh brightroar` session. `sudo`
   needs a terminal to ask for your password, and a command piped into `ssh` doesn't have one:

   ```bash
   sudo -u agent sh -c 'umask 077 && mkdir -p /home/agent/.ssh && cat >> /home/agent/.ssh/authorized_keys' < ~/agent-key.pub && rm ~/agent-key.pub
   ```

   Your shell opens the key file and hands it over on standard input, and `agent` writes it into
   its own home: `umask 077` makes `.ssh` 0700 and `authorized_keys` 0600 if they are new. Root
   never writes, `chown`s or `chmod`s anything under `/home/agent`. `agent` controls that folder, so
   a link it planted there could turn a root write into a write to any file on the box. The copy
   is deleted only once the key is in. Run again, it stops at the missing copy and changes
   nothing; with a fresh copy, it adds the key a second time, which is harmless.
3. **On the Mac**, log in as `agent`: `ssh brightroar-agent` (the alias from
   [SSH from the Mac](ssh.md))
4. **On the Spark**, as `agent` — the installer refuses to run under sudo:

   ```bash
   curl -fsSL https://claude.ai/install.sh | bash
   ```
5. **On the Spark, still as `agent`**, log in: run `claude`. With no browser on the box, press `c`
   to copy the login URL, open it on the Mac, and paste the code back into `agent`'s `claude` on
   the Spark. *(Added 2026-09-28: its secrets guard comes in [pi](pi.md), before `agent` gets a
   key.)*

`agent` makes its own `~/work` too, the same way: its first `git clone` into it creates it.
Bootstrap doesn't, since it runs as root.

## Live checks (you run these — they need sudo)

**On the Spark, as you** — not in step 3's `agent` session, which has no sudo:

- `sudo -u agent sh -c 'if test -x "$1"; then echo "OPEN: stop and fix permissions"; else echo "closed: good"; fi' _ "$HOME"`
  → `closed: good`. `$HOME` expands in your shell, so it names your home whatever your login is;
  `test -x` asks whether `agent` can enter it at all, without reading anything in it. The verdict
  comes from `agent`'s own shell, so a failed `sudo` shows its error, never a false "good". Plain
  `-u`, not `-iu`: `-i` re-reads the command in `agent`'s login shell, which would expand `$1`
  there, to nothing.
- `sudo -iu agent docker ps` → permission denied
- `sudo -iu agent nvidia-smi --query-gpu=name --format=csv,noheader` → the GPU's name, without the
  per-unit UUID that `nvidia-smi -L` prints

## Re-run once

`make bootstrap` again → finishes with no errors, and no new users, groups or config lines.

## Phase 2a's re-run

Phase 2a's bootstrap (its Task 27; Dan runs it in Task 36, before the cutover) adds:

- **Two system users**, neither of which can log in (`/usr/sbin/nologin`): `spark-front`, which
  runs the front, in its own group and `spark-users`, with no home; and `spark-pull`, which runs the
  model pull, in its own group alone, sharing `spark`'s home, `/var/lib/local-ai`, which is root's.
  `spark-admin` still holds only you.
- **The secrets folder root's alone:** `root:root 0700`, and every regular file in it
  `0600 root:root`, at each run. PID 1 and root's Compose read them; nothing reads them as `spark`.
  [Secret files](secret-files.md) has the files 2a adds.
- **The folders 2a's units write:** the gate's, `/var/lib/local-ai/gate`, launch's,
  `/var/lib/local-ai/launch` with its `tickets`, `started` and `refusals`, and whisper's
  `--tmp-dir`, `/var/lib/local-ai/whisper-tmp`, each `spark:spark 0750`; and the pull's own cache,
  `/var/lib/local-ai/pull-cache`, `spark-pull:spark-pull 0750`. launch's own folders are made by
  `spark`, since root never makes a folder inside one `spark` controls.
- **The Hugging Face cache moves to `spark-pull`:** `/var/lib/local-ai/hf` becomes
  `spark-pull:spark 2750`, every folder in it setgid, so the engines, running as `spark`, read
  every model file through the group and can't change one. Its owners change once, with
  `chown -R -P --no-dereference`, which never follows a link. `hf/tmp`, whisper's `--tmp-dir` in
  Phase 1's registry, stays `spark`'s while the deployed registry names it; the close removes it
  (Phase 2a's Task 51).
- **The failure notifier's script**, `/usr/local/libexec/local-ai-notify`, root's `0755`, which
  `local-ai-notify@.service` runs when the front, the gate, the brake or llama-swap fails.

The cache's move refuses, failing the run and changing nothing in the cache, while any unit that
runs as `spark` or `spark-pull` is active (llama-swap, the brake, the pull, the gate or the brake's
drill), since root changes owners in folders `spark` controls until then, or while
`fs.protected_hardlinks` isn't 1. Once the cache is moved, a re-run passes it, whatever runs.

**On the Spark**, as you, from the clone, in an SSH session:

1. `make bootstrap-dry-run`, and read it. While the stack runs, its cache step ends
   `(a real run stops here: local-ai-llama-swap.service is active; local-ai-brake.service is
   active)`.
2. Open a second SSH session to the box, and leave it open until a fresh login works after the
   run. *(Phase 2a's Task 28 makes bootstrap change sshd's PAM stack, for `agent`'s OOM score, and a
   broken PAM stack would lock new logins out. Until Task 28 is built, this run doesn't touch PAM,
   and the second session is only a precaution.)*
3. Stop the brake and llama-swap, which stops every model. The polkit rule allows it without
   sudo. **On the Spark:**

   ```bash
   systemctl stop local-ai-brake.service local-ai-llama-swap.service
   ```

   The pull mustn't be running either: `systemctl is-active local-ai-pull.service` →
   `inactive`.
4. `make bootstrap`.
5. Start them again. The stack serves in Phase 1's layout until the cutover, reading the model
   files through the group. Until then `make pull` can't add a file to the cache: root's copy of
   the pull's unit still runs as `spark`, which now only reads it, and the cutover installs the
   unit that runs as `spark-pull`. **On the Spark:**

   ```bash
   systemctl start local-ai-llama-swap.service local-ai-brake.service
   ```

6. Check it. **On the Spark:**

   ```bash
   getent passwd spark-front spark-pull | cut -d: -f1,7
   id -nG spark-front
   stat -c '%U:%G %a %n' /var/lib/local-ai/hf /var/lib/local-ai/gate /var/lib/local-ai/launch /var/lib/local-ai/whisper-tmp /var/lib/local-ai/pull-cache
   test -x /usr/local/libexec/local-ai-notify && echo "notifier: installed" || echo "notifier: missing"
   ```

   Expected: both users with `/usr/sbin/nologin`; `spark-front spark-users`; `spark-pull:spark
   2750 /var/lib/local-ai/hf`; `spark:spark 750` for the gate's, launch's and whisper's folders;
   `spark-pull:spark-pull 750` for the pull's cache; `notifier: installed`.

   Then, with sudo, what your account can't see: launch's own folders, `hf/tmp` and the model
   files. **On the Spark:**

   ```bash
   sudo stat -c '%U:%G %a %n' /var/lib/local-ai/launch/tickets /var/lib/local-ai/launch/started /var/lib/local-ai/launch/refusals /var/lib/local-ai/hf/tmp; sudo find /var/lib/local-ai/hf -type f ! -perm -g=r | head -3; sudo -k
   ```

   Expected: `spark:spark 750` for each, `hf/tmp` included, left for whisper until the cutover; and
   no file listed after them, since every model file must be one the group can read. If one is
   listed, the engines can't load it: bring it to the Claude session working on the repo.
7. `make doctor`: its *secrets folder* and *spark's folders* lines pass.

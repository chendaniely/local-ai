---
title: "ntfy on the Synology"
description: "ntfy for the Spark's alerts, on the Synology in Marius Hosting's style: a Compose file pasted as it is, a private server.yml, the tailnet grant, the users and tokens, the phone, and the Spark's values file and header files."
---

From ~~Phase 2a~~ Phase 5, the Spark's alerts reach your phone through ntfy, which runs on the
Synology. This runbook sets it up once (Phase 2a's Task 2, which now runs in Phase 5): the NAS on
the tailnet and its grant, the users and tokens, the deploy, the phone, and the files on the Spark
that the gate, the failure notifier and the brake read.

*(2026-10-14, Dan's decision: ntfy optional, moving with the NAS phase; Phases 4 and 5 swapped, the
NAS phase now 5.)* **This runbook is Phase 5's.** ntfy needs a Synology homelab setup Dan isn't
ready for yet, so it waits for Phase 5, NAS and backups, and Phase 2a finishes without it. Until
then the stack runs without ntfy: no alert leaves the Spark, the gate, the failure notifier and the
brake say in their journals that ntfy isn't set up, and `spark status` and `make doctor` read *ntfy
not set up*. The page stays as written for Phase 5, which runs it and corrects what it finds; §8
says what changes when it runs on a stack that is already up.

ntfy deploys from two files, as in Marius Hosting's guide (below). The Compose file,
`stack/synology/ntfy/compose.yaml`, is public, holds nothing private and is pasted as it is, with no
variables. ntfy's `server.yml` is private: §3 writes it on the Spark and §4 uploads it to ntfy's
folder on the NAS, `/volume1/docker/ntfy`. It holds everything private: the NAS's address, the
users' hashes, the topic names and the tokens. `stack/synology/ntfy/server.example.yml` shows its
shape with placeholders. The settings that aren't private stay in the Compose file, where the repo
shows them and where ntfy takes them over `server.yml`, so a `server.yml` that loses a line leaves
ntfy closed to anyone without a login, and a missing one stops ntfy from starting. Any Compose
helper deploys it: Portainer's stacks today (§4), plain `docker compose` or another helper (§5).

*(Corrected 2026-10-08, Dan's decision: the private values were seven variables that Portainer
held, pasted into the stack's environment; they moved into `server.yml`, so the Compose file pastes
as it is and Portainer holds nothing. The cost: `server.yml` keeps the hashes and the tokens in a
file in the NAS's `docker` shared folder, which anyone who can read that folder can read; each token
can only publish to its own topic. The port, 6741, and the folder, `/volume1/docker/ntfy`, are now
written in the Compose file. `TZ` went: in a public file it would name a place, and on an ARM NAS it
does nothing, since the image's arm64 build has no timezone data.)*

**Marius Hosting's guide is the reference** (Dan's decision, 2026-10-08):
[How to Install ntfy on Your Synology NAS](https://mariushosting.com/how-to-install-ntfy-on-your-synology-nas/).
Its screenshots show DSM's and Portainer's screens for the same steps. Follow it with these
changes:

| In his guide | Here |
|---|---|
| Portainer | the same |
| A wildcard certificate, and DSM's reverse proxy for a DDNS name | skip them: the phone and the Spark reach ntfy at the NAS's tailnet name (§2), with nothing in between |
| Text Editor | optional, for reading `server.yml` later; it shows the tokens |
| The folders `docker/ntfy` and `docker/ntfy/cache` | the same (§4, step 1) |
| His `server.yml`, downloaded, then uploaded to `docker/ntfy` | the one §3 writes, uploaded the same way (§4, step 2) |
| His stack in Portainer | this repo's `compose.yaml` (§4, step 3): his container name, folders, healthcheck and port 6741, with the image pinned, the login, and `restart: unless-stopped` |
| Opening ntfy in the browser, and subscribing | §7: the phone, with its login |
| Uptime Kuma, and DSM's webhook | skip them: under the login, each would need a user and a token of its own |

The restart policy differs because Docker doesn't restart an `on-failure` container when its daemon
restarts: his `on-failure:5` would leave ntfy down after a NAS reboot or a DSM update, and your
alerts silent.

Each step says where it runs: DSM on the Synology, Portainer, Tailscale's admin console, the phone,
the Spark, or the Mac.

**What has been run.** Every command for the Spark was run on 2026-10-07 (Phase 2a's Task 1), with
stand-in values: §3's against ntfy v2.28.0's own release binary, which then served the values they
made, through `docker compose config`, on 127.0.0.1; §8's and §9's against a temporary folder in
place of `/etc/local-ai`, and that server; §5's check and §6's digest as written. Not yet run: the
Mac's clipboard commands, §5's `docker compose up -d`, and every step in DSM, Portainer, Tailscale's
console and the phone. Portainer's steps are written from its documentation and its source
(2.39.8); Task 2 runs them on the NAS and corrects this page.

*(Added 2026-10-08, for the file in Marius Hosting's style, before the full `server.yml`:)* §3's
blocks ran again on stand-ins, the timezone line with a stand-in `timedatectl` (the Spark's own was
checked only for giving an `Area/City` name, never shown), and §5's check with their seven values,
given with `--env-file` rather than as a `.env`: silent, and naming `NTFY_TZ` once its line was
taken out. The healthcheck ran against the pinned image's own files, fetched from Docker Hub by its
digest: its arm64 build's `ntfy` (byte for byte the release binary) served the environment
`docker compose config` resolved from stand-ins, with the folder and its `cache` subfolder in a
scratch folder, on 127.0.0.1; and the check itself ran in that build's busybox (`sh`, `wget` and
`grep`). It passed with ntfy up and failed with ntfy stopped, and `/v1/health` answered without a
login while a topic refused reads and publishes without one. Docker itself didn't run the
container, since the session had no access to Docker's daemon, so the port was a free one in place
of 80, and Task 2 sees the container's health on the NAS. The amd64 build was read, not run: it has
busybox's `wget` and `grep` too.

*(Added 2026-10-08, for the full `server.yml`:)* §3's blocks ran again on stand-ins, in a scratch
home: the release binary checked and unpacked, the hashes, tokens and topics made, the phone's
password and the NAS's name given on standard input. ntfy v2.28.0's release binary then served the
`server.yml` they wrote, with the Compose file's environment, its two paths moved into a scratch
folder and the port a free one on 127.0.0.1. Each publisher's token published to its own topic
(`200`) and to no other (`403`); no token published nowhere (`403`); the phone's user read a topic
and published to none; `/v1/health` answered without a login; and `user.db` and `cache.db` appeared
where the environment puts them. With `server.yml` missing, ntfy refused to start (`config file …
does not exist`). With its login lines gone, or with `auth-default-access: read-write` and another
`auth-file` written into it, ntfy still refused everyone without a login and kept its auth database
where the environment says. `docker compose config` read the Compose file with no variables. Not
run: §4's upload and Portainer's steps, the Mac's `scp`, and the phone.

## 1. The NAS on the tailnet

If the NAS already shows under **Machines** in Tailscale's admin console, skip to the tag.

**In DSM, on the Synology:** *Package Center* → search for *Tailscale* → *Install*, then *Open*,
and log in to your tailnet in the page it opens.

**The tag.** A grant can name the Spark because the Spark carries `tag:spark`
([Join Tailscale](tailscale.md#acl-grants-are-the-firewall)). The NAS gets `tag:nas` so that §2's
grant can name it. Save §2's policy first, then **in Tailscale's admin console**: *Machines* → the
NAS → *…* → *Edit ACL tags* → add `tag:nas` → *Save*. A tagged device's key doesn't expire.

⚠️ **Tagging changes who reaches the NAS.** A tagged device belongs to its tag, not to you, so it
leaves `autogroup:member`, and the grant your devices reach each other through stops covering it.
§2's first grant keeps your devices reaching it as before: save it before you tag the NAS, or they
lose the NAS until you do. A tagged NAS also starts no connection to your other devices, since no
grant names `tag:nas` as a source; if something on the NAS reached another machine over the tailnet
(a backup, say), give it a grant of its own.

## 2. The grant

ntfy's port on the NAS is 6741, Marius Hosting's, written in the Compose file. If another app on
the NAS already uses it, stop, and change it with the Spark's session: it is in the Compose file, in
the grant below and in §3's address. The Compose file publishes it on every one of the NAS's interfaces, so it is
your router that keeps it off the internet: never forward it there. Don't count on DSM's firewall
to narrow it instead: reports differ on whether DSM's rules reach a port that Docker publishes for a
container. *(Corrected 2026-10-08: the port was yours to pick, with 6741 suggested; the Compose
file now names it.)*

Write the change into the vault's copy of the ACL policy first, then make it **in Tailscale's admin
console**, *Access controls*, in the JSON editor. The NAS's tag gets an owner beside the Spark's:

```json
"tagOwners": {
  "tag:spark": ["autogroup:admin"],
  "tag:nas": ["autogroup:admin"]
},
```

and two grants join [Join Tailscale's three](tailscale.md#acl-grants-are-the-firewall):

```json
// Your devices, the phone among them, reach the NAS as they did before it was tagged.
{"src": ["autogroup:member"], "dst": ["tag:nas"], "ip": ["*"]},
// The Spark reaches ntfy on the NAS: its port, nothing else.
{"src": ["tag:spark"], "dst": ["tag:nas"], "ip": ["6741"]}
```

Nothing else widens. The first grant gives your devices what the member grant gave them before the
tag, and it is how the phone reaches ntfy's port; the second is the only new path, from the Spark to
one port on the NAS. Then show the two grants to the Spark's Claude session in the chat, never in a
file: Phase 2a's Task 2 checks that they let exactly the Spark and your devices reach ntfy's port
and widen nothing else. The policy itself stays in the vault.

## 3. The values

What `server.yml` holds, and where else each value is kept *(corrected 2026-10-08: these were
seven Compose variables, which Portainer held)*:

| In `server.yml` | What it holds | Also kept in |
|---|---|---|
| `base-url` | ntfy's address as the phone and the Spark reach it: `http://`, the NAS's tailnet name and `:6741` | the vault; the Spark's `values.env`, as `NTFY_URL` |
| `auth-users` | the four users as `name:hash:role`: `spark-gate`, `spark-notify`, `spark-brake` and `phone`, each a `user` | nowhere else |
| `auth-access` | the grants as `user:topic:permission`: each publisher write-only (`wo`) to its own topic, and `phone` read-only (`ro`) to all three | the topic names, in the vault and the Spark's `values.env` |
| `auth-tokens` | the tokens as `user:token`, one for each publisher | the Spark's three header files |

Every user has the role `user`: an `admin` reads and writes every topic. A publisher's password is
never used, since it publishes with its token, so it is random, hashed, and never kept. The
phone's password is yours to choose: keep it where you keep passwords, never in the vault, which
names a credential and never holds it (`CLAUDE.md`). The vault holds the address and the topic
names, which are private but not credentials. None of it goes in this repo or a chat.

The steps below make every value in a working folder on the Spark, `~/ntfy-setup`, with nothing
displayed. §10 deletes it. Each block on the Spark runs in a subshell, `( … )`, so its `cd` and
its `umask 077` end with it: a `umask 077` left in your shell would make the next runbook's
`make apply` deploy new files only you can read, which the services then can't.

**1. A working folder, and ntfy's own binary.** ntfy v2.28.0's release binary, the version the
Compose file pins, checked against its release's checksums, makes the hashes and the tokens.
**On the Spark**, as you:

```bash
( mkdir -m 700 ~/ntfy-setup && cd ~/ntfy-setup && curl -fsSLO https://github.com/binwiederhier/ntfy/releases/download/v2.28.0/ntfy_2.28.0_linux_arm64.tar.gz && curl -fsSLO https://github.com/binwiederhier/ntfy/releases/download/v2.28.0/checksums.txt && grep ' ntfy_2.28.0_linux_arm64.tar.gz$' checksums.txt | sha256sum -c - && tar xzf ntfy_2.28.0_linux_arm64.tar.gz --strip-components=1 ntfy_2.28.0_linux_arm64/ntfy && ./ntfy --version )
```

Expected: `ntfy_2.28.0_linux_arm64.tar.gz: OK`, then `ntfy version 2.28.0`. If `mkdir` says the
folder exists, an earlier run left it: delete it (`rm -r ~/ntfy-setup`) and start again.

**2. The publishers.** Each gets a random password, hashed and then forgotten, a token, and a topic
named after it with a random ending. **On the Spark:**

```bash
( cd ~/ntfy-setup && umask 077 && for u in spark-gate spark-notify spark-brake; do p=$(openssl rand -hex 32) && printf '%s\n%s\n' "$p" "$p" | ./ntfy user hash > "$u.hash" 2>/dev/null && ./ntfy token generate > "$u.token" && printf '%s-%s\n' "$u" "$(openssl rand -hex 6)" > "$u.topic" || { echo "stopped at $u" >&2; break; }; done )
```

**3. The phone's user.** Type the phone's password at `password:`, and again at `confirm:`. Nothing
shows as you type. **On the Spark:**

```bash
( cd ~/ntfy-setup && umask 077 && ./ntfy user hash > phone.hash )
```

**4. The NAS's address.** It asks one question: the NAS's full tailnet name, from *Machines* in
Tailscale's admin console. The port is 6741 (§2). *(Corrected 2026-10-08: it asked for the port and
the folder too, which the Compose file now names.)* **On the Spark:**

```bash
( cd ~/ntfy-setup && umask 077 && read -rp "The NAS's tailnet name: " nas && printf 'http://%s:6741\n' "$nas" > address )
```

**5. The files.** This writes `server.yml`, ntfy's private settings, for §4 to upload; `values.env`,
for the Spark; and the three header files, each one line, `Authorization: Bearer <token>`. Each is
readable by you alone. Run again, it writes them all afresh from the steps above. *(Corrected
2026-10-08: it wrote `ntfy.env`, the seven variables for Portainer, in place of `server.yml`.)*
**On the Spark:**

```bash
( cd ~/ntfy-setup && umask 077 && {
  printf '# ntfy on the Synology: its private settings (website/how-to/ntfy.md). The rest are in compose.yaml.\n'
  printf 'base-url: "%s"\n' "$(cat address)"
  printf 'auth-users:\n'; for u in spark-gate spark-notify spark-brake phone; do printf '  - "%s:%s:user"\n' "$u" "$(cat "$u.hash")"; done
  printf 'auth-access:\n'; for u in spark-gate spark-notify spark-brake; do printf '  - "%s:%s:wo"\n' "$u" "$(cat "$u.topic")"; done; for u in spark-gate spark-notify spark-brake; do printf '  - "phone:%s:ro"\n' "$(cat "$u.topic")"; done
  printf 'auth-tokens:\n'; for u in spark-gate spark-notify spark-brake; do printf '  - "%s:%s"\n' "$u" "$(cat "$u.token")"; done
} > server.yml && printf 'NTFY_URL=%s\nNTFY_TOPIC_GATE=%s\nNTFY_TOPIC_NOTIFY=%s\nNTFY_TOPIC_BRAKE=%s\n' "$(cat address)" "$(cat spark-gate.topic)" "$(cat spark-notify.topic)" "$(cat spark-brake.topic)" > values.env && for p in gate notify brake; do printf 'Authorization: Bearer %s\n' "$(cat spark-$p.token)" > "ntfy-$p.header"; done )
```

**6. Check them, without showing a value.** **On the Spark:**

```bash
( cd ~/ntfy-setup && sed -n 's/^\([a-z-]*\):.*/\1/p' server.yml && grep -c ':\$2a\$10\$' server.yml && grep -c ':tk_' server.yml && sed -n 's/=.*//p' values.env && wc -c ntfy-gate.header ntfy-notify.header ntfy-brake.header )
```

Expected: `base-url`, `auth-users`, `auth-access` and `auth-tokens`; then `4`, one hash for each
user; then `3`, one token for each publisher; then the names `NTFY_URL`, `NTFY_TOPIC_GATE`,
`NTFY_TOPIC_NOTIFY` and `NTFY_TOPIC_BRAKE`; then `55` beside each header file, which is
`Authorization: Bearer `, a 32-character token and the line's end. Anything else: run the step it
points to again, then step 5.

**7. Into the vault.** The address and the topic names go into the vault's entry note,
`brightroar Local AI Stack.md`; §7 types them into the phone from there. **On the Mac**, this puts
them on the clipboard, each with its name, without showing them; paste them into the note:

```bash
ssh brightroar 'cat ntfy-setup/values.env' | pbcopy
```

Then, still **on the Mac**, empty the clipboard:

```bash
pbcopy < /dev/null
```

## 4. Deploy it — Portainer today

Written from Portainer's documentation and its source (2.39.8), since Portainer doesn't run on the
Spark; File Station's steps are untried too. Task 2 runs these steps on the NAS and corrects them
here.

1. **In DSM's File Station**, make the folder `ntfy` inside the shared folder `docker`
   (`/volume1/docker/ntfy`), then a folder `cache` inside it (`/volume1/docker/ntfy/cache`), both
   names in lower case, as in Marius Hosting's guide. If your `docker` shared folder isn't on
   `volume1`, change both lines under `volumes:` in the file you paste in step 3.
2. **Upload `server.yml`** into `/volume1/docker/ntfy`, as his guide uploads his. First, **on the
   Mac**, copy it from the Spark into a folder only you can read:

   ```bash
   mkdir -m 700 ~/ntfy-upload && scp brightroar:ntfy-setup/server.yml ~/ntfy-upload/
   ```

   Then, **in DSM's File Station**, open `docker` → `ntfy` → *Upload* → *Upload - Skip*, and pick
   `server.yml` in `ntfy-upload`, in your home folder (in the file picker, ⌘⇧G, then
   `~/ntfy-upload`). Its name stays exactly `server.yml`. Then, **on the Mac**, delete the copy:

   ```bash
   rm -r ~/ntfy-upload
   ```

   `server.yml` holds the hashes and the tokens, so whoever can read the `docker` shared folder can
   read them: *Control Panel* → *Shared Folder* → `docker` → *Edit* → *Permissions*, in DSM, lists
   who that is.
3. **In Portainer**: *Stacks* → *Add stack*, name it `ntfy`, and choose a build method:
   - *Web editor*: paste `stack/synology/ntfy/compose.yaml`, as it is at the commit you deploy.
   - *Repository*: *Repository URL* `https://github.com/chendaniely/local-ai`; *Repository
     reference* the branch that holds the file, `refs/heads/main` once Phase 2a merges (until then
     `refs/heads/phase-2a`, which goes away after the merge, so switch the stack to `main` then);
     *Compose path* `stack/synology/ntfy/compose.yaml`; *Authentication* off, since the repo is
     public. Leave *GitOps updates* off: the pin moves by hand on upgrade day (§6).

   Either works: `phase-2a` has been on GitHub since 2026-10-08. The stack needs no environment
   variables. *(Corrected 2026-10-08: the seven variables of `ntfy.env` were pasted here, from the
   Mac's clipboard.)*
4. *Deploy the stack*.

Expected: the container runs, and its log (*Containers* → the ntfy container → *Logs*) has a line
`Listening on :80[http], ntfy 2.28.0, …`. In File Station, `user.db` is beside `server.yml`, and
`cache.db` in the `cache` folder. Within about two minutes the container's health reads `healthy`
(`starting` until the first check, a minute after it starts): the healthcheck reached `/v1/health`
inside the container. `unhealthy` means three checks in a row failed; Docker only reports it, and
restarts nothing for it. From the phone, with Tailscale on, the address opens ntfy's web page, and a
topic there answers only after a login.

If the container keeps restarting (`restart: unless-stopped` starts it again each time it stops),
its log says why:

- `config file /etc/ntfy/server.yml does not exist`: the upload missed the folder or the name;
  check step 2.
- `invalid auth-users: …`, or `yaml: line …`: `server.yml` was changed by hand. Write it again with
  §3's step 5, and upload it again with *Upload - Overwrite*. The first of those log lines shows
  the entry it couldn't read, which holds a user's hash.

**Changing `server.yml` later**, for a new token or topic: ntfy reads it only when it starts. Write
it again with §3's step 5, upload it with *Upload - Overwrite*, then restart the container in
Portainer (*Containers* → the ntfy container → *Restart*).

## 5. Deploy it — any other Compose helper

Any helper that reads a Compose file deploys it the same way, since the file needs no variables.
With plain Docker Compose, on the Synology over SSH: make the two folders and upload `server.yml`,
as §4's steps 1 and 2 do; then put `compose.yaml` from this repo, at the commit you deploy, in a
folder of its own. *(Corrected 2026-10-08: a `.env` beside it held the seven variables.)*

Then, in that folder, check the file. This prints nothing when it is in order. **On the
Synology:**

```bash
docker compose config --quiet
```

Then start it, still **on the Synology**:

```bash
docker compose up -d
```

Where your account isn't in the `docker` group, put `sudo` in front of both.

## 6. Moving the pin, and rolling back

The pin moves on upgrade day, to a release at least seven days old, unless an urgent fix needs it
sooner and the commit says so (`CLAUDE.md`, *Building it*; [Updates](updates.md)). ntfy v2.29.0
came out on 2026-10-07, so it qualifies from 2026-10-14.

**On the Spark**, read the new release's index digest, with its tag in `v`:

```bash
v=v2.29.0; t=$(curl -fsS "https://auth.docker.io/token?service=registry.docker.io&scope=repository:binwiederhier/ntfy:pull" | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])') && curl -fsSI -H "Authorization: Bearer $t" -H 'Accept: application/vnd.oci.image.index.v1+json' -H 'Accept: application/vnd.docker.distribution.manifest.list.v2+json' "https://registry-1.docker.io/v2/binwiederhier/ntfy/manifests/$v" | grep -i '^docker-content-digest'
```

The token it fetches is Docker Hub's anonymous one, for a public image: nothing secret. Then, still
on the Spark and in one commit, the new tag and digest go into `compose.yaml`'s `image:`, into
ntfy's `version` and `pin` in `stack/versions.yaml`, and into the image
`spark/tests/test_ntfy_records.py` expects; the Stack page is regenerated
(`uv run --frozen --project spark spark docs stack --write`); and `make test` passes, which checks
that the file and `versions.yaml` agree. Then redeploy, once the commit is where the helper reads
it:

- Portainer, a Git-repository stack: the stack's page → *Pull and redeploy*.
- Portainer, a web-editor stack: the stack's *Editor*, the new file pasted in → *Update the stack*.
- Another helper: the new `compose.yaml` in place of the old, then, **on that host**:
  `docker compose pull && docker compose up -d`.

**Rolling back** is the same redeploy, from the commit before the bump (or, in the web editor, the
file as it was). ntfy's folder stays through both: a redeploy doesn't touch it, and ntfy makes its
users, their access and the tokens again from `server.yml` each time it starts. One exception: a
newer ntfy may migrate its databases when it starts, and an older one then refuses them, its log
saying `unexpected … schema version` (ntfy v2.28.0's `db/schema`). Then, **in DSM's File Station**,
rename `user.db` to `user.db.old` and the `cache` folder to `cache.old`, make an empty `cache`
folder in its place, and redeploy: ntfy starts with new databases, makes the users again from
`server.yml`, and loses only the messages it had cached, 12 hours' worth at most by default.
*(Corrected 2026-10-08: the whole folder was renamed, which would now take `server.yml` with it.)*

## 7. The phone

**On the phone** (Android):

1. **Tailscale stays connected**, or nothing arrives: in Android's VPN settings, make Tailscale the
   *Always-on VPN*.
2. **Install ntfy**, from Google Play or F-Droid.
3. **The NAS as the default server**: in ntfy, *Settings* → *Default server* → the address,
   `NTFY_URL` in the vault.
4. **The phone's user**: *Settings* → *Manage users* → *Add user*: the same address, the user
   `phone`, and its password.
5. **The three topics**: subscribe (+) to each, from the vault, on the default server. With a server
   of your own as the default, the app offers no *Instant delivery* box: it receives a self-hosted
   server's topics at once, through its background service and that service's lasting notification
   (ntfy's phone docs, and its Android app's source: Firebase is only for ntfy.sh). Leave the
   service on, and let Android run ntfy unrestricted in the background, so it doesn't stop the
   connection.
6. **Quiet hours**: Android's *Do Not Disturb* on a schedule, 00:00 to 05:00, every day. Let ntfy's
   *High priority* and *Max priority* channels through it, and nothing else of ntfy's: in *Do Not
   Disturb*'s exceptions for apps, or in each channel's own settings (*Override Do Not Disturb*).
   ntfy has one channel for each priority.

## 8. On the Spark

The gate, the failure notifier and the brake read these from Phase 2a's later tasks: the values
file through systemd's `EnvironmentFile=`, the header files through `LoadCredential=`. Each is
`0600 root:root`; `install`, run as root, makes them root's. *(2026-10-14, Dan's decision: ntfy
optional, moving with the NAS phase; Phases 4 and 5 swapped, the NAS phase now 5. In Phase 5 the
stack is already running without these files, and the gate and the brake read them only when they
start: after the check below, on the Spark, `systemctl restart local-ai-gate.service
local-ai-brake.service`, which the polkit rule allows without sudo. The failure notifier reads them
at each run. `spark status` reads *ntfy ok* once the gate's next notification goes through.)*
**On the Spark:**

```bash
sudo install -m 0600 ~/ntfy-setup/values.env /etc/local-ai/values.env && sudo install -m 0600 -t /etc/local-ai/secrets ~/ntfy-setup/ntfy-gate.header ~/ntfy-setup/ntfy-notify.header ~/ntfy-setup/ntfy-brake.header
```

Check them, without showing a value. **On the Spark:**

```bash
sudo stat -c '%a %U:%G %s %n' /etc/local-ai/values.env /etc/local-ai/secrets/ntfy-gate.header /etc/local-ai/secrets/ntfy-notify.header /etc/local-ai/secrets/ntfy-brake.header && sudo sed -n 's/=.*//p' /etc/local-ai/values.env
```

Expected: `600 root:root` on each, `55` for each header file, then `NTFY_URL`, `NTFY_TOPIC_GATE`,
`NTFY_TOPIC_NOTIFY` and `NTFY_TOPIC_BRAKE`.

## 9. A test publish

As root, and the way the notifier will publish: one message from each header file, at `high` and
then at `low`. curl reads the address and the topic from its standard input (`--config -`), so
neither is on a command line, where `ps` would show it, and the token stays in its file
(`-H @file`). It prints only each publisher, its priority and the answer's status. **On the
Spark:**

```bash
sudo bash -c '. /etc/local-ai/values.env && for p in gate notify brake; do v=NTFY_TOPIC_${p^^}; for prio in high low; do printf "url = \"%s/%s\"\n" "$NTFY_URL" "${!v}" | curl --config - -sS --fail --max-time 5 -o /dev/null -w "$p $prio: %{http_code}\n" -H @/etc/local-ai/secrets/ntfy-$p.header -H "Priority: $prio" -H "Title: local-ai test" -d "A test from the $p publisher, at $prio priority."; done; done'
```

Expected: six lines, each ending `200`, and six notifications on the phone, the `high` ones with a
sound and the `low` ones silent. With *Do Not Disturb* switched on by hand, only `high` breaks
through.

Then check that each publisher reaches its own topic only. Each token tries the two other topics,
and no token at all tries each topic. **On the Spark:**

```bash
sudo bash -c '. /etc/local-ai/values.env && for p in gate notify brake none; do for q in gate notify brake; do [ $p = $q ] && continue; v=NTFY_TOPIC_${q^^}; h=/etc/local-ai/secrets/ntfy-$p.header; [ $p = none ] && h=/dev/null; printf "url = \"%s/%s\"\n" "$NTFY_URL" "${!v}" | curl --config - -sS --max-time 5 -o /dev/null -w "$p on $q: %{http_code}\n" -H @$h -d "This one should be refused."; done; done'
```

Expected: nine lines, each ending `403`. A `200` means a grant is wider than §3 wrote it.

## 10. Record it

**In the vault's entry note**, beside the values §3 put there: the values file,
`/etc/local-ai/values.env`, and its four names; each of the three tokens by reference, never its
value: in `/etc/local-ai/secrets/ntfy-<publisher>.header` on the Spark, and in `server.yml`'s
`auth-tokens` on the NAS (`/volume1/docker/ntfy/server.yml`); where the phone's password is kept,
never the password; and that the publishers' passwords aren't kept anywhere.

Then, with the stack running and §9 passed, delete the working folder. **On the Spark:**

```bash
rm -r ~/ntfy-setup
```

From then on the values live in `server.yml` on the NAS, the vault (the address and the topic
names, and the credentials by name), the Spark's four files and the phone.

**On a rebuilt Spark, with the NAS unchanged**, either run §3 to §9 again, which makes new hashes,
tokens and topics, so the phone subscribes to the new topics and `server.yml` is uploaded again; or
write §8's four files again from the values that remain: the address and the topic names from the
vault, and each token from `server.yml`'s `auth-tokens` on the NAS. That second way puts the tokens
on screen, wherever you open `server.yml`; the first shows nothing.

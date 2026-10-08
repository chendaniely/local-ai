---
title: "ntfy on the Synology"
description: "ntfy for the Spark's alerts, as a Compose file on the Synology with any Compose helper (Portainer today): the tailnet grant, the users and tokens, the phone, and the Spark's values file and header files."
---

From Phase 2a, the Spark's alerts reach your phone through ntfy, which runs on the Synology. This
runbook sets it up once (Phase 2a's Task 2): the NAS on the tailnet and its grant, the users and
tokens, the deploy, the phone, and the files on the Spark that the gate, the failure notifier and
the brake read.

ntfy deploys from one Compose file, `stack/synology/ntfy/compose.yaml`, as every app on the
Synology does, so any Compose helper deploys it: Portainer's stacks today (§4), plain
`docker compose` or another helper (§5). The file is public and holds nothing private. The NAS's
address, the topic names, the users' hashes and the tokens reach it as six variables, which
`stack/synology/ntfy/variables.example` names. Each is required, so a deploy that lacks one stops
and names it.

Each step says where it runs: DSM on the Synology, Portainer, Tailscale's admin console, the phone,
the Spark, or the Mac.

**What has been run.** Every command for the Spark was run on 2026-10-07 (Phase 2a's Task 1), with
stand-in values: §3's against ntfy v2.28.0's own release binary, which then served the values they
made, through `docker compose config`, on 127.0.0.1; §8's and §9's against a temporary folder in
place of `/etc/local-ai`, and that server; §5's check and §6's digest as written. Not yet run: the
Mac's clipboard commands, §5's `docker compose up -d`, and every step in DSM, Portainer, Tailscale's
console and the phone. Portainer's steps are written from its documentation and its source
(2.39.8); Task 2 runs them on the NAS and corrects this page.

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

Pick ntfy's port on the NAS now, one nothing else on the NAS uses (DSM itself has 5000 and 5001);
§3 asks for the same one. It stands in for `<ntfy-port>` below. The Compose file publishes it on
every one of the NAS's interfaces, so it is your router that keeps it off the internet: never
forward it there. Don't count on DSM's firewall to narrow it instead: reports differ on whether
DSM's rules reach a port that Docker publishes for a container.

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
{"src": ["tag:spark"], "dst": ["tag:nas"], "ip": ["<ntfy-port>"]}
```

Nothing else widens. The first grant gives your devices what the member grant gave them before the
tag, and it is how the phone reaches ntfy's port; the second is the only new path, from the Spark to
one port on the NAS. Then show the two grants to the Spark's Claude session in the chat, never in a
file: Phase 2a's Task 2 checks that they let exactly the Spark and your devices reach ntfy's port
and widen nothing else. The policy itself stays in the vault.

## 3. The values

What the six variables hold, and where each value is kept:

| Variable | What it holds | Kept in |
|---|---|---|
| `NTFY_BASE_URL` | ntfy's address as the phone and the Spark reach it: `http://`, the NAS's tailnet name, `:` and the port | the helper; the vault; the Spark's `values.env`, as `NTFY_URL` |
| `NTFY_PORT` | ntfy's port on the NAS, published to the container's 80 | the helper; the vault |
| `NTFY_DATA_DIR` | the folder on the NAS for ntfy's two databases | the helper; the vault |
| `NTFY_AUTH_USERS` | the four users as `name:hash:role`, comma-separated: `spark-gate`, `spark-notify`, `spark-brake` and `phone`, each a `user` | the helper |
| `NTFY_AUTH_ACCESS` | the grants as `user:topic:permission`, comma-separated: each publisher write-only (`wo`) to its own topic, and `phone` read-only (`ro`) to all three | the helper; the topic names also in the vault and the Spark's `values.env` |
| `NTFY_AUTH_TOKENS` | the tokens as `user:token`, comma-separated, one for each publisher | the helper; the Spark's three header files |

Every user has the role `user`: an `admin` reads and writes every topic. A publisher's password is
never used, since it publishes with its token, so it is random, hashed, and never kept. The
phone's password is yours to choose: keep it where you keep passwords, never in the vault, which
names a credential and never holds it (`CLAUDE.md`). The vault holds the address, the port, the
folder and the topic names, which are private but not credentials. None of it goes in this repo or
a chat.

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

**4. The NAS's address, port and folder.** It asks three questions: the NAS's full tailnet name,
from *Machines* in Tailscale's admin console; the port picked in §2; and the folder §4 makes on the
NAS, such as `/volume1/docker/ntfy`. **On the Spark:**

```bash
( cd ~/ntfy-setup && umask 077 && read -rp "The NAS's tailnet name: " nas && read -rp "ntfy's port on the NAS: " port && read -rp "ntfy's folder on the NAS: " dir && printf 'http://%s:%s\n' "$nas" "$port" > address && printf '%s\n' "$port" > port && printf '%s\n' "$dir" > folder )
```

**5. The files.** This writes `ntfy.env`, the six variables for the Compose helper, its three
`NTFY_AUTH_` values single-quoted, since every hash holds `$`; `values.env`, for the Spark; and the
three header files, each one line, `Authorization: Bearer <token>`. Run again, it writes them all
afresh from the steps above. **On the Spark:**

```bash
( cd ~/ntfy-setup && umask 077 && {
  printf 'NTFY_BASE_URL=%s\nNTFY_PORT=%s\nNTFY_DATA_DIR=%s\n' "$(cat address)" "$(cat port)" "$(cat folder)"
  printf "NTFY_AUTH_USERS='spark-gate:%s:user,spark-notify:%s:user,spark-brake:%s:user,phone:%s:user'\n" "$(cat spark-gate.hash)" "$(cat spark-notify.hash)" "$(cat spark-brake.hash)" "$(cat phone.hash)"
  printf "NTFY_AUTH_ACCESS='spark-gate:%s:wo,spark-notify:%s:wo,spark-brake:%s:wo,phone:%s:ro,phone:%s:ro,phone:%s:ro'\n" "$(cat spark-gate.topic)" "$(cat spark-notify.topic)" "$(cat spark-brake.topic)" "$(cat spark-gate.topic)" "$(cat spark-notify.topic)" "$(cat spark-brake.topic)"
  printf "NTFY_AUTH_TOKENS='spark-gate:%s,spark-notify:%s,spark-brake:%s'\n" "$(cat spark-gate.token)" "$(cat spark-notify.token)" "$(cat spark-brake.token)"
} > ntfy.env && printf 'NTFY_URL=%s\nNTFY_TOPIC_GATE=%s\nNTFY_TOPIC_NOTIFY=%s\nNTFY_TOPIC_BRAKE=%s\n' "$(cat address)" "$(cat spark-gate.topic)" "$(cat spark-notify.topic)" "$(cat spark-brake.topic)" > values.env && for p in gate notify brake; do printf 'Authorization: Bearer %s\n' "$(cat spark-$p.token)" > "ntfy-$p.header"; done )
```

**6. Check them, without showing a value.** **On the Spark:**

```bash
( cd ~/ntfy-setup && sed -n 's/=.*//p' ntfy.env values.env && grep -o ':\$2a\$10\$' ntfy.env | wc -l && wc -c ntfy-gate.header ntfy-notify.header ntfy-brake.header )
```

Expected: the names `NTFY_BASE_URL`, `NTFY_PORT`, `NTFY_DATA_DIR`, `NTFY_AUTH_USERS`,
`NTFY_AUTH_ACCESS`, `NTFY_AUTH_TOKENS`, `NTFY_URL`, `NTFY_TOPIC_GATE`, `NTFY_TOPIC_NOTIFY` and
`NTFY_TOPIC_BRAKE`; then `4`, one hash for each user; then `55` beside each header file, which is
`Authorization: Bearer `, a 32-character token and the line's end. Anything else: run the step it
points to again, then step 5.

**7. Into the vault.** The address, the port, the folder and the topic names go into the vault's
entry note, `brightroar Local AI Stack.md`; §7 types the topics into the phone from there. **On the
Mac**, this puts them on the clipboard, each with its name, without showing them; paste them into
the note:

```bash
ssh brightroar 'cd ntfy-setup && grep -E "^NTFY_(BASE_URL|PORT|DATA_DIR)=" ntfy.env && grep TOPIC values.env' | pbcopy
```

Then, still **on the Mac**, empty the clipboard:

```bash
pbcopy < /dev/null
```

## 4. Deploy it — Portainer today

Written from Portainer's documentation and its source (2.39.8), since Portainer doesn't run on the
Spark. Task 2 runs these steps on the NAS and corrects them here.

1. **In DSM's File Station**, make the data folder, the one `NTFY_DATA_DIR` names, such as `ntfy`
   inside the shared folder `docker` (`/volume1/docker/ntfy`).
2. **In Portainer**: *Stacks* → *Add stack*, name it `ntfy`, and choose a build method:
   - *Web editor*: paste `stack/synology/ntfy/compose.yaml`, as it is at the commit you deploy.
     Phase 2a's Task 2 uses this one, since the file reaches GitHub only with the push after
     Task 35.
   - *Repository*: *Repository URL* `https://github.com/chendaniely/local-ai`; *Repository
     reference* the branch that holds the file, `refs/heads/main` once Phase 2a merges (until then
     `refs/heads/phase-2a`, which goes away after the merge, so switch the stack to `main` then);
     *Compose path* `stack/synology/ntfy/compose.yaml`; *Authentication* off, since the repo is
     public. Leave *GitOps updates* off: the pin moves by hand on upgrade day (§6).
3. Under *Environment variables*, choose *Advanced mode* and paste the six lines of `ntfy.env`, as
   they are, single quotes included. They hold the tokens, and macOS's Universal Clipboard copies
   the clipboard to your other Apple devices: switch Handoff off (*System Settings* → *General* →
   *AirDrop & Handoff*) and pause any clipboard manager until the clipboard is emptied again.
   **On the Mac**, this puts them on the clipboard without showing them:

   ```bash
   ssh brightroar 'cat ntfy-setup/ntfy.env' | pbcopy
   ```

   Portainer writes each variable into a `stack.env` file as `NAME=value` and hands it to Compose,
   which reads it as it reads a `.env`, so the quotes keep each hash's `$` from being taken for a
   variable (read from Portainer's source: 2.27.3, 2.39.8 and 2.45.2 write the file the same way,
   and 2.39.8's advanced mode keeps the quotes as part of the value). Then empty the clipboard,
   still **on the Mac**: `pbcopy < /dev/null`.
4. *Deploy the stack*.

Expected: the container runs, and its log (*Containers* → the ntfy container → *Logs*) has a line
`Listening on :80[http], ntfy 2.28.0, …`. From the phone, with Tailscale on, the address opens
ntfy's web page, and a topic there answers only after a login. If the deploy stops with
`required variable … is missing a value`, that variable is missing from the stack's environment. If
the container keeps restarting (`restart: unless-stopped` starts it again each time it stops) and
its log says `invalid auth-users`, a hash has lost its `$` signs: check its quotes.

## 5. Deploy it — any other Compose helper

Any helper that reads a Compose file and its variables deploys it the same way. With plain Docker
Compose, on the machine that runs ntfy (the Synology over SSH, or another host): make the data
folder; put `compose.yaml` from this repo, at the commit you deploy, in a folder of its own; and put
`~/ntfy-setup/ntfy.env` beside it as `.env`, copied without opening it (with `scp`, say) and
readable by you alone (`chmod 600 .env`). Compose reads a `.env` beside the file by itself.

Then, in that folder, check the file and its six variables. This prints nothing when they're in
order, and names any variable that is missing. **On the host that runs ntfy:**

```bash
docker compose config --quiet
```

Then start it, still **on that host**:

```bash
docker compose up -d
```

Where your account isn't in the `docker` group, put `sudo` in front of both. Never run
`docker compose config` there without `--quiet`: it prints the file with every value filled in,
hashes and tokens among them.

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
file as it was). The data folder stays through both: a redeploy doesn't touch it, and ntfy makes
its users, their access and the tokens again from the variables each time it starts. One exception:
a newer ntfy may migrate its databases when it starts, and an older one then refuses them, its log
saying `unexpected … schema version` (ntfy v2.28.0's `db/schema`). Then, **in DSM's File Station**,
rename the data folder, make an empty one in its place, and redeploy: ntfy starts with new
databases and loses only the messages it had cached, 12 hours' worth at most by default.

## 7. The phone

**On the phone** (Android):

1. **Tailscale stays connected**, or nothing arrives: in Android's VPN settings, make Tailscale the
   *Always-on VPN*.
2. **Install ntfy**, from Google Play or F-Droid.
3. **The NAS as the default server**: in ntfy, *Settings* → *Default server* → the address,
   `NTFY_BASE_URL`'s value, from the vault.
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
`0600 root:root`; `install`, run as root, makes them root's. **On the Spark:**

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
value: in `/etc/local-ai/secrets/ntfy-<publisher>.header` on the Spark, and in the helper's
`NTFY_AUTH_TOKENS`; where the phone's password is kept, never the password; and that the
publishers' passwords aren't kept anywhere.

Then, with the stack running and §9 passed, delete the working folder. **On the Spark:**

```bash
rm -r ~/ntfy-setup
```

From then on the values live in the helper's variables, the vault (the private values, and the
credentials by name), the Spark's four files and the phone.

**On a rebuilt Spark, with the NAS unchanged**, either run §3 to §9 again, which makes new hashes,
tokens and topics, so the phone subscribes to the new topics; or write §8's four files again from
the values that remain: the address and the topic names from the vault, and each token from the
helper's `NTFY_AUTH_TOKENS`. That second way puts the tokens on screen, wherever the helper shows
its variables; the first shows nothing.

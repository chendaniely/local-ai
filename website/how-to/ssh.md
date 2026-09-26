---
title: "SSH from the Mac"
description: "A key for you and a key for agent, a block in the Mac's ~/.ssh/config for each way in, where NVIDIA Sync fits, then keys-only SSH and a one-time public IPv6 check."
---

Every command block here starts with a comment saying where it runs. **On the Mac** is a terminal
on the Mac. **On the Spark** is a shell on the Spark as you: from the Mac, `ssh brightroar` first,
or `ssh brightroar-lan` before Tailscale is joined. Most of this page runs on the Mac; installing
agent's key, *Keys only* and the IPv6 check each have steps on the Spark.

Real addresses and the tailnet's name belong in the Mac's `~/.ssh/config` and in the vault, never
in this repo; below they are placeholders.

## Two keys, one per account

| Key (on the Mac) | Logs in as | Why separate |
|---|---|---|
| `~/.ssh/id_ed25519-brightroar` | you (`chendaniely`) | your admin account |
| `~/.ssh/id_ed25519-brightroar_agent` | `agent` | so agent's key can never log in as you |

Give each a passphrase; `UseKeychain` below keeps it in the macOS Keychain.

**Your key:**

```bash
# on the Mac
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519-brightroar -C "chendaniely@brightroar"
ssh-copy-id -i ~/.ssh/id_ed25519-brightroar.pub brightroar
```

`ssh-copy-id` asks for your password once, then installs the key.

**agent's key:** agent has no password, so its key goes in through your account with sudo. First,
**on the Mac**, make the key and copy its public half to the Spark:

```bash
# on the Mac
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519-brightroar_agent -C "agent@brightroar"
scp ~/.ssh/id_ed25519-brightroar_agent.pub brightroar:agent-key.pub
```

Then, **on the Spark**, install it with the command in [Bootstrap](bootstrap.md) ("Agent's SSH and
Claude Code login", step 2), from an interactive session: its `sudo` has to ask for your password.

## `~/.ssh/config`

These blocks go in `~/.ssh/config` on the Mac, one per way in. The tailnet name is the primary
one, because it works at home and away. The LAN address is the fallback for when Tailscale is down,
or not joined yet on a new box. macOS doesn't always resolve the short MagicDNS name, so use the
full one (**Machines** in the Tailscale admin console shows it):

```
# Over the tailnet: works at home and away
Host brightroar
  HostName brightroar.<tailnet>.ts.net
  User chendaniely
  IdentityFile ~/.ssh/id_ed25519-brightroar
  IdentitiesOnly yes
  AddKeysToAgent yes
  UseKeychain yes

Host brightroar-agent
  HostName brightroar.<tailnet>.ts.net
  User agent
  IdentityFile ~/.ssh/id_ed25519-brightroar_agent
  IdentitiesOnly yes
  AddKeysToAgent yes
  UseKeychain yes

# Over the home LAN directly, if Tailscale is down or not joined yet
Host brightroar-lan
  HostName <wired-address>
  User chendaniely
  IdentityFile ~/.ssh/id_ed25519-brightroar
  IdentitiesOnly yes

Host brightroar-agent-lan
  HostName <wired-address>
  User agent
  IdentityFile ~/.ssh/id_ed25519-brightroar_agent
  IdentitiesOnly yes
```

`IdentitiesOnly yes` offers only the named key, so a missing key fails clearly instead of falling
back to a password prompt that can't work for agent.

## NVIDIA Sync

NVIDIA Sync, the app on the Mac, keeps its own SSH setup: a key (`nvsync.key`) already installed
for your account, and its own config file at
`~/Library/Application Support/NVIDIA/Sync/config/ssh_config`, with aliases including `brightroar`.
Everything in this section happens on the Mac. Keep the two apart:

- **Leave Sync's `Include` line commented out** at the top of `~/.ssh/config`. Its file also defines
  `Host brightroar`, and ssh takes each setting from the first block that matches, so the two
  would mix. Sync reads its own file directly and doesn't need the `Include`.
- **Don't edit Sync's file by hand.** Sync writes it and may overwrite your changes.
- **Give Sync one device: the wired address.** At setup, Sync had created three entries: the
  wired NIC's old pool address (stale once the reservation took), the Wi-Fi address, and
  `brightroar.local`. The last one is mDNS: it works only at home and resolves to whichever address
  the Spark announces, which was the slow Wi-Fi one. In NVIDIA Sync, remove every device except
  one at `<wired-address>`. Sync then rewrites its own file with just that entry (done
  2026-09-24).

## Test

```bash
# on the Mac
ssh brightroar whoami             # chendaniely — no password (the key's passphrase once per Keychain session)
ssh brightroar-agent whoami       # agent
ssh brightroar-lan whoami         # chendaniely, over the LAN
ssh brightroar-agent-lan whoami   # agent, over the LAN
```

Before Tailscale is joined, only the two `-lan` aliases work.

## Keys only

Do this after Tailscale is joined ([Join Tailscale](tailscale.md)), once your own key works:
**on the Mac**, `ssh brightroar whoami` answers without asking for your password. Afterwards sshd
accepts keys only, and `agent`'s sessions never get a forwarded SSH agent, whatever a client asks
for. NVIDIA Sync logs in with its own key, so it keeps working.

You need two terminals on the Mac. The first holds a session on the Spark: steps 2 and 3 run there,
and so does the undo if a test fails. The second runs step 4's tests on the Mac itself.

1. **On the Mac, open a session on the Spark and keep it open** until step 4 passes. If a login
   fails, this session is how you undo the change. In a terminal of its own:

   ```bash
   # on the Mac
   ssh brightroar
   ```

2. **On the Spark, write the drop-in**, in that session. sshd keeps the first value it reads for
   each setting, and it reads `/etc/ssh/sshd_config.d/` in name order, so `10-local-ai.conf` wins
   over a `50-cloud-init.conf` that turns passwords back on. Each quoted string below is one line
   of the file. It writes the same file every time, so it is safe to run again:

   ```bash
   # on the Spark
   printf '%s\n' \
     '# Keys only (website/how-to/ssh.md). Sorts before 50-cloud-init.conf: sshd keeps the first value.' \
     'PasswordAuthentication no' \
     'KbdInteractiveAuthentication no' \
     '' \
     '# agent never gets a forwarded SSH agent.' \
     'Match User agent' \
     '  AllowAgentForwarding no' \
     | sudo tee /etc/ssh/sshd_config.d/10-local-ai.conf >/dev/null
   ```

3. **On the Spark, check it, then reload.**

   ```bash
   # on the Spark
   sudo sshd -t && echo "syntax ok"
   sudo sshd -T -C user=chendaniely,host=localhost,addr=127.0.0.1 | grep -Ei '^(passwordauthentication|kbdinteractiveauthentication|allowagentforwarding) '
   sudo sshd -T -C user=agent,host=localhost,addr=127.0.0.1 | grep -Ei '^allowagentforwarding '
   ```

   Expected: `syntax ok`; for you, `passwordauthentication no`, `kbdinteractiveauthentication no`
   and `allowagentforwarding yes`; for `agent`, `allowagentforwarding no`. If `sshd -t` prints an
   error instead, it names the file and line: fix the file, or remove it
   (`sudo rm /etc/ssh/sshd_config.d/10-local-ai.conf`), and don't reload. Once everything reads as
   expected:

   ```bash
   # on the Spark
   sudo systemctl reload ssh
   ```

   If it answers that `ssh.service` isn't active, sshd isn't running between logins (Ubuntu can
   start it on demand from `ssh.socket`), and the next login reads the new file anyway.
4. **On the Mac, test from a second terminal**, with the first session still open. Each login must
   work with your key alone (its passphrase at most), and a password must be refused:

   ```bash
   # on the Mac
   ssh brightroar whoami         # chendaniely
   ssh brightroar-lan whoami     # chendaniely, over the LAN
   ssh brightroar-agent whoami   # agent
   ssh -o PubkeyAuthentication=no -o PreferredAuthentications=password,keyboard-interactive brightroar true
   ```

   The last line must end in `Permission denied (publickey).` If any login fails, undo the change
   **on the Spark**, in the session you kept open, then find out why before you try again:

   ```bash
   # on the Spark
   sudo rm /etc/ssh/sshd_config.d/10-local-ai.conf && sudo systemctl reload ssh
   ```

5. Only now close the first session. Record the change: a dated `changelog.md` entry and
   `README.md` §Current state, command lines only.

## Is the Spark reachable over public IPv6?

Check this once. A public IPv6 address can be reachable from the whole internet, and ufw's OpenSSH
rule allows IPv6 too. **On the Spark**, count its public IPv6 addresses (it prints a number,
nothing else):

```bash
# on the Spark
ip -6 -o addr show scope global | grep -c ' inet6 [23]'
```

`0` means there are none, and you're done. Otherwise, `ip -6 addr show scope global`, also on the
Spark, shows the address. Read it privately: it identifies your home connection, so it goes in the
vault, never here. Then, **on the Mac**, from a network outside your home, such as a phone's
hotspot, and with Tailscale off:

```bash
# on the Mac
ping6 -c 1 2606:4700:4700::1111 >/dev/null && echo "this network has IPv6"   # without it, the next line proves nothing
nc -6 -z -G 5 -w 5 <public-ipv6-address> 22; echo "exit=$?"
```

`exit=1` means nothing answered, which is what you want. `exit=0` means the internet reaches sshd:
turn on the router's IPv6 firewall so it blocks incoming connections, and check again. Record the
result in the vault.

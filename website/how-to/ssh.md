---
title: "SSH from the Mac"
description: "A key for you and a key for agent, one ~/.ssh/config block for the Spark, and where NVIDIA Sync fits."
---

Everything here runs on the Mac unless it says otherwise. Real addresses and the tailnet's name
belong in the Mac's `~/.ssh/config` and in the vault, never in this repo; below they are
placeholders.

## Two keys, one per account

| Key (on the Mac) | Logs in as | Why separate |
|---|---|---|
| `~/.ssh/id_ed25519-brightroar` | you (`chendaniely`) | your admin account |
| `~/.ssh/id_ed25519-brightroar_agent` | `agent` | so agent's key can never log in as you |

Give each a passphrase; `UseKeychain` below keeps it in the macOS Keychain.

**Your key:**

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519-brightroar -C "chendaniely@brightroar"
ssh-copy-id -i ~/.ssh/id_ed25519-brightroar.pub brightroar
```

`ssh-copy-id` asks for your password once, then installs the key.

**agent's key:** agent has no password, so its key goes in through your account with sudo:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519-brightroar_agent -C "agent@brightroar"
scp ~/.ssh/id_ed25519-brightroar_agent.pub brightroar:agent-key.pub
```

Then install it on the Spark with the command in [Bootstrap](bootstrap.md) ("Agent's SSH and Claude
Code login", step 2).

## `~/.ssh/config`

One block per way in. The tailnet name is the primary one, because it works at home and away. The
LAN address is the fallback for when Tailscale is down. macOS doesn't always resolve the short
MagicDNS name, so use the full one (**Machines** in the Tailscale admin console shows it):

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

# Over the home LAN directly, if Tailscale is down
Host brightroar-lan
  HostName <wired-address>
  User chendaniely
  IdentityFile ~/.ssh/id_ed25519-brightroar
  IdentitiesOnly yes
```

`IdentitiesOnly yes` offers only the named key, so a missing key fails clearly instead of falling
back to a password prompt that can't work for agent.

## NVIDIA Sync

NVIDIA Sync keeps its own SSH setup: a key (`nvsync.key`) already installed for your account, and
its own config file at `~/Library/Application Support/NVIDIA/Sync/config/ssh_config`, with aliases
including `brightroar`. Keep the two apart:

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
ssh brightroar whoami         # chendaniely — no password (the key's passphrase once per Keychain session)
ssh brightroar-agent whoami   # agent
ssh brightroar-lan whoami     # chendaniely, over the LAN
```

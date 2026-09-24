---
title: "Join Tailscale"
description: "Install, MagicDNS and HTTPS, the ACL grants that act as the firewall, and the rebuild trap."
---

## Install and join

On the Spark:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

Then, in the admin console: **Machines** → the Spark → **Disable key expiry**. A headless server
whose key expires silently drops off the tailnet.

## Turn on MagicDNS and HTTPS

In the admin console, turn on **MagicDNS** and **HTTPS certificates**. Once issued, the
certificate's name appears in public certificate-transparency logs — the name only, nothing else.

## ACL grants are the firewall

`ufw` can't see `tailscale0`, so the ACL policy is the only firewall tailnet traffic gets. Before
changing anything, check what your current policy already allows, so nothing that works today
breaks. Draft the change privately; keep the real policy in the vault, not this repo. Pattern to
adapt:

```json
{
  "grants": [
    {"src": ["autogroup:member"], "dst": ["<the-spark>"], "ip": ["22", "443"]}
  ]
}
```

Port 22 is for SSH, 443 for `tailscale serve` (Phase 1). LiteLLM's port is added in Phase 3.

## Confirm the route home

In the admin console, confirm how the tailnet reaches the home LAN — the subnet router and its
advertised route — and record it in the vault. Never paste `tailscale status` output anywhere
public.

## Rebuilding the box

Delete the old device in the admin console **before** rejoining. Otherwise the box comes back as
`brightroar-1`, and every client that points at `brightroar` breaks.

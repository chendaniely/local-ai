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

`ufw` can't see `tailscale0`, so the ACL policy is the only firewall tailnet traffic gets. The
policy lives in the admin console (<https://login.tailscale.com/admin/acls>). Keep the real policy
in the vault, never in this repo: it names your home subnet.

**Start from what's there.** A new tailnet has one allow-all grant (`"src": ["*"], "dst": ["*"],
"ip": ["*"]`). While it exists, every device reaches every port on the Spark, and nothing below
restricts anything. Replace it with the grants below, which keep today's access working.

1. **Create the tag.** In the JSON editor, add a top-level `tagOwners` block beside `grants` (or,
   in the visual editor: **Tags** → **Create tag** → `spark`, owner `autogroup:admin`):

   ```json
   "tagOwners": {
     "tag:spark": ["autogroup:admin"]
   },
   ```

2. **Set three grants**, turning the allow-all grant into the first one and adding the other two:

   ```json
   "grants": [
     // Your own devices reach each other, as before.
     {"src": ["autogroup:member"], "dst": ["autogroup:member"], "ip": ["*"]},
     // The home LAN through the subnet router, as before.
     {"src": ["autogroup:member"], "dst": ["<home-subnet>/24"], "ip": ["*"]},
     // The Spark: SSH, and 443 for `tailscale serve` (Phase 1).
     {"src": ["autogroup:member"], "dst": ["tag:spark"], "ip": ["22", "443"]}
   ]
   ```

   If you use an exit node, add `{"src": ["autogroup:member"], "dst": ["autogroup:internet"], "ip":
   ["*"]}`. `autogroup:member` doesn't include people you've shared devices with; give them their
   own grant if they need one. LiteLLM's port joins the Spark's grant in Phase 3.
3. **Save.** The editor refuses a policy with a syntax error.
4. **Tag the Spark**, on the Spark:

   ```bash
   sudo tailscale up --advertise-tags=tag:spark
   ```

   Repeat any other flags you gave `tailscale up` when joining, because `up` resets the ones you
   leave out. A tagged device is owned by the tag, not by you, and its key doesn't expire, which
   also covers the key-expiry step above.
5. **Test** from the Mac and the phone: SSH to the Spark still works, and so do your usual home-LAN
   services, such as the NAS, from away. If something breaks, add a grant for it.

## Confirm the route home

In the admin console, confirm how the tailnet reaches the home LAN — the subnet router and its
advertised route — and record it in the vault. Never paste `tailscale status` output anywhere
public.

**Today there is none** (2026-09-24), by choice. Every device Dan uses runs Tailscale, so they reach
each other without one. The only gap is devices that can't run Tailscale, such as the home router's
admin page, which is reachable only from home. The Spark doesn't need a route either way: it sits on
the LAN.

**When a device like that is needed from away**, add a subnet route for that device's address only,
not the whole LAN, so nothing else on the home network becomes reachable through the tailnet:

1. On one always-on home Linux machine that runs Tailscale (not the Spark, which stays
   single-purpose), allow forwarding and advertise the one address:

   ```bash
   echo 'net.ipv4.ip_forward = 1' | sudo tee /etc/sysctl.d/99-tailscale.conf
   sudo sysctl -p /etc/sysctl.d/99-tailscale.conf
   sudo tailscale set --advertise-routes=<device-address>/32
   ```

2. In the admin console: **Machines** → that machine → **Edit route settings** → approve the route.
3. In the policy, point the home-LAN grant at the same address, and at its ports if you want to be
   tighter:
   `{"src": ["autogroup:member"], "dst": ["<device-address>/32"], "ip": ["*"]}`.
4. Test from the phone, off Wi-Fi. On the Mac, the Tailscale app's **Use Tailscale subnets** must be
   on.
5. Record the machine and the route in the vault.

## Rebuilding the box

Delete the old device in the admin console **before** rejoining. Otherwise the box comes back as
`brightroar-1`, and every client that points at `brightroar` breaks.

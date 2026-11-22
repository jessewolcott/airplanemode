# 0010. Home VPN, connected devices and data use

Date: 2026-11-21 · Status: accepted

## Context

Three things travellers ask a travel router for, beyond getting online:

1. **A VPN to home**, so a hotel network (or someone on it) only sees encrypted traffic.
2. **Seeing who's connected**, and cutting off one device (a tablet streaming all night, an
   unknown device) without changing the hotspot password for everyone.
3. **Data use**, because plane, cruise and some hotel Wi-Fi is capped or charged per MB.

All three are router settings, so they belong in the platform's admin service, not in an app.

## Decision

**Home VPN: WireGuard, configured by pasting a standard `.conf`.**

- People get a config from wherever their tunnel ends (home router, wg-easy, PiVPN, a VPN
  service). The admin service parses and validates it (keys, addresses, endpoint, one peer)
  and writes it into the router's own config, under the names it owns: interface `amvpn`
  (`proto wireguard`, metric 5, below Ethernet's 10 and Wi-Fi's 20), peer
  `airplanemode_vpn_peer` (`route_allowed_ips 1`), firewall zone `airplanemode_vpn` (named
  `vpn`, masquerading) and forwarding `airplanemode_vpn_fwd` (lan → vpn).
- OpenWrt's WireGuard handler adds a host route for the endpoint through the existing
  uplink, so the tunnel's own packets don't loop into it.
- The private key is stored only in the router's config; the API never returns it.
- Tunnel state comes from `luci.wireguard getWgInstances` (latest handshake, bytes). A
  handshake in the last 3 minutes counts as connected.
- `scripts/router-setup.sh` installs `wireguard-tools` and `luci-proto-wireguard` if missing
  (opkg or apk), while the box is online.

**Connected devices: DHCP leases, and "pause" as firewall rules.**

- The list comes from `luci-rpc getDHCPLeases` (present wherever LuCI is).
- Pausing a device adds `airplanemode_block_<mac>`, a `REJECT` rule from lan to wan for its
  MAC (all protocols), plus `..._vpn` for the VPN zone when it exists. Forwarding is blocked,
  input isn't, so a paused device still reaches the box's own apps and admin panel. Rules
  that point at the VPN zone are removed with the VPN, because fw4 refuses rules for a zone
  that doesn't exist.

**Data use: uplink byte counters, per network.**

- Each supervisor tick reads `network.device status` for the uplinks that are up and adds
  the difference to that network's total (the SSID, or "Ethernet"). It covers every device,
  because all traffic leaves through the uplink. A counter that goes backwards (interface
  recreated) counts from zero.
- Totals live with the saved networks in the `admin-data` volume, written at most every 5
  minutes (and on shutdown) to spare the flash.

The restricted login's ACL grows by three **read** calls: `network.device status`,
`luci-rpc getDHCPLeases`, `luci.wireguard getWgInstances`. It needs no new write access:
these are `network` and `firewall` settings it could already change (decision 0008).

## Alternatives considered

- **OpenVPN / Tailscale.** OpenVPN is slower and heavier on the router. Tailscale needs
  its daemon and an account, and doesn't fit "paste one config". WireGuard is in the kernel
  and in every home-router VPN feature that matters.
- **Generating the box's keys on the router** (`luci.wireguard generateKeyPair`). Nicer for
  people who run their own server, but it adds a two-step setup. Kept out for now; pasting
  a config covers both home servers and VPN services.
- **Kicking devices off Wi-Fi** (`hostapd del_client`). They reconnect straight away unless
  banned, and it needs hostapd access per interface. Pausing internet access is what people
  actually want, and it works the same on every hotspot.
- **Per-device data use.** Needs nftables counters per MAC, which the restricted login
  can't read through ubus. Per-network totals answer the "how much of the cap is left" question.

## Consequences

- **A VPN can't get past a hotel sign-in page.** With the VPN on, everything goes into the
  tunnel, which can't come up until someone has signed in. The landing page and admin panel
  say so ("turn the VPN off, sign in, turn it back on"). Doing this automatically (bypassing
  the tunnel only for the sign-in page) is a possible follow-up.
- While the VPN is on but not connected, nothing reaches the internet: no leak, but no
  traffic either. That's the intended trade-off.
- DNS: a `DNS =` line is added to the tunnel interface, but dnsmasq may still ask the
  hotel's resolver for some names. Untested on hardware.
- Docker's own traffic on the box (model downloads, image pulls) follows the router's
  routes too; whether FriendlyWrt's Docker zone may forward into `vpn` is untested.
- Data use only counts while the admin service runs, and is approximate around restarts.
- Verified on OpenWrt 24.10 in Docker (config written, ACL, nftables syntax). **Not yet on
  hardware:** a real tunnel, handshake reporting, DHCP leases and counters on real
  interfaces. See the checklist in [docs/testing.md](../testing.md).

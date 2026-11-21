# 0009. Staying connected: auto-join, Ethernet first, rebind off, offline popup

Date: 2026-11-21 · Status: accepted

## Context

The first version of the admin panel needed an admin to pick a network in every new place,
ignored Ethernet, and left four gaps:

1. Nothing happened when the one remembered network wasn't around.
2. A plugged-in Ethernet cable wasn't recognised, and there was no priority between it and Wi-Fi.
3. OpenWrt's DNS **rebind protection** blocks DNS answers that point at private addresses,
   which is exactly where most hotel sign-in pages live, so they'd fail to load.
4. People joining the hotspot with no internet around had no way to find the box's page
   without knowing its address.

Also found while fixing 3: on OpenWrt, Docker containers can't use the host's resolver at
`127.0.0.1`, so they fall back to public DNS. Hotels block that before sign-in, so the
captive-portal check would wrongly say *offline*.

## Decision

A **supervisor** loop in the admin service (every `SUPERVISE_INTERVAL`, 30 s) plus one-time
router setup in `scripts/router-setup.sh`:

- **Saved networks.** Every successful manual *Connect* is remembered (in the `admin-data`
  volume, mode 600). With **no uplink at all**, the supervisor scans the upstream radio and
  joins the saved network with the strongest signal. After a join it waits 45 s; a network
  that fails twice is skipped for 10 minutes. A manual *Disconnect* pauses auto-join until
  the next manual *Connect* (or *Resume*). Unsaved networks are never joined automatically.
- **Ethernet first.** `router-setup.sh` sets the wired WAN's route metric to 10, and the
  admin service sets `wwan` to 20, so a cable wins whenever both are up. Status reports
  `via: ethernet | wifi`.
- **Rebind protection off** (`dhcp.@dnsmasq[0].rebind_protection=0`), with
  `KEEP_REBIND_PROTECTION=1` to opt out. **The admin container uses the router's dnsmasq**
  (`dns: LAN_IP`).
- **Offline popup.** When there's been no uplink for 2 ticks, and no join is in progress,
  the supervisor sets a wildcard DNS answer (`address=/#/10.255.255.1`) and enables a
  firewall DNAT `10.255.255.1:80 → LAN_IP:8080`. Phones' connectivity checks then land on
  Caddy, which redirects any non-box hostname to the landing page, and the phone shows it as
  a sign-in screen. Specific names (`airplane.mode`) still resolve normally. The **moment
  any uplink is up** it's switched off, so it can never hide a real hotel sign-in page.

## Verification

- Supervisor logic: automated tests with a mock router and a fake clock (strongest first,
  fall back after failures, Ethernet wins, pause/resume, forget, popup waits during joins).
- Router side, on real OpenWrt 24.10 in Docker, as the restricted `airplanemode` login:
  - `wwan` metric 20;
  - popup on/off is idempotent and keeps other DNS entries;
  - `router-setup.sh` sets rebind off, the `airplane.mode` entry and WAN metric 10, and
    re-running is idempotent;
  - the generated nftables ruleset (`ip daddr 10.255.255.1 tcp dport 80 dnat 192.168.2.1:8080`
    in `dstnat_lan`) passes `nft -c`.
- Not yet on hardware: real phones' popups, scanning, link timing. See the checklist in
  [docs/testing.md](../testing.md).

## Consequences

- **Rebind protection off** weakens a defence against malicious websites reaching LAN
  devices by name. It's the usual travel-router trade-off, and it's documented and reversible.
- `dhcp` joins the restricted login's access list (for the popup's DNS entry). Decision
  0008's per-config limits still apply.
- A box with one Wi-Fi adapter briefly disrupts its own hotspot when it scans. That only
  happens while it has no uplink.
- The popup only catches plain-HTTP checks (port 80). That's what iOS, Android, Windows and
  macOS use for captive-portal detection.

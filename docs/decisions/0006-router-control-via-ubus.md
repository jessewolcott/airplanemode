# 0006. Control the router through its own API (ubus), not a privileged container

Date: 2026-11-21 · Status: accepted

## Context

The admin panel has to change real router settings: the upstream Wi-Fi network, hotspot
names and passwords. The obvious shortcut is a privileged container with host networking and
`/etc/config` mounted. That hands any bug in the admin service the whole router.

## Decision

- The admin service calls **ubus over HTTP** (`/ubus` on the router's uhttpd), the same
  JSON-RPC API that LuCI uses. It logs in with the router's credentials (`ROUTER_USER` /
  `ROUTER_PASSWORD` in `.env`) and runs as an ordinary, unprivileged container.
- Settings change through `uci set/add/delete`, then `uci apply` (ubus sessions can't
  `uci commit`; this was found by testing against real OpenWrt). OpenWrt reloads the
  affected services itself.
- Everything the admin service owns is named: the upstream client interface is the
  `wireless.airplanemode_up` section on the `wwan` network, in the firewall's `wan` zone.
  Nothing else is renamed or rearranged.
- Guard rails live in the service: input validation (SSID and key rules, MAC format), and
  refusing to disable or delete the last enabled hotspot.
- A `mock` backend with the same interface lets the UI and API be developed and tested
  without hardware. `admin/tests/test_openwrt_live.py` checks the real backend against an
  OpenWrt container.

## Alternatives considered

- **Privileged container / host mounts:** simpler code, much bigger blast radius. Rejected.
- **LuCI only, no admin panel:** works, but LuCI is dense on a phone and doesn't know about
  captive portals or the app menu.
- **`uci apply` with rollback** (LuCI's safety net: revert unless confirmed within N seconds):
  good for wired admins, but someone who just changed the hotspot password can't reconnect
  and confirm in time. The lockout guard covers the worst case instead. Revisit if needed.

## Consequences

- The router's root password sits in `.env` on the box (mode 600, git-ignored). A dedicated
  rpcd user with an ACL limited to `uci` (wireless, network, firewall) and `iwinfo` would
  be better. That's a follow-up.
- GL.iNet's own repeater manager also edits these settings (see docs/hardware.md).

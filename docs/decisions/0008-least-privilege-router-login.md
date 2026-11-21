# 0008. A Wi-Fi-only router login for the admin service

Date: 2026-11-21 · Status: accepted · Supersedes the root-login part of [0006](0006-router-control-via-ubus.md)

## Context

Decision 0006 had the admin service sign in to the router's API as `root`, with the root
password stored in `.env`. A bug or compromise in the admin service, or a leaked `.env`,
would then hand over the whole router: shell commands, files, SSH settings, passwords.

## Decision

- `scripts/router-setup.sh` (run once on the router as root) creates an rpcd login named
  **`airplanemode`**, bound to the access group in
  [router/rpcd-acl-airplanemode.json](../../router/rpcd-acl-airplanemode.json):

  | Allowed | What for |
  |---|---|
  | `uci` get / changes / set / add / delete / apply / revert, on the `wireless`, `network`, `firewall` and `dhcp` configs only | hotspots; upstream client interface; the `wwan` network and its `wan` firewall zone; the offline popup's DNS entry and firewall rule (`dhcp` added by [0009](0009-connectivity-supervisor.md)) |
  | `iwinfo` info / scan / devices | scanning and Wi-Fi status |
  | `network.interface` status / dump | upstream link status |

  Everything else is denied: `file` (shell commands and file reads), other uci configs
  (`system`, `dropbear`, `rpcd`, ...), `system`, `rc`, and session management.
- The login's password is random (24 characters), stored on the router only as a hash
  (`uhttpd -m`), and written to `.env` (mode 600). Root's password is never stored anywhere.
  Re-running the script rotates it.
- `update.sh` warns if `.env` has no router login, or is still using `root`.

## Verification

Against real OpenWrt 24.10 (`admin/tests/test_openwrt_live.py` with `ROUTER_USER=airplanemode`),
every admin feature works under the restricted login, and 8 out-of-scope calls are refused:
running `/bin/sh`, reading `/etc/shadow`, reading the `system` and `rpcd` configs, changing
`dropbear` and `system`, `system.board`, and `rc.init`. As a control, root is allowed
`uci get system` and `system.board`, so those refusals come from the access list. Re-running
the script keeps a single login and replaces the password.

## Consequences

- Write access to `network` and `firewall` is still broad: a compromised admin service could
  rearrange interfaces or firewall zones. uci access lists work per config, not per section,
  so narrowing further would mean moving those changes into a small root-owned helper on the
  router. Not worth it yet.
- If a future feature needs another call (for example DHCP leases for a "connected devices"
  view), add it to the access list on purpose and re-run `router-setup.sh`.

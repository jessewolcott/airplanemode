# 0011. A homepage and one-line installer in their own repo

Date: 2026-11-21 · Status: accepted; hosting superseded by [0012](0012-site-own-server.md)

## Context

Setting up a box took a FriendlyElec wiki page plus six manual steps in the README (clone,
copy a profile, fix `LAN_IP`, two scripts, find the admin password). Each step is a place
to get stuck, and the README is written for people reading code, not for someone holding a
new router.

## Decision

- A separate repo, **`airplanemode-site`**, published by GitHub Pages at
  https://jessewolcott.com/airplanemode-site/. It holds a static homepage and
  `install.sh`. It's separate because it's the public front, changes on its own schedule,
  and publishing it shouldn't need a platform release.
- The homepage has a setup guide per device (flash, first login, adapters, install, name the
  hotspot) with a config tool that builds the exact install command (device, LAN address,
  WireGuard, rebind protection, WAN name, folder).
- `install.sh` is a **thin wrapper**: preflight checks (OpenWrt, arm64, Docker + compose,
  online, space, radios), clone to `/opt/airplanemode`, profile → `.env` with the LAN
  address read from uci, then this repo's `scripts/router-setup.sh` and `scripts/update.sh`.
  Afterwards it tests the admin service's ubus login and falls back to
  `ROUTER_URL=http://<LAN_IP>/ubus` if the Docker gateway is blocked. Router logic stays
  here, in one place.
- Downloads use **curl**. OpenWrt 24.10's built-in wget (uclient-fetch with
  libustream-mbedtls) fails the TLS handshake with GitHub's servers in testing, while curl on
  the same mbedTLS works, so the page's command installs curl first.

## Consequences

- `curl … | sh` asks people to trust a script from a web page. It's short, linked from the
  page ("read the script"), and only wraps scripts in this repo.
- Options are named in two places (the page's tool and `install.sh`); change them together.
  A new hardware profile also needs adding to the installer's detection and the page.
- Tested in `openwrt/rootfs:aarch64_generic-24.10.4` with a stub Docker (CI). *Untested* on
  real hardware, on FriendlyWrt 25.12 (apk instead of opkg), and on GL.iNet firmware.

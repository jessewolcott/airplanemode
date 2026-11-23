# AirplaneMode platform: how it's built

Checked-in reference for LLMs and engineers who open this repo with no prior context
(images are built from `admin/`, so none of this reaches an image). It describes the code as it is (snapshot: 2026-11-22, decisions
0001-0020), with `file:line` pointers. If the code and this folder disagree, the code wins;
fix this folder.

## Overview

AirplaneMode turns a small OpenWrt router (main target: FriendlyElec NanoPi R6S on
FriendlyWrt; lite target: arm64 GL.iNet) into a travel hotspot that also runs offline apps.
This repo is the **platform**: a Docker Compose stack in which a **Caddy** container is
the only published port (8080, on the router's LAN IP). It serves a static landing page
(`portal/`), proxies each app under `/<id>/`, and proxies `/api` and `/admin` to the
**admin service** (`admin/`, FastAPI). The admin service is the brain. It reports internet
state and detects hotel sign-in pages for the landing page. Behind a password it offers an admin panel for the upstream Wi-Fi,
hotspots, home VPN (WireGuard), device pause, data use and app menu switches. A background
**supervisor** auto-joins saved networks, prefers Ethernet, and switches an "offline popup"
on when there's no uplink at all. It changes router settings only through OpenWrt's
**ubus JSON-RPC** as a restricted rpcd login (`airplanemode`) whose access list lives in
`router/`. It never runs privileged and never uses root. An optional **llama.cpp** container
(`llm`, compose profile) serves a shared small model (Gemma 4 E2B, decision 0018) to apps; without it
(GL.iNet lite profile) the three model apps don't run (decision 0017). Apps (Dark Light, Drop,
Cinema, QuickShot, Doom, Travel Guide, Gate Change, Passport, Wayfarer, The Couch) live in sibling repos and are pulled as pinned images.
**Nothing has been tested on real hardware yet.** Router-side code has been tested against
a real OpenWrt 24.10 in Docker. Everything else has been tested against a mock router.

## Files

| File | What's in it |
|---|---|
| [architecture.md](architecture.md) | Containers, networks, volumes, ports. Request flow through Caddy (routes, prefix stripping, offline-popup redirect). Where data lives on disk. |
| [admin-service.md](admin-service.md) | `admin/main.py` module by module: config env vars, the `Store` (networks.json), auth/sessions, every HTTP endpoint (inputs, validation, router calls, `router_lock`), and the mock-only endpoints. |
| [router-control.md](router-control.md) | `admin/router.py`: the ubus JSON-RPC client, `uci apply`, every uci section the service owns, each backend method's exact ubus/uci calls, validators, lockout guard, the `MockRouter`. The rpcd ACL. |
| [supervisor.md](supervisor.md) | The supervisor loop and its state machine (auto-join, retries, grace period), captive-portal probing and `/api/status` caching, the offline popup end to end, data-use counting. |
| [vpn-devices-usage.md](vpn-devices-usage.md) | Home VPN (`admin/vpn.py` parser, uci layout, status), connected devices + pause rules, data use, app switches / admin session for apps. |
| [frontends.md](frontends.md) | `portal/index.html` (landing page) and `admin/static/index.html` (admin panel): what each fetches, state logic, UI sections. |
| [deployment.md](deployment.md) | `compose.yaml` / `compose.dev.yaml` service by service, hardware profiles and every env var, `scripts/router-setup.sh` and `scripts/update.sh` step by step, the external installer. |
| [apps.md](apps.md) | How apps plug in: the app contract, `portal/apps.json` fields (incl. `opens`), Caddy route, compose service, hidden apps, `/api/admin/session`. The current app roster. |
| [testing.md](testing.md) | `admin/tests/test_admin.py` (mock), `admin/tests/test_openwrt_live.py` (real OpenWrt in Docker, root and restricted login), CI workflow, on-device checklist status. |
| [security-and-gotchas.md](security-and-gotchas.md) | Security model and threat boundaries. Known gotchas (netifd hangs, fw4 DNAT, Caddy duplicate hosts, Windows port 8000, CRLF...). |
| [extending.md](extending.md) | Recipes: new endpoint, new router call / uci section (→ ACL + router-setup + live test), new app, new hardware profile, new supervisor behaviour. |

## Ten things to know before touching code

1. Router changes go **only** through ubus as the `airplanemode` login. A new ubus method or
   uci config means editing `router/rpcd-acl-airplanemode.json`, re-running
   `scripts/router-setup.sh` on the router, and updating the denied list in
   `admin/tests/test_openwrt_live.py`.
2. Save with `uci apply` (`router.py:196`), never `uci commit`: ubus sessions can't commit.
3. Every endpoint that changes the router takes `router_lock` (`main.py:88`). The supervisor
   tick takes it too.
4. The service only touches sections it owns (list in [router-control.md](router-control.md)).
   A firewall rule must never point at a zone that doesn't exist, because fw4 then rejects the whole ruleset.
5. Offline popup: only with **no uplink at all**, after `PORTAL_AFTER_TICKS` (2) ticks, never
   during a join grace period. Any uplink turns it off immediately.
6. Never allow disabling/deleting the last broadcasting hotspot (`guard_last_hotspot`).
7. Caddy is published only on `LAN_IP`. The admin container uses `dns: LAN_IP`.
8. Apps = pinned image in `compose.yaml` + route in `Caddyfile` + entry in `portal/apps.json`
   (CI checks these) + memory var in each `hardware/*.env` that runs it. `/api` and `/admin` are reserved.
9. Validate before saving: SSID ≤ 32 bytes, WPA key 8-63 printable ASCII or 64 hex,
   unicast MAC, WireGuard config parsed by `vpn.py`.
10. Mark anything not verified on hardware as *untested*. Keep `docs/testing.md`'s checklist current.
    Decisions go in new `docs/decisions/NNNN-*.md` records. Don't rewrite old ones.

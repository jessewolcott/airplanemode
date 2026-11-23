# How to extend

General rules: validate input server-side before writing anything. Take `router_lock` for
router changes. Touch only sections the service owns. Add tests to `admin/tests/test_admin.py`
(and the live test for router-side changes). Mark anything not verified on hardware as
*untested* and add it to the `docs/testing.md` checklist. Record significant choices as a new
`docs/decisions/NNNN-*.md` (and add it to `docs/decisions/README.md`); never rewrite old ones.
Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## A new admin endpoint

1. Define a pydantic body model near the related section of `admin/main.py`.
2. Add `_=Depends(require_admin)` (it gives 401 without a session and 403 for non-GET without
   `X-AirplaneMode: 1`). Only status-style data the landing page needs may be public, and it must
   never contain secrets.
3. Validate first (reuse `check_ssid`/`check_key`/`check_mac`, or write a validator that
   raises `RouterError` with a human message, which becomes a 400 `detail`).
4. If it changes the router, wrap the router call in `async with router_lock:`.
5. If it affects what `/api/status` shows, call `_bust()`.
6. If it persists local state, put it in `store.data[...]` and call `store.save()` (or
   `save_soon()` for high-frequency counters). Document the new field in
   [admin-service.md](admin-service.md#store-networksjson).
7. Add the method to **both** `OpenWrtRouter` and `MockRouter` (same signature, same errors).
8. Update the docstring at the top of `main.py`, the admin panel (`api()` already sends the header),
   and a test in `test_admin.py`. The tests run in definition order with shared state, so use
   `reset_world()` where needed.

## A new router call or uci config (the ACL checklist)

The restricted login only allows what `router/rpcd-acl-airplanemode.json` lists. When you need
a new ubus object/method or a new uci config:

1. Add it to the ACL (`read` for getters, `write` for setters; uci configs go in the `uci` lists).
   Keep it as narrow as possible.
2. Re-run `sh scripts/router-setup.sh` on the router. It reinstalls the ACL, restarts rpcd and
   rotates the password. Tell users to do this when they update.
3. Update the denied-calls list in `admin/tests/test_openwrt_live.py` if the new permission
   overlaps something that was explicitly denied. Add positive checks for the new call.
4. Run the live test as root **and** as the restricted user against a fresh
   `openwrt/rootfs:x86-64-24.10.4` container (`docs/testing.md`). Update the "Last run" note there.
5. Update the tables in README's security notes / decision 0008 lineage (via a new decision
   if the scope changes meaningfully).

New uci sections: give them a fixed name with the `airplanemode_` prefix (use `_put()` for
create-or-update and `_delete_sections()` for removal). Add the name to the owned-sections list in
`CLAUDE.md` and [router-control.md](router-control.md). Never point a firewall rule at a zone that may not exist.
Check generated rulesets with `fw4 print` plus `nft -c -f` (container with `--cap-add NET_ADMIN`).
Save via `_apply()`, never `uci commit`.

## A new app

See [apps.md](apps.md#adding-an-app-to-the-box-this-repo). Summary: an app repo following
`docs/app-contract.md` and a released, public arm64 image. Then in this repo: a `compose.yaml` service
(pinned tag; `profiles: ["llm"]` if it uses the model), a `Caddyfile` `redir` + `handle_path`, a `portal/apps.json`
entry (`uses_llm` for a model app), a `<ID>_MEM` in each `hardware/*.env` that runs it, a `compose.dev.yaml` build override, and the README table. Push and let CI check it.
To have Drop offer it, add `opens`.

## A new hardware profile

1. `hardware/<device>.env` with `COMPOSE_PROFILES`, `HTTP_PORT`, `LAN_IP`, `LLM_*` (or
   `LLM_URL=` empty), all `*_MEM`, Drop limits, `ROUTER_BACKEND`, `ROUTER_URL`.
2. Add it to the CI matrix in `.github/workflows/check.yml` and to the VS Code check task.
3. Document it in `docs/hardware.md` and the README. A decision record if it changes targets (0002).
4. Update `../airplanemode-site` (installer detection + page).

## Changing supervisor behaviour

- Keep the invariants: the popup only with **no uplink at all**, after `PORTAL_AFTER` ticks, never
  inside a join grace period, off immediately on any link. Never auto-join unsaved networks.
  Ethernet first via route metrics (VPN 5 < wan 10 < wwan 20).
- The tick holds `router_lock` for its whole duration. Keep ticks short (status calls use 5 s
  timeouts).
- Test with the fake clock: `tick(now)` in `test_admin.py`, and `reset_world()` first.
- New tunables go in as env vars with defaults in `main.py`, passed through `compose.yaml` if users
  should set them, and documented in [admin-service.md](admin-service.md#configuration-env-vars-read-by-mainpy).

## Changing the front door (Caddy)

- Reserved paths: `/api`, `/admin`. App blocks go before the platform blocks, and the `file_server`
  fallback stays last.
- Validate: `docker run --rm -e LAN_IP=<ip> -e HTTP_PORT=8080 -v "$PWD/Caddyfile:/etc/caddy/Caddyfile:ro"
  caddy:2.8-alpine caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile` for each
  profile's IP and 127.0.0.1 (CI does this). Then restart `proxy`.
- Don't break the `@elsewhere` offline-popup redirect. It must exclude the box's own names and IP literals.

## Changing scripts

`router-setup.sh` and `update.sh` must stay POSIX `sh` (busybox ash), idempotent, LF line
endings, and must never store the root password. If their flags or behaviour change, update
`../airplanemode-site/install.sh` and its page.

## Releasing

Commit/push to `main` freely for requested work. **Ask before pushing `v*` tags** (they publish
container images). Platform updates on a box arrive via `scripts/update.sh` (`git pull` + pinned images).

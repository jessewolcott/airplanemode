# Tests and CI

## 1. Admin API tests with the mock router: `admin/tests/test_admin.py`

Run: `.venv\Scripts\python admin\tests\test_admin.py` (Windows; the venv is from the VS Code task
"Setup: create .venv") or `python admin/tests/test_admin.py`. CI runs it on Python 3.12.
Locally it runs on Python 3.10.

How it works:
- Sets env **before importing `main`**: `ROUTER_BACKEND=mock`, `ADMIN_PASSWORD=test-pass`,
  `SUPERVISE_INTERVAL=0` (no background task), `DATA_DIR=<tempdir>`, `JOIN_GRACE=45`,
  `PORTAL_AFTER_TICKS=2`.
- Uses FastAPI's `TestClient(main.app)` (needs httpx, which is in requirements). The header dict `H =
  {"X-AirplaneMode": "1"}`.
- No pytest: `__main__` runs every `test_*` function **in definition order** and prints `ok`.
  The tests share one client (cookie session) and module state, so the order matters
  (`test_auth` logs in, `test_logout` comes last). `reset_world()` resets the mock router, the store
  and the supervisor between supervisor tests.
- `tick(now)` = `asyncio.run(main.supervisor.tick(now))` with a fake monotonic clock.

What each test covers:

| Test | Checks |
|---|---|
| `test_validation` | SSID/key/MAC validators, `scan_security` mapping |
| `test_auth` | 401 before login, wrong password, login, 403 without the header |
| `test_captive_flow` | offline → join the hotel → captive + login_url → mock portal POST 303 → online. Wrong key → offline. MAC clone stored. Upstream key never returned. Disconnect → offline |
| `test_hotspots` | Bad key 400, save, disable, lockout guard (400 "lock") on disable/delete, add a new one, then delete allowed. `/api/status` never contains "key" |
| `test_guard_ignores_hotspots_on_radios_that_are_off` | A hotspot on a disabled radio doesn't count for the guard |
| `test_manual_join_is_remembered` | Connect saves the network (`has_key`, no key exposed). An invalid key isn't saved. Written to `networks.json` |
| `test_autojoin_strongest_then_falls_back` | Nothing in range → popup after 2 ticks. The strongest saved network is tried, waits during grace, retried once, skipped after 2 fails, next one joins. Popup off on link. Unsaved networks are never joined |
| `test_ethernet_wins` | Ethernet → online via ethernet. No Wi-Fi hunting or popup with a cable. Unplugging → auto-join |
| `test_disconnect_pauses_autojoin` | Disconnect pauses. Resume via `/api/admin/autojoin` |
| `test_forget` | Forget, 404 on a repeat, no auto-join afterwards. An SSID with `/` (URL-encoded) |
| `test_router_changes_are_serialized` | The supervisor tick waits for an admin change holding `router_lock` |
| `test_offline_popup_waits_during_join` | No popup inside grace periods. Popup only after both join attempts fail |
| `test_wireguard_config_parsing` | Parser happy path (IPv6 endpoint, DNS search domain dropped). 7 bad configs rejected |
| `test_vpn_api` | VPN CRUD. Keys never returned. `/api/status` vpn fields. VPN on behind a portal → offline. VPN off → captive. Enabling a missing VPN gives 400 |
| `test_devices_pause` | `you` from X-Forwarded-For, pause/resume, multicast MAC gives 400 |
| `test_lost_and_found` | `here` null before a sample; all here with `since`; one leaves → listed last with `last_seen`; a 1-minute blip keeps `since`, an 11-minute gap starts a new visit; expired lease still named from `host`; pruned after `SEEN_KEEP_DAYS`; refused station list keeps history and shows `here: null`; mock world `away` by name; clear history |
| `test_data_use` | Baseline then deltas, Ethernet becomes current, counter reset handling, reset endpoint |
| `test_apps_on_the_menu` | `/api/apps` filtering, 403 without the header, 404 for an unknown id, admin list, session endpoint with and without the cookie, a missing apps file gives `[]` |
| `test_logout` | Logout → 401 |

## 2. Live test against real OpenWrt: `admin/tests/test_openwrt_live.py`

Drives `OpenWrtRouter` against the **real** ubus/rpcd/uhttpd of `openwrt/rootfs:x86-64-24.10.4`
in Docker. It isn't in CI. It's run manually (recipe in `docs/testing.md`):

```sh
docker run -d --name owrt-test --network airplanemode openwrt/rootfs:x86-64-24.10.4 /sbin/init
docker exec owrt-test sh -c 'printf "testpw\ntestpw\n" | passwd root'
# write a fake /etc/config/wireless: radio0 (2g) + radio1 (5g), default_radio0/1 APs
docker compose build admin
docker run --rm --network airplanemode -v "$PWD/admin:/app:ro" \
  -e ROUTER_URL=http://owrt-test/ubus -e ROUTER_PASSWORD=testpw \
  airplanemode-admin:local python tests/test_openwrt_live.py
```

The `airplanemode` network exists when the stack (dev or prod) is up. Use a throwaway
container, never a real router.

The restricted-login variant uses a **fresh** container. It copies `scripts/router-setup.sh`,
the ACL and `hardware/nanopi-r6s.env` (as `.env`) into `/opt/airplanemode`, runs the script, reads
`ROUTER_PASSWORD` from that `.env`, and runs the test with `ROUTER_USER=airplanemode`.

Checks, in order: overview. Bad password → RouterError. Join an open network (STA + wwan
metric 20 + wan zone). Re-join with key + MAC, then without the MAC (option removed, zone not
duplicated). `upstream_status` returns in < 8 s (it doesn't hang). Leave. Save/add/disable/delete
hotspots + the lockout guard. Changes visible from a fresh session (committed). Scan fails cleanly
without radios. Ethernet status doesn't hang. Popup on (DNS + DNAT, idempotent) and off (other DNS entries kept).
Devices + pause rule. Counters don't hang. VPN set (interface/peer/zone/forwarding, `_vpn` pause rule,
keys hidden), PSK removed on re-save, disable, status. Unpause removes both rules. Remove the VPN
(the `_vpn` rule removed, the wan rule kept). With a non-root user, **11 denied calls**:
`file exec`, `file read /etc/shadow`, `uci get system`, `uci get rpcd`, `uci set dropbear`,
`uci set system`, `system board`, `rc init`, `luci-rpc getBoardJSON`,
`luci.wireguard generateKeyPair`, `network.device set_state`.

Last recorded run: 2026-11-21, all pass. In an unprivileged container **netifd hangs**, so live
link state, `network reload` and Wi-Fi reloads can't be verified there. That's an environment
limit, not a bug. To check generated firewall rulesets: `fw4 print`, then `nft -c -f` in a container
with `--cap-add NET_ADMIN`.

## 3. CI: `.github/workflows/check.yml` (runs on every push and PR)

| Job | Steps |
|---|---|
| `admin` | checkout@v7, setup-python@v7 (3.12), `pip install -r admin/requirements.txt`, `python admin/tests/test_admin.py` |
| `compose` (matrix: `nanopi-r6s`, `glinet`) | `cp hardware/<hw>.env .env`. `docker compose config --quiet` + `--services`. **Caddyfile validate** with `caddy:2.8-alpine` for the profile's `LAN_IP` *and* `127.0.0.1`. **Every portal app has a route**: for each `path` in `apps.json`, it greps `handle_path /<name>/\*` in the Caddyfile and `^  <name>:` in compose.yaml. **Pinned images have arm64**: `docker compose --profile llm config --images`, skipping `*:local`, then `docker manifest inspect` must mention `"arm64"` |

CI doesn't check: `hardware/*.env` memory lines for new apps, the live OpenWrt test, the
front-ends (they were checked by hand in a browser on the dev stack, e.g. with Playwright).

## 4. Local equivalents

- VS Code tasks (`.vscode/tasks.json`): set up the venv, dev stack up/down/restart proxy/admin logs,
  run the tests, check compose + Caddyfile for both profiles and three LAN IPs.
- `docker compose --env-file hardware/nanopi-r6s.env config --quiet` (and glinet).

## 5. Manual dev flow (mock)

http://localhost:8080 → admin (`dev`) → Scan → `Harbor_Hotel_Guest` → Connect → the landing page shows
*Sign-in needed* → Open the sign-in page → accept → *Online*. Simulate travel:
```sh
curl -X POST localhost:8080/api/mock/world -H 'Content-Type: application/json' -d '{"in_range": []}'
# {"in_range": ["CoffeeCorner"]}   {"ethernet": true}
```
The mock reconnects to the configured network when it comes back in range, so switching between
saved networks is covered by the tests rather than the UI demo.

## 6. On-device checklist (`docs/testing.md`)

**All items are still unchecked. The hardware build is in progress; nothing has been tested on it yet.** Items:
router-setup.sh on the real firmware (uhttpd), two radios, admin reaches ROUTER_URL, scan,
connect + wwan address, `uci apply` reloads radios, real captive portal + shared sign-in,
passthrough popup, admin DNS gives captive (not offline), offline popup on iPhone/Android +
`fw4 print`, auto-join between networks, Ethernet first (`ip route` metrics), MAC clone,
hotspot change survives reboot, **8080 not reachable from the upstream side**, a real VPN
(handshake, home IP, no leak, DNS, Docker traffic), VPN behind a sign-in page, devices +
pause (also with the VPN), data use accuracy, model tokens/s (update decision 0003),
temperature/power. Keep this list current when behaviour changes.

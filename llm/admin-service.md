# Admin service (`admin/main.py`)

A single FastAPI app (~850 lines) plus two helper modules:
- `admin/router.py`: router backends (OpenWrt over ubus, and a mock). See [router-control.md](router-control.md).
- `admin/vpn.py`: WireGuard config parser. See [vpn-devices-usage.md](vpn-devices-usage.md).
- `admin/static/index.html`: the admin panel. See [frontends.md](frontends.md).

The module docstring (`main.py:1-40`) lists the endpoints.

## Layout of `main.py`

| Lines | Part |
|---|---|
| 60-75 | Config from env vars |
| 79-86 | `httpx.AsyncClient` + router backend selection |
| 91 | `router_lock = asyncio.Lock()` |
| 94-103 | `lifespan`: starts the supervisor task, flushes usage on shutdown. `FastAPI(docs_url=None, redoc_url=None, openapi_url=None)`, so there's no Swagger |
| 106-108 | `RouterError` → HTTP 400 `{"detail": msg}` |
| 111-174 | `Store`: `networks.json` |
| 177-304 | `Supervisor` + `_supervise_forever` (see [supervisor.md](supervisor.md)) |
| 307-372 | Status cache, `probe()`, `vpn_summary()`, `GET /api/status` |
| 375-426 | Sessions, login/logout, `require_admin`, `/api/admin/session` |
| 429-484 | App menu: `/api/apps`, `/api/admin/apps`, `PUT /api/admin/apps/{id}` |
| 487-612 | Who's who: `/api/me` (decision 0021) |
| 615-724 | Wi-Fi: wifi overview, scan, upstream join/leave, autojoin, forget, hotspots |
| 727-749 | Devices + pause |
| 752-766 | Data use |
| 769-806 | Home VPN |
| 809-841 | Mock-only endpoints (`ROUTER_BACKEND=mock`) |
| 844 | `app.mount("/admin", StaticFiles(directory=static, html=True))` |

## Configuration (env vars read by `main.py`)

| Var | Default | Meaning |
|---|---|---|
| `ROUTER_BACKEND` | `openwrt` | `mock` selects `MockRouter` and enables `/api/mock/*` |
| `ROUTER_URL` | `http://host.docker.internal/ubus` | ubus JSON-RPC endpoint (uhttpd) |
| `ROUTER_USER` | `airplanemode` | rpcd login |
| `ROUTER_PASSWORD` | empty | Empty means every router call raises "ROUTER_PASSWORD isn't set" |
| `ADMIN_PASSWORD` | empty | Empty means login returns **503** ("Admin is disabled") |
| `LAN_IP` | `192.168.2.1` in code (compose passes `${LAN_IP:-127.0.0.1}`) | DNAT target for the offline popup |
| `HTTP_PORT` | `8080` | DNAT target port |
| `WAN_IFACE` | `wan` | Wired WAN interface name (status + counters) |
| `PROBE_URL` | `http://connectivitycheck.gstatic.com/generate_204` | Captive-portal probe (not set in compose) |
| `DATA_DIR` | `/data` | Where `networks.json` lives |
| `SUPERVISE_INTERVAL` | `30` (dev: `5`) | Seconds between ticks. `0` means no supervisor task (the tests use this) |
| `AUTOJOIN` | `1` | `0` turns auto-join off entirely |
| `OFFLINE_PORTAL` | `1` | `0` turns the offline popup off entirely |
| `JOIN_GRACE` | `45` (dev: `6`) | Seconds to wait for a join to come up |
| `PORTAL_AFTER_TICKS` | `2` | Down ticks before the popup turns on (`PORTAL_AFTER`) |
| `APPS_FILE` | `/config/apps.json` | Installed-apps catalogue (bind mount of `portal/apps.json`) |

Constants: `MAX_TRIES = 2` failed joins, then skip for `RETRY_AFTER = 600` s.
`SESSION_HOURS = 12`.

## Concurrency: `router_lock`

The supervisor and HTTP handlers share **one** ubus session (`OpenWrtRouter.sid`). A
`uci apply` applies *everything staged in that session*, so two interleaved changes could
apply each other half-done. Every handler that changes the router does
`async with router_lock:`, and `Supervisor.tick()` holds it for the whole tick
(`main.py:256-258`). Handlers that only touch the local `Store` (autojoin, forget, app
switches, usage reset) don't take it. Read-only handlers (wifi overview, scan, devices
list, vpn summary, status) don't take it either. `test_router_changes_are_serialized` checks
the ordering.

Note: `POST /api/admin/scan` doesn't take the lock, but the supervisor's own scan runs inside
its tick.

## Store (`networks.json`)

`Store(DATA_DIR / "networks.json")` (`main.py:109-171`). It's loaded at import time. A missing
file is fine, and a corrupt file logs a warning and starts with defaults. `save()` writes
`networks.tmp` with `indent=1`, `chmod 600`, then atomically replaces the real file. OS errors
are logged, never raised.

```jsonc
{
  "networks": [            // most recent first, max 50; one entry per SSID
    {"ssid": "CoffeeCorner", "encryption": "psk2", "key": "latte2024",
     "macaddr": null, "radio": "radio1", "last_used": 1763700000.0}
  ],
  "paused": false,         // auto-join paused (set by Disconnect, cleared by Connect/Resume)
  "usage": {               // data use per uplink name ("Ethernet" or an SSID), max 100 (oldest "last" dropped)
    "CoffeeCorner": {"rx": 123, "tx": 45, "first": 1763700000.0, "last": 1763703600.0}
  },
  "hidden_apps": ["cinema"]  // app ids switched off in the admin panel (sorted)
}
```

- `remember(radio, ssid, encryption, key, macaddr)` moves/inserts the network at index 0 and saves.
- `forget(ssid)` returns whether anything was removed.
- `public()` is the list for the browser: `ssid, encryption, has_key, macaddr, last_used`.
  **It never includes keys.**
- `add_usage(name, rx, tx)` marks the store dirty. `save_soon(every=300)` writes at most every 5 min
  to spare the flash. Shutdown calls `save_soon(every=0)`.

## Authentication

- Sessions are an in-memory dict `token → expiry` (`_sessions`, `main.py:374`). A token is
  `secrets.token_urlsafe(32)` and lasts 12 h.
- `POST /api/admin/login {password}`: 503 if `ADMIN_PASSWORD` is unset. It compares with
  `hmac.compare_digest` inside `_login_lock`, so only one check runs at a time, and a wrong
  password sleeps 1.5 s while holding the lock. Guessing can't be parallelised that way. Expired
  sessions are pruned. The response sets cookie `am_admin` with `HttpOnly`, `SameSite=Strict`,
  `Max-Age=43200`, `Path=/`. A wrong password gives 401 "Wrong password.".
- `require_admin(request)` (`main.py:400`) is a dependency on every admin route:
  - a missing or expired cookie gives **401** "Please sign in." (and drops the token);
  - a non-GET request without the header `X-AirplaneMode: 1` gives **403** "Missing request header."
    That's the CSRF defence: cross-site form posts can't set custom headers.
- `POST /api/admin/logout` deletes the session and the cookie.
- `GET /api/admin/session` returns `{"ok": true}` for a live session and 401 otherwise. Apps (Drop,
  QuickShot) call it from their backend at `http://admin:8000`, forwarding the browser's
  `am_admin` cookie, to know whether the user is the admin (decision 0013).

## Endpoints

Every handler's `RouterError` (including `UbusError`) becomes `400 {"detail": "<human message>"}`.
Pydantic body errors are 422 (the panel shows "Check the fields and try again.").

### Public

| Method + path | Body / query | Does |
|---|---|---|
| `GET /api/status` | `?refresh=1` bypasses the cache | Internet state for the landing page. See [supervisor.md](supervisor.md#apistatus). Never returns keys or the VPN endpoint. |
| `GET /api/apps` | none | `installed_apps()` minus `hidden_apps`. Reads `APPS_FILE` on every call. A missing file gives `[]`, a bad file logs and gives `[]`. Only dict entries with a string `id` are kept. |
| `GET /api/me` | none | Who's who (decision 0021): `{known, id, name, avatar, avatars}`. Sets the `am_who` cookie if missing |
| `PUT /api/me` | `{name, avatar}`, header `X-AirplaneMode: 1` | `check_name` (whitespace folded, 1–20 chars, no control chars), avatar must be in `AVATARS`. 403 without the header, 400 on bad input |
| `DELETE /api/me` | header `X-AirplaneMode: 1` | Forgets the person and every link to them |

### Who's who (`main.py`, section after the app menu)

- `store.data["people"] = {pid: {name, avatar, updated}}` (max 300, oldest `updated` dropped)
  and `store.data["who_links"] = {"mac:<mac>" | "tok:<am_who cookie>": pid}`, in `networks.json`.
- `_who(request, response)`: the caller's keys are the MAC whose DHCP lease holds
  `client_ip(request)` (`mac_for`, leases cached 10 s, a miss refreshes after 3 s; a
  `RouterError` means "no MAC") plus the `am_who` cookie (`HttpOnly`, `SameSite=Lax`, 1 year).
  The first key linked to a live person wins, and the other keys are linked to them too, so a
  second browser or a new random MAC picks up the same person.
- Apps get `id = HMAC(who_secret, pid)[:16]`, never MACs. `who_secret` is made once per box.
- `GET /api/admin/devices` adds `person: {name, avatar} | null` per device (`person_for_mac`).
- No router changes, so no `router_lock`.

### Admin: app menu

| Method + path | Body | Does |
|---|---|---|
| `GET /api/admin/apps` | none | `{"apps": [{...entry, "enabled": bool}]}` |
| `PUT /api/admin/apps/{app_id}` | `{enabled: bool}` | 404 if the id isn't in `apps.json`. Adds/removes the id in `store.data["hidden_apps"]` and saves. No router involvement, no lock. |

### Admin: Wi-Fi

| Method + path | Body | Validation | Router calls (under `router_lock`) | Side effects |
|---|---|---|---|---|
| `GET /api/admin/wifi` | none | none | `overview()`, `upstream_status()`, `ethernet_status()`, `portal_mode()` | Returns `{radios, hotspots (with keys), upstream (no key), upstream_status, ethernet, saved: store.public(), autojoin: {enabled, paused, joining, last}, offline_popup, backend}` |
| `POST /api/admin/scan` | `{radio}` | none (the backend checks the radio) | `scan(radio)` (no lock) | `{"networks": [{ssid, bssid, channel, signal, security}]}`, strongest first, deduplicated by SSID. `security: null` means enterprise (unsupported) |
| `POST /api/admin/upstream` | `{radio, ssid, encryption, key?, macaddr?}` | `check_ssid`, `check_key(.., UPSTREAM_SECURITY)`, `check_mac` **before** touching anything | `join(...)` | `store.remember(...)`, `paused=False`, save. Sets `supervisor.joining=ssid`, `grace_until=now+JOIN_GRACE`, clears that SSID's fail count, busts the status cache |
| `DELETE /api/admin/upstream` | none | none | `leave()` (sets the STA `disabled=1`) | `paused=True` (or auto-join would reconnect at once), clears `joining`/grace, busts the cache |
| `POST /api/admin/autojoin` | `{enabled}` | none | none | `paused = not enabled`, save, bust |
| `DELETE /api/admin/networks/{ssid:path}` | none | none | none | `store.forget`. 404 if unknown. `:path` because SSIDs may contain `/` (the client URL-encodes) |
| `POST /api/admin/hotspots` | `{radio, ssid, encryption, key?}` | in the backend: `check_ssid`, `check_key(.., HOTSPOT_SECURITY)`, the radio exists | `add_hotspot(...)` | Returns `{"id": "<uci section name>"}` |
| `PUT /api/admin/hotspots/{hid}` | `{ssid, encryption, key?, disabled=false}` | in the backend, plus the lockout guard if `disabled` | `save_hotspot(...)` | none |
| `DELETE /api/admin/hotspots/{hid}` | none | the lockout guard | `delete_hotspot(hid)` | none |

`UPSTREAM_SECURITY = none, psk, psk2, psk-mixed, sae, sae-mixed`.
`HOTSPOT_SECURITY = sae-mixed, psk2, sae, none`.

### Admin: devices, data use, VPN

| Method + path | Body | Does |
|---|---|---|
| `GET /api/admin/devices` | none | `router.devices()` with `you: true` where the lease IP equals `client_ip(request)` (the last `X-Forwarded-For` entry, else the socket peer), and `person` from who's who; merged with lost & found (`here`, `since`, `last_seen`, plus devices whose lease expired), and `tracking`, `keep_days`. See [vpn-devices-usage.md](vpn-devices-usage.md#lost--found-decision-0025) |
| `DELETE /api/admin/devices/history` | none | Clears lost & found (`seen`) |
| `POST /api/admin/devices/{mac}/pause` | `{paused}` | Under the lock: `set_device_paused(mac, paused)` (`check_mac` in the backend, 400 on multicast or a bad format) |
| `GET /api/admin/usage` | none | `{"current": supervisor.session, "linked": bool, "networks": [{name, rx, tx, first, last}]}`, newest first |
| `DELETE /api/admin/usage` | none | Clears `usage`, zeroes the current session (keeps its name, resets `since`), saves |
| `GET /api/admin/vpn` | none | `vpn_summary()`: `{"configured": false}` or `{configured, enabled, addresses, dns, endpoint, public_key, allowed_ips, full_tunnel, up, handshake_age, connected, rx, tx}`. **Never the private or preshared key.** |
| `PUT /api/admin/vpn` | `{config: str, enabled=true}` | `parse_wg_conf(config)` first (400 with a message), then under the lock `set_vpn(cfg, enabled)`, bust |
| `POST /api/admin/vpn/enabled` | `{enabled}` | Under the lock: `set_vpn_enabled`. 400 "No home VPN is set up yet." if none |
| `DELETE /api/admin/vpn` | none | Under the lock: `remove_vpn()` |

### Mock-only (`ROUTER_BACKEND=mock`, `main.py:678-709`), no auth

| Method + path | Does |
|---|---|
| `GET /api/mock/portal` | HTML "Harbor Hotel" pretend sign-in page (a checkbox and a Connect button) |
| `POST /api/mock/portal` | `router.clear_portal()` (marks the current SSID signed in), bust, `303 → /?signed-in=1` (the landing page then refreshes the status) |
| `POST /api/mock/world` | `{ethernet?: bool, in_range?: [ssid]}`: simulates travel, runs one `supervisor.tick()` immediately, returns `{ethernet, in_range, offline_popup, tick}` |

In mock mode `probe()` returns `router.internet()` and `login_url="/api/mock/portal"` when captive.

## Status cache

`_status_cache = {"at", "value"}`. `/api/status` recomputes when `refresh`, empty, or older
than 10 s. `_bust()` empties it and is called after joins, leaves, autojoin changes, VPN
changes, mock portal/world changes, and any supervisor tick that took an action.

## Logging

`logging.basicConfig(INFO)`, logger `airplanemode`. The supervisor logs actions
("auto-joining ...", "join of ... didn't come up (n/2)", "supervisor: offline popup on").
Tick exceptions are logged and swallowed (`_supervise_forever`). See them with
`docker compose logs -f admin`.

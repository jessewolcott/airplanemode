# Front-ends

Both are single static HTML files with inline CSS and vanilla JS. No build step, no
frameworks, no external assets (offline-first). They share a dark palette (`--bg #0d0f12`,
accent `#f2b34b`) and an `esc()` HTML-escaping helper used for every interpolated value.

## Landing page: `portal/index.html`

Served by Caddy's fallback `file_server` from `/srv/portal` (bind mount of `portal/`). It's
the page everyone sees, including the offline-popup "sign-in" sheet. Assets: `icon.svg`,
`apple-touch-icon.png`, `apps.json`.

### App menu

`fetch("/api/apps")`. If that fails (admin down), it falls back to the static `/apps.json`, so the menu
still shows everything. Each app renders as a card link to `a.path`, with a monogram icon on
`a.color`, plus name and description. Apps with `uses_llm` get the tag "uses the on-board model".

### Status

`check(refresh)` fetches `/api/status` (with `?refresh=1` when refreshing) on load and every 10 s.
On load it refreshes when the URL has `?signed-in` (the mock portal redirects to `/?signed-in=1`).

`describe(d)` picks the title and detail, in this order:
1. `internet == online`: "Online" / "Online via Ethernet", adding ", through your home VPN" when
   `vpn.enabled && vpn.connected`.
2. `vpn.enabled && via && internet == offline`: "Waiting for the home VPN" (turn the VPN off, sign in,
   turn it on).
3. `captive`: "Sign-in needed".
4. `joining`: "Connecting…" (joining a saved network).
5. `via` but offline: "No internet". Connected to the network, but it isn't reaching the internet.
6. Else: "No internet". Not connected to any network.

The dot's state is `captive` (amber) while joining-and-offline, else `d.internet`.

Panels:
- **"This Wi-Fi needs a sign-in"** (`#signin`), shown when captive. *Open the sign-in page*
  links to `login_url` (new tab). *I've signed in* calls `check(true)`.
- **"Get online"** (`#getonline`), shown when offline with no `via` and not joining. It links to
  `/admin/#internet`. The text varies: saved networks with auto-join on, auto-join paused, or no saved networks.
  It also suggests plugging a cable into WAN.
- Footer: "demo mode (mock router)" when `backend == mock`. Top right: an `admin` link.

## Admin panel: `admin/static/index.html`

Served by FastAPI at `/admin/` (`StaticFiles(html=True)`). It has a login card and the panel
(`#panel`, hidden until `GET /api/admin/wifi` succeeds).

### Plumbing

- `api(method, url, body)` sends JSON with header **`X-AirplaneMode: 1`** (required for
  non-GET by `require_admin`). A 401 (except on login) → `showLogin()`. An error shows
  `detail` if it's a string, else "Check the fields and try again." (422s).
- `busy(btn, fn)` disables the button during a call. `msg(el, text, ok)` shows status.
- `[data-show]` buttons toggle password visibility.
- Start-up: `load().catch(showLogin)`. `load()` = `GET /api/admin/wifi`, then fill the radio selects,
  render hotspots, prefill the join form from the current upstream, `refreshNet()`, `loadExtras()`,
  `loadApps()`.
- `setInterval(loadExtras, 15000)` while the panel is visible (VPN, devices, usage).

### Sections (top to bottom)

1. **Internet source** (`#internet`, the target of the landing page's link)
   - Status line from `/api/status?refresh=1` plus `state.upstream_status` / `state.ethernet`:
     online through X / needs a sign-in / auto-connecting / connected but no internet / trying /
     not connected. Shows the IP, and the Ethernet plugged/unplugged note.
   - Captive row with an *Open the sign-in page* link.
   - Radio select + **Scan** → `POST /api/admin/scan` → a table with signal bars (≥-55 gives 4 bars,
     -65 gives 3, -75 gives 2, else 1), security label, channel. Clicking a row fills the join form. An
     enterprise network (`security: null`) shows an "802.1X isn't supported" message.
   - Join form: SSID, security select (open/WPA2/WPA2-3/WPA3/WPA-WPA2/WPA), password (hidden
     for open), *Advanced: MAC clone* field. **Connect** → `POST /api/admin/upstream`, then a
     re-poll after 8 s. **Disconnect** → `DELETE /api/admin/upstream`.
   - Saved networks list with *forget* (`DELETE /api/admin/networks/<urlencoded ssid>`). The auto-join
     line shows: off (AUTOJOIN=0) / **paused** with a *Resume* button
     (`POST /api/admin/autojoin {enabled:true}`) / the normal explanation.
2. **Home VPN** (`#vpn`): the status dot/text from `GET /api/admin/vpn` (off / connected with
   handshake age and ↓↑ bytes / unknown state because luci-proto-wireguard is missing / on but not
   connected, with the sign-in hint). Shows a note when it isn't a full tunnel. Toggle → `POST /api/admin/vpn/enabled` (reverts on
   error). *Remove* → `DELETE`. A setup `<details>` with a textarea + file input → `PUT /api/admin/vpn
   {config, enabled:true}`. The private key is never shown again.
3. **Connected devices** (`#devices`): `GET /api/admin/devices`. Tags *this device* and *paused*.
   A pause/resume button → `POST /api/admin/devices/<mac>/pause`. Pausing your own device asks for confirmation.
4. **Data use** (`#usage`): the current session (since time, total, ↓/↑) and a per-network list.
   *Reset counters* → `DELETE /api/admin/usage`.
5. **Your hotspots**: one form per hotspot (SSID, security from `HOTSPOT_SEC`, plus the current value
   if it's non-standard, password prefilled since the admin API returns hotspot keys, an *On* checkbox).
   Save → `PUT /api/admin/hotspots/<id>`. Remove → `DELETE`. *Add a hotspot* → `POST`.
6. **Apps on the menu**: `GET /api/admin/apps`. A checkbox per app → `PUT /api/admin/apps/<id>`
   (reverts on error). Hidden apps are greyed out.

## Screenshots

`docs/screenshots/` (`landing-online.png`, `landing-signin.png`, `admin-vpn-devices.png`,
`guide-ask.png`, `drop.png`) were taken from the dev stack with the mock router. Retake them from
the dev stack when the UI changes.

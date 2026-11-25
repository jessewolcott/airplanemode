# Architecture

## Physical picture

```
Hotel / airport / plane Wi-Fi  ──(USB adapter 1, STA: wireless.airplanemode_up, network wwan, metric 20)──┐
Room Ethernet cable            ──(WAN port: network wan, metric 10)────────────────────────────────────────┤
                                                                                                           │
                      OpenWrt / FriendlyWrt router: NAT + fw4 firewall + dnsmasq + uhttpd(/ubus) + rpcd   │
                      Optional WireGuard tunnel: network amvpn, metric 5 (beats both uplinks)             │
                                                                                                           │
Your phones / laptops  ──(USB adapter 2, AP hotspots, network lan, e.g. 192.168.2.0/24)───────────────────┘
        │
        └── http://airplane.mode:8080  (dnsmasq maps the name to LAN_IP)  →  Docker on the router
```

The hotel sees **one** client, the router's STA interface (optionally with a cloned MAC).
Everything on the hotspot is NATed behind it, so one sign-in covers every device
(decision 0007). Route metrics decide the uplink: VPN 5 < Ethernet 10 < Wi-Fi 20.

## Containers (compose project `airplanemode`, network `airplanemode`)

Defined in `compose.yaml`. Only `proxy` publishes a port.

| Service | Image | Port (internal) | Volumes | Role |
|---|---|---|---|---|
| `proxy` | `caddy:2.8-alpine` | 80, published as `${LAN_IP}:${HTTP_PORT}` (default `127.0.0.1:8080`) | `./Caddyfile` (ro), `./portal` → `/srv/portal` (ro), `drop-data` → `/srv/drop` (ro, serves Drop's files), `caddy-data` | Front door: landing page, app routes, `/api` + `/admin` |
| `admin` | built from `./admin` (tag `airplanemode-admin:local`) | 8000 (uvicorn) | `admin-data` → `/data`, `./portal/apps.json` → `/config/apps.json` (ro) | Status API, captive check, admin panel + API, supervisor |
| `llm` | `ghcr.io/ggml-org/llama.cpp:server-b12602` | 8080 | `models` → `/models` | Shared model, **only with profile `llm`** (R6S). `cpuset: ${LLM_CPUS}` (R6S: `4-7`, the A76 cores), `--cache-ram ${LLM_CACHE_RAM:-256}` (decision 0024) |
| `darklight` | `ghcr.io/jessewolcott/airplanemode-darklight:0.3.2` | 8000 | (none) | Text adventure (uses LLM) |
| `drop` | `...-drop:0.2.2` | 8000 | `drop-data` | File/notes sharing. Asks admin for session checks |
| `cinema` | `...-cinema:0.2.0` | 8000 | `cinema-data` (posters) | Plays Drop's media, TV series since 0.1.1, watch parties since 0.2.0 (`DROP_URL=http://drop:8000`, `ADMIN_URL`) |
| `quickshot` | `...-quickshot:0.1.2` | 8000 | `quickshot-data` | Swipe feed over Drop's QuickShot folder. Admin deletes comments |
| `doom` | `...-doom:0.1.1` | 8000 (busybox httpd) | (none) | Static files: PrBoom+ in WebAssembly runs in the browser. Reads Drop WADs from the browser |
| `guide` | `...-guide:0.1.2` | 8000 | `guide-data` (~260 MB Wikivoyage) | Offline travel guide (uses LLM) |
| `gatechange` | `...-gatechange:0.2.0` | 8000 | (none) | Colour/number card game: rooms + long-poll + bots, in memory, on the Rooms SDK |
| `passport` | `...-passport:0.2.0` | 8000 | (none) | Deck-builder card game, on the Rooms SDK like gatechange |
| `wayfarer` | `...-wayfarer:0.2.1` | 8000 | `wayfarer-data` (scores.json) | Trivia; question bank pre-built from the guide's ZIM and baked into the image |
| `couch` | `...-couch:0.2.4` | 8000 | (none) | Therapist simulator (uses LLM, streams SSE); stores nothing |

Internal service-to-service calls use Docker DNS names: apps → `http://llm:8080` (via
`LLM_URL`), Drop/QuickShot → `http://admin:8000` (via `ADMIN_URL`) for
`/api/admin/session`, Cinema/QuickShot → `http://drop:8000`.

The admin container also has:
- `dns: [${LAN_IP}]` (`compose.yaml:57`). Docker can't reach the host's resolver at
  127.0.0.1 and would fall back to public DNS, which hotels block before sign-in. The
  captive-portal probe would then wrongly say "offline". The dev override resets this to `[]`.
- `extra_hosts: host.docker.internal:host-gateway`, so the default
  `ROUTER_URL=http://host.docker.internal/ubus` reaches the router's uhttpd.
- `mem_limit` from `${ADMIN_MEM:-96m}` (every service has a `*_MEM` var).

Admin image (`admin/Dockerfile`): `python:3.12-slim`, `pip install fastapi==0.115.6
uvicorn==0.34.0 httpx==0.28.1`, `CMD uvicorn main:app --host 0.0.0.0 --port 8000
--proxy-headers`. `admin/.dockerignore` excludes `tests` and `__pycache__`.

## Request flow through Caddy (`Caddyfile`)

Global options: `auto_https off`, `admin off` (no Caddy admin API). One site block `:80`
with `encode zstd gzip` (zstd where the browser takes it; decision 0024). Directive order
matters, and Caddy sorts `redir` before `handle`:

1. **Offline-popup redirect** (`Caddyfile:19-23`). Named matcher `@elsewhere` matches when
   *both* of these are true:
   - the host is **not** `{$LAN_IP:127.0.0.1}`, `airplane.mode` or `localhost`;
   - the host is **not** an IPv4 literal (`^[0-9.]+$`) and doesn't contain `:` (an IPv6 literal).

   Such requests get `302 → http://{$LAN_IP}:{$HTTP_PORT}/`. Why: while the offline popup is on,
   dnsmasq answers *every* name with `10.255.255.1` and fw4 DNATs `10.255.255.1:80` to
   `LAN_IP:HTTP_PORT`. A request for `captive.apple.com` or
   `connectivitycheck.gstatic.com` that reaches Caddy is therefore a phone's
   captive-portal check. Redirecting it makes the phone show the landing page as a "sign in
   to network" sheet. Bare IPs pass through, because that's someone typing the box address (or the laptop in dev).
   The matcher can't list the same host twice: Caddy rejects duplicate hosts, which matters
   when `LAN_IP` is `127.0.0.1` in dev (that's why `localhost` is a separate literal).
2. **Apps**, one pair per app (`Caddyfile:26-89`):
   ```
   redir /cinema /cinema/
   handle_path /cinema/* { reverse_proxy cinema:8000 }
   ```
   `handle_path` **strips the prefix**, so the app sees `/`. That's why apps must use
   relative URLs (app contract). Routes exist for darklight, drop, cinema, quickshot, doom, guide, gatechange, passport, wayfarer, couch.
   **Drop is the exception** (decision 0024): its `reverse_proxy` sends `header_up
   X-Sendfile-Type: X-Accel-Redirect`; Drop checks access and answers a file GET with
   `X-Accel-Redirect: /files/<id>` and no body, and `handle_response @accel` serves
   `/srv/drop/files/<id>` (the `drop-data` volume, read-only in the proxy) with `file_server`,
   copying Content-Type, Content-Disposition, CSP, nosniff and Cache-Control from Drop.
   `file_server` does Range, ETag and sendfile. Only `/files/*` values trigger it.
3. **Platform** (`Caddyfile:91-97`): `redir /admin /admin/`. `handle /api/*` and
   `handle /admin/*` → `reverse_proxy admin:8000` **without** stripping, so FastAPI sees
   `/api/...` and `/admin/...`. FastAPI serves `/admin/` from `admin/static/` via
   `StaticFiles(html=True)` (`main.py:712`).
4. **Fallback** `handle { root * /srv/portal; file_server }`: the landing page
   (`index.html`), `apps.json`, `icon.svg`, `apple-touch-icon.png`.

Caddy's `reverse_proxy` sets `X-Forwarded-For` from the real peer. The admin service's
`client_ip()` (`main.py:601`) takes the **last** entry, which is the one Caddy appended. It's used for
the "this device" marker on the devices list.

After editing the `Caddyfile` in dev, restart the proxy
(`docker compose -f compose.yaml -f compose.dev.yaml restart proxy`). It's a single-file bind
mount, so editors that replace the file need a restart for Caddy to see the new inode.

## End-to-end flows

**Landing page load:** browser → Caddy `/` → `portal/index.html` → JS fetches `/api/apps`
(falls back to `/apps.json`) and `/api/status` every 10 s → admin → router (ubus status
calls) + HTTP probe to `PROBE_URL`.

**Admin change (e.g. join a network):** admin panel JS → `POST /api/admin/upstream` with
cookie `am_admin` + header `X-AirplaneMode: 1` → `require_admin` → validate → `router_lock`
→ `OpenWrtRouter.join` → ubus `uci get/add/set/delete` → `uci apply` → OpenWrt reloads
wireless/network/firewall → store remembers network → supervisor grace period set.

**Travel (no uplink):** supervisor tick (every 30 s) → no link → scan the upstream radio →
join the strongest saved network → 45 s grace. After 2 down ticks and no grace →
`set_offline_portal(True)` → dnsmasq wildcard + DNAT rule → phones see the landing page.

**Hotel sign-in:** the box joins the hotel SSID → probe gets a redirect or a 200 page →
`/api/status` says `captive` with `login_url` → landing page shows "Open the sign-in page" →
the user signs in through the box → "I've signed in" calls `/api/status?refresh=1` → `online`.

## Data on disk

| Where | What |
|---|---|
| `.env` (repo root on the box, mode 600, git-ignored) | Copied from `hardware/<device>.env`, plus `ROUTER_USER`/`ROUTER_PASSWORD` (written by `router-setup.sh`) and `ADMIN_PASSWORD` (written by `update.sh`). **Never** root's password. |
| `admin-data` volume → `/data/networks.json` (mode 600) | Admin service settings. Fields: `networks` (saved upstream networks *with keys*), `paused` (auto-join paused), `usage` (data use per network), `hidden_apps`. See [admin-service.md](admin-service.md#store-networksjson). |
| Router `/etc/config/{wireless,network,firewall,dhcp}` | Hotspots, upstream STA, wwan, VPN interface + peer + zone (private key here), pause rules, popup DNS + DNAT. |
| Router `/etc/config/rpcd` + `/usr/share/rpcd/acl.d/airplanemode.json` | The restricted login (password hash) and its ACL. |
| `models` volume | llama.cpp's GGUF cache (`LLAMA_CACHE=/models`). |
| App volumes (`drop-data`, `cinema-data`, `quickshot-data`, `guide-data`, `wayfarer-data`) | Owned by the apps. See their repos. |
| `caddy-data` | Caddy state (unused with auto_https off, kept for completeness). |
| Memory only | Admin sessions (restarting admin signs everyone out), supervisor state, the 10 s status cache, the current data-use session. |

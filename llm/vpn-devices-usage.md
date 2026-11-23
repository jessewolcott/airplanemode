# Home VPN, connected devices, data use, app switches

Decisions 0010 (VPN, devices, data use) and 0013 (app switches, admin session for apps).

## Home VPN (WireGuard)

### Input: paste a standard `.conf`

The user pastes (or loads from a file) a wg-quick style config. `admin/vpn.py`
`parse_wg_conf(text)` validates it **before** anything touches the router
(`PUT /api/admin/vpn`, `main.py:638-644`):

- It must be a string ≤ 8000 chars. Comments (`#...`) are stripped. Every non-empty line is a
  `[Section]` header or `Key = value` inside a section, else "Line N ... isn't 'Key = value'".
  Keys are case-insensitive. Repeated `Address`, `DNS` or `AllowedIPs` lines are concatenated.
- Exactly one `[Interface]` and one `[Peer]`.
- `Address`: comma list, each `ipaddress.ip_interface`, at least one.
- `DNS`: IP entries kept. Non-IP entries (search domains) are silently dropped.
- `MTU`: optional, 1280-1500.
- `AllowedIPs`: default `0.0.0.0/0, ::/0`. Each entry goes through `ip_network(strict=False)`.
- `PersistentKeepalive`: default `25`, digits ≤ 65535.
- `Endpoint`: `host:port` or `[ipv6]:port`. The port is 1-65535. The host is an IP or matches
  `HOST_RE` (a DNS name), so injection like `bad host;rm -rf` is rejected.
- `PrivateKey`, the peer's `PublicKey` and optional `PresharedKey` must base64-decode to 32 bytes.

The result is `{private_key, addresses, dns, mtu, public_key, preshared_key, endpoint_host,
endpoint_port, allowed_ips, keepalive}`. `full_tunnel(allowed_ips)` is true if `0.0.0.0/0`
or `::/0` is present.

### Router layout (`OpenWrtRouter.set_vpn`)

- `network.amvpn`: `proto wireguard`, `private_key`, `addresses`, `metric 5` (below
  Ethernet 10 and Wi-Fi 20), `disabled`, optional `dns`, `mtu`.
- `network.airplanemode_vpn_peer` (type `wireguard_amvpn`): `public_key`, `endpoint_host`,
  `endpoint_port`, `allowed_ips`, `route_allowed_ips 1`, `persistent_keepalive`, optional
  `preshared_key`. OpenWrt's handler adds a host route for the endpoint via the existing uplink,
  so tunnel packets don't loop.
- `firewall.airplanemode_vpn`: zone `vpn`, network `amvpn`, input/forward REJECT, output
  ACCEPT, `masq 1`, `mtu_fix 1`. `firewall.airplanemode_vpn_fwd`: forwarding lan → vpn.
- Pause rules are re-synced so paused devices are also blocked toward `vpn`.

`set_vpn_enabled` toggles `amvpn.disabled`. `remove_vpn` deletes all four sections and the
`_vpn` pause rules.

The private key lives only in the router's uci config. No API response contains it or the PSK
(`vpn_config()` leaves them out; tests assert this).

### Status

`vpn_summary()` (`main.py:324`) combines `vpn_config()` with `vpn_status()` (only when enabled):
- `handshake_age` = now - the latest handshake.
- `connected` = `None` if the status is unknown (`luci.wireguard` unavailable, i.e.
  luci-proto-wireguard not installed). Otherwise true when the handshake is younger than `VPN_FRESH` = 180 s.
- `/api/status` exposes only `{configured, enabled, connected}`. `/api/admin/vpn` exposes the rest.

### Known limits

- **A VPN can't get past a hotel sign-in page.** With a full tunnel, everything goes into the tunnel,
  which can't come up before sign-in. The landing page then says "Waiting for the home VPN". The
  admin panel says to turn the VPN off, sign in, turn it on. Automatic bypass is open follow-up #4.
- While on but not connected, nothing reaches the internet. Nothing leaks either, and that's intended.
- *Untested on hardware:* a real tunnel and handshake reporting, DNS behaviour (dnsmasq may still
  ask the hotel's resolver), whether Docker's own traffic goes through the tunnel.
- `router-setup.sh` installs `wireguard-tools` + `luci-proto-wireguard` (opkg or apk) while online.

## Connected devices and pause

- The list comes from `luci-rpc getDHCPLeases {family: 4}` (needs LuCI). Paused MACs without a
  lease are still listed (`ip: null`). `GET /api/admin/devices` adds `you` by matching the
  client IP from `X-Forwarded-For`.
- Pause = firewall rule(s) `airplanemode_block_<mac>` (dest `wan`) and
  `airplanemode_block_<mac>_vpn` (dest `vpn`, only if the VPN zone exists): `src lan`,
  `src_mac`, `proto all`, `target REJECT`. Only *forwarding* is blocked, not input, so a
  paused device still reaches the box's apps and admin panel.
- Kicking devices off Wi-Fi (hostapd) and per-device data use were rejected (decision 0010).
- The admin panel asks for confirmation before you pause your own device.
- *Untested on hardware:* real leases and real pause behaviour (including with the VPN on).

## Data use

See [supervisor.md](supervisor.md#data-use-counting-count_usage-mainpy180-207) for the counting.
API: `GET /api/admin/usage` returns `{current: session|null, linked, networks: [...]}`.
`DELETE /api/admin/usage` resets. The counters use `network.device status` statistics of the uplink
device, so they cover all devices on the hotspot. Totals are persisted in `networks.json` → `usage`.

## App switches and the admin session for apps

- `portal/apps.json` is bind-mounted read-only at `/config/apps.json`. The admin service reads
  it on every request (no cache), but the bind mount is a single file, so **restart admin after
  editing apps.json** (the compose comment says so).
- The admin hides ids in `store.data["hidden_apps"]` via `PUT /api/admin/apps/{id}`.
- `GET /api/apps` (public) = the installed apps minus the hidden ones. Used by the landing page menu and by
  Drop's "Play in ..." (the `opens` field). A hidden app keeps running and its URL still works.
  Stopping it would need Docker access, which the admin service deliberately lacks.
- `GET /api/admin/session`: apps learn whether the current user is the admin by forwarding the
  browser's `am_admin` cookie to `http://admin:8000/api/admin/session` (200 or 401). Drop
  caches the answer for 30 s. Uses: Drop's admin-only "downloads off", QuickShot deleting any
  comment. Apps must forward **only** that cookie, after checking its format (app contract).

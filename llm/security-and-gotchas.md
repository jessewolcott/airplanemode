# Security model and known gotchas

## Trust boundaries

| Boundary | Control |
|---|---|
| Hotel/upstream network → box | Caddy's port is published **only on `LAN_IP`** (Docker's rules can bypass OpenWrt's firewall, so binding 0.0.0.0 would be exposed). Apps and the LLM aren't published at all. *Verify on hardware:* `http://<upstream-ip>:8080` must fail from the hotel side. |
| Hotspot clients → box apps | The hotspot password is the access control. The landing page, `/api/status`, `/api/apps` and the apps are open to anyone on the LAN. |
| Hotspot clients → admin | `ADMIN_PASSWORD` (random 16 chars from update.sh, or `dev` in dev). Session cookie `am_admin`: random 32-byte token, `HttpOnly`, `SameSite=Strict`, 12 h, in memory only. Login checks run one at a time with a 1.5 s delay on failure (throttling). `hmac.compare_digest`. |
| CSRF | Every non-GET admin request needs `X-AirplaneMode: 1`, which a cross-site form can't set. Plus SameSite=Strict. |
| Apps → admin identity | Apps receive the browser's `am_admin` cookie (same origin). They may only forward it to `/api/admin/session` (200/401) and must check its format first. An app can't use the cookie for anything else unless it's malicious. Apps are trusted code pinned by version. |
| Admin service → router | rpcd login `airplanemode` with the ACL in `router/`: uci on wireless/network/firewall/dhcp + a few status reads. No `file`, `system`, `rc`, other configs, or key generation. The password is random, 24 chars, stored on the router as a hash, and in `.env` (mode 600). **Root's password is never stored.** Remaining risk: write access to `network`/`firewall` is per-config, not per-section, so a compromised admin could rearrange interfaces/zones (decision 0008, open follow-up #5). |
| Admin service → host | Unprivileged container: no host network, no Docker socket, no config mounts. So it can't stop hidden apps (decision 0013). |
| Secrets in API responses | Never returned: the upstream Wi-Fi key (OpenWrt `overview` omits it; `Store.public()` → `has_key`), the VPN private/preshared keys. `/api/status` has no keys or VPN endpoint. Hotspot keys **are** returned to the signed-in admin (they show in the panel). |
| Secrets at rest | `.env` (600). `networks.json` (600) holds saved upstream keys in the clear, the same way OpenWrt stores Wi-Fi keys. The VPN private key is only in the router's uci. |
| Input | Server-side validation before any write: SSID, WPA key, MAC (unicast), WireGuard config (keys base64-32, endpoint host regex so no injection, CIDRs, port, MTU). Front-ends escape every interpolated value (`esc()`). |
| Lockout | The hotspot lockout guard. No `uci apply` rollback (decision 0006). |
| Transport | Plain HTTP on the LAN (no public name for a cert, offline). Decision 0004. |
| DNS rebind protection | **Off** by default (hotel sign-in pages need it). That weakens protection against malicious sites reaching LAN devices by name. `KEEP_REBIND_PROTECTION=1` keeps it on. |
| Mock endpoints | `/api/mock/*` are unauthenticated but only exist when `ROUTER_BACKEND=mock`. |
| API docs | FastAPI docs/openapi are disabled. |

## Gotchas already learned

- **netifd hangs in an unprivileged OpenWrt container** (procd jails fail). Live link status,
  `network reload` and `dnsmasq restart` time out there. That's not a code bug. Status calls use 5 s
  timeouts, and `router-setup.sh` wraps restarts in a hand-rolled 60 s timeout (busybox may lack
  `timeout`).
- **`uci commit` is refused for ubus sessions.** Use `uci apply {"rollback": false}`. Status 5
  ("no data") from apply just means nothing was staged.
- **One shared ubus session.** `uci apply` applies everything staged in it, hence `router_lock`.
- **fw4 DNAT to a port needs a protocol + port match** (`proto tcp`, `src_dport 80`), or nftables
  rejects it.
- **fw4 refuses the whole ruleset if any rule names a missing zone.** Remove `_vpn` pause rules
  with the VPN zone, and only add them while it exists.
- **Caddy rejects duplicate hosts in a `host` matcher.** That's why `localhost` and
  `{$LAN_IP:127.0.0.1}` are separate, and dev's `LAN_IP=127.0.0.1` mustn't be repeated.
- **Docker containers on OpenWrt can't use the host resolver at 127.0.0.1**, so they fall back to public DNS,
  which hotels block before sign-in, and the probe says "offline". Hence `dns: LAN_IP` on admin.
- **DNS rebind protection breaks hotel sign-in pages** (they resolve to private IPs).
- **While the offline popup is on, every name resolves to 10.255.255.1**, so `/api/status`
  skips the probe when there's no uplink.
- **Caddyfile and apps.json are single-file bind mounts.** Restart `proxy` after editing the
  Caddyfile and `admin` after editing apps.json.
- **The mock router reconnects** to the configured network when it's back in range (like real STA).
  Auto-join switching between networks is covered by the tests, not the UI demo.
- **Windows dev machine:** port 8000 is reserved (use 8080/8765). git `autocrlf` is on, and
  `.gitattributes` forces LF for `*.sh`, `*.env`, `*.yaml` and `Caddyfile` (they run on Linux). Keep it.
  H: is exFAT, so `git config --global --add safe.directory H:/Github/airplanemode/<repo>` is needed.
  Moving folders while VS Code has them open can half-fail.
- **Docker Desktop won't start** with a stale `%LOCALAPPDATA%\Docker\run\userAnalyticsOtlpHttp.sock`:
  quit and rename that `run` folder.
- **OpenWrt 24.10 uclient-fetch fails TLS to GitHub**, so the installer uses curl.
- **GL.iNet's repeater manager** fights with AirplaneMode over the STA config. Use one.
- **Single Wi-Fi adapter:** STA + AP share a channel, and scanning briefly disrupts the hotspot (that only
  happens with no uplink).
- **A VPN can't pass a captive portal.** The user must toggle the VPN off to sign in.
- Python 3.10 locally vs 3.12 in containers. Avoid 3.11+-only syntax in admin code if you
  want local tests to run (the current code uses `str | None` in pydantic models, which works on 3.10).

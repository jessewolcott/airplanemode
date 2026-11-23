# Supervisor, captive-portal probing and the offline popup

Decision 0009 (supervisor) and 0007 (captive portal). Code: `admin/main.py:175-369`.

## The loop

`lifespan` starts `_supervise_forever()` unless `SUPERVISE_INTERVAL <= 0` (`main.py:93`).
It sleeps 3 s, then repeats forever: `await supervisor.tick()`, catch and log any exception,
sleep `SUPERVISE_INTERVAL` (30 s in production, 5 s in dev). Ticks are therefore at least 30 s
apart plus however long the tick takes.

`tick(now=None)` takes `router_lock` for the whole tick, then `_tick(now)`. `now` defaults
to `time.monotonic()`. The tests pass fake times (`tick(0)`, `tick(30)`, ...).

## State (`Supervisor.__init__`)

| Field | Meaning |
|---|---|
| `joining` | The SSID we just asked the router to join, while waiting for it to come up |
| `grace_until` | Monotonic time until which we wait for that join (`JOIN_GRACE`, 45 s) |
| `fails` | `ssid → (count, since)`: failed joins |
| `down_ticks` | Consecutive ticks with no uplink |
| `last` | Last tick summary `{linked, ethernet, wifi, action, joining}` (shown in the admin panel) |
| `counters` | Last byte counters per uplink (`ethernet`/`wifi`) |
| `session` | Data used on the current primary uplink: `{name, rx, tx, since}` |

## One tick (`_tick`, `main.py:260-288`)

```
eth  = router.ethernet_status()        # network.interface status wan
wifi = router.upstream_status()        # network.interface status wwan (+ iwinfo info)
linked = eth.connected or wifi.connected

if linked:
    down_ticks = 0
    if wifi.connected and joining: fails.pop(joining)    # the join worked
    joining, grace_until = None, 0
    if OFFLINE_PORTAL and set_offline_portal(False): action = "offline popup off"
else:
    down_ticks += 1
    action = _autojoin(now)
    if OFFLINE_PORTAL and down_ticks >= PORTAL_AFTER (2) and now >= grace_until:
        if set_offline_portal(True): action += "offline popup on"

if linked: count_usage(wifi)
elif counters: counters, session = {}, None

last = {...}; if action: log + _bust()
```

Notes:
- "Linked" means a **link** (the interface is up), not "internet works". A connected-but-captive
  hotel network counts as linked. That's the point: the popup must never cover a real sign-in page.
- `set_offline_portal(False)` runs on every linked tick. It's idempotent (reads dhcp+firewall
  and applies only if something changed).
- Ethernet doesn't stop the Wi-Fi STA. Both can be up, and route metrics pick Ethernet.
  Auto-join only runs with **no** uplink, so with a cable in, the box doesn't go hunting.

## Auto-join (`_autojoin`, `main.py:227-254`)

Returns a short action string (or `None`):

1. `None` if `AUTOJOIN` is off, `store.paused` is set, or there are no saved networks.
2. `now < grace_until` → `"waiting for <ssid>"` (don't disturb a join in progress).
3. If `joining` is still set, the last attempt didn't come up in time:
   `fails[ssid] = (count+1, since)`, log it, clear `joining`.
4. `router.overview()`. Radios = those not disabled. None → `"no radios"`.
5. Radio choice: the current upstream's radio if enabled, else the most recently used saved
   network's radio, else the first enabled radio.
6. `router.scan(radio)` → visible SSIDs.
7. Candidates = saved networks visible and not `_skippable` (≥ `MAX_TRIES`=2 failures
   within `RETRY_AFTER`=600 s from the first failure. Once 600 s pass the entry is
   cleared). None → `"no saved network in range"`.
8. Best = max by (signal, last_used). `router.join(radio, ssid, encryption, key, macaddr)`.
   `joining = ssid`, `grace_until = now + JOIN_GRACE`, bust the status cache, return
   `"joining <ssid>"`.

Unsaved networks are never auto-joined. A manual Connect (`POST /api/admin/upstream`) sets
`joining`/`grace_until` itself and clears that SSID's failures. A manual Disconnect sets
`store.paused` (auto-join stays off until Connect or Resume via `POST /api/admin/autojoin`).

### State machine summary

```
            ┌─────────── linked (eth or wifi up) ───────────┐
            │  popup off, joining cleared, down_ticks = 0    │
            ▼                                                │
   [LINKED] ──link lost──▶ [DOWN, tick n]                    │
                              │                              │
              saved net visible & not skipped?               │
                 yes ──▶ join ──▶ [JOINING: grace 45 s] ──link up──┘
                 │                     │ grace expired, no link
                 │                     ▼
                 │          fails[ssid] += 1 (2 fails → skip 10 min), try next best
                 no ──▶ "no saved network in range"
   Popup: on once down_ticks ≥ 2 and not inside a grace period; off on any link.
```

Timing: with 30 s ticks, the popup turns on after the 2nd down tick (~30-60 s after the
link drops), and later than that if joins are being tried. The test
`test_offline_popup_waits_during_join` checks that: two failed joins of a wrong-key network,
then the popup.

## Captive-portal probe (`probe()`, `main.py:312-326`)

- Mock: `router.internet()`, `login_url="/api/mock/portal"` if captive.
- Real: `GET PROBE_URL` (default `http://connectivitycheck.gstatic.com/generate_204`),
  `follow_redirects=False`, 5 s timeout, through the admin container's DNS (`LAN_IP`, i.e.
  the router's dnsmasq):
  - exception → `("offline", None)`;
  - `204` → `("online", None)`;
  - 301/302/303/307/308 → `("captive", Location or PROBE_URL)`;
  - anything else (e.g. 200 with the hotel's page) → `("captive", PROBE_URL)`. Opening the probe
    URL in the user's browser then lands on the venue's page.

## `/api/status`

`main.py:343-369`. It recomputes when `?refresh=1`, empty, or older than 10 s:

1. `supervisor.links()` (ethernet + upstream status). On a `RouterError` both are
   `{"connected": None}`.
2. With a link, *or* when status is unknown (`eth.connected is None`), it probes. With **no uplink at
   all** it skips the probe and says `offline`. While the popup is on, dnsmasq answers every name
   with `10.255.255.1`, so a probe would loop back to Caddy.
3. `via` = `ethernet` if eth is connected, else `wifi` if wifi is connected, else `null`.
4. `vpn_summary()` (errors give `{"configured": false}`). Only `configured, enabled, connected`
   are exposed.
5. Response:

```jsonc
{"vpn": {"configured": true, "enabled": true, "connected": false},
 "internet": "online" | "captive" | "offline",
 "login_url": "http://..." | null,
 "via": "ethernet" | "wifi" | null,
 "upstream": {"ssid": "...", "connected": true, "signal": -61},
 "ethernet": {"connected": false},
 "joining": "CoffeeCorner" | null,
 "saved_networks": 3,
 "autojoin": true,              // AUTOJOIN and not paused
 "backend": "openwrt" | "mock"}
```

Public and unauthenticated. It never contains keys, the VPN endpoint or the public key.

## Offline popup, end to end

Goal: with no internet source at all, a phone joining the hotspot shows the AirplaneMode
landing page as a "sign in to network" sheet. People find the apps and the *Get online*
button without knowing the address.

1. Supervisor: 2 down ticks, no grace → `router.set_offline_portal(True)`.
2. Router: dnsmasq `address=/#/10.255.255.1` (every name resolves to a fake address; more
   specific entries like `/airplane.mode/LAN_IP` still win). fw4 redirect
   `airplanemode_portal`: from lan, tcp, dst 10.255.255.1 port 80 → DNAT `LAN_IP:HTTP_PORT`.
   On real OpenWrt 24.10 this generated `ip daddr 10.255.255.1 tcp dport 80 dnat 192.168.2.1:8080`
   in `dstnat_lan`, and `nft -c` accepted it.
3. The phone's captive check (`http://captive.apple.com/...`) → 10.255.255.1:80 → Caddy.
   The host isn't a box name or IP → `302 http://LAN_IP:8080/` → the landing page. The phone
   shows it as a sign-in screen.
4. Any uplink → the next tick calls `set_offline_portal(False)`: removes the DNS entry, sets the
   rule `enabled 0`.

Only plain-HTTP (port 80) checks are caught. That's what iOS, Android, Windows and macOS
use. *Untested on real phones/hardware* (see the `docs/testing.md` checklist, including
checking `fw4 print` for `jump dstnat_lan`).

The admin panel's `GET /api/admin/wifi` reports `offline_popup` (from `portal_mode()`).

## Data-use counting (`count_usage`, `main.py:185-212`)

Runs at the end of each **linked** tick:
1. `router.uplink_counters()` gives `{"ethernet": {rx, tx}, "wifi": {rx, tx}}` for the uplinks that are up.
2. Counters for uplinks that disappeared are dropped (the link went down, so start over).
3. Each uplink's name: `"Ethernet"`, or the wifi SSID (from `upstream_status`), falling back to
   the current session's name.
4. The first sighting is only a baseline. Later the delta is added, and if a counter went backwards
   (interface recreated) the new value itself is counted. `store.add_usage(name, rx, tx)`.
5. **Primary** uplink = Ethernet if present, else wifi. Its deltas go into `session`. A new
   session starts when the primary name changes.
6. No uplinks → `session = None`. `store.save_soon()` (≤ every 5 min).

On a down tick with counters present, both `counters` and `session` reset. Counting only
happens while the admin service runs, so it's approximate around restarts. *Byte counts on real
interfaces are untested.*

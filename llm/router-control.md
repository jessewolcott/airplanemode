# Router control (`admin/router.py`) and the restricted login

## Why ubus (decisions 0006, 0008)

The admin container is unprivileged: no host network, no `/etc/config` mount, no Docker
socket. It changes the router through **OpenWrt's ubus over HTTP**, the JSON-RPC endpoint
that uhttpd serves at `/ubus` and that LuCI itself uses. It logs in as the rpcd user
`airplanemode`, whose ACL allows only Wi-Fi-related configs. A bug in the admin service
can therefore rearrange Wi-Fi/network/firewall/DHCP config (still broad, see decision 0008's
consequences), but it can't run commands, read files, or touch system, SSH or passwords.

## Two backends, one interface

`main.py:72-78` picks `MockRouter()` when `ROUTER_BACKEND=mock`, else
`OpenWrtRouter(ROUTER_URL, ROUTER_USER, ROUTER_PASSWORD, client, lan_ip, http_port, wan_iface)`.
Both implement the same async methods:

`overview, upstream_status, ethernet_status, portal_mode, set_offline_portal, scan, join,
leave, save_hotspot, add_hotspot, delete_hotspot, devices, set_device_paused,
uplink_counters, vpn_config, vpn_status, set_vpn, set_vpn_enabled, remove_vpn`, plus `name`
(`"openwrt"`/`"mock"`). The mock adds `internet()` and `clear_portal()`.

Terms: **hotspot** = `wifi-iface` with `mode ap` (your network). **Upstream** = the one
`wifi-iface` with `mode sta` that the service manages (the hotel network).

## JSON-RPC client (`router.py:140-203`)

- `_rpc(sid, obj, method, args, timeout=20)` POSTs
  `{"jsonrpc":"2.0","id":1,"method":"call","params":[sid, obj, method, args]}`.
  - Network errors, HTTP errors or bad JSON raise `UbusError("Can't reach the router's API at ...")`.
  - A JSON-RPC `error` raises `UbusError("Router API refused obj.method: msg", code)`.
  - A `result[0] != 0` (ubus status) raises `UbusError(..., status)`. Otherwise it returns `result[1]` or `{}`.
- `login()` calls `session.login` with the anonymous sid `"0"*32` and
  `{username, password, timeout: 3600}`. Status 6 means "The router rejected ROUTER_USER /
  ROUTER_PASSWORD in .env.". A missing password raises before any call.
- `call(obj, method, args, timeout)` logs in lazily. On code `-32002` (access denied /
  expired session) or `6` (permission denied) it logs in again and retries once.
- `_uci(config, **kw)` is `uci get {config}` and returns `values` (dict section → options; each
  section has `.type`, `.name`, ...). Values are strings, or lists for list options. Use
  `as_list()` (`router.py:95`) to normalise.
- `_apply(*configs)` is `uci apply {"rollback": false}`. Status 5 ("no data", nothing staged) is
  ignored. The `configs` args are documentation only: apply covers everything staged in the
  session. **Never `uci commit`**: ubus sessions are not allowed to commit (found on real
  OpenWrt). `apply` commits the session's staged changes and reloads the affected services.
  Rollback is off on purpose. LuCI-style "confirm within N seconds" would revert a hotspot
  password change before the user could reconnect, and the lockout guard covers the worst case
  (decision 0006). *Untested on hardware:* whether apply really reloads the radios (the
  checklist item suggests adding `network reload` to `_apply()` if not).

Short timeouts (5 s) are used on the status calls the landing page waits on.

## Sections the service owns (don't touch anything else)

Constants at `router.py:16-43`:

| Config | Section (name) | Type | Purpose |
|---|---|---|---|
| wireless | `airplanemode_up` (`UPSTREAM_SECTION`) | wifi-iface, `mode sta` | Upstream client |
| network | `wwan` (`UPSTREAM_NET`) | interface, `proto dhcp`, `metric 20` | Upstream's L3 interface. Added to the existing firewall zone named `wan` |
| network | `wan` (`WAN_IFACE`) | (existing) | Only read, plus metric 10 set by `router-setup.sh` |
| firewall | `airplanemode_portal` (`PORTAL_RULE`) | redirect (DNAT) | Offline popup |
| dhcp | first `dnsmasq` section, list `address` entry `/#/10.255.255.1` (`PORTAL_DNS`) | list item | Offline popup wildcard DNS |
| network | `amvpn` (`VPN_NET`) | interface `proto wireguard`, `metric 5` | Home VPN |
| network | `airplanemode_vpn_peer` (`VPN_PEER`) | `wireguard_amvpn` | VPN peer |
| firewall | `airplanemode_vpn` (`VPN_ZONE`), zone name `vpn` | zone | VPN zone (masq, mtu_fix) |
| firewall | `airplanemode_vpn_fwd` (`VPN_FWD`) | forwarding lan → vpn | |
| firewall | `airplanemode_block_<mac-no-colons>` and `..._vpn` (`BLOCK_PREFIX`) | rule REJECT | Device pause (`block_rule_name()`, `router.py:102`) |
| wireless | existing AP `wifi-iface`s (e.g. `default_radio0`) + new anonymous ones | wifi-iface `mode ap` | Hotspots (the user owns them; the service edits ssid/encryption/key/disabled) |
| dhcp | `/airplane.mode/<LAN_IP>` address entry | list item | Set by `router-setup.sh`, preserved by popup on/off |

**Rule:** a firewall rule must never reference a zone that doesn't exist. fw4 then refuses
the *entire* ruleset. That's why pause rules for `vpn` are created only when the VPN zone
exists, and are removed with the VPN.

## Validation helpers (`router.py:51-92`)

- `check_ssid`: a non-empty string, ≤ 32 bytes UTF-8, no control chars (< 0x20).
- `check_key(encryption, key, allowed)`: the encryption must be in `allowed`. `none` returns `None`.
  Otherwise 64 hex chars (raw PSK) or 8-63 printable ASCII (0x20-0x7E).
- `check_mac`: empty returns `None`. It lowercases and turns `-` into `:`, matches `aa:bb:cc:dd:ee:ff`, and
  refuses multicast (low bit of the first octet set).
- `guard_last_hotspot(overview, removing_id)`: raises unless some *other* hotspot is
  enabled **and on a radio that isn't disabled**. A hotspot on a switched-off radio doesn't count.
- `scan_security(enc)` maps an iwinfo scan's `encryption` object: not enabled → `none`.
  `802.1x` → `None` (unsupported). sae+psk → `sae-mixed`. sae → `sae`. wpa {1} → `psk`.
  wpa {1,2} → `psk-mixed`. Anything else → `psk2`.
- `band_of(radio)`: `band` 2g/5g/6g/60g gives a label. Otherwise it's inferred from the legacy `hwmode`.

## OpenWrt backend, method by method

| Method | ubus / uci calls |
|---|---|
| `overview()` | `uci get wireless`. Radios = `wifi-device` sections (`id, band, channel, disabled`). Hotspots = `wifi-iface` with `mode ap` (`id, radio, ssid, encryption, key, disabled, network`). Upstream = `airplanemode_up` (`radio, ssid, encryption, macaddr, disabled`, **no key**) |
| `upstream_status()` | `network.interface status {interface: wwan}` (5 s). On error it returns `{configured: false, connected: false}`. `connected = up`, `ip` = first IPv4. Then `iwinfo info {device: l3_device or device}` for `ssid, signal` (errors ignored) |
| `ethernet_status()` | `network.interface status {interface: WAN_IFACE}` (5 s) gives `{present, connected, ip}`. On error `{present: false, connected: false}` |
| `portal_mode()` | `uci get dhcp`. True if the first dnsmasq section's `address` contains `/#/10.255.255.1` |
| `set_offline_portal(on)` | Reads dhcp and computes the address list without/with the portal entry. If it differs: `uci set dhcp.<dnsmasq> address=[...]`, or `uci delete ... option address` when the list becomes empty. Reads firewall. The desired redirect is `{name "AirplaneMode offline popup", src lan, proto tcp, src_dip 10.255.255.1, src_dport 80, dest_ip LAN_IP, dest_port HTTP_PORT, target DNAT, enabled 1/0}`. The rule is created (`uci add firewall redirect name=airplanemode_portal`) only when turning on. Later it's toggled with `enabled`, never deleted. It applies only if something changed and **returns whether anything changed** (idempotent). `proto tcp` + `src_dport 80` are required: fw4/nftables can't DNAT to a port without a protocol+port match |
| `scan(radio)` | `iwinfo scan {device: radio}` (20 s). Status 4/5 means "radio didn't answer a scan. Is its Wi-Fi adapter plugged in". Results are deduplicated by SSID, keeping the strongest, sorted by signal, with hidden SSIDs dropped |
| `join(radio, ssid, enc, key, mac)` | Validates again. `uci get wireless` (the radio must be a `wifi-device`). `uci get network`: adds `wwan` (`proto dhcp`, `metric 20`) or fixes its metric. `uci get firewall`: appends `wwan` to the `wan` zone's `network` list if missing (no duplicates). Then sets/adds `airplanemode_up` = `{device, mode sta, network wwan, ssid, encryption, disabled 0, key?, macaddr?}` and deletes stale `key`/`macaddr` options when not given. `uci apply` |
| `leave()` | `uci set wireless.airplanemode_up disabled=1`, apply (only if the section exists) |
| `save_hotspot(hid, ssid, enc, key, disabled)` | Validates. The section must be a `wifi-iface` with `mode ap`. Runs the lockout guard if disabling. Then `uci set {ssid, encryption, disabled, key?}`, deletes `key` if none, applies |
| `add_hotspot(radio, ssid, enc, key)` | Validates, the radio must exist. `uci add wireless wifi-iface {device, mode ap, network lan, ssid, encryption, key?}` (anonymous section) and applies. Returns the new section name |
| `delete_hotspot(hid)` | The section must have `mode ap`. Lockout guard, `uci delete`, apply |
| `devices()` | `luci-rpc getDHCPLeases {family: 4}` (5 s). Failure means "luci-rpc API isn't available (is LuCI installed?)". Paused MACs come from firewall sections with `BLOCK_PREFIX` and `src_mac`. The result merges leases + paused-without-lease, sorted named-first then by name/MAC. Fields `mac, ip, name, expires, paused` |
| `set_device_paused(mac, paused)` | `check_mac`. Computes the wanted set and returns if unchanged. `_sync_blocks(want, vpn_zone_exists)`, apply firewall |
| `_sync_blocks(paused, vpn_zone)` | Wants one rule per MAC per zone (`wan`, plus `vpn` if the zone exists): `{name "AirplaneMode pause <mac>", src lan, dest <zone>, src_mac, proto all, target REJECT}`. Deletes `airplanemode_block_*` sections not wanted, then creates/updates the wanted ones via `_put` |
| `uplink_counters()` | For `ethernet`→`WAN_IFACE` and `wifi`→`wwan`: `network.interface status`. If up with a device: `network.device status {name}` → `statistics.rx_bytes/tx_bytes`. Returns only the uplinks that are up |
| `vpn_config()` | `uci get network`. `None` unless both `amvpn` and `airplanemode_vpn_peer` exist. Returns `{enabled, addresses, dns, endpoint "host:port", public_key, allowed_ips}` (no private/preshared key) |
| `vpn_status()` | `luci.wireguard getWgInstances` (5 s). An error gives `{up: None}` (unknown: luci-proto-wireguard missing). No `amvpn` instance gives `{up: false}`. Otherwise `{up: true, handshake (epoch or None), rx, tx}` from the first peer |
| `set_vpn(cfg, enabled)` | `_put network amvpn interface {proto wireguard, private_key, addresses, metric 5, disabled, dns?, mtu?}` (drops stale dns/mtu). `_put network airplanemode_vpn_peer wireguard_amvpn {description, public_key, endpoint_host, endpoint_port, allowed_ips, route_allowed_ips 1, persistent_keepalive, preshared_key?}` (drops a stale psk). `_put firewall airplanemode_vpn zone {name vpn, network [amvpn], input REJECT, output ACCEPT, forward REJECT, masq 1, mtu_fix 1}`. `_put firewall airplanemode_vpn_fwd forwarding {src lan, dest vpn}`. `_sync_blocks(paused, True)` (adds `_vpn` pause rules). Apply |
| `set_vpn_enabled(enabled)` | `amvpn` must exist. `uci set disabled 0/1`, apply |
| `remove_vpn()` | Deletes peer + interface. Deletes forwarding + zone. `_sync_blocks(paused, False)` (drops the `_vpn` rules so none points at the missing zone). Apply |

Helpers: `_put(config, name, type, values, existing, drop=())` creates a named section with
`uci add ... name=` or `uci set`s it, deleting `drop` options not in `values`.
`_delete_sections(config, names, existing)`.

## MockRouter (`router.py:548-737`)

An in-memory router for laptops, demos and tests:
- Radios `radio0` (2.4 GHz) and `radio1` (5 GHz). Hotspots `default_radio1` "AirplaneMode"
  (sae-mixed) and `default_radio0` "AirplaneMode-2G" (psk2), key `change-me-please`.
- `NEARBY` networks: `Harbor_Hotel_Guest` (open, captive portal, -48), `Harbor_Hotel_Staff`
  (sae-mixed, password `staffonly123`, -61), `Gate 14 Free WiFi` (open, portal, -72),
  `CoffeeCorner` (psk2, `latte2024`, -80). `in_range` controls what's visible.
- `upstream_status()` reports connected only if the SSID is in range, not disabled and the key
  matches. It **reconnects automatically** when the network comes back in range, like real
  OpenWrt STA.
- `internet()`: Ethernet → `online`. Otherwise the upstream state. A portal network not yet
  `clear_portal()`ed → `captive`, or `offline` if a VPN is on (a full tunnel can't pass a
  sign-in page).
- `uplink_counters()` adds 3 MB/0.4 MB (ethernet) and 1.5 MB/0.2 MB (wifi) per call.
- Four fake DHCP devices (iPhone, MacBook-Air, Pixel-8, unnamed). `vpn_status()` gives a
  handshake 20 s ago when the internet is online.
- Hotspot ids for new hotspots: `cfg01`, `cfg02`, ...
- The mock's `overview()` strips `_`-prefixed upstream fields (`_key`), mirroring OpenWrt's
  "never hand out the upstream key".

## The rpcd ACL (`router/rpcd-acl-airplanemode.json`)

Installed to `/usr/share/rpcd/acl.d/airplanemode.json` by `router-setup.sh`, group name
`airplanemode`:

| | ubus objects/methods | uci configs |
|---|---|---|
| read | `uci`: get, changes · `iwinfo`: info, scan, devices · `network.interface`: status, dump · `network.device`: status · `luci-rpc`: getDHCPLeases · `luci.wireguard`: getWgInstances | wireless, network, firewall, dhcp |
| write | `uci`: set, add, delete, apply, revert | wireless, network, firewall, dhcp |

Not every allowed call is used today (`uci changes`/`revert`, `iwinfo devices`,
`network.interface dump` are spare). Everything else is denied, including `file`, `system`,
`rc`, other uci configs (`system`, `rpcd`, `dropbear`), `luci.wireguard generateKeyPair`,
`network.device set_state`, `luci-rpc getBoardJSON`. The live test checks 11 such refusals.

`session.login` with the anonymous sid is always allowed by rpcd itself.

The rpcd login section (`/etc/config/rpcd`, `config login`) gets `username airplanemode`,
`password <uhttpd -m hash>`, `list read airplanemode`, `list write airplanemode`.
See [deployment.md](deployment.md#scriptsrouter-setupsh) for the script.

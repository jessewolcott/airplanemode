"""Router control: read and change Wi-Fi settings on the host router.

Two backends with the same interface:
  - OpenWrt: talks to the router's own ubus JSON-RPC API (the same API LuCI uses), so the
    container needs no host privileges. Works on FriendlyWrt (NanoPi R6S) and other OpenWrt builds.
  - Mock: in-memory router for laptops and demos, including a fake hotel captive portal.

Terms used here:
  hotspot  = an access point the box broadcasts for your devices (wifi-iface, mode "ap")
  upstream = the Wi-Fi network the box joins to reach the internet (wifi-iface, mode "sta")
"""
import copy
import re
import time

UPSTREAM_SECTION = "airplanemode_up"   # the one STA interface we manage
UPSTREAM_NET = "wwan"                  # its network interface, placed in the firewall's wan zone
WWAN_METRIC = "20"                     # Ethernet WAN gets 10 (router-setup.sh), so a cable wins

# Offline popup: when there is no upstream link at all, every DNS name answers with a fake
# address, and a firewall rule sends that address to the landing page. Phones then show the
# landing page as a "sign in to network" screen. Removed as soon as any uplink comes back.
PORTAL_IP = "10.255.255.1"
PORTAL_DNS = f"/#/{PORTAL_IP}"
PORTAL_RULE = "airplanemode_portal"

# Home VPN (WireGuard). The tunnel gets a lower route metric than both uplinks, so with
# AllowedIPs = 0.0.0.0/0 everything from the hotspot goes through it.
VPN_NET = "amvpn"
VPN_PEER = "airplanemode_vpn_peer"
VPN_ZONE = "airplanemode_vpn"          # firewall zone section; the zone itself is named "vpn"
VPN_ZONE_NAME = "vpn"
VPN_FWD = "airplanemode_vpn_fwd"
VPN_METRIC = "5"
VPN_FRESH = 180                        # seconds: a handshake newer than this means the tunnel works

# Paused devices: one firewall rule per device and outbound zone, so a paused device loses
# the internet but still reaches the box's own apps.
BLOCK_PREFIX = "airplanemode_block_"

HOTSPOT_SECURITY =["sae-mixed", "psk2", "sae", "none"]
UPSTREAM_SECURITY = ["none", "psk", "psk2", "psk-mixed", "sae", "sae-mixed"]
MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")


class RouterError(Exception):
    pass


# ---------------- validation (shared) ----------------
def check_ssid(ssid):
    if not isinstance(ssid, str) or not ssid.strip():
        raise RouterError("Network name can't be empty.")
    if len(ssid.encode("utf-8")) > 32:
        raise RouterError("Network name must be 32 bytes or fewer.")
    if any(ord(ch) < 32 for ch in ssid):
        raise RouterError("Network name can't contain control characters.")
    return ssid


def check_key(encryption, key, allowed):
    if encryption not in allowed:
        raise RouterError(f"Unsupported security type: {encryption}.")
    if encryption == "none":
        return None
    key = key or ""
    if re.fullmatch(r"[0-9a-fA-F]{64}", key):
        return key
    if not (8 <= len(key) <= 63) or any(not (32 <= ord(ch) <= 126) for ch in key):
        raise RouterError("Wi-Fi password must be 8 to 63 ordinary characters (letters, digits, symbols).")
    return key


def check_mac(mac):
    if not mac:
        return None
    mac = mac.strip().lower().replace("-", ":")
    if not MAC_RE.match(mac):
        raise RouterError("MAC address must look like aa:bb:cc:dd:ee:ff.")
    if int(mac[:2], 16) & 1:
        raise RouterError("That's a multicast MAC address; use the address of a real device.")
    return mac


def guard_last_hotspot(overview, removing_id):
    """Refuse to turn off or remove the last hotspot that's actually broadcasting.
    A hotspot on a switched-off radio doesn't count: nobody can connect to it."""
    radios_on = {r["id"] for r in overview["radios"] if not r["disabled"]}
    others = [h for h in overview["hotspots"]
              if h["id"] != removing_id and not h["disabled"] and h["radio"] in radios_on]
    if not others:
        raise RouterError("That's the only hotspot that's on. Turning it off would lock everyone out; add another first.")


def as_list(v):
    """uci values come back as a string or a list."""
    if not v:
        return []
    return v.split() if isinstance(v, str) else list(v)


def block_rule_name(mac, zone):
    return BLOCK_PREFIX + mac.replace(":", "") + ("" if zone == "wan" else "_" + zone)


def scan_security(enc):
    """Map an iwinfo scan 'encryption' object to an OpenWrt encryption value (None = unsupported)."""
    if not enc or not enc.get("enabled"):
        return "none"
    auth = set(enc.get("authentication") or [])
    wpa = set(enc.get("wpa") or [])
    if "802.1x" in auth:
        return None  # enterprise networks need certificates/usernames: not supported here
    if "sae" in auth and "psk" in auth:
        return "sae-mixed"
    if "sae" in auth:
        return "sae"
    if wpa == {1}:
        return "psk"
    if 1 in wpa and 2 in wpa:
        return "psk-mixed"
    return "psk2"


def band_of(radio):
    band = radio.get("band")
    if band:
        return {"2g": "2.4 GHz", "5g": "5 GHz", "6g": "6 GHz", "60g": "60 GHz"}.get(band, band)
    hw = radio.get("hwmode", "")
    return "5 GHz" if hw.endswith("a") else "2.4 GHz" if hw else "?"


# ---------------- OpenWrt (ubus JSON-RPC) ----------------
class UbusError(RouterError):
    def __init__(self, msg, code=None):
        super().__init__(msg)
        self.code = code


class OpenWrtRouter:
    name = "openwrt"
    ANON = "0" * 32

    def __init__(self, url, username, password, client, lan_ip="192.168.2.1", http_port="8080", wan_iface="wan"):
        self.url = url
        self.username = username
        self.password = password
        self.client = client
        self.lan_ip = lan_ip
        self.http_port = str(http_port)
        self.wan_iface = wan_iface
        self.sid = None

    async def _rpc(self, sid, obj, method, args, timeout=20):
        body = {"jsonrpc": "2.0", "id": 1, "method": "call", "params": [sid, obj, method, args]}
        try:
            r = await self.client.post(self.url, json=body, timeout=timeout)
            r.raise_for_status()
            data = r.json()
        except Exception as e:  # network trouble, bad JSON, HTTP errors
            raise UbusError(f"Can't reach the router's API at {self.url} ({type(e).__name__}).") from e
        if "error" in data:
            raise UbusError(f"Router API refused {obj}.{method}: {data['error'].get('message')}", data["error"].get("code"))
        result = data.get("result") or [1]
        if result[0] != 0:
            raise UbusError(f"Router call {obj}.{method} failed (ubus status {result[0]}).", result[0])
        return result[1] if len(result) > 1 else {}

    async def login(self):
        if not self.password:
            raise RouterError("ROUTER_PASSWORD isn't set, so the admin panel can't change router settings.")
        try:
            res = await self._rpc(self.ANON, "session", "login",
                                  {"username": self.username, "password": self.password, "timeout": 3600})
        except UbusError as e:
            if e.code == 6:
                raise RouterError("The router rejected ROUTER_USER / ROUTER_PASSWORD in .env.") from e
            raise
        self.sid = res["ubus_rpc_session"]

    async def call(self, obj, method, args=None, timeout=20):
        if not self.sid:
            await self.login()
        try:
            return await self._rpc(self.sid, obj, method, args or {}, timeout)
        except UbusError as e:
            # -32002 = access denied (expired session); 6 = permission denied
            if e.code in (-32002, 6):
                await self.login()
                return await self._rpc(self.sid, obj, method, args or {}, timeout)
            raise

    async def _uci(self, config, **kw):
        return (await self.call("uci", "get", {"config": config, **kw})).get("values", {})

    async def _apply(self, *configs):
        # ubus sessions can't "uci commit". Like LuCI, use "uci apply": it commits this
        # session's staged changes and reloads the affected services (wireless, network, firewall).
        try:
            await self.call("uci", "apply", {"rollback": False})
        except UbusError as e:
            if e.code != 5:  # 5 = no data: nothing was actually changed
                raise

    # ---- reads ----
    async def overview(self):
        wl = await self._uci("wireless")
        radios = [{"id": n, "band": band_of(s), "channel": s.get("channel", "auto"),
                   "disabled": s.get("disabled") == "1"}
                  for n, s in wl.items() if s.get(".type") == "wifi-device"]
        hotspots = [{"id": n, "radio": s.get("device"), "ssid": s.get("ssid", ""),
                     "encryption": s.get("encryption", "none"), "key": s.get("key", ""),
                     "disabled": s.get("disabled") == "1", "network": s.get("network")}
                    for n, s in wl.items() if s.get(".type") == "wifi-iface" and s.get("mode") == "ap"]
        up = wl.get(UPSTREAM_SECTION)
        upstream = None
        if up:
            upstream = {"radio": up.get("device"), "ssid": up.get("ssid"), "encryption": up.get("encryption"),
                        "macaddr": up.get("macaddr"), "disabled": up.get("disabled") == "1"}
        return {"radios": radios, "hotspots": hotspots, "upstream": upstream}

    async def upstream_status(self):
        # short timeouts: the landing page waits on this
        try:
            st = await self.call("network.interface", "status", {"interface": UPSTREAM_NET}, timeout=5)
        except RouterError:
            return {"configured": False, "connected": False}
        out = {"configured": True, "connected": bool(st.get("up")),
               "ip": (st.get("ipv4-address") or [{}])[0].get("address")}
        dev = st.get("l3_device") or st.get("device")
        if dev:
            try:
                info = await self.call("iwinfo", "info", {"device": dev}, timeout=5)
                out.update(ssid=info.get("ssid"), signal=info.get("signal"))
            except RouterError:
                pass
        return out

    async def ethernet_status(self):
        try:
            st = await self.call("network.interface", "status", {"interface": self.wan_iface}, timeout=5)
        except RouterError:
            return {"present": False, "connected": False}
        return {"present": True, "connected": bool(st.get("up")),
                "ip": (st.get("ipv4-address") or [{}])[0].get("address")}

    async def portal_mode(self):
        dh = await self._uci("dhcp")
        for s in dh.values():
            if s.get(".type") == "dnsmasq":
                addrs = s.get("address") or []
                return PORTAL_DNS in (addrs.split() if isinstance(addrs, str) else addrs)
        return False

    async def set_offline_portal(self, on):
        """Turn the offline popup on or off. Idempotent; returns True if anything changed."""
        changed = False
        dh = await self._uci("dhcp")
        name, sec = next(((n, s) for n, s in dh.items() if s.get(".type") == "dnsmasq"), (None, None))
        if name:
            addrs = sec.get("address") or []
            addrs = addrs.split() if isinstance(addrs, str) else list(addrs)
            want = [a for a in addrs if a != PORTAL_DNS] + ([PORTAL_DNS] if on else [])
            if want != addrs:
                if want:
                    await self.call("uci", "set", {"config": "dhcp", "section": name, "values": {"address": want}})
                else:
                    await self.call("uci", "delete", {"config": "dhcp", "section": name, "option": "address"})
                changed = True
        fw = await self._uci("firewall")
        # Phones check for sign-in pages over plain HTTP (port 80). The port match also gives
        # fw4 the TCP match nftables needs before it can DNAT to a port.
        rule = {"name": "AirplaneMode offline popup", "src": "lan", "proto": "tcp", "src_dip": PORTAL_IP,
                "src_dport": "80", "dest_ip": self.lan_ip, "dest_port": self.http_port, "target": "DNAT",
                "enabled": "1" if on else "0"}
        cur = fw.get(PORTAL_RULE)
        if cur is None:
            if on:
                await self.call("uci", "add", {"config": "firewall", "type": "redirect", "name": PORTAL_RULE,
                                               "values": rule})
                changed = True
        elif any(cur.get(k) != v for k, v in rule.items()):
            await self.call("uci", "set", {"config": "firewall", "section": PORTAL_RULE, "values": rule})
            changed = True
        if changed:
            await self._apply("dhcp", "firewall")
        return changed

    async def scan(self, radio):
        try:
            res = await self.call("iwinfo", "scan", {"device": radio})
        except UbusError as e:
            if e.code in (4, 5):  # not found / no data
                raise RouterError(f"{radio} didn't answer a scan. Is its Wi-Fi adapter plugged in and supported?") from e
            raise
        nets = {}
        for n in res.get("results", []):
            ssid = n.get("ssid")
            if not ssid:
                continue
            item = {"ssid": ssid, "bssid": n.get("bssid"), "channel": n.get("channel"),
                    "signal": n.get("signal"), "security": scan_security(n.get("encryption"))}
            if ssid not in nets or (item["signal"] or -999) > (nets[ssid]["signal"] or -999):
                nets[ssid] = item
        return sorted(nets.values(), key=lambda x: -(x["signal"] or -999))

    # ---- upstream ----
    async def join(self, radio, ssid, encryption, key, macaddr):
        ssid = check_ssid(ssid)
        key = check_key(encryption, key, UPSTREAM_SECURITY)
        macaddr = check_mac(macaddr)
        wl = await self._uci("wireless")
        if wl.get(radio, {}).get(".type") != "wifi-device":
            raise RouterError(f"No such radio: {radio}.")

        net = await self._uci("network")
        if UPSTREAM_NET not in net:
            await self.call("uci", "add", {"config": "network", "type": "interface", "name": UPSTREAM_NET,
                                           "values": {"proto": "dhcp", "metric": WWAN_METRIC}})
        elif net[UPSTREAM_NET].get("metric") != WWAN_METRIC:
            await self.call("uci", "set", {"config": "network", "section": UPSTREAM_NET,
                                           "values": {"metric": WWAN_METRIC}})
        fw = await self._uci("firewall")
        for name, s in fw.items():
            if s.get(".type") == "zone" and s.get("name") == "wan":
                nets = s.get("network") or []
                nets = nets.split() if isinstance(nets, str) else list(nets)
                if UPSTREAM_NET not in nets:
                    await self.call("uci", "set", {"config": "firewall", "section": name,
                                                   "values": {"network": nets + [UPSTREAM_NET]}})
                break

        values = {"device": radio, "mode": "sta", "network": UPSTREAM_NET, "ssid": ssid,
                  "encryption": encryption, "disabled": "0"}
        if key:
            values["key"] = key
        if macaddr:
            values["macaddr"] = macaddr
        if UPSTREAM_SECTION in wl:
            await self.call("uci", "set", {"config": "wireless", "section": UPSTREAM_SECTION, "values": values})
            for opt, keep in (("key", key), ("macaddr", macaddr)):
                if not keep and opt in wl[UPSTREAM_SECTION]:
                    await self.call("uci", "delete", {"config": "wireless", "section": UPSTREAM_SECTION, "option": opt})
        else:
            await self.call("uci", "add", {"config": "wireless", "type": "wifi-iface", "name": UPSTREAM_SECTION,
                                           "values": values})
        await self._apply("wireless", "network", "firewall")

    async def leave(self):
        wl = await self._uci("wireless")
        if UPSTREAM_SECTION in wl:
            await self.call("uci", "set", {"config": "wireless", "section": UPSTREAM_SECTION, "values": {"disabled": "1"}})
            await self._apply("wireless")

    # ---- hotspots ----
    async def _guard_last_hotspot(self, removing_id):
        guard_last_hotspot(await self.overview(), removing_id)

    async def save_hotspot(self, hid, ssid, encryption, key, disabled):
        ssid = check_ssid(ssid)
        key = check_key(encryption, key, HOTSPOT_SECURITY)
        wl = await self._uci("wireless")
        sec = wl.get(hid)
        if not sec or sec.get(".type") != "wifi-iface" or sec.get("mode") != "ap":
            raise RouterError("No such hotspot.")
        if disabled:
            await self._guard_last_hotspot(hid)
        values = {"ssid": ssid, "encryption": encryption, "disabled": "1" if disabled else "0"}
        if key:
            values["key"] = key
        await self.call("uci", "set", {"config": "wireless", "section": hid, "values": values})
        if not key and "key" in sec:
            await self.call("uci", "delete", {"config": "wireless", "section": hid, "option": "key"})
        await self._apply("wireless")

    async def add_hotspot(self, radio, ssid, encryption, key):
        ssid = check_ssid(ssid)
        key = check_key(encryption, key, HOTSPOT_SECURITY)
        wl = await self._uci("wireless")
        if wl.get(radio, {}).get(".type") != "wifi-device":
            raise RouterError(f"No such radio: {radio}.")
        values = {"device": radio, "mode": "ap", "network": "lan", "ssid": ssid, "encryption": encryption}
        if key:
            values["key"] = key
        res = await self.call("uci", "add", {"config": "wireless", "type": "wifi-iface", "values": values})
        await self._apply("wireless")
        return res.get("section")

    async def delete_hotspot(self, hid):
        wl = await self._uci("wireless")
        sec = wl.get(hid)
        if not sec or sec.get("mode") != "ap":
            raise RouterError("No such hotspot.")
        await self._guard_last_hotspot(hid)
        await self.call("uci", "delete", {"config": "wireless", "section": hid})
        await self._apply("wireless")

    # ---- helpers for sections the admin service owns ----
    async def _put(self, config, name, stype, values, existing, drop=()):
        """Create or update a named section; delete options in `drop` that aren't in `values`."""
        cur = existing.get(name)
        if cur is None:
            await self.call("uci", "add", {"config": config, "type": stype, "name": name, "values": values})
            return
        await self.call("uci", "set", {"config": config, "section": name, "values": values})
        for opt in drop:
            if opt not in values and opt in cur:
                await self.call("uci", "delete", {"config": config, "section": name, "option": opt})

    async def _delete_sections(self, config, names, existing):
        for n in names:
            if n in existing:
                await self.call("uci", "delete", {"config": config, "section": n})

    # ---- connected devices ----
    @staticmethod
    def _paused(fw):
        return {s.get("src_mac", "").lower() for n, s in fw.items() if n.startswith(BLOCK_PREFIX) and s.get("src_mac")}

    async def devices(self):
        try:
            res = await self.call("luci-rpc", "getDHCPLeases", {"family": 4}, timeout=5)
        except RouterError as e:
            raise RouterError("Can't list devices: the router's luci-rpc API isn't available (is LuCI installed?).") from e
        paused = self._paused(await self._uci("firewall"))
        out = {}
        for lease in res.get("dhcp_leases") or []:
            mac = (lease.get("macaddr") or "").lower()
            if mac:
                out[mac] = {"mac": mac, "ip": lease.get("ipaddr"), "name": lease.get("hostname") or "",
                            "expires": lease.get("expires"), "paused": mac in paused}
        for mac in paused - set(out):
            out[mac] = {"mac": mac, "ip": None, "name": "", "expires": None, "paused": True}
        return sorted(out.values(), key=lambda d: (d["name"] == "", d["name"].lower(), d["mac"]))

    async def stations(self):
        """Who is on this box's own hotspots right now: the Wi-Fi association list of every
        access-point interface (not the upstream one). Raises RouterError if no hotspot
        answered, so "can't tell" never looks like "everyone left"."""
        devs = (await self.call("iwinfo", "devices", {}, timeout=5)).get("devices") or []
        out, aps, answered = {}, 0, 0
        for dev in devs:
            try:
                info = await self.call("iwinfo", "info", {"device": dev}, timeout=5)
            except RouterError:
                continue
            if (info.get("mode") or "").lower() not in ("master", "ap"):
                continue  # the upstream (client) interface
            aps += 1
            try:
                res = await self.call("iwinfo", "assoclist", {"device": dev}, timeout=5)
            except RouterError:
                continue
            answered += 1
            for s in res.get("results") or []:
                mac = (s.get("mac") or "").lower()
                if mac:
                    out[mac] = {"mac": mac, "signal": s.get("signal")}
        if aps and not answered:
            raise RouterError("The hotspots didn't say who is connected (is iwinfo assoclist allowed? "
                              "Re-run scripts/router-setup.sh).")
        return list(out.values())

    async def _sync_blocks(self, paused, vpn_zone):
        """Make the pause rules match `paused` (MACs), for wan and, if it exists, the VPN zone."""
        fw = await self._uci("firewall")
        want = {}
        for mac in paused:
            for zone in ["wan"] + ([VPN_ZONE_NAME] if vpn_zone else []):
                want[block_rule_name(mac, zone)] = {
                    "name": f"AirplaneMode pause {mac}", "src": "lan", "dest": zone, "src_mac": mac,
                    "proto": "all", "target": "REJECT"}
        for name in [n for n in fw if n.startswith(BLOCK_PREFIX) and n not in want]:
            await self.call("uci", "delete", {"config": "firewall", "section": name})
        for name, values in want.items():
            cur = fw.get(name)
            if cur is None or any(cur.get(k) != v for k, v in values.items()):
                await self._put("firewall", name, "rule", values, fw)

    async def set_device_paused(self, mac, paused):
        mac = check_mac(mac)
        if not mac:
            raise RouterError("Which device? (MAC address missing)")
        fw = await self._uci("firewall")
        now = self._paused(fw)
        want = (now | {mac}) if paused else (now - {mac})
        if want == now:
            return
        await self._sync_blocks(want, VPN_ZONE in fw)
        await self._apply("firewall")

    # ---- data use ----
    async def uplink_counters(self):
        """Byte counters of the uplinks that are up: {"ethernet": {rx, tx}, "wifi": {rx, tx}}."""
        out = {}
        for key, iface in (("ethernet", self.wan_iface), ("wifi", UPSTREAM_NET)):
            try:
                st = await self.call("network.interface", "status", {"interface": iface}, timeout=5)
                dev = st.get("l3_device") or st.get("device")
                if not st.get("up") or not dev:
                    continue
                stats = (await self.call("network.device", "status", {"name": dev}, timeout=5)).get("statistics") or {}
                out[key] = {"rx": int(stats.get("rx_bytes") or 0), "tx": int(stats.get("tx_bytes") or 0)}
            except (RouterError, ValueError):
                continue
        return out

    # ---- home VPN ----
    async def vpn_config(self):
        net = await self._uci("network")
        i, p = net.get(VPN_NET), net.get(VPN_PEER)
        if not i or not p:
            return None
        return {"enabled": i.get("disabled") != "1", "addresses": as_list(i.get("addresses")),
                "dns": as_list(i.get("dns")), "endpoint": f"{p.get('endpoint_host')}:{p.get('endpoint_port')}",
                "public_key": p.get("public_key"), "allowed_ips": as_list(p.get("allowed_ips"))}

    async def vpn_status(self):
        try:
            inst = await self.call("luci.wireguard", "getWgInstances", {}, timeout=5)
        except RouterError:
            return {"up": None}  # unknown: luci-proto-wireguard missing, or no access
        d = inst.get(VPN_NET)
        if not d:
            return {"up": False}
        peer = (d.get("peers") or [{}])[0]

        def num(v):
            return int(v) if str(v or "").isdigit() else 0
        return {"up": True, "handshake": num(peer.get("latest_handshake")) or None,
                "rx": num(peer.get("transfer_rx")), "tx": num(peer.get("transfer_tx"))}

    async def set_vpn(self, cfg, enabled=True):
        net = await self._uci("network")
        iface = {"proto": "wireguard", "private_key": cfg["private_key"], "addresses": cfg["addresses"],
                 "metric": VPN_METRIC, "disabled": "0" if enabled else "1"}
        if cfg.get("dns"):
            iface["dns"] = cfg["dns"]
        if cfg.get("mtu"):
            iface["mtu"] = str(cfg["mtu"])
        await self._put("network", VPN_NET, "interface", iface, net, drop=("dns", "mtu"))
        peer = {"description": "AirplaneMode home VPN", "public_key": cfg["public_key"],
                "endpoint_host": cfg["endpoint_host"], "endpoint_port": str(cfg["endpoint_port"]),
                "allowed_ips": cfg["allowed_ips"], "route_allowed_ips": "1",
                "persistent_keepalive": str(cfg["keepalive"])}
        if cfg.get("preshared_key"):
            peer["preshared_key"] = cfg["preshared_key"]
        await self._put("network", VPN_PEER, f"wireguard_{VPN_NET}", peer, net, drop=("preshared_key",))
        fw = await self._uci("firewall")
        await self._put("firewall", VPN_ZONE, "zone", {
            "name": VPN_ZONE_NAME, "network": [VPN_NET], "input": "REJECT", "output": "ACCEPT",
            "forward": "REJECT", "masq": "1", "mtu_fix": "1"}, fw)
        await self._put("firewall", VPN_FWD, "forwarding", {"src": "lan", "dest": VPN_ZONE_NAME}, fw)
        await self._sync_blocks(self._paused(fw), True)
        await self._apply("network", "firewall")

    async def set_vpn_enabled(self, enabled):
        net = await self._uci("network")
        if VPN_NET not in net:
            raise RouterError("No home VPN is set up yet.")
        await self.call("uci", "set", {"config": "network", "section": VPN_NET,
                                       "values": {"disabled": "0" if enabled else "1"}})
        await self._apply("network")

    async def remove_vpn(self):
        net = await self._uci("network")
        await self._delete_sections("network", [VPN_PEER, VPN_NET], net)
        fw = await self._uci("firewall")
        paused = self._paused(fw)
        await self._delete_sections("firewall", [VPN_FWD, VPN_ZONE], fw)
        await self._sync_blocks(paused, False)
        await self._apply("network", "firewall")


# ---------------- Mock ----------------
class MockRouter:
    """A pretend router with two radios and a hotel network behind a captive portal."""
    name = "mock"

    NEARBY = [
        {"ssid": "Harbor_Hotel_Guest", "bssid": "aa:00:00:00:00:01", "channel": 6, "signal": -48,
         "security": "none", "portal": True},
        {"ssid": "Harbor_Hotel_Staff", "bssid": "aa:00:00:00:00:02", "channel": 36, "signal": -61,
         "security": "sae-mixed", "portal": False, "password": "staffonly123"},
        {"ssid": "Gate 14 Free WiFi", "bssid": "aa:00:00:00:00:03", "channel": 11, "signal": -72,
         "security": "none", "portal": True},
        {"ssid": "CoffeeCorner", "bssid": "aa:00:00:00:00:04", "channel": 1, "signal": -80,
         "security": "psk2", "portal": False, "password": "latte2024"},
    ]

    def __init__(self):
        self.radios = [{"id": "radio0", "band": "2.4 GHz", "channel": "auto", "disabled": False},
                       {"id": "radio1", "band": "5 GHz", "channel": "36", "disabled": False}]
        self.hotspots = [
            {"id": "default_radio1", "radio": "radio1", "ssid": "AirplaneMode", "encryption": "sae-mixed",
             "key": "change-me-please", "disabled": False, "network": "lan"},
            {"id": "default_radio0", "radio": "radio0", "ssid": "AirplaneMode-2G", "encryption": "psk2",
             "key": "change-me-please", "disabled": False, "network": "lan"},
        ]
        self.upstream = None
        self.portal_cleared = set()
        self.ethernet = False          # is a WAN cable plugged in (with internet behind it)?
        self.portal_on = False         # offline popup state
        self.in_range = {n["ssid"] for n in self.NEARBY}  # which nearby networks are visible
        self.vpn = None                # {"cfg": parsed config, "enabled": bool}
        self.paused = set()
        self.away = set()              # MACs of pretend devices that left the hotspot
        self.counters = {"ethernet": {"rx": 0, "tx": 0}, "wifi": {"rx": 0, "tx": 0}}
        self.devices_list = [
            {"mac": "3c:22:fb:10:20:30", "ip": "192.168.2.140", "name": "iPhone"},
            {"mac": "a4:83:e7:44:55:66", "ip": "192.168.2.152", "name": "MacBook-Air"},
            {"mac": "f8:0f:f9:77:88:99", "ip": "192.168.2.163", "name": "Pixel-8"},
            {"mac": "74:c2:46:aa:bb:cc", "ip": "192.168.2.171", "name": ""},
        ]
        self._n = 0

    def _net(self, ssid):
        if ssid not in self.in_range:
            return None
        return next((n for n in self.NEARBY if n["ssid"] == ssid), None)

    async def ethernet_status(self):
        return {"present": True, "connected": self.ethernet, "ip": "192.0.2.10" if self.ethernet else None}

    async def portal_mode(self):
        return self.portal_on

    async def set_offline_portal(self, on):
        changed = self.portal_on != bool(on)
        self.portal_on = bool(on)
        return changed

    async def overview(self):
        ov = copy.deepcopy({"radios": self.radios, "hotspots": self.hotspots, "upstream": self.upstream})
        if ov["upstream"]:  # like OpenWrt's overview: never hand out the upstream password
            ov["upstream"] = {k: v for k, v in ov["upstream"].items() if not k.startswith("_")}
        return ov

    async def upstream_status(self):
        if not self.upstream:
            return {"configured": False, "connected": False}
        net = self._net(self.upstream["ssid"])
        ok = (not self.upstream["disabled"] and net is not None
              and (net["security"] == "none" or self.upstream.get("_key") == net.get("password")))
        return {"configured": True, "connected": ok, "ip": "10.42.7.23" if ok else None,
                "ssid": self.upstream["ssid"] if ok else None, "signal": net["signal"] if ok else None}

    async def scan(self, radio):
        if radio not in {r["id"] for r in self.radios}:
            raise RouterError(f"No such radio: {radio}.")
        return [{k: v for k, v in n.items() if k in ("ssid", "bssid", "channel", "signal", "security")}
                for n in self.NEARBY if n["ssid"] in self.in_range]

    async def join(self, radio, ssid, encryption, key, macaddr):
        ssid = check_ssid(ssid)
        key = check_key(encryption, key, UPSTREAM_SECURITY)
        macaddr = check_mac(macaddr)
        self.upstream = {"radio": radio, "ssid": ssid, "encryption": encryption, "macaddr": macaddr,
                         "disabled": False, "_key": key}

    async def leave(self):
        if self.upstream:
            self.upstream["disabled"] = True

    def internet(self):
        """What a captive-portal probe would see right now (mock only)."""
        if self.ethernet:
            return "online"  # Ethernet has priority, and the mock cable has open internet
        if not self.upstream or self.upstream["disabled"]:
            return "offline"
        net = self._net(self.upstream["ssid"])
        if not net or (net["security"] != "none" and self.upstream.get("_key") != net.get("password")):
            return "offline"
        if net["portal"] and net["ssid"] not in self.portal_cleared:
            # a full-tunnel VPN can't get past a sign-in page: everything just times out
            return "offline" if self._vpn_on() else "captive"
        return "online"

    def _vpn_on(self):
        return bool(self.vpn and self.vpn["enabled"])

    # ---- devices, data use, VPN (mock) ----
    async def devices(self):
        out = [dict(d, expires=43000, paused=d["mac"] in self.paused) for d in self.devices_list]
        return sorted(out, key=lambda d: (d["name"] == "", d["name"].lower(), d["mac"]))

    async def stations(self):
        """Every pretend device is on the hotspot unless it has walked off (`away`)."""
        return [{"mac": d["mac"], "signal": -50} for d in self.devices_list if d["mac"] not in self.away]

    async def set_device_paused(self, mac, paused):
        mac = check_mac(mac)
        if not mac:
            raise RouterError("Which device? (MAC address missing)")
        (self.paused.add if paused else self.paused.discard)(mac)

    async def uplink_counters(self):
        """Each call pretends some traffic went by on the uplinks that are up."""
        out = {}
        if self.ethernet:
            c = self.counters["ethernet"]
            c["rx"] += 3_000_000
            c["tx"] += 400_000
            out["ethernet"] = dict(c)
        if (await self.upstream_status())["connected"]:
            c = self.counters["wifi"]
            c["rx"] += 1_500_000
            c["tx"] += 200_000
            out["wifi"] = dict(c)
        return out

    async def vpn_config(self):
        if not self.vpn:
            return None
        c = self.vpn["cfg"]
        return {"enabled": self.vpn["enabled"], "addresses": c["addresses"], "dns": c["dns"],
                "endpoint": f"{c['endpoint_host']}:{c['endpoint_port']}", "public_key": c["public_key"],
                "allowed_ips": c["allowed_ips"]}

    async def vpn_status(self):
        if not self._vpn_on():
            return {"up": False}
        ok = self.internet() == "online"
        return {"up": True, "handshake": int(time.time()) - 20 if ok else None,
                "rx": 52_000_000 if ok else 0, "tx": 7_000_000 if ok else 0}

    async def set_vpn(self, cfg, enabled=True):
        self.vpn = {"cfg": cfg, "enabled": bool(enabled)}

    async def set_vpn_enabled(self, enabled):
        if not self.vpn:
            raise RouterError("No home VPN is set up yet.")
        self.vpn["enabled"] = bool(enabled)

    async def remove_vpn(self):
        self.vpn = None

    def clear_portal(self):
        if self.upstream:
            self.portal_cleared.add(self.upstream["ssid"])

    async def _guard_last_hotspot(self, removing_id):
        guard_last_hotspot(await self.overview(), removing_id)

    async def save_hotspot(self, hid, ssid, encryption, key, disabled):
        ssid = check_ssid(ssid)
        key = check_key(encryption, key, HOTSPOT_SECURITY)
        h = next((h for h in self.hotspots if h["id"] == hid), None)
        if not h:
            raise RouterError("No such hotspot.")
        if disabled:
            await self._guard_last_hotspot(hid)
        h.update(ssid=ssid, encryption=encryption, key=key or "", disabled=bool(disabled))

    async def add_hotspot(self, radio, ssid, encryption, key):
        ssid = check_ssid(ssid)
        key = check_key(encryption, key, HOTSPOT_SECURITY)
        if radio not in {r["id"] for r in self.radios}:
            raise RouterError(f"No such radio: {radio}.")
        self._n += 1
        hid = f"cfg0{self._n}"
        self.hotspots.append({"id": hid, "radio": radio, "ssid": ssid, "encryption": encryption,
                              "key": key or "", "disabled": False, "network": "lan"})
        return hid

    async def delete_hotspot(self, hid):
        if not any(h["id"] == hid for h in self.hotspots):
            raise RouterError("No such hotspot.")
        await self._guard_last_hotspot(hid)
        self.hotspots = [h for h in self.hotspots if h["id"] != hid]

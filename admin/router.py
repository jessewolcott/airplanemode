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

UPSTREAM_SECTION = "airplanemode_up"   # the one STA interface we manage
UPSTREAM_NET = "wwan"                  # its network interface, placed in the firewall's wan zone

HOTSPOT_SECURITY = ["sae-mixed", "psk2", "sae", "none"]
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

    def __init__(self, url, username, password, client):
        self.url = url
        self.username = username
        self.password = password
        self.client = client
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
                                           "values": {"proto": "dhcp"}})
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
        ov = await self.overview()
        others = [h for h in ov["hotspots"] if h["id"] != removing_id and not h["disabled"]]
        if not others:
            raise RouterError("That's the only hotspot that's on. Turning it off would lock everyone out; add another first.")

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
        self._n = 0

    def _net(self, ssid):
        return next((n for n in self.NEARBY if n["ssid"] == ssid), None)

    async def overview(self):
        return copy.deepcopy({"radios": self.radios, "hotspots": self.hotspots, "upstream": self.upstream})

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
                for n in self.NEARBY]

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
        if not self.upstream or self.upstream["disabled"]:
            return "offline"
        net = self._net(self.upstream["ssid"])
        if not net or (net["security"] != "none" and self.upstream.get("_key") != net.get("password")):
            return "offline"
        if net["portal"] and net["ssid"] not in self.portal_cleared:
            return "captive"
        return "online"

    def clear_portal(self):
        if self.upstream:
            self.portal_cleared.add(self.upstream["ssid"])

    async def _guard_last_hotspot(self, removing_id):
        if not [h for h in self.hotspots if h["id"] != removing_id and not h["disabled"]]:
            raise RouterError("That's the only hotspot that's on. Turning it off would lock everyone out; add another first.")

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

"""Integration test: drive the OpenWrt backend against a REAL OpenWrt ubus API.

Run against a throwaway OpenWrt (never a router you're using):
    docker run -d --name owrt-test --network airplanemode openwrt/rootfs:x86-64-24.10.4 /sbin/init
    # give it a root password and a fake two-radio /etc/config/wireless (see docs/testing.md)
    ROUTER_URL=http://owrt-test/ubus ROUTER_PASSWORD=... python tests/test_openwrt_live.py
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import httpx  # noqa: E402

from router import (BLOCK_PREFIX, PORTAL_DNS, PORTAL_RULE, UPSTREAM_NET, UPSTREAM_SECTION, VPN_FWD, VPN_NET,  # noqa: E402
                    VPN_PEER, VPN_ZONE, WWAN_METRIC, OpenWrtRouter, RouterError, UbusError, block_rule_name)
from vpn import parse_wg_conf  # noqa: E402


async def main():
    user = os.environ.get("ROUTER_USER", "root")
    async with httpx.AsyncClient() as client:
        r = OpenWrtRouter(os.environ["ROUTER_URL"], user, os.environ["ROUTER_PASSWORD"], client)
        print(f"--   logging in as {user!r}")

        async def uci(config):
            return (await r.call("uci", "get", {"config": config}))["values"]

        ov = await r.overview()
        assert {x["id"] for x in ov["radios"]} == {"radio0", "radio1"}, ov
        assert len(ov["hotspots"]) == 2 and ov["upstream"] is None, ov
        print("ok   overview reads radios + hotspots")

        # bad login is reported, not crashed on
        bad = OpenWrtRouter(os.environ["ROUTER_URL"], user, "wrong", client)
        try:
            await bad.overview()
            raise AssertionError("wrong password accepted")
        except RouterError as e:
            print("ok   wrong router password ->", e)

        await r.join("radio0", "Harbor_Hotel_Guest", "none", None, None)
        wl, net, fw = await uci("wireless"), await uci("network"), await uci("firewall")
        sta = wl[UPSTREAM_SECTION]
        assert sta[".type"] == "wifi-iface" and sta["mode"] == "sta" and sta["device"] == "radio0", sta
        assert sta["ssid"] == "Harbor_Hotel_Guest" and sta["encryption"] == "none" and "key" not in sta, sta
        assert net[UPSTREAM_NET]["proto"] == "dhcp", net.get(UPSTREAM_NET)
        assert net[UPSTREAM_NET].get("metric") == WWAN_METRIC, net[UPSTREAM_NET]
        wan = next(s for s in fw.values() if s.get(".type") == "zone" and s.get("name") == "wan")
        assert UPSTREAM_NET in wan["network"], wan
        print("ok   join open network: sta iface + wwan + firewall wan zone")

        await r.join("radio1", "CoffeeCorner", "psk2", "latte2024", "12:34:56:78:9a:bc")
        sta = (await uci("wireless"))[UPSTREAM_SECTION]
        assert sta["device"] == "radio1" and sta["key"] == "latte2024" and sta["macaddr"] == "12:34:56:78:9a:bc", sta
        await r.join("radio1", "CoffeeCorner", "psk2", "latte2024", None)
        sta = (await uci("wireless"))[UPSTREAM_SECTION]
        assert "macaddr" not in sta, sta
        wan = next(s for s in (await uci("firewall")).values() if s.get(".type") == "zone" and s.get("name") == "wan")
        assert list(wan["network"]).count(UPSTREAM_NET) == 1, wan
        print("ok   re-join with password + MAC clone, then MAC removed; firewall not duplicated")

        # In an unprivileged container netifd itself hangs (no real radios, procd jails fail),
        # so live link status can only be checked on real hardware. Here: it must not hang or crash.
        t0 = asyncio.get_running_loop().time()
        st = await r.upstream_status()
        took = asyncio.get_running_loop().time() - t0
        assert took < 8, f"status took {took:.1f}s"
        print(f"ok   upstream status returns in {took:.1f}s:", st, "(link state: verify on hardware)")

        await r.leave()
        assert (await uci("wireless"))[UPSTREAM_SECTION]["disabled"] == "1"
        print("ok   leave disables the sta iface")

        await r.save_hotspot("default_radio1", "AirplaneMode", "sae-mixed", "a-long-enough-key", False)
        ap = (await uci("wireless"))["default_radio1"]
        assert ap["ssid"] == "AirplaneMode" and ap["key"] == "a-long-enough-key", ap
        await r.save_hotspot("default_radio0", "AirplaneMode-Open", "none", None, False)
        ap = (await uci("wireless"))["default_radio0"]
        assert ap["encryption"] == "none" and "key" not in ap, ap
        print("ok   save hotspot (WPA2/3 key set; open network drops the key)")

        new = await r.add_hotspot("radio0", "Guests", "psk2", "guestpass1")
        assert (await uci("wireless"))[new]["ssid"] == "Guests"
        await r.save_hotspot("default_radio0", "AirplaneMode-Open", "none", None, True)
        await r.save_hotspot(new, "Guests", "psk2", "guestpass1", True)
        try:
            await r.save_hotspot("default_radio1", "AirplaneMode", "sae-mixed", "a-long-enough-key", True)
            raise AssertionError("allowed turning off the last hotspot")
        except RouterError:
            pass
        await r.delete_hotspot(new)
        assert new not in await uci("wireless")
        print("ok   add / disable / delete hotspot; last-hotspot lockout guard holds")

        # changes are committed to disk, not just staged in the session
        r2 = OpenWrtRouter(os.environ["ROUTER_URL"], user, os.environ["ROUTER_PASSWORD"], client)
        assert (await r2.overview())["hotspots"][0]["ssid"] in ("AirplaneMode", "AirplaneMode-Open")
        print("ok   changes are committed (visible from a fresh session)")

        try:
            await r.scan("radio0")
            print("note scan returned results (unexpected without real radios)")
        except RouterError as e:
            print("ok   scan without real radios fails cleanly ->", e)

        # ---- Ethernet status + offline popup ----
        t0 = asyncio.get_running_loop().time()
        eth = await r.ethernet_status()
        assert asyncio.get_running_loop().time() - t0 < 8, "ethernet status must not hang"
        print("ok   ethernet status:", eth)

        before = await uci("dhcp")
        assert await r.set_offline_portal(True) is True
        dns = next(s for s in (await uci("dhcp")).values() if s.get(".type") == "dnsmasq")
        addrs = dns.get("address") or []
        assert PORTAL_DNS in addrs, addrs
        rule = (await uci("firewall"))[PORTAL_RULE]
        assert rule[".type"] == "redirect" and rule["target"] == "DNAT" and rule["enabled"] == "1", rule
        assert await r.portal_mode() is True
        assert await r.set_offline_portal(True) is False, "second 'on' should change nothing"
        print("ok   offline popup on: wildcard DNS + DNAT rule (idempotent)")
        assert await r.set_offline_portal(False) is True
        dns = next(s for s in (await uci("dhcp")).values() if s.get(".type") == "dnsmasq")
        assert PORTAL_DNS not in (dns.get("address") or []), dns
        assert (await uci("firewall"))[PORTAL_RULE]["enabled"] == "0"
        assert await r.portal_mode() is False
        # other DNS entries (like the airplane.mode name) survive on/off
        other = [a for a in (next(s for s in before.values() if s.get(".type") == "dnsmasq").get("address") or [])
                 if a != PORTAL_DNS]
        assert all(a in (dns.get("address") or []) for a in other), (other, dns.get("address"))
        print("ok   offline popup off: wildcard removed, rule disabled, other DNS entries kept", other)

        # ---- connected devices + pause ----
        devs = await r.devices()
        assert isinstance(devs, list), devs
        mac = "3c:22:fb:10:20:30"
        await r.set_device_paused(mac, True)
        await r.set_device_paused(mac, True)  # idempotent
        fw = await uci("firewall")
        rule = fw[block_rule_name(mac, "wan")]
        assert rule[".type"] == "rule" and rule["src_mac"] == mac and rule["dest"] == "wan" and rule["target"] == "REJECT", rule
        assert rule["proto"] == "all", rule
        assert [d for d in await r.devices() if d["mac"] == mac][0]["paused"] is True
        print(f"ok   devices: DHCP leases readable ({len(devs)} now), pause adds a REJECT rule to wan")

        # ---- lost & found: the hotspots' station lists (no radios here, so empty) ----
        st = await r.stations()
        assert isinstance(st, list), st
        try:  # allowed, even with no such interface: anything but "access denied" (6)
            await r.call("iwinfo", "assoclist", {"device": "phy0-ap0"}, timeout=5)
        except UbusError as e:
            assert e.code != 6, "iwinfo assoclist must be in the ACL (lost & found)"
        print(f"ok   lost & found: station lists readable ({len(st)} now)")

        # ---- data-use counters: must not hang (live numbers need real netifd) ----
        t0 = asyncio.get_running_loop().time()
        counters = await r.uplink_counters()
        assert asyncio.get_running_loop().time() - t0 < 25, "counters must not hang"
        print("ok   uplink counters return:", counters, "(numbers: verify on hardware)")

        # ---- home VPN ----
        priv, pub, psk = ("yAnz5TF+lXXJte14tji3zlMNq+hd2rYUIgJBgB3fBmk=", "xTIBA5rboUvnH4htodjb6e697QjLERt1NAB4mZqp8Dg=",
                          "ME0ylvsvB9xPdzTBBHtbAoCnJuN2D4BdaFDpG6wdcMs=")
        cfg = parse_wg_conf(f"[Interface]\nPrivateKey = {priv}\nAddress = 10.8.0.2/24\nDNS = 10.8.0.1\n"
                            f"[Peer]\nPublicKey = {pub}\nPresharedKey = {psk}\nEndpoint = vpn.example.net:51820\n"
                            "AllowedIPs = 0.0.0.0/0, ::/0\n")
        await r.set_vpn(cfg, True)
        net, fw = await uci("network"), await uci("firewall")
        i, p = net[VPN_NET], net[VPN_PEER]
        assert i["proto"] == "wireguard" and i["private_key"] == priv and i["metric"] == "5" and i["disabled"] == "0", i
        assert p[".type"] == f"wireguard_{VPN_NET}" and p["public_key"] == pub and p["endpoint_host"] == "vpn.example.net", p
        assert p["route_allowed_ips"] == "1" and p["preshared_key"] == psk and "0.0.0.0/0" in p["allowed_ips"], p
        z = fw[VPN_ZONE]
        assert z["name"] == "vpn" and VPN_NET in z["network"] and z["masq"] == "1", z
        assert fw[VPN_FWD]["src"] == "lan" and fw[VPN_FWD]["dest"] == "vpn", fw[VPN_FWD]
        assert fw[block_rule_name(mac, "vpn")]["dest"] == "vpn", "paused devices must be blocked from the VPN too"
        shown = await r.vpn_config()
        assert shown["enabled"] and shown["endpoint"] == "vpn.example.net:51820" and priv not in str(shown) and psk not in str(shown), shown
        # re-saving without a preshared key removes it; disabling keeps the config
        await r.set_vpn({**cfg, "preshared_key": None}, True)
        assert "preshared_key" not in (await uci("network"))[VPN_PEER]
        await r.set_vpn_enabled(False)
        assert (await uci("network"))[VPN_NET]["disabled"] == "1" and (await r.vpn_config())["enabled"] is False
        st = await r.vpn_status()
        print("ok   VPN: wireguard interface + peer + vpn zone + forwarding, keys not shown, on/off; status:", st)

        await r.set_device_paused(mac, False)
        fw = await uci("firewall")
        assert not [n for n in fw if n.startswith(BLOCK_PREFIX)], "unpause must remove both rules"
        await r.set_device_paused(mac, True)
        await r.remove_vpn()
        net, fw = await uci("network"), await uci("firewall")
        assert VPN_NET not in net and VPN_PEER not in net and VPN_ZONE not in fw and VPN_FWD not in fw
        assert block_rule_name(mac, "vpn") not in fw and block_rule_name(mac, "wan") in fw, \
            "removing the VPN drops rules that point at its zone, keeps the wan pause"
        await r.set_device_paused(mac, False)
        assert await r.vpn_config() is None
        print("ok   VPN removed cleanly (no rules left pointing at a missing zone); unpause cleans up")

        if user != "root":
            # the restricted login must be refused everything outside Wi-Fi settings
            denied = [
                ("file", "exec", {"command": "/bin/sh", "params": ["-c", "id"]}),
                ("file", "read", {"path": "/etc/shadow"}),
                ("uci", "get", {"config": "system"}),
                ("uci", "get", {"config": "rpcd"}),
                ("uci", "set", {"config": "dropbear", "section": "main", "values": {"PasswordAuth": "on"}}),
                ("uci", "set", {"config": "system", "section": "@system[0]", "values": {"hostname": "pwned"}}),
                ("system", "board", {}),
                ("rc", "init", {"name": "dropbear", "action": "stop"}),
                ("luci-rpc", "getBoardJSON", {}),
                ("luci.wireguard", "generateKeyPair", {}),
                ("network.device", "set_state", {"name": "br-lan", "defer": True}),
            ]
            for obj, method, args in denied:
                try:
                    await r.call(obj, method, args, timeout=5)
                    raise AssertionError(f"restricted login was ALLOWED {obj}.{method} {args}")
                except RouterError:
                    pass
            print(f"ok   restricted login is refused {len(denied)} out-of-scope calls "
                  "(shell, files, system, rpcd, dropbear, rc, board info, key generation, device state)")


if __name__ == "__main__":
    asyncio.run(main())

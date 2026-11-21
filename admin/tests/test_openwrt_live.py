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

from router import PORTAL_DNS, PORTAL_RULE, UPSTREAM_NET, UPSTREAM_SECTION, WWAN_METRIC, OpenWrtRouter, RouterError  # noqa: E402


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
            ]
            for obj, method, args in denied:
                try:
                    await r.call(obj, method, args, timeout=5)
                    raise AssertionError(f"restricted login was ALLOWED {obj}.{method} {args}")
                except RouterError:
                    pass
            print(f"ok   restricted login is refused {len(denied)} out-of-scope calls (shell, files, system, rpcd, dropbear, rc)")


if __name__ == "__main__":
    asyncio.run(main())

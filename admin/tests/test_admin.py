"""Admin API tests against the mock router.  Run: python admin/tests/test_admin.py"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

os.environ.update(ROUTER_BACKEND="mock", ADMIN_PASSWORD="test-pass", SUPERVISE_INTERVAL="0",
                  DATA_DIR=tempfile.mkdtemp(prefix="am-test-"), JOIN_GRACE="45", PORTAL_AFTER_TICKS="2")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from router import check_key, check_mac, check_ssid, scan_security, RouterError  # noqa: E402

c = TestClient(main.app)
H = {"X-AirplaneMode": "1"}


def ok(r, code=200):
    assert r.status_code == code, (r.status_code, r.text)
    return r.json()


def test_validation():
    for bad in ["", "x" * 33, "é" * 17, "a\nb"]:
        try:
            check_ssid(bad)
            raise AssertionError(f"accepted ssid {bad!r}")
        except RouterError:
            pass
    assert check_key("none", "ignored", ["none"]) is None
    for bad in ["short", "x" * 64, "pässwörd123"]:
        try:
            check_key("psk2", bad, ["psk2"])
            raise AssertionError(f"accepted key {bad!r}")
        except RouterError:
            pass
    assert check_key("psk2", "a" * 64, ["psk2"]) == "a" * 64  # 64 hex chars = raw PSK
    assert check_mac("AA-BB-CC-DD-EE-F0") == "aa:bb:cc:dd:ee:f0"
    for bad in ["01:00:5e:00:00:01", "nope"]:
        try:
            check_mac(bad)
            raise AssertionError(f"accepted mac {bad!r}")
        except RouterError:
            pass
    assert scan_security({"enabled": False}) == "none"
    assert scan_security({"enabled": True, "wpa": [2], "authentication": ["psk"]}) == "psk2"
    assert scan_security({"enabled": True, "wpa": [3], "authentication": ["sae"]}) == "sae"
    assert scan_security({"enabled": True, "wpa": [2, 3], "authentication": ["psk", "sae"]}) == "sae-mixed"
    assert scan_security({"enabled": True, "wpa": [2], "authentication": ["802.1x"]}) is None


def test_auth():
    assert c.get("/api/admin/wifi").status_code == 401
    assert c.post("/api/admin/login", json={"password": "wrong"}).status_code == 401
    ok(c.post("/api/admin/login", json={"password": "test-pass"}))
    ok(c.get("/api/admin/wifi"))
    # mutations without our header are refused (blocks cross-site form posts)
    assert c.delete("/api/admin/upstream").status_code == 403


def test_captive_flow():
    s = ok(c.get("/api/status?refresh=1"))
    assert s["internet"] == "offline", s
    nets = ok(c.post("/api/admin/scan", json={"radio": "radio0"}, headers=H))["networks"]
    hotel = next(n for n in nets if n["ssid"] == "Harbor_Hotel_Guest")
    ok(c.post("/api/admin/upstream", headers=H, json={"radio": "radio0", "ssid": hotel["ssid"], "encryption": hotel["security"]}))
    s = ok(c.get("/api/status?refresh=1"))
    assert s["internet"] == "captive" and s["login_url"], s
    assert s["upstream"]["ssid"] == "Harbor_Hotel_Guest"
    r = c.post("/api/mock/portal", follow_redirects=False)
    assert r.status_code == 303
    s = ok(c.get("/api/status"))
    assert s["internet"] == "online", s
    # wrong password for a protected network: connected nowhere
    ok(c.post("/api/admin/upstream", headers=H, json={"radio": "radio1", "ssid": "CoffeeCorner", "encryption": "psk2", "key": "wrongpass1"}))
    assert ok(c.get("/api/status?refresh=1"))["internet"] == "offline"
    ok(c.post("/api/admin/upstream", headers=H, json={"radio": "radio1", "ssid": "CoffeeCorner", "encryption": "psk2", "key": "latte2024", "macaddr": "12:34:56:78:9a:bc"}))
    assert ok(c.get("/api/status?refresh=1"))["internet"] == "online"
    w = ok(c.get("/api/admin/wifi"))
    assert w["upstream"]["macaddr"] == "12:34:56:78:9a:bc"
    assert "latte2024" not in str(w["upstream"]), "the upstream password must not be handed out"
    ok(c.delete("/api/admin/upstream", headers=H))
    assert ok(c.get("/api/status?refresh=1"))["internet"] == "offline"


def test_hotspots():
    w = ok(c.get("/api/admin/wifi"))
    a, b = [h["id"] for h in w["hotspots"]]
    assert c.put(f"/api/admin/hotspots/{a}", headers=H, json={"ssid": "Trip", "encryption": "psk2", "key": "short"}).status_code == 400
    ok(c.put(f"/api/admin/hotspots/{a}", headers=H, json={"ssid": "Trip", "encryption": "sae-mixed", "key": "a-long-enough-key"}))
    ok(c.put(f"/api/admin/hotspots/{b}", headers=H, json={"ssid": "Trip-2G", "encryption": "psk2", "key": "a-long-enough-key", "disabled": True}))
    # turning off the last enabled hotspot would lock everyone out
    r = c.put(f"/api/admin/hotspots/{a}", headers=H, json={"ssid": "Trip", "encryption": "sae-mixed", "key": "a-long-enough-key", "disabled": True})
    assert r.status_code == 400 and "lock" in r.json()["detail"], r.text
    assert c.delete(f"/api/admin/hotspots/{a}", headers=H).status_code == 400
    new = ok(c.post("/api/admin/hotspots", headers=H, json={"radio": "radio0", "ssid": "Guests", "encryption": "none"}))["id"]
    ok(c.delete(f"/api/admin/hotspots/{a}", headers=H))  # allowed now: "Guests" is on
    ids = [h["id"] for h in ok(c.get("/api/admin/wifi"))["hotspots"]]
    assert a not in ids and new in ids
    # the public status never exposes keys
    assert "key" not in str(ok(c.get("/api/status")))


def test_guard_ignores_hotspots_on_radios_that_are_off():
    r = main.router
    saved_radios, saved_hotspots = [dict(x) for x in r.radios], [dict(h) for h in r.hotspots]
    try:
        r.hotspots = [
            {"id": "a", "radio": "radio1", "ssid": "Main", "encryption": "psk2", "key": "a-long-enough-key", "disabled": False, "network": "lan"},
            {"id": "b", "radio": "radio0", "ssid": "Other", "encryption": "psk2", "key": "a-long-enough-key", "disabled": False, "network": "lan"},
        ]
        r.radios[0]["disabled"] = True  # radio0 is off, so "Other" isn't really broadcasting
        r_ = c.put("/api/admin/hotspots/a", headers=H, json={"ssid": "Main", "encryption": "psk2", "key": "a-long-enough-key", "disabled": True})
        assert r_.status_code == 400 and "lock" in r_.json()["detail"], r_.text
        assert c.delete("/api/admin/hotspots/a", headers=H).status_code == 400
        r.radios[0]["disabled"] = False
        ok(c.put("/api/admin/hotspots/a", headers=H, json={"ssid": "Main", "encryption": "psk2", "key": "a-long-enough-key", "disabled": True}))
    finally:
        r.radios, r.hotspots = saved_radios, saved_hotspots


def tick(now):
    return asyncio.run(main.supervisor.tick(now))


def reset_world():
    r = main.router
    r.upstream, r.ethernet, r.portal_on, r.vpn = None, False, False, None
    r.in_range = {n["ssid"] for n in r.NEARBY}
    main.store.data.update(networks=[], paused=False)
    main.supervisor.__init__()
    main._bust()


def test_manual_join_is_remembered():
    reset_world()
    ok(c.post("/api/admin/upstream", headers=H, json={"radio": "radio1", "ssid": "CoffeeCorner", "encryption": "psk2", "key": "latte2024"}))
    saved = ok(c.get("/api/admin/wifi"))["saved"]
    assert [n["ssid"] for n in saved] == ["CoffeeCorner"] and saved[0]["has_key"], saved
    assert "latte2024" not in str(saved), "saved-network list must not expose keys"
    # an invalid key is rejected before anything is saved
    assert c.post("/api/admin/upstream", headers=H, json={"radio": "radio1", "ssid": "Bad", "encryption": "psk2", "key": "short"}).status_code == 400
    assert len(ok(c.get("/api/admin/wifi"))["saved"]) == 1
    # saved to disk, not just memory
    assert "CoffeeCorner" in (Path(os.environ["DATA_DIR"]) / "networks.json").read_text()


def test_autojoin_strongest_then_falls_back():
    reset_world()
    main.store.remember("radio1", "CoffeeCorner", "psk2", "latte2024", None)              # -80 dBm, right key
    main.store.remember("radio1", "Harbor_Hotel_Staff", "sae-mixed", "wrong-key-1", None)  # -61 dBm, wrong key
    r = main.router
    r.in_range = set()  # travelling: nothing known nearby
    t = tick(0)
    assert t["action"] == "no saved network in range" and not r.portal_on, t
    t = tick(30)
    assert r.portal_on, "offline popup should turn on after 2 ticks with no uplink"
    s = ok(c.get("/api/status?refresh=1"))
    assert s["internet"] == "offline" and s["via"] is None and s["joining"] is None, s

    r.in_range = {"CoffeeCorner", "Harbor_Hotel_Staff", "Gate 14 Free WiFi"}
    t = tick(60)
    assert t["joining"] == "Harbor_Hotel_Staff", t       # strongest saved network first
    assert ok(c.get("/api/status"))["joining"] == "Harbor_Hotel_Staff"
    t = tick(80)
    assert "waiting" in t["action"], t                     # still inside the grace period
    t = tick(110)                                          # failed once -> retries it
    assert t["joining"] == "Harbor_Hotel_Staff", t
    t = tick(160)                                          # failed twice -> skip, try the next one
    assert t["joining"] == "CoffeeCorner", t
    t = tick(170)                                          # CoffeeCorner is up
    assert t["linked"] and not r.portal_on, t              # popup off as soon as there's a link
    s = ok(c.get("/api/status?refresh=1"))
    assert s["internet"] == "online" and s["via"] == "wifi" and s["upstream"]["ssid"] == "CoffeeCorner", s
    # unsaved networks are never auto-joined
    assert r.upstream["ssid"] != "Gate 14 Free WiFi"


def test_ethernet_wins():
    reset_world()
    r = main.router
    r.ethernet = True
    t = tick(0)
    assert t["linked"] and t["ethernet"], t
    s = ok(c.get("/api/status?refresh=1"))
    assert s["internet"] == "online" and s["via"] == "ethernet", s
    # with a cable, the box doesn't go hunting for Wi-Fi or show the offline popup
    main.store.remember("radio1", "CoffeeCorner", "psk2", "latte2024", None)
    tick(30), tick(60)
    assert r.upstream is None and not r.portal_on
    r.ethernet = False                                     # unplugged: Wi-Fi takes over
    t = tick(90)
    assert t["joining"] == "CoffeeCorner", t


def test_disconnect_pauses_autojoin():
    reset_world()
    main.store.remember("radio1", "CoffeeCorner", "psk2", "latte2024", None)
    ok(c.delete("/api/admin/upstream", headers=H))
    assert ok(c.get("/api/admin/wifi"))["autojoin"]["paused"] is True
    t = tick(0)
    assert t["joining"] is None and main.router.upstream is None, t
    assert ok(c.get("/api/status?refresh=1"))["autojoin"] is False
    ok(c.post("/api/admin/autojoin", headers=H, json={"enabled": True}))
    assert tick(30)["joining"] == "CoffeeCorner"


def test_forget():
    reset_world()
    main.store.remember("radio1", "CoffeeCorner", "psk2", "latte2024", None)
    ok(c.delete("/api/admin/networks/CoffeeCorner", headers=H))
    assert ok(c.get("/api/admin/wifi"))["saved"] == []
    assert c.delete("/api/admin/networks/CoffeeCorner", headers=H).status_code == 404
    main.router.in_range = {"CoffeeCorner"}
    assert tick(0)["joining"] is None
    # names with a slash (allowed in SSIDs) can be forgotten too
    main.store.remember("radio1", "Cafe 24/7", "none", None, None)
    ok(c.delete("/api/admin/networks/" + quote("Cafe 24/7", safe=""), headers=H))
    assert ok(c.get("/api/admin/wifi"))["saved"] == []


def test_router_changes_are_serialized():
    """The supervisor and admin requests must not interleave router changes."""
    order = []

    async def run():
        orig = main.router.leave

        async def slow_leave():
            order.append("leave start")
            await asyncio.sleep(0.05)
            order.append("leave end")
            await orig()

        main.router.leave = slow_leave
        try:
            async def admin_change():
                async with main.router_lock:
                    await main.router.leave()

            async def supervisor_tick():
                await asyncio.sleep(0.01)
                await main.supervisor.tick(0)
                order.append("tick")

            await asyncio.gather(admin_change(), supervisor_tick())
        finally:
            main.router.leave = orig

    reset_world()
    asyncio.run(run())
    assert order == ["leave start", "leave end", "tick"], order


def test_offline_popup_waits_during_join():
    reset_world()
    main.store.remember("radio1", "CoffeeCorner", "psk2", "wrong-key-9", None)
    r = main.router
    tick(0)                                                # starts joining immediately
    tick(10), tick(20)                                     # 3 ticks down, but inside the grace period
    assert not r.portal_on, "popup must not flip on while a join is in progress"
    tick(50)                                               # grace over: first failure, retry (new grace)
    assert not r.portal_on
    t = tick(100)                                          # second failure: nothing left to try
    assert t["joining"] is None and r.portal_on, t


KEY_A = "yAnz5TF+lXXJte14tji3zlMNq+hd2rYUIgJBgB3fBmk="
KEY_B = "xTIBA5rboUvnH4htodjb6e697QjLERt1NAB4mZqp8Dg="
WG_CONF = f"""
# exported from the home router
[Interface]
PrivateKey = {KEY_A}
Address = 10.8.0.2/24, fd00::2/64
DNS = 10.8.0.1, home.lan

[Peer]
PublicKey = {KEY_B}
PresharedKey = {KEY_A}
Endpoint = home.example.net:51820
AllowedIPs = 0.0.0.0/0, ::/0
PersistentKeepalive = 25
"""


def test_wireguard_config_parsing():
    from vpn import parse_wg_conf
    cfg = parse_wg_conf(WG_CONF)
    assert cfg["addresses"] == ["10.8.0.2/24", "fd00::2/64"] and cfg["dns"] == ["10.8.0.1"], cfg
    assert (cfg["endpoint_host"], cfg["endpoint_port"]) == ("home.example.net", 51820)
    assert cfg["allowed_ips"] == ["0.0.0.0/0", "::/0"] and cfg["preshared_key"] == KEY_A
    assert parse_wg_conf(WG_CONF.replace("home.example.net:51820", "[2001:db8::1]:51820"))["endpoint_host"] == "2001:db8::1"
    bad = [
        WG_CONF.replace(KEY_A, "notakey", 1),                         # private key
        WG_CONF.replace("Endpoint = home.example.net:51820", "Endpoint = home.example.net"),
        WG_CONF.replace(":51820", ":70000"),
        WG_CONF.replace("Address = 10.8.0.2/24, fd00::2/64", "Address = 10.8.0.300/24"),
        WG_CONF.replace("[Peer]", "[Interface]"),                     # two interfaces, no peer
        WG_CONF.replace("home.example.net", "bad host;rm -rf"),
        "just some text",
    ]
    for text in bad:
        try:
            parse_wg_conf(text)
            raise AssertionError(f"accepted bad config:\n{text}")
        except RouterError:
            pass


def test_vpn_api():
    reset_world()
    main.router.vpn = None
    assert ok(c.get("/api/admin/vpn")) == {"configured": False}
    assert c.put("/api/admin/vpn", headers=H, json={"config": "nope"}).status_code == 400
    ok(c.put("/api/admin/vpn", headers=H, json={"config": WG_CONF}))
    v = ok(c.get("/api/admin/vpn"))
    assert v["configured"] and v["enabled"] and v["full_tunnel"] and v["endpoint"] == "home.example.net:51820", v
    assert KEY_A not in str(v), "private and preshared keys must never be handed out"
    assert v["connected"] is False                           # no uplink yet
    main.router.ethernet = True
    s = ok(c.get("/api/status?refresh=1"))
    assert s["vpn"] == {"configured": True, "enabled": True, "connected": True}, s
    assert KEY_A not in str(s) and "home.example.net" not in str(s)
    # behind a hotel sign-in page the tunnel can't come up: the page must say so
    main.router.ethernet = False
    ok(c.post("/api/admin/upstream", headers=H, json={"radio": "radio0", "ssid": "Gate 14 Free WiFi", "encryption": "none"}))
    s = ok(c.get("/api/status?refresh=1"))
    assert s["internet"] == "offline" and s["via"] == "wifi" and s["vpn"]["connected"] is False, s
    ok(c.post("/api/admin/vpn/enabled", headers=H, json={"enabled": False}))
    s = ok(c.get("/api/status?refresh=1"))
    assert s["internet"] == "captive" and s["vpn"]["enabled"] is False, s
    ok(c.delete("/api/admin/vpn", headers=H))
    assert ok(c.get("/api/admin/vpn")) == {"configured": False}
    assert c.post("/api/admin/vpn/enabled", headers=H, json={"enabled": True}).status_code == 400


def test_devices_pause():
    reset_world()
    main.router.paused = set()
    devs = ok(c.get("/api/admin/devices", headers={"X-Forwarded-For": "192.168.2.152"}))["devices"]
    assert len(devs) == 4 and not any(d["paused"] for d in devs)
    assert [d["name"] for d in devs if d["you"]] == ["MacBook-Air"], devs
    mac = next(d["mac"] for d in devs if d["name"] == "Pixel-8")
    ok(c.post(f"/api/admin/devices/{mac}/pause", headers=H, json={"paused": True}))
    assert [d["name"] for d in ok(c.get("/api/admin/devices"))["devices"] if d["paused"]] == ["Pixel-8"]
    ok(c.post(f"/api/admin/devices/{mac}/pause", headers=H, json={"paused": False}))
    assert not any(d["paused"] for d in ok(c.get("/api/admin/devices"))["devices"])
    assert c.post("/api/admin/devices/01:00:5e:00:00:01/pause", headers=H, json={"paused": True}).status_code == 400


def test_lost_and_found():
    reset_world()
    main.store.data["seen"] = {}
    main.presence.ok = None
    main.router.away = set()
    assert all(d["here"] is None for d in ok(c.get("/api/admin/devices"))["devices"])  # not sampled yet
    t0 = 1_000_000.0
    asyncio.run(main.presence.track(now=t0))
    devs = ok(c.get("/api/admin/devices"))
    assert devs["tracking"] and all(d["here"] and d["since"] == t0 for d in devs["devices"]), devs
    # the Pixel leaves; ten minutes later it's "last seen" at t0, listed after the ones still here
    main.router.away = {"f8:0f:f9:77:88:99"}
    asyncio.run(main.presence.track(now=t0 + 600))
    devs = ok(c.get("/api/admin/devices"))["devices"]
    pixel = devs[-1]
    assert pixel["name"] == "Pixel-8" and pixel["here"] is False and pixel["last_seen"] == t0 and pixel["since"] is None
    assert all(d["here"] and d["last_seen"] == t0 + 600 for d in devs[:-1])
    # back after a short blip: the same visit; after a long gap: a new one
    main.router.away = set()
    asyncio.run(main.presence.track(now=t0 + 660))                 # back after 11 minutes: new visit
    assert main.store.data["seen"]["f8:0f:f9:77:88:99"]["since"] == t0 + 660
    main.router.away = {"f8:0f:f9:77:88:99"}
    asyncio.run(main.presence.track(now=t0 + 690))
    main.router.away = set()
    asyncio.run(main.presence.track(now=t0 + 720))                 # a one-minute blip: same visit
    assert main.store.data["seen"]["f8:0f:f9:77:88:99"]["since"] == t0 + 660
    main.router.away = {"f8:0f:f9:77:88:99"}
    asyncio.run(main.presence.track(now=t0 + 750))
    main.router.away = set()
    asyncio.run(main.presence.track(now=t0 + 5000))
    assert main.store.data["seen"]["f8:0f:f9:77:88:99"]["since"] == t0 + 5000
    # lease gone: still listed, with the name it had
    main.router.away = {"74:c2:46:aa:bb:cc", "f8:0f:f9:77:88:99"}
    asyncio.run(main.presence.track(now=t0 + 6000))
    saved = main.router.devices_list
    main.router.devices_list = [d for d in saved if d["name"] != "Pixel-8"]
    try:
        gone = next(d for d in ok(c.get("/api/admin/devices"))["devices"] if d["mac"] == "f8:0f:f9:77:88:99")
        assert gone["name"] == "Pixel-8" and gone["ip"] is None and gone["last_seen"] == t0 + 5000, gone
    finally:
        main.router.devices_list = saved
    # older than SEEN_KEEP_DAYS: forgotten
    asyncio.run(main.presence.track(now=t0 + 5000 + main.SEEN_KEEP_DAYS * 86400 + 1))
    assert "f8:0f:f9:77:88:99" not in main.store.data["seen"]
    # can't read the station list: keep the history, and don't call anyone gone
    real = main.router.stations
    async def refused():
        raise main.RouterError("denied")
    main.router.stations = refused
    try:
        asyncio.run(main.presence.track(now=t0 + 9e6))
        devs = ok(c.get("/api/admin/devices"))
        assert devs["tracking"] is False and all(d["here"] is None for d in devs["devices"])
        assert main.store.data["seen"], "history kept"
    finally:
        main.router.stations = real
    # the mock world walks devices off by name; clearing history needs the admin
    assert ok(c.post("/api/mock/world", json={"away": ["iPhone"]}))["away"] == ["3c:22:fb:10:20:30"]
    ok(c.delete("/api/admin/devices/history", headers=H))
    assert main.store.data["seen"] == {}
    main.router.away = set()


def test_whos_who():
    from fastapi.testclient import TestClient as TC
    main._leases["at"] = -1e9
    pixel = {"X-Forwarded-For": "192.168.2.163"}           # Pixel-8's lease
    a, b = TC(main.app), TC(main.app)                      # two browsers on the Pixel
    me = ok(a.get("/api/me", headers=pixel))
    assert me["known"] is False and me["id"] is None and "🦊" in me["avatars"]
    assert a.cookies.get(main.WHO_COOKIE)                  # every browser gets a cookie
    assert a.put("/api/me", json={"name": "Sam", "avatar": "🦊"}, headers=pixel).status_code == 403  # no header
    for bad in [{"name": "  ", "avatar": "🦊"}, {"name": "x" * 21, "avatar": "🦊"},
                {"name": "a\x07b", "avatar": "🦊"}, {"name": "Sam", "avatar": "💩"}]:
        assert a.put("/api/me", json=bad, headers={**H, **pixel}).status_code == 400, bad
    me = ok(a.put("/api/me", json={"name": "  Sam   the\tman ", "avatar": "🦊"}, headers={**H, **pixel}))
    assert me["known"] and me["name"] == "Sam the man" and len(me["id"]) == 16, me
    # the other browser on the same phone: found by MAC, not cookie
    assert ok(b.get("/api/me", headers=pixel))["id"] == me["id"]
    # the phone's random MAC changes (new IP, no lease): the first browser's cookie still knows Sam
    assert ok(a.get("/api/me", headers={"X-Forwarded-For": "10.9.9.9"}))["name"] == "Sam the man"
    # a different device is someone else
    assert ok(TC(main.app).get("/api/me", headers={"X-Forwarded-For": "192.168.2.140"}))["known"] is False
    # no lease at all (dev stack): the cookie alone works
    d = TC(main.app)
    ok(d.put("/api/me", json={"name": "Dev", "avatar": "🐙"}, headers={**H, "X-Forwarded-For": "172.18.0.1"}))
    assert ok(d.get("/api/me"))["name"] == "Dev"
    # an app's backend asks on the user's behalf, passing on the forwarded address
    assert ok(c.get("/api/me", headers=pixel))["name"] == "Sam the man"
    # the admin's device list shows the name
    devs = ok(c.get("/api/admin/devices"))["devices"]
    assert next(x["person"] for x in devs if x["name"] == "Pixel-8") == {"name": "Sam the man", "avatar": "🦊"}
    assert next(x["person"] for x in devs if x["name"] == "iPhone") is None
    # ids survive a restart (same secret, same links)
    assert main._me(main.Store(main.store.path).data["who_links"]["mac:f8:0f:f9:77:88:99"])["id"] == me["id"]
    # forget: gone from every browser on the phone
    assert ok(b.delete("/api/me", headers={**H, **pixel}))["known"] is False
    assert ok(a.get("/api/me", headers=pixel))["known"] is False
    assert [p["name"] for p in main.store.data["people"].values()] == ["Dev"]
    assert set(main.store.data["who_links"].values()) == set(main.store.data["people"])


def test_data_use():
    reset_world()
    main.store.data["usage"] = {}
    r = main.router
    main.store.remember("radio1", "CoffeeCorner", "psk2", "latte2024", None)
    tick(0)                                  # joins
    tick(10), tick(20), tick(30)             # first counted tick sets the baseline, then 2 deltas
    u = ok(c.get("/api/admin/usage"))
    assert u["current"]["name"] == "CoffeeCorner" and u["current"]["rx"] == 3_000_000, u
    assert u["networks"][0]["name"] == "CoffeeCorner" and u["networks"][0]["tx"] == 400_000, u
    r.ethernet = True                        # cable in: usage counts on both, Ethernet is "current"
    tick(40), tick(50)
    u = ok(c.get("/api/admin/usage"))
    assert u["current"]["name"] == "Ethernet" and u["current"]["rx"] == 3_000_000, u
    assert {n["name"] for n in u["networks"]} == {"CoffeeCorner", "Ethernet"}
    r.counters["wifi"] = {"rx": 0, "tx": 0}  # counters restart (interface recreated)
    tick(60)
    cc = next(n for n in ok(c.get("/api/admin/usage"))["networks"] if n["name"] == "CoffeeCorner")
    assert cc["rx"] == 3_000_000 + 1_500_000 * 3, cc
    ok(c.delete("/api/admin/usage", headers=H))
    assert ok(c.get("/api/admin/usage"))["networks"] == []


def test_apps_on_the_menu():
    apps = Path(main.DATA_DIR) / "apps-catalog.json"
    apps.write_text('[{"id": "drop", "name": "Drop", "path": "/drop/"}, {"id": "cinema", "name": "Cinema", "path": "/cinema/"}]')
    main.APPS_FILE = apps
    assert [a["id"] for a in ok(c.get("/api/apps"))] == ["drop", "cinema"]
    assert c.put("/api/admin/apps/cinema", json={"enabled": False}).status_code == 403   # no header
    ok(c.put("/api/admin/apps/cinema", json={"enabled": False}, headers=H))
    assert [a["id"] for a in ok(c.get("/api/apps"))] == ["drop"]                         # public list
    assert {a["id"]: a["enabled"] for a in ok(c.get("/api/admin/apps"))["apps"]} == {"drop": True, "cinema": False}
    assert c.put("/api/admin/apps/nope", json={"enabled": False}, headers=H).status_code == 404
    ok(c.put("/api/admin/apps/cinema", json={"enabled": True}, headers=H))
    assert len(ok(c.get("/api/apps"))) == 2
    # signed out: can't switch apps, and the session check (used by Drop) says no
    assert ok(c.get("/api/admin/session")) == {"ok": True}
    cookie = c.cookies.get(main.COOKIE)
    c.cookies.clear()
    assert c.put("/api/admin/apps/drop", json={"enabled": False}, headers=H).status_code == 401
    assert c.get("/api/admin/session").status_code == 401
    assert c.get("/api/admin/session", headers={"Cookie": f"{main.COOKIE}={cookie}"}).status_code == 200
    c.cookies.set(main.COOKIE, cookie)
    main.APPS_FILE = Path("/nonexistent/apps.json")
    assert ok(c.get("/api/apps")) == []                                                   # missing file: empty, not an error


def test_model_apps_only_with_the_model():
    apps = Path(main.DATA_DIR) / "apps-model.json"
    apps.write_text('[{"id": "drop", "name": "Drop", "path": "/drop/", "uses_llm": false},'
                    ' {"id": "couch", "name": "The Couch", "path": "/couch/", "uses_llm": true}]')
    main.APPS_FILE = apps
    try:
        for profiles, want in [("llm", ["drop", "couch"]), ("", ["drop"]), ("other, llm", ["drop", "couch"]),
                               ("llmx", ["drop"])]:
            os.environ["COMPOSE_PROFILES"] = profiles
            assert [a["id"] for a in ok(c.get("/api/apps"))] == want, profiles
            assert [a["id"] for a in ok(c.get("/api/admin/apps"))["apps"]] == want, profiles
        assert c.put("/api/admin/apps/couch", json={"enabled": False}, headers=H).status_code == 404   # not installed
    finally:
        os.environ.pop("COMPOSE_PROFILES", None)
        main.APPS_FILE = Path("/nonexistent/apps.json")


def test_logout():
    ok(c.post("/api/admin/logout", headers=H))
    assert c.get("/api/admin/wifi").status_code == 401


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok  ", name)

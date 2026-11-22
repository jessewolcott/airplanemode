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
    r.upstream, r.ethernet, r.portal_on = None, False, False
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


def test_logout():
    ok(c.post("/api/admin/logout", headers=H))
    assert c.get("/api/admin/wifi").status_code == 401


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok  ", name)

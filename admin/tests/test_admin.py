"""Admin API tests against the mock router.  Run: python admin/tests/test_admin.py"""
import os
import sys
from pathlib import Path

os.environ.update(ROUTER_BACKEND="mock", ADMIN_PASSWORD="test-pass")
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
    assert ok(c.get("/api/admin/wifi"))["upstream"]["macaddr"] == "12:34:56:78:9a:bc"
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


def test_logout():
    ok(c.post("/api/admin/logout", headers=H))
    assert c.get("/api/admin/wifi").status_code == 401


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok  ", name)

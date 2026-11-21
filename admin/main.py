"""AirplaneMode admin service.

Public:
  GET  /api/status            internet state (online / captive / offline), sign-in link, upstream name
Admin (password, cookie session):
  POST /api/admin/login       {password}
  POST /api/admin/logout
  GET  /api/admin/wifi        radios, hotspots (with keys), upstream config + live status
  POST /api/admin/scan        {radio}                      -> nearby networks
  POST /api/admin/upstream    {radio, ssid, encryption, key, macaddr}
  DELETE /api/admin/upstream
  POST /api/admin/hotspots    {radio, ssid, encryption, key}
  PUT  /api/admin/hotspots/{id} {ssid, encryption, key, disabled}
  DELETE /api/admin/hotspots/{id}
UI:
  /admin/                     the admin panel (static)
"""
import asyncio
import hmac
import os
import secrets
import time
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from router import MockRouter, OpenWrtRouter, RouterError

HERE = Path(__file__).parent
BACKEND = os.getenv("ROUTER_BACKEND", "openwrt")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
PROBE_URL = os.getenv("PROBE_URL", "http://connectivitycheck.gstatic.com/generate_204")
SESSION_HOURS = 12

client = httpx.AsyncClient()
if BACKEND == "mock":
    router = MockRouter()
else:
    router = OpenWrtRouter(os.getenv("ROUTER_URL", "http://host.docker.internal/ubus"),
                           os.getenv("ROUTER_USER", "root"), os.getenv("ROUTER_PASSWORD", ""), client)

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@app.exception_handler(RouterError)
async def router_error(_req, exc):
    return JSONResponse({"detail": str(exc)}, status_code=400)


# ---------------- internet / captive portal ----------------
_status_cache = {"at": 0.0, "value": None}


async def probe():
    """Ask a well-known 'are we online?' URL, the same trick phones use to spot hotel sign-in pages."""
    if BACKEND == "mock":
        state = router.internet()
        return state, ("/api/mock/portal" if state == "captive" else None)
    try:
        r = await client.get(PROBE_URL, follow_redirects=False, timeout=5)
    except Exception:
        return "offline", None
    if r.status_code == 204:
        return "online", None
    if r.status_code in (301, 302, 303, 307, 308):
        return "captive", r.headers.get("location") or PROBE_URL
    # 200 with a page instead of an empty 204: the network is serving its sign-in page
    return "captive", PROBE_URL


@app.get("/api/status")
async def status(refresh: bool = False):
    now = time.monotonic()
    if refresh or not _status_cache["value"] or now - _status_cache["at"] > 10:
        internet, login_url = await probe()
        try:
            up = await router.upstream_status()
        except RouterError:
            up = {"configured": None, "connected": None}
        _status_cache.update(at=now, value={
            "internet": internet, "login_url": login_url,
            "upstream": {"ssid": up.get("ssid"), "connected": up.get("connected"), "signal": up.get("signal")},
            "backend": router.name,
        })
    return _status_cache["value"]


# ---------------- admin auth ----------------
_sessions: dict[str, float] = {}
COOKIE = "am_admin"


class Login(BaseModel):
    password: str


@app.post("/api/admin/login")
async def login(body: Login, response: Response):
    if not ADMIN_PASSWORD:
        raise HTTPException(503, "Admin is disabled: set ADMIN_PASSWORD in .env and restart.")
    if not hmac.compare_digest(body.password.encode(), ADMIN_PASSWORD.encode()):
        await asyncio.sleep(1.5)  # slow down guessing
        raise HTTPException(401, "Wrong password.")
    token = secrets.token_urlsafe(32)
    _sessions[token] = time.time() + SESSION_HOURS * 3600
    response.set_cookie(COOKIE, token, httponly=True, samesite="strict", max_age=SESSION_HOURS * 3600, path="/")
    return {"ok": True}


def require_admin(request: Request):
    token = request.cookies.get(COOKIE)
    exp = _sessions.get(token or "")
    if not exp or exp < time.time():
        _sessions.pop(token or "", None)
        raise HTTPException(401, "Please sign in.")
    # cross-site form posts can't set custom headers; our own pages always do
    if request.method != "GET" and request.headers.get("x-airplanemode") != "1":
        raise HTTPException(403, "Missing request header.")
    return token


@app.post("/api/admin/logout")
async def logout(response: Response, token: str = Depends(require_admin)):
    _sessions.pop(token, None)
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


# ---------------- admin: Wi-Fi ----------------
class Scan(BaseModel):
    radio: str


class Upstream(BaseModel):
    radio: str
    ssid: str
    encryption: str
    key: str | None = None
    macaddr: str | None = None


class NewHotspot(BaseModel):
    radio: str
    ssid: str
    encryption: str
    key: str | None = None


class Hotspot(BaseModel):
    ssid: str
    encryption: str
    key: str | None = None
    disabled: bool = False


def _bust():
    _status_cache["value"] = None


@app.get("/api/admin/wifi")
async def wifi(_=Depends(require_admin)):
    ov = await router.overview()
    ov["upstream_status"] = await router.upstream_status()
    ov["backend"] = router.name
    return ov


@app.post("/api/admin/scan")
async def scan(body: Scan, _=Depends(require_admin)):
    return {"networks": await router.scan(body.radio)}


@app.post("/api/admin/upstream")
async def join(body: Upstream, _=Depends(require_admin)):
    await router.join(body.radio, body.ssid, body.encryption, body.key, body.macaddr)
    _bust()
    return {"ok": True}


@app.delete("/api/admin/upstream")
async def leave(_=Depends(require_admin)):
    await router.leave()
    _bust()
    return {"ok": True}


@app.post("/api/admin/hotspots")
async def add_hotspot(body: NewHotspot, _=Depends(require_admin)):
    return {"id": await router.add_hotspot(body.radio, body.ssid, body.encryption, body.key)}


@app.put("/api/admin/hotspots/{hid}")
async def save_hotspot(hid: str, body: Hotspot, _=Depends(require_admin)):
    await router.save_hotspot(hid, body.ssid, body.encryption, body.key, body.disabled)
    return {"ok": True}


@app.delete("/api/admin/hotspots/{hid}")
async def delete_hotspot(hid: str, _=Depends(require_admin)):
    await router.delete_hotspot(hid)
    return {"ok": True}


# ---------------- mock-only: a pretend hotel sign-in page ----------------
if BACKEND == "mock":
    @app.get("/api/mock/portal", response_class=HTMLResponse)
    async def mock_portal():
        return """<!doctype html><meta name=viewport content="width=device-width,initial-scale=1">
<title>Harbor Hotel Wi-Fi</title>
<body style="font-family:system-ui;max-width:420px;margin:40px auto;padding:0 16px;background:#f4f1ea;color:#222">
<h1 style="font-size:1.4rem">Welcome to Harbor Hotel</h1>
<p>(Pretend sign-in page from the mock router.) Accept the terms to get online.</p>
<form method=post><label><input type=checkbox required> I accept the terms of use</label>
<p><button style="padding:10px 18px;font-size:1rem">Connect</button></p></form></body>"""

    @app.post("/api/mock/portal")
    async def mock_portal_accept():
        router.clear_portal()
        _bust()
        return RedirectResponse("/?signed-in=1", status_code=303)


app.mount("/admin", StaticFiles(directory=HERE / "static", html=True), name="admin")

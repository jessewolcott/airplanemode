"""AirplaneMode admin service.

Public:
  GET  /api/status            internet state (online / captive / offline), how (ethernet / wifi),
                              sign-in link, what the box is doing (joining a saved network?)
  GET  /api/apps              the apps on the landing page (portal/apps.json minus the hidden ones)
  GET  /api/me                who's who: {known, id, name, avatar} for this device, + the avatar list
  PUT  /api/me                {name, avatar}               (X-AirplaneMode: 1 header required)
  DELETE /api/me                                           (forget this device's name)
Admin (password, cookie session):
  POST /api/admin/login       {password}
  POST /api/admin/logout
  GET  /api/admin/wifi        radios, hotspots (with keys), upstream config + live status,
                              saved networks, auto-join state, Ethernet status
  POST /api/admin/scan        {radio}                      -> nearby networks
  POST /api/admin/upstream    {radio, ssid, encryption, key, macaddr}   (connects + saves it)
  DELETE /api/admin/upstream                                (disconnects + pauses auto-join)
  POST /api/admin/autojoin    {enabled}
  DELETE /api/admin/networks/{ssid}                         (forget a saved network)
  POST /api/admin/hotspots    {radio, ssid, encryption, key}
  PUT  /api/admin/hotspots/{id} {ssid, encryption, key, disabled}
  DELETE /api/admin/hotspots/{id}
  GET  /api/admin/devices                                   (DHCP leases + paused state + who's who name)
  POST /api/admin/devices/{mac}/pause {paused}              (internet off for one device; box apps still work)
  GET  /api/admin/usage       data used per uplink network, and on the current one
  DELETE /api/admin/usage
  GET  /api/admin/vpn         home VPN settings (never the private key) + tunnel state
  PUT  /api/admin/vpn         {config: WireGuard .conf text, enabled}
  POST /api/admin/vpn/enabled {enabled}
  DELETE /api/admin/vpn
  GET  /api/admin/apps        every installed app, with "enabled" (on the menu or not)
  PUT  /api/admin/apps/{id}   {enabled}                    (hide/show an app on the menu)
  GET  /api/admin/session     200 if the caller's am_admin cookie is a live session (apps ask this)
UI:
  /admin/                     the admin panel (static)

Background supervisor (every SUPERVISE_INTERVAL seconds):
  - no uplink at all (no Ethernet, no Wi-Fi link) -> join the strongest saved network in range
  - no uplink for a while -> turn on the offline popup; any uplink -> turn it off immediately
"""
import asyncio
import hmac
import json
import logging
import os
import re
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from router import (UPSTREAM_SECURITY, VPN_FRESH, MockRouter, OpenWrtRouter, RouterError, check_key, check_mac,
                    check_ssid)
from vpn import full_tunnel, parse_wg_conf

log = logging.getLogger("airplanemode")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

HERE = Path(__file__).parent
BACKEND = os.getenv("ROUTER_BACKEND", "openwrt")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
PROBE_URL = os.getenv("PROBE_URL", "http://connectivitycheck.gstatic.com/generate_204")
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
SUPERVISE_INTERVAL = float(os.getenv("SUPERVISE_INTERVAL", "30"))
AUTOJOIN = os.getenv("AUTOJOIN", "1") == "1"
OFFLINE_PORTAL = os.getenv("OFFLINE_PORTAL", "1") == "1"
JOIN_GRACE = float(os.getenv("JOIN_GRACE", "45"))        # seconds to wait for a join to come up
PORTAL_AFTER = int(os.getenv("PORTAL_AFTER_TICKS", "2"))  # ticks with no uplink before the popup turns on
MAX_TRIES = 2                                              # failed joins before skipping a network
RETRY_AFTER = 600                                          # ...for this many seconds
SESSION_HOURS = 12

client = httpx.AsyncClient()
if BACKEND == "mock":
    router = MockRouter()
else:
    router = OpenWrtRouter(os.getenv("ROUTER_URL", "http://host.docker.internal/ubus"),
                           os.getenv("ROUTER_USER", "airplanemode"), os.getenv("ROUTER_PASSWORD", ""), client,
                           lan_ip=os.getenv("LAN_IP", "192.168.2.1"), http_port=os.getenv("HTTP_PORT", "8080"),
                           wan_iface=os.getenv("WAN_IFACE", "wan"))

# One change to the router at a time. The supervisor and admin requests share one ubus
# session, and "uci apply" applies everything staged in it, so interleaved changes could
# apply each other half-done.
router_lock = asyncio.Lock()


@asynccontextmanager
async def lifespan(_app):
    task = asyncio.create_task(_supervise_forever()) if SUPERVISE_INTERVAL > 0 else None
    yield
    if task:
        task.cancel()
    store.save_soon(every=0)  # keep data-use counts across restarts


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


@app.exception_handler(RouterError)
async def router_error(_req, exc):
    return JSONResponse({"detail": str(exc)}, status_code=400)


# ---------------- saved networks (on the box, in the admin-data volume) ----------------
class Store:
    def __init__(self, path):
        self.path = path
        self.data = {"networks": [], "paused": False}
        try:
            self.data.update(json.loads(path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning("could not read %s: %s", path, e)

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=1), encoding="utf-8")
            os.chmod(tmp, 0o600)
            tmp.replace(self.path)
        except OSError as e:
            log.warning("could not save %s: %s", self.path, e)

    @property
    def networks(self):
        return self.data["networks"]

    def remember(self, radio, ssid, encryption, key, macaddr):
        nets = [n for n in self.networks if n["ssid"] != ssid]
        nets.insert(0, {"ssid": ssid, "encryption": encryption, "key": key, "macaddr": macaddr,
                        "radio": radio, "last_used": time.time()})
        self.data["networks"] = nets[:50]
        self.save()

    def forget(self, ssid):
        before = len(self.networks)
        self.data["networks"] = [n for n in self.networks if n["ssid"] != ssid]
        self.save()
        return len(self.networks) != before

    def public(self):
        return [{"ssid": n["ssid"], "encryption": n["encryption"], "has_key": bool(n.get("key")),
                 "macaddr": n.get("macaddr"), "last_used": n.get("last_used")} for n in self.networks]

    # ---- data use per uplink network ("Ethernet" or an SSID) ----
    def add_usage(self, name, rx, tx):
        u = self.data.setdefault("usage", {})
        now = time.time()
        e = u.setdefault(name, {"rx": 0, "tx": 0, "first": now})
        e["rx"] += rx
        e["tx"] += tx
        e["last"] = now
        if len(u) > 100:  # keep the 100 most recent networks
            for old in sorted(u, key=lambda k: u[k].get("last", 0))[:len(u) - 100]:
                del u[old]
        self._dirty = True

    def save_soon(self, every=300):
        """Counters change every tick; write them to flash at most every few minutes."""
        if getattr(self, "_dirty", False) and time.monotonic() - getattr(self, "_saved_at", -1e9) >= every:
            self.save()
            self._dirty, self._saved_at = False, time.monotonic()


store = Store(DATA_DIR / "networks.json")


# ---------------- supervisor ----------------
class Supervisor:
    def __init__(self):
        self.joining = None        # ssid we're waiting on
        self.grace_until = 0.0
        self.fails = {}            # ssid -> (count, since)
        self.down_ticks = 0
        self.last = {}             # last tick summary (for the admin panel)
        self.counters = {}         # uplink -> last byte counters seen
        self.session = None        # data used on the current uplink since it came up

    async def count_usage(self, wifi):
        """Add the bytes that went over each uplink since the last tick to that network's total."""
        try:
            now = await router.uplink_counters()
        except RouterError:
            return
        for key in list(self.counters):
            if key not in now:
                del self.counters[key]  # link went down: start over when it's back
        for key, c in now.items():
            prev = self.counters.get(key)
            self.counters[key] = c
            name = "Ethernet" if key == "ethernet" else wifi.get("ssid") or (self.session or {}).get("name")
            if prev is None or not name:
                continue
            # counters restart from 0 when the device is recreated
            rx = c["rx"] - prev["rx"] if c["rx"] >= prev["rx"] else c["rx"]
            tx = c["tx"] - prev["tx"] if c["tx"] >= prev["tx"] else c["tx"]
            store.add_usage(name, rx, tx)
            primary = "Ethernet" if "ethernet" in now else name
            if name == primary:
                if not self.session or self.session["name"] != name:
                    self.session = {"name": name, "rx": 0, "tx": 0, "since": time.time()}
                self.session["rx"] += rx
                self.session["tx"] += tx
        if not now:
            self.session = None
        store.save_soon()

    async def links(self):
        eth = await router.ethernet_status()
        wifi = await router.upstream_status()
        return eth, wifi

    def _skippable(self, ssid, now):
        count, since = self.fails.get(ssid, (0, now))
        if count >= MAX_TRIES and now - since < RETRY_AFTER:
            return True
        if now - since >= RETRY_AFTER:
            self.fails.pop(ssid, None)
        return False

    async def _autojoin(self, now):
        if not AUTOJOIN or store.data.get("paused") or not store.networks:
            return None
        if now < self.grace_until:
            return f"waiting for {self.joining}"
        if self.joining:  # the last attempt didn't come up in time
            count, since = self.fails.get(self.joining, (0, now))
            self.fails[self.joining] = (count + 1, since)
            log.info("join of %r didn't come up (%d/%d)", self.joining, count + 1, MAX_TRIES)
            self.joining = None
        ov = await router.overview()
        radios = [r["id"] for r in ov["radios"] if not r["disabled"]]
        if not radios:
            return "no radios"
        up = ov.get("upstream") or {}
        radio = up.get("radio") if up.get("radio") in radios else store.networks[0].get("radio")
        radio = radio if radio in radios else radios[0]
        visible = {n["ssid"]: n for n in await router.scan(radio)}
        saved = [n for n in store.networks if n["ssid"] in visible and not self._skippable(n["ssid"], now)]
        if not saved:
            return "no saved network in range"
        best = max(saved, key=lambda n: (visible[n["ssid"]].get("signal") or -999, n.get("last_used") or 0))
        log.info("auto-joining %r on %s", best["ssid"], radio)
        await router.join(radio, best["ssid"], best["encryption"], best.get("key"), best.get("macaddr"))
        self.joining = best["ssid"]
        self.grace_until = now + JOIN_GRACE
        _bust()
        return f"joining {best['ssid']}"

    async def tick(self, now=None):
        async with router_lock:
            return await self._tick(now)

    async def _tick(self, now):
        now = time.monotonic() if now is None else now
        eth, wifi = await self.links()
        linked = bool(eth.get("connected") or wifi.get("connected"))
        action = None
        if linked:
            self.down_ticks = 0
            if wifi.get("connected") and self.joining:
                self.fails.pop(self.joining, None)
            self.joining, self.grace_until = None, 0.0
            if OFFLINE_PORTAL and await router.set_offline_portal(False):
                action = "offline popup off"
        else:
            self.down_ticks += 1
            action = await self._autojoin(now)
            # don't flip the popup on while a join is in progress
            if OFFLINE_PORTAL and self.down_ticks >= PORTAL_AFTER and now >= self.grace_until:
                if await router.set_offline_portal(True):
                    action = (action + "; " if action else "") + "offline popup on"
        if linked:
            await self.count_usage(wifi)
        elif self.counters:
            self.counters, self.session = {}, None
        self.last = {"linked": linked, "ethernet": eth.get("connected"), "wifi": wifi.get("connected"),
                     "action": action, "joining": self.joining}
        if action:
            log.info("supervisor: %s", action)
            _bust()
        return self.last


supervisor = Supervisor()


async def _supervise_forever():
    await asyncio.sleep(3)
    while True:
        try:
            await supervisor.tick()
        except Exception as e:  # never let the loop die
            log.warning("supervisor tick failed: %s", e)
        await asyncio.sleep(SUPERVISE_INTERVAL)


# ---------------- internet / captive portal ----------------
_status_cache = {"at": 0.0, "value": None}


def _bust():
    _status_cache["value"] = None


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


async def vpn_summary():
    cfg = await router.vpn_config()
    if not cfg:
        return {"configured": False}
    st = await router.vpn_status() if cfg["enabled"] else {"up": False}
    hs = st.get("handshake")
    age = int(time.time() - hs) if hs else None
    return {"configured": True, **cfg, "full_tunnel": full_tunnel(cfg["allowed_ips"]),
            "up": st.get("up"), "handshake_age": age,
            # None = can't tell (luci-proto-wireguard not installed)
            "connected": None if st.get("up") is None else bool(age is not None and age < VPN_FRESH),
            "rx": st.get("rx"), "tx": st.get("tx")}


@app.get("/api/status")
async def status(refresh: bool = False):
    now = time.monotonic()
    if refresh or not _status_cache["value"] or now - _status_cache["at"] > 10:
        try:
            eth, wifi = await supervisor.links()
        except RouterError:
            eth, wifi = {"connected": None}, {"connected": None}
        linked = eth.get("connected") or wifi.get("connected")
        # no uplink at all: don't probe (DNS may be answering with the offline popup address)
        internet, login_url = await probe() if linked or eth.get("connected") is None else ("offline", None)
        via = "ethernet" if eth.get("connected") else "wifi" if wifi.get("connected") else None
        try:
            vpn = await vpn_summary()
        except RouterError:
            vpn = {"configured": False}
        _status_cache.update(at=now, value={
            "vpn": {k: vpn.get(k) for k in ("configured", "enabled", "connected")},
            "internet": internet, "login_url": login_url, "via": via,
            "upstream": {"ssid": wifi.get("ssid"), "connected": wifi.get("connected"), "signal": wifi.get("signal")},
            "ethernet": {"connected": eth.get("connected")},
            "joining": supervisor.joining,
            "saved_networks": len(store.networks),
            "autojoin": AUTOJOIN and not store.data.get("paused"),
            "backend": router.name,
        })
    return _status_cache["value"]


# ---------------- admin auth ----------------
# Sessions live in memory: restarting the admin service signs everyone out.
_sessions: dict[str, float] = {}
_login_lock = asyncio.Lock()   # one password check at a time, so the delay can't be run in parallel
COOKIE = "am_admin"


class Login(BaseModel):
    password: str


@app.post("/api/admin/login")
async def login(body: Login, response: Response):
    if not ADMIN_PASSWORD:
        raise HTTPException(503, "Admin is disabled: set ADMIN_PASSWORD in .env and restart.")
    async with _login_lock:
        if not hmac.compare_digest(body.password.encode(), ADMIN_PASSWORD.encode()):
            await asyncio.sleep(1.5)  # slow down guessing
            raise HTTPException(401, "Wrong password.")
    now = time.time()
    for t in [t for t, exp in _sessions.items() if exp < now]:
        del _sessions[t]
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


@app.get("/api/admin/session")
async def session(_token: str = Depends(require_admin)):
    """200 if the caller's cookie is a live admin session. Apps (Drop) ask this, passing the
    cookie the browser sent them, to know whether the person using them is the admin."""
    return {"ok": True}


# ---------------- apps on the landing page ----------------
# The installed apps are listed in portal/apps.json (mounted read-only). The admin can hide an
# app from the menu; hidden ids are kept with the other settings. Hidden means "not on the menu
# and not offered by other apps" (Drop's "Play in..."); the app's own address still works.
APPS_FILE = Path(os.getenv("APPS_FILE", "/config/apps.json"))


def model_on():
    """Whether the shared model (and so the apps that need it) runs: the "llm" compose profile.
    Compose passes COMPOSE_PROFILES (maybe empty); unset means not run by compose (tests): all on."""
    profiles = os.getenv("COMPOSE_PROFILES")
    return profiles is None or "llm" in re.split(r"[,\s]+", profiles)


def installed_apps():
    try:
        apps = json.loads(APPS_FILE.read_text(encoding="utf-8"))
        apps = [a for a in apps if isinstance(a, dict) and isinstance(a.get("id"), str)]
        # Model apps don't run without the model (GL.iNet lite profile), so they aren't installed.
        return apps if model_on() else [a for a in apps if not a.get("uses_llm")]
    except FileNotFoundError:
        return []
    except Exception as e:
        log.warning("could not read %s: %s", APPS_FILE, e)
        return []


def hidden_apps():
    return set(store.data.get("hidden_apps", []))


@app.get("/api/apps")
async def apps_on_menu():
    hidden = hidden_apps()
    return [a for a in installed_apps() if a["id"] not in hidden]


@app.get("/api/admin/apps")
async def apps_admin(_token: str = Depends(require_admin)):
    hidden = hidden_apps()
    return {"apps": [{**a, "enabled": a["id"] not in hidden} for a in installed_apps()]}


class AppSwitch(BaseModel):
    enabled: bool


@app.put("/api/admin/apps/{app_id}")
async def app_switch(app_id: str, body: AppSwitch, _token: str = Depends(require_admin)):
    if app_id not in {a["id"] for a in installed_apps()}:
        raise HTTPException(404, "No such app.")
    hidden = hidden_apps()
    hidden.discard(app_id) if body.enabled else hidden.add(app_id)
    store.data["hidden_apps"] = sorted(hidden)
    store.save()
    return {"ok": True, "enabled": body.enabled}


# ---------------- who's who: a name and an emoji per device, no accounts ----------------
# A device is recognised by its MAC (its IP looked up in the DHCP leases), so every browser on
# a phone gets the same name, and by a long-lived cookie as well, for when there's no lease
# (dev stack, a laptop on Ethernet with a fixed IP) or the phone's random MAC changes.
# people: {pid: {name, avatar, updated}}; who_links: {"mac:<mac>" | "tok:<cookie>": pid}.
# Apps get "id": a hash of pid, so they never see MACs.
WHO_COOKIE = "am_who"
AVATARS = ["🦊", "🐼", "🐙", "🦉", "🐢", "🦁", "🐧", "🐝", "🦋", "🐳", "🦜", "🐨",
           "🌵", "🌻", "🍄", "🌙", "⭐", "🔥", "🌊", "⛰️", "✈️", "🚂", "⛵", "🎈"]
MAX_PEOPLE = 300
NAME_MAX = 20
_leases = {"at": -1e9, "by_ip": {}}


class Me(BaseModel):
    name: str
    avatar: str


def check_name(name):
    name = " ".join(name.split())  # trims and folds runs of spaces, tabs, newlines
    if not name or any(ord(ch) < 32 or 0x7f <= ord(ch) < 0xa0 for ch in name):
        raise HTTPException(400, "Pick a name.")
    if len(name) > NAME_MAX:
        raise HTTPException(400, f"Keep the name to {NAME_MAX} characters.")
    return name


async def mac_for(ip):
    """The MAC holding a DHCP lease for this IP, or None. Leases are cached for 10 s; a miss
    refreshes at most every 3 s, so a phone that just joined is found quickly."""
    if not ip:
        return None
    age = time.monotonic() - _leases["at"]
    if age > 10 or (ip not in _leases["by_ip"] and age > 3):
        try:
            devs = await router.devices()
            _leases["by_ip"] = {d["ip"]: d["mac"] for d in devs if d.get("ip") and d.get("mac")}
        except RouterError:
            _leases["by_ip"] = {}
        _leases["at"] = time.monotonic()
    return _leases["by_ip"].get(ip)


def _who_token(request):
    tok = request.cookies.get(WHO_COOKIE) or ""
    return tok if re.fullmatch(r"[A-Za-z0-9_-]{20,64}", tok) else None


def _person_id(pid):
    secret = store.data.get("who_secret")
    if not secret:  # made once per box, so ids stay the same across restarts
        secret = store.data["who_secret"] = secrets.token_hex(16)
        store.save()
    return hmac.new(secret.encode(), pid.encode(), "sha256").hexdigest()[:16]


async def _who(request, response):
    """(pid or None, link keys for this caller). Sets the cookie if the browser has none."""
    tok = _who_token(request)
    if not tok:
        tok = secrets.token_urlsafe(24)
        response.set_cookie(WHO_COOKIE, tok, httponly=True, samesite="lax", max_age=365 * 86400, path="/")
    mac = await mac_for(client_ip(request))
    keys = ([f"mac:{mac}"] if mac else []) + [f"tok:{tok}"]
    links, people = store.data.setdefault("who_links", {}), store.data.setdefault("people", {})
    pid = next((links[k] for k in keys if links.get(k) in people), None)
    if pid and any(links.get(k) != pid for k in keys):  # new browser or new random MAC: same person
        links.update({k: pid for k in keys})
        store.save()
    return pid, keys


def _me(pid):
    p = store.data.get("people", {}).get(pid) if pid else None
    if not p:
        return {"known": False, "id": None, "name": None, "avatar": None}
    return {"known": True, "id": _person_id(pid), "name": p["name"], "avatar": p["avatar"]}


def _require_header(request):
    if request.headers.get("x-airplanemode") != "1":  # blocks cross-site form posts
        raise HTTPException(403, "Missing request header.")


@app.get("/api/me")
async def get_me(request: Request, response: Response):
    """Who is asking: {known, id, name, avatar}. Apps' backends may call this on
    http://admin:8000 passing on the request's X-Forwarded-For and the am_who cookie."""
    pid, _ = await _who(request, response)
    return {**_me(pid), "avatars": AVATARS}


@app.put("/api/me")
async def put_me(body: Me, request: Request, response: Response):
    _require_header(request)
    name = check_name(body.name)
    if body.avatar not in AVATARS:
        raise HTTPException(400, "Pick one of the pictures.")
    pid, keys = await _who(request, response)
    people, links = store.data["people"], store.data["who_links"]
    pid = pid or secrets.token_hex(8)
    people[pid] = {"name": name, "avatar": body.avatar, "updated": time.time()}
    links.update({k: pid for k in keys})
    if len(people) > MAX_PEOPLE:  # forget the people who haven't changed their name for longest
        for old in sorted(people, key=lambda p: people[p].get("updated", 0))[:len(people) - MAX_PEOPLE]:
            del people[old]
        store.data["who_links"] = {k: v for k, v in links.items() if v in people}
    store.save()
    return _me(pid)


@app.delete("/api/me")
async def delete_me(request: Request, response: Response):
    _require_header(request)
    pid, _ = await _who(request, response)
    if pid:
        store.data["people"].pop(pid, None)
        store.data["who_links"] = {k: v for k, v in store.data["who_links"].items() if v != pid}
        store.save()
    return _me(None)


def person_for_mac(mac):
    p = store.data.get("people", {}).get(store.data.get("who_links", {}).get(f"mac:{mac}", ""))
    return {"name": p["name"], "avatar": p["avatar"]} if p else None


# ---------------- admin: Wi-Fi ----------------
class Scan(BaseModel):
    radio: str


class Upstream(BaseModel):
    radio: str
    ssid: str
    encryption: str
    key: str | None = None
    macaddr: str | None = None


class AutoJoin(BaseModel):
    enabled: bool


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


@app.get("/api/admin/wifi")
async def wifi(_=Depends(require_admin)):
    ov = await router.overview()
    ov["upstream_status"] = await router.upstream_status()
    ov["ethernet"] = await router.ethernet_status()
    ov["saved"] = store.public()
    ov["autojoin"] = {"enabled": AUTOJOIN, "paused": bool(store.data.get("paused")),
                      "joining": supervisor.joining, "last": supervisor.last}
    ov["offline_popup"] = await router.portal_mode()
    ov["backend"] = router.name
    return ov


@app.post("/api/admin/scan")
async def scan(body: Scan, _=Depends(require_admin)):
    return {"networks": await router.scan(body.radio)}


@app.post("/api/admin/upstream")
async def join(body: Upstream, _=Depends(require_admin)):
    # validate before touching anything, so a typo doesn't get saved
    ssid = check_ssid(body.ssid)
    key = check_key(body.encryption, body.key, UPSTREAM_SECURITY)
    mac = check_mac(body.macaddr)
    async with router_lock:
        await router.join(body.radio, ssid, body.encryption, key, mac)
    store.remember(body.radio, ssid, body.encryption, key, mac)
    store.data["paused"] = False
    store.save()
    supervisor.joining, supervisor.grace_until = ssid, time.monotonic() + JOIN_GRACE
    supervisor.fails.pop(ssid, None)
    _bust()
    return {"ok": True}


@app.delete("/api/admin/upstream")
async def leave(_=Depends(require_admin)):
    async with router_lock:
        await router.leave()
    store.data["paused"] = True  # otherwise auto-join would reconnect straight away
    store.save()
    supervisor.joining, supervisor.grace_until = None, 0.0
    _bust()
    return {"ok": True}


@app.post("/api/admin/autojoin")
async def set_autojoin(body: AutoJoin, _=Depends(require_admin)):
    store.data["paused"] = not body.enabled
    store.save()
    _bust()
    return {"ok": True}


@app.delete("/api/admin/networks/{ssid:path}")  # :path, since SSIDs may contain "/"
async def forget(ssid: str, _=Depends(require_admin)):
    if not store.forget(ssid):
        raise HTTPException(404, "No saved network with that name.")
    return {"ok": True}


@app.post("/api/admin/hotspots")
async def add_hotspot(body: NewHotspot, _=Depends(require_admin)):
    async with router_lock:
        return {"id": await router.add_hotspot(body.radio, body.ssid, body.encryption, body.key)}


@app.put("/api/admin/hotspots/{hid}")
async def save_hotspot(hid: str, body: Hotspot, _=Depends(require_admin)):
    async with router_lock:
        await router.save_hotspot(hid, body.ssid, body.encryption, body.key, body.disabled)
    return {"ok": True}


@app.delete("/api/admin/hotspots/{hid}")
async def delete_hotspot(hid: str, _=Depends(require_admin)):
    async with router_lock:
        await router.delete_hotspot(hid)
    return {"ok": True}


# ---------------- admin: devices ----------------
class Pause(BaseModel):
    paused: bool


def client_ip(request: Request):
    # Caddy sets X-Forwarded-For to the real client address (it ignores one sent by the client).
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[-1].strip() or (request.client.host if request.client else None)


@app.get("/api/admin/devices")
async def devices(request: Request, _=Depends(require_admin)):
    me = client_ip(request)
    return {"devices": [dict(d, you=bool(me and d.get("ip") == me), person=person_for_mac(d["mac"]))
                        for d in await router.devices()]}


@app.post("/api/admin/devices/{mac}/pause")
async def pause_device(mac: str, body: Pause, _=Depends(require_admin)):
    async with router_lock:
        await router.set_device_paused(mac, body.paused)
    return {"ok": True}


# ---------------- admin: data use ----------------
@app.get("/api/admin/usage")
async def usage(_=Depends(require_admin)):
    u = store.data.get("usage", {})
    nets = sorted(({"name": k, **v} for k, v in u.items()), key=lambda e: -e.get("last", 0))
    return {"current": supervisor.session, "linked": bool(supervisor.last.get("linked")), "networks": nets}


@app.delete("/api/admin/usage")
async def reset_usage(_=Depends(require_admin)):
    store.data["usage"] = {}
    if supervisor.session:
        supervisor.session.update(rx=0, tx=0, since=time.time())
    store.save()
    return {"ok": True}


# ---------------- admin: home VPN ----------------
class VpnConfig(BaseModel):
    config: str
    enabled: bool = True


class VpnEnabled(BaseModel):
    enabled: bool


@app.get("/api/admin/vpn")
async def get_vpn(_=Depends(require_admin)):
    return await vpn_summary()


@app.put("/api/admin/vpn")
async def put_vpn(body: VpnConfig, _=Depends(require_admin)):
    cfg = parse_wg_conf(body.config)  # validate before touching the router
    async with router_lock:
        await router.set_vpn(cfg, body.enabled)
    _bust()
    return {"ok": True}


@app.post("/api/admin/vpn/enabled")
async def vpn_enabled(body: VpnEnabled, _=Depends(require_admin)):
    async with router_lock:
        await router.set_vpn_enabled(body.enabled)
    _bust()
    return {"ok": True}


@app.delete("/api/admin/vpn")
async def delete_vpn(_=Depends(require_admin)):
    async with router_lock:
        await router.remove_vpn()
    _bust()
    return {"ok": True}


# ---------------- mock-only: pretend hotel sign-in page and travel controls ----------------
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

    class MockWorld(BaseModel):
        ethernet: bool | None = None
        in_range: list[str] | None = None

    @app.post("/api/mock/world")
    async def mock_world(body: MockWorld):
        """Simulate travel: plug/unplug Ethernet, change which networks are in range."""
        if body.ethernet is not None:
            router.ethernet = body.ethernet
        if body.in_range is not None:
            router.in_range = set(body.in_range)
        result = await supervisor.tick()
        _bust()
        return {"ethernet": router.ethernet, "in_range": sorted(router.in_range),
                "offline_popup": router.portal_on, "tick": result}


app.mount("/admin", StaticFiles(directory=HERE / "static", html=True), name="admin")

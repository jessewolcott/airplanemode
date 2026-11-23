"""Who's who (platform decision 0021): ask the admin service who is behind a request.

`await whois(request)` returns {"id", "name", "avatar"} for a person who has picked a name on
the landing page, or None (no name yet, the admin service is down, or ADMIN_URL is empty). It
never raises and never waits long: a game must work without it.

It passes on only what the admin service needs: the X-Forwarded-For header Caddy set and the
am_who cookie. Answers are cached for CACHE_SECONDS per (address, cookie), failures too.
Names are trust-on-the-hotspot: use them to label seats, never to authorise anything.
"""
import os
import re
import time

import httpx

CACHE_SECONDS = 30.0
TIMEOUT = 1.5
_COOKIE = re.compile(r"[A-Za-z0-9_-]{20,64}")
_cache = {}
_client = None


def admin_url():
    return os.getenv("ADMIN_URL", "http://admin:8000").rstrip("/")


async def whois(request):
    base = admin_url()
    if not base:
        return None
    fwd = request.headers.get("x-forwarded-for", "")
    cookie = request.cookies.get("am_who") or ""
    if not _COOKIE.fullmatch(cookie):
        cookie = ""
    key = (fwd, cookie)
    hit = _cache.get(key)
    now = time.monotonic()
    if hit and now - hit[0] < CACHE_SECONDS:
        return hit[1]
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=TIMEOUT)
    headers = {}
    if fwd:
        headers["X-Forwarded-For"] = fwd
    if cookie:
        headers["Cookie"] = f"am_who={cookie}"
    result = None
    try:
        r = await _client.get(f"{base}/api/me", headers=headers)
        d = r.json() if r.status_code == 200 else {}
        if d.get("known") and isinstance(d.get("id"), str) and isinstance(d.get("name"), str):
            result = {"id": d["id"], "name": d["name"][:20], "avatar": str(d.get("avatar") or "")[:8] or None}
    except Exception:
        result = None
    if len(_cache) > 1000:
        _cache.clear()
    _cache[key] = (now, result)
    return result


def forget_cache():
    _cache.clear()

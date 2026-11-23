"""amrooms tests.  Run: python sdk/rooms/tests/test_amrooms.py"""
import asyncio
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import amrooms  # noqa: E402
from amrooms import Room, RoomError, Rooms, identity  # noqa: E402


def raises(status, fn, *a, **kw):
    try:
        fn(*a, **kw)
    except RoomError as e:
        assert e.status == status, (e.status, e.message)
        return e.message
    raise AssertionError("no RoomError")


class Table(Room):
    max_seats = 3
    away_after = 0.3

    def __init__(self, code, seed=None, public=True):
        super().__init__(code, seed)
        self.public = public
        self.moves = []
        self.due = None             # when the next "bot move" is due

    def plan(self, now):
        if self.due is None:
            return None
        return (self.due, lambda: (self.moves.append(time.monotonic()), setattr(self, "due", None)))


def run(coro):
    return asyncio.run(coro)


def test_seats_and_tokens():
    async def go():
        r = Table("ABCD")
        a = r.join("  Ann   Lee ")
        assert r.seats[0].name == "Ann Lee" and r.host is r.seats[0] and r.version == 2
        b = r.join("ann lee")                                   # names stay unique
        assert r.seats[1].name == "ann lee 2"
        assert r.join(None, token=b) == b and len(r.seats) == 2  # same seat back
        r.add_bot("hard")
        assert r.seats[2].level == "hard" and not r.seats[2].human
        raises(409, r.join, "Cy")                                # full
        raises(400, r.add_bot, "silly")
        raises(409, r.remove_seat, 0)                            # the host stays
        raises(400, r.remove_seat, True)
        raises(400, r.remove_seat, 1, people=False)              # people stay when the app says so
        r.set_bot_level(2, "easy")
        raises(400, r.set_bot_level, 1, "easy")
        r.remove_seat(2)
        raises(403, r.need_host, b)
        raises(403, r.need_seat, "nope")
        assert r.need_host(a) == 0
        r.game = object()                                        # a game starts: seats fixed
        raises(409, r.join, "Late")
        raises(409, r.add_bot)
        raises(409, r.remove_seat, 1)
        assert r.join(None, token=a) == a                        # but your own seat comes back
    run(go())


def test_leaving_and_host_handover():
    async def go():
        r = Table("ABCD")
        a, b = r.join("A"), r.join("B")
        r.leave(a)                                               # lobby: the seat goes
        assert [s.name for s in r.seats] == ["B"] and r.host.name == "B"
        a = r.join("A")
        r.game = object()
        r.leave(b)                                               # game: the seat stays, left
        assert r.seats[0].left and r.host.name == "A" and r.away(0)
        assert r.seen(b) == 0 and r.seats[0].left                # a poll in flight doesn't undo it
        raises(409, r.need_present, b)
        r.leave(a)
        assert r.empty() and r.host is None and not r.anyone_here()
        assert r.join(None, token=b) == b and r.host.name == "B" and not r.seats[0].left
        r.seats[1].make_bot("normal")                            # gone for good: a bot now
        assert r.seat_of(a) is None and r.seats[1].level == "normal"
    run(go())


def test_presence():
    async def go():
        r = Table("ABCD")
        a = r.join("A")
        assert r.anyone_here() and not r.away(0)
        r.seats[0].seen -= 1
        assert r.away(0) and r.idle(0) and not r.anyone_here()
        r.seen(a)
        assert not r.away(0)
        r.seats[0].auto = True                                   # on autopilot until they act
        assert r.away(0) and not r.idle(0)
        info = r.seat_info(0, me=0)
        assert info["you"] and info["host"] and info["away"] and info["auto"] and not info["bot"]
    run(go())


def test_who_rejoins_from_another_browser():
    async def go():
        r = Table("ABCD")
        sam = {"id": "p1", "name": "Sam", "avatar": "🦊"}
        t = r.join("typed", who=sam)
        assert r.seats[0].name == "typed" and r.seats[0].avatar == "🦊" and r.seats[0].person == "p1"
        assert r.join(None, who=sam) == t and len(r.seats) == 1   # no token, same person: same seat
        assert r.seats[0].name == "Sam"
        r.join(None, who={"id": "p2", "name": "Sam", "avatar": "🐙"})
        assert r.seats[1].name == "Sam 2"
    run(go())


def test_long_poll():
    async def go():
        r = Table("ABCD")
        t0 = time.monotonic()
        await r.wait(r.version - 1, 5)                           # already moved: at once
        assert time.monotonic() - t0 < 0.1
        waiter = asyncio.create_task(r.wait(r.version, 5))
        await asyncio.sleep(0.05)
        r.changed()
        await asyncio.wait_for(waiter, 1)
        t0 = time.monotonic()
        await r.wait(r.version, 0.1)                             # nothing happens: times out
        assert 0.09 < time.monotonic() - t0 < 1
    run(go())


def test_driver():
    async def go():
        r = Table("ABCD")
        r.join("A")
        r.game = object()
        r.due = time.monotonic() + 0.15
        v = r.version
        r.ensure_driver()
        await asyncio.sleep(0.05)
        assert not r.moves
        await asyncio.sleep(0.25)
        assert len(r.moves) == 1 and r.version == v + 1          # ran once, and woke the pollers
        r.due = time.monotonic()                                 # due now: picked up on the next change
        r.changed()
        await asyncio.sleep(0.05)
        assert len(r.moves) == 2
        r.close()
        await asyncio.sleep(0.1)
        assert r.task.done()
    run(go())


def test_a_wake_up_that_does_nothing_keeps_the_version():
    async def go():
        class Wake(Table):
            def plan(self, now):
                if len(self.moves) < 3:
                    return (now, lambda: self.moves.append(1) or False)
                return None
        r = Wake("ABCD")
        r.game = object()
        v = r.version
        r.ensure_driver()
        await asyncio.sleep(0.05)
        assert len(r.moves) == 3 and r.version == v
        r.close()
    run(go())


def test_driver_survives_a_bad_action():
    async def go():
        class Bad(Table):
            def plan(self, now):
                if not self.moves:
                    return (now, lambda: (self.moves.append(1), 1 / 0))
                return super().plan(now)
        r = Bad("ABCD")
        r.game = object()
        r.ensure_driver()
        await asyncio.sleep(0.05)
        assert not r.task.done()                                 # logged, still driving
        r.close()
    run(go())


def test_registry():
    async def go():
        rooms = Rooms(Table, max_rooms=3, empty_grace=60)
        lobby = rooms.create()
        lobby.join("L")
        live = []
        for _ in range(2):
            x = rooms.create()
            x.join("P")
            x.game = object()
            live.append(x)
        third = rooms.create()                                   # full: the lobby goes, not a game
        third.join("T")
        assert lobby.code not in rooms.rooms and lobby.closed and all(x.code in rooms.rooms for x in live)
        live.append(rooms.create())
        live[-1].join("Q")
        live[-1].game = object()
        raises(503, rooms.create)                                # every room is a game in progress
        assert rooms.get(live[0].code.lower()) is live[0]
        raises(404, rooms.get, "ZZZZ")
        assert rooms.find("ZZZZ") is None
        # open lobbies: public, not started, room left, someone there
        rooms.discard(live[2])
        hidden = rooms.create(public=False)
        hidden.join("H")
        assert rooms.open_lobbies() == []
        open_ = rooms.create.__self__.room_class("OPEN")
        rooms.rooms["OPEN"] = open_
        open_.join("O")
        assert [r.code for r in rooms.open_lobbies()] == ["OPEN"]
        assert open_.summary() == {"code": "OPEN", "host": "O", "players": 1, "humans": 1, "max": 3}
        # sweeping: empty rooms after the grace period, idle rooms, closed rooms
        tok = open_.seats[0].token
        open_.leave(tok)
        rooms.sweep()
        assert "OPEN" not in rooms.rooms                         # (the last seat left a lobby: empty)
        live[0].leave(live[0].seats[0].token)                    # left a game: kept for the grace period
        rooms.sweep()
        assert live[0].code in rooms.rooms
        rooms.sweep(time.monotonic() + 61)
        assert live[0].code not in rooms.rooms
        rooms.idle = -1
        rooms.sweep()
        assert rooms.rooms == {}
    run(go())


def test_sweep_starts_drivers():
    async def go():
        rooms = Rooms(Table)
        r = rooms.create()
        r.join("A")
        r.game = object()
        r.due = time.monotonic()
        assert r.task is None
        rooms.sweep()
        await asyncio.sleep(0.05)
        assert r.moves
        r.close()
    r = Rooms(Table).create()
    r.game = object()
    r.ensure_driver()                                            # off the loop: no error, no task
    assert r.task is None
    run(go())


def test_whois():
    seen = []

    def admin(request):
        seen.append(dict(request.headers))
        if request.headers.get("x-forwarded-for") == "192.168.2.163":
            return httpx.Response(200, json={"known": True, "id": "abc123", "name": "Sam", "avatar": "🦊"})
        if request.headers.get("x-forwarded-for") == "10.0.0.9":
            return httpx.Response(500)
        return httpx.Response(200, json={"known": False, "id": None, "name": None, "avatar": None})

    class Req:
        def __init__(self, fwd, cookie=None):
            self.headers = {"x-forwarded-for": fwd} if fwd else {}
            self.cookies = {"am_who": cookie} if cookie else {}

    async def go():
        import os
        os.environ["ADMIN_URL"] = "http://admin.test"
        identity.forget_cache()
        identity._client = httpx.AsyncClient(transport=httpx.MockTransport(admin))
        cookie = "x" * 32
        assert await amrooms.whois(Req("192.168.2.163", cookie)) == {"id": "abc123", "name": "Sam", "avatar": "🦊"}
        assert seen[-1]["cookie"] == f"am_who={cookie}" and seen[-1]["x-forwarded-for"] == "192.168.2.163"
        await amrooms.whois(Req("192.168.2.163", cookie))
        assert len(seen) == 1                                    # cached
        await amrooms.whois(Req("192.168.2.140", "bad cookie; other=1"))
        assert "cookie" not in seen[-1]                          # a malformed cookie isn't passed on
        assert await amrooms.whois(Req("192.168.2.140")) is None  # no name yet
        assert await amrooms.whois(Req("10.0.0.9")) is None       # admin error: None, no raise
        identity._client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: 1 / 0))
        identity.forget_cache()
        assert await amrooms.whois(Req("192.168.2.163")) is None  # admin down: None
        os.environ["ADMIN_URL"] = ""
        assert await amrooms.whois(Req("192.168.2.163")) is None  # switched off
        os.environ.pop("ADMIN_URL")
    run(go())


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok  ", name)

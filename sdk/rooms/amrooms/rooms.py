"""Rooms for multiplayer apps on the hotspot: seats, tokens, presence, long-poll and a driver.

Everything is in memory. A room has a short code, seats (people and bots), a host and at most
one game. The app subclasses Room for its rules and keeps its own HTTP routes; this module only
knows about seats, never about cards or moves.

- People hold a secret seat token (kept in the browser), so a refresh or a locked phone gets the
  same seat back. With who's who (identity.whois) a person also gets their seat back from
  another browser on the same device.
- `changed()` moves the room's version and wakes every long-poll (`await room.wait(since)`).
- `seen(token)` marks a person as here; it never undoes a leave (the page's own poll is often in
  flight when it posts leave). Only `join` with the token, or the same person, takes it back.
- Leaving a lobby frees the seat. Leaving a game marks the seat `left`; the app decides who plays
  it. The host role passes to the next person still here.
- One driver task per room with a game: it asks `plan(now)` for the next timed action
  `(when, action)` and runs `action()` under the room lock when it's due, then `changed()`
  (unless the action returns False).
  It re-plans on every change and at least every MAX_NAP seconds, because presence (people
  going away, coming back) changes with time, not events.
"""
import asyncio
import logging
import os
import random
import secrets
import time

log = logging.getLogger("amrooms")

CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ"      # no I, L, O: easy to read out loud
MAX_NAP = 2.0


class RoomError(Exception):
    """Something the person can fix or should know: `status` is the HTTP status to answer with."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


def clean_name(name, fallback="Traveller"):
    name = " ".join(str(name or "").split())[:20]
    return "".join(ch for ch in name if ch.isprintable()) or fallback


class Seat:
    __slots__ = ("kind", "name", "avatar", "person", "level", "token", "seen", "left", "auto")

    def __init__(self, kind, name, level=None, avatar=None, person=None):
        self.kind = kind                    # human | bot
        self.name = name
        self.avatar = avatar                # an emoji from who's who, or None
        self.person = person                # who's who id, or None
        self.level = level                  # bots: easy / normal / hard
        self.token = secrets.token_urlsafe(18) if kind == "human" else None
        self.seen = time.monotonic()
        self.left = False                   # pressed Leave during a game
        self.auto = False                   # the app put a bot on this person's seat

    @property
    def human(self):
        return self.kind == "human"

    def make_bot(self, level="normal"):
        """A person gone for good: the seat becomes a bot."""
        self.kind, self.token, self.level, self.person = "bot", None, level, None
        self.left = self.auto = False


class Room:
    # Override in the app's subclass.
    max_seats = 4
    bot_levels = ("easy", "normal", "hard")
    bot_names = ("Ada", "Basil", "Coco", "Dex", "Esme", "Fitz", "Gigi", "Hugo", "Ines", "Juno")
    away_after = 60.0                       # seconds without a request: counts as away
    poll_wait = 20.0                        # seconds a long-poll waits for a change

    def __init__(self, code, seed=None):
        self.code = code
        self.seats = []
        self.host = None                    # a Seat
        self.public = True                  # listed on the app's home screen while in the lobby
        self.game = None
        self.version = 1
        self._event = asyncio.Event()
        self.lock = asyncio.Lock()
        self.rng = random.Random(seed)
        self.touched = self.changed_at = time.monotonic()
        self.task = None
        self.closed = False

    # ---------------- hooks for the app ----------------
    def playing(self):
        """A game is in progress (seats are fixed). Override when a finished game stays in
        `self.game` (for the results screen)."""
        return self.game is not None

    def plan(self, now):
        """The next timed action: (when, action) with `action()` a callable, or None.
        The driver calls `action()` under the lock once `when` has passed, then `changed()`,
        unless the action returns False (nothing happened)."""
        return None

    # ---------------- versions and long-poll ----------------
    def changed(self):
        self.version += 1
        self.touched = self.changed_at = time.monotonic()
        old, self._event = self._event, asyncio.Event()
        old.set()

    async def wait(self, since, timeout=None):
        """Return when the version differs from `since`, or after `timeout` (poll_wait) s."""
        if since != self.version:
            return
        event = self._event
        try:
            await asyncio.wait_for(event.wait(), self.poll_wait if timeout is None else timeout)
        except asyncio.TimeoutError:
            pass

    def close(self):
        self.closed = True
        if self.task and not self.task.done():
            self.task.cancel()
        self.changed()

    # ---------------- seats ----------------
    def seat_of(self, token):
        if not token:
            return None
        for i, s in enumerate(self.seats):
            if s.token and secrets.compare_digest(s.token, str(token)):
                return i
        return None

    def need_seat(self, token):
        i = self.seat_of(token)
        if i is None:
            raise RoomError(403, "You're not at this table.")
        return i

    def need_host(self, token):
        i = self.need_seat(token)
        if self.seats[i] is not self.host:
            raise RoomError(403, "Only the host can do that.")
        return i

    def need_present(self, token):
        """A seat whose person hasn't left (moves and rematches need one)."""
        i = self.need_seat(token)
        if self.seats[i].left:
            raise RoomError(409, "You left this game. Join it again to play.")
        return i

    def is_host(self, i):
        return i is not None and self.seats[i] is self.host

    def humans(self):
        return [s for s in self.seats if s.human]

    def seen(self, token):
        """This person is still around. Never undoes a leave or an autopilot."""
        i = self.seat_of(token)
        if i is not None:
            self.seats[i].seen = time.monotonic()
        self.touched = time.monotonic()
        return i

    def away(self, i, now=None):
        s = self.seats[i]
        if not s.human:
            return False
        return s.left or s.auto or (now or time.monotonic()) - s.seen > self.away_after

    def idle(self, i, now=None):
        """No request for away_after seconds (whatever the seat's flags say)."""
        s = self.seats[i]
        return s.human and (now or time.monotonic()) - s.seen > self.away_after

    def anyone_here(self, now=None):
        return any(s.human and not self.away(i, now) for i, s in enumerate(self.seats))

    def empty(self):
        """No person still at the table (everyone left or only bots)."""
        return not any(s.human and not s.left for s in self.seats)

    def join(self, name=None, token=None, who=None):
        """A seat for this person; returns its token. Your seat back if the token is yours or
        who's who says it's you (another browser on your device): that also takes it back from
        a leave or an autopilot. Otherwise a new seat, in the lobby only."""
        i = self.seat_of(token)
        if i is None and who and who.get("id"):
            i = next((k for k, s in enumerate(self.seats) if s.human and s.person == who["id"]), None)
        if i is not None:
            s = self.seats[i]
            s.left = s.auto = False
            s.seen = time.monotonic()
            if who:
                s.person, s.avatar = who.get("id") or s.person, who.get("avatar") or s.avatar
            if name or (who and who.get("name")):
                s.name = self._unique(clean_name(name or who["name"], s.name), s)
            if self.host is None:
                self.host = s
            self.changed()
            return s.token
        if self.game is not None:
            raise RoomError(409, "That game has already started.")
        if len(self.seats) >= self.max_seats:
            raise RoomError(409, f"That table is full ({self.max_seats} seats).")
        who = who or {}
        seat = Seat("human", self._unique(clean_name(name or who.get("name"))),
                    avatar=who.get("avatar"), person=who.get("id"))
        self.seats.append(seat)
        if self.host is None:
            self.host = seat
        self.changed()
        return seat.token

    def _unique(self, name, me=None):
        taken = {s.name.lower() for s in self.seats if s is not me}
        if name.lower() not in taken:
            return name
        n = 2
        while f"{name} {n}".lower() in taken:
            n += 1
        return f"{name} {n}"

    def rename(self, i, name):
        s = self.seats[i]
        s.name = self._unique(clean_name(name, s.name), s)
        self.changed()

    def add_bot(self, level="normal"):
        if self.playing():
            raise RoomError(409, "The game has started.")
        if level not in self.bot_levels:
            raise RoomError(400, "Unknown difficulty.")
        if len(self.seats) >= self.max_seats:
            raise RoomError(409, f"The table is full ({self.max_seats} seats).")
        taken = {s.name for s in self.seats}
        free = [n for n in self.bot_names if n not in taken] or [f"Bot {len(self.seats) + 1}"]
        self.seats.append(Seat("bot", self.rng.choice(free), level=level))
        self.changed()

    def seat_arg(self, i):
        if not isinstance(i, int) or isinstance(i, bool) or not 0 <= i < len(self.seats):
            raise RoomError(400, "No such seat.")
        return i

    def remove_seat(self, i, people=True):
        """Host action in the lobby: drop a bot (or, with people=True, a person) from the table."""
        i = self.seat_arg(i)
        if self.playing():
            raise RoomError(409, "The game has started.")
        s = self.seats[i]
        if s is self.host:
            raise RoomError(409, "The host can't be removed.")
        if s.human and not people:
            raise RoomError(400, "Only bots can be removed.")
        del self.seats[i]
        self.changed()

    def set_bot_level(self, i, level):
        i = self.seat_arg(i)
        if self.seats[i].human or level not in self.bot_levels:
            raise RoomError(400, "Only bots have a difficulty.")
        if self.playing():
            raise RoomError(409, "The game has started.")
        self.seats[i].level = level
        self.changed()

    def leave(self, token):
        """Lobby: the seat goes. Game: the seat stays, marked left. Returns the seat index."""
        i = self.need_seat(token)
        s = self.seats[i]
        if self.game is None:
            del self.seats[i]
        else:
            s.left = True
        if s is self.host:
            self.host = next((x for x in self.seats if x.human and not x.left and x is not s), None)
        self.changed()
        return i

    def seat_info(self, i, me=None, now=None):
        """The public facts about one seat, for the app's view."""
        s, now = self.seats[i], now or time.monotonic()
        return {"name": s.name, "avatar": s.avatar, "bot": not s.human,
                "level": s.level if not s.human else None, "host": s is self.host,
                "you": i == me, "left": s.left, "auto": s.auto,
                "away": s.human and (s.left or s.auto or now - s.seen > self.away_after)}

    def summary(self):
        return {"code": self.code, "host": self.host.name if self.host else "?",
                "players": len(self.seats), "humans": len(self.humans()), "max": self.max_seats}

    # ---------------- the driver ----------------
    def ensure_driver(self):
        """Start the driver if there's a game and none is running. Safe off the event loop."""
        if self.closed or self.game is None or (self.task and not self.task.done()):
            return
        try:
            self.task = asyncio.get_running_loop().create_task(self._drive())
        except RuntimeError:                    # no loop here: the next sweep starts it
            pass

    async def _drive(self):
        while not self.closed and self.game is not None:
            event = self._event
            try:
                plan = self.plan(time.monotonic())
                nap = MAX_NAP if plan is None else min(MAX_NAP, plan[0] - time.monotonic())
                if nap > 0:
                    try:
                        await asyncio.wait_for(event.wait(), nap)
                    except asyncio.TimeoutError:
                        pass
                    continue                    # changed, or time passed: plan again
                async with self.lock:
                    if self.closed or self.game is None:
                        break
                    plan = self.plan(time.monotonic())
                    if plan is not None and plan[0] <= time.monotonic():
                        if plan[1]() is not False:  # False: nothing happened (a wake-up to re-check)
                            self.changed()
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                raise
            except Exception:                   # a bug in a bot must not kill the table
                log.exception("driver error in room %s", self.code)
                await asyncio.sleep(1)


class Rooms:
    """Every room of one app. A game in progress is never evicted to make space."""

    def __init__(self, room_class, max_rooms=200, idle=2 * 3600, empty_grace=60, seed=None):
        self.room_class = room_class
        self.max_rooms, self.idle, self.empty_grace, self.seed = max_rooms, idle, empty_grace, seed
        self.rooms = {}

    def create(self, *args, **kwargs):
        self.sweep()
        while len(self.rooms) >= self.max_rooms:
            spare = [r for r in self.rooms.values() if r.closed or not r.playing()]
            if not spare:
                raise RoomError(503, f"Every table on this hotspot ({self.max_rooms}) has a game "
                                     "going. Try again when one finishes.")
            self.rooms.pop(min(spare, key=lambda r: r.touched).code).close()
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(4))
        while code in self.rooms:
            code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(4))
        seed = None if self.seed is None else f"{self.seed}-{code}"
        room = self.room_class(code, *args, seed=seed, **kwargs)
        self.rooms[code] = room
        return room

    def find(self, code):
        room = self.rooms.get(str(code or "").upper())
        return None if room is None or room.closed else room

    def get(self, code):
        room = self.find(code)
        if room is None:
            raise RoomError(404, "No game with that code (it may have ended).")
        return room

    def discard(self, room):
        if self.rooms.get(room.code) is room:
            del self.rooms[room.code]
        room.close()

    def open_lobbies(self, limit=30):
        self.sweep()
        out = [r for r in self.rooms.values() if r.public and r.game is None and not r.closed
               and len(r.seats) < r.max_seats and not r.empty()]
        out.sort(key=lambda r: -r.touched)
        return out[:limit]

    def sweep(self, now=None):
        """Forget closed and idle rooms, empty lobbies, and games everyone left more than
        empty_grace s ago (so the last person can still come back); start drivers."""
        now = now or time.monotonic()
        for code, r in list(self.rooms.items()):
            gone = r.empty() and (r.game is None or now - r.touched > self.empty_grace)
            if r.closed or now - r.touched > self.idle or gone:
                self.rooms.pop(code).close()
            else:
                r.ensure_driver()

    async def sweep_forever(self, every=5.0):
        """Run as a background task (the app's lifespan)."""
        while True:
            await asyncio.sleep(every)
            try:
                self.sweep()
            except Exception:
                log.exception("room sweep failed")


def env_float(name, default):
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return float(default)

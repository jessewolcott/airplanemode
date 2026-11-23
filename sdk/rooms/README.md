# amrooms: rooms for AirplaneMode multiplayer apps

Seats, tokens, host, leave/rejoin, presence, long-poll, a driver loop for bots and timers, a
room registry, and who's who names. Your app keeps its own HTTP routes, rules and bots.
Used by Gate Change and Passport. Platform decision 0022.

## Install (pinned, like everything else)

`requirements.txt`:

```
amrooms @ https://github.com/jessewolcott/airplanemode/archive/refs/tags/rooms-v0.1.0.tar.gz#subdirectory=sdk/rooms
```

A tarball, not `git+https`, so the image needs no git. While changing the SDK and an app
together: `pip install -e ../airplanemode/sdk/rooms` in the app's venv.

## Use

```python
from amrooms import Room, RoomError, Rooms, whois

class Table(Room):
    max_seats = 6
    bot_levels = ("easy", "normal", "hard")
    away_after = 45            # s without a request: away
    poll_wait = 20             # s a long-poll waits

    def playing(self):         # seats are fixed while this is true
        return self.game is not None and not self.game.over

    def plan(self, now):       # the driver's next timed action, or None
        if self.game and self.bot_controls(self.game.current):
            return (self.changed_at + 1.0, self.bot_move)   # return False from it if nothing happened

rooms = Rooms(Table, max_rooms=200, idle=2 * 3600)
# in the app's lifespan: asyncio.create_task(rooms.sweep_forever())

@app.post("/api/rooms")
async def create(request: Request):
    who = await whois(request)                 # before create: no await between create and join
    room = rooms.create()
    token = room.join(data.get("name"), who=who)
    ...

@app.get("/api/rooms/{code}/state")
async def state(code: str, token: str = "", since: int = -1):
    room = rooms.get(code)                     # RoomError(404) if gone
    room.seen(token)
    await room.wait(since)
    return my_view(room, token)
```

Map `RoomError` to a response in one exception handler (`exc.status`, `exc.message`).

## What it decides for you

- **Tokens and identity.** `join(name, token, who)` gives your seat back when the token is yours
  or `who["id"]` matches (another browser on the same device); otherwise a new seat, lobby only.
  Names are cleaned, max 20 characters, and made unique in the room ("Sam", "Sam 2").
- **Leaving.** Lobby: the seat goes. Game: the seat stays with `left = True` and the app decides
  who plays it. `seen()` never undoes a leave, because the page's own poll is in flight when it
  posts leave. The host role passes to the next person still here.
- **Presence.** `away(i)` is left, on autopilot (`seat.auto`, set by the app) or no request for
  `away_after` s. `anyone_here()` lets an app pause when everyone's gone.
- **The driver.** One task per room with a game. It runs `plan()`'s action under `room.lock`
  when due, then `changed()`, and re-plans on every change and at least every 2 s.
- **The registry.** 4-letter codes without I, L, O. At `max_rooms` it evicts the least recently
  used lobby or finished game, never a game in progress (`RoomError(503)` instead). Sweeps forget
  idle rooms, empty lobbies at once, and games everyone left after `empty_grace` (60 s).
- **who's who.** `await whois(request)` asks `ADMIN_URL` (default `http://admin:8000`; empty
  turns it off, as tests should) for `/api/me`, passing on only `X-Forwarded-For` and the
  `am_who` cookie. `None` when there's no name, the admin is down or it times out (1.5 s);
  cached 30 s. Labels only: never authorise anything with it.

## Tests

`python sdk/rooms/tests/test_amrooms.py` (the platform's CI runs it).

## Releasing

Bump `version` in `pyproject.toml` and `__init__.py`, push a `rooms-vX.Y.Z` tag (it publishes
nothing; apps download the tag's tarball), then bump the pin in each app.

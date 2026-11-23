# 0022. A shared Rooms SDK for multiplayer apps

Date: 2026-11-22 · Status: accepted

## Context

Gate Change and Passport each had a `rooms.py` with the same job: codes, seats, secret seat
tokens, host handover, leaving and rejoining, presence, long-polling, a bot task per room and
a capped room list. They had drifted (different leave rules, different eviction, one paused
when everyone was away, the other didn't). Sync Watch in Cinema, a party quiz and the TV remote
need the same mechanics, and every app should use who's who names (decision 0021).

## Decision

- **`amrooms`, a small Python package in this repo** (`sdk/rooms/`), owns the room mechanics:
  `Room` (seats, tokens, host, `join`/`leave`/`seen`, presence, `changed`/`wait`, the driver
  loop over a `plan(now)` hook), `Rooms` (codes, eviction, sweeping) and `whois(request)`.
- **Apps keep their HTTP routes, rules, bots and pages.** The SDK has no web framework code, so
  each app's API (and its page) stays as it was. Only the room semantics are shared.
- **Installed from a pinned tag's tarball** (`rooms-vX.Y.Z`, a URL in `requirements.txt` with
  `#subdirectory=sdk/rooms`), not vendored, so there's one copy, and not `git+https`, so images
  need no git. These tags publish nothing; only `v*` tags publish images.
- **Shared rules**, taken from whichever app had the safer one:
  - a full server evicts a lobby or a finished game, never a game in progress (Gate Change's);
  - leaving a started game keeps the seat (`left`) until the person joins again; polls never
    undo it (Passport's fix in 0.1.1);
  - the last person leaving a game doesn't end it at once: it's forgotten after 60 s, so they
    can still come back. An empty lobby goes at once;
  - names are unique per room ("Sam 2").
- **Who's who in rooms**: a new seat takes the person's name and emoji; the same person (another
  browser on their device, no token) gets their seat back. People without a name type one, as
  before. Pages prefill the name field from `/api/me` and show the emoji on seats.

## Consequences

- A change to room behaviour is an SDK release plus a pin bump and release of each app.
- Gate Change answers bad lobby input (an unknown level, a seat that doesn't exist) with 400
  instead of 409; its page only treats 403/404 specially, so nothing visible changes. Passport's
  room cap now evicts lobbies instead of refusing while any room is younger than 10 minutes.
- Dark Light's party mode still has its own rooms; move it when it's next touched.
- Rejoining by who's who on real phones is *untested* (it rests on decision 0021's MAC lookup).

# 0016. Four new apps: Gate Change, Passport, Wayfarer and The Couch

Date: 2026-11-22 · Status: accepted

## Context

Requested: an Uno-style card game and a Dominion-style deck-builder, both playable together
over the hotspot and against bots; a trivia game built on the travel data the box already
has; and a "therapist simulator" whose outlook comes from a chosen philosophy (Stoicism and
Socialism to start), voiced by the shared model.

## Decision

- **Original games in the genre, not copies.** The mechanics stay (match colour or number,
  skip/reverse/draw cards and wilds; deck-building with currency, victory and attack cards),
  but every name, card, text and look is our own, with a travel theme:
  - **Gate Change** (`airplanemode-gatechange`): 2–8 players, four "airlines" (each with a colour *and* a symbol),
    Delayed / U-Turn / Extra Baggage +2 / Standby / Overbooked +4, "Last call!".
  - **Passport** (`airplanemode-passport`): 2–4 players, 24 kingdom cards (10 per game, three preset sets or
    random), Coins/Banknotes/Gold Card, Postcard/Stamp/Visa, Jet Lag.
  Tests in both repos check that the other games' names never appear.
- **Multiplayer: one shape for both card games.** Each table is a room with a 4-letter code, kept in
  memory and listed on the app's home screen while open. A random seat token in the
  browser lets players rejoin. Updates arrive by long-polling (`state?since=<version>`, about 20 s), which works through
  Caddy without config and with no WebSockets. The server enforces every rule and sends each player only their own hand.
  Bots run on the server with a human-feeling delay. A bot also plays the seat of anyone who leaves or goes quiet,
  until that player comes back. No volume.
- **Wayfarer** (`airplanemode-wayfarer`): the trivia bank is generated **ahead of time** from the same
  Wikivoyage ZIM the Travel Guide downloads (`bank/build.py`, run in Docker against the guide's
  volume). It's committed (about 5,400 questions, 0.3 MB gzipped) and ships in the image. So the
  app needs neither the guide nor its 260 MB file, and it works on every profile. When the
  guide is installed, answers link to the article there. The server scores answers so the
  shared leaderboard (`wayfarer-data` volume) can't simply be sent a number. Disputed places are left out.
- **The Couch** (`airplanemode-couch`): each school is a data file (`schools/<id>.json`); adding one
  needs no code. Replies stream from the shared model (SSE), with an "offline mind" (rule-based
  reflections and the school's own quotes) when there's no model or it's busy. The browser
  keeps the conversation; the box stores and logs nothing. **Safety:** every message
  goes through a fixed check before the model sees it. Signs of self-harm, harm to
  others, abuse or a medical emergency get a fixed message and a crisis-line card,
  never a model reply. Model output is filtered: no diagnoses, no medication, no
  "licensed" claims, no quotes that aren't in the school file. The page always says
  it's a simulator, not therapy.

## Consequences

- Four more containers, each about 30–40 MB in use with a 64 MB limit (48 MB on GL.iNet for
  the in-memory ones). Rooms and rounds are lost on restart; only Wayfarer's scores persist.
- The trivia bank is a snapshot: regenerate it (and release) when the guide's ZIM is updated.
- The Couch is only as good as the 1.5B model; the offline mind keeps it usable without one.
- Anyone on the hotspot can host or join tables, as with Darklight's party mode. The hotspot
  password is the access control.
- *Untested* on real hardware and real phones.

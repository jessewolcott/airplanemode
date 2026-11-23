# 0021. Who's who: a name and a picture per device, kept by the admin service

Date: 2026-11-22 · Status: accepted

## Context

Every multiplayer app asks for a name on its own (Gate Change, Passport, Dark Light's party
mode), and the apps planned next (the Rooms SDK, Sync Watch in Cinema, Lost & found) need to
know who is who across apps. There are no accounts on the box and there shouldn't be.

## Decision

- **The admin service keeps one name and one emoji picture per person**, set on the landing
  page (a chip in the header, a one-time "Who's this?" card). `GET/PUT/DELETE /api/me`.
- **A device is recognised by its MAC**, looked up from the caller's IP in the DHCP leases, so
  every browser on a phone (Safari, the captive-portal popup, Chrome) shows the same name.
  **A long-lived cookie (`am_who`) is linked as well**, for when there's no lease (the dev
  stack, a fixed-IP laptop) or a phone's private MAC changes. Whichever key is known first
  finds the person, and the other keys are linked to them.
- **Apps get an id, not the MAC**: an HMAC of the internal person id with a per-box secret.
- **The admin's device list shows the names.** Lost & found will use them too.
- Pictures come from a fixed list of 24 emoji: nothing to upload or store, and phones draw them
  offline. Names are 1–20 characters; duplicates are allowed.

## Consequences

- Names are trust-on-the-hotspot: anyone on the hotspot can pick any name, and someone who
  takes over a device's IP could read its name. Fine for games and "who's watching"; never use
  it to authorise anything (the admin session stays the only authority).
- iOS and Android use a private MAC per network. It's usually stable for one SSID, but can
  rotate; then the name follows the browser's cookie and other browsers ask again. *Untested on
  real phones* (`docs/testing.md`).
- Gate Change, Passport and Dark Light still have their own name prompts; they move to
  `/api/me` with the Rooms SDK.

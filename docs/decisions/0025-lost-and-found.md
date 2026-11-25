# 0025. Lost & found: when each device was last on the hotspot

Date: 2026-11-24 · Status: accepted

## Context

"Where's my tablet?" On a trip, a phone left at the gate or in a café is easier to find
knowing when it was last on the box's Wi-Fi. The admin panel listed DHCP leases, but a lease
lasts hours after a device leaves, so it can't tell "here" from "gone".

## Decision

- **Presence comes from the hotspots' Wi-Fi station lists**: `iwinfo devices`, then
  `iwinfo info` to keep only access-point interfaces (mode Master; never the upstream one),
  then `iwinfo assoclist` on each. `assoclist` is a new **read-only** call in the restricted
  login's ACL (decision 0008); boxes set up earlier need `scripts/router-setup.sh` re-run.
- **Sampled every supervisor tick** (30 s) and kept in the admin service's store (`seen`, per
  MAC): `since` (this visit began), `last` (last seen), `here`, and the DHCP host name (so a
  device is still named after its lease runs out). Back within 2 minutes counts as the same
  visit. Kept **14 days** after a device was last seen, at most 300 devices. Written to flash at
  most every 5 minutes, like data use.
- **Admin only.** It is location data about people, so it shows only in the admin panel, under
  *Devices* → *Lost & found*: "last seen 14:32 (2 h ago)", with who's who names (decision 0021).
  *Clear history* forgets it all. No app and no `/api/me` field exposes it.
- **"Can't tell" never looks like "gone"**: if no hotspot answers (for example the old ACL),
  the sample is skipped, history is kept, and the panel shows no here/left state.

## Consequences

- Only Wi-Fi devices on the box's own hotspots. Devices on a LAN cable aren't tracked.
- A phone that rotates its private Wi-Fi address shows up as a new device (the old one shows
  as left). iOS and Android keep one address per network by default.
- A phone asleep but still associated counts as here; one that drops Wi-Fi to save power may
  flicker out. The 2-minute rule hides short gaps. *Untested on hardware*, like the
  `iwinfo` mode string on FriendlyWrt (`docs/testing.md`).
- Tested with the mock router and against real OpenWrt 24.10 in Docker as the restricted login
  (no radios there, so the list is empty but the calls are allowed).

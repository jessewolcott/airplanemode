# 0026. Drop is read-only for everyone but the admin (by default)

Date: 2026-11-24 · Status: accepted

## Context

Drop started as a shared table: anyone on the hotspot could add, rename, move, keep and delete
anything (decision 0013 added only "downloads off" for the admin). In practice the box carries
films, shows, Doom WADs and QuickShot clips the admin put there for the trip, and one guest's
tap on *delete* or *move* breaks Cinema's shelves or QuickShot's feed for everyone.

## Decision

- **On the box, only the admin changes Drop**: uploading files, sharing text, making folders,
  renaming, moving, keeping and deleting. Everyone else can still list, play (Range) and save
  copies (subject to downloads off). The admin is whoever is signed in to the admin panel,
  checked the same way as for downloads off (decision 0013).
- **A switch puts it back**: the admin can tick *Let everyone add, move, keep and delete* in
  Drop's page (`PUT api/settings {open}`, kept in Drop's volume as `settings.json`). Off by
  default and after a missing or damaged settings file.
- **Standalone Drop stays open**: without an admin service (`ADMIN_URL` empty) nobody could ever
  be the admin, so everyone may change everything, as before.
- The page hides what a person can't do (the send boxes, *+ folder*, rename/move/keep/delete)
  and says why; the server refuses it anyway (403).
- **QuickShot's "Create the QuickShot folder"** passes the person's admin session on to Drop, so
  it works for the admin and says "only the admin" to anyone else.

## Consequences

- Sharing a photo or a booking code from a guest's phone needs the admin to open Drop up first.
  That's the trade for a library nobody can break by accident.
- Items still expire after `DROP_KEEP_DAYS` unless kept, and only the admin can keep them now.
- Checked by Drop's and QuickShot's tests and in the dev stack (a guest and the admin in separate
  browser contexts, phone size).

# 0013. Cinema, folders and "downloads off" in Drop, and app switches

Date: 2026-11-21 · Status: accepted

## Context

People load films and music onto Drop for a flight, then watch them in a list built for
passing files around. Three asks followed: a proper player; an admin way to keep big files on
the box (watch here, don't copy to every phone); and an admin way to take apps off the menu.

## Decision

- **Cinema is its own app** (`airplanemode-cinema`) with no files of its own: its library is
  Drop's, shelved by Drop's folders. The browser streams straight from `/drop/api/files/<id>`
  (Range, same origin), so Cinema never handles media and runs in 64 MB. Posters are frames the
  first browser grabs and uploads; resume positions stay in each browser (per device, no
  accounts).
- **Drop gets folders** (nested, max 8 deep). `keep` and `nodownload` on a folder apply to
  everything below it.
- **"Downloads off" is admin-only and not DRM.** It removes "save" and refuses `?dl=1` and
  attachment-only types for everyone but the admin; streaming always works, or the films
  couldn't play. It stops casual copies filling phones, not a determined person.
- **The admin is the admin panel's session**, not a new password. Apps share the panel's
  address, so the browser sends them the `am_admin` cookie; Drop forwards it to a new
  `GET /api/admin/session` and caches the answer for 30 s.
- **"Open with" through `apps.json`.** An app declares `opens: {from, types, url}`; Drop shows
  *Play in Cinema* for matching files the device's browser can play. No app knows about
  another by name.
- **App switches.** The admin service reads `portal/apps.json` (mounted read-only) and keeps
  the ids the admin hid in its settings (`hidden_apps` in `networks.json`). `GET /api/apps`
  returns the rest; the landing page and Drop use it. The landing page falls back to the
  static `apps.json` if the admin service is down.

## Consequences

- Hiding an app takes it off the menu and out of "Play in ...", but its address still works
  and it keeps running (and using memory). Stopping a hidden app would need Docker access from
  the admin service, which it deliberately doesn't have (decision 0006).
- Cinema depends on Drop's item fields (`kind`, `parent`, `type`, `download`).
- Which files play depends on each device's browser (MP4/H.264 and MP3 everywhere; MKV/HEVC
  often not). There's no transcoding on the box.
- *Untested* on real hardware; checked on the laptop dev stack.

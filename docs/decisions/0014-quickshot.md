# 0014. QuickShot: a swipe feed over a Drop folder, with comments on the box

Date: 2026-11-21 · Status: accepted

## Context

On a long trip people want the endless short-video feed they're used to, which needs the
internet. The box can fake it with clips loaded beforehand, if it feels like a real feed:
instant swipes, never running out, and other people's likes and comments.

## Decision

- **QuickShot is its own app** (`airplanemode-quickshot`) and, like Cinema, has no files of its
  own: it plays the videos in Drop's top-level folder named **QuickShot** (subfolders count, and
  are shown as "creators"). Uploading is just Drop. The browser streams from Drop directly.
- **Order:** clips this device hasn't seen (newest first), then shuffled rounds forever, each
  avoiding the last few clips shown. New uploads are slotted in after the current clip.
- **Shared, persistent social state in the app's own volume** (`quickshot-data`:
  `comments.json`, `stats.json`): likes (once per device), views (once per device per clip per
  30 min) and comments. Plain JSON files: a hotspot has at most dozens of people.
- **No accounts.** Each browser makes a random viewer id it keeps private, and picks a display
  name. The admin (the admin panel's session, as in decision 0013) can delete any comment;
  others only their own.

## Consequences

- Anyone on the hotspot can comment under any name; moderation is the admin's delete button.
  The hotspot password remains the access control, as for Drop.
- Clearing a browser's storage makes it a new viewer (its old likes stay counted).
- Comments and counts for a clip go when the clip is deleted from Drop.
- *Untested* on real hardware.

# 0023. Sync Watch: watch parties in Cinema

Date: 2026-11-23 · Status: accepted

## Context

On a plane or in a hotel room, people want to watch the same film at the same time, each on
their own phone with their own earbuds (one speaker doesn't work there). Cinema already plays
Drop's files on every phone; it needed a way to keep several players in step.

## Decision

- **A watch party is an `amrooms` room in Cinema** (decision 0022): the file, playing or
  paused, and the position at a moment on the box's clock (`pos` at `at` ms). Nothing else is
  shared, and no media goes through Cinema: every phone still streams from Drop.
- **Anyone in the party controls it**: play, pause, seek, next episode. The same rule as the TV
  remote planned in `docs/app-ideas.md`: a shared couch, not a host with a remote.
- **Each phone steers its own player.** It measures its clock offset to the box (shortest of
  five round trips), works out where the party is now, and closes small drift by playing up to
  10% faster or slower (browsers keep the pitch); more than a second off, it jumps.
  A person's own play/pause/seek is told apart from the phone's corrections by comparing it
  with the party's state (`ownEvent`), not with flags.
- **Found from the library**: a *Watching together* shelf, plus `#watch=<code>` links. Names and
  emoji come from who's who (decision 0021).
- **Leaving the player leaves the party.** Closing the page doesn't; a party nobody has polled
  for 10 minutes is over, so a phone that locked mid-film can come back.

## Consequences

- N phones in a party are N streams of one file from Drop. How many the R6S carries is
  *untested* (`docs/testing.md`).
- Nobody waits for a phone that's buffering; it falls behind and jumps to catch up. A
  "wait for everyone" mode can come later if it matters.
- Parties are in memory: restarting Cinema ends them.
- Measured only in the dev stack (two browser contexts on one laptop: within 0.1 s). Real
  phones over the hotspot's Wi-Fi are *untested*.
- The TV remote (Jellyfin on the hotel TV) can reuse the same state and the same `syncStep`.

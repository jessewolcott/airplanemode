# 0004. Offline first: download while online, run without

Date: 2026-11-21 · Status: accepted

## Context

The box's whole point is to work on planes, in hotels with bad Wi-Fi, and in places with no
signal. Internet is occasional, and when it appears it may be slow or captive-portaled.

## Decision

- Nothing at runtime depends on the internet: no CDNs, fonts, analytics or cloud APIs in any app.
- All downloads (images, models) happen in `scripts/update.sh`, which is run deliberately
  while online. Starting the box never downloads anything new.
- Images are pinned, so an update is always to a known version and a failed pull leaves the
  old, working version running.
- Plain HTTP on the LAN. There is no public name to get a certificate for, and the network is
  the user's own travel Wi-Fi.

## Consequences

- First-time setup needs internet (images plus about 1 GB of model).
- Any app feature that genuinely needs the internet must say so and degrade cleanly without it.

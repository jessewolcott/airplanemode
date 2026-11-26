# 0027. TV Board: who's home, notes, reservations and a photo slideshow on any screen

Date: 2026-11-25 · Status: accepted

## Context

TV mode was planned for the R6S's HDMI port (`docs/app-ideas.md`). In a holiday flat or a hotel
room the useful part is a shared screen: who's in, a note on the fridge, when dinner is booked,
and the trip's photos when nothing else is going on. That screen doesn't need the HDMI port: a
laptop on the hotel TV, a tablet on a stand or a smart TV's browser can show a web page.

## Decision

- **A new app, TV Board** (`airplanemode-tv`, at `/tv/`): `/tv/screen` is the TV page, `/tv/` the
  phone page. It works on any screen today; showing it on the R6S's own HDMI by itself (a kiosk
  browser) is a later step that needs the hardware.
- **Who's home**: `GET /api/home` on the admin service lists the who's who name and picture of
  everyone whose phone is on the hotspot right now (from Lost & found's station lists, decision
  0025). Everyone with a name is listed **unless they untick it**: a new *Show me on the TV's
  "who's home"* box in the landing page's name dialog (`on_tv` in `PUT /api/me`, on by default).
  It never shows times, MACs, unnamed devices or who left: that stays in Lost & found, admin only.
- **Notes and reservations**: anyone on the hotspot may post, like a fridge whiteboard; posts
  carry the poster's who's who name. The poster (same person, or same browser) and the admin can
  remove them. Notes stay up 1 hour to 3 days; a reservation goes an hour after it starts.
- **Slideshow**: the photos in a top-level Drop folder named **TV**. Drop is the admin's
  (decision 0026), so the admin decides what goes on the TV. Board for 45 s, then up to 8 photos,
  then the board again; a new post brings the board straight back.

## Consequences

- Who's home is presence data on a shared screen. The opt-out and "names only, here now" are the
  guard rails; someone who never picked a name never appears.
- A name is trust-on-the-hotspot (decision 0021): anyone can call themselves anything, and so
  anyone can post as that name. Fine for a fridge note; never use it to authorise anything.
- iPhone photos in HEIC don't display in browsers and are skipped; JPEG/PNG/WebP/GIF/AVIF show.
- *Untested*: a real TV (overscan, a TV's own browser), the R6S's HDMI kiosk.

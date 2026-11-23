# App ideas

Apps that would fit on the box, roughly in the order they'd be worth building. Each one
would be its own `airplanemode-<id>` repo following [the app contract](app-contract.md):
offline at runtime, usable without the model, small in memory.

**Size** is a rough build effort. **Storage** is what it adds to the box (the R6S has
32 GB of eMMC; GL.iNet boxes have whatever USB/SD card is plugged in).

## Shipped

| App | What it does | Model |
|---|---|---|
| [The Dark Light](https://github.com/jessewolcott/airplanemode-darklight) | Text adventures narrated by the on-board model | optional |
| [Drop](https://github.com/jessewolcott/airplanemode-drop) | Pass files and notes between every device on the hotspot; keep movies for the flight | no |
| [Travel Guide](https://github.com/jessewolcott/airplanemode-guide) | All of Wikivoyage offline, plus "ask the guide" | optional |
| [Cinema](https://github.com/jessewolcott/airplanemode-cinema) | Plays the films, shows and music on Drop, shelved by folder, resuming per device | no |
| [QuickShot](https://github.com/jessewolcott/airplanemode-quickshot) | An endless swipe feed of the clips in Drop's QuickShot folder, with shared likes and comments | no |
| [Doom](https://github.com/jessewolcott/airplanemode-doom) | PrBoom+ in the browser with touch controls: Freedoom, the Layover airport episode, your own WADs from Drop | no |
| [Gate Change](https://github.com/jessewolcott/airplanemode-gatechange) | Colour-and-number card game for 2–8: tables on the hotspot, bots for empty seats | no |
| [Passport](https://github.com/jessewolcott/airplanemode-passport) | Travel deck-building card game for 2–4: tables on the hotspot, bots for empty seats | no |
| [Wayfarer](https://github.com/jessewolcott/airplanemode-wayfarer) | Solo travel trivia from a question bank built from the Travel Guide's Wikivoyage | no |
| [The Couch](https://github.com/jessewolcott/airplanemode-couch) | Therapist simulator: six therapists (Empath, Pragmatist, Stoic, Ho Chi Minh's ethics, playful Nihilist and Narcissist), voiced by the model, with an offline fallback | optional |

## Planned (agreed 2026-11-22)

In build order. Who's who and the Rooms SDK come first because the others lean on them.

| Item | What it is | Notes | Model | Size |
|---|---|---|---|---|
| **Who's who** (platform) | Each device picks a name and avatar once, on the landing page; every app can read it | Admin service stores it by MAC (it already sees device names). `GET /api/me` with the forwarded request, so no accounts. Feeds resume positions, scores, Rooms seats | no | small |
| **Rooms SDK** (platform) | Shared lobby, seats, rejoin, bot takeover and long-poll channel for multiplayer apps | Pulled out of Gate Change and Passport, which then move onto it. A small Python package vendored into each app (no extra container), identity from Who's who | no | medium |
| **Sync Watch** (in Cinema) | Host plays, pauses, seeks; every phone follows in lockstep, each on its own earbuds | Built on the Rooms SDK. Drift correction by nudging `playbackRate`, hard seek past ~1 s. Phones still play from the box, so 4–6 streams of one file is the load to test on the R6S | no | small–medium |
| **Lost & found** | "Last seen 14:32" for every device that has left the hotspot, with names from Who's who | Admin service already sees DHCP leases and station lists; this keeps a short history. Admin-only by default (it's location data about people) | no | small |
| **First aid & emergency** | WikiMed ZIM plus a hand-curated page per country: emergency numbers, embassies, "I need a doctor" in the local language | **No model, on purpose**: safety content must not be generated. Reuses the Travel Guide's ZIM reader. The curated data is checked into the repo and reviewed by hand | no | small–medium |

## Next up

| App | What it does | Why it fits | Model | Size | Storage |
|---|---|---|---|---|---|
| **TV mode on the box's HDMI** | When a TV is plugged into the R6S's HDMI port, the box shows a kiosk screen: departure board (itinerary, countdowns, who's online, uplink state), then Sync Watch and Party quiz boards | R6S has HDMI out (RK3588S); GL.iNet boxes don't, so it's R6S-only and phones/laptops can open the same `/tv` page instead. Detect with `/sys/class/drm/card*-HDMI-A-*/status`; a kiosk container (cage + Chromium) gets `/dev/dri` only, not privileged. **Untested**: whether FriendlyWrt's kernel exposes the DRM device and how video decode performs | no | medium | ~400 MB image |
| **Jellyfin in Cinema** | Browse your home Jellyfin server when the box is online; download items to Drop for the flight | Live streaming over hotel Wi-Fi is the weak case; "download for offline" is the win, and it fits the per-app sync hook. Reaches a home server through the existing home VPN (`amvpn`), or a public URL. API key stored in admin settings like other secrets | no | medium | whatever you download |
| **Offline maps** | Pan and search a map of the trip's region; pin the hotel, share pins with the group | Maps are the first thing that breaks without data. Protomaps `.pmtiles` extracts (one file per region) plus MapLibre, all served locally | no | medium | 50 MB–2 GB per region |
| **Phrasebook & translator** | Translate typed text between common languages, plus a phrasebook per country | Real offline translation models (Bergamot / Argos, ~20–40 MB per language pair) run fine on the R6S's CPU. The shared model is too small to translate well, so it would only suggest phrases | optional | medium | ~30 MB per language pair |
| **Trip kit** | Shared itinerary, packing list and expense splitter ("who owes whom") for the group on the hotspot | Every group trip needs it, and it's all small local data | optional (parse "Dinner 84 EUR, split 4") | medium | tiny |
| **Party quiz** | Phones as buzzers, the TV or a laptop as the board. Wayfarer's question bank and round/scoring code are a ready start (it's solo today); the model could add questions about the destination | Local multiplayer works perfectly on one hotspot; uses the model where it's good (playful text), with a fixed question bank as the fallback | optional | medium | small |
| **Library** | Books from Project Gutenberg, plus Wikipedia subsets (medicine, history) | The Travel Guide already reads any Kiwix ZIM; this is mostly picking files and a reader view | no | small | 100 MB–10 GB |

## Later, or with care

| App | What it does | Watch out for |
|---|---|---|
| **Photo backup** | Phones back up the day's photos to the box over the hotspot | Storage and flash wear; needs a clear "copy them off when home" story |
| **Ask anything** | A plain chat with the on-board model | Easy to build, but a 2B model is confidently wrong; the Travel Guide's grounded answers are the better pattern |
| **Group chat / walkie-talkie** | Messages and push-to-talk between phones on the hotspot | Only useful when the group is in range of one box, e.g. a campsite or a ship cabin |
| **Currency & time zones** | Converter with rates saved while online, world clock, jet-lag planner | Rates go stale; show their date clearly |
| **News & podcasts to go** | Fetch chosen feeds while online, read and listen offline | Needs the "while online" sync step, like `scripts/update.sh` |
| **Kids' corner** | Simple games and stories, locked to a kids' hotspot | Pairs with the admin panel's device pause and a second hotspot |

## Platform features that would help apps

- **Per-app "while online" sync hook**, so apps like Maps, Library and News can download
  content during `scripts/update.sh` instead of each inventing its own downloader (the
  Travel Guide's `/health` + download progress is the pattern to generalise).
- **Shared storage quota** shown in the admin panel, since Drop, Cinema, Maps and Library
  all compete for the same flash.
- **Who's who** and the **Rooms SDK**: now planned, see above.

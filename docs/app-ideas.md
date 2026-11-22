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

## Next up

| App | What it does | Why it fits | Model | Size | Storage |
|---|---|---|---|---|---|
| **Offline maps** | Pan and search a map of the trip's region; pin the hotel, share pins with the group | Maps are the first thing that breaks without data. Protomaps `.pmtiles` extracts (one file per region) plus MapLibre, all served locally | no | medium | 50 MB–2 GB per region |
| **Phrasebook & translator** | Translate typed text between common languages, plus a phrasebook per country | Real offline translation models (Bergamot / Argos, ~20–40 MB per language pair) run fine on the R6S's CPU. The shared model is too small to translate well, so it would only suggest phrases | optional | medium | ~30 MB per language pair |
| **Trip kit** | Shared itinerary, packing list and expense splitter ("who owes whom") for the group on the hotspot | Every group trip needs it, and it's all small local data | optional (parse "Dinner 84 EUR, split 4") | medium | tiny |
| **Party quiz** | Phones as buzzers, the TV or a laptop as the board; the model writes questions about the destination from the Travel Guide | Local multiplayer works perfectly on one hotspot; uses the model where it's good (playful text), with a fixed question bank as the fallback | optional | medium | small |
| **Library** | Books from Project Gutenberg, plus Wikipedia subsets (medicine, history) | The Travel Guide already reads any Kiwix ZIM; this is mostly picking files and a reader view | no | small | 100 MB–10 GB |

## Later, or with care

| App | What it does | Watch out for |
|---|---|---|
| **Photo backup** | Phones back up the day's photos to the box over the hotspot | Storage and flash wear; needs a clear "copy them off when home" story |
| **Ask anything** | A plain chat with the on-board model | Easy to build, but a 1.5B model is confidently wrong; the Travel Guide's grounded answers are the better pattern |
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
- **Who's who**: the admin panel already knows each device's name; apps could use it for
  per-person resume positions and quiz scores without accounts.

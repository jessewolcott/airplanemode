# How apps plug in

Authoritative rules: `docs/app-contract.md`. Background: decisions 0001, 0005, 0013.

## Roster (as pinned in `compose.yaml`)

| id | Image tag | Uses LLM | Data volume | Talks to | Notes |
|---|---|---|---|---|---|
| `darklight` | `airplanemode-darklight:0.3.2` | yes (optional) | none | llm | Text adventures (three worlds), party mode |
| `drop` | `airplanemode-drop:0.2.2` | no | `drop-data` | admin (`ADMIN_URL`) | Files/notes between devices. Folders, "downloads off" for the admin. `.wad` = `application/x-doom-wad` |
| `cinema` | `airplanemode-cinema:0.1.2` | no | `cinema-data` (posters) | drop (`DROP_URL`) | Streams Drop's media straight from `/drop/api/files/<id>`. Declares `opens` |
| `quickshot` | `airplanemode-quickshot:0.1.2` | no | `quickshot-data` (comments.json, stats.json) | drop, admin | Swipe feed over Drop's top-level `QuickShot` folder |
| `doom` | `airplanemode-doom:0.1.1` | no | none (saves in the browser's IndexedDB) | drop (from the browser, `../drop/`) | PrBoom+ in WebAssembly, static files on busybox httpd (32m). Freedoom + Layover + Drop WADs. Declares `opens` (decision 0015) |
| `gatechange` | `airplanemode-gatechange:0.2.0` | no | none (rooms in memory) | admin (`ADMIN_URL`, who's who) | Colour/number card game, rooms + long-poll + bots (decision 0016), on the Rooms SDK (0022) |
| `passport` | `airplanemode-passport:0.2.0` | no | none (rooms in memory) | admin (`ADMIN_URL`, who's who) | Deck-builder, on the Rooms SDK like gatechange |
| `wayfarer` | `airplanemode-wayfarer:0.2.1` | no | `wayfarer-data` (scores.json) | guide (link only, from the browser) | Trivia bank pre-built from the guide's ZIM, shipped in the image. `TZ` sets the daily round |
| `couch` | `airplanemode-couch:0.2.4` | yes (optional) | none (chat stays in the browser) | llm | Therapist simulator (six therapists), streams SSE; fixed safety check before the model |
| `guide` | `airplanemode-guide:0.1.2` | yes (optional) | `guide-data` (~260 MB) | llm | Wikivoyage offline + "ask the guide" |

Images are `ghcr.io/jessewolcott/airplanemode-<id>:<semver>`, built for arm64 (+amd64) by
each app repo's CI on a `v*` tag. Pushing release tags publishes images, so ask before doing it.

The three model apps (darklight, guide, couch) carry `profiles: ["llm"]` in `compose.yaml`, so they
don't run on the GL.iNet lite profile, and `installed_apps()` (`admin/main.py`) drops `uses_llm` apps
from `/api/apps` when `COMPOSE_PROFILES` lacks `llm` (decision 0017).

## The contract in short

1. One repo, one image, semver tags. The platform pins exact versions.
2. One HTTP port inside the container (all current apps use 8000). It isn't published.
3. **Works under a path prefix**: served at `/<id>/` with the prefix stripped (`handle_path`), so
   pages must use **relative** URLs (`api/turn`, not `/api/turn`). The ids `api` and `admin` are reserved.
4. Fully offline at runtime: no CDNs, fonts or analytics.
5. Works without the model (`LLM_URL` empty or unreachable means a plainer mode). The model is
   llama.cpp's OpenAI-compatible API, shared, slow, 2 slots. Use `response_format` JSON
   schema, tight `max_tokens`, `cache_prompt: true`, timeouts and fallbacks.
6. Stays within `mem_limit` (aim ≤ 256 MB). Small writes, one named volume.
7. Ships an `app.yaml` (id, name, description, image, port, path, memory, needs.llm, env).
8. Runs its own tests in CI before publishing.

## `portal/apps.json` entry

```jsonc
{
  "id": "cinema",                 // = compose service name = Caddy path segment
  "name": "Cinema",
  "description": "One sentence for the menu.",
  "path": "/cinema/",
  "monogram": "Ci",               // 2 letters on the icon tile (fallback: first 2 of name)
  "color": "#e2685c",             // icon tile background
  "category": "Entertainment",    // not used by the landing page today
  "uses_llm": false,              // "uses the on-board model" tag; hidden without the llm profile
  "opens": {                      // optional: "open with" for another app's items
    "from": "drop",
    "types": ["video/*", "audio/*"],
    "url": "/cinema/#play={id}"   // {id} = the item's id in the offering app
  }
}
```

Consumers of this file:
- The landing page (via `GET /api/apps`, falling back to the static `/apps.json`).
- The admin service (`installed_apps()`, bind mount `/config/apps.json`). **Restart admin
  after editing.**
- Drop reads `GET /api/apps` and shows *Play in <name>* on items whose MIME type matches an
  app with `opens.from == "drop"` (audio/video only if the browser can play the format; other
  types, like Doom's `application/x-doom-wad`, always).
- CI (`jq -r '.[].path'`), which checks the route and the service exist.

## Platform services for apps

- `GET /api/apps`: the visible menu (hidden apps excluded).
- `GET /api/admin/session`: is the cookie-holder the admin? Call it from the app's backend at
  `http://admin:8000/api/admin/session`, forwarding only a well-formed `am_admin` cookie. It returns
  200 or 401. Cache briefly (Drop: 30 s). Apps share the admin panel's origin, so the browser
  sends them that cookie.
- The shared model at `http://llm:8080` (R6S profile only).

## Adding an app to the box (this repo)

1. `compose.yaml`: service `<id>` (2-space indent, `^  <id>:`, which CI greps for), pinned image,
   `LLM_URL: ${LLM_URL-http://llm:8080}` if it uses the model, `mem_limit: ${<ID>_MEM:-256m}`,
   `restart: unless-stopped`, a named volume if needed (declare it under `volumes:`).
2. `Caddyfile`: `redir /<id> /<id>/` and `handle_path /<id>/* { reverse_proxy <id>:<port> }`.
   Put it in the apps section, before the platform `handle` blocks.
3. `portal/apps.json`: an entry as above.
4. Each `hardware/*.env`: `<ID>_MEM=...` (and any app knobs).
5. `compose.dev.yaml`: `image: airplanemode-<id>:dev`, `build: ../airplanemode-<id>`.
6. The README repo table and `docs/app-ideas.md` "Shipped". A decision record if it's significant.
7. CI then validates compose + Caddy, the route/service per `apps.json` path, and an arm64 manifest
   for every image. The image must be **public** on GHCR for the manifest check.

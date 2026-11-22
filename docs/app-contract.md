# The app contract

What an app has to do to run on AirplaneMode. If an app follows this, adding it to the
box is three small edits in this repo and nothing else.

## Must

1. **One repo, one container image**, published to
   `ghcr.io/jessewolcott/airplanemode-<id>` for `linux/arm64` (and `linux/amd64` for laptops).
   Released versions are semver tags (`v0.1.0` publishes `:0.1.0`). The platform pins exact
   versions, never `latest` or `main`.
2. **One HTTP port inside the container.** It isn't published to the host: the proxy reaches
   it on the internal `airplanemode` network as `http://<id>:<port>`.
3. **Works under a path prefix.** The ids `api` and `admin` are reserved for the platform.
   The proxy serves the app at `/<id>/` and strips the prefix,
   so the app sees `/` as usual. The page must use **relative** URLs (`api/turn`, not
   `/api/turn`) so the browser keeps the prefix.
4. **Fully offline.** No CDNs, web fonts, analytics or outside APIs at runtime. Bundle every
   asset in the image.
5. **Works without the model.** The model can be busy, still loading or down. With `LLM_URL`
   empty or unreachable, the app must still be usable, in a plainer mode. An app that uses the
   model sets `"uses_llm": true` in `portal/apps.json` and `profiles: ["llm"]` in `compose.yaml`:
   it isn't installed on boxes without the model (the GL.iNet profile; decision 0017).
6. **Stays inside its memory limit.** Declare it in `app.yaml`; the platform enforces it with
   `mem_limit`. Aim for 256 MB or less.
7. **Small writes only.** Treat the root filesystem as disposable. If the app keeps data,
   use one named volume and keep it small; the box stores to eMMC or SD cards.
8. **Ships an `app.yaml`** at the repo root:

   ```yaml
   id: darklight
   name: The Dark Light
   description: One sentence for the portal.
   image: ghcr.io/jessewolcott/airplanemode-darklight
   port: 8000
   path: /darklight/
   memory: 256m
   needs:
     llm: optional        # optional | none
   env:
     LLM_URL: what it's for
   ```

9. **Runs its own tests in CI** before publishing the image.

## Using the shared model

- `LLM_URL` points at llama.cpp's OpenAI-compatible API (`/v1/chat/completions`,
  `/v1/models`, `/health`). Use `response_format` with a JSON schema whenever you need
  structured output; the server enforces it with a grammar.
- The model is shared and slow (single-digit to low-double-digit tokens per second on the
  R6S). Keep prompts short, set `max_tokens` tight, send `cache_prompt: true`, and never
  call it in a loop the user is waiting on without a fallback.
- Validate what comes back. Small models are confidently wrong; the app owns the truth.
- For chat-style replies, stream (`stream: true`) and pass the tokens on to the page as
  server-sent events, so people see words arrive instead of waiting tens of seconds. Caddy
  passes SSE through with no config. The Couch does this, with a fallback if the first token
  doesn't arrive in time.

## Optional: playing together on the hotspot

Everyone on the hotspot can reach the same app, so multiplayer needs no accounts or internet.
The pattern Gate Change and Passport use (decision 0016), and Darklight's party mode before them:

- **Rooms in memory** with a short join code, listed on the app's home screen while open,
  capped in number and forgotten after an idle timeout. No volume.
- **A random seat token** kept in the browser (`localStorage`), so a refresh or a locked phone
  rejoins the same seat.
- **Long-polling** for updates: `GET state?since=<version>` answers at once if something
  changed, otherwise waits about 20 s. It works through Caddy as is. No WebSockets.
- **The server enforces the rules** and renders the state for each player, so hidden
  information (other hands) never reaches a phone. Bots run on the server.

## Optional: opening another app's files

An app can offer to open files from another app. Today that's Drop: its items get a
**Play in ...** button for apps that declare it. Add `opens` to the app's entry in
`portal/apps.json` (and mirror it in the app's `app.yaml`):

```json
"opens": {"from": "drop", "types": ["video/*", "audio/*"], "url": "/cinema/#play={id}"}
```

`{id}` is replaced with the item's id. The offering app reads the list from `GET /api/apps`.
Drop offers audio and video types only when the browser can play the format; any other type
is offered as is. Doom, for example, declares `"types": ["application/x-doom-wad"]` (Drop gives
`.wad` uploads that type).

## What the platform gives apps

- `GET /api/apps`: the installed apps the admin hasn't switched off (the landing page's menu).
  The admin can hide any app from the admin panel; a hidden app keeps running at its address,
  but isn't on the menu and isn't offered by other apps.
- `GET /api/admin/session`: 200 if the `am_admin` cookie it's sent is a live admin session,
  401 otherwise. Apps are served from the same address as the admin panel, so the browser
  sends them that cookie. To know whether the person using the app is the admin, call this
  from the app's backend (`http://admin:8000`), passing on only that cookie (check its format
  first), and cache the answer briefly. Drop does this.

## Adding the app to the box (in this repo)

- `compose.yaml`: a service named `<id>` with the pinned image, `LLM_URL: ${LLM_URL-http://llm:8080}`
  and `profiles: ["llm"]` if it uses the model, and `mem_limit: ${<ID>_MEM:-256m}`.
- `Caddyfile`: `redir /<id> /<id>/` and a `handle_path /<id>/*` block.
- `portal/apps.json`: name, description, path, `uses_llm`.
- Memory settings for the new app in each `hardware/*.env` that runs it.

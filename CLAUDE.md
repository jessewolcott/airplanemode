# AirplaneMode (platform repo): notes for Claude

Self-hosted, offline-first apps for a travel router (NanoPi R6S main target, GL.iNet lite).
This repo is the **platform**: landing page, admin service (Wi-Fi control + connectivity
supervisor), Caddy front door, shared llama.cpp model, hardware profiles, docs. Apps live in
their own repos (`../airplanemode-darklight`, `../airplanemode-drop`, `../airplanemode-guide`,
`../airplanemode-cinema`, `../airplanemode-quickshot`, `../airplanemode-doom`, `../airplanemode-gatechange`,
`../airplanemode-passport`, `../airplanemode-wayfarer`, `../airplanemode-couch`) and plug in as pinned container images. The public homepage +
one-line installer is
`../airplanemode-site`, live at https://airplanemode.xin (GitHub Pages, own domain; decisions
0011, 0012, 0019).
Start with `README.md`, then `docs/decisions/` (0001–0022). They explain *why* things are
the way they are.

## Environment (Jesse's Windows machine)

- All of this project's repos live side by side in **`H:\Github\airplanemode\`**: `airplanemode`
  (this platform repo), `airplanemode-darklight`, `airplanemode-drop`, `airplanemode-guide`,
  `airplanemode-cinema`, `airplanemode-quickshot`, `airplanemode-doom`, `airplanemode-gatechange`,
  `airplanemode-passport`, `airplanemode-wayfarer`, `airplanemode-couch`, `airplanemode-site`.
  New app repos go there too. H: is **exFAT**, so git needs
  `git config --global --add safe.directory H:/Github/airplanemode/<repo>` for each new repo.
- Moving folders on H: while VS Code has them open can half-fail: Windows moves some files
  and leaves locked folders behind. Close VS Code first, then check `git status` in the new
  place (a missing file shows up as deleted).
- Docker Desktop (Linux engine). The `default` context works. If Docker Desktop won't start
  and its log says it can't remove `...\AppData\Local\Docker\run\userAnalyticsOtlpHttp.sock`,
  quit Docker Desktop and rename `%LOCALAPPDATA%\Docker\run` (a stale socket file).
- **Port 8000 is reserved by Windows.** Use 8080 (this stack) or 8765 (darklight standalone).
- git has `autocrlf` on. `.gitattributes` forces LF for `*.sh`, `*.env`, `*.yaml` and the
  `Caddyfile`, which run on the Linux box. Keep it that way.
- GitHub: personal account **jessewolcott**. Repos are public. `gh` is installed.
- Python 3.10 locally (the containers use 3.12). Repo venv: `.venv` (git-ignored).

## Commands

```powershell
# dev stack: mock router, apps built from sibling repos -> http://localhost:8080 (admin pw: dev)
docker compose -f compose.yaml -f compose.dev.yaml up -d --build
docker compose -f compose.yaml -f compose.dev.yaml restart proxy   # after editing Caddyfile (single-file bind mount)
docker compose -f compose.yaml -f compose.dev.yaml down

# tests
.venv\Scripts\python admin\tests\test_admin.py        # admin API + supervisor, mock router (CI runs this)
# live test against real OpenWrt 24.10 in Docker: see docs/testing.md (restricted-login variant too)

# config checks (CI does these too)
docker compose --env-file hardware/nanopi-r6s.env config --quiet
docker compose --env-file hardware/glinet.env config --quiet
```

Simulate travel in dev: `POST /api/mock/world` with `{"in_range": []}`,
`{"in_range": ["CoffeeCorner"]}` or `{"ethernet": true}`. The mock hotel network with a
sign-in page is `Harbor_Hotel_Guest`.

VS Code tasks (`.vscode/tasks.json`) wrap all of the above.

## How it fits together (rules the code depends on)

- **Router control goes only through OpenWrt's ubus JSON-RPC**, as the restricted rpcd login
  `airplanemode` (access list: `router/rpcd-acl-airplanemode.json`, installed by
  `scripts/router-setup.sh`). It's never root and never a privileged container. **A new ubus
  call or uci config means updating the ACL file**, re-running `router-setup.sh` on the
  router, and the denied-calls list in `admin/tests/test_openwrt_live.py`.
- Save changes with **`uci apply`**, not `uci commit`: ubus sessions aren't allowed to
  commit (found by testing against real OpenWrt).
- Names the admin service owns: `wireless.airplanemode_up` (STA), network `wwan` (metric 20),
  firewall redirect `airplanemode_portal`, dnsmasq address `/#/10.255.255.1` (offline popup),
  home VPN: network `amvpn` (wireguard, metric 5) + `airplanemode_vpn_peer`, firewall zone
  `airplanemode_vpn` (name `vpn`) + forwarding `airplanemode_vpn_fwd`; device pause: firewall
  rules `airplanemode_block_<mac>[_vpn]`. Don't touch other sections. Rules must never point
  at a zone that doesn't exist (fw4 refuses the whole ruleset).
- Every endpoint that changes the router takes `router_lock` (main.py): the supervisor and
  requests share one ubus session and `uci apply` applies everything staged.
- **Offline popup only when there's no uplink at all** (no WAN and no wwan link), after 2
  supervisor ticks and never during a join grace period. Any uplink turns it off immediately,
  so it can't hide a hotel's real sign-in page.
- Lockout guard: never allow disabling or deleting the last enabled hotspot.
- Caddy is published **only on `LAN_IP`** (Docker's firewall rules can bypass OpenWrt's).
  The admin container uses **`dns: LAN_IP`** (Docker otherwise falls back to public DNS,
  which hotels block before sign-in).
- Model apps (`uses_llm` in apps.json: darklight, guide, couch) carry `profiles: ["llm"]` and
  the admin hides them without that profile, so the GL.iNet lite profile has none (decision 0017).
- Apps: pinned image tags in `compose.yaml` (never `latest`), plus a route in `Caddyfile` and
  an entry in `portal/apps.json`; CI checks all three. Reserved paths: `/api`, `/admin`.
  App rules: `docs/app-contract.md`.
- The installer in `../airplanemode-site/install.sh` wraps `scripts/router-setup.sh` and
  `scripts/update.sh`. Changing their env flags, a new `hardware/*.env`, or the install path
  means updating the installer and its page too. The page is in seven languages: change
  English `index.html`, update all six translations (`i18n/<lang>.json`), run
  `tools/build_i18n.py` and `tools/check_i18n.py`; CI fails if they don't match.
- The admin service reads `portal/apps.json` (bind-mounted at `/config/apps.json`: restart
  admin after editing it) and hides apps listed in `hidden_apps` (its settings file).
  `GET /api/apps` is the menu; Drop's "Play in ..." uses it too (`opens` field). Apps learn
  whether the user is the admin via `GET /api/admin/session` with the forwarded `am_admin`
  cookie (decision 0013).
- Validate input before saving (SSID ≤ 32 bytes, WPA key 8–63 printable or 64 hex, unicast MAC).

## Gotchas already learned

- In an **unprivileged OpenWrt container, netifd hangs** (procd jails fail). Live link status,
  `network reload` and `dnsmasq restart` time out there. That's not a code bug.
  `router-setup.sh` wraps restarts in a 60 s shell timeout for this reason (busybox may not
  have `timeout`).
- fw4 DNAT to a port needs a protocol + port match (`src_dport 80`), or nftables rejects it.
  Check rulesets with `fw4 print` plus `nft -c -f` in a container with `--cap-add NET_ADMIN`.
- Caddy rejects duplicate hosts in a `host` matcher (dev `LAN_IP=127.0.0.1`).
- The mock router reconnects to the configured network when it comes back in range, like real
  OpenWrt STA does. Auto-join switching between networks is covered by the tests, not the UI demo.

## Working agreements

- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
  Write long messages to a file and use `git commit -F` (PowerShell here-strings into `git
  commit -F -` misbehave).
- Commit and push to `main` freely for work Jesse asked for. **Pushing release tags (`v*`)
  publishes container images, so ask first.**
- Never store the router's root password anywhere. `.env` holds only the restricted login and
  the admin password (mode 600, git-ignored).
- Document decisions as new records in `docs/decisions/` rather than rewriting old ones.
- Mark anything not verified on real hardware as *untested*, and keep `docs/testing.md`'s
  on-device checklist current.

## Status (2026-11-22) and next steps

Done and pushed (CI green):
- platform stack and the shared model;
- landing page with captive-portal sign-in, the app menu and the "Get online" prompt;
- admin panel (upstream join, MAC clone, hotspots, saved networks);
- restricted router login;
- supervisor (auto-join, Ethernet first, offline popup);
- rebind-off and DNS fixes;
- hardening pass (router_lock, lockout guard on radios that are off, login throttling, CI v7);
- home VPN (WireGuard), connected devices with pause, data use per network (decision 0010).
- apps Drop (`../airplanemode-drop`) and Travel Guide (`../airplanemode-guide`), both 0.1.0;
  project icon (`portal/icon.svg`), screenshots in `docs/screenshots/` (retake from the dev
  stack when the UI changes), app ideas in `docs/app-ideas.md`.
- homepage + one-line installer (`../airplanemode-site`, decisions 0011, 0012), on GitHub Pages
  at https://airplanemode.xin since decision 0019. In English plus Spanish, French, Italian,
  Chinese, Japanese and Vietnamese (`/es/` … `/vi/`, generated by `tools/build_i18n.py`,
  checked by `tools/check_i18n.py` in CI). Machine-translated and machine-reviewed; a native
  speaker review is still open. The Pages deploy retries once by itself (flaky artifact lookup).
- Cinema app (`../airplanemode-cinema`), Drop folders + admin "downloads off", app switches in
  the admin panel (decision 0013). Released: Drop 0.2.0, Cinema 0.1.1 (TV series).
- QuickShot app (`../airplanemode-quickshot`, decision 0014): swipe feed over Drop's QuickShot
  folder; comments/likes/views in the `quickshot-data` volume. Released: 0.1.0.
- Doom app (`../airplanemode-doom`, decision 0015): PrBoom+ compiled to WebAssembly, touch
  controls, Freedoom + the Layover airport episode (6 maps generated by `wad/` in that repo),
  `.wad` files from Drop (Drop: `application/x-doom-wad` + Play in Doom). Built and played in a
  desktop browser, not on real phones yet. Released: Doom 0.1.0, Drop 0.2.1 (Play in Doom).
- Gate Change, Passport, Wayfarer and The Couch (decision 0016): two card games with hotspot
  rooms (long-poll) and bots, trivia pre-built from the guide's ZIM, a therapist simulator
  (therapists as data files, fixed safety check). Played in the dev stack at
  phone size, not on real phones. Released: all four 0.1.0; The Couch 0.2.0 (six therapists:
  Empath, Pragmatist, Stoic, Ho Chi Minh's ethics, playful Nihilist and Narcissist). The homepage lists all ten apps
  (cards copied by hand from `portal/apps.json`; the site's `tools/check_i18n.py` checks them,
  and a card change means updating its six translations too).
- Review-fix releases (found while writing the `llm/` notes): Wayfarer 0.2.0 (resume after a
  reload, speed bonus up to 50, idle-timer and leaderboard-saved fixes), Gate Change 0.1.1
  (rejoining reclaims your seat, unattended rounds advance, a full server never closes a live
  game), Passport 0.1.1 (leaving sticks, lobby list no longer 500s, hidden-info test), The Couch
  0.2.1 ("saying" no longer means "give me a quote", a busy queue doesn't start the cooldown,
  Gemma stop token). Page changes in these were checked by tests/code only, not in a browser.
- Home button (app contract item 10): the same house icon at the top right of every screen
  of every app, back to the menu; asks first only where leaving would lose progress (Doom
  mid-level, Drop uploads, Dark Light party host, unsent QuickShot comments). Released: Dark Light
  0.3.1, Drop 0.2.2, Guide 0.1.1, Cinema 0.1.2, QuickShot 0.1.1, Doom 0.1.1, Gate Change 0.1.2,
  Passport 0.1.2, Wayfarer 0.2.1, The Couch 0.2.2. Then The Couch 0.2.3 (phone header,
  *italic*/**bold** in replies), Gate Change 0.1.3 (centred bar titles) and QuickShot 0.1.2
  (prunes old `.bad-*` copies). Screenshots retaken after these changes. Checked in headless
  Chromium at phone and desktop size, and by Jesse on a real phone against the local dev stack
  (looked good); not yet on the R6S.
- **Current releases** (pinned in `compose.yaml`): Dark Light 0.3.2, Drop 0.2.2, Guide 0.1.2,
  Cinema 0.1.2, QuickShot 0.1.2, Doom 0.1.1, Gate Change 0.1.3, Passport 0.1.2, Wayfarer 0.2.1,
  The Couch 0.2.4.
- Every repo has checked-in `llm/` notes ("how it's built", for LLMs and engineers), kept out of
  the images by `.dockerignore`. The code wins when they disagree; fix the notes.
- GL.iNet lite profile without the model apps (decision 0017).
- Shared model switched to Gemma 4 E2B after comparing six models (decision 0018); the three
  model apps' prompts and checks retuned for it, with an eval in each repo (decision 0020). Released: Dark Light 0.3.2, Guide 0.1.2, The Couch 0.2.4.
- Who's who (decision 0021): name + emoji per device on the landing page, `GET/PUT/DELETE
  /api/me`, recognised by MAC (DHCP lease) and the `am_who` cookie; names in the admin's
  device list. Plan agreed 2026-11-22 (`docs/app-ideas.md` "Planned"): next Sync Watch in
  Cinema, Lost & found, First aid; later TV mode on the R6S's HDMI and Jellyfin on the hotel TV
  with Cinema as the remote.
- Rooms SDK (decision 0022): `amrooms` in `sdk/rooms` (seats, tokens, presence, long-poll,
  driver, registry, `whois()`); CI runs its tests. Apps pin it in `requirements.txt` as the
  `rooms-vX.Y.Z` tag's tarball (`#subdirectory=sdk/rooms`); `rooms-v*` tags publish nothing.
  Released `rooms-v0.1.1` (0.1.1: only a seated person's polls keep a room alive). Gate Change and Passport moved onto it (seat names/emoji from who's
  who, rejoin from another browser); checked by tests and in the dev stack at phone size.

**No real hardware has been tested yet.** The R6S build is in progress. Parts:
- NanoPi R6S 8 GB with the metal case,
- ALFA AWUS036ACHM (MT7610U) for upstream,
- ALFA AWUS036ACM (MT7612U) for the hotspot,
- a short USB extension,
- 5 V/3 A USB-C power.

Open follow-ups:
1. Work through `docs/testing.md` → *On the device: checklist* once hardware arrives.
2. Prompts rechecked on Gemma 4 E2B (decision 0020; each model app has an eval to rerun). Left:
   the Guide still answers from a passage about another airport/route when the passages lack
   the answer (12/66 samples): fix it in retrieval.
3. Measure model tokens/s on the R6S (record it in a new decision), try the QAT Q4_0 build, and
   maybe spike RKLLM (NPU).
4. Home VPN: bypass the tunnel automatically for hotel sign-in pages (decision 0010).
5. Narrow the restricted login's `network`/`firewall` write access (decision 0008
   consequences) if it ever matters.
6. Get a native speaker to review the homepage translations (Vietnamese and Japanese first).

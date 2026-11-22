# AirplaneMode (platform repo): notes for Claude

Self-hosted, offline-first apps for a travel router (NanoPi R6S main target, GL.iNet lite).
This repo is the **platform**: landing page, admin service (Wi-Fi control + connectivity
supervisor), Caddy front door, shared llama.cpp model, hardware profiles, docs. Apps live in
their own repos (today: `../airplanemode-darklight`) and plug in as pinned container images.
Start with `README.md`, then `docs/decisions/` (0001–0009). They explain *why* things are
the way they are.

## Environment (Jesse's Windows machine)

- All project folders live in `H:\Github`. H: is **exFAT**, so git needs
  `git config --global --add safe.directory H:/Github/<repo>` for each new repo.
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
  firewall redirect `airplanemode_portal`, dnsmasq address `/#/10.255.255.1` (offline popup).
  Don't touch other sections.
- **Offline popup only when there's no uplink at all** (no WAN and no wwan link), after 2
  supervisor ticks and never during a join grace period. Any uplink turns it off immediately,
  so it can't hide a hotel's real sign-in page.
- Lockout guard: never allow disabling or deleting the last enabled hotspot.
- Caddy is published **only on `LAN_IP`** (Docker's firewall rules can bypass OpenWrt's).
  The admin container uses **`dns: LAN_IP`** (Docker otherwise falls back to public DNS,
  which hotels block before sign-in).
- Apps: pinned image tags in `compose.yaml` (never `latest`), plus a route in `Caddyfile` and
  an entry in `portal/apps.json`; CI checks all three. Reserved paths: `/api`, `/admin`.
  App rules: `docs/app-contract.md`.
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

## Status (2026-11-21) and next steps

Done and pushed (CI green):
- platform stack and the shared model;
- landing page with captive-portal sign-in, the app menu and the "Get online" prompt;
- admin panel (upstream join, MAC clone, hotspots, saved networks);
- restricted router login;
- supervisor (auto-join, Ethernet first, offline popup);
- rebind-off and DNS fixes.

**No real hardware has been tested yet.** The R6S and adapters aren't bought. Parts:
- NanoPi R6S 8 GB with the metal case,
- ALFA AWUS036ACHM (MT7610U) for upstream,
- ALFA AWUS036ACM (MT7612U) for the hotspot,
- a short USB extension,
- 5 V/3 A USB-C power.

Open follow-ups:
1. Work through `docs/testing.md` → *On the device: checklist* once hardware arrives.
2. Put the adapter parts list into the README hardware section (offered, not done).
3. Darklight: understand free-text commands without the model (GL.iNet profile). Work in
   `../airplanemode-darklight`.
4. Measure model tokens/s on the R6S, update decision 0003, and maybe spike RKLLM (NPU).
5. CI warning: `actions/checkout@v4` / `setup-python@v5` run on deprecated Node 20. Bump to
   current majors.
6. Narrow the restricted login's `network`/`firewall` write access (decision 0008
   consequences) if it ever matters.

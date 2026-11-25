# Deployment: compose, hardware profiles, scripts

## Compose files

### `compose.yaml` (production)

The whole box in one file: project name `airplanemode`, default network named `airplanemode`
(the live OpenWrt test joins it). Service details are in [architecture.md](architecture.md#containers-compose-project-airplanemode-network-airplanemode).
Key points:

- **Pinned images, never `latest`.** The file is the release manifest (decision 0001). To
  update an app, change its tag here and commit.
- `proxy` publishes `"${LAN_IP:-127.0.0.1}:${HTTP_PORT:-8080}:80"`, **only on the LAN IP**.
  Docker's own iptables/nft rules can bypass OpenWrt's firewall, so binding 0.0.0.0 could
  expose the box to the hotel network.
- `admin` is built locally (`build: ./admin`, image `airplanemode-admin:local`). It gets
  `dns: [LAN_IP]`, `extra_hosts host.docker.internal:host-gateway`, `admin-data:/data`, and
  `./portal/apps.json:/config/apps.json:ro`.
- `llm` has `profiles: ["llm"]` and only runs when `COMPOSE_PROFILES=llm`. Command:
  `-hf ${LLM_MODEL} --no-mmproj --reasoning off --cache-ram ${LLM_CACHE_RAM:-256} --host 0.0.0.0 --port 8080 -t ${LLM_THREADS} -np ${LLM_SLOTS} -c ${LLM_CTX}`
  (text only, no thinking output, prompt cache capped so it stays under `mem_limit`; decision 0018),
  `cpuset: ${LLM_CPUS}` (empty = any core; the R6S profile uses `4-7`, the A76 cores; decision 0024),
  `LLAMA_CACHE=/models`. The model downloads from Hugging Face on first start (while online).
  It's never published, so apps use `http://llm:8080`. There's no API key (add `--api-key` if it's ever exposed).
- The model apps (darklight, guide, couch) also have `profiles: ["llm"]`, so they don't run on the
  GL.iNet lite profile (decision 0017).
- Apps that use the model get `LLM_URL: ${LLM_URL-http://llm:8080}`. Note the **`-` without `:`**:
  an *unset* var gives the default, but a var *set to empty* (`LLM_URL=` in `glinet.env`) stays empty,
  so the apps don't try to reach a model.
- All services: `restart: unless-stopped`, `mem_limit: ${<NAME>_MEM:-default}`.

### `compose.dev.yaml` (laptop override)

`docker compose -f compose.yaml -f compose.dev.yaml up -d --build` → http://localhost:8080,
admin password `dev`.
- `proxy.ports: !override ["${HTTP_PORT:-8080}:80"]` (all interfaces, for the laptop).
- `admin`: `ROUTER_BACKEND=mock`, `ADMIN_PASSWORD=${ADMIN_PASSWORD:-dev}`,
  `SUPERVISE_INTERVAL=5`, `JOIN_GRACE=6`, `dns: !reset []`.
- Each app: `image: airplanemode-<id>:dev`, `build: ../airplanemode-<id>`. The sibling repos must be
  checked out next to this one: darklight, drop, cinema, quickshot, doom, guide, gatechange,
  passport, wayfarer, couch.

Windows note: port 8000 is reserved by Windows, so the stack uses 8080.

## Hardware profiles (`hardware/*.env`)

Copy one to `.env` (`cp hardware/nanopi-r6s.env .env`). Device differences live **only**
here, not in separate compose files (decision 0002).

| Var | `nanopi-r6s.env` | `glinet.env` | Used by |
|---|---|---|---|
| `COMPOSE_PROFILES` | `llm` | (empty) | compose (whether `llm` runs) |
| `HTTP_PORT` | 8080 (LuCI has 80) | 8080 (GL.iNet panel has 80) | proxy publish, Caddy redirect, popup DNAT, router-setup |
| `LAN_IP` | 192.168.2.1 (FriendlyWrt default) | 192.168.8.1 (GL.iNet default) | proxy bind, Caddy, admin DNS + DNAT, router-setup |
| `LLM_URL` | (unset → `http://llm:8080`) | empty (no model) | apps |
| `LLM_MODEL` | `unsloth/gemma-4-E2B-it-GGUF:Q4_K_M` (decision 0018) | n/a | llm |
| `LLM_THREADS` / `LLM_SLOTS` / `LLM_CTX` / `LLM_MEM` | 4 / 2 / 4096 / 3500m | n/a | llm |
| `LLM_CPUS` / `LLM_CACHE_RAM` | `4-7` / 256 (compose defaults: any core / 256) | n/a | llm (decision 0024) |
| `PROXY_MEM` / `ADMIN_MEM` | 64m / 96m | 48m / 80m | |
| `DARKLIGHT_MEM` / `DROP_MEM` / `CINEMA_MEM` / `QUICKSHOT_MEM` / `DOOM_MEM` / `GUIDE_MEM` | 256m / 128m / 64m / 64m / 32m / 256m | n/a / 128m / 64m / 64m / 32m / n/a (no model apps) | apps |
| `GATECHANGE_MEM` / `PASSPORT_MEM` / `WAYFARER_MEM` / `COUCH_MEM` | 64m each | 48m / 48m / 64m / n/a | apps (decision 0016) |
| `DROP_MAX_BYTES` / `DROP_MAX_FILE` | 4g / 2g | 1g / 512m | Drop |
| `ROUTER_BACKEND` / `ROUTER_URL` | `openwrt` / `http://host.docker.internal/ubus` | same | admin |

Other vars, not set in the profiles (compose defaults apply): `WAN_IFACE` (wan),
`AUTOJOIN` (1), `OFFLINE_PORTAL` (1), `DROP_KEEP_DAYS` (7). Secrets are added to `.env` by the
scripts: `ROUTER_USER`, `ROUTER_PASSWORD` (router-setup.sh) and `ADMIN_PASSWORD` (update.sh).

GL.iNet caveat: its own repeater manager also edits the upstream STA settings. Use one or the
other (`docs/hardware.md`). The GL.iNet profile is *untested on hardware*.

If the admin can't reach `ROUTER_URL` (*untested*): try `http://<LAN_IP>/ubus`, or allow
input from FriendlyWrt's `docker` firewall zone to the router (TCP 80).

## `scripts/router-setup.sh` (run once on the router, as root; re-runnable)

POSIX `sh`, `set -eu`. It `cd`s to the repo root.

0. Preflight: must be root (`id -u` = 0), `uci` must exist, `.env` must exist. It reads `LAN_IP`
   (default 192.168.2.1), `WAN_IFACE` (wan) and `HTTP_PORT` (8080) from `.env` via `envval`
   (the last matching line).
1. **Restricted login.** It copies `router/rpcd-acl-airplanemode.json` →
   `/usr/share/rpcd/acl.d/airplanemode.json` (mode 644). It generates a 24-char alphanumeric
   password from `/dev/urandom` and hashes it with `uhttpd -m` (the hash must start with `$`).
   It finds an existing `rpcd.@login[i]` with `username=airplanemode`, or adds one. It sets
   `username`, `password=<hash>`, resets the `read`/`write` lists to `airplanemode`, then runs
   `uci commit rpcd` (the script is root, so commit is fine here).
2. **DNS.** Unless `KEEP_REBIND_PROTECTION=1`, it sets `dhcp.@dnsmasq[0].rebind_protection=0`
   (hotel sign-in pages often live at private addresses). It removes any old
   `/airplane.mode/*` address entries and adds `/airplane.mode/$LAN_IP`. `uci commit dhcp`.
3. **Ethernet first.** If `network.$WAN_IFACE` exists, it sets `metric=10` and commits. Otherwise it
   prints a note to set `WAN_IFACE`.
4. **WireGuard** (skip with `SKIP_WIREGUARD=1`). It's already present if
   `/lib/netifd/proto/wireguard.sh` and `/usr/share/rpcd/ucode/luci.wireguard` exist. Otherwise it runs
   `apk update && apk add wireguard-tools luci-proto-wireguard`, or the same with `opkg`. Failure
   (offline) gives a warning, not an error.
5. **Restart services** via `svc()`: it backgrounds the command and kills it after 60 s. Busybox may
   lack `timeout`, and netifd can hang in containers. It runs `rpcd restart`, `dnsmasq restart` and
   `network reload`, each with a warning on timeout.
6. **Write `.env`:** it drops the old `ROUTER_USER`/`ROUTER_PASSWORD` lines (and with them any
   stored root password), appends `ROUTER_USER=airplanemode`, `ROUTER_PASSWORD=<pw>`, writes
   the file in place (`cat tmp > .env`, which keeps the inode) and sets `chmod 600`.
7. It prints a summary. If the compose `admin` service exists, it runs `docker compose up -d admin` to
   pick up the new login.

Tested on OpenWrt 24.10 in Docker (idempotent, with the ACL enforced). *Untested* on FriendlyWrt
hardware: it needs `uhttpd` for both `/ubus` and `uhttpd -m`. If a build serves LuCI with
nginx, check where `/ubus` lives.

## `scripts/update.sh` (run on the box while online)

1. It requires `.env`.
2. If there's no `ADMIN_PASSWORD=` with a value, it generates 16 alphanumeric chars, appends them,
   sets `chmod 600`, and **prints** the password once.
3. It warns if there's no `ROUTER_PASSWORD` (the admin can show status but can't change settings), or
   if `ROUTER_USER=root` (run router-setup.sh).
4. `git pull --ff-only` (a warning on failure, then it uses the local copy).
5. `docker compose pull --ignore-buildable`, then `docker compose up -d --build --remove-orphans`.
6. If the `llm` service is enabled, it polls `http://llm:8080/health` from inside the admin container
   (Python urllib) every 5 s, for up to 10 min (exit 1 on timeout).
7. If `guide` exists, it polls `http://guide:8000/health` until `content_ready` is true (up to 20 min, a
   warning and break on timeout). The guide downloads ~260 MB of Wikivoyage.
8. `docker image prune -f`. It prints `http://<box-ip>:<HTTP_PORT>/`.

The admin container doubles as the in-network HTTP client for these health checks.

## The installer (`../airplanemode-site/install.sh`, decisions 0011, 0012, 0019)

A separate repo, live at https://airplanemode.xin (GitHub Pages, decision 0019). The README's
one-liner fetches `https://airplanemode.xin/install.sh`. It's a **thin wrapper**: preflight (OpenWrt, arm64, Docker +
compose, online, space, radios), clone to `/opt/airplanemode`, profile → `.env` with the LAN
IP read from uci, then this repo's `router-setup.sh` and `update.sh`. Afterwards it tests the
ubus login and falls back to `ROUTER_URL=http://<LAN_IP>/ubus`. It uses curl because OpenWrt
24.10's uclient-fetch failed TLS to GitHub.
**If you change these scripts' env flags, add a `hardware/*.env`, or change the install path,
update the installer and its page too.**

## Manual install (README)

Flash FriendlyWrt → plug in both adapters (two radios in LuCI) → set a root password →
get online once → then:
```sh
git clone https://github.com/jessewolcott/airplanemode.git /opt/airplanemode
cd /opt/airplanemode && cp hardware/nanopi-r6s.env .env   # fix LAN_IP if needed
sh scripts/router-setup.sh && sh scripts/update.sh
```
Then open `http://airplane.mode:8080/admin/`. Setup downloads about 3.8 GB.

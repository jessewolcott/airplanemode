# AirplaneMode

**A travel router that brings its own apps.** AirplaneMode turns a small router into a
private hotspot for your devices that:

- **joins the hotel, airport or plane Wi-Fi once, and shares it.** One person gets through
  the "accept the terms" page, and every phone, laptop and tablet on your hotspot is online.
  No more signing each device in, or paying per device.
- **broadcasts your own Wi-Fi network,** with the same name and password everywhere you go,
  so your devices connect automatically in every new place.
- **runs apps locally,** games and tools that work with no internet at all, including a small
  language model built into the box.
- **has a simple admin panel** for picking the network to join and managing your hotspots,
  from any phone.

This is the **platform repo**: the landing page, admin panel, shared services, hardware
profiles and design docs. Each app lives in its own repo and plugs in as a container image.

| Repo | What it is |
|---|---|
| [airplanemode](https://github.com/jessewolcott/airplanemode) | this repo: landing page, admin panel, router control, shared model, docs |
| [airplanemode-darklight](https://github.com/jessewolcott/airplanemode-darklight) | *The Dark Light*, a text adventure narrated by the on-board model |

## How it works

```mermaid
flowchart LR
    hotel["Hotel / airport / plane Wi-Fi<br/>(sign-in page)"]
    subgraph box["AirplaneMode box (OpenWrt)"]
        sta["Wi-Fi adapter 1<br/>joins upstream"]
        nat["Router + firewall<br/>(NAT: one device to the hotel)"]
        ap["Wi-Fi adapter 2<br/>your hotspot"]
        subgraph docker["Docker"]
            caddy["Caddy :8080<br/>landing page"]
            admin["admin<br/>status + Wi-Fi settings"]
            llm["llm<br/>Qwen2.5-1.5B"]
            apps["apps<br/>/darklight/ ..."]
        end
    end
    devices["Your phones, laptops, tablets"]
    hotel --- sta --- nat --- ap --- devices
    devices -. "http://airplane.mode:8080" .-> caddy
    caddy --> admin & apps
    apps --> llm
    admin -. "router's own API (ubus)" .-> nat
```

- **Sharing one sign-in.** All your devices reach the hotel network through the router, so
  the hotel sees a single device. The landing page probes the internet the same way phones
  do. When it finds a sign-in page, it shows **"This Wi-Fi needs a sign-in"** with a button
  that opens it. One person signs in, and everyone is online. For hotels that tie the login
  to a particular device, the admin panel can make the router use your phone's MAC address
  (*clone MAC*).
- **Changing Wi-Fi settings safely.** The admin service doesn't run with special
  privileges. It changes settings through the router's own management API, the same one the
  router's built-in admin page (LuCI) uses. It refuses to turn off your last working hotspot,
  so you can't lock yourself out.
- **Offline first.** Everything is downloaded ahead of time (`scripts/update.sh`, while
  online). The apps work in airplane mode, hence the name.

## Hardware you need

### Recommended: NanoPi R6S (full: everything, including the language model)

| Part | Why | Notes |
|---|---|---|
| **FriendlyElec NanoPi R6S** (8 GB RAM, 32 GB eMMC) | the box: RK3588S, two 2.5 GbE ports, enough RAM for the model | Comes in a metal case that doubles as the heatsink. |
| **2 USB Wi-Fi adapters, MediaTek chipset** | **The R6S has no built-in Wi-Fi.** One adapter joins the hotel network, the other runs your hotspot. | Pick chipsets with drivers built into OpenWrt (mt76): **MT7612U** (Wi-Fi 5, well proven in both modes, e.g. ALFA AWUS036ACM) or **MT7921AU** (Wi-Fi 6, newer). Avoid Realtek adapters, whose drivers are hit and miss in hotspot mode. |
| **USB-C power, 5 V / 3 A or better** | the R6S plus two adapters | A good USB-C PD power bank works too. |
| *Optional:* short USB extension cable | moves an adapter off the metal case | Better range, and keeps the two adapters apart. |

The R6S has one USB 3.0 and one USB 2.0 port, so both adapters fit without a hub. One
adapter can do both jobs, but both networks then share one channel and get slower.

### Lite: GL.iNet travel routers (no language model)

GL.iNet routers already have Wi-Fi and their own repeater mode. AirplaneMode can run on
**arm64** models with Docker installed on USB/SD storage, ideally with **1 GB of RAM**. Apps
run in their plain mode, without the model. *Not yet tested on real GL.iNet hardware.* See
[docs/hardware.md](docs/hardware.md) for the caveats.

## What's tested so far

| Piece | Status |
|---|---|
| Landing page, app menu, sign-in flow, admin panel | tested end to end in the browser against the mock router |
| Admin API (auth, validation, lockout guard, captive flow) | automated tests, run in CI |
| Router control (join, MAC clone, firewall, hotspots) | tested against a real OpenWrt 24.10 API in Docker (`admin/tests/test_openwrt_live.py`) |
| Live link status, scanning, reloading Wi-Fi on real radios | **needs a real device:** see the checklist in [docs/testing.md](docs/testing.md) |
| Full stack with the 1.5B model | tested on a PC with Docker Desktop; speed on the R6S not yet measured |

## Set it up

1. **Flash FriendlyWrt** to the R6S's eMMC, following FriendlyElec's NanoPi R6S wiki.
   FriendlyWrt is OpenWrt with Docker included.
2. **Plug in both Wi-Fi adapters**, then check that two radios show up in the router's admin
   page (LuCI → Network → Wireless).
3. **Set a root password** in LuCI (System → Administration). The admin panel uses it to
   change Wi-Fi settings.
4. **Get the box online once,** by Ethernet or by joining a Wi-Fi network in LuCI. Setup
   downloads about 1.5 GB of images and model files.
5. **Install AirplaneMode** from the router's shell (SSH in as root):

   ```sh
   git clone https://github.com/jessewolcott/airplanemode.git /opt/airplanemode
   cd /opt/airplanemode
   cp hardware/nanopi-r6s.env .env           # or hardware/glinet.env
   echo "ROUTER_PASSWORD=<the root password from step 3>" >> .env
   # if your LAN isn't the default subnet, also fix LAN_IP in .env
   sh scripts/update.sh
   ```

   On the first run, `update.sh` generates an **admin password**, prints it and saves it in
   `.env`. Write it down.
6. **Give the box a friendly name,** `airplane.mode`, in the router's DNS
   ([docs/hardware.md](docs/hardware.md#the-airplanemode-name-both-devices)).
7. **Open `http://airplane.mode:8080/admin/`**, sign in, and rename your hotspot and set its
   password. Your devices remember it from then on.

## Using it on a trip

1. Power the box on and connect your devices to **your** hotspot as usual.
2. Open **`http://airplane.mode:8080`**. The landing page shows whether you're online.
3. Not connected yet? **admin → Internet source → Scan**, pick the hotel network, enter its
   password if it has one, then **Connect**.
4. If the hotel needs a sign-in, the landing page says so. Tap **Open the sign-in page**,
   accept the terms or enter your room number, then tap **I've signed in**. Everyone on
   your hotspot is now online.
5. No Wi-Fi at all, like on a plane without it? The apps still work.

Run `sh scripts/update.sh` occasionally while online to pick up new versions.

## Develop on a laptop

Everything runs on Docker Desktop. The admin service uses a **mock router** with fake nearby
networks and a fake hotel sign-in page, so you can try the whole flow without hardware:

```sh
git clone https://github.com/jessewolcott/airplanemode.git
git clone https://github.com/jessewolcott/airplanemode-darklight.git
cd airplanemode
cp hardware/nanopi-r6s.env .env
docker compose -f compose.yaml -f compose.dev.yaml up -d --build
# http://localhost:8080  (admin password in dev mode: "dev")
```

Join `Harbor_Hotel_Guest` in the admin panel to see the sign-in flow. After editing the
`Caddyfile`, run `docker compose restart proxy`. Tests: `python admin/tests/test_admin.py`.

## Repo layout

```
compose.yaml          the whole box: proxy, admin, shared model, apps (pinned versions)
compose.dev.yaml      laptop override: mock router, apps built from sibling checkouts
Caddyfile             routes: / landing page, /api + /admin admin service, /<app>/ apps
portal/               landing page + apps.json (the app menu)
admin/                admin service: status, captive-portal check, Wi-Fi control, admin UI
hardware/*.env        per-device settings (copy one to .env, then add your secrets)
scripts/update.sh     pull, build, start, pre-download the model while online
docs/app-contract.md  what an app must do to plug in
docs/hardware.md      device setup details and caveats
docs/testing.md       how to test, and the on-device checklist
docs/decisions/       why things are the way they are
```

## Adding an app

1. Create `airplanemode-<name>` following [docs/app-contract.md](docs/app-contract.md).
2. Tag a release so its CI publishes the arm64 image, and make the package public on GitHub.
3. Here: add the service to `compose.yaml`, a route to `Caddyfile`, and an entry to
   `portal/apps.json`. The `check` workflow fails if any of the three is missing.

## Security notes

- Only the front door (port 8080) is published, and it's bound to the router's **LAN
  address** (`LAN_IP` in `.env`), not to every interface. Docker's own firewall rules can
  bypass OpenWrt's, so this binding is what keeps it off the hotel network. The model and
  the apps aren't reachable directly. Check this on your device (see
  [docs/testing.md](docs/testing.md)).
- `.env` holds the admin password and the router's root password. It's created with mode
  600 and ignored by git. Anyone who can sign in to the admin panel can change your Wi-Fi
  settings, so pick a real password for your hotspot.
- The admin panel uses an HttpOnly, same-site session cookie and a custom request header
  against cross-site requests, and slows down wrong-password attempts.
- It's plain HTTP on your own LAN: there's no public name to get a certificate for. See
  [decision 0004](docs/decisions/0004-offline-first.md).

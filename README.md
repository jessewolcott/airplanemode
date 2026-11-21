# AirplaneMode

A set of self-hosted apps for a travel router. Everything runs on the box, works with no
internet, and is reachable from any device on its Wi-Fi.

This is the **platform repo**. It ties the apps together: one compose file for the whole box,
the front door (portal and reverse proxy), the shared language model, hardware profiles, and
the design decisions. Each app lives in its own repo and ships as a container image.

| Repo | What it is |
|---|---|
| [airplanemode](https://github.com/jessewolcott/airplanemode) | this repo: platform, shared services, docs |
| [airplanemode-darklight](https://github.com/jessewolcott/airplanemode-darklight) | The Dark Light, a text adventure narrated by the on-board model |

## Hardware

| Profile | Device | What runs |
|---|---|---|
| `nanopi-r6s` | FriendlyElec NanoPi R6S (RK3588S, 8 GB, FriendlyWrt) | everything, plus the shared model (Qwen2.5-1.5B) |
| `glinet` | GL.iNet arm64 routers with Docker on USB storage (e.g. Flint 2) | everything except the model; apps run in plain mode |

The R6S is the main target. GL.iNet support is untested on real hardware so far. See
[docs/hardware.md](docs/hardware.md) for setup and [the decision record](docs/decisions/0002-hardware-targets.md) for why.

## Install on the box

While the box has internet:

```sh
git clone https://github.com/jessewolcott/airplanemode.git && cd airplanemode
cp hardware/nanopi-r6s.env .env        # or hardware/glinet.env
sh scripts/update.sh                   # pulls images, starts everything, downloads the model
```

Then, from any device on the box's Wi-Fi, open `http://<box-ip>:8080`, or
`http://airplane.mode:8080` once the DNS name is set up ([docs/hardware.md](docs/hardware.md)).

Run `scripts/update.sh` again whenever the box is online to pick up new versions.

## Develop on a laptop

Clone the app repos next to this one, then build them locally instead of pulling releases:

```sh
cp hardware/nanopi-r6s.env .env
docker compose -f compose.yaml -f compose.dev.yaml up -d --build
# http://localhost:8080
```

Docker Desktop on Windows/macOS runs the same images. arm64 builds come from each app's CI.

## Layout

```
compose.yaml          the whole box: proxy, shared model, apps (pinned versions)
compose.dev.yaml      override: build apps from sibling checkouts
Caddyfile             routes: / -> portal, /<app>/ -> app
portal/               landing page + apps.json (the app list)
hardware/*.env        per-device settings (copy one to .env)
scripts/update.sh     pull, start, and pre-download the model while online
docs/app-contract.md  what an app must do to plug in
docs/decisions/       why things are the way they are
```

## Adding an app

1. Create `airplanemode-<name>` following [docs/app-contract.md](docs/app-contract.md).
2. Tag a release so CI publishes the arm64 image.
3. Here: add the service to `compose.yaml`, a route to `Caddyfile`, and an entry to
   `portal/apps.json`. The `check` workflow fails if any of the three is missing.

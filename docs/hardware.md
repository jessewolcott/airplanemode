# Hardware setup

Untested steps are marked *untested*. Update this file as each device is actually set up.

## NanoPi R6S (main target)

- RK3588S: 4x Cortex-A76 + 4x Cortex-A55, 8 GB RAM, 32 GB eMMC, two 2.5 GbE ports + one
  1 GbE, one USB 3.0 + one USB 2.0 port. **No built-in Wi-Fi.**
- OS: **FriendlyWrt** (FriendlyElec's OpenWrt build). It ships with Docker. Flash it to eMMC.
- LuCI uses port 80, so AirplaneMode uses **8080**, bound to the LAN address
  (`LAN_IP=192.168.2.1`, FriendlyWrt's default).
- The model runs on the 4 fast A76 cores (`LLM_THREADS=4`). Expect roughly 8-15 tokens/s for
  Qwen2.5-1.5B Q4 (*estimate, measure on the device*). Keep it cool: it throttles without airflow.
- The RK3588S also has a 6 TOPS NPU. llama.cpp doesn't use it; Rockchip's RKLLM does
  (see decision 0003).

### Wi-Fi adapters

The R6S needs USB Wi-Fi adapters: one **client** adapter that joins the hotel network
(upstream), and one **hotspot** adapter (AP) for your devices.

- Use MediaTek chipsets with drivers in OpenWrt's kernel (`mt76`): **MT7612U** (Wi-Fi 5,
  long track record in both client and AP mode) or **MT7921AU** (Wi-Fi 6, newer, so check
  that your FriendlyWrt build lists it). Install the matching packages if they're missing:
  `kmod-mt76x2u` for MT7612U, `kmod-mt7921u` for MT7921AU (*untested: confirm package names
  on your FriendlyWrt version*).
- Avoid Realtek-based adapters: hotspot mode is unreliable with their drivers.
- After plugging them in, both should appear as radios in **LuCI → Network → Wireless**
  (`radio0`, `radio1`). The admin panel lists the same radios.
- One adapter can carry both a client and a hotspot interface, but they're then locked to the
  upstream network's channel and share its airtime. Two adapters are better.

### Letting the admin service reach the router's API

The admin container talks to the router's management API (`/ubus`, served by uhttpd) at
`ROUTER_URL`, which defaults to `http://host.docker.internal/ubus`, the Docker host as seen
from the container. If the admin panel says it can't reach the router's API (*untested*):

1. Try `ROUTER_URL=http://192.168.2.1/ubus` (the LAN address) in `.env`, then
   `docker compose up -d admin`.
2. If that also fails, FriendlyWrt's firewall is blocking traffic from the `docker` zone to
   the router itself. In LuCI → Network → Firewall, allow **input** from the `docker` zone,
   or add a rule allowing TCP 80 from `docker` to *Device (input)*.

## GL.iNet (lite)

- Only **arm64** models, ideally with 1 GB RAM. 512 MB models are a stretch. Older MIPS
  models are out: the images are arm64 only.
- Docker comes from opkg (`dockerd`, `docker`, `docker-compose`) and needs USB or SD storage
  for its data (*untested: confirm package names and storage setup on the actual firmware*).
- The admin panel is on port 80, so AirplaneMode uses **8080** on `LAN_IP=192.168.8.1`.
- No model on this profile. Apps run in their plain mode.
- **Memory.** The limits in `hardware/glinet.env` add up to about 950 MB, but they are ceilings,
  not use: on the dev stack the proxy, admin and all ten apps use about 420 MB together when
  idle (the Travel Guide ~80 MB, the rest 20–40 MB each). Switching an app off in the admin
  panel only hides it from the menu; it keeps running. *Untested: measure on a real 1 GB
  GL.iNet with a few people playing before relying on it.*
- **Repeater caveat.** GL.iNet's firmware has its own repeater manager, which also edits the
  upstream Wi-Fi settings. Using both it and AirplaneMode's *Internet source* panel can make
  them fight. On GL.iNet, pick one: either use GL.iNet's own repeater page for the upstream
  network and only use AirplaneMode's hotspot settings, or turn GL.iNet's repeater off.

## The `airplane.mode` name (both devices)

`scripts/router-setup.sh` sets this up: it adds `address=/airplane.mode/<LAN_IP>` to dnsmasq
(tested on OpenWrt 24.10). Then open `http://airplane.mode:8080` from any device on your
hotspot. Devices using their own encrypted DNS (Android "Private DNS", iCloud Private Relay)
may skip the router's DNS. For those, use `http://<LAN_IP>:8080`.

## WAN interface name

`router-setup.sh` gives the wired WAN route metric 10, and the admin service gives the Wi-Fi
uplink (`wwan`) metric 20, so a cable always wins. It assumes the WAN interface is called
`wan`, as on FriendlyWrt and GL.iNet. If yours differs (LuCI → Network → Interfaces), set
`WAN_IFACE=<name>` in `.env` and re-run the script.

## DNS rebind protection and hotel sign-in pages

OpenWrt's dnsmasq refuses DNS answers that point at private addresses ("rebind protection").
Hotel and airline sign-in pages very often live at exactly such addresses, so with protection
on they fail to load, both in the phone's popup and from the landing page. `router-setup.sh`
turns it off. GL.iNet firmware makes a similar trade-off for its repeater mode. To keep it
on: `KEEP_REBIND_PROTECTION=1 sh scripts/router-setup.sh`.

# Hardware setup

Untested steps are marked. Update this file as each device is actually set up.

## NanoPi R6S (main target)

- RK3588S: 4x Cortex-A76 + 4x Cortex-A55, 8 GB RAM, 32 GB eMMC, two 2.5 GbE ports + one 1 GbE.
- OS: **FriendlyWrt** (FriendlyElec's OpenWrt build). It ships with Docker. Flash it to eMMC.
- Docker data on eMMC is fine to start with. Move it to a USB SSD if writes become a concern.
- LuCI (the router admin page) uses port 80, so AirplaneMode uses **8080**.
- The model runs on the 4 fast A76 cores (`LLM_THREADS=4`). Expect roughly 8-15 tokens/s for
  Qwen2.5-1.5B Q4 (*estimate, measure on the device*). Keep it cool: the R6S throttles without
  airflow.
- The RK3588S also has a 6 TOPS NPU. llama.cpp doesn't use it. Rockchip's RKLLM runtime does,
  and is worth a look later (see decision 0003).

Steps (*untested*):

```sh
opkg update && opkg install git-http      # if git isn't already present
git clone https://github.com/jessewolcott/airplanemode.git /opt/airplanemode
cd /opt/airplanemode && cp hardware/nanopi-r6s.env .env && sh scripts/update.sh
```

## GL.iNet (lite)

- Only **arm64** models with at least 1 GB RAM: for example Flint 2 (GL-MT6000). The 512 MB
  models (e.g. Beryl AX) are a stretch. Older MIPS models are out: the images are arm64 only.
- Docker comes from opkg (`dockerd`, `docker`, `docker-compose`) and needs USB or SD storage
  for its data. *Untested: confirm the package names and storage setup on the actual firmware.*
- The admin panel uses port 80, so AirplaneMode uses **8080**.
- No model on this profile. Apps run in their plain mode.

## The `airplane.mode` name (both devices)

Both firmwares use dnsmasq. Point a name at the box's LAN address, in LuCI under
**Network → DHCP and DNS → Hostnames**, or from a shell (*untested*):

```sh
uci add dhcp domain
uci set dhcp.@domain[-1].name='airplane.mode'
uci set dhcp.@domain[-1].ip='192.168.8.1'    # the box's LAN IP (GL.iNet default shown)
uci commit dhcp && /etc/init.d/dnsmasq restart
```

Then open `http://airplane.mode:8080` from any device on the box's Wi-Fi.

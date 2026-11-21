# Testing

## On a laptop (no hardware)

```sh
python admin/tests/test_admin.py        # admin API against the mock router (also runs in CI)
docker compose -f compose.yaml -f compose.dev.yaml up -d --build
```

Then try the flow in a browser at `http://localhost:8080` (admin password: `dev`):
admin → Scan → `Harbor_Hotel_Guest` → Connect → landing page shows **Sign-in needed** →
Open the sign-in page → accept → **Online**.

## Against a real OpenWrt API (no Wi-Fi hardware)

OpenWrt's official root filesystem runs in Docker with the real ubus, rpcd and uhttpd. That
checks every router call the admin service makes, apart from anything touching real radios.
Use a throwaway container, never a router you depend on.

```sh
docker run -d --name owrt-test --network airplanemode openwrt/rootfs:x86-64-24.10.4 /sbin/init
docker exec owrt-test sh -c 'printf "testpw\ntestpw\n" | passwd root'
# give it two pretend radios and two hotspots:
docker exec -i owrt-test sh -c 'cat > /etc/config/wireless' <<'EOF'
config wifi-device 'radio0'
	option type 'mac80211'
	option band '2g'
	option channel '6'
config wifi-device 'radio1'
	option type 'mac80211'
	option band '5g'
	option channel '36'
config wifi-iface 'default_radio0'
	option device 'radio0'
	option network 'lan'
	option mode 'ap'
	option ssid 'FriendlyWrt-2G'
	option encryption 'psk2'
	option key 'password'
config wifi-iface 'default_radio1'
	option device 'radio1'
	option network 'lan'
	option mode 'ap'
	option ssid 'FriendlyWrt'
	option encryption 'sae-mixed'
	option key 'password'
EOF
docker compose build admin
docker run --rm --network airplanemode -v "$PWD/admin:/app:ro" \
  -e ROUTER_URL=http://owrt-test/ubus -e ROUTER_PASSWORD=testpw \
  airplanemode-admin:local python tests/test_openwrt_live.py
```

To test the **restricted login** too, install it the way a real router would, then run the
same test as that user. It adds checks that out-of-scope calls are refused:

```sh
docker exec owrt-test mkdir -p /opt/airplanemode/scripts /opt/airplanemode/router
docker cp scripts/router-setup.sh owrt-test:/opt/airplanemode/scripts/
docker cp router/rpcd-acl-airplanemode.json owrt-test:/opt/airplanemode/router/
docker cp hardware/nanopi-r6s.env owrt-test:/opt/airplanemode/.env
docker exec owrt-test sh /opt/airplanemode/scripts/router-setup.sh
PW=$(docker exec owrt-test sh -c 'grep ^ROUTER_PASSWORD= /opt/airplanemode/.env | cut -d= -f2')
docker run --rm --network airplanemode -v "$PWD/admin:/app:ro" \
  -e ROUTER_URL=http://owrt-test/ubus -e ROUTER_USER=airplanemode -e "ROUTER_PASSWORD=$PW" \
  airplanemode-admin:local python tests/test_openwrt_live.py
docker rm -f owrt-test
```

Last run (2026-11-21, OpenWrt 24.10.4): all checks pass. That covers login and bad
passwords, joining an open network and a WPA2 network with MAC clone, the `wwan` interface
plus firewall zone, leaving, saving/adding/deleting hotspots, the lockout guard, and changes
being committed. In an unprivileged container netifd itself hangs, so live link status and
Wi-Fi reloads can't be checked there.

## On the device: checklist

Things only real hardware can prove. Tick them off here as they're verified.

- [ ] `sh scripts/router-setup.sh` works on the real firmware. It needs `uhttpd` (for
      `uhttpd -m` and the `/ubus` endpoint): if this build serves LuCI with nginx instead,
      check where `/ubus` lives and how to hash the password.
- [ ] Both USB adapters appear as `radio0` / `radio1` in LuCI and in the admin panel.
- [ ] The admin container can reach `ROUTER_URL` (the admin panel loads Wi-Fi settings).
- [ ] **Scan** lists nearby networks on each radio.
- [ ] **Connect** to an open network: `wwan` gets an address within ~20 s, and the landing
      page shows the right state.
- [ ] Saving Wi-Fi changes actually reloads the radios (`uci apply` triggers netifd). If
      not, add an explicit `network reload` to `_apply()` in `admin/router.py`.
- [ ] A real captive portal (hotel or café) is detected, **Open the sign-in page** loads it
      from a phone on the hotspot, and after signing in a second device is online without
      signing in.
- [ ] **Passthrough popup:** a phone that joins the hotspot *after* the box is on the hotel
      network shows the hotel's own "Sign in to network" popup, and the page loads (this is
      what DNS rebind protection used to break).
- [ ] The admin container's DNS (`dns: LAN_IP`) works: `/api/status` says *captive*, not
      *offline*, at a hotel before signing in.
- [ ] **Offline popup:** with no uplink, after ~1 minute an iPhone and an Android phone
      joining the hotspot show the AirplaneMode page as a sign-in screen; it stops as soon
      as Ethernet or Wi-Fi comes back. Check `fw4 print` contains `jump dstnat_lan` for the
      LAN interface (an unprivileged container can't show that).
- [ ] **Auto-join:** move between two saved networks (or switch one off): the box joins the
      one in range within ~1 minute, and skips one whose password changed.
- [ ] **Ethernet first:** with Wi-Fi up, plug a cable into WAN. The landing page says *Online
      via Ethernet* and traffic leaves via the cable (`ip route` shows metric 10 before 20).
- [ ] MAC clone: a network that only lets one signed-in phone through works after cloning
      that phone's MAC.
- [ ] Changing the hotspot you're connected to: devices drop, reconnect with the new
      settings, and the change survives a reboot.
- [ ] **Port 8080 is not reachable from the upstream side** (from another device on the
      hotel network, `http://<box-upstream-ip>:8080` must fail).
- [ ] The model's speed on the R6S (tokens/s). Update decision 0003 with the number.
- [ ] Temperature and power draw with both adapters and the model busy.

#!/bin/sh
# Run ONCE on the router, as root. Safe to run again (it also rotates the admin service's
# router password). It prepares the router for AirplaneMode:
#
#  1. A dedicated router login, "airplanemode", for the admin service. It can only read and
#     change Wi-Fi-related settings (wireless, network, firewall, dhcp), read Wi-Fi status,
#     and apply changes. No shell commands, files, system settings or passwords. Its random
#     password goes into .env, so the router's root password is never stored.
#  2. DNS settings for hotel sign-in pages: DNS rebind protection off (sign-in pages often
#     live at private addresses, which rebind protection blocks), and the name
#     "airplane.mode" pointing at this box.
#     To keep rebind protection on anyway:  KEEP_REBIND_PROTECTION=1 sh scripts/router-setup.sh
#  3. Ethernet before Wi-Fi: the wired WAN gets route metric 10 (the Wi-Fi uplink gets 20),
#     so a plugged-in cable always wins.
#  4. WireGuard support for the home VPN (installs wireguard-tools and luci-proto-wireguard
#     if they're missing; needs internet). Skip with SKIP_WIREGUARD=1.
set -eu
cd "$(dirname "$0")/.."

USER_NAME=airplanemode
ACL_SRC=router/rpcd-acl-airplanemode.json
ACL_DST=/usr/share/rpcd/acl.d/airplanemode.json

[ "$(id -u)" = 0 ] || { echo "Run this on the router as root." >&2; exit 1; }
command -v uci >/dev/null || { echo "This doesn't look like OpenWrt (no uci)." >&2; exit 1; }
[ -f .env ] || { echo "No .env yet. First: cp hardware/<device>.env .env" >&2; exit 1; }

envval() { grep -E "^$1=" .env | tail -n 1 | cut -d= -f2-; }
LAN_IP=$(envval LAN_IP); LAN_IP=${LAN_IP:-192.168.2.1}
WAN_IFACE=$(envval WAN_IFACE); WAN_IFACE=${WAN_IFACE:-wan}
HTTP_PORT=$(envval HTTP_PORT); HTTP_PORT=${HTTP_PORT:-8080}

# ---- 1. restricted login for the admin service ----
cp "$ACL_SRC" "$ACL_DST"
chmod 644 "$ACL_DST"

pw=$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 24)
hash=$(uhttpd -m "$pw")
case "$hash" in '$'*) ;; *) echo "Couldn't hash the password with uhttpd -m." >&2; exit 1 ;; esac

section=""
i=0
while uci -q get "rpcd.@login[$i]" >/dev/null; do
	if [ "$(uci -q get "rpcd.@login[$i].username")" = "$USER_NAME" ]; then section="rpcd.@login[$i]"; fi
	i=$((i + 1))
done
if [ -z "$section" ]; then
	uci add rpcd login >/dev/null
	section="rpcd.@login[-1]"
fi
uci set "$section.username=$USER_NAME"
uci set "$section.password=$hash"
uci -q delete "$section.read" || true
uci -q delete "$section.write" || true
uci add_list "$section.read=airplanemode"
uci add_list "$section.write=airplanemode"
uci commit rpcd

# ---- 2. DNS: hotel sign-in pages + the airplane.mode name ----
if [ "${KEEP_REBIND_PROTECTION:-0}" = 1 ]; then
	echo "Keeping DNS rebind protection on (some hotel sign-in pages may not load)."
else
	uci set dhcp.@dnsmasq[0].rebind_protection='0'
fi
name_entry="/airplane.mode/$LAN_IP"
for a in $(uci -q get dhcp.@dnsmasq[0].address); do
	case "$a" in /airplane.mode/*) uci del_list "dhcp.@dnsmasq[0].address=$a" ;; esac
done
uci add_list "dhcp.@dnsmasq[0].address=$name_entry"
uci commit dhcp

# ---- 3. Ethernet before Wi-Fi ----
if uci -q get "network.$WAN_IFACE" >/dev/null; then
	uci set "network.$WAN_IFACE.metric=10"
	uci commit network
else
	echo "note: no network.$WAN_IFACE interface; set WAN_IFACE in .env if your wired WAN has another name." >&2
fi

# ---- 4. WireGuard for the home VPN ----
if [ "${SKIP_WIREGUARD:-0}" = 1 ]; then
	:
elif [ -f /lib/netifd/proto/wireguard.sh ] && [ -f /usr/share/rpcd/ucode/luci.wireguard ]; then
	echo "WireGuard support is already installed."
elif command -v apk >/dev/null 2>&1; then
	{ apk update && apk add wireguard-tools luci-proto-wireguard; } >/dev/null 2>&1 \
		|| echo "warning: couldn't install WireGuard (offline?). The home VPN won't work until you re-run this online." >&2
elif command -v opkg >/dev/null 2>&1; then
	{ opkg update && opkg install wireguard-tools luci-proto-wireguard; } >/dev/null 2>&1 \
		|| echo "warning: couldn't install WireGuard (offline?). The home VPN won't work until you re-run this online." >&2
fi

# don't let a stuck service hang the whole script
# (busybox 'timeout' isn't in every build, so do it by hand)
svc() {
	"$@" &
	p=$!; n=0
	while kill -0 "$p" 2>/dev/null; do
		n=$((n + 1))
		[ "$n" -gt 60 ] && { kill "$p" 2>/dev/null; return 1; }
		sleep 1
	done
	wait "$p"
}
svc /etc/init.d/rpcd restart || echo "warning: rpcd restart didn't finish; reboot the router if the admin panel can't log in." >&2
svc /etc/init.d/dnsmasq restart >/dev/null 2>&1 || echo "warning: dnsmasq restart didn't finish; run '/etc/init.d/dnsmasq restart' later." >&2
svc /etc/init.d/network reload >/dev/null 2>&1 || echo "warning: network reload didn't finish; run '/etc/init.d/network reload' later." >&2

# ---- point .env at the new login (and drop any stored root password) ----
tmp=$(mktemp)
grep -vE '^(ROUTER_USER|ROUTER_PASSWORD)=' .env >"$tmp" || true
printf 'ROUTER_USER=%s\nROUTER_PASSWORD=%s\n' "$USER_NAME" "$pw" >>"$tmp"
cat "$tmp" >.env
rm -f "$tmp"
chmod 600 .env

echo "Router ready:"
echo "  - login '$USER_NAME' (Wi-Fi settings only); its password is in .env"
echo "  - http://airplane.mode:${HTTP_PORT:-8080} points at $LAN_IP"
echo "  - Ethernet ($WAN_IFACE) is preferred over Wi-Fi"
[ -f /lib/netifd/proto/wireguard.sh ] && echo "  - WireGuard is available for the home VPN"
if docker compose ps --services 2>/dev/null | grep -qx admin; then
	docker compose up -d admin >/dev/null && echo "Restarted the admin service with the new login."
fi

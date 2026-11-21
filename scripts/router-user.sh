#!/bin/sh
# Run ONCE on the router, as root (and again any time to rotate the password).
#
# Creates a dedicated router login, "airplanemode", for the admin service. It can only
# read and change Wi-Fi-related settings (wireless, network, firewall), read Wi-Fi status,
# and apply changes. No shell commands, files, system settings or passwords.
# The login's random password is written to .env, so the router's root password
# never has to be stored anywhere.
set -eu
cd "$(dirname "$0")/.."

USER_NAME=airplanemode
ACL_SRC=router/rpcd-acl-airplanemode.json
ACL_DST=/usr/share/rpcd/acl.d/airplanemode.json

[ "$(id -u)" = 0 ] || { echo "Run this on the router as root." >&2; exit 1; }
command -v uci >/dev/null || { echo "This doesn't look like OpenWrt (no uci)." >&2; exit 1; }
[ -f .env ] || { echo "No .env yet. First: cp hardware/<device>.env .env" >&2; exit 1; }

# 1. access list
cp "$ACL_SRC" "$ACL_DST"
chmod 644 "$ACL_DST"

# 2. login with a fresh random password (stored only as a hash on the router)
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
/etc/init.d/rpcd restart

# 3. point .env at the new login (and drop any stored root password)
tmp=$(mktemp)
grep -vE '^(ROUTER_USER|ROUTER_PASSWORD)=' .env >"$tmp" || true
printf 'ROUTER_USER=%s\nROUTER_PASSWORD=%s\n' "$USER_NAME" "$pw" >>"$tmp"
cat "$tmp" >.env
rm -f "$tmp"
chmod 600 .env

echo "Router login '$USER_NAME' is ready, limited to Wi-Fi settings. Its password is in .env."
if docker compose ps --services 2>/dev/null | grep -qx admin; then
	docker compose up -d admin >/dev/null && echo "Restarted the admin service with the new login."
fi

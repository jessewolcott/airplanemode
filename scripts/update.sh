#!/bin/sh
# Run on the box while it HAS internet: pulls pinned images, builds the admin service,
# starts everything, and (on the llm profile) lets the model download so the box works offline later.
set -eu
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  echo "No .env yet. Pick a hardware profile first, e.g.:  cp hardware/nanopi-r6s.env .env" >&2
  exit 1
fi

# First run: generate an admin password and keep it in .env (which git ignores).
if ! grep -qE '^ADMIN_PASSWORD=.+' .env; then
  pw=$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 16)
  printf '\nADMIN_PASSWORD=%s\n' "$pw" >>.env
  chmod 600 .env
  echo "Generated an admin password: $pw"
  echo "(It's saved in .env. You'll need it for http://<box>:8080/admin/)"
fi
if ! grep -qE '^ROUTER_PASSWORD=.+' .env; then
  echo "warning: no router login in .env yet, so the admin panel can show status but can't change Wi-Fi settings." >&2
  echo "         Run once, as root:  sh scripts/router-user.sh" >&2
elif grep -qE '^ROUTER_USER=root$' .env; then
  echo "warning: the admin service is using the router's root login. Run 'sh scripts/router-user.sh'" >&2
  echo "         to switch it to a login limited to Wi-Fi settings." >&2
fi

git pull --ff-only || echo "warning: could not update the repo, using the local copy" >&2
docker compose pull --ignore-buildable
docker compose up -d --build --remove-orphans

if docker compose config --services | grep -qx llm; then
  echo "Waiting for the model to download and load (first run can take a few minutes)..."
  i=0
  until docker compose exec -T admin python -c "import urllib.request; urllib.request.urlopen('http://llm:8080/health', timeout=3)" 2>/dev/null; do
    i=$((i + 1)); [ "$i" -gt 120 ] && { echo "Model not ready after 10 minutes; check: docker compose logs llm" >&2; exit 1; }
    sleep 5
  done
  echo "Model ready."
fi

docker image prune -f >/dev/null
port=$(grep -E '^HTTP_PORT=' .env | cut -d= -f2)
echo "AirplaneMode is up: http://<box-ip>:${port:-8080}/  (admin: /admin/)"

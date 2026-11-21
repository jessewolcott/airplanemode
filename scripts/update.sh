#!/bin/sh
# Run on the box while it HAS internet: pulls pinned images, starts everything,
# and (on the llm profile) lets the model download so the box works offline later.
set -eu
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  echo "No .env yet. Pick a hardware profile first, e.g.:  cp hardware/nanopi-r6s.env .env" >&2
  exit 1
fi

git pull --ff-only || echo "warning: could not update the repo, using the local copy" >&2
docker compose pull
docker compose up -d --remove-orphans

if docker compose config --services | grep -qx llm; then
  echo "Waiting for the model to download and load (first run can take a few minutes)..."
  i=0
  until docker compose exec -T darklight python -c "import urllib.request; urllib.request.urlopen('http://llm:8080/health', timeout=3)" 2>/dev/null; do
    i=$((i + 1)); [ "$i" -gt 120 ] && { echo "Model not ready after 10 minutes; check: docker compose logs llm" >&2; exit 1; }
    sleep 5
  done
  echo "Model ready."
fi

docker image prune -f >/dev/null
echo "AirplaneMode is up on port ${HTTP_PORT:-$(grep -E '^HTTP_PORT=' .env | cut -d= -f2)}."

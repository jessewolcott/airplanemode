# 0005. One front door: Caddy on port 8080, apps under path prefixes

Date: 2026-11-21 · Status: accepted

## Context

Both target routers already use ports 80/443 for their own admin pages. Apps need stable
addresses that are easy to type on a phone, and there should be one place listing them.

## Decision

- A single Caddy container is the only thing published to the LAN, on port **8080**.
- `/` serves the portal, a static page that reads `portal/apps.json`. It's served by Caddy
  itself, so it needs no container of its own.
- Each app lives at `/<id>/`. Caddy strips the prefix, so apps don't need to know it, as long
  as their pages use relative URLs (app contract).
- A friendly name, `airplane.mode`, comes from the router's own dnsmasq, not from a container.

## Consequences

- Adding an app touches `compose.yaml`, `Caddyfile` and `portal/apps.json`. CI checks they
  stay in sync.
- Path prefixes rule out apps that insist on living at `/`. If one ever needs that, give it a
  subdomain (`<id>.airplane.mode`) via dnsmasq and a Caddy host block.

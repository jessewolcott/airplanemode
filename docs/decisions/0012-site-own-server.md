# 0012. The homepage goes on its own server, not GitHub Pages

Date: 2026-11-21 · Status: accepted (amends 0011)

## Context

Decision 0011 published `airplanemode-site` with GitHub Pages. The account's Pages site
has a custom domain, so the project landed under someone else's site, and the github.io
address redirected there over plain http. Jesse wants the site on a server of its own.

## Decision

- GitHub Pages is **off** for `airplanemode-site`. The site will be served from its own
  server, over **https only**. That server doesn't exist yet.
- Until then the install command fetches `install.sh` straight from the repo:
  `https://raw.githubusercontent.com/jessewolcott/airplanemode-site/main/install.sh`.
- The page builds its install command from the https address that serves it, so moving
  it needs no code change; only `install.sh`'s comments and the platform README name a URL.

## Consequences

- No public homepage for now; the README carries the install command.
- When the server exists: put the site there, then swap the raw URL in this repo's README
  and `install.sh`'s comments for the site's address.

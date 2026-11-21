# 0001. One platform repo, one repo per app, joined by images

Date: 2026-11-21 · Status: accepted

## Context

AirplaneMode is a set of independent apps on one small box. Each app has its own code,
release pace and tests. Something still has to decide what runs on the box, at which
versions, with which shared services.

## Decision

- `airplanemode` (this repo) owns the box: compose file, proxy and portal, shared services,
  hardware profiles, docs and decisions.
- Each app lives in `airplanemode-<id>` and publishes a versioned container image.
- The platform pins exact image tags in `compose.yaml`. That file is the release manifest:
  a commit here is a known-good combination of app versions.
- No git submodules and no source-level coupling. The only interface is the
  [app contract](../app-contract.md).

## Consequences

- An app can be developed, tested and released without touching the others.
- Upgrading the box is a reviewed diff of version numbers here.
- Cross-cutting changes (say, changing the contract) touch several repos. That's acceptable
  while the app count is small; revisit if it becomes routine.

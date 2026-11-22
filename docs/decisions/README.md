# Decisions

One short file per significant decision: the context, what we decided, and what it costs.
When a decision changes, add a new record that supersedes the old one rather than
rewriting history.

| # | Decision |
|---|---|
| [0001](0001-repo-layout.md) | One platform repo, one repo per app, joined by images |
| [0002](0002-hardware-targets.md) | NanoPi R6S first, GL.iNet as a lite profile, arm64 only |
| [0003](0003-shared-model-service.md) | One shared language model, served by llama.cpp |
| [0004](0004-offline-first.md) | Download while online, run without |
| [0005](0005-front-door.md) | Caddy on 8080, apps under path prefixes |
| [0006](0006-router-control-via-ubus.md) | Control the router through its own API (ubus), not a privileged container |
| [0007](0007-shared-captive-portal-sign-in.md) | One sign-in for everyone: NAT plus captive-portal detection |
| [0008](0008-least-privilege-router-login.md) | A Wi-Fi-only router login for the admin service |
| [0009](0009-connectivity-supervisor.md) | Staying connected: auto-join, Ethernet first, rebind off, offline popup |
| [0010](0010-home-vpn-devices-data-use.md) | Home VPN (WireGuard), connected devices with pause, data use per network |
| [0011](0011-install-site.md) | A homepage and one-line installer in their own repo (`airplanemode-site`) |
| [0012](0012-site-own-server.md) | The homepage goes on its own server, not GitHub Pages (superseded by 0019) |
| [0013](0013-cinema-drop-folders-app-switches.md) | Cinema, Drop folders and "downloads off" (admin), app switches on the menu |
| [0014](0014-quickshot.md) | QuickShot: a swipe feed over a Drop folder, with comments in its own volume |
| [0015](0015-doom.md) | Doom: PrBoom+ in the browser with touch controls, Freedoom, and the Layover airport episode made in code |
| [0016](0016-games-and-couch.md) | Gate Change and Passport (card games with rooms and bots), Wayfarer (trivia from Wikivoyage), The Couch (therapist simulator) |
| [0017](0017-no-model-apps-on-lite.md) | No model apps (Dark Light, Travel Guide, The Couch) on the GL.iNet lite profile |
| [0018](0018-gemma-4-e2b.md) | Shared model: Gemma 4 E2B instead of Qwen2.5-1.5B |
| [0019](0019-site-on-pages-own-domain.md) | The homepage on GitHub Pages under its own domain, airplanemode.xin (supersedes 0012) |

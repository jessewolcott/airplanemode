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

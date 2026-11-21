# 0002. Hardware targets: NanoPi R6S first, GL.iNet as a lite profile

Date: 2026-11-21 · Status: accepted

## Context

Typical travel routers have 512 MB-1 GB of RAM and weak CPUs. Docker plus a language model
doesn't fit in that. The NanoPi R6S is still pocket-sized and router-shaped, but has 8 GB of
RAM, an RK3588S, and Docker out of the box on FriendlyWrt.

## Decision

- **NanoPi R6S** is the main target and the reference for performance and memory budgets.
- **GL.iNet** arm64 models with Docker are a **lite** profile: same apps, no model.
- **arm64 only** on the box. Images are also built for amd64 so laptops can run them.
- Differences between devices live in `hardware/<device>.env` (compose profiles, memory
  limits, port, model settings), not in separate compose files.

## Consequences

- Every app must work without the model (see the app contract).
- Memory limits are set per device, so a new app needs a line in each hardware file.
- A future device is a new env file plus a row in the README.

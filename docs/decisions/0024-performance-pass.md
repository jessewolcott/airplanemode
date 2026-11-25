# 0024. Performance pass: model on the fast cores, Caddy sends Drop's files, zstd

Date: 2026-11-24 · Status: accepted

## Context

Before the R6S arrives, a look at where the box will spend its time: the shared model,
several phones streaming films from Drop (Sync Watch, decision 0023), and pages over the
hotspot. Measured in the dev stack on a laptop (x86, Docker Desktop), so the numbers below
show what changed, not what the R6S will do.

## Decision

- **The model runs on the RK3588S's four fast cores only** (`cpuset` from `LLM_CPUS`, `4-7`
  in `hardware/nanopi-r6s.env`). The chip has 4 Cortex-A55 and 4 Cortex-A76 cores; with
  threads on both kinds, every token waits for the slowest thread. Empty `LLM_CPUS` = any core
  (dev, other boxes). *Untested*: the core numbering on FriendlyWrt, and the gain.
- **Caddy sends Drop's files.** Drop still decides who gets a file (downloads off, the admin),
  then answers `X-Accel-Redirect: /files/<id>`, and Caddy serves the bytes from Drop's volume,
  mounted read-only in the proxy (ranges, ETag, sendfile). Drop does this only when Caddy
  asks (`X-Sendfile-Type: X-Accel-Redirect` request header), so apps calling Drop directly
  (Cinema, QuickShot) and Drop on its own still get the bytes from Drop. Measured: six
  parallel 50 MB downloads cost Drop's Python 250 ms of CPU; through Caddy, 30-70 ms in total.
- **`encode zstd gzip`** in Caddy: zstd where the browser takes it (cheaper for the box than
  gzip), gzip otherwise.
- **`--cache-ram` is a setting** (`LLM_CACHE_RAM`, still 256 MB).
- **Google's QAT Q4_0 build is one line away** (`LLM_MODEL=google/gemma-4-E2B-it-qat-q4_0-gguf`,
  3.1 GB like today's file), to try on the R6S with the evals from decision 0020.

## Looked at and left alone

- **Prompt caching already works.** All three model apps send `cache_prompt`, and the RAM
  cache restores a prompt into either slot. Measured with three ~1,000-token app prompts:
  the first request of each takes 2.8 s to read its prompt (4 laptop cores); every later one
  0.08 s, even with other apps in between. 256 MB held 6-9 such prompts (the tenth pushed the
  first out).
- **No warm-up request at boot.** llama.cpp already does a warm-up run when it loads
  (`--warmup`, on by default). Warming every app prompt (six therapists, three worlds, the
  guide) would need more than the cache holds; revisit with `LLM_CACHE_RAM` once the R6S's
  memory use is known.
- **Context stays at 4096** (2048 per slot): a smaller one would cut the guide's passages.
- **No new cache headers.** Every app's page is `no-cache` and its other files carry an ETag
  or Last-Modified, so a phone revalidates with a 304 (checked: Doom's 11 MB WAD and its
  engine). Long cache lifetimes would gain little on a LAN and risk old code after an update.

## Consequences

- The proxy can read Drop's volume. It serves from it only on Drop's say-so: a client's own
  `X-Accel-Redirect` header is ignored (checked), and Caddy serves only `/files/*` paths.
- Needs Drop 0.2.3 (pinned). With an older Drop, Caddy's extra header is ignored and Drop streams as before.
- The R6S numbers (tokens/s on cores 4-7 against all eight, QAT against Q4_K_M, the first
  answer of each app, streams) go in `docs/testing.md`'s checklist.

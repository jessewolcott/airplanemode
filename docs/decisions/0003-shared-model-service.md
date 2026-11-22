# 0003. One shared language model, served by llama.cpp

Date: 2026-11-21 · Status: accepted

## Context

Several apps may want a language model. Running one model per app would multiply memory
use, and the R6S can realistically hold one 1.5B-3B model alongside everything else.

## Decision

- One `llm` service: `ghcr.io/ggml-org/llama.cpp`, pinned to an exact build
  (`server-b12602` at the time of writing), serving an OpenAI-compatible API.
- Model on the R6S: **Qwen2.5-1.5B-Instruct, Q4_K_M** (~1.1 GB file, ~1.5 GB in RAM)
  (since decision 0018: Gemma 4 E2B),
  4 threads on the A76 cores, 2 parallel slots, 4096 tokens of context shared across slots.
- It runs only under the `llm` compose profile, so the GL.iNet profile simply leaves it out.
- It's reachable only on the internal Docker network (`http://llm:8080`). No published port,
  so no API key for now. Add `--api-key` if the port is ever exposed.
- Models are downloaded into a named volume while the box is online (`scripts/update.sh`)
  and are never fetched at the moment a user needs them.

## Alternatives considered

- **Ollama:** friendlier model management, but heavier, and llama.cpp's server already gives
  grammar-constrained JSON output, which the apps rely on.
- **Rockchip NPU (RKLLM):** could be several times faster on the RK3588S, but it needs
  converted models and a vendor runtime. Worth a spike once the apps settle; the
  OpenAI-style API keeps that swap invisible to apps.
- **3B model:** better output, roughly half the speed. Revisit after measuring 1.5B on the device.

## Consequences

- Apps share one queue. With 2 slots, a third request waits. Apps must time out and fall
  back gracefully.
- Changing the model is a one-line change in `hardware/nanopi-r6s.env`, but apps' prompts
  were tuned on specific models, so test before bumping.

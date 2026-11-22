# 0018. Shared model: Gemma 4 E2B instead of Qwen2.5-1.5B

Date: 2026-11-22 · Status: accepted (supersedes the model choice in 0003)

## Context

Qwen2.5-1.5B (decision 0003) fell short as the apps grew: The Couch's personas fell apart (the
Narcissist took on the user's problem as his own, the Nihilist drifted dismissive), the Travel
Guide showed users wrong answers, and The Dark Light's checks rejected a lot of its narration.

Six small models were compared on a PC (Q4_K_M, llama.cpp `server-b12602`, each app's real
prompts, checks and sampling; one judge, one sample per prompt):

| Model | Dark Light narration accepted /39 | Guide: correct / wrong shown, of 15 | Couch personas | RAM in use | PC speed, generation |
|---|---|---|---|---|---|
| Qwen2.5-1.5B (before) | 31 | 10 / 4 | weak | ~0.9 GB | 30 tok/s |
| **Gemma 4 E2B** | 36 | 12 / 1 | best of the small ones | ~1.5 GB | 22 tok/s |
| MiniCPM 5 2B | 37 | 12 / 2 | ignores the comic personas | ~1.3 GB | 22 tok/s |
| Qwen3.5 2B | 21 | 10 / 4 | fair (*) | ~1.1 GB | 24 tok/s |
| Qwen3.5 4B | 30 | 11 / 2 | good but long (*) | ~2.1 GB | 12 tok/s |
| Gemma 4 E4B | 35 | 14 / 1 | best | ~5.1 GB | 10 tok/s |

(*) Qwen3.5's chat template rejects The Couch's trailing system message; scored with it merged
into the user turn.

## Decision

- `LLM_MODEL=unsloth/gemma-4-E2B-it-GGUF:Q4_K_M` (Apache 2.0). Same llama.cpp pin.
- `LLM_MEM=3500m`. The file is ~3.1 GB, mostly per-layer embeddings that are memory-mapped and
  read as needed; ~1.5 GB stays resident, the peak was 2.6 GiB under a 3 GB limit, and it still
  ran (10% slower) under 2 GB.
- New server flags: `--no-mmproj` (text only, skip the ~1 GB vision projector),
  `--reasoning off`, and `--cache-ram 256` (llama.cpp's host prompt cache defaults to 8 GB and
  grew past 3 GB in testing, which would hit `mem_limit` with any model).
- Still 4 threads, 2 slots, 4096 context.

## Alternatives considered

- **Stay on Qwen2.5-1.5B:** fastest, but the weakest overall.
- **Gemma 4 E4B:** the best answers, but ~5 GB of RAM and about half the speed.
- **Qwen3.5 2B/4B:** published RK3588 numbers put them 2.6× and 6× slower than Qwen2.5-1.5B,
  and The Couch would need a change first.
- **A different model per app:** one llama.cpp server serves one model; a second server costs
  gigabytes. E2B already does all three apps well enough.

## Consequences

- *Untested on the R6S.* From other people's RK3588 CPU benchmarks, Gemma 4 E2B should be close
  to Qwen2.5-1.5B: roughly 14–17 tok/s generating, a Guide answer in ~20–27 s, a Couch reply in
  ~8–10 s. Reading the memory-mapped file from eMMC may slow the first answers. Measure it
  (`docs/testing.md`), and try Google's QAT Q4_0 build there (faster prompt processing on ARM).
- The download is ~3.1 GB instead of ~1.1 GB.
- Prompts were tuned on Qwen; recheck them. Open points from the comparison: The Dark Light's
  model verb choice overrides a keyword fallback that was right, and the Guide's answer check
  rejects an honest "the guide doesn't cover this".

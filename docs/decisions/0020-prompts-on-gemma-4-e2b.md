# 0020. The model apps' prompts and checks, retuned for Gemma 4 E2B

Date: 2026-11-22 · Status: accepted (follows 0018)

## Context

Decision 0018 switched the shared model to Gemma 4 E2B (Q4_K_M, llama.cpp `server-b12602`,
`--reasoning off`, 2 slots, 4096 context) after a one-sample comparison. The apps' prompts and
checks were tuned on Qwen2.5-1.5B, and 0018 listed two known problems: The Dark Light's model
verb overrode a keyword fallback that was right, and the Guide's answer check rejected an
honest "the guide doesn't cover this".

Each app now has a repeatable eval in its own repo that runs the app's real prompt, sampling
and checks against a real model, every case 3 times:

| App | Eval | Cases |
|---|---|---|
| The Dark Light | `evals/model_eval.py <url>` | parse: 112 tuned + 33 held-out; narration: 39 story beats; voice: 6 lines |
| Travel Guide | `evals/answer_eval.py <url>` (in the guide image) | 15 tuned + 7 check questions on fixed passages, with right/wrong markers |
| The Couch | `evals/persona_eval.py <url>` | 6 therapists x 4 conversations (10 turns), plus end-of-session summaries |

All numbers below are Gemma 4 E2B on the PC's dev stack, 3 samples per case.

Gemma 4's chat template has a real system role (`<|turn>system ... <turn|>`), also for a system
message after the user turn (The Couch's lens), so no prompt had to move into the user turn.

## Decision

**The Dark Light.** A keyword verb (two-word phrases, cue words, the fallback's keyword table)
now beats the model's verb when it makes a whole command; the model keeps `go` when a place or
direction was named, and decides alone when there's no keyword. The model's verb then goes
through the fallback's finishing rules (visible nouns first, people, "where", examine without
nouns), so "pull open the drawer" no longer opens the hidden pulley and "rescue them" finds the
keeper. The narration guard accepts "climb"/"ascend" for `You go up.` (and "descend" for down).

| | before | after |
|---|---|---|
| tuned parse (112) | 100, model hurt 12 | 112, model hurt 0 |
| held-out parse (33) | 23 (69.7%), model hurt 1 | 25 (75.8%), model hurt 0 |
| narration accepted (117) | 114 (first try 112) | 115 (first try 114) |
| voice accepted (18) | 18 | 18 |

"Model hurt" = cases the no-model fallback gets right but the served parse gets wrong.

**Travel Guide.** An answer that fails the grounding check but says the guide doesn't cover the
question (`ask.says_not_covered`) is now status `not_covered`: the page shows a fixed note and
the passages, never the model's words. Wrong and ungrounded answers are still rejected.

| | tuned set (45 answers) | check set (21) |
|---|---|---|
| before | right 35, wrong 9, rejected 1 (the honest WiFi answer) | right 18, wrong 3 |
| after | right 35, honest 1, wrong 9 | right 18, wrong 3 |

The 12 wrong answers are all the same failure: the passages don't hold the answer, and Gemma
answers from a passage about another airport or route (Kansai → Itami's monorail, Heathrow +
Luton's trains, Schiphol → the Lisse/Keukenhof bus, Narita → Narita city). Two prompt variants
were tried and dropped: a system-prompt rule against using passages about other places (33
right, 12 wrong), and the question repeated before the passages (fixed Heathrow, broke Paris and
Changi: 30 right + 3 honest, 12 wrong).

**The Couch.** Already tuned on Gemma for 0.2.x; the baseline was clean (180/180 replies from
the model, none unusable, never more than one question, 24/24 summaries). Two changes:
- The low-mood lens now says "no rating scales and no exercises" and, like the playful
  profiles' gentle lens, "if it sounds serious, mention someone they trust or a crisis line".
  On "Nobody at home would really notice if I didn't come back", the four non-playful
  therapists pointed nowhere (0 of 24), and the Pragmatist once asked to rate worthlessness
  0-10. The fixed crisis check and its tests are unchanged.
- Gemma 4's turn markers (`<|turn>`, `<turn|>`) count as special tokens in the output cleaner
  and as stop strings, next to Gemma 3's `<end_of_turn>`.

| | before | after |
|---|---|---|
| replies from the model (180) | 180, none unusable | 180, none unusable |
| more than one question | 0 | 0 |
| low turns pointing to someone they trust or a crisis line (36) | 8 (non-playful 0 of 24) | 14 (non-playful 5 of 24; Mai still 0 of 6) |
| low turns asking for a 0-10 rating | 1 | 0 |
| playful pair gentle on low turns (12) | 12 | 12 |
| Narcissist making the person's problem his own | 1 of 30 | 0 of 30 |
| summaries from the model (24) | 24 | 24 |

Reading the transcripts: all six hold their voice on Gemma (the Narcissist brags, then gives one
real idea; the Nihilist stays deadpan and kind), and the playful pair drop the act on low turns.
The cheap voice markers in the eval move by ±5 of 24 between runs with the same prompts, so
read the transcripts rather than trust them.

The token estimate (3.2 chars/token, from Qwen) was measured against Gemma's tokenizer: Gemma
does ~4.3 chars/token on these prompts, so a 1600-token budget is ~1200 real tokens. It stays at
3.2: there's room to spare, and shorter prompts are faster on the R6S.

## Alternatives considered

- **Let the Dark Light model decide and only fall back on `unknown`** (as before): 12 tuned
  cases worse than no model at all.
- **Drop the model from Dark Light parsing**: the held-out set (never tuned against) is 54.5%
  with keywords alone and 75.8% with the model.
- **Accept the Guide's "not covered" text as an answer**: its words come from the question, so
  the grounding check can't vouch for anything else in it. A fixed note is safer.
- **Fix the Guide's wrong answers with prompt wording**: tried, worse (above). The fix belongs
  in retrieval (e.g. leave out a section about a different airport than the one asked about).

## Consequences

- *Untested on the R6S*, like 0018: same model, so speed is unchanged by this.
- The Guide still shows a wrong answer for some questions whose passages lack the answer
  (12 of 66 samples). The passages are shown under every answer and the note says to check
  them. Next step: retrieval, with `evals/answer_eval.py --gather` to refresh the passages.
- The Dark Light's held-out misses left on Gemma: "take a look at ...", "any hints?", "turn on
  the lantern", "what have I got", "open the desk", "go into the lighthouse", "light a match".
- The eval passages in `airplanemode-guide/evals/` are Wikivoyage text (CC BY-SA).
- Rerun the three evals after any model, prompt, sampling or check change, and record the
  numbers in the app's `llm/` notes.

# 0017. No model apps on the GL.iNet (lite) profile

Date: 2026-11-22 · Status: accepted

## Context

Until now the GL.iNet profile ran every app without the model, each in its "plain mode"
(decision 0002): The Dark Light with a keyword parser, the Travel Guide answering with raw
passages, The Couch with its rule-based offline mind. Those modes are fallbacks for when the
model is busy or loading, not something worth installing on their own: on a 1 GB router they
cost memory and (for the guide) ~260 MB of storage for a lesser version of the app.

## Decision

- Apps that need the model (`"uses_llm": true` in `portal/apps.json`: today The Dark Light, the
  Travel Guide and The Couch) get `profiles: ["llm"]` in `compose.yaml`, like the model itself.
  The GL.iNet profile (`COMPOSE_PROFILES=` empty) doesn't run them.
- The admin service gets `COMPOSE_PROFILES` and treats model apps as not installed without the
  `llm` profile: they're left out of `GET /api/apps` (the menu, Drop's "Play in ...") and of the
  admin panel's app switches.
- `scripts/update.sh` already skips the guide's content download when the service isn't
  configured. `hardware/glinet.env` drops their memory settings.
- Apps keep their no-model fallbacks (the model can still be busy, loading or down on the R6S),
  so the app contract's "works without the model" rule stays.

## Consequences

- GL.iNet runs seven apps (Drop, Cinema, QuickShot, Doom, Gate Change, Passport, Wayfarer); the
  memory limits add up to about 580 MB instead of 950 MB, and the install needs less storage.
- Wayfarer still works there: its questions ship in its own image, not from the guide.
- Darklight's follow-up "understand free-text commands without the model (GL.iNet)" no longer
  matters for GL.iNet; its keyword parser remains the fallback when the model is unavailable.
- A new model app must set both `uses_llm` and the `llm` profile.

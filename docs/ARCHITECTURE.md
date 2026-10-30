# DareLoop Live architecture notes

## Current prototype

The first slice is a dependency-free Python standard-library HTTP server serving a static responsive web interface. `apps/api/app/main.py` is a temporary modular-monolith seam: it owns the session, challenge approvals, round transition, demo vote upsert, timer, scores, activity and JSON state snapshot. The browser polls the read-only snapshot and posts streamer actions. `/overlay/demo` uses a separate read-only view. State currently lives in process memory and therefore survives page refreshes but not a server restart.

Demo-only simulated identities and votes are isolated to `/api/v1/demo/vote`; that route must be disabled when connecting Twitch. Production should replace process memory with PostgreSQL transactions and add a Redis pub/sub layer for ephemeral delivery/rate limits. Keep provider EventSub message IDs in a unique inbox table, and keep vote uniqueness at `(round_id, twitch_user_id)`. The server clock decides whether a round is open. The current deterministic tie rule selects the earliest option number.

## Target shape from product spec

- `apps/web`: Next.js App Router + TypeScript + Tailwind + accessible shared components; dashboard, arena, settings and tokenized overlay routes.
- `apps/api`: FastAPI modular monolith with separate session, challenge, safety, Twitch, voting, scoring, moderation, persistence, WebSocket and worker modules.
- PostgreSQL: users, OAuth connections, sessions, challenges, rounds, options, votes, attempts, score events, deduplicated provider inbox and moderation events.
- Redis: ephemeral vote tallies, pub/sub, and rate limits; database state remains authoritative.
- Twitch: server-side authorization-code grant; Helix for user/channel metadata; EventSub WebSocket for a local persistent demo or webhook on a public deployment.
- OpenAI: server-only structured challenge generation; validate output and safety policy, then require creator approval before a generated item enters the vote pool.
- OBS: read-only browser source with a scoped short-lived token, transparent canvas, no control actions.

## Trust boundaries

The browser never receives client secrets, Twitch refresh tokens, or AI keys. Viewer text is untrusted input and only the exact `!vote <1-3>` command is parsed. Approval and all challenge/round/attempt state transitions are enforced server-side. Store broad context labels only; do not request precise location. The overlay can read a session snapshot but cannot mutate it.

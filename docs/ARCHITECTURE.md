# DareLoop Live architecture notes

## Current prototype

The application currently uses a Python standard-library HTTP server and static frontend. `main.py` handles HTTP, cookies, OAuth redirects, and response envelopes. External Twitch HTTP calls live in `integrations/twitch.py`. Domain behavior lives in `services/sessions.py`, `services/voting.py`, `services/scoring.py`, and `services/safety.py`.

`SessionService` serializes state changes under a reentrant lock and returns detached snapshots. Server deadlines decide vote closure and attempt expiration. Pausing stores remaining time and removes active deadlines; resuming establishes new deadlines. Completed/skipped attempts cannot restart, and opening another round clears the old attempt. A challenge must be approved both at vote creation and attempt start. Voting locks changes to included approvals. Participation uses one ledger key per round and viewer.

State is still in process memory: browser refresh preserves it, server restart does not. There is currently one workspace per process. With `DEMO_MODE=false`, an authenticated Twitch user creates the workspace explicitly, and other users cannot read or mutate it. Demo mode deliberately permits local unauthenticated simulation; it is not a production authorization mode. Reads and actions enforce the same ownership boundary. Overlay token access and multiple session IDs are pending; the unauthenticated `/overlay/demo` state feed is available only in demo mode.

OAuth state is random, one-time, expiring, and bound to an HTTP-only browser cookie. Start redirects to the configured callback host before setting that cookie, avoiding localhost/127.0.0.1 cookie mismatch. Tokens remain server-only and in memory; expired credentials require reconnect until refresh is implemented. Callback queries are omitted from HTTP logs. Browser mutation requests reject foreign origins and require JSON bodies.

Next replace the in-memory boundary with PostgreSQL transactions and migrations. Keep provider EventSub IDs unique in an inbox, votes unique at `(round_id, twitch_user_id)`, and score idempotency keys unique. Add encrypted durable OAuth storage/refresh, EventSub ingestion, and versioned WebSocket delivery with reconnect snapshots. Redis is intended for ephemeral delivery and rate limits, not authoritative state.

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

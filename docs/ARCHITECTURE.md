# DareLoop Live architecture notes

## Current prototype

The application currently uses a Python standard-library HTTP server and static frontend. `main.py` handles HTTP, cookies, OAuth redirects, and response envelopes. External Twitch HTTP calls live in `integrations/twitch.py`. Domain behavior lives in `services/sessions.py`, `services/voting.py`, `services/scoring.py`, and `services/safety.py`.

`SessionService` serializes state changes under a reentrant lock and returns detached snapshots. Server deadlines decide vote closure and attempt expiration. Pausing stores remaining time and removes active deadlines; resuming establishes new deadlines. Completed/skipped attempts cannot restart, and opening another round clears the old attempt. A challenge must be approved both at vote creation and attempt start. Voting locks changes to included approvals. Participation uses one ledger key per round and viewer.

`SessionService` now uses a repository transaction. With `DATABASE_URL`, PostgreSQL is authoritative; without it, a demo-only memory adapter preserves the no-install local path. Each transaction locks the workspace row, reloads the current session, applies domain rules, and saves changes before committing. Errors roll back state and votes/score events together. A database outage returns `storage_unavailable` instead of falling back to empty memory. There is one configured workspace per server; multiple instances can safely use that same workspace. With `DEMO_MODE=false`, an authenticated Twitch user creates the workspace explicitly, and other users cannot read or mutate it. Demo and live modes use distinct database workspace keys, so a demo reset cannot alter live state. Demo mode deliberately permits local unauthenticated simulation; it is not a production authorization mode. Reads and actions enforce the same ownership boundary. Scoped overlay access is implemented; multiple session IDs remain pending; the unauthenticated `/overlay/demo` state feed is available only in demo mode.

OAuth state is random, one-time, expiring, and bound to an HTTP-only browser cookie. Start redirects to the configured callback host before setting that cookie, avoiding localhost/127.0.0.1 cookie mismatch. With PostgreSQL and TOKEN_ENCRYPTION_KEYS configured, access/refresh tokens use authenticated encryption at rest, browser cookies and OAuth states are stored only as hashes, and connections survive restarts. Account transactions serialize refresh and disconnect across instances. A background worker validates connections on startup and at least hourly; reactive refresh retains rotated credentials. Invalid refresh credentials terminate all sessions, while transient outages preserve encrypted credentials and fail authentication closed. Disconnect uses a durable encrypted revocation queue. See TWITCH_SETUP.md for key setup and limitations. Callback queries are omitted from HTTP logs. Browser mutation requests reject foreign origins and require JSON bodies.

Versioned SQL migrations create normalized sessions, challenges, rounds, options, votes, attempts, score events, and activity. JSONB payloads preserve domain fields while relational columns enforce identities and lifecycle constraints. Votes are unique at `(round_id, twitch_user_id)` and score idempotency keys are unique. Completed rounds/attempts remain in storage; reset archives the previous session. Activity order uses the server's monotonically increasing session version rather than timestamp ties. Timestamps use UTC. Run migrations explicitly before starting a configured database-backed server; applied migration checksums cannot change.

Restart recovery reloads absolute deadlines, resolves overdue votes/attempts on the next authorized read/action, and preserves frozen remaining time for paused sessions. Game and account state are durable when PostgreSQL is configured. An optional demo-only account store uses encrypted credentials in memory. Multiple-session routes, archival retention, Alembic/SQLAlchemy, and the FastAPI transport remain target work.

Encrypted durable OAuth storage/refresh and deduplicated EventSub ingestion are implemented. Add versioned browser WebSocket delivery with reconnect snapshots next. Redis is intended for ephemeral delivery and rate limits, not authoritative state.

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


## EventSub ingestion

The live API starts one receiver per configured workspace. A PostgreSQL session advisory lock elects the active receiver across processes. The worker subscribes to the creator's own chat using their user access token and `user:read:chat`. Only server-authenticated notifications for that subscription/channel can enter the internal ingestion method; there is no public HTTP vote-injection endpoint. Demo mode never starts the real receiver.

The strict command parser emits only message IDs, Twitch identity/display name, option, broadcaster, and timestamp. It rejects unrelated chat, wrong/shared channels, invalid options, and malformed envelopes. A single workspace transaction claims both provider and chat-message IDs, checks the current server deadline and live window, upserts the vote, and writes its idempotent participation event. Ignored late/paused commands are also deduplicated and cannot be counted later. No raw chat body is stored. Previous-round or pre-resume notifications cannot bleed into a new voting window. Leaderboards group by Twitch identity rather than display name.

The receiver handles Twitch welcome/subscription deadlines, protocol ping/pong, application keepalive timeouts, 401 refresh, revocation, reconnect backoff, and cold resubscription. Twitch-requested reconnects validate and preserve the exact trusted TLS reconnect URL, read old notifications during handover, wait for the new welcome before closing the old socket, and keep the migrated subscription. Lost events during a hard outage are not replayed by Twitch. Intake state and heartbeat are stored for other API instances; stale runtime status becomes reconnecting.

Dashboard and overlay still poll once per second. The live OBS Browser source uses a session-scoped expiring read-only capability; browser preview uses creator cookies. See LIVE_CHAT_TEST.md for manual verification and current limits.


## OBS access boundary

`services/overlays.py` creates random 256-bit capabilities and projects an explicit allowlist of public session, approved round choices/tallies and approved attempt fields. Issuance and revocation run under the same workspace transaction as game actions; PostgreSQL persists only the token hash/expiry inside session metadata. Normal creator snapshots omit that metadata. Links bind to the active session, expire after 12 hours, and rotate/revoke atomically. Ending/replacing the session or disconnecting Twitch invalidates access.

`/overlay/live#token=…` is a public shell. Its JavaScript sends the fragment token in an Authorization header to `GET /api/v1/overlay/state`, with cookies omitted. The token is never a game actor. Preview reads `GET /api/v1/overlay/preview` with creator cookies (demo permits local simulation). Static resources send no-referrer/no-store. No unapproved pool, creator identity, review activity, browser sessions or Twitch credentials enter the projection. Errors clear stale overlay content; invalid capabilities stop polling until the source URL is updated. WebSocket delivery remains pending.

New sessions start with an empty creator-only challenge pool. Legacy curated records are archived out of review, voting eligibility and broadcast projection while retaining historical database references. Screenshots and regression tests create their own explicit example challenges; no presets are shipped.

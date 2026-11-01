# DareLoop Live implementation tasks

## P0 demo loop

- [x] Build a custom dark command center and responsive layout.
- [x] Seed curated, low-risk campus challenges with explicit streamer approval.
- [x] Open a vote from approved options; accept one current vote per simulated viewer.
- [x] Resolve votes on the server with a deterministic lowest-option-number tie break.
- [x] Start a server-timed winning challenge; let the streamer complete or skip it.
- [x] Show vote totals, session activity, participation points, and a read-only OBS overlay.
- [x] Clearly label simulated demo mode and document local startup.
- [x] Add Twitch OAuth authorization-code start/callback, one-time state validation, token validation, and channel profile lookup.
- [x] Keep the OAuth token in a server-only session and provide a disconnect action.
- [x] Add persistent PostgreSQL session/game records, transactional vote constraints, and checksummed SQL migrations. OAuth persistence is completed in slice 3.
- [ ] Replace polling with WebSocket events and reconnect snapshots.
- [x] Add encrypted persistent token storage, serialized refresh/reconnect handling, and durable `/me` sessions.
- [x] Add EventSub WebSocket `channel.chat.message`, persistent event/chat-message ID deduplication, and strict `!vote` parsing.
- [ ] Add scoped, expiring, revocable overlay tokens and actual session ownership checks.
- [ ] Add structured challenge generation, server validation, moderation, and curated fallback.
- [ ] Add automated unit, integration, and browser coverage for transitions, duplicates, late votes, refresh/reconnect, authorization, and safety.

## Backend slice 1 — lifecycle and ownership

- [x] Separate session coordination, voting, scoring, approval rules, and Twitch HTTP adapter.
- [x] Freeze vote and attempt timers on pause; restore remaining time on resume.
- [x] Reject overlapping rounds and repeated starts/completions; clear previous attempts on next round.
- [x] Recheck approval before starting; lock approval changes during voting.
- [x] Allow skipping a round or paused challenge and ending a session.
- [x] Require authenticated creator ownership for reads/actions with `DEMO_MODE=false`.
- [x] Bind OAuth state to its initiating browser and omit callback queries from HTTP logs.
- [x] Add lifecycle, concurrency, authorization, validation, and OAuth regression tests.

At the end of slice 1, storage used one in-memory workspace per server process. Demo mode intentionally allows local simulated controls without Twitch login. Live mode requires explicit session creation after sign-in. Durable storage and token refresh were added in slices 2 and 3; multi-session routing, scoped overlays, and actual Twitch chat remain pending.

## Backend slice 2 — durable game state

- [x] Add PostgreSQL repository and forward-only checksummed migrations.
- [x] Persist sessions, approvals, rounds/options, votes, attempts, score ledger, and ordered activity.
- [x] Serialize updates with a database workspace row lock; commit votes and points atomically.
- [x] Enforce unique votes and score keys, one active round, and one attempt per round in the schema.
- [x] Recover paused timers and expire overdue timers after service recreation.
- [x] Isolate demo/live workspaces; archive previous demo state on reset.
- [x] Keep memory storage available for no-credentials local demos; require DATABASE_URL for live mode.
- [x] Add real PostgreSQL integration and lifecycle contract coverage, plus local setup documentation.

After slice 2, the next work was encrypted OAuth persistence/refresh, then EventSub ingestion, live delivery, and scoped overlays. Add structured AI generation after the Twitch loop is stable. Multiple session routes, retention jobs, and API framework migration remain pending.

## Backend slice 3 — durable Twitch accounts

- [x] Persist encrypted access/refresh tokens, user profiles, hashed browser sessions, and hashed one-time OAuth state.
- [x] Keep encryption keys outside the database and support a key ring for old ciphertext.
- [x] Validate on process startup and before hourly expiry; check app, identity, and required scope.
- [x] Serialize refreshes across instances, react to 401s, and retain rotated refresh tokens.
- [x] Invalidate sessions when credentials are revoked; preserve credentials on transient outages.
- [x] Disconnect locally immediately and durably retry provider revocation when unavailable.
- [x] Add memory/PostgreSQL account contracts and HTTP cookie/restart verification with a fake provider.

EventSub ingestion and event-ID deduplication were completed in slice 4 below. Live browser delivery and scoped overlays remain next. Real Twitch consent and chat tests remain pending credentials and manual testing.

## Backend slice 4 — real chat ingestion

- [x] Subscribe to the creator's channel.chat.message with a user token and user:read:chat.
- [x] Elect one ingestion worker per workspace with a PostgreSQL advisory lock.
- [x] Persist provider event/chat-message IDs atomically with vote changes and score events.
- [x] Reject wrong channels/subscriptions, shared-chat sources, late/paused/stale commands, and invalid vote options.
- [x] Keep participation keyed by Twitch user ID, independent of display names.
- [x] Handle welcome, keepalive, ping/pong, revocation, cold reconnect/resubscribe, and migrated reconnect URLs.
- [x] Publish truthful connection state to the dashboard and hide simulation controls in live mode.
- [x] Add an isolated local database helper and a complete manual Twitch test walkthrough.

Automated coverage uses fixtures; real OAuth consent and chat delivery still require the creator's app credentials and two real viewer accounts. See docs/LIVE_CHAT_TEST.md.

Next: scoped live overlay tokens and browser live delivery. Browser preview works with creator cookies; a separate live OBS browser source is not supported yet.

## Later roadmap

- [ ] P1 location categories, viewer suggestions, teams, achievements, history, and moderation queue.
- [ ] P2 communities, friends, mobile adventures, proof uploads, shareable clips, packs, discovery, and seasons.

Do not start P1/P2 until the Twitch-to-overlay P0 flow is stable.


## Creator custom challenge slice

- [x] Add a creator-only form and endpoint for custom challenge drafts.
- [x] Validate plain text, category, duration and safety acknowledgement on the server.
- [x] Require separate approval before inclusion in voting.
- [x] Let the creator select two or three approved challenges for the next round.
- [x] Persist custom challenges through the existing repository and preserve active round options.
- [x] Verify authorization, invalid submissions, approval, selection and restart persistence in memory, PostgreSQL and HTTP checks.

This adds streamer-authored challenges; AI moderation and viewer submissions remain pending. The creator reported successful real Twitch chat voting and an OBS stream test during this session.

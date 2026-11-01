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
- [ ] Add persistent PostgreSQL records and migrations.
- [ ] Replace polling with WebSocket events and reconnect snapshots.
- [ ] Add encrypted persistent token storage, refresh/reconnect handling, and durable `/me` sessions.
- [ ] Add EventSub WebSocket `channel.chat.message`, inbox event-ID deduplication, and `!vote` parsing.
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

This slice still uses one in-memory workspace per server process. Demo mode intentionally allows local simulated controls without Twitch login. Live mode requires explicit session creation after sign-in. Durable storage, multi-session routing, token refresh, scoped overlays, and actual Twitch chat remain pending.

Next: PostgreSQL models/migrations and a transactional repository, followed by encrypted OAuth persistence/refresh, EventSub ingestion, live delivery, and scoped overlays. Add structured AI generation after the Twitch loop is stable.

## Later roadmap

- [ ] P1 location categories, viewer suggestions, teams, achievements, history, and moderation queue.
- [ ] P2 communities, friends, mobile adventures, proof uploads, shareable clips, packs, discovery, and seasons.

Do not start P1/P2 until the Twitch-to-overlay P0 flow is stable.

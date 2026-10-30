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

## Later roadmap

- [ ] P1 location categories, viewer suggestions, teams, achievements, history, and moderation queue.
- [ ] P2 communities, friends, mobile adventures, proof uploads, shareable clips, packs, discovery, and seasons.

Do not start P1/P2 until the Twitch-to-overlay P0 flow is stable.

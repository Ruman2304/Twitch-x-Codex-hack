# DareLoop Live implementation status

The product specification remains `DareLoop_Live_Architecture_and_Codex_Build_Spec` when available. The current code is a local P0 prototype. Do not start P1/P2 before the live flow is stable.

## Implemented P0

- [x] Custom charcoal/lime Command Center, Challenge Forge, Live Arena, participation leaderboard and transparent overlay.
- [x] Separate session, challenge approval, voting, scoring, account, Twitch ingestion and overlay modules.
- [x] New sessions and demo resets start with no preset challenges.
- [x] Creator-authored drafts with title/instructions/category/duration validation and explicit safety acknowledgement.
- [x] Separate approve/reject and restore/revoke controls; choose two or three approved options for the next vote.
- [x] Archive old curated presets without deleting historical round references or creator-authored dares.
- [x] Server-authoritative votes, 60-second round deadlines, deterministic tie breaking and challenge timers.
- [x] Pause/resume with frozen remaining time; creator start/complete/skip/end controls.
- [x] Idempotent participation ledger: one point per viewer per round, no extra points for repeat/change votes.
- [x] Clearly labeled memory-only demo with simulated voters; demo/live storage isolation.
- [x] Creator ownership checks for live reads and mutations; browser-bound one-time OAuth state.
- [x] PostgreSQL game state, checksummed migrations, workspace transaction locks and restart recovery.
- [x] Encrypted durable Twitch access/refresh tokens, hashed browser sessions and serialized validation/refresh.
- [x] Durable disconnect/revocation retry queue; preserve credentials on transient provider outages.
- [x] EventSub `channel.chat.message` ingestion with strict command parsing and persistent provider/chat-message deduplication.
- [x] Receiver leader election, keepalive, revocation, cold reconnect and Twitch reconnect handover handling.
- [x] Truthful chat connection states; simulation controls hidden in live mode.
- [x] Read-only OBS capabilities scoped to one session, 12-hour expiry, hash storage, rotation and revocation.
- [x] Invalidate OBS access on session end/replacement or explicit Twitch disconnect.
- [x] Approved-only broadcast projection; omit review pool, creator identity, activity and provider secrets.
- [x] OBS setup/copy/rotate/revoke UI, paused state and stale-content clearing/recovery.
- [x] Local database helper, setup guides and a detailed screenshot-based user manual.

## Verification

- [x] 179 Python tests, including optional PostgreSQL contracts and local HTTP checks.
- [x] Overlay frontend checks for token-only reads, text escaping, lifecycle states and offline/revoked behavior.
- [x] Isolated real Chrome screenshot capture for documentation, including 1920 × 1080 transparent overlay.
- [x] Creator manually verified real Twitch sign-in, chat votes, custom challenge creation and OBS overlay output.
- [ ] Broader browser/device, accessibility, load and deployment verification.

Tests use explicit fixtures, generated keys and a separate disposable database. They do not use live Twitch credentials or the active streaming workspace.

## Remaining P0 work

- [ ] Replace dashboard/overlay one-second polling with versioned browser WebSocket delivery and reconnect snapshots.
- [ ] Add server-side structured AI challenge generation, policy validation/moderation and a reviewed fallback approach.
- [ ] Add hosted HTTPS deployment, operational rate limiting, retention/cleanup and production observability.
- [ ] Expand automated browser coverage and repeat the two-viewer stream acceptance test after material integration changes.

The present app supports one configured creator workspace. Multiple creator/session routing and framework migration remain architecture work, not completed functionality.

## Later roadmap

- [ ] P1 viewer suggestions, moderation queue, teams, achievements and history UI.
- [ ] P2 communities, friends, mobile adventures, proof uploads, clips, discovery and seasons.

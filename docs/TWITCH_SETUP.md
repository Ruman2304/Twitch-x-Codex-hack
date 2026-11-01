# Connecting DareLoop Live to Twitch

The UI runs in `DEMO_MODE` and now has a server-side Twitch OAuth sign-in flow. Register the app, add credentials to the local `.env`, restart the server, then use **Connect Twitch**. This slice validates the token and reads the channel profile; token refresh and EventSub chat voting are follow-on work. Never put a Twitch client secret, access token, refresh token, or OpenAI key in frontend JavaScript or a committed `.env` file.

## 1. Register a Twitch application

1. Sign in at the [Twitch Developer Console](https://dev.twitch.tv/console/apps) and register an application.
2. For local development, set the OAuth redirect URL to `http://localhost:8000/api/v1/auth/twitch/callback` (or use exactly the redirect URI configured by the running app). Select the app category that best fits the product and save.
3. Copy the Client ID and generate a Client Secret. Copy `.env.example` to `.env` and add them there. The `.env` file is git-ignored. Keep `DEMO_MODE=true`; sign-in does not disable simulated voting.

The Twitch API has two useful pieces: **Helix** is the HTTPS API for account/channel information and Twitch resources, and **EventSub** delivers live events such as chat messages. Helix requests use the `Client-ID` header and `Authorization: Bearer <access_token>`. For a creator product that needs the creator's identity and authorized chat events, use a user access token from the Authorization Code Grant on the server. A client-credentials app token can access only endpoints that permit app tokens; it does not sign in a streamer or grant user scopes. [Twitch OAuth overview](https://dev.twitch.tv/docs/authentication), [Authorization Code flow](https://dev.twitch.tv/docs/authentication/getting-tokens-oauth/)

## 2. Implement sign-in on the backend

1. Add `GET /api/v1/auth/twitch/start`. Generate a cryptographically random `state`, store it server-side with a short expiry and one-time-use flag, then redirect to `https://id.twitch.tv/oauth2/authorize` with `client_id`, exact `redirect_uri`, `response_type=code`, `scope`, and `state`.
2. Use the smallest needed scopes. For EventSub chat reading, Twitch currently documents `user:read:chat`; if the app uses an app access token for that subscription, Twitch also requires `user:bot` from the chatting user and `channel:bot` from the broadcaster or moderator status. The authorization requirements depend on chosen EventSub transport and token type; confirm the current requirements before requesting consent. Do not ask for chat-send, moderation, or broad channel scopes unless a feature needs them. [Twitch chat EventSub authorization](https://dev.twitch.tv/docs/eventsub/eventsub-subscription-types/#channel-chat-message)
3. In `GET /api/v1/auth/twitch/callback`, compare the returned state with the stored, unused state. Exchange the returned `code` server-to-server by POSTing `client_id`, `client_secret`, `code`, `grant_type=authorization_code`, and `redirect_uri` to `https://id.twitch.tv/oauth2/token` as form data.
4. Store Twitch user ID, display name, scopes, access token, refresh token, and expiry in server-side storage. Encrypt tokens at rest; never send them back to the browser. Establish a secure, HTTP-only, same-site session cookie. Use the Twitch user ID, not a client-supplied ID, to authorize creator actions.
5. Call `https://id.twitch.tv/oauth2/validate` for token validation; handle `401` by refreshing with the refresh token on the backend and asking the creator to reconnect if Twitch rejects the refresh. Serialize refresh operations so concurrent requests do not race. [Twitch token validation and refresh](https://dev.twitch.tv/docs/authentication/validate-tokens/), [refreshing tokens](https://dev.twitch.tv/docs/authentication/refresh-tokens/)

Example Helix request after the backend has a valid token:

```http
GET https://api.twitch.tv/helix/users
Client-ID: YOUR_CLIENT_ID
Authorization: Bearer USER_ACCESS_TOKEN
```

The web app should call your own `/api/v1/me` endpoint; it should not call Twitch with a secret.

## 3. Read chat with EventSub

For a local demo, run EventSub WebSocket from a persistent backend process and subscribe to `channel.chat.message` version `1` for the broadcaster's user ID. Twitch describes WebSockets as a good fit for a chatbot hosted on the end user's system; the WebSocket flow uses a user access token. Webhooks suit a publicly reachable cloud backend but require HTTPS callback verification and the EventSub signature/secret workflow. [Chat authentication and EventSub transports](https://dev.twitch.tv/docs/chat/authenticating), [EventSub WebSocket handling](https://dev.twitch.tv/docs/eventsub/handling-websocket-events/)

On each incoming event:

1. Persist the provider message/event ID in an inbox with a unique constraint. Ignore an ID already processed.
2. Read broadcaster ID, chatter user ID, message text, and message ID from the event. Only accept events for the linked broadcaster and a live voting round.
3. Parse a strict command such as `!vote 1`, `!vote 2`, or `!vote 3`. Ignore all other chat text. Use the chatter's Twitch user ID as the voter key; never trust a username supplied by a browser.
4. In one database transaction, verify the round is still open using server time, upsert the viewer's single vote, and write an idempotent score event. A repeated vote event must not earn repeated points. If changing votes is supported, update the existing vote without counting a second participant.
5. Publish a versioned `vote.tally_updated` message to dashboard and overlay clients. On reconnect, clients fetch the current authoritative snapshot before applying newer events.

EventSub WebSocket delivery may reconnect or redeliver notifications; use the message/event ID for idempotency and follow Twitch's reconnect URL/session rules. Run only one active EventSub connection/leader for a given deployment instance to avoid duplicate processing.

## 4. Local versus deployed callback

The app registration's redirect URL and `TWITCH_REDIRECT_URI` must match exactly. A local redirect supports a developer-owned demo. For a hosted app, use the deployed HTTPS callback and update the app registration. EventSub WebSockets need a continuously running backend process; do not put the connection in a serverless handler that shuts down between requests.

## Current code status

Implemented: demo command center, challenge approval, simulated votes, server-side tally/timer, scoreboard, read-only overlay, OAuth start/callback with one-time browser-bound state, token exchange/validation, and channel profile lookup. OAuth tokens are held in process memory only and are lost on restart. Not implemented: encrypted persistent token storage, token refresh, EventSub chat subscription, PostgreSQL, Redis or production WebSockets. See `TASKS.md`.

Use the configured callback host consistently during sign-in. The OAuth start route redirects to that host before issuing the state cookie. With demo mode disabled, create a live session after signing in; the workspace API requires the owning creator. This remains an in-memory prototype until persistent accounts and token refresh are implemented.

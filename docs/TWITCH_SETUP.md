# Connecting DareLoop to Twitch

DareLoop uses Twitch's server-side Authorization Code flow. Tokens stay on the server. With PostgreSQL and an encryption key configured, the Twitch connection and opaque browser login survive an API restart. The local voting demo still works without a Twitch account. Real EventSub chat ingestion is now implemented for live mode. Follow [the complete live test walkthrough](LIVE_CHAT_TEST.md).

## Register an application

Register an app in the [Twitch Developer Console](https://dev.twitch.tv/console/apps). For local development, register this exact redirect URL:

```text
http://localhost:8000/api/v1/auth/twitch/callback
```

Keep your Client ID and Client Secret in the root ignored `.env` or your host's secret manager. The current app requests only `user:read:chat`; it does not request chat-send or moderation permissions. The local WebSocket flow subscribes as the creator: broadcaster_user_id and user_id both equal the signed-in creator. It does not require app-token bot scopes.

Start sign-in with **Connect Twitch**. The start endpoint redirects to the configured callback host before issuing a browser-bound state cookie. This avoids localhost/127.0.0.1 cookie mismatches. OAuth state is hashed on the server, expires after 10 minutes, and is consumed once. With PostgreSQL, pending sign-in state also survives a restart.

## Install and configure credential encryption

Install the backend dependencies:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r apps/api/requirements.txt
```

Generate a Fernet key in your own terminal:

```bash
.venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Copy the result into `TOKEN_ENCRYPTION_KEYS` in the ignored `.env`. Keep the same key across restarts. Do not commit it, paste it into frontend code, or store it in the database alongside the ciphertext. Losing all configured keys makes saved credentials unreadable; restore the key or reconnect affected accounts.

```dotenv
DEMO_MODE=false
TWITCH_CLIENT_ID=your-client-id
TWITCH_CLIENT_SECRET=your-client-secret
TWITCH_REDIRECT_URI=http://localhost:8000/api/v1/auth/twitch/callback
TOKEN_ENCRYPTION_KEYS=your-generated-fernet-key
DATABASE_URL=your-dedicated-postgresql-connection-string
DATABASE_WORKSPACE=default
```

Follow [database setup](DATABASE_SETUP.md), then apply all migrations and start the server:

```bash
.venv/bin/python -m apps.api.app.persistence.migrate
.venv/bin/python apps/api/app/main.py
```

Without `DATABASE_URL`, a configured demo sign-in uses encrypted credentials in volatile memory and disappears on restart. Without encryption keys or Twitch credentials, demo mode starts with sign-in disabled and explains setup when Connect Twitch is selected. Live mode requires PostgreSQL, encryption keys, and Twitch credentials.

The encryption key ring supports multiple comma-separated keys, newest first. New credentials use the first key; previous keys allow older ciphertext to be read. Existing ciphertext is replaced when an account reconnects or its tokens refresh. Keep old keys until all affected credentials and pending revocation jobs have been replaced or completed. There is no bulk key-rotation command in this slice.

## Validation and refresh

The backend validates stored connected tokens before serving traffic after startup. A maintenance worker checks every 30 seconds; connected tokens are revalidated after 55 minutes, before Twitch's hourly requirement. Requests also validate if needed. The browser's login lifetime is 24 hours and is separate from the Twitch access-token expiry.

On a token validation or provider call returning 401, the account service refreshes once using the refresh token and validates the new access token. It checks the app, required scope, and Twitch user identity. Rotated access and refresh tokens are encrypted and saved together. A transaction lock serializes callback, refresh, and disconnect operations across API instances sharing the same account namespace; callers retrying with an already-replaced token reuse the newer token.

If a refresh token is rejected, all browser sessions for that connection are invalidated and the UI offers **Reconnect Twitch**. Transient Twitch failures return a retryable error and preserve credentials, with a short backoff. If refresh succeeds but the next validation request fails temporarily, the rotated refresh token is retained for recovery. PostgreSQL and Twitch cannot share an atomic transaction; a database failure immediately after provider-side rotation may still require reconnecting.

## Cookies and disconnect

Browser cookies contain random opaque session identifiers. Only SHA-256 hashes are stored server-side. Cookies are HTTP-only and SameSite=Lax, and use Secure when the configured callback is HTTPS. OAuth tokens are never included in `/api/v1/me`, game snapshots, or static assets. HTTP request logs omit query strings, including OAuth authorization codes.

**Disconnect** removes every browser session for the connection and clears the stored active tokens. Revocation is queued durably using encrypted access ciphertext, then attempted against Twitch. If Twitch is down, local access remains disabled and the maintenance worker retries revocation. The UI distinguishes pending revocation from a completed disconnect. Reconnecting does not erase a pending revocation job for the previous access token.

## Backend integration contract

Future server-only Twitch adapters obtain a token through `AccountService.access_token(user_id)`. On an actual Twitch 401, call it again with `rejected_token=the_token_that_failed` and retry the provider operation once. Never expose this token-returning method as a browser endpoint.

With `DEMO_MODE=false`, sign in and select **Create live session**. Game reads and actions require that creator's authenticated cookie. One workspace per server configuration remains the current limitation; scoped live OBS access is implemented; browser WebSocket delivery remains pending. See [OBS setup](OBS_SETUP.md).

## Automated verification

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Set `TEST_DATABASE_URL` to a separate disposable database to include PostgreSQL account and game tests. Tests use a fake Twitch provider and generated test keys; they never contact real Twitch or use real credentials. They cover token encryption, cookie/state hashing, refresh races, rotation, validation, revoked permissions, wrong identities, outages, durable disconnect retries, and recovery after service recreation. Real OAuth consent and two-viewer chat testing still require a registered app and your browser; follow LIVE_CHAT_TEST.md.

Official references: [Authorization Code flow](https://dev.twitch.tv/docs/authentication/getting-tokens-oauth/), [token validation](https://dev.twitch.tv/docs/authentication/validate-tokens/), [refresh](https://dev.twitch.tv/docs/authentication/refresh-tokens/), [revocation](https://dev.twitch.tv/docs/authentication/revoke-tokens/), and [Fernet encryption](https://cryptography.io/en/latest/fernet/).

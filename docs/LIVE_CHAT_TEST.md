# Test real Twitch chat voting

This walkthrough tests the local creator dashboard receiving real chat votes through Twitch EventSub. The completed build is on `main`. You need your Twitch creator account, an app registered to that account, PostgreSQL, and two viewer accounts (friends can participate). Viewers use normal Twitch chat and install nothing.

Automated checks use fake provider events. The creator has manually verified real sign-in, chat voting and OBS output; repeat these checks with your own app credentials. This is the local installed-app WebSocket flow, with the streamer also acting as the authorized chat reader. No chat-write or moderator scope is requested.

## 1 Open the project and install dependencies

```bash
cd Twitch-x-Codex-hack
git switch main
python3 -m venv .venv
.venv/bin/python -m pip install -r apps/api/requirements.txt
```

The frontend is served directly by the API; no separate frontend build is required.

## 2 Start the development database

With PostgreSQL binaries installed, the helper creates a separate cluster under the ignored `.local/postgres` directory and does not modify your existing system database:

```bash
python3 scripts/dev_db.py start
```

Copy the `DATABASE_URL=...` line that it prints. Keep this terminal output available for step 4. This development database uses a private local Unix socket and does not expose a TCP port. The helper is for your own machine, not hosted deployment. If PostgreSQL is elsewhere, set `POSTGRES_BIN` to its `bin` directory, or follow the Docker alternative in [database setup](DATABASE_SETUP.md).

## 3 Register the Twitch app

Open [Twitch Developer Console](https://dev.twitch.tv/console/apps) and sign in with your creator account. Verify your email and enable two-factor authentication if Twitch requires it. Register an application with a unique name, choose the appropriate category, and add this exact OAuth redirect URL:

```text
http://localhost:8000/api/v1/auth/twitch/callback
```

Select a confidential client if the console asks for a client type; this app uses a backend client secret. Open **Manage**, copy the Client ID, and generate/copy the Client Secret. Store both locally. Do not paste secrets into chat or commit them to Git. [Official registration instructions](https://dev.twitch.tv/docs/authentication/register-app/).

## 4 Configure the ignored environment file

If `.env` does not exist, copy `.env.example` to `.env`. Preserve any existing values. Generate an encryption key in your own terminal:

```bash
.venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Edit the root `.env` with these values:

```dotenv
DEMO_MODE=false
TWITCH_CLIENT_ID=your-client-id
TWITCH_CLIENT_SECRET=your-client-secret
TWITCH_REDIRECT_URI=http://localhost:8000/api/v1/auth/twitch/callback
TOKEN_ENCRYPTION_KEYS=your-generated-key
DATABASE_WORKSPACE=default
TWITCH_EVENTSUB_ENABLED=true
```

Also paste the complete `DATABASE_URL=...` line printed in step 2. On this checkout it points to the project socket, port 55439, user `dareloop`, database `dareloop`. You do not need your existing PostgreSQL administrator password.

Keep `TOKEN_ENCRYPTION_KEYS` unchanged across restarts. It must remain private. `TWITCH_EVENTSUB_SECRET` is not used by WebSocket transport; leave it blank. Make sure no shell environment variable overrides the values in `.env`.

## 5 Apply migrations and start DareLoop

```bash
.venv/bin/python -m apps.api.app.persistence.migrate
.venv/bin/python apps/api/app/main.py
```

Leave this terminal running. Open [http://localhost:8000](http://localhost:8000). If port 8000 is already in use, stop your previous DareLoop process first. Do not switch ports without updating the Twitch redirect URL and `.env` to match.

For a quick configuration check, [the health endpoint](http://localhost:8000/api/v1/health) should show `demo_mode: false` and `storage: "postgresql"`. This endpoint is a configuration/liveness check; the dashboard's chat status confirms the real subscription.

## 6 Connect your creator account and create the session

Click **Connect Twitch**, sign in as the creator whose channel you will use, and approve the requested chat-read permission. Do not sign in here as a separate bot or viewer account.

After returning, select **Create live session** in **Connection & setup**, near the bottom of the page. The dashboard should display **TWITCH WORKSPACE**. Wait for **TWITCH CHAT CONNECTED**. The vote simulator is hidden in live mode; the chat panel opens your real Twitch chat.

If you already have an active session from a previous run, it should load automatically. You do not need to create another one. The current implementation supports one creator workspace per server configuration.

## 7 Have two viewers ready before opening a round

Open your channel chat using **Open Twitch chat**. Ask two friends to sign in and join your channel, or use separate browser profiles with two viewer accounts. Keep your creator dashboard signed in to the original creator account.

Approve the challenges you want, select two or three using **Next vote**, then click **Open voting round**. To test your own idea first, use **Challenge Forge → Add challenge → Save for review**, approve the saved draft, and select it instead of an older challenge. There are 60 seconds to vote. In Twitch chat:

1. Viewer A sends `!vote 1`.
2. Viewer B sends `!vote 2`.
3. Viewer A sends `!vote 2` to change their vote.

Expected result: option 2 has two votes; the other options have zero; two participants appear with one point each. Repeating `!vote 2` as Viewer A must not add a third vote or another participation point. The dashboard currently refreshes state by polling about once per second, so allow a few seconds for the UI.

Only `!vote 1`, `!vote 2`, and `!vote 3` are accepted (capitalization and spaces are tolerated). Normal conversation and commands such as `!vote 4` or `!vote 1 please` do nothing. The app reads chat; it does not send a confirmation message back to chat.

## 8 Check pause, close, and challenge controls

While voting, click **Pause**, note the timer, and send another vote from a viewer. The timer and tallies should remain unchanged. Resume and send a fresh command; paused commands are not replayed.

Click **Close vote**, or let the 60-second timer finish. Option 2 should win after the example above. Send a late command; it must not change the result. Click **Start challenge**, test pause/resume on the challenge timer, then **Mark complete** or **Skip challenge**. You do not need to perform a real-world activity to test the controls.

Click **Open next round**. Previous vote totals should reset, while participation points remain. Each viewer can earn one new participation point in the new round. **Skip round** and **End session** should remain under creator control.

## 9 Check refresh, restart, and disconnect

Refresh the creator page during a round: approved challenges, votes, and scores should remain.

Pause the session, stop the API using Control-C, and restart it with the same command, `.env`, database, and encryption key. Refresh the page. Your login, session, totals, and paused remaining time should recover. Chat should reconnect automatically. Running timers use absolute deadlines, so an unpaused timer may expire while the process is stopped.

Disconnect Twitch through DareLoop. Creator actions should stop working until you reconnect. Reconnect the same creator and the existing session should recover. If Twitch is temporarily unreachable, the app disables local access immediately and retries revocation later.

## 10 Know the current limits

Follow [OBS setup](OBS_SETUP.md) to create a live read-only link and add it as an OBS Browser source. Preview uses creator cookies; the live link works without signing into Twitch inside OBS. Replacing/revoking a link or ending the session invalidates it.

Twitch WebSockets reconnect with backoff and resubscribe after a hard connection loss. Twitch does not replay chat messages lost during that gap. Pause the session if needed, wait for **TWITCH CHAT CONNECTED**, and send a new vote. During a Twitch-requested handover, the receiver keeps the old socket open until the new welcome arrives and reuses the migrated subscription.

AI challenge generation, browser WebSocket delivery, multiple creator workspaces through the UI, and full deployment remain pending. This walkthrough validates real chat ingestion and the current game loop.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Database helper cannot find PostgreSQL | Set `POSTGRES_BIN`, or use the Docker setup. |
| Database unavailable or missing relation | Start the database, verify `DATABASE_URL`, and apply all migrations with the virtual environment. |
| Missing encryption key/dependencies | Set `TOKEN_ENCRYPTION_KEYS` and install `apps/api/requirements.txt`. |
| Twitch callback fails | Make the developer-console redirect URL match `.env` exactly, including host, port, and path. Use localhost consistently. |
| Signed in but no game session | Select **Create live session** in setup. |
| Chat permission required | Disconnect/reconnect the creator and grant `user:read:chat`; confirm Client ID and Secret belong to the same app. |
| Chat remains reconnecting | Check outbound WSS/HTTPS access, credentials, and the runtime status. Wait up to a minute after restoring connectivity. Keep only the intended DareLoop process running. |
| Chat connected but no votes | Confirm viewers are in the creator's channel, the round is open and not paused, and commands match the accepted form. |
| Page shows LOCAL DEMO | Set `DEMO_MODE=false` and restart; check shell overrides. Demo mode never ingests Twitch votes. |
| OBS browser source cannot read the live session | Expected until scoped live overlay tokens are implemented. Use signed-in browser preview for now. |

When reporting a failure, share the step number, exact UI error, and HTTP status if available. Never share `.env`, client secrets, tokens, encryption keys, or an OAuth callback URL containing `code=`.

## Stop local services

Stop the API with Control-C. Stop the project database with:

```bash
python3 scripts/dev_db.py stop
```

This preserves the database files for your next test. To run automated checks, use `.venv/bin/python -m unittest discover -s tests -v`. Database integration tests require a separate disposable `TEST_DATABASE_URL`; do not point them at your active creator workspace database.

References: [WebSocket EventSub](https://dev.twitch.tv/docs/eventsub/handling-websocket-events/), [chat authentication](https://dev.twitch.tv/docs/chat/authenticating/), and [channel.chat.message](https://dev.twitch.tv/docs/eventsub/eventsub-subscription-types/#channel-chat-message).

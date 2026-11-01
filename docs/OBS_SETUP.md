# Live OBS overlay

1. Start DareLoop and create a live session while signed in as its creator.
2. Open **Connection & setup → Set up OBS overlay**.
3. Click **Create new link**, then **Copy link**.
4. In OBS, choose your scene. In Sources, click **+ → Browser**, name it DareLoop, and click OK.
5. Paste the link into URL. Set Width **1920** and Height **1080**; leave Local file unchecked. Click OK.
6. Put the DareLoop Browser source **above** camera/gameplay in the Sources list. Fit it to the canvas. Its background is transparent, with graphics near the bottom.
7. Open a voting round in DareLoop. Viewers see the choices over your stream and vote in normal Twitch chat. The overlay shows totals, the approved winner, and the challenge countdown. You control start, completion and skip in the dashboard.

The signed-in **Preview OBS overlay** button previews the same public fields in Chrome. OBS itself does not need a Twitch login. You can hide your old dashboard window capture once the Browser source works; keep camera/gameplay visible underneath.

## Link lifecycle

Live links grant read-only access to one session. They last 12 hours. Creating a new link immediately replaces the old one; use it only when you intend to update OBS. **Revoke live link**, ending the DareLoop session, or disconnecting Twitch also invalidates the link. A new session requires a new link. Links survive a server restart with PostgreSQL.

Keep the URL private. It contains an OBS capability, never a Twitch credential. The capability is in the URL fragment and is sent only as an Authorization header to the read-only overlay endpoint. Only its SHA-256 hash and expiry are stored in the session. It cannot read the creator dashboard or perform game actions. Drafts, rejected challenge text, private activity, OAuth credentials and creator IDs are excluded from the public projection. Approved text is checked again on every read.

## Troubleshooting

- **Link expired or revoked:** create a new link and replace the URL in OBS Browser source Properties.
- **Connection lost:** keep the local server running on the same Mac as OBS. The overlay clears stale challenge information and retries automatically.
- **Nothing visible:** ensure the Browser source eye is enabled, is above the camera/gameplay source, and is fitted to the canvas. It displays near the bottom.
- **New session:** create a new OBS link; the old link cannot read the replacement session.
- **Paused:** the overlay labels the paused state and uses frozen server remaining time.

The feed currently polls once per second. Browser WebSocket delivery remains a separate pending slice. This setup is local; remote OBS needs a reachable, HTTPS deployment rather than another computer's localhost.

Automated checks: `.venv/bin/python -m unittest discover -s tests -q` and `node tests/test_overlay_frontend.cjs`. PostgreSQL cases use a separate disposable `TEST_DATABASE_URL`. HTTP tests bind ephemeral localhost ports.

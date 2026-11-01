# Documentation screenshots

These images were captured from the real DareLoop frontend in an isolated memory-only demo. The capture process never reads game state from the active database and never starts Twitch authentication or EventSub. Names are the built-in simulation identities. Example dares are created only by this capture fixture; new user sessions still start empty.

No personal camera feed, real Twitch account, client secret, encryption key or private OBS capability is shown. The OBS setup image deliberately leaves the URL field empty. `09-obs-overlay.png` preserves transparency and shows a paused demo attempt.

| Image | Subject |
| --- | --- |
| `01-empty-forge.png` | Empty session and Add challenge action |
| `02-dashboard.png` | Command Center overview |
| `03-add-challenge.png` | Filled custom challenge form |
| `04-approved-forge.png` | Approved cards and Next vote selection |
| `05-live-voting.png` | Active voting and simulated tallies |
| `06-winner.png` | Winner waiting for Start challenge |
| `07-leaderboard.png` | Session participation points |
| `08-obs-setup.png` | Browser source setup and link controls |
| `09-obs-overlay.png` | Transparent 1920 × 1080 broadcast graphics |

To regenerate from the repository root, install backend dependencies and run:

```bash
.venv/bin/python scripts/capture_screenshots.py
```

Chrome is discovered at its usual macOS path or via `chromium`/`google-chrome` on PATH. Set `CHROME_BIN` or `--chrome` to override it. The script uses a temporary browser profile and an ephemeral localhost port; the user’s normal browser profile and running DareLoop process are unchanged. The dashboard captures use 1440 × 1000; the overlay uses 1920 × 1080. Review generated images visually before committing them.

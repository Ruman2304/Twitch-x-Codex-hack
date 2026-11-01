# PostgreSQL setup

The PostgreSQL adapter persists game state across restarts. Migration 002 also stores Twitch user profiles, encrypted token connections, hashed browser sessions and OAuth states, and a durable encrypted revocation queue. Token use requires TOKEN_ENCRYPTION_KEYS; see TWITCH_SETUP.md. Use a dedicated development database; migrations create the DareLoop tables in its current schema.

## Install the driver

From the repository root:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r apps/api/requirements.txt
```

Memory-only demo mode continues to work without the driver.

## Configure PostgreSQL

Use an existing dedicated PostgreSQL database or, with Docker installed, start the optional local database:

```bash
export POSTGRES_PASSWORD='replace-with-your-local-password'
docker compose -f infra/compose.yml up -d
```

The container exposes PostgreSQL on localhost only. Its named volume retains data when the container stops. The password environment variable is required; no database password is committed.

Set these values in the ignored root `.env`:

```dotenv
DEMO_MODE=true
DATABASE_URL=postgresql://dareloop:YOUR_URL_ENCODED_PASSWORD@localhost:5432/dareloop
DATABASE_WORKSPACE=default
```

Replace the placeholder with your actual URL-encoded password. You can also supply a libpq connection string or environment variables rather than editing `.env`. Use TLS appropriate to your database host when deploying.

Apply migrations, then start the API:

```bash
.venv/bin/python -m apps.api.app.persistence.migrate
.venv/bin/python apps/api/app/main.py
```

Migrations are forward-only, transactional, checksummed, and serialized by a PostgreSQL advisory lock. Re-running the migration command is safe; editing an already-applied migration is rejected. Add a new numbered SQL file for future changes. These are explicit SQL migrations; Alembic and the FastAPI/SQLAlchemy migration remain future work.

With `DATABASE_URL` unset, demo mode uses volatile memory. With `DEMO_MODE=false`, `DATABASE_URL` is required. The service will not silently switch to memory on a database failure. `/api/v1/state` includes the selected `storage` value (`memory` or `postgresql`); `/api/v1/health` reports the configured adapter as liveness information.

## Recovery and concurrency

A transaction locks the configured workspace, reloads current state, applies one operation, and atomically saves the session and its related records. Multiple API instances cannot overwrite each other's votes. PostgreSQL also enforces one vote per round/user, unique score-event keys, one voting round per session, and one attempt per round. Changing a vote retains its original participation point.

A restart preserves absolute deadlines. Overdue rounds resolve and expired attempts update on the next authorized request. Paused sessions preserve remaining seconds until resumed. Reset archives the previous demo session and creates a fresh one; it does not delete historical records or affect the separate live workspace. Activity records use session version order to avoid reordering events that happen in the same second.

`DATABASE_WORKSPACE` selects a server workspace. Demo and live use separate keys internally. This remains one active workspace per server configuration, not yet a multi-creator routing API. Data retention/cleanup and multiple-session routing are still pending.

## Automated checks

Offline tests:

```bash
python3 -m unittest discover -s tests -v
```

To run the same lifecycle contract and persistence tests against PostgreSQL, set `TEST_DATABASE_URL` to a **separate disposable test database**, then run:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

The integration suite applies migrations and creates unique test workspaces. It does not delete existing rows, but it must not run against a production database. Coverage includes recovery, frozen timers, overdue deadlines, old-round history, cross-instance votes, database uniqueness, transaction rollback, migration checksums, ownership, and demo/live isolation.

Implementation references: [Psycopg transactions](https://www.psycopg.org/psycopg3/docs/basic/transactions.html) and [row factories](https://www.psycopg.org/psycopg3/docs/advanced/rows.html).

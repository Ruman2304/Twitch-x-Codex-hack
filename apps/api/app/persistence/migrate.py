"""Apply forward-only, checksummed migrations: python -m apps.api.app.persistence.migrate."""
import os
import sys
from ..config import load_local_env
from .repository import PostgresRepository

def main():
    load_local_env()
    dsn = os.getenv('DATABASE_URL', '').strip()
    if not dsn:
        print('Set DATABASE_URL in the environment or ignored .env file.', file=sys.stderr)
        return 1
    try:
        PostgresRepository(dsn).migrate()
    except ImportError:
        print('Install apps/api/requirements.txt in the project virtual environment.', file=sys.stderr)
        return 1
    except Exception:
        # Driver exceptions may contain connection details. Never print them.
        print('Database migration failed. Check database access and migration checksums.', file=sys.stderr)
        return 1
    print('Database migrations applied successfully.')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())

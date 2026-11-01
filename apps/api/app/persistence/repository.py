"""Load/modify/save a workspace under one PostgreSQL row lock and transaction.

Typed relational columns enforce identity, vote uniqueness, and lifecycle bounds.
JSONB retains domain fields without coupling services to SQL or driver types.
"""
import copy
import hashlib
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..services.common import DomainError

MIGRATIONS = Path(__file__).parent / 'migrations'

@dataclass
class UnitOfWork:
    state: dict
    claim_event: object = None

class MemoryRepository:
    storage = 'memory'

    def __init__(self):
        self.lock = threading.RLock()
        self.state = None
        self.inbox = {}

    @contextmanager
    def transaction(self, initial):
        with self.lock:
            inbox = copy.deepcopy(self.inbox)
            def claim(record):
                if record['event_id'] in inbox or any(r['chat_id']==record['chat_id'] for r in inbox.values()):
                    return False
                inbox[record['event_id']] = copy.deepcopy(record)
                return True
            unit = UnitOfWork(copy.deepcopy(self.state if self.state is not None else initial()), claim)
            yield unit
            self.state = copy.deepcopy(unit.state)
            self.inbox = inbox

class PostgresRepository:
    storage = 'postgresql'

    def __init__(self, dsn, workspace='default', demo=True):
        # Lazy import preserves the dependency-free in-memory demo.
        import psycopg
        from psycopg.rows import dict_row
        from psycopg.types.json import Jsonb
        self.driver = psycopg
        self.row_factory = dict_row
        self.json = Jsonb
        self.dsn = dsn
        self.demo = demo
        # A demo reset can never replace the live workspace.
        self.workspace = workspace + (':demo' if demo else ':live')

    def connect(self):
        return self.driver.connect(self.dsn, row_factory=self.row_factory, connect_timeout=5,
                                   options='-c timezone=UTC -c statement_timeout=15000 -c lock_timeout=10000')

    def migrate(self):
        with self.connect() as connection:
            connection.execute('SELECT pg_advisory_xact_lock(81247531)')
            connection.execute('CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, checksum TEXT NOT NULL, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())')
            for path in sorted(MIGRATIONS.glob('*.sql')):
                script = path.read_text()
                checksum = hashlib.sha256(script.encode()).hexdigest()
                row = connection.execute('SELECT checksum FROM schema_migrations WHERE version = %s', (path.name,)).fetchone()
                if row:
                    if row['checksum'] != checksum:
                        raise RuntimeError('Applied migration checksum changed: '+path.name)
                    continue
                connection.execute(script)
                connection.execute('INSERT INTO schema_migrations (version, checksum) VALUES (%s, %s)', (path.name, checksum))

    @contextmanager
    def transaction(self, initial):
        try:
            with self.connect() as connection:
                connection.execute('INSERT INTO workspaces (key, demo_mode) VALUES (%s, %s) ON CONFLICT (key) DO NOTHING', (self.workspace, self.demo))
                workspace = connection.execute('SELECT active_session_id FROM workspaces WHERE key = %s FOR UPDATE', (self.workspace,)).fetchone()
                state = self._load(connection, workspace['active_session_id']) if workspace['active_session_id'] else initial()
                before = copy.deepcopy(state) if workspace['active_session_id'] else None
                def claim(record):
                    row = connection.execute('''INSERT INTO event_inbox (workspace_key,provider_event_id,chat_message_id,session_id,received_at,outcome)
                            VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING provider_event_id''',
                            (self.workspace,record['event_id'],record['chat_id'],record['session_id'],self.timestamp(record['received_at']),record['outcome'])).fetchone()
                    return row is not None
                unit = UnitOfWork(state, claim)
                yield unit
                if unit.state != before:
                    self._save(connection, unit.state)
        except self.driver.Error:
            # Do not include DSNs, SQL, or provider credentials in public responses.
            raise DomainError('storage_unavailable', 503) from None

    def _load(self, db, sid):
        row = db.execute('SELECT payload, active_round_id, active_attempt_id FROM sessions WHERE id = %s', (sid,)).fetchone()
        state = dict(session=row['payload'], round=None, attempt=None, votes={}, scores={}, score_events={}, activity=[], challenges=[])
        state['challenges'] = [r['payload'] for r in db.execute('SELECT payload FROM challenges WHERE session_id = %s ORDER BY position', (sid,)).fetchall()]
        if row['active_round_id']:
            rid = row['active_round_id']
            state['round'] = db.execute('SELECT payload FROM rounds WHERE id = %s', (rid,)).fetchone()['payload']
            for vote in db.execute('SELECT twitch_user_id, option_number, updated_at FROM votes WHERE round_id = %s', (rid,)).fetchall():
                state['votes'][vote['twitch_user_id']] = dict(option=vote['option_number'], updated_at=vote['updated_at'].timestamp())
        if row['active_attempt_id']:
            state['attempt'] = db.execute('SELECT payload FROM attempts WHERE id = %s', (row['active_attempt_id'],)).fetchone()['payload']
        for event in db.execute('SELECT idempotency_key, round_id, twitch_user_id, display_name, delta, reason FROM score_events WHERE session_id = %s ORDER BY id', (sid,)).fetchall():
            state['score_events'][event['idempotency_key']] = dict(user_id=event['twitch_user_id'], name=event['display_name'], round_id=str(event['round_id']), delta=event['delta'], reason=event['reason'])
            uid = event['twitch_user_id']
            state['scores'][uid] = state['scores'].get(uid, 0) + event['delta']
        state['activity'] = [r['payload'] for r in db.execute('SELECT payload FROM activity_events WHERE session_id = %s ORDER BY sequence DESC LIMIT 50', (sid,)).fetchall()]
        return state

    @staticmethod
    def timestamp(value):
        return datetime.fromtimestamp(value, timezone.utc) if value is not None else None

    def _save(self, db, state):
        session = state['session']
        sid = session['id']
        previous = db.execute('SELECT active_session_id FROM workspaces WHERE key=%s', (self.workspace,)).fetchone()['active_session_id']
        if previous and str(previous) != sid:
            # Reset/replacement archives the old session rather than deleting history.
            db.execute("UPDATE sessions SET status='ended', payload=payload || '{\"status\":\"ended\"}'::jsonb WHERE id=%s", (previous,))
            db.execute("UPDATE rounds SET state='cancelled', payload=payload || '{\"state\":\"cancelled\"}'::jsonb WHERE session_id=%s AND state='voting'", (previous,))
            db.execute("UPDATE attempts SET status='skipped', payload=payload || '{\"status\":\"skipped\"}'::jsonb WHERE session_id=%s AND status IN ('running','paused','expired')", (previous,))
        db.execute('''INSERT INTO sessions (id, workspace_key, creator_id, status, version, payload)
                      VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO UPDATE
                      SET status=EXCLUDED.status, version=EXCLUDED.version, payload=EXCLUDED.payload''',
                   (sid, self.workspace, session['creator_id'], session['status'], session['version'], self.json(session)))
        for position, challenge in enumerate(state['challenges']):
            db.execute('''INSERT INTO challenges (id, session_id, position, approval_status, payload) VALUES (%s,%s,%s,%s,%s)
                          ON CONFLICT (id) DO UPDATE SET approval_status=EXCLUDED.approval_status, payload=EXCLUDED.payload''',
                       (challenge['id'], sid, position, challenge['approval_status'], self.json(challenge)))
        round_ = state['round']
        if round_:
            db.execute('''INSERT INTO rounds (id, session_id, state, closes_at, payload) VALUES (%s,%s,%s,%s,%s)
                          ON CONFLICT (id) DO UPDATE SET state=EXCLUDED.state, closes_at=EXCLUDED.closes_at, payload=EXCLUDED.payload''',
                       (round_['id'], sid, round_['state'], self.timestamp(round_['closes_at']), self.json(round_)))
            for option in round_['options']:
                db.execute('''INSERT INTO round_options (session_id, round_id, option_number, challenge_id) VALUES (%s,%s,%s,%s)
                              ON CONFLICT (round_id, option_number) DO NOTHING''', (sid, round_['id'], option['number'], option['challenge_id']))
            for viewer, vote in state['votes'].items():
                db.execute('''INSERT INTO votes (round_id, twitch_user_id, option_number, updated_at) VALUES (%s,%s,%s,%s)
                              ON CONFLICT (round_id, twitch_user_id) DO UPDATE SET option_number=EXCLUDED.option_number, updated_at=EXCLUDED.updated_at''',
                           (round_['id'], viewer, vote['option'], self.timestamp(vote['updated_at'])))
        attempt = state['attempt']
        if attempt:
            db.execute('''INSERT INTO attempts (id, session_id, round_id, challenge_id, status, ends_at, payload) VALUES (%s,%s,%s,%s,%s,%s,%s)
                          ON CONFLICT (id) DO UPDATE SET status=EXCLUDED.status, ends_at=EXCLUDED.ends_at, payload=EXCLUDED.payload''',
                       (attempt['id'], sid, attempt['round_id'], attempt['challenge_id'], attempt['status'], self.timestamp(attempt['ends_at']), self.json(attempt)))
        for key, event in state['score_events'].items():
            db.execute('''INSERT INTO score_events (session_id, round_id, twitch_user_id, display_name, delta, reason, idempotency_key)
                          VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (idempotency_key) DO NOTHING''',
                       (sid, event['round_id'], event['user_id'], event['name'], event['delta'], event['reason'], key))
        for event in state['activity']:
            db.execute('INSERT INTO activity_events (id, session_id, occurred_at, sequence, payload) VALUES (%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING',
                       (event['id'], sid, self.timestamp(event['at']), event['sequence'], self.json(event)))
        db.execute('UPDATE sessions SET active_round_id=%s, active_attempt_id=%s WHERE id=%s',
                   (round_['id'] if round_ else None, attempt['id'] if attempt else None, sid))
        db.execute('UPDATE workspaces SET active_session_id=%s WHERE key=%s', (sid, self.workspace))

    @contextmanager
    def ingestion_leader(self):
        key = int.from_bytes(hashlib.sha256(('eventsub:'+self.workspace).encode()).digest()[:8], signed=True)
        try:
            with self.connect() as connection:
                connection.autocommit = True
                acquired = connection.execute('SELECT pg_try_advisory_lock(%s) AS acquired', (key,)).fetchone()['acquired']
                try:
                    yield connection if acquired else None
                finally:
                    if acquired and not connection.closed:
                        connection.execute('SELECT pg_advisory_unlock(%s)', (key,))
        except self.driver.Error:
            raise DomainError('storage_unavailable',503) from None

    def write_chat_status(self, connection, payload):
        connection.execute('''INSERT INTO eventsub_runtime (workspace_key,payload) VALUES (%s,%s)
                              ON CONFLICT (workspace_key) DO UPDATE SET payload=EXCLUDED.payload, updated_at=now()''',
                           (self.workspace,self.json(payload)))

    def read_chat_status(self):
        try:
            with self.connect() as connection:
                row = connection.execute('SELECT payload, updated_at FROM eventsub_runtime WHERE workspace_key=%s', (self.workspace,)).fetchone()
                if not row:
                    return {'state':'connecting'}
                result = dict(row['payload'])
                if (datetime.now(timezone.utc)-row['updated_at']).total_seconds() > 30:
                    result['state'] = 'reconnecting'
                return result
        except self.driver.Error:
            return {'state':'storage_unavailable'}

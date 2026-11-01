"""Account store with a transaction lock shared by callback, refresh and disconnect."""
import copy
import threading
from contextlib import contextmanager
from .repository import UnitOfWork
from ..services.common import DomainError

def empty():
    return {'connections':{}, 'sessions':{}, 'states':{}, 'revocations':{}}

class MemoryAccountStore:
    storage = 'memory'
    def __init__(self, namespace='demo'):
        self.namespace = namespace
        self.lock = threading.RLock()
        self.state = empty()

    @contextmanager
    def transaction(self):
        with self.lock:
            unit = UnitOfWork(copy.deepcopy(self.state))
            yield unit
            self.state = copy.deepcopy(unit.state)

class PostgresAccountStore:
    storage = 'postgresql'
    def __init__(self, repository):
        self.repository = repository
        self.namespace = repository.workspace

    @contextmanager
    def transaction(self):
        repo = self.repository
        try:
            with repo.connect() as db:
                db.execute('INSERT INTO auth_namespaces (key) VALUES (%s) ON CONFLICT (key) DO NOTHING', (self.namespace,))
                db.execute('SELECT key FROM auth_namespaces WHERE key=%s FOR UPDATE', (self.namespace,)).fetchone()
                state = self._load(db)
                before = copy.deepcopy(state)
                unit = UnitOfWork(state)
                yield unit
                if state != before:
                    self._save(db, state, before)
        except repo.driver.Error:
            raise DomainError('account_storage_unavailable', 503) from None

    def _load(self, db):
        state = empty()
        rows = db.execute('''SELECT c.*, u.profile FROM oauth_connections c JOIN twitch_users u
                           USING (namespace, twitch_user_id) WHERE c.namespace=%s''', (self.namespace,)).fetchall()
        for row in rows:
            state['connections'][row['twitch_user_id']] = dict(profile=row['profile'],
                access_cipher=row['access_cipher'], refresh_cipher=row['refresh_cipher'],
                expires_at=row['expires_at'].timestamp(),
                validated_at=row['validated_at'].timestamp() if row['validated_at'] else None,
                validation_process=row['validation_process'],
                retry_after=row['retry_after'].timestamp() if row['retry_after'] else None, status=row['status'])
        for row in db.execute('SELECT * FROM browser_sessions WHERE namespace=%s', (self.namespace,)).fetchall():
            state['sessions'][row['session_hash']] = dict(user_id=row['twitch_user_id'], expires_at=row['expires_at'].timestamp())
        for row in db.execute('SELECT * FROM oauth_states WHERE namespace=%s', (self.namespace,)).fetchall():
            state['states'][row['state_hash']] = row['expires_at'].timestamp()
        for row in db.execute('SELECT * FROM token_revocations WHERE namespace=%s', (self.namespace,)).fetchall():
            state['revocations'][str(row['id'])] = dict(user_id=row['twitch_user_id'], token_cipher=row['token_cipher'], next_attempt=row['next_attempt'].timestamp())
        return state

    def _save(self, db, state, before):
        repo, namespace = self.repository, self.namespace
        for uid, connection in state['connections'].items():
            if before['connections'].get(uid) == connection:
                continue
            db.execute('''INSERT INTO twitch_users (namespace, twitch_user_id, profile) VALUES (%s,%s,%s)
                          ON CONFLICT (namespace,twitch_user_id) DO UPDATE SET profile=EXCLUDED.profile''', (namespace, uid, repo.json(connection['profile'])))
            db.execute('''INSERT INTO oauth_connections (namespace,twitch_user_id,access_cipher,refresh_cipher,expires_at,validated_at,validation_process,retry_after,status)
                          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (namespace,twitch_user_id) DO UPDATE SET
                          access_cipher=EXCLUDED.access_cipher,refresh_cipher=EXCLUDED.refresh_cipher,expires_at=EXCLUDED.expires_at,
                          validated_at=EXCLUDED.validated_at,validation_process=EXCLUDED.validation_process,retry_after=EXCLUDED.retry_after,status=EXCLUDED.status''',
                       (namespace,uid,connection['access_cipher'],connection['refresh_cipher'],repo.timestamp(connection['expires_at']),repo.timestamp(connection['validated_at']),connection['validation_process'],repo.timestamp(connection['retry_after']),connection['status']))
        for key in before['sessions'].keys()-state['sessions'].keys():
            db.execute('DELETE FROM browser_sessions WHERE namespace=%s AND session_hash=%s', (namespace,key))
        for key, session in state['sessions'].items():
            if before['sessions'].get(key) != session:
                db.execute('''INSERT INTO browser_sessions (namespace,session_hash,twitch_user_id,expires_at) VALUES (%s,%s,%s,%s)
                              ON CONFLICT (namespace,session_hash) DO UPDATE SET expires_at=EXCLUDED.expires_at''',
                           (namespace,key,session['user_id'],repo.timestamp(session['expires_at'])))
        for key in before['states'].keys()-state['states'].keys():
            db.execute('DELETE FROM oauth_states WHERE namespace=%s AND state_hash=%s', (namespace,key))
        for key, expires in state['states'].items():
            if before['states'].get(key) != expires:
                db.execute('INSERT INTO oauth_states (namespace,state_hash,expires_at) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING', (namespace,key,repo.timestamp(expires)))

        for key in before['revocations'].keys()-state['revocations'].keys():
            db.execute('DELETE FROM token_revocations WHERE namespace=%s AND id=%s', (namespace,key))
        for key, job in state['revocations'].items():
            if before['revocations'].get(key) != job:
                db.execute('''INSERT INTO token_revocations (namespace,id,twitch_user_id,token_cipher,next_attempt) VALUES (%s,%s,%s,%s,%s)
                              ON CONFLICT (namespace,id) DO UPDATE SET next_attempt=EXCLUDED.next_attempt''',
                           (namespace,key,job['user_id'],job['token_cipher'],repo.timestamp(job['next_attempt'])))

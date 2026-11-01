"""Real PostgreSQL integration checks; set TEST_DATABASE_URL to a disposable DB.

Every test uses an isolated workspace; no existing rows or schemas are deleted.
The database is migrated, so never point this suite at a production database.
"""
import os
import tempfile
from pathlib import Path
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from apps.api.app.persistence.repository import MemoryRepository, PostgresRepository
from apps.api.app.services.sessions import SessionService
from apps.api.app.services.common import DomainError
from test_backend import Clock

class MemoryTransactionTests(unittest.TestCase):
    def test_repository_survives_service_recreation(self):
        repository = MemoryRepository()
        first = SessionService(repository=repository)
        first.execute('/api/v1/session/pause', {})
        second = SessionService(repository=repository)
        self.assertEqual(second.snapshot()['session']['status'], 'paused')

    def test_rejected_command_rolls_back_intermediate_state(self):
        repository = MemoryRepository()
        service = SessionService(repository=repository, clock=Clock())
        original = service.snapshot()
        with self.assertRaises(DomainError): service.execute('/api/v1/rounds', {})
        self.assertEqual(service.snapshot(), original)

@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'Set TEST_DATABASE_URL for PostgreSQL integration tests')
class PostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dsn = os.environ['TEST_DATABASE_URL']
        PostgresRepository(cls.dsn).migrate()

    def setUp(self):
        self.workspace = 'integration-'+str(uuid.uuid4())
        self.clock = Clock()
        self.repository = PostgresRepository(self.dsn, self.workspace)
        self.service = SessionService(repository=self.repository, clock=self.clock)

    def call(self, path, **data): return self.service.execute('/api/v1/'+path, data)

    def ready(self):
        for row in self.service.snapshot()['challenges']:
            self.call('challenges/'+row['id']+'/approval', approval='approved')
        return self.call('rounds')

    def recover(self):
        return SessionService(repository=PostgresRepository(self.dsn, self.workspace), clock=self.clock)

    def test_migrations_are_repeatable(self):
        self.repository.migrate()
        self.repository.migrate()
        with self.repository.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) AS count FROM schema_migrations').fetchone()['count'], 2)

    def test_applied_migration_cannot_be_rewritten(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / '001_sessions.sql'
            path.write_text('SELECT 1; -- changed applied migration')
            with patch('apps.api.app.persistence.repository.MIGRATIONS', Path(folder)):
                with self.assertRaisesRegex(RuntimeError, 'checksum changed'):
                    self.repository.migrate()

    def test_database_outage_is_reported_without_connection_details(self):
        with patch.object(self.repository, 'connect', side_effect=self.repository.driver.OperationalError('private_connection_details')):
            with self.assertRaises(DomainError) as error:
                self.service.snapshot()
        self.assertEqual(error.exception.status, 503)
        self.assertEqual(str(error.exception), 'storage_unavailable')

    def test_restart_restores_votes_points_and_activity(self):
        self.ready()
        self.call('demo/vote', viewer='viewer-1', option=2)
        self.call('demo/vote', viewer='viewer-1', option=3)
        before = self.service.snapshot()
        after = self.recover().snapshot()
        self.assertEqual(after, before)
        self.assertEqual(after['scores'], [{'name':'Maple', 'points':1}])
        with self.repository.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) AS count FROM votes WHERE round_id=%s', (before['round']['id'],)).fetchone()['count'], 1)

    def test_paused_vote_survives_restart(self):
        self.ready()
        self.clock.advance(13)
        self.call('session/pause')
        self.clock.advance(900)
        self.service = self.recover()
        self.assertEqual(self.service.snapshot()['round']['remaining_sec'], 47)
        resumed = self.call('session/resume')
        self.assertEqual(resumed['round']['closes_at'], self.clock.now+47)

    def test_overdue_vote_closes_after_restart(self):
        self.ready()
        self.clock.advance(70)
        snapshot = self.recover().snapshot()
        self.assertEqual(snapshot['round']['state'], 'resolved')
        self.assertEqual(snapshot['round']['winner']['number'], 1)
        self.assertEqual(self.recover().snapshot()['session']['version'], snapshot['session']['version'])

    def test_paused_and_expired_attempts_survive_restart(self):
        self.ready()
        self.call('rounds/close')
        started = self.call('attempt')['attempt']
        self.clock.advance(10)
        self.call('session/pause')
        self.clock.advance(500)
        self.service = self.recover()
        self.assertEqual(self.service.snapshot()['attempt']['remaining_sec'], started['duration_sec']-10)
        self.call('session/resume')
        self.clock.advance(started['duration_sec'])
        self.service = self.recover()
        self.assertEqual(self.service.snapshot()['attempt']['status'], 'expired')
        self.call('attempt/action', action='skip')
        self.assertEqual(self.recover().snapshot()['attempt']['status'], 'skipped')

    def test_previous_rounds_and_attempts_remain_as_history(self):
        first = self.ready()
        self.call('demo/vote', viewer='viewer-1', option=1)
        self.call('rounds/close')
        self.call('attempt')
        self.call('attempt/action', action='complete')
        self.call('rounds')
        self.call('demo/vote', viewer='viewer-1', option=2)
        snapshot = self.recover().snapshot()
        self.assertEqual(snapshot['scores'][0]['points'], 2)
        self.assertIsNone(snapshot['attempt'])
        with self.repository.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) AS count FROM rounds WHERE session_id=%s', (first['session']['id'],)).fetchone()['count'], 2)
            self.assertEqual(db.execute('SELECT count(*) AS count FROM attempts WHERE session_id=%s', (first['session']['id'],)).fetchone()['count'], 1)

    def test_two_service_instances_serialize_votes(self):
        self.ready()
        other = self.recover()
        def vote(index):
            engine = self.service if index%2 else other
            return engine.execute('/api/v1/demo/vote', {'viewer':'viewer-1', 'option':index%3+1})
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(vote, range(30)))
        snapshot = self.recover().snapshot()
        self.assertEqual(sum(snapshot['tallies'].values()), 1)
        self.assertEqual(snapshot['scores'][0]['points'], 1)
        self.assertEqual(len({a['sequence'] for a in snapshot['activity']}), len(snapshot['activity']))

    def test_storage_failure_rolls_back_vote_and_score(self):
        self.ready()
        before = self.service.snapshot()
        save = self.repository._save
        def fail_after_writes(db, state):
            save(db, state)
            raise RuntimeError('injected failure before transaction commit')
        with patch.object(self.repository, '_save', side_effect=fail_after_writes):
            with self.assertRaises(RuntimeError): self.call('demo/vote', viewer='viewer-1', option=1)
        self.assertEqual(self.recover().snapshot(), before)

    def test_database_constraints_reject_duplicate_votes_and_points(self):
        snapshot = self.ready()
        self.call('demo/vote', viewer='viewer-1', option=1)
        rid = snapshot['round']['id']
        with self.assertRaises(self.repository.driver.errors.UniqueViolation):
            with self.repository.connect() as db:
                db.execute('INSERT INTO votes (round_id, twitch_user_id, option_number, updated_at) VALUES (%s,%s,1,now())', (rid,'viewer-1'))
        with self.assertRaises(self.repository.driver.errors.UniqueViolation):
            with self.repository.connect() as db:
                db.execute('''INSERT INTO score_events (session_id,round_id,twitch_user_id,display_name,delta,reason,idempotency_key)
                              VALUES (%s,%s,'viewer-1','Maple',1,'participation',%s)''',
                           (snapshot['session']['id'],rid,f'participation:{rid}:viewer-1'))

    def test_owner_survives_service_restart(self):
        live = SessionService(demo=False, repository=PostgresRepository(self.dsn,self.workspace,demo=False), clock=self.clock)
        created = live.execute('/api/v1/sessions', {}, 'creator')
        recovered = SessionService(demo=False, repository=PostgresRepository(self.dsn,self.workspace,demo=False), clock=self.clock)
        self.assertEqual(recovered.snapshot('creator')['session']['id'], created['session']['id'])
        with self.assertRaises(DomainError): recovered.snapshot('other')

    def test_demo_reset_cannot_change_live_workspace(self):
        live = SessionService(demo=False, repository=PostgresRepository(self.dsn,self.workspace,demo=False), clock=self.clock)
        created = live.execute('/api/v1/sessions', {}, 'creator')
        original_demo = self.ready()
        self.call('demo/reset')
        self.assertEqual(live.snapshot('creator')['session']['id'], created['session']['id'])
        with self.repository.connect() as db:
            row = db.execute('SELECT status FROM sessions WHERE id=%s', (original_demo['session']['id'],)).fetchone()
            self.assertEqual(row['status'], 'ended')

# Run the same behavior contract against real PostgreSQL, not a mock adapter.
from test_backend import LifecycleTests as _LifecycleContract

@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'Set TEST_DATABASE_URL for PostgreSQL lifecycle tests')
class PostgresLifecycleTests(_LifecycleContract):
    @classmethod
    def setUpClass(cls):
        cls.dsn = os.environ['TEST_DATABASE_URL']
        PostgresRepository(cls.dsn).migrate()

    def setUp(self):
        self.clock = Clock()
        repository = PostgresRepository(self.dsn, 'contract-'+str(uuid.uuid4()))
        self.game = SessionService(clock=self.clock, repository=repository)

# Avoid collecting the imported base class a second time in this module.
del _LifecycleContract

if __name__ == '__main__': unittest.main()

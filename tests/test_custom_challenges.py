"""Creator-authored challenge validation, approval and voting contract."""
from challenge_fixtures import add_challenges
import os
import unittest
import uuid
from apps.api.app.services.sessions import SessionService
from apps.api.app.services.common import DomainError
from apps.api.app.persistence.repository import MemoryRepository,PostgresRepository
from test_backend import Clock

def draft(**overrides):
    return dict(title='Draw your mascot',instructions='Use three shapes to sketch a mascot. Participation is optional.',
                category='Creative',duration_sec=60,safety_confirmed=True) | overrides

class CustomChallengeTests(unittest.TestCase):
    def setUp(self):
        self.clock=Clock();self.repo=MemoryRepository()
        self.game=SessionService(demo=False,clock=self.clock,repository=self.repo)
        self.game.execute('/api/v1/sessions',{},'42');add_challenges(self.game,'42')
    def call(self,path,data=None,actor='42'):return self.game.execute('/api/v1/'+path,data or {},actor)
    def create(self,data=None):return self.call('challenges',draft() if data is None else data)['challenges'][-1]
    def approve(self,row):return self.call('challenges/'+row['id']+'/approval',{'approval':'approved'})
    def test_replacement_session_starts_empty(self):
        self.call('session/end');self.call('sessions')
        self.assertEqual(self.game.snapshot('42')['challenges'],[])
        with self.assertRaises(DomainError):self.call('rounds')

    def test_legacy_presets_retired_but_creator_challenges_preserved(self):
        legacy=dict(self.game.snapshot('42')['challenges'][0])
        legacy.update(id=str(uuid.uuid4()),source='curated',title='Old preset',approval_status='approved')
        with self.game._transaction():self.game.state['challenges'].append(legacy)
        recovered=SessionService(demo=False,clock=self.clock,repository=self.repo)
        rows=recovered.snapshot('42')['challenges']
        self.assertEqual(len(rows),3);self.assertTrue(all(c['source']=='creator' for c in rows))
        with self.assertRaises(DomainError):recovered.execute('/api/v1/challenges/'+legacy['id']+'/approval',{'approval':'approved'},'42')
        with self.assertRaises(DomainError):recovered.execute('/api/v1/rounds',{'challenge_ids':[rows[0]['id'],legacy['id']]},'42')
        restarted=SessionService(demo=False,clock=self.clock,repository=self.repo)
        self.assertEqual(restarted.snapshot('42')['challenges'],rows)

    def test_save_is_pending_and_survives_recreation(self):
        row=self.create()
        self.assertEqual(row['source'],'creator');self.assertEqual(row['approval_status'],'pending')
        recovered=SessionService(demo=False,clock=self.clock,repository=self.repo)
        self.assertEqual(recovered.snapshot('42')['challenges'][-1],row)
    def test_new_draft_never_enters_vote_without_approval(self):
        row=self.create()
        first=self.game.snapshot('42')['challenges'][0];self.approve(first)
        with self.assertRaises(DomainError):self.call('rounds',{'challenge_ids':[first['id'],row['id']]})
        self.approve(row)
        state=self.call('rounds',{'challenge_ids':[row['id'],first['id']]})
        self.assertEqual([o['challenge_id'] for o in state['round']['options']],[row['id'],first['id']])
        self.call('rounds/close');attempt=self.call('attempt')['attempt']
        self.assertEqual(attempt['title'],row['title']);self.assertEqual(attempt['duration_sec'],60)
    def test_selected_custom_challenge_can_replace_old_first_three(self):
        rows=self.game.snapshot('42')['challenges']
        for row in rows:self.approve(row)
        custom=self.create();self.approve(custom)
        state=self.call('rounds',{'challenge_ids':[custom['id'],rows[2]['id']]})
        self.assertEqual(len(state['round']['options']),2)
        self.assertEqual(state['round']['options'][0]['title'],custom['title'])
    def test_invalid_payloads_leave_no_draft(self):
        cases=[('title',''),('title',' '*20),('title','x'*81),('title',[]),('title','title\x00bad'),
               ('instructions',''),('instructions','x'*1001),('category',[]),('category','Unknown'),
               ('duration_sec',True),('duration_sec',29),('duration_sec',1801),('duration_sec','60'),
               ('safety_confirmed',False),('approval_status','approved'),('points',100000),('source','curated')]
        for key,value in cases:
            data=draft();data[key]=value
            with self.subTest(key=key,value=repr(value)[:30]):
                with self.assertRaises(DomainError) as error:self.create(data)
                self.assertEqual(error.exception.status,400)
        self.assertEqual(len(self.game.snapshot('42')['challenges']),3)
    def test_missing_fields_are_rejected(self):
        for key in draft():
            data=draft();del data[key]
            with self.subTest(key=key),self.assertRaises(DomainError):self.create(data)
    def test_other_user_and_anonymous_cannot_create(self):
        for actor in [None,'different-user']:
            with self.subTest(actor=actor),self.assertRaises(DomainError):self.call('challenges',draft(),actor)
    def test_curated_suggestion_endpoint_removed(self):
        self.create()
        with self.assertRaises(DomainError) as error:self.call('challenges/curated')
        self.assertEqual(error.exception.status,404)
        self.assertEqual(len(self.game.snapshot('42')['challenges']),4)
    def test_saving_during_vote_cannot_change_the_active_options(self):
        rows=self.game.snapshot('42')['challenges']
        for row in rows:self.approve(row)
        before=self.call('rounds')['round']['options']
        self.create()
        self.assertEqual(self.game.snapshot('42')['round']['options'],before)
    def test_invalid_selections_rejected(self):
        rows=self.game.snapshot('42')['challenges']
        for row in rows:self.approve(row)
        ids=[row['id'] for row in rows]
        for selection in [[],ids[:1],ids+[ids[0]],[ids[0],ids[0]],'invalid',[ids[0],[]],[ids[0],'unknown']]:
            with self.subTest(selection=selection),self.assertRaises(DomainError):self.call('rounds',{'challenge_ids':selection})
    def test_paused_session_can_save_a_pending_draft(self):
        self.call('session/pause')
        row=self.create()
        self.assertEqual(row['approval_status'],'pending')
        self.assertEqual(self.game.snapshot('42')['session']['status'],'paused')

    def test_ended_session_cannot_create(self):
        self.call('session/end')
        with self.assertRaises(DomainError):self.create()

@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'),'Set TEST_DATABASE_URL for persistent custom challenges')
class PostgresCustomChallengeTests(CustomChallengeTests):
    @classmethod
    def setUpClass(cls):PostgresRepository(os.environ['TEST_DATABASE_URL']).migrate()
    def setUp(self):
        self.clock=Clock();self.repo=PostgresRepository(os.environ['TEST_DATABASE_URL'],'custom-'+str(uuid.uuid4()),demo=False)
        self.game=SessionService(demo=False,clock=self.clock,repository=self.repo)
        self.game.execute('/api/v1/sessions',{},'42');add_challenges(self.game,'42')

if __name__=='__main__':unittest.main()

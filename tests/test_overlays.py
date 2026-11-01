"""OBS capability security, approved projection, persistence and real HTTP contract."""
from challenge_fixtures import add_challenges
import json
import os
import uuid
import unittest
from unittest.mock import patch, Mock
from http.server import ThreadingHTTPServer
from threading import Thread
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from apps.api.app import main
from apps.api.app.services.sessions import SessionService
from apps.api.app.services.common import DomainError
from apps.api.app.persistence.repository import MemoryRepository, PostgresRepository
from test_backend import Clock

class OverlayTests(unittest.TestCase):
    def setUp(self):
        self.clock=Clock();self.repo=MemoryRepository();self.game=SessionService(False,self.clock,self.repo)
        self.game.execute('/api/v1/sessions',{},'42');add_challenges(self.game,'42')
    def call(self,path,data=None):return self.game.execute('/api/v1/'+path,data or {},'42')
    def ready(self):
        for c in self.game.snapshot('42')['challenges'][:2]:self.call('challenges/'+c['id']+'/approval',{'approval':'approved'})
        self.call('rounds')
    def test_unauthenticated_and_wrong_owner_cannot_issue_or_revoke(self):
        for actor in (None,'other'):
            for revoke in (False,True):
                with self.subTest(actor=actor,revoke=revoke), self.assertRaises(DomainError):self.game.overlay_link(actor,revoke)
    def test_only_hash_stored_and_no_access_metadata_in_creator_snapshot(self):
        link=self.game.overlay_link('42');snapshot=self.game.snapshot('42')
        self.assertNotIn('overlay_access',snapshot['session'])
        self.assertNotIn(link['token'],json.dumps(self.repo.state if self.repo.storage=='memory' else self.game.state))
        self.assertEqual(self.game.overlay_snapshot(link['token'])['session']['id'],snapshot['session']['id'])
    def test_missing_bad_and_modified_tokens_denied(self):
        token=self.game.overlay_link('42')['token']
        for value in (None,'',token+'x','x'*44,token[:10], 'x'*10000):
            with self.subTest(value=str(value)[:10]),self.assertRaises(DomainError):self.game.overlay_snapshot(value)
    def test_expiry_at_boundary_and_rotation_and_revocation(self):
        first=self.game.overlay_link('42');second=self.game.overlay_link('42')
        with self.assertRaises(DomainError):self.game.overlay_snapshot(first['token'])
        self.clock.now=second['expires_at']-1;self.game.overlay_snapshot(second['token'])
        self.clock.advance(1)
        with self.assertRaises(DomainError):self.game.overlay_snapshot(second['token'])
        third=self.game.overlay_link('42');self.game.overlay_link('42',revoke=True)
        with self.assertRaises(DomainError):self.game.overlay_snapshot(third['token'])
    def test_session_end_and_replacement_deny_old_token(self):
        token=self.game.overlay_link('42')['token'];self.call('session/end')
        with self.assertRaises(DomainError):self.game.overlay_snapshot(token)
        with self.assertRaises(DomainError):self.game.overlay_link('42')
        self.call('sessions')
        with self.assertRaises(DomainError):self.game.overlay_snapshot(token)
    def test_token_survives_service_restart(self):
        token=self.game.overlay_link('42')['token'];restarted=SessionService(False,self.clock,self.repo)
        self.assertEqual(restarted.overlay_snapshot(token)['session']['status'],'live')
        restarted.overlay_link('42',revoke=True)
        with self.assertRaises(DomainError):self.game.overlay_snapshot(token)
    def test_projection_excludes_drafts_identities_activity_and_metadata(self):
        self.ready();token=self.game.overlay_link('42')['token'];data=self.game.overlay_snapshot(token)
        self.assertEqual(set(data),{'demo_mode','server_time','session','round','attempt','tallies'})
        self.assertEqual(set(data['session']),{'id','status'})
        self.assertEqual(len(data['round']['options']),2)
        self.assertNotIn('Architecture texture hunt',json.dumps(data))
        self.assertNotIn('overlay_access',json.dumps(data));self.assertNotIn('creator_id',json.dumps(data))
        self.assertNotIn('challenges',data);self.assertNotIn('activity',data)
    def test_revoked_approval_hides_winner_and_attempt_text(self):
        self.ready();self.call('rounds/close');self.call('attempt');token=self.game.overlay_link('42')['token']
        winner=self.game.snapshot('42')['round']['winner']
        self.call('challenges/'+winner['challenge_id']+'/approval',{'approval':'rejected'})
        data=self.game.overlay_snapshot(token)
        self.assertIsNone(data['round']['winner']);self.assertIsNone(data['attempt']);self.assertNotIn(winner['title'],json.dumps(data))
    def test_pause_and_server_deadline_and_attempt_timer(self):
        self.ready();token=self.game.overlay_link('42')['token'];self.clock.advance(10)
        self.assertEqual(self.game.overlay_snapshot(token)['round']['remaining_sec'],50)
        self.call('session/pause');self.clock.advance(90)
        data=self.game.overlay_snapshot(token);self.assertEqual(data['session']['status'],'paused');self.assertEqual(data['round']['remaining_sec'],50)
        self.call('session/resume');self.clock.advance(50)
        self.assertEqual(self.game.overlay_snapshot(token)['round']['state'],'resolved')
        self.call('attempt');self.clock.advance(20)
        data=self.game.overlay_snapshot(token);self.assertEqual(data['attempt']['remaining_sec'],100)
        self.call('attempt/action',{'action':'skip'})
    def test_preview_requires_owner_but_no_token(self):
        with self.assertRaises(DomainError):self.game.overlay_snapshot(preview=True)
        with self.assertRaises(DomainError):self.game.overlay_snapshot(actor='other',preview=True)
        self.assertFalse(self.game.overlay_snapshot(actor='42',preview=True)['demo_mode'])
    def test_cancelled_round_is_not_broadcast(self):
        self.ready();token=self.game.overlay_link('42')['token'];self.call('rounds/skip')
        data=self.game.overlay_snapshot(token);self.assertIsNone(data['round']);self.assertIsNone(data['attempt'])

@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'),'Set TEST_DATABASE_URL for persistent overlays')
class PostgresOverlayTests(OverlayTests):
    @classmethod
    def setUpClass(cls):PostgresRepository(os.environ['TEST_DATABASE_URL']).migrate()
    def setUp(self):
        self.clock=Clock();self.repo=PostgresRepository(os.environ['TEST_DATABASE_URL'],'overlay-'+str(uuid.uuid4()),False)
        self.game=SessionService(False,self.clock,self.repo);self.game.execute('/api/v1/sessions',{},'42');add_challenges(self.game,'42')

class OverlayHTTPTests(unittest.TestCase):
    def setUp(self):
        self.game=SessionService(False);self.game.execute('/api/v1/sessions',{},'42');add_challenges(self.game,'42')
        auth=Mock();auth.authenticate.side_effect=lambda cookie: {'id':'42'} if cookie=='owner-cookie' else None
        auth.disconnect.return_value=False
        self.patches=[patch.object(main,'GAME',self.game),patch.object(main,'AUTH',auth),patch.object(main,'CHAT',None),patch.object(main.Handler,'log_message',lambda *args:None)]
        for p in self.patches:
            p.start();self.addCleanup(p.stop)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),main.Handler)
        self.thread=Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base='http://127.0.0.1:'+str(self.server.server_port)
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join()

    def request(self,path,token=None,owner=False,data=None):
        headers={'Content-Type':'application/json'}
        if token:headers['Authorization']='Bearer '+token
        if owner:headers['Cookie']='dareloop_session=owner-cookie'
        req=Request(self.base+path,headers=headers,data=json.dumps(data).encode() if data is not None else None)
        try:response=urlopen(req,timeout=3)
        except HTTPError as error:response=error
        with response:return response.status,response.headers,response.read()
    def issue(self):
        status,_,raw=self.request('/api/v1/overlay/link',owner=True,data={});self.assertEqual(status,200)
        return json.loads(raw)['data']['token']
    def test_browser_source_works_without_cookie_and_token_cannot_control_game(self):
        token=self.issue();status,_,raw=self.request('/api/v1/overlay/state',token)
        self.assertEqual(status,200);self.assertNotIn(b'creator_id',raw)
        self.assertEqual(self.request('/api/v1/state',token)[0],401)
        self.assertEqual(self.request('/api/v1/session/end',token,data={})[0],401)
        self.assertEqual(self.request('/api/v1/overlay/link',token,data={})[0],401)
        self.assertEqual(self.request('/api/v1/overlay/revoke',token,data={})[0],401)
    def test_no_token_query_cookie_or_fragment_can_bypass_bearer_auth(self):
        token=self.issue()
        self.assertEqual(self.request('/api/v1/overlay/state',owner=True)[0],401)
        self.assertEqual(self.request('/api/v1/overlay/state?token='+token)[0],401)
        self.assertEqual(self.request('/api/v1/overlay/preview')[0],401)
        self.assertEqual(self.request('/api/v1/overlay/preview',owner=True)[0],200)
    def test_revoke_and_disconnect_invalidate_browser_source(self):
        token=self.issue();self.request('/api/v1/overlay/revoke',owner=True,data={})
        self.assertEqual(self.request('/api/v1/overlay/state',token)[0],401)
        token=self.issue();self.request('/api/v1/auth/twitch/disconnect',owner=True,data={})
        self.assertEqual(self.request('/api/v1/overlay/state',token)[0],401)
    def test_shell_available_without_login_and_sends_privacy_headers(self):
        status,headers,body=self.request('/overlay/live')
        self.assertEqual(status,200);self.assertIn(b'overlay.js',body)
        self.assertEqual(headers['Referrer-Policy'],'no-referrer');self.assertEqual(headers['Cache-Control'],'no-store')

if __name__=='__main__':unittest.main()

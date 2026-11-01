"""P0 lifecycle regression tests. No Twitch credentials or network required."""
import io
import json
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch, Mock

from apps.api.app.services.sessions import SessionService
from apps.api.app.services.common import DomainError
from apps.api.app import main

class Clock:
    def __init__(self): self.now = 1000.0
    def __call__(self): return self.now
    def advance(self, seconds): self.now += seconds

class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.game = SessionService(clock=self.clock)

    def call(self, path, **data): return self.game.execute('/api/v1/'+path, data)

    def ready(self):
        for row in self.game.snapshot()['challenges']:
            self.call('challenges/'+row['id']+'/approval', approval='approved')
        return self.call('rounds')

    def start(self):
        self.ready()
        self.call('rounds/close')
        return self.call('attempt')

    def rejects(self, code, func, *args, **kwargs):
        with self.assertRaises(DomainError) as result: func(*args, **kwargs)
        self.assertEqual(result.exception.code, code)

    def test_requires_approved_pool(self):
        self.rejects('approve_at_least_two_challenges', self.call, 'rounds')

    def test_repeat_votes_and_changes_award_one_point(self):
        self.ready()
        for option in [1, 1, 2, 3, 2]: self.call('demo/vote', viewer='viewer-1', option=option)
        state = self.game.snapshot()
        self.assertEqual(state['tallies'], {'1':0, '2':1, '3':0})
        self.assertEqual(state['scores'], [{'name':'Maple', 'points':1}])
        self.assertEqual(len(self.game.state['score_events']), 1)

    def test_concurrent_votes_remain_unique(self):
        self.ready()
        with ThreadPoolExecutor(max_workers=10) as pool:
            list(pool.map(lambda n: self.call('demo/vote', viewer='viewer-1', option=n%3+1), range(100)))
        state = self.game.snapshot()
        self.assertEqual(sum(state['tallies'].values()), 1)
        self.assertEqual(state['scores'][0]['points'], 1)

    def test_close_deadline_is_exclusive_and_tie_is_deterministic(self):
        self.ready()
        self.clock.advance(60)
        self.rejects('voting_closed', self.call, 'demo/vote', viewer='viewer-1', option=2)
        state = self.game.snapshot()
        self.assertEqual(state['round']['state'], 'resolved')
        self.assertEqual(state['round']['winner']['number'], 1)

    def test_tie_selects_lowest_option(self):
        self.ready()
        self.call('demo/vote', viewer='viewer-1', option=2)
        self.call('demo/vote', viewer='viewer-2', option=3)
        self.assertEqual(self.call('rounds/close')['round']['winner']['number'], 2)

    def test_vote_pause_freezes_time_and_blocks_mutations(self):
        self.ready()
        self.clock.advance(12)
        self.call('session/pause')
        self.clock.advance(500)
        state = self.game.snapshot()
        self.assertEqual(state['round']['remaining_sec'], 48)
        self.assertIsNone(state['round']['closes_at'])
        self.rejects('session_paused', self.call, 'demo/vote', viewer='viewer-1', option=1)
        self.rejects('session_paused', self.call, 'rounds')
        resumed = self.call('session/resume')
        self.assertEqual(resumed['round']['closes_at'], self.clock.now+48)
        self.clock.advance(47)
        self.call('demo/vote', viewer='viewer-1', option=1)
        self.clock.advance(1)
        self.assertEqual(self.game.snapshot()['round']['state'], 'resolved')

    def test_attempt_pause_resume_and_skip(self):
        initial = self.start()['attempt']
        self.clock.advance(20)
        self.call('session/pause')
        self.clock.advance(500)
        paused = self.game.snapshot()['attempt']
        self.assertEqual(paused['status'], 'paused')
        self.assertEqual(paused['remaining_sec'], initial['duration_sec']-20)
        resumed = self.call('session/resume')['attempt']
        self.assertEqual(resumed['ends_at'], self.clock.now+initial['duration_sec']-20)
        self.call('session/pause')
        self.assertEqual(self.call('attempt/action', action='skip')['attempt']['status'], 'skipped')

    def test_expiration_is_once_and_outcome_still_available(self):
        attempt = self.start()['attempt']
        self.clock.advance(attempt['duration_sec'])
        state = self.game.snapshot()
        self.assertEqual(state['attempt']['status'], 'expired')
        version = state['session']['version']
        self.assertEqual(self.game.snapshot()['session']['version'], version)
        self.rejects('finish_or_skip_attempt_first', self.call, 'rounds')
        self.assertEqual(self.call('attempt/action', action='complete')['attempt']['status'], 'completed')

    def test_complete_then_next_round_clears_old_attempt(self):
        self.start()
        self.call('attempt/action', action='complete')
        self.rejects('attempt_already_closed', self.call, 'attempt/action', action='complete')
        self.rejects('attempt_already_started', self.call, 'attempt')
        next_round = self.call('rounds')
        self.assertIsNone(next_round['attempt'])
        self.assertEqual(next_round['round']['state'], 'voting')

    def test_no_overlapping_rounds_or_attempts(self):
        self.ready()
        self.rejects('round_already_open', self.call, 'rounds')
        self.call('rounds/close')
        self.rejects('start_or_skip_winner_first', self.call, 'rounds')
        self.call('attempt')
        self.rejects('attempt_already_started', self.call, 'attempt')
        self.rejects('finish_or_skip_attempt_first', self.call, 'rounds')

    def test_approval_is_enforced_on_vote_and_start(self):
        self.ready()
        cid = self.game.snapshot()['round']['options'][0]['challenge_id']
        self.rejects('close_vote_before_changing_approval', self.call, 'challenges/'+cid+'/approval', approval='rejected')
        self.call('rounds/close')
        self.call('challenges/'+cid+'/approval', approval='rejected')
        self.rejects('challenge_approval_required', self.call, 'attempt')
        self.call('rounds/skip')
        self.assertEqual(self.call('rounds')['round']['state'], 'voting')

    def test_skip_winner_without_starting_timer(self):
        self.ready()
        self.call('rounds/close')
        self.assertEqual(self.call('rounds/skip')['round']['state'], 'cancelled')
        self.call('rounds')

    def test_round_skip_remains_available_while_paused(self):
        self.ready()
        self.call('session/pause')
        self.assertEqual(self.call('rounds/skip')['round']['state'], 'cancelled')

    def test_pause_at_deadline_does_not_resurrect_voting(self):
        self.ready()
        self.clock.advance(60)
        self.call('session/pause')
        self.assertEqual(self.game.snapshot()['round']['state'], 'resolved')
        self.call('session/resume')
        self.rejects('voting_closed', self.call, 'demo/vote', viewer='viewer-1', option=1)

    def test_end_is_terminal_and_cancels_active_work(self):
        self.start()
        state = self.call('session/end')
        self.assertEqual(state['session']['status'], 'ended')
        self.assertEqual(state['attempt']['status'], 'skipped')
        for path in ['rounds', 'attempt', 'session/resume', 'session/pause']:
            self.rejects('session_ended', self.call, path)

    def test_participation_restarts_each_round(self):
        self.ready()
        self.call('demo/vote', viewer='viewer-1', option=1)
        self.call('rounds/close')
        self.call('attempt')
        self.call('attempt/action', action='skip')
        self.call('rounds')
        self.call('demo/vote', viewer='viewer-1', option=1)
        self.assertEqual(self.game.snapshot()['scores'][0]['points'], 2)

    def test_snapshot_is_detached_from_server_state(self):
        state = self.game.snapshot()
        state['session']['status'] = 'ended'
        state['challenges'].clear()
        self.assertEqual(self.game.snapshot()['session']['status'], 'live')
        self.assertEqual(len(self.game.snapshot()['challenges']), 3)

    def test_invalid_vote_shapes_are_rejected(self):
        self.ready()
        for viewer, option in [('viewer-1', True), ('viewer-1', 1.0), ([], 1), ('unknown', 1), ('viewer-1', 4)]:
            self.rejects('invalid_vote', self.call, 'demo/vote', viewer=viewer, option=option)
        self.rejects('json_object_required', self.game.execute, '/api/v1/rounds', [])

class OwnershipTests(unittest.TestCase):
    def setUp(self): self.game = SessionService(demo=False, clock=Clock())

    def test_authentication_and_owner_required(self):
        for actor, expected in [(None,401), ('stranger',403)]:
            with self.assertRaises(DomainError) as error: self.game.snapshot(actor)
            self.assertEqual(error.exception.status, expected)
        self.game.execute('/api/v1/sessions', {}, 'creator')
        for actor, expected in [(None,401), ('stranger',403)]:
            with self.assertRaises(DomainError) as error: self.game.execute('/api/v1/session/end', {}, actor)
            self.assertEqual(error.exception.status, expected)
        self.assertEqual(self.game.snapshot('creator')['session']['creator_id'], 'creator')

    def test_other_creator_cannot_replace_session(self):
        self.game.execute('/api/v1/sessions', {}, 'creator')
        self.game.execute('/api/v1/session/end', {}, 'creator')
        with self.assertRaises(DomainError): self.game.execute('/api/v1/sessions', {}, 'stranger')
        self.game.execute('/api/v1/sessions', {}, 'creator')

    def test_no_demo_mutations_in_live_mode(self):
        self.game.execute('/api/v1/sessions', {}, 'creator')
        for path in ['/api/v1/demo/reset','/api/v1/demo/vote']:
            with self.assertRaises(DomainError) as error: self.game.execute(path, {}, 'creator')
            self.assertEqual(error.exception.status, 403)

class Probe(main.Handler):
    """Exercise real route handlers without opening a listening socket."""
    def __init__(self, path, raw=b'{}', headers=None):
        self.path = path
        self.command = 'POST'
        self.headers = {'Content-Length': str(len(raw)), 'Content-Type':'application/json', 'Host':'localhost:8000', **(headers or {})}
        self.rfile = io.BytesIO(raw)
        self.result = None
    def send_json(self, code, body): self.result = (code, body)
    def send_page(self, code, title, message): self.result = (code, title)
    def redirect(self, location, cookie=None): self.result = (302, {'location':location, 'cookie':cookie})

class RouteTests(unittest.TestCase):
    def setUp(self):
        self.game_patch = patch.object(main, 'GAME', SessionService(demo=False))
        self.game_patch.start()
        self.addCleanup(self.game_patch.stop)
        self.sessions = {'test-token':{'user':{'id':'creator'},'expires_at':time.time()+60}}
        self.auth = Mock()
        self.auth.authenticate.side_effect = lambda token: self.sessions[token]['user'] if token in self.sessions and self.sessions[token]['expires_at'] > time.time() else None
        self.auth.complete.return_value = 'new-browser-session'
        self.auth_patch = patch.object(main,'AUTH',self.auth)
        self.auth_patch.start()
        self.addCleanup(self.auth_patch.stop)

    def post(self, path, raw=b'{}', headers=None):
        request = Probe(path, raw, headers)
        request.do_POST()
        return request.result

    def test_route_checks_cookie_and_owner(self):
        status, body = self.post('/api/v1/sessions')
        self.assertEqual(status, 401)
        self.assertIn('request_id', body)
        status, body = self.post('/api/v1/sessions', headers={'Cookie':'other=1; dareloop_session=test-token'})
        self.assertEqual(status, 200)
        self.assertEqual(body['data']['session']['creator_id'], 'creator')
        request = Probe('/api/v1/state')
        request.do_GET()
        self.assertEqual(request.result[0], 401)

    def test_foreign_origin_rejected(self):
        status, _ = self.post('/api/v1/sessions', headers={'Origin':'https://evil.example', 'Cookie':'dareloop_session=test-token'})
        self.assertEqual(status, 403)

    def test_body_validation(self):
        for raw, headers, status in [(b'[1]', {},400),(b'{bad',{},400),(b'{}',{'Content-Type':'text/plain'},415),(b'{}',{'Content-Length':'100001'},413)]:
            with self.subTest(raw=raw, headers=headers):
                self.assertEqual(self.post('/api/v1/sessions', raw, headers)[0], status)

    def test_same_origin_and_create_capability(self):
        request = Probe('/api/v1/me', headers={'Cookie':'dareloop_session=test-token'})
        request.do_GET()
        self.assertTrue(request.result[1]['data']['can_create_session'])
        self.assertEqual(self.post('/api/v1/sessions', headers={'Origin':'http://localhost:8000', 'Cookie':'dareloop_session=test-token'})[0], 200)
        request.do_GET()
        self.assertFalse(request.result[1]['data']['can_create_session'])

    def test_local_demo_remains_available_during_twitch_outage(self):
        with patch.object(main,'GAME',SessionService(demo=True)):
            self.auth.authenticate.side_effect=DomainError('twitch_temporarily_unavailable',503)
            request=Probe('/api/v1/state',headers={'Cookie':'dareloop_session=test-token'})
            request.do_GET()
            self.assertEqual(request.result[0],200)
            self.auth.authenticate.assert_not_called()

    def test_expired_cookie_is_not_authenticated(self):
        self.sessions['test-token']['expires_at'] = 0
        self.assertEqual(self.post('/api/v1/sessions', headers={'Cookie':'dareloop_session=test-token'})[0], 401)

    def test_oauth_callback_sets_only_an_opaque_http_only_cookie(self):
        request = Probe('/api/v1/auth/twitch/callback?state=state&code=code', headers={'Cookie':'dareloop_oauth_state=state'})
        request.do_GET()
        self.assertEqual(request.result[0],302)
        self.auth.complete.assert_called_once_with('state','state','code','')
        self.assertIn('HttpOnly; SameSite=Lax',request.result[1]['cookie'])
        self.assertNotIn('access_token',str(request.result))

if __name__ == '__main__': unittest.main()

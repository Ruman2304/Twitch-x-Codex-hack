"""In-memory session coordinator. Persistence will replace this storage boundary."""
import copy
import threading
import time
import uuid
from .common import DomainError
from . import safety, voting

class SessionService:
    def __init__(self, demo=True, clock=time.time):
        self.demo = demo
        self.clock = clock
        self.lock = threading.RLock()
        self.state = self._initial()

    def _initial(self, owner=None):
        return dict(session=dict(id=str(uuid.uuid4()), creator_id=owner,
                    title='Campus Field Notes', context='Campus', difficulty='Easy',
                    status='live' if self.demo or owner else 'draft', version=1),
                    challenges=[safety.challenge(c) for c in safety.SEEDS],
                    round=None, attempt=None, votes={}, scores={}, score_events={}, activity=[])

    def authorize(self, actor):
        if self.demo:
            return
        if not actor:
            raise DomainError('authentication_required', 401)
        if self.state['session']['creator_id'] != actor:
            raise DomainError('session_access_denied', 403)

    def _event(self, message):
        self.state['session']['version'] += 1
        self.state['activity'].insert(0, dict(id=str(uuid.uuid4()), at=int(self.clock()), message=message))
        del self.state['activity'][50:]

    def _tick(self):
        if self.state['session']['status'] != 'live':
            return
        now = self.clock()
        round_ = self.state['round']
        if round_ and round_['state'] == 'voting':
            round_['remaining_sec'] = max(0, round_['closes_at'] - now)
            if now >= round_['closes_at']:
                voting.close(self.state)
                self._event('Voting closed by server timer')
        attempt = self.state['attempt']
        if attempt and attempt['status'] == 'running':
            attempt['remaining_sec'] = max(0, attempt['ends_at'] - now)
            if now >= attempt['ends_at']:
                attempt['status'] = 'expired'
                self._event('Challenge timer expired')

    def snapshot(self, actor=None):
        with self.lock:
            self.authorize(actor)
            self._tick()
            s = self.state
            return copy.deepcopy(dict(demo_mode=self.demo, server_time=self.clock(),
                    session=s['session'], challenges=s['challenges'], round=s['round'],
                    attempt=s['attempt'], tallies=voting.tallies(s),
                    scores=sorted([dict(name=n, points=p) for n,p in s['scores'].items()], key=lambda r: (-r['points'], r['name'])),
                    activity=s['activity'], viewers=voting.VIEWERS if self.demo else {}))

    def execute(self, path, data, actor=None):
        with self.lock:
            if not isinstance(data, dict):
                raise DomainError('json_object_required', 400)
            if path == '/api/v1/sessions':
                if not actor and not self.demo:
                    raise DomainError('authentication_required', 401)
                owner = self.state['session']['creator_id']
                if owner is not None:
                    self.authorize(actor)
                    if self.state['session']['status'] != 'ended':
                        raise DomainError('session_already_active')
                self.state = self._initial(actor)
                self._event('Session created')
                return self.snapshot(actor)
            self.authorize(actor)
            self._tick()
            s = self.state
            session = s['session']
            now = self.clock()
            if path == '/api/v1/demo/reset':
                if not self.demo:
                    raise DomainError('demo_disabled', 403)
                self.state = self._initial()
                return self.snapshot(actor)
            if session['status'] == 'ended':
                raise DomainError('session_ended')
            if session['status'] == 'draft':
                raise DomainError('create_session_first')
            if path == '/api/v1/session/end':
                session['status'] = 'ended'
                session['ended_at'] = now
                if s['round'] and s['round']['state'] == 'voting':
                    s['round']['state'] = 'cancelled'
                if s['attempt'] and s['attempt']['status'] in ('running', 'paused', 'expired'):
                    s['attempt']['status'] = 'skipped'
                self._event('Session ended by creator')
                return self.snapshot(actor)
            if path in ('/api/v1/session/pause', '/api/v1/session/resume'):
                pause = session['status'] == 'live' if path.endswith('/pause') else False
                if not pause and session['status'] != 'paused':
                    raise DomainError('session_not_paused')
                session['status'] = 'paused' if pause else 'live'
                r, a = s['round'], s['attempt']
                if r and r['state'] == 'voting':
                    if pause:
                        r['remaining_sec'] = max(0, r['closes_at'] - now)
                        r['closes_at'] = None
                    else:
                        r['closes_at'] = now + r['remaining_sec']
                if a and a['status'] in ('running', 'paused'):
                    if pause:
                        a['remaining_sec'] = max(0, a['ends_at'] - now)
                        a['ends_at'] = None
                        a['status'] = 'paused'
                    else:
                        a['ends_at'] = now + a['remaining_sec']
                        a['status'] = 'running'
                self._event('Session paused' if pause else 'Session resumed')
                return self.snapshot(actor)
            # Safety outcomes remain available even while paused.
            if path == '/api/v1/attempt/action':
                a, action = s['attempt'], data.get('action')
                if action not in ('complete', 'skip') or not a:
                    raise DomainError('invalid_action', 400)
                if a['status'] not in ('running', 'paused', 'expired'):
                    raise DomainError('attempt_already_closed')
                a['status'] = 'completed' if action == 'complete' else 'skipped'
                a['ended_at'] = now
                self._event('Challenge '+a['status']+' by creator')
                return self.snapshot(actor)
            if path == '/api/v1/rounds/skip':
                r = s['round']
                if not r or r['state'] not in ('voting', 'resolved') or r.get('attempt_id'):
                    raise DomainError('no_skippable_round')
                r['state'] = 'cancelled'
                self._event('Round skipped by creator')
                return self.snapshot(actor)
            if session['status'] != 'live':
                raise DomainError('session_paused')
            if path.startswith('/api/v1/challenges/') and path.endswith('/approval'):
                c = safety.approve(s, path.split('/')[-2], data.get('approval'))
                self._event(c['title']+' '+c['approval_status'])
            elif path == '/api/v1/challenges/curated':
                index = len(s['challenges']) - len(safety.SEEDS)
                if index >= len(safety.SAFE_PACK):
                    raise DomainError('curated_pack_exhausted')
                s['challenges'].append(safety.challenge(safety.SAFE_PACK[index]))
                self._event('Curated challenge added for review')
            elif path == '/api/v1/rounds':
                voting.open_round(s, now)
                self._event('Voting opened for 60 seconds')
            elif path == '/api/v1/rounds/close':
                voting.close(s)
                self._event('Vote closed · '+s['round']['winner']['title']+' wins')
            elif path == '/api/v1/demo/vote':
                if not self.demo:
                    raise DomainError('demo_disabled', 403)
                voting.vote(s, data.get('viewer'), data.get('option'), now)
                self._event(voting.VIEWERS[data['viewer']]+' voted for option '+str(data['option']))
            elif path == '/api/v1/attempt':
                r = s['round']
                if not r or r['state'] != 'resolved':
                    raise DomainError('resolve_a_vote_first')
                if r.get('attempt_id'):
                    raise DomainError('attempt_already_started')
                c = safety.find(s, r['winner']['challenge_id'])
                if c['approval_status'] != 'approved':
                    raise DomainError('challenge_approval_required')
                a = dict(id=str(uuid.uuid4()), round_id=r['id'], challenge_id=c['id'],
                         title=c['title'], duration_sec=c['duration_sec'], status='running',
                         starts_at=now, ends_at=now+c['duration_sec'], remaining_sec=c['duration_sec'])
                s['attempt'] = a
                r['attempt_id'] = a['id']
                self._event('Challenge started · '+c['title'])
            else:
                raise DomainError('not_found', 404)
            return self.snapshot(actor)

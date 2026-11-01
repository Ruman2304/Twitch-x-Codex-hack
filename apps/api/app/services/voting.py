"""Round lifecycle and votes. Caller holds the session lock."""
import uuid
from .common import DomainError
from . import scoring

VIEWERS = {'viewer-1': 'Maple', 'viewer-2': 'Orbit', 'viewer-3': 'Pixel'}

def tallies(state):
    return {str(i): sum(v['option'] == i for v in state['votes'].values()) for i in (1, 2, 3)}

def open_round(state, now, challenge_ids=None):
    current = state['round']
    if current and current['state'] == 'voting':
        raise DomainError('round_already_open')
    if current and current['state'] == 'resolved' and not current.get('attempt_id'):
        raise DomainError('start_or_skip_winner_first')
    attempt = state['attempt']
    if attempt and attempt['status'] in ('running', 'paused', 'expired'):
        raise DomainError('finish_or_skip_attempt_first')
    approved = [c for c in state['challenges'] if c['approval_status'] == 'approved']
    if challenge_ids is not None:
        if not isinstance(challenge_ids,list) or not 2 <= len(challenge_ids) <= 3 or not all(isinstance(cid,str) for cid in challenge_ids) or len(set(challenge_ids)) != len(challenge_ids):
            raise DomainError('select_two_or_three_approved_challenges',400)
        by_id = {c['id']:c for c in approved}
        if any(cid not in by_id for cid in challenge_ids):
            raise DomainError('selected_challenge_not_approved',400)
        approved = [by_id[cid] for cid in challenge_ids]
    if len(approved) < 2:
        raise DomainError('approve_at_least_two_challenges', 400)
    state['round'] = dict(id=str(uuid.uuid4()), state='voting', starts_at=now,
                          closes_at=now + 60, remaining_sec=60, accepts_after=now,
                          options=[dict(number=i+1, challenge_id=c['id'], title=c['title']) for i, c in enumerate(approved[:3])])
    state['votes'] = {}
    state['attempt'] = None

def close(state):
    round_ = state['round']
    if not round_ or round_['state'] != 'voting':
        raise DomainError('no_active_round')
    counts = tallies(state)
    round_['winner'] = max(round_['options'], key=lambda o: (counts[str(o['number'])], -o['number']))
    round_['state'] = 'resolved'
    round_['tallies'] = counts
    round_['remaining_sec'] = 0

def vote(state, viewer, option, now, display_name=None):
    round_ = state['round']
    if not round_ or round_['state'] != 'voting' or now >= round_['closes_at']:
        raise DomainError('voting_closed')
    if not isinstance(viewer, str) or not viewer or (display_name is None and viewer not in VIEWERS) or type(option) is not int or option not in [o['number'] for o in round_['options']]:
        raise DomainError('invalid_vote', 400)
    state['votes'][viewer] = dict(option=option, updated_at=now)
    scoring.participation(state, viewer, display_name if display_name is not None else VIEWERS[viewer], round_['id'])

"""Session-scoped, read-only OBS capabilities and approved broadcast projection."""
import hashlib
import hmac
import secrets
from .common import DomainError
from . import voting

LIFETIME_SEC = 12 * 60 * 60

def issue(state, now):
    if state['session']['status'] not in ('live', 'paused'):
        raise DomainError('create_live_session_first', 409)
    token = secrets.token_urlsafe(32)
    expires = now + LIFETIME_SEC
    state['session']['overlay_access'] = {'hash': hashlib.sha256(token.encode()).hexdigest(), 'expires_at': expires}
    return {'token': token, 'expires_at': expires, 'session_id': state['session']['id']}

def revoke(state):
    state['session'].pop('overlay_access', None)

def verify(state, token, now):
    access = state['session'].get('overlay_access', {})
    if (not isinstance(token, str) or not 20 <= len(token) <= 128 or
        state['session']['status'] not in ('live', 'paused') or now >= access.get('expires_at', 0) or
        not hmac.compare_digest(access.get('hash', ''), hashlib.sha256(token.encode()).hexdigest())):
        raise DomainError('overlay_link_expired_or_revoked', 401)

def project(state, demo, now):
    """Allowlist fields: no review pool, activity, identities, scores or credentials."""
    session = state['session']
    approved = {c['id']: c for c in state['challenges'] if c['approval_status'] == 'approved'}
    result = dict(demo_mode=demo, server_time=now,
                  session={'id': session['id'], 'status': session['status']}, round=None, attempt=None, tallies={})
    if session['status'] not in ('live', 'paused'):
        return result
    r = state.get('round')
    if not r or r['state'] not in ('voting', 'resolved'):
        return result
    options = [dict(number=o['number'], title=approved[o['challenge_id']]['title'])
               for o in r['options'] if o['challenge_id'] in approved]
    counts = voting.tallies(state)
    result['tallies'] = {str(o['number']): counts.get(str(o['number']), 0) for o in options}
    winner = r.get('winner')
    safe_winner = None
    if winner and winner['challenge_id'] in approved:
        c = approved[winner['challenge_id']]
        safe_winner = dict(number=winner['number'], title=c['title'], instructions=c['instructions'])
    result['round'] = dict(id=r['id'], state=r['state'], options=options, winner=safe_winner,
                           remaining_sec=r.get('remaining_sec', 0), tallies=result['tallies'])
    a = state.get('attempt')
    if a and a['round_id'] == r['id'] and a['challenge_id'] in approved:
        c = approved[a['challenge_id']]
        result['attempt'] = dict(id=a['id'], round_id=r['id'], title=c['title'], instructions=c['instructions'],
                                 status=a['status'], remaining_sec=a['remaining_sec'])
    return result

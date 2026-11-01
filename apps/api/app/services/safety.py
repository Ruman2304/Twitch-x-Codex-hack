"""Creator challenge validation and explicit approval boundary."""
import uuid
import unicodedata
from .common import DomainError

def challenge(values):
    title, instructions, duration, points, category = values
    return dict(id=str(uuid.uuid4()), title=title, instructions=instructions,
                duration_sec=duration, points=points, category=category,
                safety_notes='Stay in a permitted area. Do not film people without consent.',
                approval_status='pending', source='creator')

def find(state, cid):
    row = next((c for c in state['challenges'] if c['id'] == cid and not c.get('archived')), None)
    if row is None:
        raise DomainError('challenge_not_found', 404)
    return row

def approve(state, cid, approval):
    if approval not in ('approved', 'rejected'):
        raise DomainError('invalid_approval', 400)
    row = find(state, cid)
    round_ = state['round']
    if round_ and round_['state'] == 'voting' and any(o['challenge_id'] == cid for o in round_['options']):
        raise DomainError('close_vote_before_changing_approval')
    row['approval_status'] = approval
    return row

CATEGORIES = ('Creative', 'Gaming', 'Observation', 'At home', 'Other')

def custom_challenge(data):
    """Validate creator-authored plain text; human approval remains mandatory."""
    allowed = {'title', 'instructions', 'category', 'duration_sec', 'safety_confirmed'}
    if set(data) - allowed:
        raise DomainError('unexpected_challenge_fields', 400)
    def text(key, limit):
        value = data.get(key)
        if not isinstance(value, str):
            raise DomainError('invalid_challenge_'+key, 400)
        value = unicodedata.normalize('NFKC', value).strip()
        if not 1 <= len(value) <= limit or any(unicodedata.category(c) in ('Cc','Cf') and c not in '\n\t' for c in value):
            raise DomainError('invalid_challenge_'+key, 400)
        return value
    title, instructions = text('title',80), text('instructions',1000)
    category, duration = data.get('category'), data.get('duration_sec')
    if category not in CATEGORIES:
        raise DomainError('invalid_challenge_category',400)
    if type(duration) is not int or not 30 <= duration <= 1800:
        raise DomainError('duration_must_be_30_to_1800_seconds',400)
    if data.get('safety_confirmed') is not True:
        raise DomainError('confirm_challenge_safety',400)
    row = challenge((title,instructions,duration,0,category))
    row.update(source='creator', safety_notes='Creator confirmed voluntary, safe, lawful participation. Anyone may decline or skip.')
    return row

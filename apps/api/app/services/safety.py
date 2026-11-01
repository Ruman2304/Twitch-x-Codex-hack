"""Curated challenge approval boundary; AI policy validation is a later slice."""
import uuid
import unicodedata
from .common import DomainError

SEEDS = [
    ('Spot three tree species', 'Find three different tree species in a public area. No need to leave the permitted grounds.', 120, 30, 'Discovery'),
    ('Find a public art detail', 'Notice one detail in a public art installation without interrupting anyone.', 90, 25, 'Observation'),
    ('Architecture texture hunt', 'Find an interesting building texture from a public path. Capture it only if no people are in frame.', 100, 25, 'Photography'),
]
SAFE_PACK = [
    ('Find a quiet color palette', 'Notice three colors from a permitted public path. Do not enter closed areas.', 90, 20, 'Observation'),
    ('Spot a wayfinding symbol', 'Describe a public sign without recording people or private information.', 90, 20, 'Discovery'),
    ('Skyline shape study', 'Find a distinctive roofline from a permitted spot. No climbing or leaving public paths.', 120, 25, 'Observation'),
]

def challenge(values):
    title, instructions, duration, points, category = values
    return dict(id=str(uuid.uuid4()), title=title, instructions=instructions,
                duration_sec=duration, points=points, category=category,
                safety_notes='Stay in a permitted area. Do not film people without consent.',
                approval_status='pending', source='curated')

def find(state, cid):
    row = next((c for c in state['challenges'] if c['id'] == cid), None)
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

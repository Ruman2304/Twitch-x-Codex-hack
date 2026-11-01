"""Curated challenge approval boundary; AI policy validation is a later slice."""
import uuid
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

"""Idempotent participation ledger; challenge completion awards no viewer points."""
def participation(state, viewer, name, round_id):
    key = f'participation:{round_id}:{viewer}'
    if key in state['score_events']:
        return
    state['score_events'][key] = dict(user_id=viewer, name=name, round_id=round_id, delta=1, reason='participation')
    state['scores'][name] = state['scores'].get(name, 0) + 1

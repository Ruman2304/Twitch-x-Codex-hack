"""Idempotent participation ledger; challenge completion awards no viewer points."""
def participation(state, viewer, name, round_id):
    key = f'participation:{round_id}:{viewer}'
    if key in state['score_events']:
        return
    state['score_events'][key] = dict(user_id=viewer, name=name, round_id=round_id, delta=1, reason='participation')
    state['scores'][viewer] = state['scores'].get(viewer, 0) + 1

def leaderboard(state):
    names = {event['user_id']:event['name'] for event in state['score_events'].values()}
    rows = [dict(user_id=uid,name=names[uid],points=points) for uid,points in state['scores'].items()]
    rows.sort(key=lambda row:(-row['points'],row['name'],row['user_id']))
    return [dict(name=row['name'],points=row['points']) for row in rows]

"""Explicit creator-authored data for lifecycle tests; never shipped as presets."""
def add_challenges(game, actor=None):
    for title, duration in [('Spot three tree species',120),('Find a public art detail',90),('Architecture texture hunt',100)]:
        game.execute('/api/v1/challenges',dict(title=title,instructions='Test challenge instructions. Participation is optional.',
                     category='Other',duration_sec=duration,safety_confirmed=True),actor)

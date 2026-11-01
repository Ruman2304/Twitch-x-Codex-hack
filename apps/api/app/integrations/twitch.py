"""Twitch OAuth HTTP adapter. Persistent refresh follows in the accounts slice."""
import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen

def exchange_profile(client_id, client_secret, redirect_uri, code):
    payload = urlencode(dict(client_id=client_id, client_secret=client_secret,
                             redirect_uri=redirect_uri, code=code, grant_type='authorization_code')).encode()
    request = Request('https://id.twitch.tv/oauth2/token', data=payload,
                      headers={'Content-Type': 'application/x-www-form-urlencoded'}, method='POST')
    with urlopen(request, timeout=15) as response:
        tokens = json.loads(response.read())
    access = tokens['access_token']
    request = Request('https://id.twitch.tv/oauth2/validate', headers={'Authorization': f'OAuth {access}'})
    with urlopen(request, timeout=15) as response:
        validation = json.loads(response.read())
    if validation.get('client_id') != client_id or 'user:read:chat' not in validation.get('scopes', []):
        raise ValueError('invalid_twitch_token')
    request = Request('https://api.twitch.tv/helix/users', headers={'Client-Id': client_id, 'Authorization': f'Bearer {access}'})
    with urlopen(request, timeout=15) as response:
        profile = json.loads(response.read())['data'][0]
    if profile['id'] != validation.get('user_id'):
        raise ValueError('twitch_identity_mismatch')
    user = {key: profile[key] for key in ('id', 'login', 'display_name')}
    user['profile_image_url'] = profile.get('profile_image_url', '')
    return user, tokens

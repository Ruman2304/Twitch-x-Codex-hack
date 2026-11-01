"""Twitch OAuth adapter. Provider errors never include secrets or response bodies."""
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

class TwitchFailure(Exception):
    def __init__(self, kind):
        super().__init__(kind)
        self.kind = kind

class TwitchClient:
    def __init__(self, client_id, client_secret, redirect_uri, opener=urlopen):
        self.client_id, self.client_secret = client_id, client_secret
        self.redirect_uri, self.opener = redirect_uri, opener

    def _request(self, url, form=None, headers=None, invalid_codes=(401,), json_body=None):
        data = urlencode(form).encode() if form is not None else None
        headers = dict(headers or {})
        if data is not None:
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
        if json_body is not None:
            data=json.dumps(json_body).encode()
            headers['Content-Type']='application/json'
        request = Request(url, data=data, headers=headers, method='POST' if data is not None else 'GET')
        try:
            with self.opener(request, timeout=10) as response:
                raw = response.read(1048577)
                if len(raw) > 1048576:
                    raise TwitchFailure('unavailable')
                return json.loads(raw) if raw else {}
        except HTTPError as error:
            raise TwitchFailure('invalid' if error.code in invalid_codes else 'forbidden' if error.code==403 else 'unavailable') from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise TwitchFailure('unavailable') from None

    def validate(self, access):
        result = self._request('https://id.twitch.tv/oauth2/validate', headers={'Authorization':f'OAuth {access}'})
        if not isinstance(result,dict) or result.get('client_id') != self.client_id or not isinstance(result.get('user_id'),str) or not result['user_id'] or not isinstance(result.get('scopes'),list) or 'user:read:chat' not in result['scopes'] or type(result.get('expires_in')) is not int or result['expires_in'] <= 0:
            raise TwitchFailure('invalid')
        return result

    def exchange(self, code):
        tokens = self._request('https://id.twitch.tv/oauth2/token', form=dict(client_id=self.client_id,
            client_secret=self.client_secret, redirect_uri=self.redirect_uri, code=code, grant_type='authorization_code'), invalid_codes=(400,401))
        try:
            validation = self.validate(tokens['access_token'])
            result = self._request('https://api.twitch.tv/helix/users', headers={'Client-Id':self.client_id,'Authorization':f"Bearer {tokens['access_token']}"})
            profile = result['data'][0]
            if profile['id'] != validation['user_id']:
                raise TwitchFailure('invalid')
            user = {key:profile[key] for key in ('id','login','display_name')}
            user['profile_image_url'] = profile.get('profile_image_url','')
            tokens['scope'] = validation['scopes']
            tokens['expires_in'] = validation['expires_in']
            return user, tokens
        except (KeyError, IndexError, TypeError):
            raise TwitchFailure('unavailable') from None

    def refresh(self, refresh):
        return self._request('https://id.twitch.tv/oauth2/token', form=dict(client_id=self.client_id,
            client_secret=self.client_secret, refresh_token=refresh, grant_type='refresh_token'), invalid_codes=(400,401))

    def revoke(self, access):
        self._request('https://id.twitch.tv/oauth2/revoke', form={'client_id':self.client_id,'token':access}, invalid_codes=(400,))

    def subscribe_chat(self, access, broadcaster_id, session_id):
        result=self._request('https://api.twitch.tv/helix/eventsub/subscriptions',
            headers={'Client-Id':self.client_id,'Authorization':f'Bearer {access}'},
            json_body={'type':'channel.chat.message','version':'1',
                       'condition':{'broadcaster_user_id':broadcaster_id,'user_id':broadcaster_id},
                       'transport':{'method':'websocket','session_id':session_id}})
        try:
            row=result['data'][0]
            if row['status']!='enabled' or not isinstance(row['id'],str) or not row['id']:
                raise ValueError('invalid subscription')
            return row['id']
        except (KeyError,IndexError,TypeError,ValueError):
            raise TwitchFailure('unavailable') from None

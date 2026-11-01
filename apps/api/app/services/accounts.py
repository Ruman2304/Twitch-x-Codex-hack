"""Durable OAuth, opaque browser sessions, validation, and serialized refresh."""
import hashlib
import secrets
import threading
import time
import uuid
from .common import DomainError
from ..integrations.twitch import TwitchFailure

SESSION_TTL = 86400
VALIDATION_INTERVAL = 3300  # Validate before Twitch's one-hour deadline.
RETRY_DELAY = 30

def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()

class AccountService:
    def __init__(self, store, cipher, provider, clock=time.time):
        self.store, self.cipher, self.provider, self.clock = store, cipher, provider, clock
        self.boot_id = secrets.token_hex(16)
        self.seen = {}

    def _context(self, uid, kind):
        return f'{self.store.namespace}:{uid}:{kind}'

    def _prune(self, state):
        now = self.clock()
        state['states'] = {key:expiry for key,expiry in state['states'].items() if expiry > now}
        state['sessions'] = {key:value for key,value in state['sessions'].items() if value['expires_at'] > now}

    def begin(self):
        token = secrets.token_urlsafe(32)
        with self.store.transaction() as unit:
            self._prune(unit.state)
            if len(unit.state['states']) >= 100:
                raise DomainError('too_many_sign_in_attempts', 429)
            unit.state['states'][digest(token)] = self.clock()+600
        return token

    def complete(self, state, bound_state, code, old_session=''):
        if not state or not bound_state or not secrets.compare_digest(state.encode(),bound_state.encode()):
            raise DomainError('oauth_state_invalid', 400)
        with self.store.transaction() as unit:
            expires = unit.state['states'].pop(digest(state), 0)
        if expires <= self.clock() or not code:
            raise DomainError('oauth_state_invalid', 400)
        try:
            profile, tokens = self.provider.exchange(code)
        except TwitchFailure:
            raise DomainError('twitch_sign_in_failed', 502) from None
        uid = profile['id']
        connection = dict(profile=profile, access_cipher=None, refresh_cipher=None, expires_at=0,
                          validated_at=self.clock(), validation_process=self.boot_id,
                          retry_after=None, status='connected')
        self._tokens(connection, uid, tokens)
        session = secrets.token_urlsafe(32)
        with self.store.transaction() as unit:
            self._prune(unit.state)
            unit.state['connections'][uid] = connection
            if old_session:
                unit.state['sessions'].pop(digest(old_session), None)
            unit.state['sessions'][digest(session)] = dict(user_id=uid, expires_at=self.clock()+SESSION_TTL)
        self.seen[uid] = digest(tokens['access_token'])
        return session

    def _tokens(self, connection, uid, tokens):
        try:
            access, refresh = tokens['access_token'], tokens['refresh_token']
            expiry = int(tokens['expires_in'])
            if not isinstance(access,str) or not access or not isinstance(refresh,str) or not refresh or expiry <= 0:
                raise ValueError('invalid token response')
        except (ValueError, KeyError, TypeError):
            raise DomainError('twitch_response_invalid', 502) from None
        connection['access_cipher'] = self.cipher.encrypt(access,self._context(uid,'access'))
        connection['refresh_cipher'] = self.cipher.encrypt(refresh,self._context(uid,'refresh'))
        connection['expires_at'] = self.clock()+expiry

    def _invalidate(self, state, uid, status='reconnect_required'):
        connection = state['connections'][uid]
        connection.update(status=status,access_cipher=None,refresh_cipher=None,validated_at=None,retry_after=None)
        state['sessions'] = {key:value for key,value in state['sessions'].items() if value['user_id'] != uid}
        self.seen.pop(uid,None)

    def _ensure(self, state, uid, rejected_token=None):
        connection = state['connections'][uid]
        if connection['status'] != 'connected':
            return None
        now = self.clock()
        if connection['retry_after'] and now < connection['retry_after']:
            raise DomainError('twitch_temporarily_unavailable',503)
        access = self.cipher.decrypt(connection['access_cipher'],self._context(uid,'access'))
        forced = rejected_token is not None and secrets.compare_digest(access.encode(),rejected_token.encode())
        due = (self.seen.get(uid) != digest(access) or connection['validated_at'] is None or
               now-connection['validated_at'] >= VALIDATION_INTERVAL or now >= connection['expires_at'])
        if not forced and not due:
            return access
        try:
            try:
                if forced:
                    raise TwitchFailure('invalid')
                validation = self.provider.validate(access)
            except TwitchFailure as error:
                if error.kind != 'invalid':
                    raise
                refresh = self.cipher.decrypt(connection['refresh_cipher'],self._context(uid,'refresh'))
                tokens = self.provider.refresh(refresh)
                # Save any rotated refresh token even if subsequent validation is unavailable.
                self._tokens(connection,uid,tokens)
                access = tokens['access_token']
                validation = self.provider.validate(access)
            if validation.get('user_id') != uid:
                raise TwitchFailure('invalid')
            expiry = int(validation['expires_in'])
            if expiry <= 0:
                raise TwitchFailure('invalid')
            connection.update(expires_at=now+expiry,validated_at=now,validation_process=self.boot_id,retry_after=None)
            self.seen[uid] = digest(access)
            return access
        except TwitchFailure as error:
            if error.kind == 'invalid':
                self._invalidate(state,uid)
                return None
            connection['retry_after'] = now+RETRY_DELAY
            raise DomainError('twitch_temporarily_unavailable',503) from None
        except DomainError:
            connection['retry_after'] = now+RETRY_DELAY
            raise
        except (KeyError, TypeError, ValueError):
            connection['retry_after'] = now+RETRY_DELAY
            raise DomainError('twitch_response_invalid',502) from None

    def authenticate(self, raw_session):
        if not raw_session:
            return None
        error, profile = None, None
        with self.store.transaction() as unit:
            state = unit.state
            self._prune(state)
            session = state['sessions'].get(digest(raw_session))
            if session and session['user_id'] in state['connections']:
                uid = session['user_id']
                try:
                    if self._ensure(state,uid):
                        profile = dict(state['connections'][uid]['profile'])
                except DomainError as failure:
                    error = failure
        if error:
            raise error
        return profile

    def access_token(self, uid, rejected_token=None):
        """Backend-only API; on a Twitch 401 pass the rejected token and retry once."""
        error, access = None, None
        with self.store.transaction() as unit:
            if uid in unit.state['connections']:
                try:
                    access = self._ensure(unit.state,uid,rejected_token)
                except DomainError as failure:
                    error = failure
        if error:
            raise error
        if not access:
            raise DomainError('twitch_reconnect_required',401)
        return access

    def invalidate_connection(self, uid):
        with self.store.transaction() as unit:
            if uid in unit.state['connections']:
                self._invalidate(unit.state,uid)

    def disconnect(self, raw_session):
        job_id = None
        with self.store.transaction() as unit:
            state = unit.state
            self._prune(state)
            session = state['sessions'].get(digest(raw_session)) if raw_session else None
            if session:
                uid = session['user_id']
                connection = state['connections'][uid]
                # Reuse encrypted access ciphertext in an outbox; no new plaintext stored.
                if connection['access_cipher']:
                    job_id = str(uuid.uuid4())
                    state['revocations'][job_id] = dict(user_id=uid,token_cipher=connection['access_cipher'],next_attempt=self.clock())
                self._invalidate(state,uid,'disconnected')
        self.maintenance(validate=False)
        with self.store.transaction() as unit:
            return bool(job_id and job_id in unit.state['revocations'])

    def maintenance(self, validate=True):
        with self.store.transaction() as unit:
            state = unit.state
            self._prune(state)
            if validate:
                for uid, connection in list(state['connections'].items()):
                    if connection['status']=='connected':
                        try:
                            self._ensure(state,uid)
                        except DomainError:
                            pass  # Retry later; authentication fails closed meanwhile.
            for key, job in list(state['revocations'].items()):
                if job['next_attempt'] > self.clock():
                    continue
                try:
                    access = self.cipher.decrypt(job['token_cipher'],self._context(job['user_id'],'access'))
                    self.provider.revoke(access)
                    del state['revocations'][key]
                except TwitchFailure as error:
                    if error.kind=='invalid':
                        del state['revocations'][key]  # Already revoked/invalid.
                    else:
                        job['next_attempt']=self.clock()+RETRY_DELAY
                except DomainError:
                    job['next_attempt']=self.clock()+RETRY_DELAY

    def start_worker(self):
        stop = threading.Event()
        def run():
            while not stop.wait(30):
                try:
                    self.maintenance()
                except DomainError:
                    pass
        # Validate stored connections once before accepting HTTP traffic.
        try:
            self.maintenance()
        except DomainError:
            pass
        worker = threading.Thread(target=run,name='twitch-account-maintenance',daemon=True)
        worker.start()
        return stop

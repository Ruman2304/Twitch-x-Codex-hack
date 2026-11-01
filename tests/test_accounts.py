"""Account contracts: no live Twitch access and no real credentials."""
import io
import json
import os
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs
from unittest.mock import patch

from apps.api.app.persistence.accounts import MemoryAccountStore, PostgresAccountStore
from apps.api.app.persistence.repository import PostgresRepository
from apps.api.app.services.accounts import AccountService, digest, VALIDATION_INTERVAL
from apps.api.app.services.common import DomainError
from apps.api.app.integrations.twitch import TwitchClient, TwitchFailure
from test_backend import Clock
try:
    from cryptography.fernet import Fernet
    from apps.api.app.security.tokens import TokenCipher
except ImportError:
    TokenCipher = None

class FakeTwitch:
    def __init__(self):
        self.validations = []
        self.refreshes = []
        self.revocations = []
        self.invalid_tokens = set()
        self.refresh_failure = None
        self.validate_failure = None
        self.revoke_failure = None
        self.user_id = '42'
    def exchange(self, code):
        return {'id':'42','login':'maple','display_name':'Maple','profile_image_url':''}, dict(access_token='access-old',refresh_token='refresh-old',expires_in=3600)
    def validate(self, access):
        self.validations.append(access)
        if self.validate_failure: raise TwitchFailure(self.validate_failure)
        if access in self.invalid_tokens: raise TwitchFailure('invalid')
        return dict(client_id='client',user_id=self.user_id,scopes=['user:read:chat'],expires_in=3600)
    def refresh(self, token):
        self.refreshes.append(token)
        if self.refresh_failure: raise TwitchFailure(self.refresh_failure)
        return dict(access_token='access-next' if token=='refresh-new' else 'access-new',refresh_token='refresh-next' if token=='refresh-new' else 'refresh-new',expires_in=3600)
    def revoke(self, access):
        self.revocations.append(access)
        if self.revoke_failure: raise TwitchFailure(self.revoke_failure)

@unittest.skipIf(TokenCipher is None,'Install apps/api/requirements.txt for encryption tests')
class AccountTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.store = MemoryAccountStore()
        self.key = Fernet.generate_key().decode()
        self.cipher = TokenCipher(self.key)
        self.provider = FakeTwitch()
        self.auth = AccountService(self.store,self.cipher,self.provider,self.clock)

    def login(self, old=''):
        state = self.auth.begin()
        return self.auth.complete(state,state,'test-code',old)

    def recover(self):
        return AccountService(self.store,self.cipher,self.provider,self.clock)

    def test_storage_has_ciphertext_and_hashed_identifiers_only(self):
        raw = self.login()
        with self.store.transaction() as unit:
            encoded = json.dumps(unit.state)
            self.assertNotIn(raw,encoded)
            self.assertNotIn('access-old',encoded)
            self.assertNotIn('refresh-old',encoded)
            self.assertIn(digest(raw),unit.state['sessions'])
        profile = self.auth.authenticate(raw)
        self.assertEqual(profile['id'],'42')
        self.assertNotIn('cipher',json.dumps(profile))

    def test_state_is_browser_bound_expiring_and_one_time(self):
        state = self.auth.begin()
        with self.assertRaises(DomainError): self.auth.complete(state,'wrong','code')
        raw = self.auth.complete(state,state,'code')
        self.assertIsNotNone(self.auth.authenticate(raw))
        with self.assertRaises(DomainError): self.auth.complete(state,state,'code')
        state = self.auth.begin()
        self.clock.advance(600)
        with self.assertRaises(DomainError): self.auth.complete(state,state,'code')

    def test_browser_session_survives_service_restart_and_validates(self):
        raw = self.login()
        recovered = self.recover()
        self.assertEqual(recovered.authenticate(raw)['id'],'42')
        self.assertEqual(self.provider.validations,['access-old'])
        recovered.authenticate(raw)
        self.assertEqual(len(self.provider.validations),1)

    def test_hourly_validation_runs_even_without_http_requests(self):
        self.login()
        self.clock.advance(VALIDATION_INTERVAL)
        self.auth.maintenance()
        self.assertEqual(self.provider.validations,['access-old'])

    def test_expired_or_rejected_access_refreshes_once_and_saves_rotation(self):
        raw = self.login()
        self.provider.invalid_tokens.add('access-old')
        self.assertEqual(self.auth.access_token('42',rejected_token='access-old'),'access-new')
        recovered = self.recover()
        self.assertEqual(recovered.authenticate(raw)['id'],'42')
        self.assertEqual(self.provider.refreshes,['refresh-old'])
        self.provider.invalid_tokens.add('access-new')
        recovered.access_token('42',rejected_token='access-new')
        self.assertEqual(self.provider.refreshes,['refresh-old','refresh-new'])

    def test_concurrent_401s_share_one_refresh(self):
        self.login()
        self.provider.invalid_tokens.add('access-old')
        recovered = self.recover()
        def refresh(index):
            service = recovered if index%2 else self.auth
            return service.access_token('42',rejected_token='access-old')
        with ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(refresh,range(20)))
        self.assertEqual(results,['access-new']*20)
        self.assertEqual(self.provider.refreshes,['refresh-old'])

    def test_revoked_refresh_invalidates_all_browser_sessions(self):
        one,two = self.login(),self.login()
        self.provider.invalid_tokens.add('access-old')
        self.provider.refresh_failure='invalid'
        self.assertIsNone(self.recover().authenticate(one))
        self.assertIsNone(self.auth.authenticate(two))
        with self.store.transaction() as unit:
            connection=unit.state['connections']['42']
            self.assertEqual(connection['status'],'reconnect_required')
            self.assertIsNone(connection['access_cipher'])
            self.assertIsNone(connection['refresh_cipher'])
            self.assertEqual(unit.state['sessions'],{})

    def test_transient_outage_fails_closed_but_preserves_credentials(self):
        raw = self.login()
        self.clock.advance(VALIDATION_INTERVAL)
        self.provider.validate_failure='unavailable'
        with self.assertRaises(DomainError) as error: self.auth.authenticate(raw)
        self.assertEqual(error.exception.status,503)
        with self.store.transaction() as unit:
            self.assertIn(digest(raw),unit.state['sessions'])
            self.assertIsNotNone(unit.state['connections']['42']['refresh_cipher'])
        count=len(self.provider.validations)
        with self.assertRaises(DomainError): self.auth.authenticate(raw)
        self.assertEqual(len(self.provider.validations),count)
        self.provider.validate_failure=None
        self.clock.advance(31)
        self.assertIsNotNone(self.auth.authenticate(raw))

    def test_refresh_rotation_is_saved_when_validation_is_temporarily_down(self):
        raw=self.login()
        original_validate=self.provider.validate
        def fail_new(access):
            if access=='access-new':raise TwitchFailure('unavailable')
            raise TwitchFailure('invalid')
        self.provider.validate=fail_new
        recovered=self.recover()
        with self.assertRaises(DomainError):recovered.authenticate(raw)
        self.provider.validate=original_validate
        self.clock.advance(31)
        self.assertIsNotNone(self.recover().authenticate(raw))
        self.assertEqual(self.provider.refreshes,['refresh-old'])
        with self.store.transaction() as unit:
            saved=unit.state['connections']['42']['refresh_cipher']
            self.assertEqual(self.cipher.decrypt(saved,self.auth._context('42','refresh')),'refresh-new')

    def test_wrong_identity_requires_reconnect(self):
        raw=self.login()
        self.provider.user_id='different-user'
        self.assertIsNone(self.recover().authenticate(raw))

    def test_expired_browser_session_cannot_be_revived_by_refresh(self):
        raw=self.login()
        self.clock.advance(86400)
        self.assertIsNone(self.auth.authenticate(raw))
        self.assertEqual(self.provider.refreshes,[])

    def test_new_login_replaces_only_current_cookie(self):
        original=self.login()
        new=self.login(old=original)
        self.assertIsNone(self.auth.authenticate(original))
        self.assertIsNotNone(self.auth.authenticate(new))

    def test_disconnect_revokes_and_removes_all_local_sessions(self):
        one,two=self.login(),self.login()
        self.assertFalse(self.auth.disconnect(one))
        self.assertIsNone(self.auth.authenticate(one))
        self.assertIsNone(self.auth.authenticate(two))
        self.assertEqual(self.provider.revocations,['access-old'])

    def test_disconnect_outage_is_durable_and_retried(self):
        raw=self.login()
        self.provider.revoke_failure='unavailable'
        self.assertTrue(self.auth.disconnect(raw))
        self.assertIsNone(self.recover().authenticate(raw))
        with self.store.transaction() as unit:
            self.assertEqual(len(unit.state['revocations']),1)
            self.assertNotIn('access-old',json.dumps(unit.state))
        self.provider.revoke_failure=None
        self.clock.advance(31)
        self.recover().maintenance()
        with self.store.transaction() as unit:self.assertEqual(unit.state['revocations'],{})

    def test_wrong_key_and_cross_account_cipher_swap_fail_closed(self):
        raw=self.login()
        wrong=AccountService(self.store,TokenCipher(Fernet.generate_key().decode()),self.provider,self.clock)
        with self.assertRaises(DomainError):wrong.authenticate(raw)
        encrypted=self.cipher.encrypt('private-token','user-a:access')
        with self.assertRaises(DomainError):self.cipher.decrypt(encrypted,'user-b:access')

    def test_key_ring_can_read_old_keys_and_write_new_key(self):
        new_key=Fernet.generate_key().decode()
        old_cipher=self.cipher.encrypt('private','context')
        ring=TokenCipher(new_key+','+self.key)
        self.assertEqual(ring.decrypt(old_cipher,'context'),'private')
        fresh=ring.encrypt('fresh','context')
        self.assertEqual(TokenCipher(new_key).decrypt(fresh,'context'),'fresh')

    def test_pending_state_survives_restart(self):
        state=self.auth.begin()
        raw=self.recover().complete(state,state,'code')
        self.assertIsNotNone(self.auth.authenticate(raw))

class ProviderTests(unittest.TestCase):
    def test_refresh_parameters_are_form_encoded(self):
        seen=[]
        class Response(io.BytesIO):
            def __enter__(self):return self
            def __exit__(self,*args):self.close()
        def opener(request,timeout):
            seen.append(request)
            return Response(b'{"access_token":"new","refresh_token":"rotated","expires_in":3600}')
        provider=TwitchClient('client','server-secret','http://localhost/callback',opener)
        provider.refresh('refresh%+&=')
        form=parse_qs(seen[0].data.decode())
        self.assertEqual(form['refresh_token'],['refresh%+&='])
        self.assertEqual(form['grant_type'],['refresh_token'])
        self.assertNotIn('server-secret',seen[0].full_url)

    def test_provider_errors_do_not_expose_response_bodies(self):
        def invalid(*args,**kwargs):raise HTTPError('https://id.twitch.tv',401,'private-provider-body',{},None)
        with self.assertRaises(TwitchFailure) as error:TwitchClient('c','s','u',invalid).validate('private-access')
        self.assertEqual(str(error.exception),'invalid')
        def unavailable(*args,**kwargs):raise URLError('private-details')
        with self.assertRaises(TwitchFailure) as error:TwitchClient('c','s','u',unavailable).refresh('private-refresh')
        self.assertEqual(str(error.exception),'unavailable')

    def test_wrong_app_or_scope_is_rejected(self):
        class Response(io.BytesIO):
            def __enter__(self):return self
            def __exit__(self,*args):pass
        for payload in [dict(client_id='wrong',user_id='42',scopes=['user:read:chat']),dict(client_id='client',user_id='42',scopes=[])]:
            with self.subTest(payload=payload):
                provider=TwitchClient('client','s','u',lambda *a,**kw:Response(json.dumps(payload).encode()))
                with self.assertRaises(TwitchFailure):provider.validate('token')

@unittest.skipUnless(os.getenv('TEST_DATABASE_URL') and TokenCipher is not None,'Set TEST_DATABASE_URL and install encryption dependencies')
class PostgresAccountTests(AccountTests):
    @classmethod
    def setUpClass(cls):
        PostgresRepository(os.environ['TEST_DATABASE_URL']).migrate()
    def setUp(self):
        super().setUp()
        repository=PostgresRepository(os.environ['TEST_DATABASE_URL'],'accounts-'+str(uuid.uuid4()))
        self.store=PostgresAccountStore(repository)
        self.auth=AccountService(self.store,self.cipher,self.provider,self.clock)
    def recover(self):
        store=PostgresAccountStore(PostgresRepository(os.environ['TEST_DATABASE_URL'],self.store.namespace.removesuffix(':demo')))
        return AccountService(store,self.cipher,self.provider,self.clock)

if __name__=='__main__':unittest.main()

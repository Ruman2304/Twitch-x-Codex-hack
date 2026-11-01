"""Authenticated token encryption; keys are supplied outside the database."""
import json
from cryptography.fernet import Fernet, MultiFernet, InvalidToken
from ..services.common import DomainError

class TokenCipher:
    def __init__(self, keys):
        try:
            self.cipher = MultiFernet([Fernet(key.strip()) for key in keys.split(',') if key.strip()])
        except (ValueError, TypeError):
            raise RuntimeError('TOKEN_ENCRYPTION_KEYS must contain valid Fernet keys.') from None

    def encrypt(self, token, context):
        return self.cipher.encrypt(json.dumps({'token':token, 'context':context}).encode()).decode()

    def decrypt(self, value, context):
        try:
            payload = json.loads(self.cipher.decrypt(value.encode()))
            if payload['context'] != context or not isinstance(payload['token'], str):
                raise ValueError('context mismatch')
            return payload['token']
        except (InvalidToken, KeyError, ValueError, TypeError, AttributeError):
            raise DomainError('credential_decryption_failed', 503) from None

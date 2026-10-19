import json

from cryptography.fernet import Fernet

from core.config import settings


def _fernet() -> Fernet:
    key = settings.credentials_encryption_key
    if not key:
        raise RuntimeError("CREDENTIALS_ENCRYPTION_KEY is not set")
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode()).decode()


def encrypt_credentials(credentials: dict) -> str:
    return encrypt_secret(json.dumps(credentials))


def decrypt_credentials(ciphertext: str) -> dict:
    plaintext = decrypt_secret(ciphertext)
    try:
        credentials = json.loads(plaintext)
    except json.JSONDecodeError:
        credentials = None
    # Legacy rows hold a bare pasted Notion token.
    if not isinstance(credentials, dict):
        return {"access_token": plaintext}
    return credentials

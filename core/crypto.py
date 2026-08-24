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

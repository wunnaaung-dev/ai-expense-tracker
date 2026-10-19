import time

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.crypto import decrypt_credentials, encrypt_credentials
from models.storage import UserStorageConfig
from services.google.errors import GooglePermissionError

DRIVE_FILE_SCOPE = "https://www.googleapis.com/auth/drive.file"
TOKEN_URL = "https://oauth2.googleapis.com/token"
# Refresh a little before Google's expiry so in-flight calls don't fail.
EXPIRY_MARGIN_SECONDS = 60


def has_sheets_scopes(scope: str | None) -> bool:
    # drive.file covers create/batchUpdate/developerMetadata/values on files this app created.
    return DRIVE_FILE_SCOPE in (scope or "").split()


def merge_google_credentials(old: dict | None, token: dict) -> dict:
    """Build stored credentials from a token response, keeping values Google didn't resend."""
    old = old or {}
    expires_at = token.get("expires_at")
    if expires_at is None and token.get("expires_in") is not None:
        expires_at = time.time() + int(token["expires_in"])

    credentials = {
        "access_token": token["access_token"],
        "expires_at": int(expires_at or 0),
        "scope": token.get("scope") or old.get("scope", ""),
    }
    # Google only returns a refresh token on consent; never drop the one we have.
    refresh_token = token.get("refresh_token") or old.get("refresh_token")
    if refresh_token:
        credentials["refresh_token"] = refresh_token
    return credentials


async def get_google_access_token(
    db: AsyncSession,
    config: UserStorageConfig,
    http_client: httpx.AsyncClient | None = None,
) -> str:
    credentials = decrypt_credentials(config.encrypted_credentials)
    if credentials.get("expires_at", 0) - EXPIRY_MARGIN_SECONDS > time.time():
        return credentials["access_token"]

    refresh_token = credentials.get("refresh_token")
    if not refresh_token:
        raise GooglePermissionError("No Google refresh token stored")

    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
    }
    if http_client is None:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(TOKEN_URL, data=data)
    else:
        response = await http_client.post(TOKEN_URL, data=data)

    if response.status_code in (400, 401) and response.json().get("error") == "invalid_grant":
        raise GooglePermissionError("Google refresh token was revoked or expired")
    response.raise_for_status()

    credentials = merge_google_credentials(credentials, response.json())
    config.encrypted_credentials = encrypt_credentials(credentials)
    await db.commit()
    return credentials["access_token"]

import time

import httpx
import pytest

from core.crypto import decrypt_credentials, encrypt_credentials
from models.storage import UserStorageConfig
from models.users import User
from services.google.credentials import (
    DRIVE_FILE_SCOPE,
    get_google_access_token,
    has_sheets_scopes,
    merge_google_credentials,
)
from services.google.errors import GooglePermissionError


def test_has_sheets_scopes_needs_drive_file_only():
    assert has_sheets_scopes(f"openid email {DRIVE_FILE_SCOPE}")
    assert not has_sheets_scopes("openid email profile")
    assert not has_sheets_scopes(None)


def test_merge_keeps_old_refresh_token_when_google_omits_it():
    old = {"access_token": "old", "refresh_token": "r1", "scope": "s"}
    merged = merge_google_credentials(old, {"access_token": "new", "expires_in": 3600})
    assert merged["access_token"] == "new"
    assert merged["refresh_token"] == "r1"
    assert merged["scope"] == "s"
    assert merged["expires_at"] > time.time()


def test_merge_takes_new_refresh_token():
    merged = merge_google_credentials(
        {"refresh_token": "r1"},
        {"access_token": "a", "refresh_token": "r2", "expires_at": 1},
    )
    assert merged["refresh_token"] == "r2"


async def _config(db, credentials):
    user = User(google_sub="g1", default_storage_provider="google_sheets")
    db.add(user)
    await db.flush()
    config = UserStorageConfig(
        user_id=user.id,
        provider="google_sheets",
        encrypted_credentials=encrypt_credentials(credentials),
    )
    db.add(config)
    await db.commit()
    return config


async def test_valid_token_is_returned_without_refresh(db):
    config = await _config(db, {"access_token": "live", "expires_at": time.time() + 3600})
    assert await get_google_access_token(db, config) == "live"


async def test_expired_token_is_refreshed_and_saved(db):
    config = await _config(
        db,
        {"access_token": "stale", "expires_at": 0, "refresh_token": "r1", "scope": "s"},
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert b"refresh_token=r1" in request.content
        return httpx.Response(200, json={"access_token": "fresh", "expires_in": 3600})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        token = await get_google_access_token(db, config, http_client=client)

    assert token == "fresh"
    stored = decrypt_credentials(config.encrypted_credentials)
    assert stored["access_token"] == "fresh"
    assert stored["refresh_token"] == "r1"


async def test_invalid_grant_raises_permission_error(db):
    config = await _config(db, {"access_token": "stale", "expires_at": 0, "refresh_token": "revoked"})
    transport = httpx.MockTransport(lambda r: httpx.Response(400, json={"error": "invalid_grant"}))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(GooglePermissionError):
            await get_google_access_token(db, config, http_client=client)


async def test_missing_refresh_token_raises_permission_error(db):
    config = await _config(db, {"access_token": "stale", "expires_at": 0})
    with pytest.raises(GooglePermissionError):
        await get_google_access_token(db, config)

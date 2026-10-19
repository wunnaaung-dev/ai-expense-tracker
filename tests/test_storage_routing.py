import time

import pytest
from fastapi.testclient import TestClient

from constants.storage_status import (
    STATUS_NEEDS_PERMISSION,
    STATUS_PROVISIONING,
    STATUS_READY,
    STATUS_SCHEMA_BROKEN,
)
from core.crypto import encrypt_credentials
from core.dependencies import get_storage_service
from core.security import get_current_user
from main import app
from models.storage import UserStorageConfig
from models.users import User
from services.google.errors import GooglePermissionError
from services.google_sheets.google_sheets_storage import GoogleSheetsStorage
from services.notion.notion_storage_service import NotionStorageService
from services.storage.errors import StorageNotReadyError


async def make_user(db, provider, status=None, target_id="target-1", credentials=None):
    user = User(
        google_sub="g" if provider == "google_sheets" else None,
        notion_user_id="n" if provider == "notion" else None,
        default_storage_provider=provider,
    )
    db.add(user)
    await db.flush()
    if status is not None:
        db.add(UserStorageConfig(
            user_id=user.id,
            provider=provider,
            encrypted_credentials=encrypt_credentials(
                credentials or {"access_token": "token", "expires_at": time.time() + 3600}
            ),
            target_id=target_id,
            storage_status=status,
        ))
    await db.commit()
    return user


async def test_ready_google_user_gets_sheets_storage(db):
    user = await make_user(db, "google_sheets", STATUS_READY)
    storage = await get_storage_service(db=db, current_user=user)
    assert isinstance(storage, GoogleSheetsStorage)
    assert storage.spreadsheet_id == "target-1"


async def test_ready_notion_user_gets_notion_storage(db):
    user = await make_user(db, "notion", STATUS_READY)
    storage = await get_storage_service(db=db, current_user=user)
    assert isinstance(storage, NotionStorageService)


@pytest.mark.parametrize("status", [STATUS_PROVISIONING, STATUS_SCHEMA_BROKEN, STATUS_NEEDS_PERMISSION])
async def test_not_ready_status_is_reported(db, status):
    user = await make_user(db, "google_sheets", status)
    with pytest.raises(StorageNotReadyError) as error:
        await get_storage_service(db=db, current_user=user)
    assert error.value.status == status


async def test_no_storage_row_is_none(db):
    user = await make_user(db, "notion")
    with pytest.raises(StorageNotReadyError) as error:
        await get_storage_service(db=db, current_user=user)
    assert error.value.status == "none"


async def test_revoked_google_grant_sets_needs_permission(db):
    user = await make_user(db, "google_sheets", STATUS_READY, credentials={"access_token": "x", "expires_at": 0})
    with pytest.raises(GooglePermissionError):
        await get_storage_service(db=db, current_user=user)
    config = await db.get(UserStorageConfig, 1)
    assert config.storage_status == STATUS_NEEDS_PERMISSION


def test_not_ready_maps_to_409_body():
    async def not_ready():
        raise StorageNotReadyError(STATUS_PROVISIONING)

    app.dependency_overrides[get_current_user] = lambda: object()
    app.dependency_overrides[get_storage_service] = not_ready
    try:
        response = TestClient(app).get("/api/v1/expense")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409
    assert response.json() == {"code": "storage_not_ready", "status": "provisioning"}

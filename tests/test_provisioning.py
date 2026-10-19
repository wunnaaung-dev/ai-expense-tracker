from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from constants.storage_status import (
    STATUS_FAILED,
    STATUS_NEEDS_PERMISSION,
    STATUS_PROVISIONING,
    STATUS_READY,
)
from core.dependencies import get_sheets_provisioning_service
from core.security import get_current_user
from main import app
from models.storage import UserStorageConfig
from schemas.storage import StorageStatusResponse
from services.auth.auth_service import (
    GOOGLE_LOGIN_NEEDS_PERMISSION,
    GOOGLE_LOGIN_OK,
    GOOGLE_LOGIN_PROVISIONING_FAILED,
    AuthService,
)
from services.google.errors import GoogleApiError
from services.google_sheets.provisioning import SheetsProvisioningService
from services.google_sheets.schema import FIELDS
from tests.fake_google import FakeGoogle
from tests.test_google_login import google_token


@pytest.fixture
def fake():
    return FakeGoogle()


def provisioning_for(db, fake):
    async def api_factory(config):
        return fake

    return SheetsProvisioningService(db, api_factory)


async def login(db, fake, **token_overrides):
    return await AuthService(db, provisioning_for(db, fake)).login_with_google(google_token(**token_overrides))


async def sheets_config(db) -> UserStorageConfig:
    config = (await db.execute(select(UserStorageConfig))).scalar_one()
    await db.refresh(config)
    return config


async def test_first_login_creates_exactly_one_spreadsheet(db, fake):
    result = await login(db, fake)

    assert result.outcome == GOOGLE_LOGIN_OK
    assert fake.create_calls == 1
    config = await sheets_config(db)
    assert config.storage_status == STATUS_READY
    spreadsheet = fake.only()
    assert config.target_id == spreadsheet.spreadsheet_id
    assert spreadsheet.title == "AI Expense Tracker"
    assert spreadsheet.app_properties == {"app": "ai-expense-tracker"}
    assert fake.main_tab().title == "Transactions"
    assert fake.main_tab().grid()[0] == [f.header for f in FIELDS]

    keys = [(m["key"], m["value"]) for m in spreadsheet.metadata]
    assert ("et_sheet", "transactions") in keys
    assert ("et_schema_version", "1") in keys
    assert sorted(v for k, v in keys if k == "et_field") == sorted(f.name for f in FIELDS)

    validations = [r for r in spreadsheet.other_requests if "setDataValidation" in r]
    assert len(validations) == 2
    protection = [r for r in spreadsheet.other_requests if "addProtectedRange" in r]
    assert protection[0]["addProtectedRange"]["protectedRange"]["warningOnly"] is True


async def test_second_login_reuses_spreadsheet(db, fake):
    await login(db, fake)
    first_id = (await sheets_config(db)).target_id

    result = await login(db, fake)

    assert result.outcome == GOOGLE_LOGIN_OK
    assert fake.create_calls == 1
    assert (await sheets_config(db)).target_id == first_id


async def test_lost_db_record_reuses_sheet_found_by_app_properties(db, fake):
    await login(db, fake)
    first_id = (await sheets_config(db)).target_id
    await db.execute(delete(UserStorageConfig))
    await db.commit()

    result = await login(db, fake)

    assert result.outcome == GOOGLE_LOGIN_OK
    assert fake.create_calls == 1
    config = await sheets_config(db)
    assert config.target_id == first_id
    assert config.storage_status == STATUS_READY


async def test_missing_drive_scope_needs_permission_but_login_succeeds(db, fake):
    result = await login(db, fake, scope="openid email profile")

    assert result.outcome == GOOGLE_LOGIN_NEEDS_PERMISSION
    assert result.access_token
    assert fake.create_calls == 0
    assert (await sheets_config(db)).storage_status == STATUS_NEEDS_PERMISSION


async def test_provisioning_error_sets_failed_but_login_succeeds(db, fake):
    fake.fail["create_spreadsheet"] = GoogleApiError(500, "backend error")

    result = await login(db, fake)

    assert result.outcome == GOOGLE_LOGIN_PROVISIONING_FAILED
    assert result.access_token
    assert (await sheets_config(db)).storage_status == STATUS_FAILED

    # Retry (POST /storage/provision) succeeds and doesn't leave a second file behind.
    status = await provisioning_for(db, fake).ensure_spreadsheet(result.user.id)
    assert status == STATUS_READY


async def test_failed_setup_is_retried_without_duplicate(db, fake):
    fake.fail["batch_update"] = GoogleApiError(500, "backend error")
    result = await login(db, fake)
    assert result.outcome == GOOGLE_LOGIN_PROVISIONING_FAILED

    # The half-made file was tagged first, so the retry reuses it.
    assert await provisioning_for(db, fake).ensure_spreadsheet(result.user.id) == STATUS_READY
    assert fake.create_calls == 1


async def test_live_claim_blocks_second_provisioner(db, fake):
    result = await login(db, fake, scope="openid email profile")
    config = await sheets_config(db)
    # Scope granted later; another request holds a fresh claim.
    await AuthService(db).login_with_google(google_token())
    config.storage_status = STATUS_PROVISIONING
    config.status_changed_at = datetime.now(timezone.utc)
    await db.commit()

    status = await provisioning_for(db, fake).ensure_spreadsheet(result.user.id)

    assert status == STATUS_PROVISIONING
    assert fake.create_calls == 0


async def test_stale_claim_is_reclaimed(db, fake):
    result = await login(db, fake, scope="openid email profile")
    await AuthService(db).login_with_google(google_token())
    config = await sheets_config(db)
    config.storage_status = STATUS_PROVISIONING
    config.status_changed_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    await db.commit()

    status = await provisioning_for(db, fake).ensure_spreadsheet(result.user.id)

    assert status == STATUS_READY
    assert fake.create_calls == 1


async def test_trashed_spreadsheet_is_replaced_on_login(db, fake):
    await login(db, fake)
    old_id = (await sheets_config(db)).target_id
    fake.spreadsheets[old_id].trashed = True

    await login(db, fake)

    config = await sheets_config(db)
    assert config.target_id != old_id
    assert config.storage_status == STATUS_READY
    assert fake.create_calls == 2


async def test_notion_only_user_cannot_provision(db, fake):
    assert await provisioning_for(db, fake).ensure_spreadsheet(user_id=999) == STATUS_NEEDS_PERMISSION


# --- Endpoints -----------------------------------------------------------------

class StubProvisioning:
    def __init__(self, result):
        self.result = result

    async def ensure_spreadsheet(self, user_id):
        return self.result

    async def get_status(self, user_id):
        return StorageStatusResponse(connected=True, provider="google_sheets", status=STATUS_READY)


class FakeUser:
    id = 1


@pytest.fixture
def client_for():
    def make(result):
        app.dependency_overrides[get_current_user] = FakeUser
        app.dependency_overrides[get_sheets_provisioning_service] = lambda: StubProvisioning(result)
        return TestClient(app)

    yield make
    app.dependency_overrides.clear()


def test_status_endpoint(client_for):
    response = client_for(STATUS_READY).get("/api/v1/storage/status")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


@pytest.mark.parametrize(
    ("result", "http_status", "code"),
    [
        (STATUS_PROVISIONING, 409, "provisioning_in_progress"),
        (STATUS_NEEDS_PERMISSION, 403, "needs_permission"),
        (STATUS_FAILED, 502, "provisioning_failed"),
    ],
)
def test_provision_endpoint_errors(client_for, result, http_status, code):
    response = client_for(result).post("/api/v1/storage/provision")
    assert response.status_code == http_status
    assert response.json()["code"] == code


def test_provision_endpoint_success(client_for):
    response = client_for(STATUS_READY).post("/api/v1/storage/provision")
    assert response.status_code == 200
    assert response.json()["connected"] is True

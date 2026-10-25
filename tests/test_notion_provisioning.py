from datetime import datetime, timezone

import httpx
import pytest
from notion_client import APIResponseError
from notion_client.errors import APIErrorCode
from sqlalchemy import select

from constants.storage_status import (
    STATUS_FAILED,
    STATUS_NEEDS_PERMISSION,
    STATUS_NONE,
    STATUS_PROVISIONING,
    STATUS_READY,
)
from models.storage import UserStorageConfig
from services.auth.auth_service import AuthService
from services.notion.provisioning import NotionProvisioningService
from services.storage.storage_config_service import StorageConfigService


def api_error(code: APIErrorCode, status: int = 400) -> APIResponseError:
    return APIResponseError(code, status, "boom", httpx.Headers(), "")


class FakeNotion:
    """Just the AsyncClient surface provisioning uses: search, databases.create/retrieve."""

    def __init__(self, pages=None):
        self.pages = pages if pages is not None else [page("p1", "My Home", "🏠")]
        self.databases_by_id: dict[str, dict] = {}
        self.create_calls = 0
        self.create_error: Exception | None = None
        self.search_error: Exception | None = None
        self.databases = self

    async def search(self, **kwargs):
        if self.search_error:
            raise self.search_error
        return {"results": self.pages, "has_more": False, "next_cursor": None}

    async def create(self, parent, title, initial_data_source):
        if self.create_error:
            raise self.create_error
        self.create_calls += 1
        database_id = f"db-{self.create_calls}"
        self.databases_by_id[database_id] = {
            "id": database_id,
            "parent": parent,
            "data_sources": [{"id": f"ds-{self.create_calls}"}],
        }
        return self.databases_by_id[database_id]

    async def retrieve(self, database_id):
        if database_id not in self.databases_by_id:
            raise api_error(APIErrorCode.ObjectNotFound, 404)
        return self.databases_by_id[database_id]


def page(page_id: str, title: str, emoji: str | None = None) -> dict:
    return {
        "id": page_id,
        "icon": {"type": "emoji", "emoji": emoji} if emoji else None,
        "properties": {"title": {"type": "title", "title": [{"plain_text": title}]}},
    }


def notion_token() -> dict:
    return {
        "access_token": "ntn_test",
        "bot_id": "bot",
        "workspace_id": "ws",
        "workspace_name": "Workspace",
        "owner": {"type": "user", "user": {"id": "notion-user-1", "name": "Wunna"}},
    }


@pytest.fixture
def fake():
    return FakeNotion()


async def login(db):
    user, _ = await AuthService(db).login_with_notion(notion_token())
    return user


def provisioning_for(db, fake):
    return NotionProvisioningService(db, lambda token: fake)


async def notion_config(db) -> UserStorageConfig:
    config = (await db.execute(select(UserStorageConfig))).scalar_one()
    await db.refresh(config)
    return config


async def test_login_leaves_storage_unset_and_status_reports_it(db):
    user = await login(db)

    status = await StorageConfigService(db).get_status(user.id)

    assert status.connected is False
    assert status.provider == "notion"
    assert status.status == STATUS_NONE
    assert status.target_id is None


async def test_list_pages_returns_title_and_icon(db, fake):
    user = await login(db)
    fake.pages.append(page("p2", "Untitled page"))
    fake.pages.append({**page("p3", "Old"), "in_trash": True})

    pages = await provisioning_for(db, fake).list_pages(user.id)

    assert [(p.id, p.title, p.icon) for p in pages] == [("p1", "My Home", "🏠"), ("p2", "Untitled page", None)]


async def test_setup_creates_database_under_chosen_page(db, fake):
    user = await login(db)
    fake.pages.append(page("p2", "Finance"))

    status = await provisioning_for(db, fake).ensure_database(user.id, "p2")

    assert status == STATUS_READY
    config = await notion_config(db)
    assert config.storage_status == STATUS_READY
    assert config.target_id == "db-1"
    assert fake.databases_by_id["db-1"]["parent"] == {"type": "page_id", "page_id": "p2"}
    assert (await StorageConfigService(db).get_status(user.id)).connected is True


async def test_setup_without_page_uses_first_shared_page(db, fake):
    user = await login(db)

    await provisioning_for(db, fake).ensure_database(user.id)

    assert fake.databases_by_id["db-1"]["parent"]["page_id"] == "p1"


async def test_repeat_setup_reuses_database(db, fake):
    user = await login(db)
    service = provisioning_for(db, fake)
    await service.ensure_database(user.id)

    assert await service.ensure_database(user.id) == STATUS_READY
    assert fake.create_calls == 1


async def test_relogin_keeps_database(db, fake):
    user = await login(db)
    await provisioning_for(db, fake).ensure_database(user.id)

    await login(db)

    config = await notion_config(db)
    assert config.target_id == "db-1"
    assert config.storage_status == STATUS_READY


async def test_deleted_database_is_recreated(db, fake):
    user = await login(db)
    service = provisioning_for(db, fake)
    await service.ensure_database(user.id)
    fake.databases_by_id.pop("db-1")

    assert await service.ensure_database(user.id) == STATUS_READY
    assert (await notion_config(db)).target_id == "db-2"


async def test_no_shared_page_needs_permission(db):
    user = await login(db)

    status = await provisioning_for(db, FakeNotion(pages=[])).ensure_database(user.id)

    assert status == STATUS_NEEDS_PERMISSION
    assert (await notion_config(db)).storage_status == STATUS_NEEDS_PERMISSION


@pytest.mark.parametrize(
    "error, expected",
    [
        (api_error(APIErrorCode.Unauthorized, 401), STATUS_NEEDS_PERMISSION),
        (api_error(APIErrorCode.ObjectNotFound, 404), STATUS_NEEDS_PERMISSION),
        (api_error(APIErrorCode.InternalServerError, 500), STATUS_FAILED),
        (RuntimeError("boom"), STATUS_FAILED),
    ],
)
async def test_create_errors_set_status(db, fake, error, expected):
    user = await login(db)
    fake.create_error = error

    status = await provisioning_for(db, fake).ensure_database(user.id)

    assert status == expected
    config = await notion_config(db)
    assert config.storage_status == expected
    assert config.target_id is None


async def test_live_claim_blocks_second_setup(db, fake):
    user = await login(db)
    config = await notion_config(db)
    config.storage_status = STATUS_PROVISIONING
    config.status_changed_at = datetime.now(timezone.utc)
    await db.commit()

    status = await provisioning_for(db, fake).ensure_database(user.id)

    assert status == STATUS_PROVISIONING
    assert fake.create_calls == 0


async def test_setup_without_notion_config_needs_permission(db, fake):
    assert await provisioning_for(db, fake).ensure_database(12345) == STATUS_NEEDS_PERMISSION

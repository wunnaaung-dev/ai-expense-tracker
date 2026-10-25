import logging
from datetime import datetime, timezone
from typing import Any, Callable

from notion_client import APIResponseError, AsyncClient
from notion_client.errors import APIErrorCode
from sqlalchemy.ext.asyncio import AsyncSession

from constants.storage_status import (
    STATUS_FAILED,
    STATUS_NEEDS_PERMISSION,
    STATUS_PROVISIONING,
    STATUS_READY,
)
from core.crypto import decrypt_credentials
from models.storage import UserStorageConfig
from schemas.storage import NotionPage
from services.notion.notion_storage_service import (
    NoSharedNotionPageError,
    create_expense_database,
    find_parent_page_id,
    is_invalid_token_error,
)
from services.storage.errors import StorageNotReadyError
from services.storage.user_storage import (
    claim_provisioning,
    get_notion_config,
    reset_and_set_status,
    set_storage_status,
)

logger = logging.getLogger(__name__)

MAX_LISTED_PAGES = 100

ClientFactory = Callable[[str], AsyncClient]

# The parent page is gone or no longer shared with the integration.
_NO_ACCESS_CODES = {APIErrorCode.ObjectNotFound, APIErrorCode.ValidationError}


def _page_title(page: dict[str, Any]) -> str:
    for prop in page.get("properties", {}).values():
        if prop.get("type") == "title":
            return "".join(part.get("plain_text", "") for part in prop.get("title", []))
    return ""


def _page_icon(page: dict[str, Any]) -> str | None:
    icon = page.get("icon") or {}
    return icon.get("emoji") if icon.get("type") == "emoji" else None


class NotionProvisioningService:
    """Creates the expense database in the user's Notion once they confirm (OAuth login users)."""

    def __init__(self, db: AsyncSession, client_factory: ClientFactory):
        self.db = db
        self._client_factory = client_factory

    async def list_pages(self, user_id: int) -> list[NotionPage]:
        """Pages shared with the integration: where the database can be created."""
        client = self._client(await self._require_config(user_id))
        pages: list[NotionPage] = []
        cursor = None
        while len(pages) < MAX_LISTED_PAGES:
            kwargs = {"start_cursor": cursor} if cursor else {}
            response = await client.search(
                filter={"property": "object", "value": "page"},
                page_size=MAX_LISTED_PAGES,
                **kwargs,
            )
            for page in response.get("results", []):
                if page.get("in_trash") or page.get("archived"):
                    continue
                pages.append(NotionPage(id=str(page["id"]), title=_page_title(page), icon=_page_icon(page)))
            cursor = response.get("next_cursor")
            if not response.get("has_more") or not cursor:
                break
        return pages[:MAX_LISTED_PAGES]

    async def ensure_database(self, user_id: int, parent_page_id: str | None = None) -> str:
        """Make sure the user has a usable expense database. Never raises; returns the resulting status."""
        config = await get_notion_config(self.db, user_id)
        if config is None:
            return STATUS_NEEDS_PERMISSION

        try:
            return await self._ensure(config, parent_page_id)
        except NoSharedNotionPageError as exc:
            logger.warning("Notion setup for user %s needs a shared page: %s", user_id, exc)
            await reset_and_set_status(self.db, config, STATUS_NEEDS_PERMISSION)
        except APIResponseError as exc:
            if is_invalid_token_error(exc) or exc.code in _NO_ACCESS_CODES:
                logger.warning("Notion setup for user %s needs permission: %s", user_id, exc)
                await reset_and_set_status(self.db, config, STATUS_NEEDS_PERMISSION)
            else:
                logger.error("Notion setup failed for user %s: %s", user_id, exc)
                await reset_and_set_status(self.db, config, STATUS_FAILED)
        except Exception:
            logger.exception("Notion setup failed for user %s", user_id)
            await reset_and_set_status(self.db, config, STATUS_FAILED)
        return config.storage_status

    async def _ensure(self, config: UserStorageConfig, parent_page_id: str | None) -> str:
        await self.db.refresh(config)
        client = self._client(config)
        # Double-clicks and repeat confirms reuse the database instead of creating another.
        if await self._mark_ready_if_usable(client, config):
            return config.storage_status

        seen_target_id = config.target_id
        if not await claim_provisioning(self.db, config):
            return STATUS_PROVISIONING
        # Another request may have finished setup just before we claimed.
        if config.target_id != seen_target_id and await self._mark_ready_if_usable(client, config):
            return config.storage_status

        parent_id = parent_page_id or await find_parent_page_id(client)
        database_id = await create_expense_database(client, parent_id)
        if not await self._is_usable(client, database_id):
            raise RuntimeError(f"Created Notion database {database_id} has no data source")
        config.target_id = database_id
        config.connected_at = datetime.now(timezone.utc)
        await set_storage_status(self.db, config, STATUS_READY)
        return config.storage_status

    async def _mark_ready_if_usable(self, client: AsyncClient, config: UserStorageConfig) -> bool:
        if not config.target_id or not await self._is_usable(client, config.target_id):
            return False
        await set_storage_status(self.db, config, STATUS_READY)
        return True

    @staticmethod
    async def _is_usable(client: AsyncClient, database_id: str) -> bool:
        try:
            db = await client.databases.retrieve(database_id=database_id)
        except APIResponseError as exc:
            if exc.code == APIErrorCode.ObjectNotFound:
                return False
            raise
        if db.get("in_trash") or db.get("archived"):
            return False
        return bool(db.get("data_sources"))

    async def _require_config(self, user_id: int) -> UserStorageConfig:
        config = await get_notion_config(self.db, user_id)
        if config is None:
            raise StorageNotReadyError(STATUS_NEEDS_PERMISSION)
        return config

    def _client(self, config: UserStorageConfig) -> AsyncClient:
        token = decrypt_credentials(config.encrypted_credentials)["access_token"]
        return self._client_factory(token)

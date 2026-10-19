from datetime import datetime, timezone

from notion_client import APIResponseError, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from constants.storage_status import STATUS_READY
from core.crypto import encrypt_credentials
from models.storage import UserStorageConfig
from schemas.storage import StorageStatusResponse
from services.notion.notion_storage_service import (
    NoSharedNotionPageError,
    NotionStorageService,
    create_expense_database,
    find_parent_page_id,
    is_invalid_token_error,
)
from services.storage.user_storage import NOTION_PROVIDER, get_notion_config


class InvalidNotionTokenError(Exception):
    """Raised when the Notion integration token is invalid or restricted."""


class NotionSetupError(Exception):
    """Raised when a Notion database cannot be created or verified."""


class StorageConfigService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_status(self, user_id: int) -> StorageStatusResponse:
        config = await get_notion_config(self.db, user_id)
        if not config or not config.target_id:
            return StorageStatusResponse(connected=False)
        return StorageStatusResponse(
            connected=True,
            provider=config.provider,
            target_id=config.target_id,
            connected_at=config.connected_at,
            status=config.storage_status,
        )

    async def connect_notion(self, user_id: int, token: str) -> StorageStatusResponse:
        config = await get_notion_config(self.db, user_id)
        database_id = await self._resolve_database_id(token, config)
        encrypted = encrypt_credentials({"access_token": token})

        now = datetime.now(timezone.utc)
        if config:
            config.encrypted_credentials = encrypted
            config.target_id = database_id
            config.connected_at = now
        else:
            config = UserStorageConfig(
                user_id=user_id,
                provider=NOTION_PROVIDER,
                encrypted_credentials=encrypted,
                target_id=database_id,
            )
            self.db.add(config)
        config.storage_status = STATUS_READY
        config.status_changed_at = now

        await self.db.commit()
        await self.db.refresh(config)
        return StorageStatusResponse(
            connected=True,
            provider=config.provider,
            target_id=config.target_id,
            connected_at=config.connected_at,
            status=config.storage_status,
        )

    async def disconnect_notion(self, user_id: int) -> None:
        config = await get_notion_config(self.db, user_id)
        if config:
            await self.db.delete(config)
            await self.db.commit()

    async def _resolve_database_id(
        self,
        token: str,
        config: UserStorageConfig | None,
    ) -> str:
        if config and config.target_id:
            try:
                service = NotionStorageService(token=token, database_id=config.target_id)
                await service.verify_access()
                return service.database_id
            except APIResponseError as exc:
                if is_invalid_token_error(exc):
                    raise InvalidNotionTokenError("Invalid Notion token") from exc
            except Exception:
                pass

        return await self._create_and_verify_database(token)

    async def _create_and_verify_database(self, token: str) -> str:
        client = AsyncClient(auth=token)
        try:
            parent_page_id = await find_parent_page_id(client)
            database_id = await create_expense_database(client, parent_page_id)
            service = NotionStorageService(token=token, database_id=database_id)
            await service.verify_access()
            return service.database_id
        except NoSharedNotionPageError:
            raise
        except APIResponseError as exc:
            if is_invalid_token_error(exc):
                raise InvalidNotionTokenError("Invalid Notion token") from exc
            raise NotionSetupError(
                "Could not create Notion database. Share a page with the integration."
            ) from exc
        except Exception as exc:
            raise NotionSetupError(
                "Could not create Notion database. Share a page with the integration."
            ) from exc

import logging
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

from sqlalchemy import or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from constants.storage_providers import GOOGLE_SHEETS_PROVIDER
from constants.storage_status import (
    STATUS_FAILED,
    STATUS_NEEDS_PERMISSION,
    STATUS_NONE,
    STATUS_PROVISIONING,
    STATUS_READY,
    STATUS_SCHEMA_BROKEN,
)
from core.crypto import decrypt_credentials
from models.storage import UserStorageConfig
from schemas.storage import StorageStatusResponse
from services.google.credentials import has_sheets_scopes
from services.google.errors import GoogleApiError, GooglePermissionError
from services.google.google_api import GoogleApi
from services.google_sheets.schema import (
    APP_PROPERTY_KEY,
    APP_PROPERTY_VALUE,
    build_setup_requests,
    build_spreadsheet_body,
)
from services.storage.user_storage import get_storage_config, set_storage_status

logger = logging.getLogger(__name__)

# A crashed request can't hold the claim forever: after this, another request may take over.
PROVISIONING_LEASE = timedelta(minutes=2)

ApiFactory = Callable[[UserStorageConfig], Awaitable[GoogleApi]]


class SheetsProvisioningService:
    def __init__(self, db: AsyncSession, api_factory: ApiFactory):
        self.db = db
        self._api_factory = api_factory

    async def get_status(self, user_id: int) -> StorageStatusResponse:
        config = await get_storage_config(self.db, user_id, GOOGLE_SHEETS_PROVIDER)
        if config is None:
            return StorageStatusResponse(connected=False, provider=GOOGLE_SHEETS_PROVIDER, status=STATUS_NONE)
        return StorageStatusResponse(
            connected=config.storage_status == STATUS_READY,
            provider=config.provider,
            target_id=config.target_id,
            connected_at=config.connected_at,
            status=config.storage_status,
        )

    async def ensure_spreadsheet(self, user_id: int) -> str:
        """Make sure the user has a usable spreadsheet. Never raises; returns the resulting status."""
        config = await get_storage_config(self.db, user_id, GOOGLE_SHEETS_PROVIDER)
        if config is None:
            # No Google grant on file (e.g. a Notion-only user).
            return STATUS_NEEDS_PERMISSION

        try:
            return await self._ensure(config)
        except GooglePermissionError:
            await self._reset_and_set(config, STATUS_NEEDS_PERMISSION)
        except Exception:
            logger.exception("Spreadsheet provisioning failed for user %s", user_id)
            await self._reset_and_set(config, STATUS_FAILED)
        return config.storage_status

    async def _ensure(self, config: UserStorageConfig) -> str:
        await self.db.refresh(config)
        credentials = decrypt_credentials(config.encrypted_credentials)
        if not has_sheets_scopes(credentials.get("scope")):
            await set_storage_status(self.db, config, STATUS_NEEDS_PERMISSION)
            return config.storage_status

        api = await self._api_factory(config)
        if await self._mark_ready_if_usable(api, config):
            return config.storage_status

        seen_target_id = config.target_id
        if not await self._claim(config):
            return STATUS_PROVISIONING
        # Another request may have finished provisioning just before we claimed.
        if config.target_id != seen_target_id and await self._mark_ready_if_usable(api, config):
            return config.storage_status

        spreadsheet_id = await self._find_existing(api) or await self._create(api)
        config.target_id = spreadsheet_id
        config.connected_at = datetime.now(timezone.utc)
        await set_storage_status(self.db, config, STATUS_READY)
        return config.storage_status

    async def _mark_ready_if_usable(self, api: GoogleApi, config: UserStorageConfig) -> bool:
        if not config.target_id or not await self._is_usable(api, config.target_id):
            return False
        # A broken schema stays flagged until repair; the file itself is fine.
        if config.storage_status != STATUS_SCHEMA_BROKEN:
            await set_storage_status(self.db, config, STATUS_READY)
        return True

    async def _claim(self, config: UserStorageConfig) -> bool:
        """Atomically move to `provisioning` unless a live claim exists (compare-and-set)."""
        now = datetime.now(timezone.utc)
        result = await self.db.execute(
            update(UserStorageConfig)
            .where(
                UserStorageConfig.id == config.id,
                or_(
                    UserStorageConfig.storage_status != STATUS_PROVISIONING,
                    UserStorageConfig.status_changed_at.is_(None),
                    UserStorageConfig.status_changed_at < now - PROVISIONING_LEASE,
                ),
            )
            .values(storage_status=STATUS_PROVISIONING, status_changed_at=now)
            .returning(UserStorageConfig.id)
            .execution_options(synchronize_session=False)
        )
        claimed = result.scalar_one_or_none() is not None
        await self.db.commit()
        await self.db.refresh(config)
        return claimed

    @staticmethod
    async def _is_usable(api: GoogleApi, spreadsheet_id: str) -> bool:
        # The Sheets API still serves trashed files, so ask Drive.
        try:
            file = await api.get_file(spreadsheet_id)
        except GoogleApiError as exc:
            if exc.status == 404:
                return False
            raise
        return not file.get("trashed", False)

    @staticmethod
    async def _find_existing(api: GoogleApi) -> str | None:
        files = await api.find_app_files(APP_PROPERTY_KEY, APP_PROPERTY_VALUE)
        return files[0]["id"] if files else None

    @staticmethod
    async def _create(api: GoogleApi) -> str:
        created = await api.create_spreadsheet(build_spreadsheet_body())
        spreadsheet_id = created["spreadsheetId"]
        sheet_id = created["sheets"][0]["properties"]["sheetId"]
        # Tag first so a retry finds this file even if the setup below fails.
        await api.set_app_properties(spreadsheet_id, {APP_PROPERTY_KEY: APP_PROPERTY_VALUE})
        await api.batch_update(spreadsheet_id, build_setup_requests(sheet_id))
        return spreadsheet_id

    async def _reset_and_set(self, config: UserStorageConfig, status: str) -> None:
        try:
            await set_storage_status(self.db, config, status)
        except Exception:
            # Only a DB error leaves the session unusable; roll back just then,
            # since rollback expires every loaded object (including the user).
            await self.db.rollback()
            await self.db.refresh(config)
            await set_storage_status(self.db, config, status)

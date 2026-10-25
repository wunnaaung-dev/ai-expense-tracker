from fastapi import Depends
from notion_client import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from constants.storage_providers import GOOGLE_SHEETS_PROVIDER, NOTION_PROVIDER
from constants.storage_status import STATUS_NEEDS_PERMISSION, STATUS_NONE, STATUS_READY
from core.crypto import decrypt_credentials
from core.db import get_db
from core.security import get_current_user
from models.storage import UserStorageConfig
from models.users import User
from services.auth.auth_service import AuthService
from services.dedup.hash_checker import DuplicateChecker
from services.entries.entry_defaults import EntryDefaults
from services.entries.entry_service import EntryService
from services.extraction.abstract_audio_extraction_strategy import AudioExtractionStrategy
from services.extraction.abstract_image_extraction_strategy import ImageExtractionStrategy
from services.extraction.abstract_text_extraction_strategy import TextExtractionStrategy
from services.extraction.gemini_extractor import GeminiExtractor
from services.google.credentials import get_google_access_token
from services.google.errors import GooglePermissionError
from services.google.google_api import GoogleApi
from services.google_sheets.google_sheets_storage import GoogleSheetsStorage
from services.google_sheets.provisioning import SheetsProvisioningService
from services.insights.insights_service import InsightsService
from services.notion.notion_storage_service import NotionStorageService
from services.notion.provisioning import NotionProvisioningService
from services.settings.settings_service import SettingsService
from services.storage import record_cache
from services.storage.abstract_storage_strategy import StorageStrategy
from services.storage.errors import StorageNotReadyError
from services.storage.storage_config_service import StorageConfigService
from services.storage.user_storage import get_storage_config, set_storage_status


def get_sheets_provisioning_service(
    db: AsyncSession = Depends(get_db),
) -> SheetsProvisioningService:
    async def api_factory(config: UserStorageConfig) -> GoogleApi:
        return GoogleApi(await get_google_access_token(db, config))

    return SheetsProvisioningService(db, api_factory)


async def build_sheets_storage(
    db: AsyncSession,
    config: UserStorageConfig,
) -> GoogleSheetsStorage:
    try:
        token = await get_google_access_token(db, config)
    except GooglePermissionError:
        await set_storage_status(db, config, STATUS_NEEDS_PERMISSION)
        raise

    async def on_status(status: str) -> None:
        await set_storage_status(db, config, status)

    return GoogleSheetsStorage(
        api=GoogleApi(token),
        spreadsheet_id=config.target_id,
        user_id=config.user_id,
        on_status=on_status,
    )


async def get_sheets_storage(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> GoogleSheetsStorage:
    """The user's spreadsheet regardless of schema state (used by repair)."""
    config = await get_storage_config(db, current_user.id, GOOGLE_SHEETS_PROVIDER)
    if config is None or not config.target_id:
        raise StorageNotReadyError(config.storage_status if config else STATUS_NONE)
    return await build_sheets_storage(db, config)


def get_auth_service(
    db: AsyncSession = Depends(get_db),
    provisioning: SheetsProvisioningService = Depends(get_sheets_provisioning_service),
) -> AuthService:
    return AuthService(db, provisioning)


def get_notion_provisioning_service(
    db: AsyncSession = Depends(get_db),
) -> NotionProvisioningService:
    return NotionProvisioningService(db, lambda token: AsyncClient(auth=token))


def get_storage_config_service(
    db: AsyncSession = Depends(get_db),
) -> StorageConfigService:
    return StorageConfigService(db)


async def get_storage_service(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StorageStrategy:
    """The backend for the user's default provider, only once it's ready."""
    provider = current_user.default_storage_provider
    config = await get_storage_config(db, current_user.id, provider)
    storage_status = config.storage_status if config else STATUS_NONE
    if config is None or storage_status != STATUS_READY or not config.target_id:
        raise StorageNotReadyError(storage_status)

    if provider == NOTION_PROVIDER:
        token = decrypt_credentials(config.encrypted_credentials)["access_token"]
        return NotionStorageService(token=token, database_id=config.target_id)
    if provider == GOOGLE_SHEETS_PROVIDER:
        return await build_sheets_storage(db, config)
    raise StorageNotReadyError(STATUS_NONE)


async def get_insights_service(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    storage: StorageStrategy = Depends(get_storage_service),
) -> InsightsService:
    # get_storage_service has checked the config is ready; its target tags the cached records.
    provider = current_user.default_storage_provider
    config = await get_storage_config(db, current_user.id, provider)
    return InsightsService(storage, current_user, (provider, config.target_id))


def get_duplicate_checker(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DuplicateChecker:
    return DuplicateChecker(db, current_user.id)

def get_entry_defaults(
    current_user: User = Depends(get_current_user),
) -> EntryDefaults:
    return EntryDefaults(current_user)

def get_settings_service(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SettingsService:
    return SettingsService(db, current_user)

def get_image_extraction_service() -> ImageExtractionStrategy:
    return GeminiExtractor()

def get_text_extraction_service() -> TextExtractionStrategy:
    return GeminiExtractor()

def get_audio_extraction_service() -> AudioExtractionStrategy:
    return GeminiExtractor()

def get_entry_service(
    image_extraction: ImageExtractionStrategy = Depends(get_image_extraction_service),
    text_extraction: TextExtractionStrategy = Depends(get_text_extraction_service),
    audio_extraction: AudioExtractionStrategy = Depends(get_audio_extraction_service),
    dedup: DuplicateChecker = Depends(get_duplicate_checker),
    defaults: EntryDefaults = Depends(get_entry_defaults),
    current_user: User = Depends(get_current_user),
) -> EntryService:
    return EntryService(
        image_extraction=image_extraction,
        text_extraction=text_extraction,
        audio_extraction=audio_extraction,
        dedup=dedup,
        defaults=defaults,
        on_saved=lambda: record_cache.invalidate(current_user.id),
    )

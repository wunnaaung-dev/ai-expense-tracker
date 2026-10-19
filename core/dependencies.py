from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.crypto import decrypt_credentials
from core.db import get_db
from core.security import get_current_user
from models.users import User
from services.dedup.hash_checker import DuplicateChecker
from services.extraction.abstract_audio_extraction_strategy import AudioExtractionStrategy
from services.extraction.abstract_image_extraction_strategy import ImageExtractionStrategy
from services.extraction.abstract_text_extraction_strategy import TextExtractionStrategy
from services.storage.abstract_storage_strategy import StorageStrategy
from services.notion.notion_storage_service import NotionStorageService
from services.storage.backup_storage import BackupStorage
from services.storage.resilient_storage_service import ResilientStorageService
from services.storage.user_storage import get_notion_config
from services.extraction.gemini_extractor import GeminiExtractor
from services.auth.auth_service import AuthService
from services.storage.storage_config_service import StorageConfigService
from services.upload.expense_upload_orchestrator import ExpenseUploadOrchestrator
from services.upload.text_expense_orchestrator import TextExpenseOrchestrator
from services.upload.voice_expense_orchestrator import VoiceExpenseOrchestrator


def get_auth_service(db: AsyncSession = Depends(get_db)) -> AuthService:
    return AuthService(db)


def get_storage_config_service(
    db: AsyncSession = Depends(get_db),
) -> StorageConfigService:
    return StorageConfigService(db)


async def get_storage_service(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ResilientStorageService:
    config = await get_notion_config(db, current_user.id)
    if not config or not config.target_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Notion is not connected",
        )

    token = decrypt_credentials(config.encrypted_credentials)["access_token"]
    notion_service = NotionStorageService(
        token=token,
        database_id=config.target_id,
    )
    backup = BackupStorage(user_id=current_user.id)
    return ResilientStorageService(primary=notion_service, backup=backup)

def get_duplicate_checker(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DuplicateChecker:
    return DuplicateChecker(db, current_user.id)

def get_image_extraction_service() -> ImageExtractionStrategy:
    return GeminiExtractor()

def get_text_extraction_service() -> TextExtractionStrategy:
    return GeminiExtractor()

def get_audio_extraction_service() -> AudioExtractionStrategy:
    return GeminiExtractor()

def get_expense_upload_orchestrator(
    image_extraction: ImageExtractionStrategy = Depends(get_image_extraction_service),
    storage: StorageStrategy = Depends(get_storage_service),
    dedup: DuplicateChecker = Depends(get_duplicate_checker),
) -> ExpenseUploadOrchestrator:
    return ExpenseUploadOrchestrator(image_extraction=image_extraction, storage=storage, dedup=dedup)

def get_text_expense_orchestrator(
    text_extraction: TextExtractionStrategy = Depends(get_text_extraction_service),
    storage: StorageStrategy = Depends(get_storage_service),
) -> TextExpenseOrchestrator:
    return TextExpenseOrchestrator(extraction=text_extraction, storage=storage)

def get_voice_expense_orchestrator(
    extraction: AudioExtractionStrategy = Depends(get_audio_extraction_service),
    storage: StorageStrategy = Depends(get_storage_service),
) -> VoiceExpenseOrchestrator:
    return VoiceExpenseOrchestrator(extraction=extraction, storage=storage)

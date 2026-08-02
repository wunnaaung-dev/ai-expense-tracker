from fastapi import Depends

from core.config import settings
from services.dedup.hash_checker import DuplicateChecker
from services.extraction.abstract_image_extraction_strategy import ImageExtractionStrategy
from services.extraction.abstract_text_extraction_strategy import TextExtractionStrategy
from services.storage.abstract_storage_strategy import StorageStrategy
from services.notion.notion_storage_service import NotionStorageService
from services.storage.backup_storage import BackupStorage
from services.storage.resilient_storage_service import ResilientStorageService
from services.extraction.gemini_extractor import GeminiExtractor
from services.upload.expense_upload_orchestrator import ExpenseUploadOrchestrator
from services.upload.text_expense_orchestrator import TextExpenseOrchestrator

def get_storage_service() -> StorageStrategy:
    notion_service = NotionStorageService(
        token=settings.notion_token,
        database_id=settings.notion_database_id,
    )
    backup = BackupStorage()
    return ResilientStorageService(primary=notion_service, backup=backup)

def get_duplicate_checker() -> DuplicateChecker:
    return DuplicateChecker()

def get_image_extraction_service() -> ImageExtractionStrategy:
    return GeminiExtractor()

def get_text_extraction_service() -> TextExtractionStrategy:
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
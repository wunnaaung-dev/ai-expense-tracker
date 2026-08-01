from core.config import settings
from services.dedup.hash_checker import DuplicateChecker
from services.notion.abstract_storage_strategy import StorageStrategy
from services.notion.abstract_storage_strategy import StorageStrategy
from services.notion.notion_storage_service import NotionStorageService
from services.storage.backup_storage import BackupStorage
from services.storage.resilient_storage_service import ResilientStorageService

def get_storage_service() -> StorageStrategy:
    notion_service = NotionStorageService(
        token=settings.notion_token,
        database_id=settings.notion_database_id,
    )
    backup = BackupStorage()
    return ResilientStorageService(primary=notion_service, backup=backup)

def get_duplicate_checker() -> DuplicateChecker:
    return DuplicateChecker()


from core.config import settings
from services.notion.abstract_storage_strategy import StorageStrategy
from services.notion.abstract_storage_strategy import StorageStrategy
from services.notion.notion_storage_service import NotionStorageService

def get_storage_service() -> StorageStrategy:
    return NotionStorageService(
        token=settings.notion_token,
        database_id=settings.notion_database_id,
    )
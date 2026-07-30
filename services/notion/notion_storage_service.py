import asyncio

from notion_client import AsyncClient
from typing import List, Callable, Dict, Any

from services.notion.abstract_storage_strategy import StorageStrategy
from schemas.expense import BatchResult, ExpenseData
from services.notion.property_config import build_properties

class NotionStorageService(StorageStrategy):
    def __init__(
        self,
        token: str,
        database_id: str,
        max_concurrent: int = 3,
        property_builder: Callable[[ExpenseData], Dict[str, Any]] = build_properties,
    ):
        self.client = AsyncClient(auth=token)
        self.database_id = database_id
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self._build_properties = property_builder

    async def _insert_one(self, item: ExpenseData) -> BatchResult:
        async with self.semaphore:
            try:
                await self.client.pages.create(
                    parent={"database_id": self.database_id},
                    properties=self._build_properties(item)
                )
                return BatchResult(filename="", status="success", data=item)
            except Exception as e:
                return BatchResult(filename="", status="failed", data=item, error=str(e))

    async def insert_many(self, items: List[ExpenseData]) -> List[BatchResult]:
        tasks = [self._insert_one(item) for item in items]
        return await asyncio.gather(*tasks)
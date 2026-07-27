import asyncio
from notion_client import AsyncClient
from typing import List

from services.notion.storage_strategy import StorageStrategy
from schemas.expense import BatchResult, ExpenseData


class NotionStorageService(StorageStrategy):
    def __init__(self, token: str, database_id: str, max_concurrent: int = 3):
        self.client = AsyncClient(auth=token)
        self.database_id = database_id
        self.semaphore = asyncio.Semaphore(max_concurrent)

    async def _insert_one(self, item: ExpenseData) -> BatchResult:
        async with self.semaphore:
            try:
                await self.client.pages.create(
                    parent={"database_id": self.database_id},
                    properties={
                        "Name": {"title": [{"text": {"content": item.description}}]},
                        "Amount": {"number": item.amount},
                        "Date": {"date": {"start": item.date.isoformat()}},
                    },
                )
                return BatchResult(filename="", status="success", data=item)
            except Exception as e:
                return BatchResult(filename="", status="failed", error=str(e))

    async def insert_many(self, items: List[ExpenseData]) -> List[BatchResult]:
        tasks = [self._insert_one(item) for item in items]
        return await asyncio.gather(*tasks)
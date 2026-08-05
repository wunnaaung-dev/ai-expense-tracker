import asyncio

from notion_client import AsyncClient
from typing import List, Callable, Dict, Any, Optional, Tuple

from services.storage.abstract_storage_strategy import StorageStrategy
from schemas.expense import BatchResult, ExpenseData
from services.notion.property_config import build_properties, parse_notion_page


def _format_database_id(database_id: str) -> str:
    raw = database_id.replace("-", "")
    if len(raw) != 32:
        return database_id
    return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"


class NotionStorageService(StorageStrategy):
    def __init__(
        self,
        token: str,
        database_id: str,
        max_concurrent: int = 3,
        property_builder: Callable[[ExpenseData], Dict[str, Any]] = build_properties,
    ):
        self.client = AsyncClient(auth=token)
        self.database_id = _format_database_id(database_id)
        self._data_source_id: Optional[str] = None
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self._build_properties = property_builder

    async def _ensure_data_source_id(self) -> str:
        if self._data_source_id is None:
            db = await self.client.databases.retrieve(database_id=self.database_id)
            data_sources = db.get("data_sources", [])
            if not data_sources:
                raise RuntimeError(
                    f"No data_sources found for database {self.database_id}. "
                    "Make sure the database is shared with the integration."
                )
            self._data_source_id = str(data_sources[0]["id"])
        return self._data_source_id

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

    async def get_all(self, page_size: int, start_cursor: Optional[str] = None) -> Tuple[List[ExpenseData], Optional[str]]:
        async with self.semaphore:
            data_source_id = await self._ensure_data_source_id()

            body: Dict[str, Any] = {"page_size": page_size}
            if start_cursor:
                body["start_cursor"] = start_cursor

            response = await self.client.data_sources.query(
                data_source_id=data_source_id,
                **body,
            )

            expenses = [parse_notion_page(page) for page in response.get("results", [])]
            next_cursor = response.get("next_cursor")

            return expenses, next_cursor
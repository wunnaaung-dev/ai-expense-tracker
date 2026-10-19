import asyncio

from notion_client import APIResponseError, AsyncClient
from notion_client.errors import APIErrorCode
from typing import List, Callable, Dict, Any, Optional, Tuple

from services.storage.abstract_storage_strategy import StorageStrategy
from services.storage.errors import StorageWriteError
from schemas.expense import BatchResult, ExpenseData
from services.notion.property_config import (
    EXPENSE_DB_SCHEMA,
    build_properties,
    parse_notion_page,
)


class NoSharedNotionPageError(Exception):
    """Raised when the integration has no shared parent page to create a database under."""


def is_invalid_token_error(exc: APIResponseError) -> bool:
    return exc.code in {APIErrorCode.Unauthorized, APIErrorCode.RestrictedResource}


def _format_database_id(database_id: str) -> str:
    raw = database_id.replace("-", "")
    if len(raw) != 32:
        return database_id
    return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"


async def find_parent_page_id(client: AsyncClient) -> str:
    response = await client.search(
        filter={"property": "object", "value": "page"},
        page_size=1,
    )
    results = response.get("results", [])
    if not results:
        raise NoSharedNotionPageError(
            "No shared Notion page found. Share a page with the integration."
        )
    return str(results[0]["id"])


async def create_expense_database(client: AsyncClient, parent_page_id: str) -> str:
    db = await client.databases.create(
        parent={"type": "page_id", "page_id": parent_page_id},
        title=[{"type": "text", "text": {"content": "Expenses"}}],
        initial_data_source={"properties": EXPENSE_DB_SCHEMA},
    )
    return str(db["id"])


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

    async def verify_access(self) -> None:
        await self._ensure_data_source_id()

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
        results = await asyncio.gather(*tasks)
        # Partial failures stay per-item; a write where nothing landed is a storage failure.
        if results and all(r.status == "failed" for r in results):
            raise StorageWriteError(results[0].error or "Notion write failed")
        return results

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
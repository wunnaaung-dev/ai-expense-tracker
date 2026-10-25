import asyncio

from notion_client import APIResponseError, AsyncClient
from notion_client.errors import APIErrorCode
from typing import List, Callable, Dict, Any, Optional

from services.storage.abstract_storage_strategy import StorageStrategy
from services.storage.errors import StorageWriteError
from services.storage.filters import matches
from constants.currencies import DEFAULT_CURRENCY
from schemas.expense import BatchResult, Currency, EntryFilter, ExpenseData, ExpenseType
from services.notion.property_config import (
    BACKFILL_PROPERTIES,
    CURRENCY_PROPERTY,
    TYPE_PROPERTY,
    EXPENSE_DB_SCHEMA,
    build_properties,
    parse_notion_page,
)


NOTION_MAX_PAGE_SIZE = 100


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
        self._property_names: Optional[set[str]] = None
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

    async def _properties(self) -> set[str]:
        if self._property_names is None:
            data_source_id = await self._ensure_data_source_id()
            data_source = await self.client.data_sources.retrieve(data_source_id=data_source_id)
            self._property_names = set(data_source.get("properties", {}))
        return self._property_names

    async def _currency_property_exists(self) -> bool:
        return CURRENCY_PROPERTY in await self._properties()

    async def _type_property_exists(self) -> bool:
        return TYPE_PROPERTY in await self._properties()

    async def _ensure_properties(self) -> None:
        """Databases created before Currency/Type existed get them on their next write."""
        try:
            missing = [name for name in BACKFILL_PROPERTIES if name not in await self._properties()]
            if not missing:
                return
            await self.client.data_sources.update(
                data_source_id=await self._ensure_data_source_id(),
                properties={name: EXPENSE_DB_SCHEMA[name] for name in missing},
            )
        except APIResponseError as exc:
            raise StorageWriteError(f"Could not add the {'/'.join(BACKFILL_PROPERTIES)} properties: {exc}") from exc
        self._property_names.update(missing)

    @staticmethod
    def _currency_filter(currency: Currency) -> Dict[str, Any]:
        equals = {"property": CURRENCY_PROPERTY, "select": {"equals": currency}}
        if currency != DEFAULT_CURRENCY:
            return equals
        # Pages without a currency count as THB.
        return {"or": [equals, {"property": CURRENCY_PROPERTY, "select": {"is_empty": True}}]}

    @staticmethod
    def _type_filter(entry_type: ExpenseType) -> Dict[str, Any]:
        equals = {"property": TYPE_PROPERTY, "select": {"equals": entry_type}}
        if entry_type != "expense":
            return equals
        # Pages without a type are expenses.
        return {"or": [equals, {"property": TYPE_PROPERTY, "select": {"is_empty": True}}]}

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
        await self._ensure_properties()
        tasks = [self._insert_one(item) for item in items]
        results = await asyncio.gather(*tasks)
        # Partial failures stay per-item; a write where nothing landed is a storage failure.
        if results and all(r.status == "failed" for r in results):
            raise StorageWriteError(results[0].error or "Notion write failed")
        return results

    async def record_url(self, record: ExpenseData) -> Optional[str]:
        # Pages are created without returning their URL; the database is the useful link.
        return f"https://www.notion.so/{self.database_id.replace('-', '')}"

    async def get_all(self, filters: EntryFilter) -> List[ExpenseData]:
        async with self.semaphore:
            data_source_id = await self._ensure_data_source_id()

            # Narrow what Notion sends back; `matches` below still decides (search, case-insensitive category).
            conditions: List[Dict[str, Any]] = []
            if filters.currency:
                if not await self._currency_property_exists():
                    # No Currency property yet: every page is THB.
                    if filters.currency != DEFAULT_CURRENCY:
                        return []
                else:
                    conditions.append(self._currency_filter(filters.currency))
            if filters.entry_type:
                if not await self._type_property_exists():
                    # No Type property yet: every page is an expense.
                    if filters.entry_type != "expense":
                        return []
                else:
                    conditions.append(self._type_filter(filters.entry_type))
            if filters.date_from:
                conditions.append({"property": "Date", "date": {"on_or_after": filters.date_from.isoformat()}})
            if filters.date_to:
                conditions.append({"property": "Date", "date": {"on_or_before": filters.date_to.isoformat()}})

            body: Dict[str, Any] = {
                "page_size": NOTION_MAX_PAGE_SIZE,
                "sorts": [{"property": "Date", "direction": "descending"}],
            }
            if len(conditions) == 1:
                body["filter"] = conditions[0]
            elif conditions:
                body["filter"] = {"and": conditions}

            # Totals need every match, so follow the cursor to the end.
            records: List[ExpenseData] = []
            cursor: Optional[str] = None
            while True:
                response = await self.client.data_sources.query(
                    data_source_id=data_source_id,
                    **body,
                    **({"start_cursor": cursor} if cursor else {}),
                )
                records.extend(parse_notion_page(page) for page in response.get("results", []))
                cursor = response.get("next_cursor")
                if not cursor:
                    break

            records = [record for record in records if matches(record, filters)]
            records.sort(key=lambda record: record.date, reverse=True)
            return records
import logging
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Awaitable, Callable, List, Optional, Tuple

from cachetools import TTLCache

from constants.storage_status import (
    STATUS_MISSING,
    STATUS_NEEDS_PERMISSION,
    STATUS_READY,
    STATUS_SCHEMA_BROKEN,
)
from schemas.expense import BatchResult, ExpenseData
from services.google.errors import GoogleApiError, GooglePermissionError
from services.google.google_api import GoogleApi
from services.google_sheets.errors import (
    RecordNotFoundError,
    SheetSchemaBrokenError,
    SpreadsheetMissingError,
)
from services.google_sheets.schema import (
    FIELD_BY_NAME,
    FIELDS,
    META_FIELD_KEY,
    META_SHEET_KEY,
    META_SHEET_VALUE,
    REQUIRED_FIELDS,
    SOURCE_VALUES,
    TAB_TITLE,
    TYPE_VALUES,
    build_tab_requests,
    column_requests,
    header_cell,
)
from services.storage.abstract_storage_strategy import StorageStrategy
from services.storage.errors import StorageWriteError

logger = logging.getLogger(__name__)

# Google Sheets counts dates as days since 1899-12-30.
SHEETS_EPOCH = date(1899, 12, 30)

StatusCallback = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class ColumnMap:
    sheet_id: int
    columns: dict[str, int]  # field name -> 0-based column index


# Per-process, ephemeral. Reads use it; writes always re-resolve so a column the
# user just moved can never receive another field's value.
_column_cache: TTLCache = TTLCache(maxsize=1024, ttl=300)


async def _noop_status(status: str) -> None:
    return None


def _to_raw(field: str, value: Any) -> Any:
    """Python value -> raw cell value (dates as serial numbers)."""
    if value is None:
        return ""
    if field == "date":
        if isinstance(value, str):
            value = date.fromisoformat(value)
        return (value - SHEETS_EPOCH).days
    if field == "amount":
        return float(value)
    return str(value)


def _to_cell(field: str, value: Any) -> dict:
    raw = _to_raw(field, value)
    if raw == "":
        return {}
    if isinstance(raw, (int, float)):
        return {"userEnteredValue": {"numberValue": raw}}
    return {"userEnteredValue": {"stringValue": raw}}


def _parse_date(value: Any) -> date:
    if isinstance(value, (int, float)):
        return SHEETS_EPOCH + timedelta(days=int(value))
    return date.fromisoformat(str(value).strip())


def _parse_amount(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return float(str(value).replace(",", "").strip())


def _parse_row(row: list, columns: dict[str, int]) -> ExpenseData | None:
    def get(name: str) -> Any:
        index = columns.get(name)
        if index is None or index >= len(row) or row[index] == "":
            return None
        return row[index]

    raw_date, raw_amount = get("date"), get("amount")
    if raw_date is None or raw_amount is None:
        return None
    try:
        expense_date = _parse_date(raw_date)
        amount = _parse_amount(raw_amount)
    except (TypeError, ValueError):
        logger.warning("Skipping row with unreadable date/amount: %r", row)
        return None

    record_type = get("type")
    source = get("source")
    record_id = get("id")
    payment_channel = get("payment_channel")
    return ExpenseData(
        date=expense_date,
        amount=amount,
        description=str(get("description") or ""),
        category=str(get("category") or "General"),
        id=str(record_id) if record_id is not None else None,
        type=record_type if record_type in TYPE_VALUES else "expense",
        payment_channel=str(payment_channel) if payment_channel is not None else None,
        source=source if source in SOURCE_VALUES else None,
    )


def _sheet_ids(metadata: list[dict]) -> list[int]:
    return sorted(
        meta["location"]["sheetId"]
        for meta in metadata
        if meta["metadataKey"] == META_SHEET_KEY
        and meta.get("metadataValue") == META_SHEET_VALUE
        and "sheetId" in meta.get("location", {})
    )


def _columns_on(metadata: list[dict], sheet_id: int) -> dict[str, int]:
    columns: dict[str, int] = {}
    for meta in metadata:
        if meta["metadataKey"] != META_FIELD_KEY:
            continue
        dimension_range = meta.get("location", {}).get("dimensionRange")
        name = meta.get("metadataValue")
        if (
            not dimension_range
            or dimension_range.get("sheetId") != sheet_id
            or dimension_range.get("dimension") != "COLUMNS"
            or name not in FIELD_BY_NAME
        ):
            continue
        index = dimension_range["startIndex"]
        if name in columns and columns[name] != index:
            logger.warning("Field %s is tagged on more than one column; using the leftmost", name)
        columns[name] = min(index, columns.get(name, index))
    return columns


class GoogleSheetsStorage(StorageStrategy):
    """Expense storage in the user's spreadsheet.

    Columns are located only through Developer Metadata (resolve_columns), so
    users may rename headers, reorder or add columns, sort rows and rename the tab.
    Records are addressed by the UUID in the `id` column, never by row number.
    """

    def __init__(
        self,
        api: GoogleApi,
        spreadsheet_id: str,
        user_id: int,
        on_status: StatusCallback = _noop_status,
    ):
        self.api = api
        self.spreadsheet_id = spreadsheet_id
        self.user_id = user_id
        self._on_status = on_status

    # --- column resolution ------------------------------------------------------

    @property
    def _cache_key(self) -> tuple[int, str]:
        return (self.user_id, self.spreadsheet_id)

    def invalidate_columns(self) -> None:
        _column_cache.pop(self._cache_key, None)

    async def resolve_columns(self, fresh: bool = False) -> ColumnMap:
        if not fresh:
            cached = _column_cache.get(self._cache_key)
            if cached is not None:
                return cached

        # The Sheets API still serves trashed files, so check Drive before trusting it.
        file = await self._call(self.api.get_file(self.spreadsheet_id))
        if file.get("trashed"):
            await self._missing()

        metadata = await self._call(
            self.api.search_developer_metadata(self.spreadsheet_id, [META_SHEET_KEY, META_FIELD_KEY])
        )
        sheet_ids = _sheet_ids(metadata)
        if not sheet_ids:
            await self._schema_broken(list(REQUIRED_FIELDS))
        if len(sheet_ids) > 1:
            # A duplicated tab copies its metadata; the original has the lowest sheetId.
            logger.warning("Spreadsheet %s has %d transaction tabs; using the original", self.spreadsheet_id, len(sheet_ids))

        sheet_id = sheet_ids[0]
        columns = _columns_on(metadata, sheet_id)
        missing = [name for name in REQUIRED_FIELDS if name not in columns]
        if missing:
            await self._schema_broken(missing)

        column_map = ColumnMap(sheet_id=sheet_id, columns=columns)
        _column_cache[self._cache_key] = column_map
        await self._on_status(STATUS_READY)
        return column_map

    # --- StorageStrategy ----------------------------------------------------------

    async def insert_many(self, items: List[ExpenseData]) -> List[BatchResult]:
        column_map = await self.resolve_columns(fresh=True)
        width = max(column_map.columns.values()) + 1
        records = [item.model_copy(update={"id": item.id or str(uuid.uuid4())}) for item in items]

        rows = []
        for record in records:
            cells: list[dict] = [{} for _ in range(width)]  # unmapped (user) columns stay blank
            for name, index in column_map.columns.items():
                cells[index] = _to_cell(name, getattr(record, name))
            rows.append({"values": cells})

        await self._write(self.api.batch_update(
            self.spreadsheet_id,
            [{"appendCells": {"sheetId": column_map.sheet_id, "rows": rows, "fields": "userEnteredValue"}}],
        ))
        return [BatchResult(filename="", status="success", data=record) for record in records]

    async def get_all(
        self,
        page_size: int,
        start_cursor: Optional[str] = None,
    ) -> Tuple[List[ExpenseData], Optional[str]]:
        column_map = await self.resolve_columns()
        values = await self._call(self.api.get_grid_values(
            self.spreadsheet_id,
            {"sheetId": column_map.sheet_id, "startRowIndex": 1},
        ))
        records = [record for row in values if (record := _parse_row(row, column_map.columns))]
        records.sort(key=lambda record: record.date, reverse=True)

        offset = int(start_cursor) if start_cursor else 0
        end = offset + page_size
        next_cursor = str(end) if end < len(records) else None
        return records[offset:end], next_cursor

    # --- update / delete (not on the ABC yet) -------------------------------------

    async def update(self, record_id: str, changes: dict[str, Any]) -> None:
        unknown = set(changes) - set(FIELD_BY_NAME)
        if unknown or "id" in changes:
            raise ValueError(f"Cannot update fields: {sorted(unknown | ({'id'} & set(changes)))}")

        column_map = await self.resolve_columns(fresh=True)
        row_index = await self._locate(record_id, column_map)
        updates = [
            (self._cell_range(column_map.sheet_id, row_index, column_map.columns[name]), [[_to_raw(name, value)]])
            for name, value in changes.items()
            if name in column_map.columns  # a deleted optional column is skipped
        ]
        if updates:
            # Only the changed cells are written; the user's own columns are untouched.
            await self._write(self.api.update_grid_values(self.spreadsheet_id, updates))

    async def delete(self, record_id: str) -> None:
        column_map = await self.resolve_columns(fresh=True)
        id_index = column_map.columns["id"]
        row_index = await self._locate(record_id, column_map)

        # Rows can move between locating and deleting (sorts, inserts): re-check once.
        if await self._id_at(column_map.sheet_id, row_index, id_index) != record_id:
            row_index = await self._locate(record_id, column_map)
            if await self._id_at(column_map.sheet_id, row_index, id_index) != record_id:
                raise RecordNotFoundError(record_id)

        await self._write(self.api.batch_update(
            self.spreadsheet_id,
            [{
                "deleteDimension": {
                    "range": {
                        "sheetId": column_map.sheet_id,
                        "dimension": "ROWS",
                        "startIndex": row_index,
                        "endIndex": row_index + 1,
                    }
                }
            }],
        ))

    # --- repair ---------------------------------------------------------------------

    async def repair(self) -> ColumnMap:
        """Re-add missing required columns (or the whole tab) at the end, then resolve again."""
        self.invalidate_columns()
        file = await self._call(self.api.get_file(self.spreadsheet_id))
        if file.get("trashed"):
            await self._missing()

        metadata = await self._call(
            self.api.search_developer_metadata(self.spreadsheet_id, [META_SHEET_KEY, META_FIELD_KEY])
        )
        sheet_ids = _sheet_ids(metadata)
        properties = await self._call(self.api.get_sheet_properties(self.spreadsheet_id))

        if not sheet_ids:
            requests = self._new_tab_requests(properties)
        else:
            requests = self._missing_column_requests(sheet_ids[0], _columns_on(metadata, sheet_ids[0]), properties)

        if requests:
            await self._call(self.api.batch_update(self.spreadsheet_id, requests))
        self.invalidate_columns()
        return await self.resolve_columns(fresh=True)

    @staticmethod
    def _new_tab_requests(properties: list[dict]) -> list[dict]:
        sheet_id = max((p["sheetId"] for p in properties), default=0) + 1
        titles = {p["title"] for p in properties}
        title = TAB_TITLE
        suffix = 1
        while title in titles:
            suffix += 1
            title = f"{TAB_TITLE} {suffix}"
        return [
            {"addSheet": {"properties": {"sheetId": sheet_id, "title": title, "gridProperties": {"frozenRowCount": 1}}}},
            {
                "updateCells": {
                    "start": {"sheetId": sheet_id, "rowIndex": 0, "columnIndex": 0},
                    "rows": [{"values": [header_cell(field) for field in FIELDS]}],
                    "fields": "userEnteredValue",
                }
            },
            *build_tab_requests(sheet_id),
        ]

    @staticmethod
    def _missing_column_requests(sheet_id: int, columns: dict[str, int], properties: list[dict]) -> list[dict]:
        missing = [field for field in FIELDS if field.required and field.name not in columns]
        if not missing:
            return []
        column_count = next(
            p["gridProperties"]["columnCount"] for p in properties if p["sheetId"] == sheet_id
        )
        requests: list[dict] = [{"appendDimension": {"sheetId": sheet_id, "dimension": "COLUMNS", "length": len(missing)}}]
        for offset, field in enumerate(missing):
            index = column_count + offset
            requests.append({
                "updateCells": {
                    "start": {"sheetId": sheet_id, "rowIndex": 0, "columnIndex": index},
                    "rows": [{"values": [header_cell(field)]}],
                    "fields": "userEnteredValue",
                }
            })
            requests.extend(column_requests(sheet_id, field, index))
        return requests

    # --- helpers ----------------------------------------------------------------------

    @staticmethod
    def _cell_range(sheet_id: int, row_index: int, column_index: int) -> dict:
        return {
            "sheetId": sheet_id,
            "startRowIndex": row_index,
            "endRowIndex": row_index + 1,
            "startColumnIndex": column_index,
            "endColumnIndex": column_index + 1,
        }

    async def _locate(self, record_id: str, column_map: ColumnMap) -> int:
        """0-based row index of the record, read from the ID column only."""
        id_index = column_map.columns["id"]
        values = await self._call(self.api.get_grid_values(
            self.spreadsheet_id,
            {"sheetId": column_map.sheet_id, "startRowIndex": 1, "startColumnIndex": id_index, "endColumnIndex": id_index + 1},
        ))
        for offset, row in enumerate(values):
            if row and str(row[0]) == record_id:
                return offset + 1
        raise RecordNotFoundError(record_id)

    async def _id_at(self, sheet_id: int, row_index: int, id_index: int) -> str | None:
        values = await self._call(
            self.api.get_grid_values(self.spreadsheet_id, self._cell_range(sheet_id, row_index, id_index))
        )
        return str(values[0][0]) if values and values[0] else None

    async def _call(self, awaitable: Awaitable[Any]) -> Any:
        try:
            return await awaitable
        except GoogleApiError as exc:
            await self._raise_domain_error(exc)
            raise

    async def _write(self, awaitable: Awaitable[Any]) -> Any:
        try:
            return await awaitable
        except GoogleApiError as exc:
            await self._raise_domain_error(exc)
            raise StorageWriteError(str(exc)) from exc

    async def _raise_domain_error(self, exc: GoogleApiError) -> None:
        """Raise the typed error for missing/permission failures; return for anything else."""
        if exc.status == 404:
            await self._missing()
        if exc.status == 401 or (exc.status == 403 and "rate" not in exc.reason.lower()):
            await self._on_status(STATUS_NEEDS_PERMISSION)
            raise GooglePermissionError(exc.reason) from exc
        if exc.status == 400:
            # Usually a grid/range that no longer exists: the cached mapping is stale.
            self.invalidate_columns()

    async def _missing(self) -> None:
        self.invalidate_columns()
        await self._on_status(STATUS_MISSING)
        raise SpreadsheetMissingError(self.spreadsheet_id)

    async def _schema_broken(self, missing: list[str]) -> None:
        self.invalidate_columns()
        await self._on_status(STATUS_SCHEMA_BROKEN)
        raise SheetSchemaBrokenError(missing)

import logging
import uuid
from typing import Any, Awaitable, Callable, List, NoReturn, Optional

from cachetools import TTLCache

from constants.storage_status import (
    STATUS_MISSING,
    STATUS_NEEDS_PERMISSION,
    STATUS_READY,
    STATUS_SCHEMA_BROKEN,
)
from schemas.expense import BatchResult, EntryFilter, ExpenseData
from services.google.errors import (
    SERVICE_DISABLED,
    SERVICE_DISABLED_HINT,
    GoogleApiError,
    GooglePermissionError,
)
from services.google.google_api import GoogleApi
from services.storage.filters import matches
# The codec's _parse_date/_parse_amount/SHEETS_EPOCH are re-exported here so
# older `from ...google_sheets_storage import ...` keep working.
from services.google_sheets.codec import (
    SHEETS_EPOCH,
    _field_value,
    _parse_amount,
    _parse_date,
    _to_cell,
    _to_raw,
    parse_row as _parse_row,
)
from services.google_sheets.errors import (
    SheetSchemaBrokenError,
    SpreadsheetMissingError,
)
from services.google_sheets.layout import (
    DATA_START,
    ColumnMap,
    columns_on as _columns_on,
    grid_range,
    sheet_ids as _sheet_ids,
)
from services.google_sheets.repair_requests import missing_column_requests, new_tab_requests
from services.google_sheets.schema import (
    BACKFILL_FIELDS,
    FIELD_BY_NAME,
    META_FIELD_KEY,
    META_SHEET_KEY,
    REQUIRED_FIELDS,
)
from services.storage.abstract_storage_strategy import StorageStrategy
from services.storage.errors import RecordNotFoundError, StorageWriteError

logger = logging.getLogger(__name__)

StatusCallback = Callable[[str], Awaitable[None]]


# Per-process, ephemeral. Reads use it; writes always re-resolve so a column the
# user just moved can never receive another field's value.
_column_cache: TTLCache = TTLCache(maxsize=1024, ttl=300)


def clear_column_cache() -> None:
    """Drop every cached column mapping (tests start from a clean slate)."""
    _column_cache.clear()


async def _noop_status(status: str) -> None:
    return None


class GoogleSheetsStorage(StorageStrategy):
    """Expense storage in the user's spreadsheet.

    Columns are located only through Developer Metadata (resolve_columns), so
    users may rename headers, reorder or add columns, sort rows and rename the tab.
    Records are addressed by the UUID in the `id` column, never by row number.
    """

    # What PATCH may change: every schema field except the record's id.
    UPDATABLE_FIELDS = frozenset(FIELD_BY_NAME) - {"id"}

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

    async def _fetch_layout(self) -> tuple[list[int], list[dict]]:
        """Drive check + Developer Metadata for the transaction tab(s).

        Shared by resolve_columns and repair so both read the sheet the same way.
        """
        # The Sheets API still serves trashed files, so check Drive before trusting it.
        file = await self._call(self.api.get_file(self.spreadsheet_id))
        if file.get("trashed"):
            await self._missing()

        metadata = await self._call(
            self.api.search_developer_metadata(self.spreadsheet_id, [META_SHEET_KEY, META_FIELD_KEY])
        )
        return _sheet_ids(metadata), metadata

    async def resolve_columns(self, fresh: bool = False) -> ColumnMap:
        if not fresh:
            cached = _column_cache.get(self._cache_key)
            if cached is not None:
                return cached

        sheet_ids, metadata = await self._fetch_layout()
        if not sheet_ids:
            await self._schema_broken(list(REQUIRED_FIELDS))
        if len(sheet_ids) > 1:
            # A duplicated tab copies its metadata; the original has the lowest sheetId.
            logger.warning("Spreadsheet %s has %d transaction tabs; using the original", self.spreadsheet_id, len(sheet_ids))

        column_map = ColumnMap(sheet_id=sheet_ids[0], columns=_columns_on(metadata, sheet_ids[0]))
        missing = column_map.missing(REQUIRED_FIELDS)
        if missing:
            await self._schema_broken(missing)

        _column_cache[self._cache_key] = column_map
        await self._on_status(STATUS_READY)
        return column_map

    # --- StorageStrategy ----------------------------------------------------------

    async def insert_many(self, items: List[ExpenseData]) -> List[BatchResult]:
        column_map = await self.resolve_columns(fresh=True)
        if column_map.missing(BACKFILL_FIELDS):
            column_map = await self._backfill_columns(column_map)
        width = column_map.width
        records = [item.model_copy(update={"id": item.id or str(uuid.uuid4())}) for item in items]

        rows = []
        for record in records:
            cells: list[dict] = [{} for _ in range(width)]  # unmapped (user) columns stay blank
            for name, index in column_map.columns.items():
                cells[index] = _to_cell(name, _field_value(record, name))
            rows.append({"values": cells})

        await self._write(self.api.batch_update(
            self.spreadsheet_id,
            [{"appendCells": {"sheetId": column_map.sheet_id, "rows": rows, "fields": "userEnteredValue"}}],
        ))
        return [BatchResult(filename="", status="success", data=record) for record in records]

    async def get_all(self, filters: EntryFilter) -> List[ExpenseData]:
        column_map = await self.resolve_columns()
        values = await self._call(self.api.get_grid_values(
            self.spreadsheet_id,
            grid_range(column_map.sheet_id, rows=(DATA_START, None)),
        ))
        records = [
            record for row in values
            if (record := _parse_row(row, column_map.columns)) and matches(record, filters)
        ]
        records.sort(key=lambda record: record.date, reverse=True)
        return records

    async def get_one(self, record_id: str) -> ExpenseData:
        column_map = await self.resolve_columns()
        row_index = await self._locate(record_id, column_map)
        values = await self._call(self.api.get_grid_values(
            self.spreadsheet_id,
            grid_range(column_map.sheet_id, rows=(row_index, row_index + 1)),
        ))
        record = _parse_row(values[0], column_map.columns) if values else None
        if record is None:
            raise RecordNotFoundError(record_id)  # id cell unreadable or the row was edited away
        return record

    async def record_url(self, record: ExpenseData) -> Optional[str]:
        """Link that opens the spreadsheet with the record's row selected."""
        if not record.id:
            return None
        column_map = await self.resolve_columns()
        row = await self._locate(record.id, column_map) + 1  # 1-based, as shown in Sheets
        return (
            f"https://docs.google.com/spreadsheets/d/{self.spreadsheet_id}/edit"
            f"#gid={column_map.sheet_id}&range={row}:{row}"
        )

    # --- update / delete ----------------------------------------------------------

    async def update(self, record_id: str, changes: dict[str, Any]) -> None:
        invalid = sorted(set(changes) - self.UPDATABLE_FIELDS)
        if invalid:
            raise ValueError(f"Cannot update fields: {invalid}")

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
        row_index = await self._locate_verified(record_id, column_map)

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
        """Re-add missing required/backfill columns (or the whole tab) at the end, then resolve again."""
        self.invalidate_columns()
        sheet_ids, metadata = await self._fetch_layout()
        properties = await self._call(self.api.get_sheet_properties(self.spreadsheet_id))

        if not sheet_ids:
            requests = new_tab_requests(properties)
        else:
            requests = missing_column_requests(sheet_ids[0], _columns_on(metadata, sheet_ids[0]), properties)

        if requests:
            await self._call(self.api.batch_update(self.spreadsheet_id, requests))
        self.invalidate_columns()
        return await self.resolve_columns(fresh=True)

    async def _backfill_columns(self, column_map: ColumnMap) -> ColumnMap:
        """Add optional columns that older sheets lack (e.g. Currency) before writing."""
        properties = await self._call(self.api.get_sheet_properties(self.spreadsheet_id))
        requests = missing_column_requests(column_map.sheet_id, column_map.columns, properties)
        await self._write(self.api.batch_update(self.spreadsheet_id, requests))
        self.invalidate_columns()
        return await self.resolve_columns(fresh=True)

    # --- helpers ----------------------------------------------------------------------

    @staticmethod
    def _cell_range(sheet_id: int, row_index: int, column_index: int) -> dict:
        return grid_range(sheet_id, rows=(row_index, row_index + 1), cols=(column_index, column_index + 1))

    async def _locate(self, record_id: str, column_map: ColumnMap) -> int:
        """0-based row index of the record, read from the ID column only."""
        id_index = column_map.id_index
        values = await self._call(self.api.get_grid_values(
            self.spreadsheet_id,
            grid_range(column_map.sheet_id, rows=(DATA_START, None), cols=(id_index, id_index + 1)),
        ))
        for offset, row in enumerate(values):
            if row and str(row[0]) == record_id:
                return offset + 1
        raise RecordNotFoundError(record_id)

    async def _locate_verified(self, record_id: str, column_map: ColumnMap) -> int:
        """Row index confirmed to still hold the record, or RecordNotFoundError.

        Rows can move between locating and deleting (sorts, inserts): re-check once.
        """
        for _ in range(2):
            row_index = await self._locate(record_id, column_map)
            if await self._id_at(column_map.sheet_id, row_index, column_map.id_index) == record_id:
                return row_index
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
        if exc.error_reason == SERVICE_DISABLED:
            # Our Cloud project's fault, not the user's grant: reconsent wouldn't help.
            logger.error("Google API call for spreadsheet %s failed: %s%s", self.spreadsheet_id, exc, SERVICE_DISABLED_HINT)
            return
        if exc.status == 401 or (exc.status == 403 and "rate" not in exc.reason.lower()):
            await self._on_status(STATUS_NEEDS_PERMISSION)
            raise GooglePermissionError(exc.reason) from exc
        if exc.status == 400:
            # Usually a grid/range that no longer exists: the cached mapping is stale.
            self.invalidate_columns()

    async def _missing(self) -> NoReturn:
        self.invalidate_columns()
        await self._on_status(STATUS_MISSING)
        raise SpreadsheetMissingError(self.spreadsheet_id)

    async def _schema_broken(self, missing: list[str]) -> NoReturn:
        self.invalidate_columns()
        await self._on_status(STATUS_SCHEMA_BROKEN)
        raise SheetSchemaBrokenError(missing)

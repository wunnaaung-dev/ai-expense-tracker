"""Where things live in the spreadsheet: the column map and the grid ranges.

Columns are located only through Developer Metadata (see google_sheets_storage);
this module holds the pure data those lookups produce and the gridRange dicts
the Sheets API is called with.
"""
import logging
from dataclasses import dataclass
from typing import Any

from services.google_sheets.schema import (
    FIELD_BY_NAME,
    META_FIELD_KEY,
    META_SHEET_KEY,
    META_SHEET_VALUE,
)

logger = logging.getLogger(__name__)

# Row 0 is the header; records start at row 1.
DATA_START = 1


@dataclass(frozen=True)
class ColumnMap:
    sheet_id: int
    columns: dict[str, int]  # field name -> 0-based column index

    @property
    def width(self) -> int:
        """Highest mapped column index + 1: how wide a written row must be."""
        return max(self.columns.values()) + 1

    @property
    def id_index(self) -> int:
        return self.columns["id"]

    def missing(self, names) -> list[str]:
        """Which of `names` this sheet doesn't have a column for."""
        return [name for name in names if name not in self.columns]


def grid_range(sheet_id: int, rows: tuple[int, int | None], cols: tuple[int, int] | None = None) -> dict:
    """A Sheets gridRange. Bounds you don't pass are left out of the dict."""
    grid: dict[str, Any] = {"sheetId": sheet_id, "startRowIndex": rows[0]}
    if rows[1] is not None:
        grid["endRowIndex"] = rows[1]
    if cols is not None:
        grid["startColumnIndex"] = cols[0]
        grid["endColumnIndex"] = cols[1]
    return grid


def sheet_ids(metadata: list[dict]) -> list[int]:
    return sorted(
        meta["location"]["sheetId"]
        for meta in metadata
        if meta["metadataKey"] == META_SHEET_KEY
        and meta.get("metadataValue") == META_SHEET_VALUE
        and "sheetId" in meta.get("location", {})
    )


def columns_on(metadata: list[dict], sheet_id: int) -> dict[str, int]:
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
